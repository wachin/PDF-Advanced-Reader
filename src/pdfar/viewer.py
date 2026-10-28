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

import bisect
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import (
    QPoint, QPointF, QRect, QRectF, Qt, QThread, QTimer, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QFontMetricsF, QImage, QKeyEvent, QMouseEvent,
    QPaintEvent, QPainter, QPainterPath, QPen, QPixmap, QWheelEvent,
    QGuiApplication, QResizeEvent,
)
from PyQt6.QtWidgets import (
    QAbstractScrollArea, QFrame, QInputDialog, QMenu, QScroller,
)

from .cache import PageCache
from .edits import Edit, EditError, Scope
from .geometry import (
    display_size, map_point, map_rect, normalize_rotation, unmap_delta,
    unmap_point, unmap_rect,
)
from .selection_tools import (
    group_lines, table_size, table_text as format_table, words_in_rect,
)
from .page_numbers import (
    EraseSettings, PageNumberSettings, box_to_settings, erase_box, number_box,
    page_range, pdf_number_font, preview_font,
)
from .reflow import (
    PageLayout, REFLOW_FONT_STEP, REFLOW_PAGE_GAP, ReflowWorker,
    clamp_font_scale, reflow_text_width,
)
from .render import RenderPool, RenderResult
from .search import SearchHit
from .session import DocumentSession
from .tiles import Tile, TileGrid, TILE_SIZE
from .annotations import (
    CUSTOM_STAMP_NAME, INK_COLOR, INK_WIDTH, SHAPE_LABELS, STAMP_LABELS,
    Annotation, AnnotationManager, HIGHLIGHT_COLORS, NoteAnnotation,
)

#: How far apart two captured points of a pen stroke have to be, in screen
#: pixels.  A drag delivers a point every couple of pixels; keeping all of them
#: would put thousands of numbers in the file to draw one line.
PEN_MIN_STEP = 2.0


def _segment_distance(point: tuple, start: tuple, end: tuple) -> float:
    """How far *point* is from the segment *start*–*end*, in page points."""
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0,
                     ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _simplify_outline(points: List[tuple], tolerance: float) -> List[tuple]:
    """Reduce a traced outline to its corners (Ramer–Douglas–Peucker).

    A polygon annotation should hold the corners the user meant, not the jitter
    of the hand.  The tolerance is in page points and comes from the same
    screen-space rule that spaces a stroke, so the result looks the same at any
    zoom.  The split stack is explicit because a long scribble can have more
    points than Python's recursion limit.
    """
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    pending = [(0, len(points) - 1)]
    while pending:
        start, end = pending.pop()
        index, farthest = 0, 0.0
        for i in range(start + 1, end):
            distance = _segment_distance(points[i], points[start], points[end])
            if distance > farthest:
                index, farthest = i, distance
        if farthest > tolerance:
            keep[index] = True
            pending.append((start, index))
            pending.append((index, end))
    return [point for point, kept in zip(points, keep) if kept]

PAD = 16          # outer margin (content coords)
GAP = 12          # gap between pages / columns
MIN_ZOOM = 0.05
MAX_ZOOM = 10.0

# Page layouts reimplemented from Okular's PageView (part/pageview.cpp and
# conf/okular.kcfg, GPL-2.0+/GPL-3.0+).  PDFAR exposes Okular's four view
# modes plus an explicit continuous single-column mode; the layout, pairing
# and centering rules are original Python/PyQt6 code.  Facing/Overview stack
# every row and scroll vertically (Okular's ViewContinuous default); only
# Single Page shows one screen at a time.
VIEW_MODES = ("continuous", "single", "facing", "facing_centered", "overview")
VIEW_MODE_LABELS = {
    "continuous": "Continuous",
    "single": "Single Page",
    "facing": "Facing Pages",
    "facing_centered": "Facing Pages (Center First Page)",
    "overview": "Overview",
}
OVERVIEW_COLUMNS = 3   # Okular's default ViewColumns

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
# The ring around the annotation the comments panel pointed at.  A border rather
# than a fill: the highlight or note icon underneath has to stay recognisable.
ANNOTATION_SELECTED = QColor(0, 150, 255)

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

# Reflow palette: a paper-like surface is easier to read than the dark page
# backdrop used for rendered pages.
LIGHT_REFLOW_BG = QColor(250, 249, 246)
LIGHT_REFLOW_FG = QColor(32, 32, 32)
LIGHT_REFLOW_MUTED = QColor(128, 128, 128)
DARK_REFLOW_BG = QColor(28, 28, 30)
DARK_REFLOW_FG = QColor(225, 225, 225)
DARK_REFLOW_MUTED = QColor(140, 140, 140)
# Decoded reflow figures are cached per visible page; PNG bytes stay in the
# layout, so this budget only covers images the user has actually looked at.
REFLOW_MEDIA_CACHE_BYTES = 96 * 1024 * 1024


