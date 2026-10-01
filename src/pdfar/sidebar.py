"""Left sidebar: page thumbnails and the document outline."""

from __future__ import annotations

from typing import Dict

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from .render import RenderPool, RenderResult

THUMB_W = 120


class Sidebar(QWidget):
    """Thumbnails + table of contents."""

    goto_page_requested = pyqtSignal(int)

    def __init__(self, doc: fitz.Document, pool: RenderPool, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.pool = pool
        self._thumb_zoom: Dict[int, float] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # ---- thumbnails -------------------------------------------------
        self.thumbs = QListWidget()
        self.thumbs.setViewMode(QListWidget.ViewMode.IconMode)
        self.thumbs.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.thumbs.setMovement(QListWidget.Movement.Static)
        self.thumbs.setIconSize(QSize(THUMB_W, THUMB_W * 2))
        self.thumbs.setSpacing(8)
        self.thumbs.setWordWrap(True)
        self.thumbs.itemDoubleClicked.connect(self._on_thumb)
        self.tabs.addTab(self.thumbs, "Thumbnails")

        # ---- outline ----------------------------------------------------
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.itemActivated.connect(self._on_outline)
        self.tabs.addTab(self.outline, "Index")

        self._build_thumbs()
        self._build_outline()
        self.pool.result_ready.connect(self._on_render)

    # ------------------------------------------------------------------
    def _build_thumbs(self) -> None:
        for i in range(self.doc.page_count):
            item = QListWidgetItem(f"{i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setIcon(QIcon())
            self.thumbs.addItem(item)
            page = self.doc.load_page(i)
            w0 = max(1.0, page.rect.width)
            zoom = THUMB_W / w0
            self._thumb_zoom[i] = zoom
            self.pool.request(i, zoom, priority=8, tag="thumb")

    def _build_outline(self) -> None:
        try:
            toc = self.doc.get_toc() or []
        except Exception:
            toc = []
        if not toc:
            self.tabs.setTabText(1, "Index")
            self.outline.addTopLevelItem(QTreeWidgetItem(["(no outline)"]))
            return
        stack: Dict[int, QTreeWidgetItem] = {}
        for level, title, page in toc:
            level = max(1, int(level))
            node = QTreeWidgetItem([str(title)])
            node.setData(0, Qt.ItemDataRole.UserRole, max(0, int(page) - 1))
            parent = stack.get(level - 1) if level > 1 else None
            if parent is not None:
                parent.addChild(node)
            else:
                self.outline.addTopLevelItem(node)
            stack[level] = node
            for deeper in [k for k in stack if k > level]:
                stack.pop(deeper, None)
        self.outline.expandAll()

    # ------------------------------------------------------------------
    def _on_render(self, result: object) -> None:
        if not isinstance(result, RenderResult) or result.tag != "thumb":
            return
        if not result.ok or not result.png:
            return
        img = QImage()
        if not img.loadFromData(result.png, "PNG"):
            return
        if not (0 <= result.page < self.thumbs.count()):
            return
        item = self.thumbs.item(result.page)
        if item is not None:
            item.setIcon(QIcon(QPixmap.fromImage(img)))
            self.thumbs.setIconSize(QSize(THUMB_W, int(THUMB_W * result.height / max(1, result.width))))

    def _on_thumb(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int):
            self.goto_page_requested.emit(page)

    def _on_outline(self, item: QTreeWidgetItem, _col: int) -> None:
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(page, int):
            self.goto_page_requested.emit(page)

    def set_current_page(self, page: int) -> None:
        """Keep the thumbnail list roughly in sync with the view."""
        if 0 <= page < self.thumbs.count():
            item = self.thumbs.item(page)
            if item is not None and self.thumbs.currentItem() is not item:
                self.thumbs.setCurrentItem(item)
