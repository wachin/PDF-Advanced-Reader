"""Componentes de visualización con renderizado asíncrono."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import fitz
import concurrent.futures

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QPainter, QBrush, QColor, QPen, QPixmap, QImage
from PyQt6.QtWidgets import QLabel, QWidget, QVBoxLayout

from .cache import PageCache
from .worker import RenderWorker
from .search import HIGHLIGHT_COLOR, HIGHLIGHT_PEN


class PageState:
    """State for a single page widget."""
    EMPTY = 0
    QUEUED = 1
    RENDERING = 2
    READY = 3
    ERROR = 4


class PageWidget:
    """Wrapper for page label and state."""
    def __init__(self, page_index: int, width: int, height: int):
        self.page_index = page_index
        self.width = width
        self.height = height
        self.state = PageState.EMPTY
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.current_pixmap: Optional[QPixmap] = None

    def set_ready(self, pixmap: QPixmap):
        """Set the widget to ready state with the given pixmap."""
        self.current_pixmap = pixmap
        self.label.setPixmap(pixmap)
        self.state = PageState.READY

    def set_queued(self):
        """Mark widget as queued for rendering."""
        self.state = PageState.QUEUED

    def set_error(self):
        """Mark widget as having a rendering error."""
        self.state = PageState.ERROR


class PDFViewer(QWidget):
    """PDF viewer with async rendering and viewport-based loading."""

    # Signal to notify parent when scroll position changes
    scroll_changed = pyqtSignal(int)  # emits new vertical scroll position

    def __init__(self, doc_path: str, zoom: float = 1.0):
        super().__init__()
        self.doc_path = doc_path
        self.doc = fitz.open(doc_path)
        self.zoom = zoom
        self.cache = PageCache(max_size=30)
        self.highlights: Dict[int, List[fitz.Rect]] = {}
        self.active_workers: Dict[int, RenderWorker] = {}
        self.page_widgets: Dict[int, PageWidget] = {}
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.layout.setSpacing(10)

        # Use ThreadPoolExecutor for async rendering
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=8)

        self.render_timer = QTimer()
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._check_workers)

        self.scroll_timer = QTimer()
        self.scroll_timer.setSingleShot(True)
        self.scroll_timer.timeout.connect(self._on_scroll)

        # Track page heights for position calculations
        self.page_heights: List[int] = []
        self.page_tops: List[int] = []  # Cumulative top position of each page

        self._init_all_pages()
        self._preload_visible()

    def _init_all_pages(self):
        """Initialize all pages with geometry placeholders."""
        self.page_heights.clear()
        self.page_tops.clear()
        cumulative_height = 0

        for i in range(self.doc.page_count):
            page = self.doc.load_page(i)
            w = int(page.rect.width * self.zoom)
            h = int(page.rect.height * self.zoom)
            page_widget = PageWidget(page_index=i, width=w, height=h)
            page_widget.label.setFixedSize(w, h)
            page_widget.label.setText(f"Page {i + 1}")
            self.layout.addWidget(page_widget.label)
            self.page_widgets[i] = page_widget

            # Store page geometry for position calculations
            self.page_heights.append(h)
            self.page_tops.append(cumulative_height)
            cumulative_height += h + 10  # 10px spacing between pages

    def _preload_visible(self):
        """Render first 10 pages immediately for quick display."""
        pages = list(range(min(10, self.doc.page_count)))
        self._render_pages_batch(pages, priority=0)

    def set_zoom(self, zoom: float):
        """Change zoom and re-render current viewport."""
        self.zoom = zoom
        self.cache.invalidate_zoom(zoom)

        visible_range = self._get_visible_page_range()
        for i in range(visible_range[0], min(visible_range[1], self.doc.page_count)):
            if i in self.page_widgets:
                widget = self.page_widgets[i]
                widget.state = PageState.EMPTY
                widget.label.setText(f"Page {i + 1}")
                widget.current_pixmap = None

        self._preload_visible()

    def set_highlights(self, per_page_rects: Dict[int, List[fitz.Rect]]):
        """Set search highlights for pages."""
        self.highlights = per_page_rects or {}
        visible_range = self._get_visible_page_range()
        for i in range(visible_range[0], min(visible_range[1], self.doc.page_count)):
            if i in self.page_widgets:
                widget = self.page_widgets[i]
                if widget.state == PageState.READY and widget.current_pixmap:
                    self._apply_highlights(widget.current_pixmap, i)
                    widget.label.setPixmap(widget.current_pixmap)

    def set_scroll_position(self, scroll_pos: int, viewport_height: int):
        """
        Set scroll position from parent (QScrollArea). This is called by the
        parent widget to provide scroll context for visible range calculation.
        
        This fixes Bug 1.1 where _get_visible_page_range() couldn't detect
        scroll position when PDFViewer was inside QScrollArea.
        """
        self._scroll_pos = scroll_pos
        self._viewport_height = viewport_height
        self.scroll_changed.emit(scroll_pos)

    def _get_visible_page_range(self) -> Tuple[int, int]:
        """Determine which pages are visible in viewport."""
        if not self.page_widgets:
            return (0, 0)

        # Use stored scroll position from parent widget (set via set_scroll_position)
        scroll_pos = getattr(self, '_scroll_pos', 0)
        viewport_h = getattr(self, '_viewport_height', self.height())

        first_visible = 0
        last_visible = 0

        for i in range(self.doc.page_count):
            # Use pre-calculated page positions
            page_top = self.page_tops[i]
            page_height = self.page_heights[i]
            page_bottom = page_top + page_height

            # Check if page is within viewport
            if page_top < scroll_pos + viewport_h and page_bottom > scroll_pos:
                first_visible = i
                last_visible = i
                break

        # If no page found in viewport, find the last page that overlaps
        if last_visible == 0:
            for i in range(self.doc.page_count - 1, -1, -1):
                page_top = self.page_tops[i]
                page_height = self.page_heights[i]
                page_bottom = page_top + page_height
                if page_top < scroll_pos + viewport_h:
                    first_visible = max(0, i - 5)  # Include 5 pages before
                    last_visible = i
                    break

        return (first_visible, last_visible + 1)

    def _render_page(self, page_index: int, priority: int = 2):
        """Request page rendering with priority."""
        if page_index in self.page_widgets:
            widget = self.page_widgets[page_index]
            if widget.state == PageState.READY:
                return
            widget.state = PageState.QUEUED

        worker = RenderWorker(self.doc_path, page_index, self.zoom)
        self.active_workers[page_index] = worker
        self.executor.submit(worker.run)

        # Start checking for workers if not already running
        if not self.render_timer.isActive():
            self.render_timer.start(50)

    def _check_workers(self):
        """Check completed workers and update UI."""
        done_workers = [
            idx for idx, w in self.active_workers.items() if w.is_finished()
        ]

        for idx in done_workers:
            worker = self.active_workers.pop(idx)
            self._apply_render_result(idx, worker)

        if self.active_workers:
            self.render_timer.start(50)

    def _render_pages_batch(self, pages: List[int], priority: int = 2):
        """Render multiple pages with batching to avoid thread pool issues."""
        for i, page_index in enumerate(pages):
            if page_index in self.page_widgets:
                widget = self.page_widgets[page_index]
                if widget.state != PageState.READY:
                    widget.state = PageState.QUEUED
            worker = RenderWorker(self.doc_path, page_index, self.zoom)
            self.active_workers[page_index] = worker
            self.executor.submit(worker.run)

        if not self.render_timer.isActive():
            self.render_timer.start(50)

    def _apply_render_result(self, page_index: int, worker: RenderWorker):
        """Apply render result to page widget."""
        if page_index not in self.page_widgets:
            return

        widget = self.page_widgets[page_index]
        if widget.state == PageState.READY:
            return

        if not worker.success:
            widget.set_error()
            return

        # Allow for small dimension differences due to rounding
        width_diff = abs(widget.width - worker.width)
        height_diff = abs(widget.height - worker.height)
        if worker.raw_data and width_diff <= 2 and height_diff <= 2:
            try:
                pm = QPixmap()
                pm.loadFromData(worker.raw_data, "PNG")
                # Update widget dimensions to match rendered image
                widget.width = worker.width
                widget.height = worker.height
                widget.label.setFixedSize(worker.width, worker.height)

                # Apply highlights if present
                if page_index in self.highlights and self.highlights[page_index]:
                    self._apply_highlights(pm, page_index)

                widget.set_ready(pm)
            except Exception:
                widget.set_error()

    def _apply_highlights(self, pixmap: QPixmap, page_index: int):
        """Apply search highlights to a pixmap."""
        if page_index not in self.highlights or not self.highlights[page_index]:
            return
        rects = self.highlights[page_index]
        if not rects:
            return
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(HIGHLIGHT_COLOR))
        painter.setPen(QPen(Qt.PenStyle.NoPen))
        for rect in rects:
            painter.drawRect(int(rect.x0), int(rect.y0), int(rect.width), int(rect.height))
        painter.end()

    def _on_scroll(self):
        """Trigger viewport-based loading after scrolling."""
        visible_range = self._get_visible_page_range()
        first, last = visible_range

        before_range = max(0, first - 3)
        after_range = min(self.doc.page_count, last + 8)

        for i in range(before_range, after_range):
            if i in self.page_widgets and self.page_widgets[i].state != PageState.READY:
                if i >= last and i < after_range:
                    self._render_page(i, priority=1)
                elif i >= before_range and i < first:
                    self._render_page(i, priority=2)
                else:
                    self._render_page(i, priority=3)

    def goto_page(self, page_index: int):
        """Navigate to a specific page and trigger priority rendering."""
        if 0 <= page_index < self.doc.page_count:
            self._render_page(page_index, priority=0)

    def get_page_widget(self, page_index: int) -> Optional[QLabel]:
        if 0 <= page_index < len(self.page_widgets):
            return self.page_widgets[page_index].label
        return None
