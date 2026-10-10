"""The PDF view widget: painted, zoomable, selectable, searchable.

Design notes
------------
Pages are *painted* in ``paintEvent`` instead of being faked with ``QLabel``
pixmaps.  That gives us:

* crisp 1:1 drawing of rendered pages at any zoom,
* real text selection / copy (the text layer comes from PyMuPDF),
* search highlights and a current-match indicator drawn on top,
* cheap partial updates when a page finishes rendering.

All rendering work happens in a :class:`~pdfar.render.RenderPool`; results come
back through a Qt signal and are stored in a byte-budget LRU cache.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QPoint, QRect, Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QColor, QImage, QKeyEvent, QMouseEvent, QPaintEvent, QPainter,
    QPen, QWheelEvent, QGuiApplication, QResizeEvent,
)
from PyQt6.QtWidgets import QAbstractScrollArea, QFrame, QScroller

from .cache import PageCache
from .geometry import display_size, map_rect, normalize_rotation
from .render import RenderPool, RenderResult
from .search import SearchHit
from .tiles import Tile, TileGrid, TILE_SIZE
from .annotations import AnnotationManager, HIGHLIGHT_COLORS

PAD = 16          # outer margin (content coords)
GAP = 12          # vertical gap between pages
MIN_ZOOM = 0.05
MAX_ZOOM = 10.0

# Memory levels (Okular): how much the view preloads around the viewport.
#   "low"    -> render only what is visible (least memory)
#   "normal" -> a few pages around the viewport (default)
#   "greedy" -> preload the whole document (most fluid, most memory)
MEMORY_LEVELS = ("low", "normal", "greedy")
DEFAULT_MEMORY_LEVEL = "normal"
# level -> (around, back, forward, tile_margin_px)
#   around : extra pages each side of the visible range, requested at priority 0
#   back   : pages preloaded before the visible range (None = all)
#   forward: pages preloaded after the visible range (None = all)
_MEMORY = {
    "low": (0, 0, 0, 0),
    "normal": (1, 3, 8, 512),
    "greedy": (1, None, None, 512),
}

BG_COLOR = QColor(46, 46, 46)
PAGE_BORDER = QColor(20, 20, 20)
PLACEHOLDER_BG = QColor(238, 238, 238)
PLACEHOLDER_FG = QColor(120, 120, 120)
SHADOW = QColor(0, 0, 0, 90)
HIT_COLOR = QColor(255, 213, 79, 120)
HIT_CURRENT = QColor(255, 143, 0, 170)
SEL_COLOR = QColor(63, 145, 255, 110)

# Light theme colors (default)
LIGHT_BG_COLOR = QColor(46, 46, 46)
LIGHT_PAGE_BORDER = QColor(20, 20, 20)
LIGHT_PLACEHOLDER_BG = QColor(238, 238, 238)
LIGHT_PLACEHOLDER_FG = QColor(120, 120, 120)
LIGHT_SHADOW = QColor(0, 0, 0, 90)

# Dark theme colors
DARK_BG_COLOR = QColor(30, 30, 30)
DARK_PAGE_BORDER = QColor(50, 50, 50)
DARK_PLACEHOLDER_BG = QColor(50, 50, 50)
DARK_PLACEHOLDER_FG = QColor(180, 180, 180)
DARK_SHADOW = QColor(0, 0, 0, 120)


class PDFView(QAbstractScrollArea):
    """Continuous-scroll PDF view."""

    page_changed = pyqtSignal(int)          # current page index (0-based)
    zoom_changed = pyqtSignal(float)        # zoom factor
    selection_changed = pyqtSignal()
    hit_changed = pyqtSignal(int, int)      # (current_hit, total_hits), 1-based
    doc_error = pyqtSignal(str)

    def __init__(self, doc_path: str, parent=None, password: str = ""):
        super().__init__(parent)
        self.doc_path = doc_path
        self.doc = fitz.open(doc_path)
        if self.doc.needs_pass:
            if not password or not self.doc.authenticate(password):
                self.doc.close()
                raise ValueError("encrypted")

        self.page_count = self.doc.page_count
        self.page_sizes: List[Tuple[float, float]] = []
        for i in range(self.page_count):
            r = self.doc[i].rect
            self.page_sizes.append((r.width, r.height))

        self.zoom = 1.0
        self.rotation = 0
        self.fit_mode = "none"          # 'none' | 'width' | 'page'
        self.current_page = 0
        self.memory_level = DEFAULT_MEMORY_LEVEL
        self.dark_mode = False

        self.cache = PageCache()
        self.pool = RenderPool(doc_path, workers=min(4, max(2, (QThread.idealThreadCount() or 2))),
                               password=password)
        self.pool.result_ready.connect(self._on_result)

        # Tiled rendering (Okular large-page strategy): a page that would be
        # rendered far larger than the viewport is split into square tiles and
        # only the visible ones are rendered.  Tiles are stored here (GUI
        # thread writes/reads), separate from PageCache which holds full pages.
        self._tiles: Dict[int, Dict[Tuple[int, int], QImage]] = {}
        self._tile_grids: Dict[int, TileGrid] = {}

        # layout
        self._disp_sizes: List[Tuple[float, float]] = []
        self._page_tops: List[float] = []
        self._content_h = 0.0
        self._content_w = 0.0
        self._max_w = 0.0

        # text layer
        self._words: Dict[int, List[Tuple[float, float, float, float, str]]] = {}

        # selection: (page, word-index) pairs
        self._sel_anchor: Optional[Tuple[int, int]] = None
        self._sel_caret: Optional[Tuple[int, int]] = None
        self._dragging = False

        # search
        self._hit_rects: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self._hit_seq: List[Tuple[int, int]] = []
        self._hit_cur = -1

        self._errors: Dict[int, str] = {}
        self._press_pos = QPoint()

        # Annotations
        self.annotation_manager = AnnotationManager(doc_path, self.doc, self)
        self.annotation_manager.annotations_changed.connect(self._on_annotations_changed)
        self._highlight_color_idx = 0  # Current highlight color index

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        # Kinetic/smooth scrolling on touch devices, like Okular's
        # QScroller::scroller(viewport()).  We grab the TouchGesture only:
        # LeftMouseButtonGesture would hijack left-drag and break text
        # selection.  On a plain mouse this has no effect.
        self._scroller = QScroller.scroller(self.viewport())
        QScroller.grabGesture(self.viewport(), QScroller.ScrollerGestureType.TouchGesture)

        self._relayout()
        self._request_visible()

    # ----------------------------------------------------------------- doc
    @property
    def title(self) -> str:
        try:
            return (self.doc.metadata or {}).get("title") or ""
        except Exception:
            return ""

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
        try:
            self.doc.close()
        except Exception:
            pass

    def _on_annotations_changed(self) -> None:
        """Called when annotations change - trigger repaint."""
        self.viewport().update()

    def create_highlight_from_selection(self, color: Optional[str] = None) -> bool:
        """Create a highlight annotation from the current text selection.

        Args:
            color: Optional highlight color (hex). Uses current color if not specified.

        Returns:
            True if highlight was created, False if no selection.
        """
        if not self.has_selection():
            return False

        if color is None:
            color = HIGHLIGHT_COLORS[self._highlight_color_idx][1]

        # Collect selection rects grouped by page
        page_rects: Dict[int, List[tuple]] = {}
        for page, _idx, word in self._iter_selected_words():
            rect = self._word_rect_view(page, word)
            page_rects.setdefault(page, []).append(rect)

        # Merge adjacent rects on the same line for cleaner highlights
        merged_rects: Dict[int, List[tuple]] = {}
        for page, rects in page_rects.items():
            merged_rects[page] = self._merge_selection_rects(rects)

        # Create highlight annotations
        author = ""  # Could be made configurable
        for page, rects in merged_rects.items():
            self.annotation_manager.add_highlight(page, rects, color, author)

        self.clear_selection()
        return True

    def _merge_selection_rects(self, rects: List[tuple], y_tolerance: float = 2.0) -> List[tuple]:
        """Merge adjacent selection rectangles on the same line.

        Args:
            rects: List of (x0, y0, x1, y1) rectangles
            y_tolerance: Max vertical distance to consider rects on same line

        Returns:
            List of merged rectangles
        """
        if not rects:
            return []

        # Sort by y then x
        sorted_rects = sorted(rects, key=lambda r: (r[1], r[0]))
        merged = []
        current = list(sorted_rects[0])

        for rect in sorted_rects[1:]:
            x0, y0, x1, y1 = rect
            cx0, cy0, cx1, cy1 = current

            # Check if on same line (y overlap within tolerance)
            if y0 <= cy1 + y_tolerance and y1 >= cy0 - y_tolerance:
                # Same line - merge horizontally
                current[0] = min(cx0, x0)
                current[1] = min(cy0, y0)
                current[2] = max(cx1, x1)
                current[3] = max(cy1, y1)
            else:
                # New line
                merged.append(tuple(current))
                current = [x0, y0, x1, y1]

        merged.append(tuple(current))
        return merged

    def set_highlight_color(self, color: str) -> None:
        """Set the current highlight color."""
        for i, (name, hex_color) in enumerate(HIGHLIGHT_COLORS):
            if hex_color == color:
                self._highlight_color_idx = i
                return
        # Custom color not in palette
        self._highlight_color_idx = len(HIGHLIGHT_COLORS)  # Will use custom

    def get_highlight_color(self) -> str:
        """Get the current highlight color."""
        if self._highlight_color_idx < len(HIGHLIGHT_COLORS):
            return HIGHLIGHT_COLORS[self._highlight_color_idx][1]
        return "#FFFF00"  # default yellow

    def next_highlight_color(self) -> str:
        """Cycle to the next highlight color and return it."""
        self._highlight_color_idx = (self._highlight_color_idx + 1) % len(HIGHLIGHT_COLORS)
        return self.get_highlight_color()

    def prev_highlight_color(self) -> str:
        """Cycle to the previous highlight color and return it."""
        self._highlight_color_idx = (self._highlight_color_idx - 1) % len(HIGHLIGHT_COLORS)
        return self.get_highlight_color()

    def save_annotations(self) -> bool:
        """Save all annotations to the PDF file."""
        return self.annotation_manager.save_to_pdf()

    def set_dark_mode(self, enabled: bool) -> None:
        """Enable or disable dark mode."""
        if self.dark_mode == enabled:
            return
        self.dark_mode = enabled
        self._apply_theme()
        self.viewport().update()

    def _apply_theme(self) -> None:
        """Apply theme colors to the widget."""
        if self.dark_mode:
            self.setStyleSheet(f"""
                QAbstractScrollArea {{
                    background-color: {DARK_BG_COLOR.name()};
                }}
                QScrollBar:vertical, QScrollBar:horizontal {{
                    background: {DARK_BG_COLOR.name()};
                    border: none;
                }}
                QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
                    background: {DARK_PAGE_BORDER.name()};
                    min-height: 20px;
                }}
            """)
        else:
            self.setStyleSheet("")

    # ------------------------------------------------------------- layout
    def _relayout(self) -> None:
        z = self.zoom
        self._disp_sizes = []
        self._page_tops = []
        top = float(PAD)
        max_w = 0.0
        for i in range(self.page_count):
            w0, h0 = self.page_sizes[i]
            dw, dh = display_size(w0, h0, self.rotation)
            dw, dh = dw * z, dh * z
            self._disp_sizes.append((dw, dh))
            self._page_tops.append(top)
            top += dh + GAP
            max_w = max(max_w, dw)
        self._max_w = max_w
        self._content_h = (top - GAP + PAD) if self.page_count else 0.0
        self._content_w = max_w + 2 * PAD
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.setRange(0, max(0, int(self._content_h - vh)))
        vbar.setPageStep(vh)
        vbar.setSingleStep(40)
        hbar.setRange(0, max(0, int(self._content_w - vw)))
        hbar.setPageStep(vw)
        hbar.setSingleStep(40)

    def _x_offset(self) -> float:
        """Content-space x origin of page area (centres pages in wide views)."""
        vw = self.viewport().width()
        return max(0.0, (vw - self._content_w) / 2.0) if vw > self._content_w else 0.0

    def _page_origin(self, index: int) -> Tuple[float, float]:
        """Top-left of a page in content coordinates."""
        dw, _ = self._disp_sizes[index]
        x = self._x_offset() + PAD + (self._max_w - dw) / 2.0
        y = self._page_tops[index]
        return (x, y)

    def _page_at_y(self, content_y: float) -> int:
        """Index of the page containing a content-space y (clamped)."""
        if not self.page_count:
            return 0
        lo, hi = 0, self.page_count - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._page_tops[mid] <= content_y:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def visible_range(self) -> Tuple[int, int]:
        """First and last (inclusive) page index intersecting the viewport."""
        if not self.page_count:
            return (0, -1)
        y0 = float(self.verticalScrollBar().value())
        y1 = y0 + self.viewport().height()
        first = self._page_at_y(max(0.0, y0 - 200))
        last = first
        for i in range(first, self.page_count):
            if self._page_tops[i] > y1:
                break
            last = i
        return (first, last)

    # ---------------------------------------------------------- rendering
    def _request_visible(self) -> None:
        if not self.page_count:
            return
        first, last = self.visible_range()
        # keep the cache viewport-aware so it evicts far pages first (Okular)
        self.cache.set_center(self.current_page)

        around, back, fwd, margin = _MEMORY.get(
            self.memory_level, _MEMORY[DEFAULT_MEMORY_LEVEL])
        vsx = self.horizontalScrollBar().value()
        vsy = self.verticalScrollBar().value()
        vw = self.viewport().width()
        vh = self.viewport().height()

        vis_lo = max(0, first - around)
        vis_hi = min(self.page_count, last + 1 + around)   # exclusive

        # cancel queued renders for pages far outside what we now care about
        # (base: scroll fast -> do not waste threads on stale pages)
        if back is None:                                   # greedy: keep all
            keep_first, keep_last = 0, self.page_count - 1
        else:
            slack = max(around, back, fwd)
            keep_first = max(0, first - slack)
            keep_last = min(self.page_count - 1, last + slack)
        self.pool.cancel_if(
            lambda job: job.tag == "view"
            and (job.page < keep_first or job.page > keep_last)
        )

        # visible pages (+ "around"): request their tiles / whole page
        for i in range(vis_lo, vis_hi):
            self._request_visible_for_page(i, priority=0, margin=margin,
                                           vsx=vsx, vsy=vsy, vw=vw, vh=vh)

        # preload (level-dependent)
        if back is None:                                   # greedy: whole doc
            for i in range(self.page_count):
                if i < vis_lo or i >= vis_hi:
                    self._request_page(i, priority=3)
        else:
            for i in range(max(0, first - back), vis_lo):
                self._request_page(i, priority=2)
            for i in range(vis_hi, min(self.page_count, last + fwd)):
                self._request_page(i, priority=3)

        # extract words in the background too, so text-layer interactions
        # (selection/copy) don't ever block the GUI thread on first visit
        for i in range(vis_lo, vis_hi):
            self._request_page_words(i, priority=2)

    def set_memory_level(self, level: str) -> None:
        """Set the preload memory level (Okular-style).

        One of ``"low"`` (render only what is visible), ``"normal"`` (a few
        pages around the viewport) or ``"greedy"`` (preload the whole
        document).  Unknown values fall back to the default.
        """
        level = level if level in MEMORY_LEVELS else DEFAULT_MEMORY_LEVEL
        if level == self.memory_level:
            return
        self.memory_level = level
        self._request_visible()

    def _grid_for_page(self, index: int, view_w: int, view_h: int) -> TileGrid:
        """Cached TileGrid for a page; None if the page shouldn't be tiled."""
        dw, dh = self._disp_sizes[index]
        grid = self._tile_grids.get(index)
        if grid is None or grid.view_w != view_w or grid.view_h != view_h:
            grid = TileGrid(dw, dh, view_w, view_h)
            self._tile_grids[index] = grid
        return grid

    def _request_visible_for_page(self, index: int, priority: int, margin: int,
                                  vsx: int, vsy: int, vw: int, vh: int) -> None:
        """Request the visible tiles of a (potentially tiled) page."""
        grid = self._grid_for_page(index, vw, vh)
        if not grid.active or self.rotation:
            self._request_page(index, priority=priority)
            return
        # position of the page in view coordinates
        px, py = self._page_origin(index)
        dw, dh = self._disp_sizes[index]
        y0 = max(vsy - margin, py)
        y1 = min(vsy + vh + margin, py + dh)
        x0 = max(vsx - margin, px)
        x1 = min(vsx + vw + margin, px + dw)
        for tile in grid.tiles_for_region(x0 - px, y0 - py, x1 - px, y1 - py):
            self._request_tile(index, grid, tile, priority=priority)

    def _request_tile(self, index: int, grid: TileGrid, tile: Tile, priority: int) -> None:
        """Request a single tile if it isn't cached or already in flight."""
        tiles = self._tiles.setdefault(index, {})
        if (tile.row, tile.col) in tiles:
            return
        clip = grid.clip_points(tile, self.zoom)
        self.pool.request(index, self.zoom, priority=priority, tag="view",
                          alpha=False, rotation=0, clip=clip)

    def _request_page(self, index: int, priority: int = 2, tag: str = "view") -> None:
        if self.cache.get(index, self.zoom, self.rotation, tag) is not None:
            return
        self.pool.request(index, self.zoom, priority=priority, tag=tag,
                          alpha=False, rotation=self.rotation)

    def _on_result(self, result: object) -> None:
        """Render result arrives here (queued to the GUI thread)."""
        if not isinstance(result, RenderResult) or result.tag not in ("view", "text"):
            return
        if result.tag == "text":
            if result.ok and result.words is not None:
                self._words[result.page] = result.words
            return
        if result.tag != "view":
            return
        # Drop results that belong to a zoom/rotation we have left behind.
        if abs(result.zoom - self.zoom) > 1e-4 or result.rotation != self.rotation:
            return
        if not result.ok or (result.samples is None and result.png is None):
            self._errors[result.page] = result.error or "render failed"
            self.viewport().update()
            return
        img = result.to_qimage()
        if img.isNull():
            return
        if result.clip is not None:
            # A tile: store it for painter once the tile grid is present.
            grid = self._tile_grids.get(result.page)
            if grid is not None:
                # map clip back to a tile-ish origin within the page display
                tiles = self._tiles.setdefault(result.page, {})
                tiles[self._tile_key_from_clip(grid, result.clip)] = img
            self._errors.pop(result.page, None)
        else:
            self.cache.put(result.page, result.zoom, img, result.rotation)
            # a full page arrived; clear any stale tiles for it
            self._tiles.pop(result.page, None)
            self._errors.pop(result.page, None)
        self.viewport().update()

    def _tile_key_from_clip(self, grid: TileGrid, clip: Tuple[float, float, float, float]):
        """Derive a (row, col) key from a clip rect (page points)."""
        z = max(1e-6, self.zoom)
        dx = clip[0] * z   # display x origin of the clipped region
        dy = clip[1] * z
        col = int(dx // grid.tile_size)
        row = int(dy // grid.tile_size)
        return (row, col)

    # ------------------------------------------------------------- paint
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self.viewport())
        # Use theme-aware background color
        bg_color = DARK_BG_COLOR if self.dark_mode else BG_COLOR
        painter.fillRect(event.rect(), bg_color)
        if not self.page_count:
            painter.setPen(QColor(180, 180, 180) if self.dark_mode else QColor(200, 200, 200))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, "Empty document")
            painter.end()
            return

        vsx = self.horizontalScrollBar().value()
        vsy = self.verticalScrollBar().value()

        first, last = self.visible_range()
        for i in range(first, last + 1):
            px, py = self._page_origin(i)
            x = px - vsx
            y = py - vsy
            dw, dh = self._disp_sizes[i]
            target = QRect(int(round(x)), int(round(y)), int(round(dw)), int(round(dh)))
            if not target.intersects(event.rect()):
                continue

            # drop shadow + border - use theme-aware colors
            shadow_color = DARK_SHADOW if self.dark_mode else SHADOW
            placeholder_bg = DARK_PLACEHOLDER_BG if self.dark_mode else PLACEHOLDER_BG
            page_border = DARK_PAGE_BORDER if self.dark_mode else PAGE_BORDER

            painter.fillRect(target.adjusted(3, 3, 3, 3), shadow_color)
            painter.fillRect(target, placeholder_bg)
            painter.setPen(QPen(page_border, 1))
            painter.drawRect(target)

            tiles = self._tiles.get(i)
            if tiles is not None and tiles:  # page is being rendered as tiles
                dw, dh = self._disp_sizes[i]
                for (row, col), tile_img in tiles.items():
                    dx = col * TILE_SIZE
                    dy = row * TILE_SIZE
                    tw = min(TILE_SIZE, dw - dx)
                    th = min(TILE_SIZE, dh - dy)
                    self._paint_tile_visible(painter, tile_img, x + dx, y + dy, tw, th, target)
            else:
                img = self.cache.get(i, self.zoom, self.rotation)
                if img is not None and not img.isNull():
                    painter.drawImage(target, img)
                else:
                    placeholder_fg = DARK_PLACEHOLDER_FG if self.dark_mode else PLACEHOLDER_FG
                    painter.setPen(placeholder_fg)
                    err = self._errors.get(i)
                    msg = f"Page {i + 1}\n" + (f"error: {err}" if err else "rendering…")
                    painter.drawText(target, Qt.AlignmentFlag.AlignCenter, msg)
                    self._request_page(i, priority=1)

            self._paint_overlays(painter, i, x, y)

        painter.end()

    def _paint_tile_visible(self, painter: QPainter, tile_img: QImage,
                            tx: float, ty: float, tw: float, th: float,
                            visible: QRect) -> None:
        """Paint a single tile, intersecting it with the visible rect first."""
        tile_rect = QRect(int(round(tx)), int(round(ty)), int(round(tw)), int(round(th)))
        if not tile_rect.intersects(visible):
            return
        if tile_img.isNull():
            return  # empty/placeholder: caller already drew the background
        painter.drawImage(tile_rect, tile_img)

    def _paint_overlays(self, painter: QPainter, page: int, x: float, y: float) -> None:
        z = self.zoom
        # annotations (highlights, etc.) - drawn first, behind search/selection
        self._paint_annotations(painter, page, x, y)

        # search hits
        for rect in self._hit_rects.get(page, ()):
            x0, y0, x1, y1 = map_rect(rect, *self.page_sizes[page], self.rotation)
            painter.fillRect(QRect(int(x + x0 * z), int(y + y0 * z),
                                   max(1, int((x1 - x0) * z)), max(1, int((y1 - y0) * z))), HIT_COLOR)
        cur = self._current_hit_rect()
        if cur is not None and cur[0] == page:
            x0, y0, x1, y1 = map_rect(cur[1], *self.page_sizes[page], self.rotation)
            r = QRect(int(x + x0 * z), int(y + y0 * z),
                      max(1, int((x1 - x0) * z)), max(1, int((y1 - y0) * z)))
            painter.fillRect(r, HIT_CURRENT)
            painter.setPen(QPen(QColor(230, 120, 0), 1))
            painter.drawRect(r)

        # text selection
        for rect in self._selection_rects_for_page(page):
            x0, y0, x1, y1 = rect
            painter.fillRect(QRect(int(x + x0 * z), int(y + y0 * z),
                                   max(1, int((x1 - x0) * z)), max(1, int((y1 - y0) * z))), SEL_COLOR)

    def _paint_annotations(self, painter: QPainter, page: int, x: float, y: float) -> None:
        """Draw annotations (highlights) for a page."""
        z = self.zoom
        highlights = self.annotation_manager.get_highlights_for_page(page)
        for highlight in highlights:
            color = QColor(highlight.color)
            color.setAlphaF(highlight.opacity)
            for rect in highlight.rects:
                x0, y0, x1, y1 = map_rect(rect, *self.page_sizes[page], self.rotation)
                painter.fillRect(QRect(int(x + x0 * z), int(y + y0 * z),
                                       max(1, int((x1 - x0) * z)), max(1, int((y1 - y0) * z))), color)

    # ------------------------------------------------------- text layer
    def _request_page_words(self, index: int, priority: int = 3) -> None:
        """Extract a page's words on a worker thread (Okular-style).

        If the words are already cached, this is a no-op.  Off the GUI thread,
        so visiting a new page never blocks the UI on get_text('words').
        """
        if index not in self._words:
            self.pool.request_words(index, priority=priority)

    def _page_words_sync(self, index: int) -> List[Tuple[float, float, float, float, str]]:
        """Blocking fallback that loads and caches a page's words (GUI thread).

        Only used when the async extraction has not finished and code genuinely
        needs the words right now (e.g. a selection interaction).  The async
        preload in _request_visible usually makes this a no-op for visible pages.
        """
        words = self._words.get(index)
        if words is None:
            try:
                raw = self.doc.load_page(index).get_text("words") or []
                words = [(w[0], w[1], w[2], w[3], w[4]) for w in raw]
            except Exception:
                words = []
            self._words[index] = words
        return words

    def _page_words(self, index: int) -> List[Tuple[float, float, float, float, str]]:
        words = self._words.get(index)
        if words is None:
            # async path is preferred; sync fallback keeps selection working.
            self._request_page_words(index)
            return self._page_words_sync(index)
        return words

    def _word_rect_view(self, page: int, word: Tuple[float, float, float, float, str]):
        return map_rect(word[:4], *self.page_sizes[page], self.rotation)

    def _content_to_page_view(self, cx: float, cy: float) -> Optional[Tuple[int, float, float]]:
        """Content coords -> (page index, view-space point within page, unzoomed)."""
        if not self.page_count:
            return None
        idx = self._page_at_y(cy)
        px, py = self._page_origin(idx)
        return (idx, (cx - px) / self.zoom, (cy - py) / self.zoom)

    def _word_at(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        cx = pos.x() + self.horizontalScrollBar().value()
        cy = pos.y() + self.verticalScrollBar().value()
        hit = self._content_to_page_view(cx, cy)
        if hit is None:
            return None
        page, u, v = hit
        best = None
        best_d = 1e18
        for i, w in enumerate(self._page_words(page)):
            x0, y0, x1, y1 = self._word_rect_view(page, w)
            if x0 <= u <= x1 and y0 <= v <= y1:
                return (page, i)
            dx = max(x0 - u, 0.0, u - x1)
            dy = max(y0 - v, 0.0, v - y1)
            d = dx * dx + dy * dy
            if d < best_d:
                best_d = d
                best = (page, i)
        # only accept a "nearest" word when it is reasonably close (40pt)
        return best if best is not None and best_d <= 40 * 40 else None

    # --------------------------------------------------------- selection
    def _selection_bounds(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        if self._sel_anchor is None or self._sel_caret is None:
            return None
        a, c = self._sel_anchor, self._sel_caret
        return (a, c) if a <= c else (c, a)

    def _iter_selected_words(self):
        bounds = self._selection_bounds()
        if bounds is None:
            return
        (p1, i1), (p2, i2) = bounds
        for page in range(p1, min(p2, self.page_count - 1) + 1):
            words = self._page_words(page)
            lo = i1 if page == p1 else 0
            hi = i2 if page == p2 else len(words) - 1
            for i in range(max(0, lo), min(len(words) - 1, hi) + 1):
                yield page, i, words[i]

    def _selection_rects_for_page(self, page: int):
        out = []
        for p, _i, w in self._iter_selected_words() or ():
            if p == page:
                out.append(self._word_rect_view(page, w))
        return out

    def has_selection(self) -> bool:
        return self._selection_bounds() is not None

    def selected_text(self) -> str:
        pages: List[str] = []
        current_page = None
        buf: List[str] = []
        for page, _i, w in self._iter_selected_words() or ():
            if current_page is not None and page != current_page:
                pages.append(" ".join(buf))
                buf = []
            current_page = page
            buf.append(w[4])
        if buf:
            pages.append(" ".join(buf))
        return "\n".join(pages).strip()

    def copy_selection(self) -> str:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
        return text

    def select_all(self) -> None:
        if not self.page_count:
            return
        first_words = self._page_words(0)
        last_words = self._page_words(self.page_count - 1)
        self._sel_anchor = (0, 0) if first_words else None
        self._sel_caret = (self.page_count - 1, max(0, len(last_words) - 1)) if last_words else None
        self.selection_changed.emit()
        self.viewport().update()

    def clear_selection(self) -> None:
        if self._sel_anchor is not None or self._sel_caret is not None:
            self._sel_anchor = self._sel_caret = None
            self.selection_changed.emit()
            self.viewport().update()

    # ------------------------------------------------------------- search
    def set_search_hits(self, hits: List[SearchHit]) -> int:
        self._hit_rects = {}
        self._hit_seq = []
        self._hit_cur = -1
        for hit in hits:
            seq_index = len(self._hit_seq)
            rects = list(hit.rects)
            self._hit_rects[hit.page] = self._hit_rects.get(hit.page, []) + rects
            for _ in rects:
                self._hit_seq.append((hit.page, seq_index))
        # build a flat (page, rect) sequence for the current-match indicator
        flat: List[Tuple[int, Tuple[float, float, float, float]]] = []
        for page, rects in self._hit_rects.items():
            for r in rects:
                flat.append((page, r))
        self._hit_flat = flat
        self.hit_changed.emit(0, len(flat))
        self.viewport().update()
        return len(flat)

    def clear_search(self) -> None:
        self._hit_rects = {}
        self._hit_seq = []
        self._hit_flat = []
        self._hit_cur = -1
        self.hit_changed.emit(0, 0)
        self.viewport().update()

    def _current_hit_rect(self) -> Optional[Tuple[int, Tuple[float, float, float, float]]]:
        flat = getattr(self, "_hit_flat", [])
        if 0 <= self._hit_cur < len(flat):
            return flat[self._hit_cur]
        return None

    def goto_hit(self, step: int = 1) -> bool:
        flat = getattr(self, "_hit_flat", [])
        if not flat:
            return False
        self._hit_cur = (self._hit_cur + step) % len(flat)
        page, rect = flat[self._hit_cur]
        self.scroll_to_rect(page, rect)
        self.hit_changed.emit(self._hit_cur + 1, len(flat))
        self.viewport().update()
        return True

    def scroll_to_rect(self, page: int, rect: Tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = map_rect(rect, *self.page_sizes[page], self.rotation)
        px, py = self._page_origin(page)
        target_y = py + (y0 + y1) / 2.0 * self.zoom - self.viewport().height() / 2.0
        target_x = px + (x0 + x1) / 2.0 * self.zoom - self.viewport().width() / 2.0
        self.verticalScrollBar().setValue(int(max(0, target_y)))
        self.horizontalScrollBar().setValue(int(max(0, target_x)))
        self.current_page = page
        self.page_changed.emit(page)

    # ---------------------------------------------------------- zoom/fit
    def set_zoom(self, zoom: float, anchor: Optional[QPoint] = None,
                 clear_fit_mode: bool = True) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(zoom)))
        if abs(zoom - self.zoom) < 1e-6:
            return
        # Explicit zoom clears fit mode unless called from apply_fit
        if clear_fit_mode:
            self.fit_mode = "none"
        anchor_ref = None
        if anchor is not None:
            cx = anchor.x() + self.horizontalScrollBar().value()
            cy = anchor.y() + self.verticalScrollBar().value()
            anchor_ref = self._content_to_page_view(cx, cy)
            anchor_widget = (anchor.x(), anchor.y())

        old_zoom = self.zoom
        self.zoom = zoom
        self._tiles.clear()
        self._tile_grids.clear()
        self._relayout()

        if anchor_ref is not None:
            page, u, v = anchor_ref
            px, py = self._page_origin(page)
            self.verticalScrollBar().setValue(int(max(0, py + v * self.zoom - anchor_widget[1])))
            self.horizontalScrollBar().setValue(int(max(0, px + u * self.zoom - anchor_widget[0])))
        else:
            # Preserve current page position: calculate new scroll position
            # to keep the same page content at the same relative position.
            if self.page_count > 0 and self.current_page < len(self._page_tops):
                page_top = self._page_tops[self.current_page]
                new_vscroll = int(page_top * zoom - self.viewport().height() / 2.0)
                new_vscroll = max(0, min(new_vscroll, self.verticalScrollBar().maximum()))
            else:
                new_vscroll = int(self.verticalScrollBar().value() * (zoom / old_zoom))
            # Block _update_current_page during zoom change
            vbar = self.verticalScrollBar()
            vbar.blockSignals(True)
            vbar.setValue(new_vscroll)
            vbar.blockSignals(False)

        self.zoom_changed.emit(self.zoom)
        self._request_visible()
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom / 1.25)

    def set_fit_mode(self, mode: str) -> None:
        self.fit_mode = mode
        self.apply_fit()

    def apply_fit(self) -> None:
        if self.fit_mode == "none" or not self.page_count:
            return
        vw = max(1, self.viewport().width() - 2 * PAD)
        vh = max(1, self.viewport().height() - 2 * PAD)
        if vw < 50 or vh < 50:
            return          # not laid out yet; resizeEvent will retry
        # Preserve current page position: calculate zoom, then set scroll
        # to keep the same page at the same relative position.
        if self.fit_mode == "width":
            base_w = max(p[0] for p in self.page_sizes)
            factor = display_size(base_w, 1, self.rotation)[0]
            zoom = vw / max(1.0, factor)
        else:  # fit page, using the current page
            w0, h0 = self.page_sizes[min(self.current_page, self.page_count - 1)]
            dw, dh = display_size(w0, h0, self.rotation)
            zoom = min(vw / max(1.0, dw), vh / max(1.0, dh))
        # Calculate scroll position to keep current page at same relative position
        old_zoom = self.zoom
        if old_zoom > 0 and self.page_count > 0:
            # Get the top of the current page in content coordinates
            page_top = self._page_tops[self.current_page] if self.current_page < len(self._page_tops) else 0
            # New scroll position to keep the same page top at the same relative position
            new_vscroll = int(page_top * zoom - self.viewport().height() / 2.0)
            new_vscroll = max(0, min(new_vscroll, self.verticalScrollBar().maximum()))
        else:
            new_vscroll = 0
        # Temporarily block _update_current_page during zoom change
        self.blockSignals(True)
        vbar = self.verticalScrollBar()
        vbar.blockSignals(True)
        try:
            self.zoom = zoom
            self._tiles.clear()
            self._tile_grids.clear()
            self._relayout()
            vbar.setValue(new_vscroll)
            self.zoom_changed.emit(self.zoom)
            self._request_visible()
            self.viewport().update()
        finally:
            vbar.blockSignals(False)
            self.blockSignals(False)

    # ----------------------------------------------------------- rotation
    def set_rotation(self, rotation: int) -> None:
        """Set the absolute view rotation (0/90/180/270, clockwise)."""
        rotation = normalize_rotation(rotation)
        if rotation == self.rotation:
            return
        self.rotation = rotation
        self.cache.clear()          # cached bitmaps are rotation-specific
        self._tiles.clear()         # tiles only render at rotation 0
        self._tile_grids.clear()
        self._relayout()
        self._request_visible()
        self.viewport().update()

    def rotate_by(self, degrees: int) -> None:
        """Rotate the view relative to the current orientation."""
        self.set_rotation(self.rotation + degrees)

    # --------------------------------------------------------- state persistence
    def get_state(self) -> dict:
        """Return a serializable dict with the current view state."""
        return {
            "page": self.current_page,
            "zoom": self.zoom,
            "rotation": self.rotation,
            "fit_mode": self.fit_mode,
            "vscroll": self.verticalScrollBar().value(),
            "hscroll": self.horizontalScrollBar().value(),
        }

    def restore_state(self, state: dict) -> None:
        """Restore view state from a dict returned by get_state()."""
        if not state:
            return
        # Block signals on self and scroll bars to prevent _update_current_page from recalculating page
        self.blockSignals(True)
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.blockSignals(True)
        hbar.blockSignals(True)
        try:
            # Restore fit mode first (affects zoom calculation)
            fit_mode = state.get("fit_mode", "none")
            if fit_mode:
                self.fit_mode = fit_mode
            # Restore rotation before zoom (rotation affects layout)
            rotation = state.get("rotation", 0)
            if rotation != self.rotation:
                self.set_rotation(rotation)
            # Restore zoom only if fit_mode is "none" (otherwise fit mode determines zoom)
            zoom = state.get("zoom", 1.0)
            if self.fit_mode == "none":
                self.set_zoom(zoom)
            # Restore scroll positions first (blocked so _update_current_page not triggered)
            vscroll = state.get("vscroll", 0)
            hscroll = state.get("hscroll", 0)
            if vscroll:
                vbar.setValue(vscroll)
            if hscroll:
                hbar.setValue(hscroll)
            # Restore page directly
            page = state.get("page", 0)
            page = max(0, min(page, self.page_count - 1)) if self.page_count else 0
            self.current_page = page
            # Emit page_changed after all state is restored
            self.page_changed.emit(page)
            self._request_visible()
        finally:
            vbar.blockSignals(False)
            hbar.blockSignals(False)
            self.blockSignals(False)

    # ---------------------------------------------------------- navigate
    def goto_page(self, index: int, center: bool = True) -> None:
        if not self.page_count:
            return
        index = max(0, min(int(index), self.page_count - 1))
        py = self._page_tops[index]
        value = py - (self.viewport().height() / 2.0 if center else 20)
        self.verticalScrollBar().setValue(int(max(0, value)))
        self.current_page = index
        self.page_changed.emit(index)
        self._request_visible()

    def _update_current_page(self) -> None:
        if not self.page_count:
            return
        y = self.verticalScrollBar().value() + 2
        idx = self._page_at_y(y)
        if idx != self.current_page:
            self.current_page = idx
            self.page_changed.emit(idx)

    def page_step(self, direction: int) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction * max(1, self.viewport().height() - 60))

    # ------------------------------------------------------------- events
    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        self._update_current_page()
        self._request_visible()
        self.viewport().update()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.fit_mode != "none":
            self.apply_fit()
        else:
            self._update_scrollbars()
            self._request_visible()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.fit_mode != "none":
            self.apply_fit()
        self._request_visible()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.set_zoom(self.zoom * (1.15 ** steps), anchor=event.position().toPoint())
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            bar = self.verticalScrollBar()
            bar.setValue(int(bar.value() - delta * 0.5))
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._press_pos = event.position().toPoint()
            word = self._word_at(event.position().toPoint())
            if word is None:
                self.clear_selection()
            else:
                self._sel_anchor = word
                self._sel_caret = word
                self._dragging = True
                self.selection_changed.emit()
                self.viewport().update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging:
            word = self._word_at(event.position().toPoint())
            if word is not None and word != self._sel_caret:
                self._sel_caret = word
                self.selection_changed.emit()
                self.viewport().update()
            # auto-scroll while dragging near the edges
            pos = event.position().toPoint()
            if pos.y() < 24:
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 24)
            elif pos.y() > self.viewport().height() - 24:
                self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 24)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            released = event.position().toPoint()
            # a click (press and release at nearly the same spot) clears the
            # selection; a press/release pair with movement keeps it, even if
            # no mouse-move events were delivered in between.
            if (released - self._press_pos).manhattanLength() <= 3:
                self.clear_selection()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        ctrl = mods & Qt.KeyboardModifier.ControlModifier
        shift = mods & Qt.KeyboardModifier.ShiftModifier

        if ctrl and key == Qt.Key.Key_C:
            self.copy_selection()
            event.accept()
        elif ctrl and key == Qt.Key.Key_A:
            self.select_all()
            event.accept()
        elif ctrl and key == Qt.Key.Key_H:
            # Create highlight from selection
            if self.create_highlight_from_selection():
                event.accept()
            else:
                super().keyPressEvent(event)
        elif ctrl and shift and key == Qt.Key.Key_H:
            # Cycle highlight color
            self.next_highlight_color()
            event.accept()
        elif ctrl and key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
            event.accept()
        elif ctrl and key == Qt.Key.Key_Minus:
            self.zoom_out()
            event.accept()
        elif key in (Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self.page_step(+1)
            event.accept()
        elif key in (Qt.Key.Key_PageUp,):
            self.page_step(-1)
            event.accept()
        elif key == Qt.Key.Key_Down:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 48)
            event.accept()
        elif key == Qt.Key.Key_Up:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 48)
            event.accept()
        elif key == Qt.Key.Key_Home:
            self.goto_page(0)
            event.accept()
        elif key == Qt.Key.Key_End:
            self.goto_page(self.page_count - 1)
            event.accept()
        else:
            super().keyPressEvent(event)
