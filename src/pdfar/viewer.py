"""Componentes de visualización con carga diferida."""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPainter, QBrush, QColor, QPen, QPixmap, QImage
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget
import fitz

from .cache import PageCache
from .search import HIGHLIGHT_COLOR, HIGHLIGHT_PEN


class PDFViewer(QWidget):
    """Visor de PDF con carga diferida y caché."""
    
    def __init__(self, doc: fitz.Document, zoom: float = 1.0):
        super().__init__()
        self.doc = doc
        self.zoom = zoom
        self.cache = PageCache(max_size=10)
        self.highlights: Dict[int, List[fitz.Rect]] = {}
        self.pending_render_pages: set = set()
        self.render_timer = QTimer()
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self._flush_pending_renders)
        
        self.layout = QVBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        
        self.labels: List[QLabel] = []
        self._build_placeholders()
    
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        self.cache.invalidate_zoom(zoom - 0.01, zoom)
        self._clear()
        self._build_placeholders()
        self._render_visible_pages()
    
    def set_highlights(self, per_page_rects: Dict[int, List[fitz.Rect]]):
        self.highlights = per_page_rects or {}
        self._render_visible_pages()
    
    def get_page_widget(self, page_index: int) -> Optional[QLabel]:
        if 0 <= page_index < len(self.labels):
            return self.labels[page_index]
        return None
    
    def _build_placeholders(self):
        self.labels.clear()
        for i in range(self.doc.page_count):
            lbl = QLabel(f"Page {i + 1}")
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.layout.addWidget(lbl)
            self.labels.append(lbl)
    
    def _clear(self):
        while self.layout.count():
            item = self.layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.labels.clear()
    
    def _render_visible_pages(self):
        for i, lbl in enumerate(self.labels):
            if not hasattr(lbl, 'pos') or not lbl.pos():
                continue
            # Renderizar primeras páginas para inicio rápido
            if i < 3:
                self._render_page(i)
    
    def _render_page(self, page_index: int):
        if not self.doc or not (0 <= page_index < self.doc.page_count):
            return
        
        cached = self.cache.get(page_index, self.zoom)
        if cached:
            self._apply_pixmap(page_index, cached)
            return
        
        if page_index not in self.pending_render_pages:
            self.pending_render_pages.add(page_index)
            QTimer.singleShot(50, lambda: self._schedule_render(page_index))
    
    def _schedule_render(self, page_index: int):
        self.render_timer.start(100)
    
    def _flush_pending_renders(self):
        pages = list(self.pending_render_pages)
        self.pending_render_pages.clear()
        for p in pages:
            self._do_render(p)
    
    def _do_render(self, page_index: int):
        if not self.doc:
            return
        
        page = self.doc.load_page(page_index)
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=mat, alpha=True)
        pm = QPixmap.fromImage(QImage.fromData(pix.tobytes("png")))
        
        self.cache.put(page_index, self.zoom, pm)
        
        rects = self.highlights.get(page_index, [])
        if rects:
            pm = self._paint_highlights(pm, rects)
        
        self._apply_pixmap(page_index, pm)
    
    def _apply_pixmap(self, page_index: int, pixmap: QPixmap):
        if 0 <= page_index < len(self.labels):
            self.labels[page_index].setPixmap(pixmap)
    
    def _paint_highlights(self, pixmap: QPixmap, rects: List[fitz.Rect]) -> QPixmap:
        pm = pixmap.copy()
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(HIGHLIGHT_COLOR))
        painter.setPen(HIGHLIGHT_PEN)
        for rect in rects:
            painter.drawRect(int(rect.x0), int(rect.y0), int(rect.width), int(rect.height))
        painter.end()
        return pm