class PDFView(QAbstractScrollArea):
    """Continuous-scroll PDF view."""

    page_changed = pyqtSignal(int)          # current page index (0-based)
    zoom_changed = pyqtSignal(float)        # zoom factor
    selection_changed = pyqtSignal()
    hit_changed = pyqtSignal(int, int)      # (current_hit, total_hits), 1-based
    doc_error = pyqtSignal(str)
    reflow_progress = pyqtSignal(int, int)  # (pages laid out, page count)
    reflow_ready = pyqtSignal(bool, int)    # (has_text, pages without content)
    reflow_font_scale_changed = pyqtSignal(float)  # text-size multiplier
    numbering_changed = pyqtSignal(object)  # settings after dragging
    transient_message = pyqtSignal(str)     # short status-bar feedback
    view_mode_changed = pyqtSignal(str)     # page layout mode
    content_changed = pyqtSignal(int, int)  # (content version, Scope value)

    def __init__(self, doc_path: str, parent=None, password: str = ""):
        super().__init__(parent)
        doc = fitz.open(doc_path)
        authenticated = 0
        if doc.needs_pass:
            authenticated = doc.authenticate(password) if password else 0
            if not authenticated:
                doc.close()
                raise ValueError("encrypted")
        self.session = DocumentSession(doc, doc_path, password, authenticated)
        self._invalidated_version = self.session.version

        self.page_count = doc.page_count
        self.page_sizes: List[Tuple[float, float]] = []
        for i in range(self.page_count):
            r = doc[i].rect
            self.page_sizes.append((r.width, r.height))

        self.zoom = 1.0
        self.zoom_step = 0.10           # additive step for buttons / shortcuts
        self.ctrl_wheel_step = True     # Ctrl+wheel uses zoom_step, not fluid zoom
        self.rotation = 0
        self.fit_mode = "none"          # 'none' | 'width' | 'page'
        self.current_page = 0
        self.memory_level = DEFAULT_MEMORY_LEVEL
        self.dark_mode = False
        self._numbering_preview: Optional[
            PageNumberSettings | EraseSettings] = None
        self._numbering_font = None
        self._numbering_drag: Optional[dict] = None

        # Reflow (P1-5): page layouts are prepared by a background worker and
        # cached here; painting only walks the lines that intersect the view.
        self.reflow_mode = False
        self._reflow_worker: Optional[ReflowWorker] = None
        self._reflow_pages: Dict[int, PageLayout] = {}
        self._reflow_tops: List[float] = []
        self._reflow_width = 0
        self._reflow_done = 0
        self._reflow_total = 0
        self._reflow_has_text = False
        self._reflow_has_media = False
        self._reflow_empty_pages = 0
        self._reflow_media_cache: "OrderedDict[int, QImage]" = OrderedDict()
        self._reflow_media_bytes = 0
        self._reflow_pending_page: Optional[int] = None
        self._reflow_pending_center = False
        self._reflow_pending_ratio: Optional[float] = None
        self._reflow_font_scale = 1.0
        self._reflow_error = ""
        self._reflow_fonts: Dict[Tuple[str, int, bool, bool], QFont] = {}
        self._reflow_metrics: Dict[Tuple[str, int, bool, bool], QFontMetrics] = {}
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.setInterval(250)
        self._reflow_timer.timeout.connect(self._maybe_restart_reflow)
        self._vbar_policy_before_reflow = self.verticalScrollBarPolicy()

        self.cache = PageCache()
        self.pool = RenderPool(self.session,
                               workers=min(4, max(2, (QThread.idealThreadCount() or 2))))
        self.pool.result_ready.connect(self._on_result)

        # Tiled rendering (Okular large-page strategy): a page that would be
        # rendered far larger than the viewport is split into square tiles and
        # only the visible ones are rendered.  Tiles are stored here (GUI
        # thread writes/reads), separate from PageCache which holds full pages.
        self._tiles: Dict[int, Dict[Tuple[int, int], QImage]] = {}
        self._tile_grids: Dict[int, TileGrid] = {}

        # layout
        self.view_mode = "continuous"
        self._disp_sizes: List[Tuple[float, float]] = []
        self._page_tops: List[float] = []
        self._page_rects: List[Optional[Tuple[float, float, float, float]]] = []
        self._rows: List[List[int]] = []
        self._row_tops: List[float] = []
        self._row_heights: List[float] = []
        self._content_h = 0.0
        self._content_w = 0.0
        self._max_w = 0.0

        # text layer
        self._words: Dict[int, List[Tuple[float, float, float, float, str]]] = {}

        # selection: (page, word-index) pairs
        self._sel_anchor: Optional[Tuple[int, int]] = None
        self._sel_caret: Optional[Tuple[int, int]] = None
        self._dragging = False

        # Mouse tools (Okular-style): browse pan, text, area, table, pen.
        self.tool_mode = "text"
        self._marquee: Optional[dict] = None
        self._panning = False
        self._pan_last = QPoint()
        #: The stroke being dragged: ``{"page": int, "points": [(x, y), …]}`` in
        #: page space, or ``None`` when the pen is not down.
        self._stroke: Optional[dict] = None
        #: What the pen draws with.  Both go into the annotation, so a drawing
        #: keeps its look when another reader shows the file.  The shape tool
        #: draws with the same two attributes.
        self.ink_color = INK_COLOR
        self.ink_width = INK_WIDTH
        #: The shape the shape tool draws next, and the drag in progress:
        #: ``{"page", "kind", "start", "current", "points"}`` in page space.
        self.shape_kind = "rect"
        self._shape_drag: Optional[dict] = None
        #: The standard stamp the stamp tool places on the next click.
        self.stamp_name = "Draft"
        #: A stamp of PDFAR's own making, which takes the click instead:
        #: ``{"kind": "text"|"image", "caption": ..., "image": ...}``.
        self.custom_stamp: Optional[dict] = None

        # search
        self._hit_rects: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self._hit_seq: List[Tuple[int, int]] = []
        self._hit_flat: List[Tuple[int, Tuple[float, float, float, float]]] = []
        self._hit_cur = -1

        self._errors: Dict[int, str] = {}
        self._press_pos = QPoint()

        # Annotations
        self.annotation_manager = AnnotationManager(self.session, self)
        self.annotation_manager.annotations_edited.connect(self._on_annotation_edit)
        self.annotation_manager.annotation_edit_failed.connect(self._on_annotation_edit_failed)
        self._highlight_color_idx = 0  # Current highlight color index
        # The annotation the comments panel last pointed at, by id: the document
        # can drop it from under us, so nothing about it is cached.
        self._selected_ann: Optional[str] = None

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
        self._apply_tool_cursor()

    # ----------------------------------------------------------------- doc
    @property
    def doc(self) -> fitz.Document:
        """The live document — read it through the session, never store it.

        An accepted edit (or an undo) replaces the ``Document`` object, so a
        reference kept anywhere else becomes a handle on a closed document.
        """
        return self.session.doc

    @property
    def doc_path(self) -> str:
        return self.session.path

    @property
    def title(self) -> str:
        try:
            return (self.doc.metadata or {}).get("title") or ""
        except Exception:
            return ""

    def shutdown(self) -> None:
        self._reflow_timer.stop()
        self._stop_reflow_worker()
        self.pool.shutdown(wait=True)
        self.session.close()

    def apply_edit(self, edit: Edit) -> bool:
        """Run *edit* against a working copy and adopt it if it succeeded.

        On failure the user keeps the document exactly as it was and gets the
        reason in the status bar; nothing is re-rendered because nothing changed.
        """
        try:
            _version, scope = self.session.apply(edit)
        except EditError as exc:
            self.transient_message.emit(f"{edit.label}: {exc}")
            return False
        self.invalidate(scope)
        return True

    def undo_edit(self) -> bool:
        """Undo the last edit.  False with a reason when there is nothing to undo."""
        return self._step_edit(self.session.undo)

    def redo_edit(self) -> bool:
        """Redo the last undone edit.  False with a reason when there is none."""
        return self._step_edit(self.session.redo)

    def _step_edit(self, step) -> bool:
        try:
            _version, scope = step()
        except EditError as exc:
            self.transient_message.emit(str(exc))
            return False
        self.invalidate(scope)
        return True

    def invalidate(self, scope: Scope = Scope.FULL) -> int:
        """React to a change to the document and drop what *scope* made stale.

        An edit that came through ``apply_edit`` / ``undo_edit`` / ``redo_edit``
        has already bumped the version; a change made in place has not, and bumps
        here — readers are turned away by version, so a change that never bumped
        would be painted over by renders of the old content.  What is graded is
        the expensive part: re-rendering 540 pages for a title change is the waste
        scope grading exists to prevent (F10-1).
        """
        version = self.session.version
        if version == self._invalidated_version:
            # The change did not arrive through the session — a rare path now that
            # annotations are edits too — so bump it here. Without a new version,
            # renders of the old content that are already in flight would be
            # accepted as current the moment they land.
            version = self.session.content_changed()
        self._invalidated_version = version
        # The document is the annotation store, so a document we have never read
        # is an annotation list we have never read: re-derive it (F10-3).
        self.annotation_manager.reload_from_document()
        if scope & self._PAINTED:
            self.pool.cancel_if(lambda job: True)
            self.cache.clear()
            self._tiles.clear()
            self._tile_grids.clear()
            self._errors.clear()
        if scope & self._STALE_TEXT:
            self._words.clear()
            self._hit_rects.clear()
            self._hit_seq.clear()
            self._hit_flat.clear()
        if scope & Scope.STRUCTURE:
            self._refresh_page_geometry()
        if scope & self._PAINTED:
            self._relayout()
            if self.reflow_mode and scope & (Scope.STRUCTURE | Scope.CONTENT):
                self._enter_reflow()
            self._request_visible()
        self.content_changed.emit(version, int(scope))
        self.viewport().update()
        return version

    # Kept in sync with ``Scope``: what each kind of change can make stale.
    # ``LINKS`` is deliberately in neither set — an invisible link rectangle does
    # not move a pixel or a word.
    _PAINTED = Scope.STRUCTURE | Scope.CONTENT | Scope.COMMENTS | Scope.FORM
    _STALE_TEXT = Scope.STRUCTURE | Scope.CONTENT

    def _refresh_page_geometry(self) -> None:
        """Re-derive the page count and page sizes from the live document."""
        self.page_count = self.doc.page_count
        self.page_sizes = [(self.doc[i].rect.width, self.doc[i].rect.height)
                           for i in range(self.page_count)]
        if self.current_page >= self.page_count:
            self.current_page = max(0, self.page_count - 1)

    def _on_annotation_edit(self, scope: int) -> None:
        """An annotation reached the document, so what it paints has to change.

        The manager owns the edit; this is the half that says which caches the
        change can have stale — ``Scope.COMMENTS`` is in the painted set, so the
        page is re-rendered rather than patched with an overlay.
        """
        self.invalidate(Scope(scope))

    def _on_annotation_edit_failed(self, reason: str) -> None:
        """The user asked for an annotation and did not get one: say why."""
        self.transient_message.emit(reason)

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

        # Selection rects grouped by page.  Words are read in page space and a
        # highlight is stored in page space, so they go straight through: mapping
        # them onto the screen first would move the answer under view rotation,
        # and merge the lines along the wrong axis.
        page_rects: Dict[int, List[tuple]] = {}
        for page, _idx, word in self._iter_selected_words():
            page_rects.setdefault(page, []).append(word[:4])

        # Merge adjacent rects on the same line for cleaner highlights
        merged_rects: Dict[int, List[tuple]] = {}
        for page, rects in page_rects.items():
            merged_rects[page] = self._merge_selection_rects(rects)

        # Create the highlight: one edit, so one undo step, however many pages
        # the selection reached
        author = ""  # Could be made configurable
        made = self.annotation_manager.add_highlights(merged_rects, color, author)

        self.clear_selection()
        return bool(made)

    def _merge_selection_rects(self, rects: List[tuple], y_tolerance: float = 2.0) -> List[tuple]:
        """Merge adjacent selection rectangles on the same text line.

        Args:
            rects: List of (x0, y0, x1, y1) rectangles, in page space
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

    # ------------------------------------------------------------- sticky notes
    def _pdf_point_at(self, pos: QPoint) -> Optional[Tuple[int, float, float]]:
        """Widget coordinate -> ``(page, x, y)`` in PDF space, ``None`` if off-page.

        The road from a click to a point on the page runs through the scroll
        bars, the page layout, the zoom and the view rotation, and a note is
        stored in the last space of those, so the click has to be walked all the
        way back.  ``_numbering_hit_at()`` walks the same road, so the two tools
        cannot disagree about where the cursor is.
        """
        if not self.page_count:
            return None
        cx = pos.x() + self.horizontalScrollBar().value()
        cy = pos.y() + self.verticalScrollBar().value()
        hit = self._content_to_page_view(cx, cy)
        if hit is None:
            return None
        page, u, v = hit
        w0, h0 = self.page_sizes[page]
        dw, dh = display_size(w0, h0, self.rotation)
        if not (0.0 <= u <= dw and 0.0 <= v <= dh):
            return None
        x, y = unmap_point(u, v, w0, h0, self.rotation)
        return page, x, y

    def _note_at(self, page: int, x: float, y: float) -> Optional[NoteAnnotation]:
        """The note whose icon covers this PDF point.

        The icon is the whole target, with a couple of points of slack: it is
        16 pt across, and a click rounded to a widget pixel can land just off
        its edge.  A click that misses the icon means "a note *here*", not
        "that note".
        """
        tolerance = 2.0
        for note in self.annotation_manager.get_notes_for_page(page):
            if not note.rects:
                continue
            x0, y0, x1, y1 = note.rects[0]
            if (x0 - tolerance <= x <= x1 + tolerance
                    and y0 - tolerance <= y <= y1 + tolerance):
                return note
        return None

    def _note_menu(self, at: Tuple[int, float, float],
                   note: Optional[NoteAnnotation] = None) -> QMenu:
        """The menu for a right-click at *at*: open the note on it, or add one."""
        menu = QMenu(self)
        if note is not None:
            act_edit = menu.addAction("Edit Note…")
            act_edit.triggered.connect(lambda _=False, n=note: self.edit_note(n.id))
            act_delete = menu.addAction("Delete Note")
            act_delete.triggered.connect(lambda _=False, n=note: self.delete_note(n.id))
        else:
            act_add = menu.addAction("Add note here…")
            act_add.triggered.connect(lambda _=False, p=at: self.add_note_at(*p))
        return menu

    def add_note_at(self, page: int, x: float, y: float) -> bool:
        """Ask for a note, then put it on *page* at the PDF point ``(x, y)``.

        An empty answer still places the icon: the user asked for a note, and a
        note with no text is a note that has not been written in yet.  Cancel
        places nothing and leaves the undo stack alone.
        """
        text, ok = QInputDialog.getMultiLineText(
            self, "Add Note", f"Note on page {page + 1}:", "")
        if not ok:
            return False
        return self.annotation_manager.add_note(page, (x, y), text.strip()) is not None

    def edit_note(self, annotation_id: str) -> bool:
        """Change the text a note carries."""
        ann = self._annotation_or_warn(annotation_id)
        if ann is None:
            return False
        text, ok = QInputDialog.getMultiLineText(
            self, "Edit Note", f"Note on page {ann.page + 1}:", ann.get_note())
        if not ok:
            return False
        if not text.strip():
            # Emptying the text is what "Remove Note" has always meant.
            return (self.annotation_manager.remove_note(annotation_id)
                    if ann.has_note() else False)
        return self.annotation_manager.set_note(annotation_id, text.strip()) is not None

    def delete_note(self, annotation_id: str) -> bool:
        """Take a note and its icon off the page."""
        if self._annotation_or_warn(annotation_id) is None:
            return False
        return self.annotation_manager.remove_annotation(annotation_id)

    def _annotation_or_warn(self, annotation_id: str) -> Optional[Annotation]:
        """The annotation a menu was built for, or ``None`` with the reason said.

        A menu can outlive its target: undoing some other edit can make the
        annotation it pointed at disappear before the click lands.
        """
        ann = self.annotation_manager.get_annotation(annotation_id)
        if ann is None:
            self.transient_message.emit("That annotation is not in this document")
        return ann

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        """Right-click a page: place a note, or open the one under the cursor."""
        if self.reflow_mode:
            return          # reflow is its own document, with no page geometry
        hit = self._pdf_point_at(event.pos())
        if hit is None:
            return
        page, x, y = hit
        self._note_menu(hit, self._note_at(page, x, y)).exec(event.globalPos())

    # ------------------------------------------------- annotation navigation
    def select_annotation(self, annotation_id: str) -> bool:
        """Scroll an annotation into view and draw the view's attention on it.

        The comments panel names an annotation, and a page on a long document is
        not an address: the ring is what says *this* one.
        """
        ann = self._annotation_or_warn(annotation_id)
        if ann is None or not ann.rects:
            return False
        rects = ann.rects
        box = (min(r[0] for r in rects), min(r[1] for r in rects),
               max(r[2] for r in rects), max(r[3] for r in rects))
        self._selected_ann = annotation_id
        self.scroll_to_rect(ann.page, box)
        self.viewport().update()
        return True

    def clear_annotation_selection(self) -> None:
        """Let go of the annotation the panel had pointed at."""
        if self._selected_ann is None:
            return
        self._selected_ann = None
        self.viewport().update()

    def _annotation_outline_rects(self, page: int) -> List[QRect]:
        """Where the selected annotation paints, if it is on *page*.

        Resolved from the id on every call rather than from cached geometry: the
        document can lose the annotation between one paint and the next (an undo,
        a revert), and a ring around the place it used to be would report
        something that is not there.
        """
        ann = (self.annotation_manager.get_annotation(self._selected_ann)
               if self._selected_ann else None)
        if ann is None or ann.page != page:
            return []
        px, py = self._page_origin(page)
        px -= self.horizontalScrollBar().value()
        py -= self.verticalScrollBar().value()
        w0, h0 = self.page_sizes[page]
        z = self.zoom
        out = []
        for rect in ann.rects:
            x0, y0, x1, y1 = map_rect(rect, w0, h0, self.rotation)
            out.append(QRect(round(px + x0 * z), round(py + y0 * z),
                             max(1, round((x1 - x0) * z)),
                             max(1, round((y1 - y0) * z))))
        return out

    def set_dark_mode(self, enabled: bool) -> None:
        """Enable or disable dark mode."""
        if self.dark_mode == enabled:
            return
        self.dark_mode = enabled
        self._apply_theme()
        self.viewport().update()

    # ---------------------------------------------------------- mouse tools
    TOOL_MODES = ("browse", "text", "area", "table", "draw", "shape", "stamp")

    #: Tools that need the page geometry reflow does not have.
    _PAGE_ONLY_TOOLS = ("area", "table", "draw", "shape", "stamp")

    def set_tool_mode(self, mode: str) -> None:
        """Switch the left-button tool: browse, text, area, table, pen, shape
        or stamp."""
        if mode not in self.TOOL_MODES:
            return
        if self.reflow_mode and mode in self._PAGE_ONLY_TOOLS:
            mode = "text"
        if mode == self.tool_mode:
            self._apply_tool_cursor()
            return
        self.tool_mode = mode
        self._cancel_marquee()
        self._cancel_stroke()
        self._cancel_shape()
        self._panning = False
        if mode != "text":
            self.clear_selection()
        self._apply_tool_cursor()
        self.viewport().update()

    def set_shape_kind(self, kind: str) -> None:
        """Choose which shape the shape tool draws next."""
        if kind not in SHAPE_LABELS:
            return
        self.shape_kind = kind
        self.viewport().update()

    def set_stamp_name(self, name: str) -> None:
        """Choose the standard stamp the stamp tool places next."""
        if name not in STAMP_LABELS:
            return
        self.stamp_name = name
        self.custom_stamp = None

    def set_custom_stamp(self, kind: str, caption: str = "",
                         image: str = "") -> None:
        """Choose a stamp drawn from a caption or an image file.

        ``kind`` is ``"text"`` (a one-line caption) or ``"image"`` (a path).
        The definition belongs to the view, like the pen's colour, so two tabs
        can place two different stamps; the next click uses it instead of the
        standard name.
        """
        if kind == "text" and not caption.strip():
            return
        if kind == "image" and not image:
            return
        if kind not in ("text", "image"):
            return
        self.custom_stamp = {"kind": kind, "caption": caption, "image": image}
        self.stamp_name = CUSTOM_STAMP_NAME

    def set_ink_color(self, color: str) -> None:
        """Choose the colour the pen writes into the annotation's ``/C``.

        The stroke in progress is painted from these two attributes, so a
        colour chosen mid-drag changes the preview with it.
        """
        self.ink_color = color
        self.viewport().update()

    def set_ink_width(self, width: float) -> None:
        """Choose the thickness the pen writes into the annotation's ``/BS``."""
        self.ink_width = float(width)
        self.viewport().update()

    def _apply_tool_cursor(self) -> None:
        """Show the cursor for the active tool unless numbering overrides it."""
        if self._numbering_preview is not None:
            return
        if self.tool_mode == "browse":
            shape = (Qt.CursorShape.ClosedHandCursor if self._panning
                     else Qt.CursorShape.OpenHandCursor)
        elif self.tool_mode == "text":
            shape = Qt.CursorShape.IBeamCursor
        else:
            shape = Qt.CursorShape.CrossCursor
        self.viewport().setCursor(shape)

    def _update_hover_cursor(self, pos: QPoint) -> None:
        if (self._numbering_preview is not None and not self.reflow_mode
                and self._numbering_hit_at(pos) is not None):
            self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
            return
        self._apply_tool_cursor()

    def _cancel_marquee(self) -> None:
        if self._marquee is not None:
            self._marquee = None
            self.viewport().update()

    def _cancel_stroke(self) -> None:
        """Give up the stroke in progress: it never reaches the document."""
        if self._stroke is not None:
            self._stroke = None
            self.viewport().update()

    def _extend_stroke(self, pos: QPoint) -> None:
        """Add the point under the cursor to the stroke in progress.

        The point is captured in page space, the space the annotation will be
        stored in, so scroll bars, zoom and a turned view cannot move it later.
        A point closer than :data:`PEN_MIN_STEP` to the last one kept is
        dropped: a drag reports every couple of pixels, and a line that leaned on
        that would be thousands of numbers in the file for one stroke.
        """
        stroke = self._stroke
        hit = self._pdf_point_at(pos)
        if hit is None or hit[0] != stroke["page"]:
            return                      # off the page, or over a neighbour
        _page, x, y = hit
        last_x, last_y = stroke["points"][-1]
        step = PEN_MIN_STEP / self.zoom
        if abs(x - last_x) < step and abs(y - last_y) < step:
            return
        stroke["points"].append((x, y))
        self.viewport().update()

    def _commit_stroke(self, stroke: dict, pos: QPoint) -> None:
        """Hand the finished stroke to the document as one edit.

        One press-to-release is one drawing, so it is one annotation and one
        Ctrl+Z — the same rule a highlight of several lines follows (F10-3a).
        The release position is appended when it is new, so the stroke ends
        where the pen was lifted rather than at the last move Qt delivered.
        """
        points = stroke["points"]
        hit = self._pdf_point_at(pos)
        if hit is not None and hit[0] == stroke["page"]:
            _page, x, y = hit
            last_x, last_y = points[-1]
            if abs(x - last_x) > 0.01 or abs(y - last_y) > 0.01:
                points.append((x, y))
        self.annotation_manager.add_ink(stroke["page"], [points],
                                        color=self.ink_color,
                                        width=self.ink_width)

    def _cancel_shape(self) -> None:
        """Give up the shape in progress: it never reaches the document."""
        if self._shape_drag is not None:
            self._shape_drag = None
            self.viewport().update()

    def _extend_shape(self, pos: QPoint) -> None:
        """Move the shape in progress to the point under the cursor.

        A rectangle, ellipse, line or arrow keeps one moving corner in page
        space; a polygon keeps the outline the user traced, with the same
        :data:`PEN_MIN_STEP` spacing rule a stroke follows, and is reduced to
        its corners when the button comes up.  A point off the page is ignored,
        so the shape ends at the paper rather than beyond it.
        """
        drag = self._shape_drag
        hit = self._pdf_point_at(pos)
        if hit is None or hit[0] != drag["page"]:
            return
        _page, x, y = hit
        if drag["kind"] == "polygon":
            last_x, last_y = drag["points"][-1]
            step = PEN_MIN_STEP / self.zoom
            if abs(x - last_x) < step and abs(y - last_y) < step:
                return
            drag["points"].append((x, y))
        drag["current"] = (x, y)
        self.viewport().update()

    def _commit_shape(self, drag: dict) -> None:
        """Hand the finished drag to the document as one shape edit.

        One drag is one annotation and one Ctrl+Z, the rule the pen follows.
        A drag with no extent is dropped: the edit would refuse it anyway, and
        a click is not a shape the user meant to draw.
        """
        kind = drag["kind"]
        color = self.ink_color
        width = self.ink_width
        if kind in ("rect", "ellipse"):
            sx, sy = drag["start"]
            ex, ey = drag["current"]
            self.annotation_manager.add_shape(
                drag["page"], kind, rect=(sx, sy, ex, ey),
                color=color, width=width)
        elif kind in ("line", "arrow"):
            self.annotation_manager.add_shape(
                drag["page"], kind, points=(drag["start"], drag["current"]),
                color=color, width=width)
        else:
            tolerance = PEN_MIN_STEP / self.zoom
            traced = list(drag["points"])
            if traced[-1] != drag["current"]:
                traced.append(drag["current"])
            points = _simplify_outline(traced, tolerance)
            # Tracing all the way back to the start repeats the first corner;
            # a /Polygon closes itself, so the repeat is noise.
            if len(points) >= 3 and math.dist(points[0], points[-1]) <= tolerance:
                points = points[:-1]
            if len(points) >= 3:
                self.annotation_manager.add_shape(
                    drag["page"], "polygon", points=tuple(points),
                    color=color, width=width)

    # ------------------------------------------------------ numbering preview
    def set_numbering_preview(
            self, settings: Optional[PageNumberSettings
                                 | EraseSettings]) -> None:
        """Show the mask and red page numbers where they would be stamped."""
        self._numbering_preview = settings
        self._numbering_drag = None
        if settings is None:
            self._numbering_font = None
            self._apply_tool_cursor()
        elif isinstance(settings, EraseSettings):
            self._numbering_font = None
        else:
            self._numbering_font = pdf_number_font(settings)
        self.viewport().update()

    def clear_numbering_preview(self) -> None:
        if self._numbering_preview is not None:
            self.set_numbering_preview(None)

    def _preview_box(self, settings, page: int, first: int, last: int):
        w0, h0 = self.page_sizes[page]
        if isinstance(settings, EraseSettings):
            return erase_box(settings, w0, h0)
        return number_box(
            settings, w0, h0, settings.start_number + (page - first),
            last - first + 1, font=self._numbering_font)

    def _numbering_hit_at(self, pos: QPoint):
        """Return ``(page, NumberBox)`` when ``pos`` is on a preview number."""
        settings = self._numbering_preview
        if settings is None or self.reflow_mode or not self.page_count:
            return None
        cx = pos.x() + self.horizontalScrollBar().value()
        cy = pos.y() + self.verticalScrollBar().value()
        hit = self._content_to_page_view(cx, cy)
        if hit is None:
            return None
        page, u, v = hit
        w0, h0 = self.page_sizes[page]
        dw, dh = display_size(w0, h0, self.rotation)
        if not (0.0 <= u <= dw and 0.0 <= v <= dh):
            return None
        first, last = page_range(settings, self.page_count)
        if not (first <= page <= last):
            return None
        box = self._preview_box(settings, page, first, last)
        x, y = unmap_point(u, v, w0, h0, self.rotation)
        x0, y0, x1, y1 = box.mask_rect
        tolerance = 2.0
        if (x0 - tolerance <= x <= x1 + tolerance
                and y0 - tolerance <= y <= y1 + tolerance):
            return page, box
        return None

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
        # Ensure scrollbars don't inherit main window's stylesheet
        self.verticalScrollBar().setStyleSheet("")
        self.horizontalScrollBar().setStyleSheet("")

    # ------------------------------------------------------------ reflow mode
    def set_reflow_mode(self, enabled: bool) -> None:
        """Enter or leave the single-column text view (P1-5).

        Extraction and layout run on a :class:`ReflowWorker`; the GUI stays
        responsive and shows a progress message while pages are prepared.
        """
        enabled = bool(enabled)
        if self.reflow_mode == enabled:
            return
        self.reflow_mode = enabled
        if enabled:
            self.clear_selection()
            if self.tool_mode in self._PAGE_ONLY_TOOLS:
                self.set_tool_mode("text")
            self._enter_reflow()
        else:
            self._leave_reflow()
        self.viewport().update()

    def is_reflow_mode(self) -> bool:
        return self.reflow_mode

    @property
    def reflow_has_media(self) -> bool:
        """True when at least one reflowed page contains an image or table."""
        return self._reflow_has_media

    @property
    def reflow_font_scale(self) -> float:
        """Text-size multiplier used by the reflow layout (1.0 = default)."""
        return self._reflow_font_scale

    def reflow_text_bigger(self) -> None:
        self.set_reflow_font_scale(self._reflow_font_scale * REFLOW_FONT_STEP)

    def reflow_text_smaller(self) -> None:
        self.set_reflow_font_scale(self._reflow_font_scale / REFLOW_FONT_STEP)

    def set_reflow_font_scale(self, scale: float) -> None:
        """Change the reflow text size and rebuild the column.

        The viewport keeps the same reading position: the page and the
        relative offset inside it are restored once the new layout of that
        page is ready.
        """
        scale = clamp_font_scale(scale)
        if abs(scale - self._reflow_font_scale) < 1e-6:
            return
        self._reflow_font_scale = scale
        if self.reflow_mode and self.page_count:
            page = self.current_page
            ratio = self._reflow_view_ratio(page)
            self._enter_reflow()
            self._reflow_pending_page = page
            self._reflow_pending_center = False
            self._reflow_pending_ratio = ratio
        self.reflow_font_scale_changed.emit(scale)

    def _enter_reflow(self) -> None:
        self._stop_reflow_worker()
        self._reflow_pages.clear()
        self._reflow_done = 0
        self._reflow_total = self.page_count
        self._reflow_has_text = False
        self._reflow_has_media = False
        self._reflow_empty_pages = 0
        self._reflow_media_cache.clear()
        self._reflow_media_bytes = 0
        self._reflow_error = ""
        self._reflow_pending_page = None
        self._reflow_pending_center = False
        self._reflow_pending_ratio = None

        # Reserve the scrollbar so the column width does not change (and the
        # layout does not need rebuilding) once the height becomes known.
        self._vbar_policy_before_reflow = self.verticalScrollBarPolicy()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._reflow_width = self._reflow_available_width()

        self._recompute_reflow_layout()
        if self.page_count:
            self.goto_page(self.current_page, center=False)

        if self.page_count:
            worker = ReflowWorker(self.session, self._reflow_width,
                                  font_scale=self._reflow_font_scale, parent=None)
            worker.page_ready.connect(self._on_reflow_page)
            worker.progress.connect(self._on_reflow_progress)
            worker.failed.connect(self._on_reflow_failed)
            worker.finished.connect(self._on_reflow_finished)
            self._reflow_worker = worker
            # The initial scroll position was computed from estimated heights.
            # Re-anchor once the current page's real layout arrives.
            self._reflow_pending_page = self.current_page
            self._reflow_pending_center = False
            worker.start()
        else:
            self.reflow_progress.emit(0, 0)
            self.reflow_ready.emit(False, 0)

    def _leave_reflow(self) -> None:
        self._reflow_timer.stop()
        self._stop_reflow_worker()
        self._reflow_pages.clear()
        self._reflow_media_cache.clear()
        self._reflow_media_bytes = 0
        self._reflow_tops = []
        self._reflow_done = 0
        self._reflow_total = 0
        self._reflow_pending_page = None
        self._reflow_pending_center = False
        self._reflow_pending_ratio = None
        self.setVerticalScrollBarPolicy(self._vbar_policy_before_reflow)
        page = self.current_page
        self._relayout()
        if self.page_count:
            self.goto_page(page, center=False)
        self._request_visible()

    def _stop_reflow_worker(self) -> None:
        worker = self._reflow_worker
        self._reflow_worker = None
        if worker is None:
            return
        worker.requestInterruption()
        # Deterministic shutdown: never destroy a running QThread.
        worker.wait()
        try:
            worker.page_ready.disconnect()
            worker.progress.disconnect()
            worker.failed.disconnect()
            worker.finished.disconnect()
        except TypeError:
            pass
        worker.deleteLater()

    def _reflow_available_width(self) -> int:
        vw = self.viewport().width()
        if vw <= 1:
            vw = max(1, self.width())
        vbar = self.verticalScrollBar()
        if (self.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn
                and not vbar.isVisible()):
            # The policy was just changed; the viewport has not grown/shrunk
            # to account for the bar yet.
            vw -= vbar.sizeHint().width()
        return reflow_text_width(vw)

    def _reflow_font(self, family: str, size: int, bold: bool, italic: bool) -> QFont:
        key = (family, size, bold, italic)
        font = self._reflow_fonts.get(key)
        if font is None:
            font = QFont(family)
            font.setPixelSize(size)
            font.setBold(bold)
            font.setItalic(italic)
            self._reflow_fonts[key] = font
        return font

    def _reflow_metrics_for(self, family: str, size: int, bold: bool,
                            italic: bool) -> QFontMetrics:
        key = (family, size, bold, italic)
        metrics = self._reflow_metrics.get(key)
        if metrics is None:
            metrics = QFontMetrics(self._reflow_font(family, size, bold, italic))
            self._reflow_metrics[key] = metrics
        return metrics

    # -- worker slots ------------------------------------------------------
    def _on_reflow_page(self, layout: PageLayout) -> None:
        if not isinstance(layout, PageLayout):
            return
        self._reflow_pages[layout.page] = layout
        self._reflow_done += 1
        if layout.has_text:
            self._reflow_has_text = True
        if layout.media:
            self._reflow_has_media = True
        if not layout.has_text and not layout.media:
            self._reflow_empty_pages += 1
        self._recompute_reflow_layout()

        pending = self._reflow_pending_page
        if pending is not None and pending in self._reflow_pages:
            self._reflow_pending_page = None
            ratio = self._reflow_pending_ratio
            self._reflow_pending_ratio = None
            if ratio is None:
                self._scroll_to_reflow_page(pending, center=self._reflow_pending_center)
            else:
                self._scroll_to_reflow_ratio(pending, ratio)
            self._reflow_pending_center = False
        self.viewport().update()

    def _on_reflow_progress(self, done: int, total: int) -> None:
        self.reflow_progress.emit(done, total)

    def _on_reflow_failed(self, message: str) -> None:
        self._reflow_error = message
        self.reflow_ready.emit(False, self.page_count)
        self.viewport().update()

    def _on_reflow_finished(self) -> None:
        if self._reflow_worker is not None:
            worker = self._reflow_worker
            self._reflow_worker = None
            worker.deleteLater()
        self._recompute_reflow_layout()
        self.reflow_progress.emit(self._reflow_done, self.page_count)
        self.reflow_ready.emit(self._reflow_has_text, self._reflow_empty_pages)
        self.viewport().update()

    # -- layout ------------------------------------------------------------
    def _reflow_estimate_height(self, page: int) -> float:
        """Height guess for a page the worker has not reached yet.

        Keeps the scrollbar monotonic and lets ``goto_page`` work while the
        document is still being prepared; the real height replaces it as soon
        as the page arrives.
        """
        if not self.page_count:
            return 0.0
        w, h = self.page_sizes[page]
        # A reflowed column is roughly as tall as the original page's text,
        # scaled by the width ratio.  At a fixed column width the text area
        # grows roughly with the square of the font scale (fewer characters
        # per line, taller lines).
        text_w = max(1, self._reflow_width)
        ratio = (w / text_w) if text_w else 1.0
        estimate = float(h) * max(0.5, min(ratio, 2.0))
        estimate *= self._reflow_font_scale ** 2
        return max(200.0, estimate)

    def _recompute_reflow_layout(self) -> None:
        top = float(PAD)
        self._reflow_tops = []
        for i in range(self.page_count):
            self._reflow_tops.append(top)
            layout = self._reflow_pages.get(i)
            height = layout.height if layout is not None else self._reflow_estimate_height(i)
            top += height
            if i < self.page_count - 1:
                top += REFLOW_PAGE_GAP
        self._content_h = (top + PAD) if self.page_count else 0.0
        self._content_w = float(self.viewport().width())
        # Shared with _update_current_page / visible_range.
        self._page_tops = list(self._reflow_tops)
        self._update_scrollbars()

    def _maybe_restart_reflow(self) -> None:
        if not self.reflow_mode:
            return
        width = self._reflow_available_width()
        if width == self._reflow_width:
            return
        page = self.current_page
        ratio = self._reflow_view_ratio(page)
        self._enter_reflow()
        self._reflow_pending_page = page
        self._reflow_pending_center = False
        self._reflow_pending_ratio = ratio

    def _reflow_view_ratio(self, page: int) -> float:
        """Relative offset of the viewport top inside *page*'s section."""
        if page < 0 or page >= len(self._reflow_tops):
            return 0.0
        layout = self._reflow_pages.get(page)
        height = layout.height if layout is not None else self._reflow_estimate_height(page)
        if height <= 0:
            return 0.0
        offset = (self.verticalScrollBar().value() - self._reflow_tops[page]) / height
        return max(0.0, min(1.0, offset))

    def _scroll_to_reflow_ratio(self, page: int, ratio: float) -> None:
        if page < 0 or page >= len(self._reflow_tops):
            return
        layout = self._reflow_pages.get(page)
        height = layout.height if layout is not None else self._reflow_estimate_height(page)
        value = self._reflow_tops[page] + max(0.0, min(1.0, ratio)) * height
        self.verticalScrollBar().setValue(int(max(0, value)))

    def _scroll_to_reflow_page(self, page: int, center: bool = True) -> None:
        if page < 0 or page >= len(self._reflow_tops):
            return
        top = self._reflow_tops[page]
        # When not centring, put the page start at the viewport top.  A margin
        # here would leave the scroll position in the previous page's gap and
        # make _update_current_page report the wrong page.
        value = top - (self.viewport().height() / 2.0 if center else 0.0)
        self.verticalScrollBar().setValue(int(max(0, value)))

    def _reflow_page_at(self, y: float) -> int:
        if not self._reflow_tops:
            return 0
        idx = bisect.bisect_right(self._reflow_tops, y) - 1
        return max(0, min(idx, self.page_count - 1))

    # ------------------------------------------------------------- layout
    def _unit_size(self, index: int) -> Tuple[float, float]:
        """Displayed page size at zoom 1 (rotation applied)."""
        w0, h0 = self.page_sizes[index]
        return display_size(w0, h0, self.rotation)

    def _rows_for_mode(self) -> List[List[int]]:
        """Page grouping into rows for the active view mode.

        Continuous and Single use one page per row; Facing pairs pages from
        page 0; the centered variant puts page 0 alone, then pairs, and also
        centers the last page when the page count is even (Okular's
        ``centerFirstPage`` / ``centerLastPage``).  Overview uses
        ``OVERVIEW_COLUMNS`` pages per row.
        """
        n = self.page_count
        if not n:
            return []
        mode = self.view_mode
        if mode == "single":
            return [[min(self.current_page, n - 1)]]
        if mode == "overview":
            cols = min(OVERVIEW_COLUMNS, n)
            return [list(range(i, min(i + cols, n)))
                    for i in range(0, n, cols)]
        if mode in ("facing", "facing_centered"):
            rows: List[List[int]] = []
            i = 0
            if mode == "facing_centered":
                rows.append([0])
                i = 1
            while i < n:
                rows.append([i] if i + 1 >= n else [i, i + 1])
                i += 2
            return rows
        return [[i] for i in range(n)]

    def _relayout(self) -> None:
        if self.reflow_mode:
            self._recompute_reflow_layout()
            return
        z = self.zoom
        n = self.page_count
        self._disp_sizes = []
        for i in range(n):
            w0, h0 = self.page_sizes[i]
            dw, dh = display_size(w0, h0, self.rotation)
            self._disp_sizes.append((dw * z, dh * z))
        self._rows = self._rows_for_mode()
        self._page_rects = [None] * n
        self._page_tops = [0.0] * n
        self._row_tops = []
        self._row_heights = []

        if not n:
            self._content_h = self._content_w = self._max_w = 0.0
            self._update_scrollbars()
            return

        vw = max(1, self.viewport().width())
        vh = max(1, self.viewport().height())

        if self.view_mode == "continuous":
            # Identical vertical stack to the original single-column layout.
            top = float(PAD)
            max_w = 0.0
            for i in range(n):
                dw, dh = self._disp_sizes[i]
                self._page_tops[i] = top
                self._row_tops.append(top)
                self._row_heights.append(dh)
                top += dh + GAP
                max_w = max(max_w, dw)
            self._max_w = max_w
            self._content_h = top - GAP + PAD
            self._content_w = max_w + 2 * PAD
            xoff = self._x_offset()
            for i in range(n):
                dw, dh = self._disp_sizes[i]
                self._page_rects[i] = (
                    xoff + PAD + (max_w - dw) / 2.0,
                    self._page_tops[i], dw, dh)
            self._update_scrollbars()
            return

        cols = max((len(r) for r in self._rows), default=1)
        gap = GAP * z          # gutters scale with the pages
        row_widths = [sum(self._disp_sizes[i][0] for i in row)
                      + gap * (len(row) - 1) for row in self._rows]
        full_w = max(row_widths, default=0.0)
        max_page_w = max((dw for dw, _dh in self._disp_sizes), default=1.0)

        if self.view_mode == "single":
            row = next((r for r in self._rows if self.current_page in r),
                       self._rows[0])
            row_h = max(self._disp_sizes[i][1] for i in row)
            self._max_w = max_page_w
            self._content_w = max(float(vw), full_w + 2 * PAD)
            self._content_h = max(float(vh), row_h + 2 * PAD)
            row_y = (self._content_h - row_h) / 2.0
            index = row[0]
            dw, dh = self._disp_sizes[index]
            self._page_rects[index] = (
                (self._content_w - dw) / 2.0,
                row_y + (row_h - dh) / 2.0, dw, dh)
            self._page_tops[index] = row_y
            self._update_scrollbars()
            return

        # Facing/Overview: stack every row so the view scrolls through them,
        # like Okular with ViewContinuous (its default).
        row_heights = [max(self._disp_sizes[i][1] for i in row)
                       for row in self._rows]
        self._max_w = max_page_w
        self._content_w = max(float(vw), full_w + 2 * PAD)
        self._content_h = (2 * PAD + sum(row_heights)
                           + gap * (len(self._rows) - 1))
        top = float(PAD)
        for row, row_h, row_w in zip(self._rows, row_heights, row_widths):
            # Each row is centred as its own group.  Aligning to shared
            # column widths instead makes a narrow spread drift off-centre
            # in documents with mixed page sizes (an A3 sheet in one column
            # widens every row's cell), and the scroll range cannot always
            # bring it back to the middle.
            x = (self._content_w - row_w) / 2.0
            lone_left = (len(row) == 1 and cols > 1
                         and self.view_mode == "facing")
            for index in row:
                dw, dh = self._disp_sizes[index]
                if lone_left:
                    # A trailing odd page keeps the left-page slot so the
                    # gutter stays where the full spreads had it.
                    x = self._content_w / 2.0 - gap / 2.0 - dw
                y = top + (row_h - dh) / 2.0
                self._page_rects[index] = (x, y, dw, dh)
                self._page_tops[index] = top
                x += dw + gap
            self._row_tops.append(top)
            self._row_heights.append(row_h)
            top += row_h + gap
        self._update_scrollbars()

    def set_view_mode(self, mode: str) -> None:
        """Switch the page layout mode (Okular-inspired).

        The current page is kept.  Reflow has its own single-column layout,
        so there the mode is only remembered for when the pages return.
        """
        if mode not in VIEW_MODES or mode == self.view_mode:
            return
        self.view_mode = mode
        self._cancel_marquee()
        self._cancel_stroke()
        if not self.reflow_mode:
            self._relayout()
            if self.fit_mode != "none":
                # Re-fit the new layout (a spread or a three-column overview
                # has a different unit width than a single page).
                self.apply_fit()
            elif self._grid_scroll_mode():
                self._center_page(self.current_page)
            self._request_visible()
            self.viewport().update()
        self.view_mode_changed.emit(mode)

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

    def _grid_scroll_mode(self) -> bool:
        """True for stacked grid layouts that scroll vertically."""
        return self.view_mode in ("facing", "facing_centered", "overview")

    def _row_at_y(self, content_y: float) -> int:
        """Index of the grid row containing a content-space y (clamped)."""
        if not self._row_tops:
            return 0
        lo, hi = 0, len(self._row_tops) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._row_tops[mid] <= content_y:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _center_page(self, index: int) -> None:
        """Scroll so the page's row is centred in the viewport.

        Grid rows can be wider than the viewport (a spread or an overview
        row).  Centring the row rather than one page keeps the horizontal
        scroll range symmetrical and places the spread's gutter in the
        middle, as Okular does.
        """
        if not self.page_count or index >= len(self._disp_sizes):
            return
        row_idx = next((k for k, row in enumerate(self._rows) if index in row),
                       None)
        if row_idx is None or row_idx >= len(self._rows):
            return
        rects = [self._page_rects[i] for i in self._rows[row_idx]
                 if i < len(self._page_rects)
                 and self._page_rects[i] is not None]
        if not rects:
            return
        left = min(r[0] for r in rects)
        right = max(r[0] + r[2] for r in rects)
        top = min(r[1] for r in rects)
        bottom = max(r[1] + r[3] for r in rects)
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.setValue(int(max(0, min(
            top + (bottom - top) / 2.0 - self.viewport().height() / 2.0,
            vbar.maximum()))))
        hbar.setValue(int(max(0, min(
            left + (right - left) / 2.0 - self.viewport().width() / 2.0,
            hbar.maximum()))))
        self.current_page = index

    def _x_offset(self) -> float:
        """Content-space x origin of page area (centres pages in wide views)."""
        vw = self.viewport().width()
        return max(0.0, (vw - self._content_w) / 2.0) if vw > self._content_w else 0.0

    def _page_origin(self, index: int) -> Tuple[float, float]:
        """Top-left of a page in content coordinates."""
        # Continuous pages are centred dynamically, so their x stays correct
        # when the viewport is resized without a relayout (legacy behaviour).
        if self.reflow_mode or self.view_mode == "continuous":
            dw, _ = self._disp_sizes[index]
            x = self._x_offset() + PAD + (self._max_w - dw) / 2.0
            y = self._page_tops[index] if index < len(self._page_tops) else 0.0
            return (x, y)
        if index < len(self._page_rects):
            rect = self._page_rects[index]
            if rect is not None:
                return (rect[0], rect[1])
        dw = self._disp_sizes[index][0]
        return ((self._content_w - dw) / 2.0, 0.0)

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

    def _page_at(self, content_x: float, content_y: float) -> int:
        """Index of the page under a content-space point.

        Continuous (and Reflow) keep the original y-only lookup.  Grid modes
        also consider x, so clicking the right-hand page of a spread selects
        the right page.
        """
        if not self.page_count:
            return 0
        if self.reflow_mode or self.view_mode == "continuous":
            return self._page_at_y(content_y)
        best = None
        best_d = 1e18
        for i, rect in enumerate(self._page_rects):
            if rect is None:
                continue
            x, y, w, h = rect
            if x <= content_x <= x + w and y <= content_y <= y + h:
                return i
            dx = max(x - content_x, 0.0, content_x - (x + w))
            dy = max(y - content_y, 0.0, content_y - (y + h))
            d = dx * dx + dy * dy
            if d < best_d:
                best_d = d
                best = i
        return best if best is not None else self.current_page

    def visible_range(self) -> Tuple[int, int]:
        """First and last (inclusive) page index intersecting the viewport."""
        if not self.page_count:
            return (0, -1)
        if not self.reflow_mode and self.view_mode == "single":
            pages = [i for i, rect in enumerate(self._page_rects)
                     if rect is not None]
            if not pages:
                return (self.current_page, self.current_page)
            return (pages[0], pages[-1])
        if not self.reflow_mode and self._grid_scroll_mode():
            if not self._row_tops or not self._rows:
                return (self.current_page, self.current_page)
            y0 = max(0.0, float(self.verticalScrollBar().value()) - 200)
            y1 = float(self.verticalScrollBar().value())
            y1 += self.viewport().height()
            first_row = self._row_at_y(y0)
            last_row = first_row
            while (last_row + 1 < len(self._row_tops)
                   and self._row_tops[last_row + 1] <= y1):
                last_row += 1
            return (self._rows[first_row][0], self._rows[last_row][-1])
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
        if not self.page_count or self.reflow_mode:
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
        if (index >= len(self._page_rects)
                or self._page_rects[index] is None):
            # Preload of a page outside the current grid row: render it whole
            # instead of relying on tile geometry it does not have.
            self._request_page(index, priority=priority)
            return
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
        if result.version != self.session.version:
            return                      # rendered from content we have left behind
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

        if self.reflow_mode:
            self._paint_reflow(painter, event.rect())
            painter.end()
            return

        vsx = self.horizontalScrollBar().value()
        vsy = self.verticalScrollBar().value()

        first, last = self.visible_range()
        for i in range(first, last + 1):
            if i >= len(self._page_rects) or self._page_rects[i] is None:
                continue
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

        self._paint_marquee(painter)
        self._paint_stroke(painter)
        self._paint_shape(painter)
        painter.end()

    def _paint_stroke(self, painter: QPainter) -> None:
        """Draw the pen stroke in progress, in view coordinates.

        The captured points are page space, so they are mapped the same way the
        page is: a turned view has to carry the half-drawn line with the page
        instead of leaving it under the mouse (F10-3g).
        """
        if self._stroke is None:
            return
        points = self._stroke["points"]
        page = self._stroke["page"]
        if not points or page >= len(self.page_sizes):
            return
        w0, h0 = self.page_sizes[page]
        ox, oy = self._page_origin(page)
        ox -= self.horizontalScrollBar().value()
        oy -= self.verticalScrollBar().value()
        mapped = []
        for x, y in points:
            u, v = map_point(x, y, w0, h0, self.rotation)
            mapped.append(QPointF(ox + u * self.zoom, oy + v * self.zoom))
        pen = QPen(QColor(self.ink_color))
        pen.setWidthF(max(1.0, self.ink_width * self.zoom))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(mapped) == 1:
            # A tap is a dot, and a path of one move-to paints nothing.
            painter.drawPoint(mapped[0])
            return
        path = QPainterPath(mapped[0])
        for point in mapped[1:]:
            path.lineTo(point)
        painter.drawPath(path)

    def _paint_shape(self, painter: QPainter) -> None:
        """Draw the shape being dragged, in view coordinates.

        Like the pen, the geometry is captured in page space and mapped on
        every paint, so zoom and a turned view carry the preview with the page
        instead of leaving it under the mouse (F10-3g).
        """
        drag = self._shape_drag
        if drag is None or drag["page"] >= len(self.page_sizes):
            return
        page = drag["page"]
        w0, h0 = self.page_sizes[page]
        ox, oy = self._page_origin(page)
        ox -= self.horizontalScrollBar().value()
        oy -= self.verticalScrollBar().value()

        def spot(point):
            u, v = map_point(point[0], point[1], w0, h0, self.rotation)
            return QPointF(ox + u * self.zoom, oy + v * self.zoom)

        pen = QPen(QColor(self.ink_color))
        pen.setWidthF(max(1.0, self.ink_width * self.zoom))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        kind = drag["kind"]
        start = spot(drag["start"])
        current = spot(drag["current"])
        if kind == "rect":
            painter.drawRect(QRectF(start, current).normalized())
        elif kind == "ellipse":
            painter.drawEllipse(QRectF(start, current).normalized())
        elif kind in ("line", "arrow"):
            painter.drawLine(start, current)
            if kind == "arrow":
                self._paint_arrowhead(painter, start, current)
        else:
            outline = list(drag["points"])
            if outline[-1] != drag["current"]:
                outline.append(drag["current"])
            path = QPainterPath(spot(outline[0]))
            for point in outline[1:]:
                path.lineTo(spot(point))
            path.closeSubpath()
            painter.drawPath(path)

    def _paint_arrowhead(self, painter: QPainter, start: QPointF,
                         head: QPointF) -> None:
        """The V the arrow will get, so the preview shows what the file gets."""
        size = max(8.0, 6.0 + self.ink_width * self.zoom)
        angle = math.atan2(head.y() - start.y(), head.x() - start.x())
        for spread in (math.radians(150.0), math.radians(-150.0)):
            wing = angle + spread
            painter.drawLine(head, QPointF(head.x() + math.cos(wing) * size,
                                           head.y() + math.sin(wing) * size))

    def _paint_marquee(self, painter: QPainter) -> None:
        """Draw the area/table selection rectangle in view coordinates."""
        if self._marquee is None:
            return
        rect = QRect(self._marquee["start"], self._marquee["current"]).normalized()
        rect.translate(-self.horizontalScrollBar().value(),
                       -self.verticalScrollBar().value())
        painter.fillRect(rect, QColor(70, 130, 180, 45))
        pen = QPen(QColor(70, 130, 180))
        pen.setWidth(1)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

    def _paint_reflow(self, painter: QPainter, rect: QRect) -> None:
        """Paint the single-column reading view.

        Only cached layouts are drawn; nothing is extracted here.  Figures
        arrive as PNG bytes and are decoded lazily, once per visible page.
        """
        if self.dark_mode:
            bg, fg, muted = DARK_REFLOW_BG, DARK_REFLOW_FG, DARK_REFLOW_MUTED
        else:
            bg, fg, muted = LIGHT_REFLOW_BG, LIGHT_REFLOW_FG, LIGHT_REFLOW_MUTED
        painter.fillRect(rect, bg)

        if not self.page_count:
            painter.setPen(muted)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Empty document")
            return

        if self._reflow_error:
            painter.setPen(muted)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             "Reflow unavailable:\n" + self._reflow_error)
            return

        if self._reflow_done == 0 and self._reflow_total:
            painter.setPen(muted)
            painter.setFont(self._reflow_font("Sans Serif", 14, False, False))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                             f"Preparing reflow… 0/{self._reflow_total}")
            return

        if (not self._reflow_has_text and not self._reflow_has_media
                and self._reflow_done >= self._reflow_total
                and self._reflow_total):
            painter.setPen(fg)
            painter.setFont(self._reflow_font("Sans Serif", 15, False, False))
            painter.drawText(
                rect.adjusted(40, 0, -40, 0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "This document has no extractable text or images.\n\n"
                "Reflow needs a text layer (or page images) to display "
                "anything. Scanned documents require OCR first.",
            )
            return

        vw = self.viewport().width()
        vh = self.viewport().height()
        vsy = self.verticalScrollBar().value()
        text_w = self._reflow_width or reflow_text_width(vw)
        x0 = max(0.0, (vw - text_w) / 2.0)
        first = self._reflow_page_at(float(vsy))
        small = self._reflow_font("Sans Serif", 11, False, False)
        border = DARK_PAGE_BORDER if self.dark_mode else PAGE_BORDER

        for page in range(first, self.page_count):
            top = self._reflow_tops[page] - vsy
            layout = self._reflow_pages.get(page)

            if layout is not None:
                if not layout.has_text and not layout.media:
                    if -20 <= top <= vh:
                        painter.setPen(muted)
                        painter.setFont(self._reflow_font("Sans Serif", 12, False, True))
                        painter.drawText(int(x0), int(top + 20),
                                         f"Page {page + 1} — no extractable text")
                else:
                    for line in layout.lines:
                        ly = top + line.y
                        if ly + line.height < -2:
                            continue
                        if ly > vh + 2:
                            break
                        self._paint_reflow_line(painter, line, x0, top, fg)
                    for media in layout.media:
                        my = top + media.y
                        if my > vh + 2:
                            break
                        if my + media.height < -2:
                            continue
                        self._paint_reflow_media(painter, media, x0, top,
                                                 text_w, muted, border)

                sep_y = top + layout.height + REFLOW_PAGE_GAP / 2.0
                if page < self.page_count - 1 and -12 <= sep_y <= vh + 12:
                    painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
                    painter.drawLine(QPointF(x0, sep_y), QPointF(x0 + text_w, sep_y))
                    painter.setPen(muted)
                    painter.setFont(small)
                    painter.drawText(QPointF(x0 + text_w - 64, sep_y - 6),
                                     f"Page {page + 1}")

            if top > vh:
                break

    def _paint_reflow_line(self, painter: QPainter, line, x: float,
                           section_top: float, color: QColor) -> None:
        # line.baseline is relative to the page section, not to the line.
        painter.setPen(color)
        for text, family, size, bold, italic in line.runs:
            painter.setFont(self._reflow_font(family, size, bold, italic))
            painter.drawText(QPointF(x, section_top + line.baseline), text)
            x += self._reflow_metrics_for(family, size, bold, italic).horizontalAdvance(text)

    def _reflow_media_image(self, media) -> QImage:
        """Decode a media item's PNG on demand and keep it in the LRU cache."""
        key = id(media)
        image = self._reflow_media_cache.get(key)
        if image is not None:
            self._reflow_media_cache.move_to_end(key)
            return image
        if not media.data:
            return QImage()
        image = QImage.fromData(media.data, "PNG")
        if image.isNull():
            return image
        self._reflow_media_cache[key] = image
        self._reflow_media_bytes += int(image.sizeInBytes())
        while (self._reflow_media_bytes > REFLOW_MEDIA_CACHE_BYTES
               and len(self._reflow_media_cache) > 1):
            _, old = self._reflow_media_cache.popitem(last=False)
            self._reflow_media_bytes -= int(old.sizeInBytes())
        return image

    def _paint_reflow_media(self, painter: QPainter, media, x0: float,
                            section_top: float, text_w: float,
                            muted: QColor, border: QColor) -> None:
        width = max(1, int(round(media.width)))
        height = max(1, int(round(media.height)))
        x = x0 + max(0.0, (text_w - media.width) / 2.0)
        target = QRect(int(round(x)), int(round(section_top + media.y)),
                       width, height)
        image = self._reflow_media_image(media)
        if not image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawImage(target, image)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        else:
            painter.fillRect(target, muted.lighter(160))
            painter.setPen(muted)
            painter.setFont(self._reflow_font("Sans Serif", 11, False, True))
            painter.drawText(target, Qt.AlignmentFlag.AlignCenter,
                             "Table" if media.kind == "table" else "Image")
        painter.setPen(QPen(border, 1))
        painter.drawRect(target)

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
        # Highlights are not painted here any more: they live in the document and
        # the renderer draws annotations, so an overlay would paint the band twice
        # (F10-3).

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

        # the annotation the comments panel pointed at: a ring, not a fill, so it
        # still reads as the highlight or the note icon underneath it
        for r in self._annotation_outline_rects(page):
            painter.setPen(QPen(ANNOTATION_SELECTED, 2))
            painter.drawRect(r)

        # text selection
        for rect in self._selection_rects_for_page(page):
            x0, y0, x1, y1 = rect
            painter.fillRect(QRect(int(x + x0 * z), int(y + y0 * z),
                                   max(1, int((x1 - x0) * z)), max(1, int((y1 - y0) * z))), SEL_COLOR)

        if self._numbering_preview is not None:
            self._paint_numbering_preview(painter, page, x, y)

    def _paint_numbering_preview(self, painter: QPainter, page: int,
                                 x: float, y: float) -> None:
        settings = self._numbering_preview
        if settings is None:
            return
        first, last = page_range(settings, self.page_count)
        if not (first <= page <= last):
            return
        w0, h0 = self.page_sizes[page]
        box = self._preview_box(settings, page, first, last)
        z = self.zoom
        dw, dh = self._disp_sizes[page]
        erase = isinstance(settings, EraseSettings)

        # Mask that will cover the old content, plus a dashed outline so the
        # draggable area is visible even when masking is off.
        if erase or settings.mask:
            x0, y0, x1, y1 = box.mask_rect
        else:
            x0, y0, x1, y1 = (box.left - 2.0, box.top - 2.0,
                              box.right + 2.0, box.bottom + 2.0)
        x0, y0, x1, y1 = map_rect((x0, y0, x1, y1),
                                  w0, h0, self.rotation)
        mask = QRectF(x + x0 * z, y + y0 * z,
                      (x1 - x0) * z, (y1 - y0) * z)

        painter.save()
        painter.setClipRect(QRect(int(round(x)), int(round(y)),
                                  int(round(dw)), int(round(dh))))
        if erase:
            painter.fillRect(mask, QColor(settings.color))
        elif settings.mask:
            painter.fillRect(mask, QColor(settings.mask_color))
        pen = QPen(QColor(220, 30, 30, 170))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.drawRect(mask)
        if erase:
            painter.restore()
            return

        u, v = map_point(box.anchor_x, box.baseline, w0, h0, self.rotation)
        qfont = preview_font(
            settings, max(1, int(round(settings.font_size * z))))
        width = QFontMetricsF(qfont).horizontalAdvance(box.text)
        offset = 0.0
        if box.alignment == "center":
            offset = -width / 2.0
        elif box.alignment == "right":
            offset = -width

        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.translate(x + u * z, y + v * z)
        if self.rotation:
            painter.rotate(self.rotation)
        painter.setPen(QColor(220, 30, 30, 215))
        painter.setFont(qfont)
        painter.drawText(QPointF(offset, 0.0), box.text)
        painter.restore()

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
        idx = self._page_at(cx, cy)
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
        if not self.page_count or self.reflow_mode:
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

    # ------------------------------------------------- area/table selection
    def _page_clips(self, content_rect: QRect):
        """Split a content-space rectangle into per-page page-space clips."""
        if not self.page_count or not self.page_sizes:
            return []
        target = QRectF(content_rect)
        if self.reflow_mode or self.view_mode == "continuous":
            top = float(min(content_rect.top(), content_rect.bottom()))
            bottom = float(max(content_rect.top(), content_rect.bottom()))
            first = self._page_at_y(top)
            last = self._page_at_y(bottom)
        elif self._grid_scroll_mode() and self._rows:
            top = float(min(content_rect.top(), content_rect.bottom()))
            bottom = float(max(content_rect.top(), content_rect.bottom()))
            first = self._rows[self._row_at_y(top)][0]
            last = self._rows[self._row_at_y(bottom)][-1]
        else:
            first, last = 0, self.page_count - 1
        clips = []
        for page in range(first, last + 1):
            if (page >= len(self._page_rects)
                    or self._page_rects[page] is None):
                continue
            px, py = self._page_origin(page)
            dw, dh = self._disp_sizes[page]
            inter = QRectF(px, py, dw, dh).intersected(target)
            if inter.isEmpty():
                continue
            u0 = (inter.left() - px) / self.zoom
            v0 = (inter.top() - py) / self.zoom
            u1 = (inter.right() - px) / self.zoom
            v1 = (inter.bottom() - py) / self.zoom
            w0, h0 = self.page_sizes[page]
            clips.append(
                (page, unmap_rect((u0, v0, u1, v1), w0, h0, self.rotation)))
        return clips

    def _content_rect(self, view_rect: QRect) -> QRect:
        return view_rect.translated(self.horizontalScrollBar().value(),
                                    self.verticalScrollBar().value())

    def area_text(self, view_rect: QRect) -> str:
        """Text of the words inside a viewport-space rectangle."""
        parts = []
        for page, clip in self._page_clips(self._content_rect(view_rect)):
            lines = group_lines(words_in_rect(self._page_words(page), clip))
            if lines:
                parts.append("\n".join(lines))
        return "\n".join(parts)

    def area_image(self, view_rect: QRect) -> QPixmap:
        """Screen-resolution image of a viewport-space rectangle."""
        rect = view_rect.intersected(self.viewport().rect())
        if rect.isEmpty():
            return QPixmap()
        return self.viewport().grab(rect)

    def _build_area_menu(self, view_rect: QRect) -> QMenu:
        text = self.area_text(view_rect)
        menu = QMenu(self)
        copy_text = menu.addAction(f"Copy Text ({len(text)} characters)")
        copy_text.setEnabled(bool(text))
        if text:
            copy_text.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(text))
        copy_image = menu.addAction(
            f"Copy Image ({view_rect.width()} × {view_rect.height()} pixels)")
        copy_image.triggered.connect(
            lambda: QGuiApplication.clipboard().setPixmap(
                self.area_image(view_rect)))
        return menu

    def _finish_area_selection(self, view_rect: QRect) -> None:
        menu = self._build_area_menu(view_rect)
        menu.exec(self.viewport().mapToGlobal(view_rect.bottomRight()))
        menu.deleteLater()

    def table_text(self, view_rect: QRect) -> str:
        """Tab-separated text of the table inside a viewport rectangle."""
        return self._table_selection(view_rect)[0]

    def _table_selection(self, view_rect: QRect):
        parts = []
        rows = 0
        cols = 0
        for page, clip in self._page_clips(self._content_rect(view_rect)):
            text = format_table(self._page_words(page), clip)
            if not text:
                continue
            part_rows, part_cols = table_size(text)
            rows += part_rows
            cols = max(cols, part_cols)
            parts.append(text)
        return "\n\n".join(parts), rows, cols

    def _finish_table_selection(self, view_rect: QRect) -> None:
        text, rows, cols = self._table_selection(view_rect)
        if not text:
            self.transient_message.emit("No table text in selection")
            return
        QGuiApplication.clipboard().setText(text)
        self.transient_message.emit(f"Table copied ({rows} × {cols})")

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

    def first_hit_index(self, page: int) -> int:
        """Return the flat hit index of *page*'s first match, or -1."""
        for index, (hit_page, _rect) in enumerate(self._hit_flat):
            if hit_page == page:
                return index
        return -1

    def hit_page(self, index: int) -> int:
        """Return the page of flat hit *index*, or -1 when out of range."""
        if 0 <= index < len(self._hit_flat):
            return self._hit_flat[index][0]
        return -1

    def goto_hit(self, step: int = 1) -> bool:
        flat = self._hit_flat
        if not flat:
            return False
        return self.goto_hit_index((self._hit_cur + step) % len(flat))

    def goto_hit_index(self, index: int) -> bool:
        """Jump to an absolute hit index and make it the current match."""
        flat = self._hit_flat
        if not 0 <= index < len(flat):
            return False
        self._hit_cur = index
        page, rect = flat[index]
        self.scroll_to_rect(page, rect)
        self.hit_changed.emit(index + 1, len(flat))
        self.viewport().update()
        return True

    def scroll_to_rect(self, page: int, rect: Tuple[float, float, float, float]) -> None:
        if self.reflow_mode:
            # Reflow has no page-space geometry; jump to the page section.
            self.goto_page(page, center=True)
            return
        if self.view_mode == "single":
            # Show the page alone, centered in the viewport.
            self.current_page = page
            self._relayout()
            self.page_changed.emit(page)
            self._request_visible()
            self.viewport().update()
            return
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
        if self.reflow_mode:
            return          # reflow is text-sized, not zoomed
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
            if self.view_mode == "single":
                new_vscroll = 0
            elif self._grid_scroll_mode():
                self._center_page(self.current_page)
                new_vscroll = self.verticalScrollBar().value()
            elif self.page_count > 0 and self.current_page < self.page_count:
                page_top = self._page_origin(self.current_page)[1]
                new_vscroll = int(page_top * zoom - self.viewport().height() / 2.0)
                new_vscroll = max(0, min(new_vscroll,
                                         self.verticalScrollBar().maximum()))
            else:
                new_vscroll = int(self.verticalScrollBar().value() * (zoom / old_zoom))
            # Block _update_current_page during zoom change
            vbar = self.verticalScrollBar()
            vbar.blockSignals(True)
            vbar.setValue(new_vscroll)
            vbar.blockSignals(False)
            if self.view_mode == "single":
                self.horizontalScrollBar().setValue(0)

        self.zoom_changed.emit(self.zoom)
        self._request_visible()
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(self.zoom + self.zoom_step)

    def zoom_out(self) -> None:
        self.set_zoom(self.zoom - self.zoom_step)

    def set_zoom_step(self, step: float) -> None:
        """Set the additive zoom increment (0.10 == +10 percentage points)."""
        self.zoom_step = max(0.001, min(1.0, float(step)))

    def set_ctrl_wheel_step(self, enabled: bool) -> None:
        """Use the discrete zoom step for Ctrl+wheel instead of fluid zoom."""
        self.ctrl_wheel_step = bool(enabled)

    def set_fit_mode(self, mode: str) -> None:
        self.fit_mode = mode
        self.apply_fit()

    def _fit_row_unit(self, row: List[int]) -> Tuple[float, float]:
        """Width/height of a row at zoom 1 (pages touching, plus gaps)."""
        widths = [self._unit_size(i)[0] for i in row]
        heights = [self._unit_size(i)[1] for i in row]
        return (sum(widths) + GAP * (len(row) - 1), max(heights))

    def _fit_width_unit(self) -> float:
        """Widest row at zoom 1, used by Fit Width."""
        if self.view_mode in ("continuous", "single"):
            return max(self._unit_size(i)[0] for i in range(self.page_count))
        rows = self._rows_for_mode()
        return max(sum(self._unit_size(i)[0] for i in row)
                   + GAP * (len(row) - 1) for row in rows)

    def apply_fit(self) -> None:
        if self.reflow_mode or self.fit_mode == "none" or not self.page_count:
            return
        vw = max(1, self.viewport().width() - 2 * PAD)
        vh = max(1, self.viewport().height() - 2 * PAD)
        if vw < 50 or vh < 50:
            return          # not laid out yet; resizeEvent will retry
        if self.fit_mode == "width":
            zoom = vw / max(1.0, self._fit_width_unit())
        else:  # fit page, using the current row
            rows = self._rows_for_mode()
            row = next((r for r in rows if self.current_page in r), rows[0])
            unit_w, unit_h = self._fit_row_unit(row)
            zoom = min(vw / max(1.0, unit_w), vh / max(1.0, unit_h))
        # Temporarily block _update_current_page during zoom change
        self.blockSignals(True)
        vbar = self.verticalScrollBar()
        hbar = self.horizontalScrollBar()
        vbar.blockSignals(True)
        hbar.blockSignals(True)
        try:
            self.zoom = zoom
            self._tiles.clear()
            self._tile_grids.clear()
            self._relayout()
            if self.view_mode == "single":
                vbar.setValue(0)
                hbar.setValue(0)
            elif self.view_mode == "continuous":
                page_top = self._page_origin(self.current_page)[1]
                new_vscroll = int(page_top - self.viewport().height() / 2.0)
                vbar.setValue(max(0, min(new_vscroll, vbar.maximum())))
            else:
                self._center_page(self.current_page)
            self._request_visible()
            self.viewport().update()
        finally:
            hbar.blockSignals(False)
            vbar.blockSignals(False)
            self.blockSignals(False)
        self.zoom_changed.emit(self.zoom)

    # ----------------------------------------------------------- rotation
    def set_rotation(self, rotation: int) -> None:
        """Set the absolute view rotation (0/90/180/270, clockwise)."""
        if self.reflow_mode:
            return          # reflow drops the original page geometry
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
            "reflow": self.reflow_mode,
            "view_mode": self.view_mode,
        }

    def restore_state(self, state: dict) -> None:
        """Restore view state from a dict returned by get_state()."""
        if not state:
            return
        mode = state.get("view_mode", "continuous")
        self.view_mode = mode if mode in VIEW_MODES else "continuous"
        if self.reflow_mode:
            page = state.get("page", 0)
            page = max(0, min(page, self.page_count - 1)) if self.page_count else 0
            self.goto_page(page, center=False)
            self.view_mode_changed.emit(self.view_mode)
            return
        if state.get("reflow"):
            # Saved while reflow was active: only the page is meaningful in
            # the normal layout (the scroll offset was reflow coordinates).
            page = state.get("page", 0)
            page = max(0, min(page, self.page_count - 1)) if self.page_count else 0
            self.goto_page(page, center=False)
            self.view_mode_changed.emit(self.view_mode)
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
            elif self.view_mode != "continuous":
                # The saved zoom belonged to a single-page width; re-fit the
                # restored spread/overview layout.
                self.apply_fit()
            page = state.get("page", 0)
            page = max(0, min(page, self.page_count - 1)) if self.page_count else 0
            if self.view_mode == "single":
                # Single Page is one screen per page; it has no scroll state.
                vbar.setValue(0)
                hbar.setValue(0)
                self.current_page = page
                self._relayout()
            else:
                # Continuous and stacked grids restore their scroll offset.
                # Set the page last: scrolling would otherwise overwrite it.
                self._relayout()
                vscroll = state.get("vscroll", 0)
                hscroll = state.get("hscroll", 0)
                if vscroll:
                    vbar.setValue(vscroll)
                if hscroll:
                    hbar.setValue(hscroll)
                self.current_page = page
            # Emit page_changed after all state is restored
            self.page_changed.emit(page)
            self._request_visible()
        finally:
            vbar.blockSignals(False)
            hbar.blockSignals(False)
            self.blockSignals(False)
            self.view_mode_changed.emit(self.view_mode)

    # ---------------------------------------------------------- navigate
    def goto_page(self, index: int, center: bool = True) -> None:
        if not self.page_count:
            return
        index = max(0, min(int(index), self.page_count - 1))
        if self.reflow_mode:
            # If the worker has not laid this page out yet, jump once it does.
            if index not in self._reflow_pages and self._reflow_worker is not None:
                self._reflow_pending_page = index
                self._reflow_pending_center = center
                self._reflow_pending_ratio = None
            else:
                # Explicit navigation wins over a queued re-anchor.
                self._reflow_pending_page = None
                self._reflow_pending_ratio = None
            self._scroll_to_reflow_page(index, center=center)
            self.current_page = index
            self.page_changed.emit(index)
            self.viewport().update()
            return
        if self.view_mode == "single":
            self.current_page = index
            self._relayout()
            self.page_changed.emit(index)
            self._request_visible()
            self.viewport().update()
            return
        if self._grid_scroll_mode():
            self._center_page(index)
            self.page_changed.emit(index)
            self._request_visible()
            self.viewport().update()
            return
        py = self._page_origin(index)[1]
        value = py - (self.viewport().height() / 2.0 if center else 20)
        self.verticalScrollBar().setValue(int(max(0, value)))
        self.current_page = index
        self.page_changed.emit(index)
        self._request_visible()

    def _update_current_page(self) -> None:
        if not self.page_count:
            return
        if self.view_mode == "single" and not self.reflow_mode:
            return          # Single Page sets the page explicitly
        y = self.verticalScrollBar().value() + 2
        if not self.reflow_mode and self._grid_scroll_mode() and self._rows:
            # Row tops are shared by all pages of a spread, so map the
            # scroll position to the first page of the row.
            idx = self._rows[self._row_at_y(y)][0]
        else:
            idx = self._page_at_y(y)
        if idx != self.current_page:
            self.current_page = idx
            self.page_changed.emit(idx)

    def page_step(self, direction: int) -> None:
        if not self.reflow_mode and self.view_mode == "single":
            self.goto_page(self.current_page + direction)
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + direction
                     * max(1, self.viewport().height() - 60))

    # ------------------------------------------------------------- events
    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        self._update_current_page()
        self._request_visible()
        self.viewport().update()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.reflow_mode:
            self._recompute_reflow_layout()
            # Re-wrap only after the user stops dragging (extraction is cheap
            # but laying out hundreds of pages should not run per pixel).
            if self._reflow_available_width() != self._reflow_width:
                self._reflow_timer.start()
            self.viewport().update()
        elif self.fit_mode != "none":
            self.apply_fit()
        elif self.view_mode != "continuous":
            # Grid rows are centred in the viewport, so their geometry
            # depends on the viewport size.
            self._relayout()
            self._request_visible()
            self.viewport().update()
        else:
            self._update_scrollbars()
            self._request_visible()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.fit_mode != "none":
            self.apply_fit()
        elif self.view_mode != "continuous" and not self.reflow_mode:
            self._relayout()
        self._request_visible()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and not self.reflow_mode):
            steps = event.angleDelta().y() / 120.0
            if steps:
                if self.ctrl_wheel_step:
                    zoom = self.zoom + self.zoom_step * steps
                else:
                    zoom = self.zoom * (1.15 ** steps)
                self.set_zoom(zoom, anchor=event.position().toPoint())
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
            # A press on the page means the user has moved on from whatever the
            # comments panel had pointed at.
            self.clear_annotation_selection()
        if (event.button() == Qt.MouseButton.LeftButton
                and self._numbering_preview is not None):
            hit = self._numbering_hit_at(event.position().toPoint())
            if hit is not None:
                page, box = hit
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                self.clear_selection()
                self._numbering_drag = {
                    "page": page,
                    "start": event.position(),
                    "settings": self._numbering_preview,
                    "box": box,
                }
                self.viewport().setCursor(Qt.CursorShape.SizeAllCursor)
                event.accept()
                return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode == "browse"):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clear_selection()
            self._panning = True
            self._pan_last = event.position().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if self.reflow_mode:
            # Selection uses page-space geometry that reflow does not have.
            if event.button() == Qt.MouseButton.LeftButton:
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                event.accept()
                return
            super().mousePressEvent(event)
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode == "draw"):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clear_selection()
            hit = self._pdf_point_at(event.position().toPoint())
            if hit is not None:
                page, x, y = hit
                self._stroke = {"page": page, "points": [(x, y)]}
                self.viewport().update()
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode == "shape"):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clear_selection()
            hit = self._pdf_point_at(event.position().toPoint())
            if hit is not None:
                page, x, y = hit
                self._shape_drag = {
                    "page": page, "kind": self.shape_kind,
                    "start": (x, y), "current": (x, y), "points": [(x, y)],
                }
                self.viewport().update()
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode == "stamp"):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clear_selection()
            hit = self._pdf_point_at(event.position().toPoint())
            if hit is not None:
                page, x, y = hit
                if self.custom_stamp is not None:
                    self.annotation_manager.add_custom_stamp(
                        page, (x, y),
                        caption=self.custom_stamp.get("caption", ""),
                        image=self.custom_stamp.get("image", ""))
                else:
                    self.annotation_manager.add_stamp(
                        page, self.stamp_name, (x, y))
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self.tool_mode in ("area", "table")):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.clear_selection()
            point = event.position().toPoint() + QPoint(
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value())
            self._marquee = {"start": point, "current": point}
            self.viewport().update()
            event.accept()
            return
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
        if self._numbering_drag is not None:
            drag = self._numbering_drag
            pos = event.position()
            du = (pos.x() - drag["start"].x()) / self.zoom
            dv = (pos.y() - drag["start"].y()) / self.zoom
            dx, dy = unmap_delta(du, dv, self.rotation)
            w0, h0 = self.page_sizes[drag["page"]]
            settings = box_to_settings(
                drag["settings"], w0, h0, drag["box"], dx, dy)
            self._numbering_preview = settings
            self.viewport().update()
            self.numbering_changed.emit(settings)
            event.accept()
            return
        if self._panning and (event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.position().toPoint()
            delta = pos - self._pan_last
            if not delta.isNull():
                self.horizontalScrollBar().setValue(
                    self.horizontalScrollBar().value() - delta.x())
                self.verticalScrollBar().setValue(
                    self.verticalScrollBar().value() - delta.y())
                self._pan_last = pos
                self.viewport().update()
            event.accept()
            return
        if (self._stroke is not None
                and (event.buttons() & Qt.MouseButton.LeftButton)):
            self._extend_stroke(event.position().toPoint())
            event.accept()
            return
        if (self._shape_drag is not None
                and (event.buttons() & Qt.MouseButton.LeftButton)):
            self._extend_shape(event.position().toPoint())
            event.accept()
            return
        if (self._marquee is not None
                and (event.buttons() & Qt.MouseButton.LeftButton)):
            self._marquee["current"] = event.position().toPoint() + QPoint(
                self.horizontalScrollBar().value(),
                self.verticalScrollBar().value())
            self.viewport().update()
            event.accept()
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._update_hover_cursor(event.position().toPoint())
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
        if (event.button() == Qt.MouseButton.LeftButton
                and self._numbering_drag is not None):
            self._numbering_drag = None
            self._update_hover_cursor(event.position().toPoint())
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self._panning):
            self._panning = False
            self._apply_tool_cursor()
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self._stroke is not None):
            stroke = self._stroke
            self._stroke = None
            self.viewport().update()
            self._commit_stroke(stroke, event.position().toPoint())
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self._shape_drag is not None):
            drag = self._shape_drag
            self._extend_shape(event.position().toPoint())
            self._shape_drag = None
            self.viewport().update()
            self._commit_shape(drag)
            event.accept()
            return
        if (event.button() == Qt.MouseButton.LeftButton
                and self._marquee is not None):
            start = self._marquee["start"]
            current = self._marquee["current"]
            self._marquee = None
            self.viewport().update()
            if (abs(current.x() - start.x()) >= 3
                    or abs(current.y() - start.y()) >= 3):
                content_rect = QRect(start, current).normalized()
                view_rect = content_rect.translated(
                    -self.horizontalScrollBar().value(),
                    -self.verticalScrollBar().value())
                if self.tool_mode == "area":
                    self._finish_area_selection(view_rect)
                elif self.tool_mode == "table":
                    self._finish_table_selection(view_rect)
            event.accept()
            return
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

        if key == Qt.Key.Key_Escape and self._numbering_drag is not None:
            original = self._numbering_drag["settings"]
            self._numbering_drag = None
            self._numbering_preview = original
            self.viewport().update()
            self.numbering_changed.emit(original)
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._stroke is not None:
            self._cancel_stroke()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._shape_drag is not None:
            self._cancel_shape()
            event.accept()
            return
        if key == Qt.Key.Key_Escape and self._marquee is not None:
            self._cancel_marquee()
            event.accept()
            return
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
            if self.reflow_mode:
                self.reflow_text_bigger()
            else:
                self.zoom_in()
            event.accept()
        elif ctrl and key == Qt.Key.Key_Minus:
            if self.reflow_mode:
                self.reflow_text_smaller()
            else:
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
