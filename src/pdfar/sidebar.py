"""Left sidebar: page thumbnails, document outline, and user bookmarks."""

from __future__ import annotations

from typing import Dict, List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap, QAction
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QMenu, QInputDialog, QMessageBox,
)

from .render import RenderPool, RenderResult
from .bookmarks import BookmarkManager, Bookmark

THUMB_W = 120


class Sidebar(QWidget):
    """Thumbnails + table of contents + user bookmarks."""

    goto_page_requested = pyqtSignal(int)

    def __init__(self, doc: fitz.Document, pool: RenderPool, doc_path: str, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.pool = pool
        self.doc_path = doc_path
        self._thumb_zoom: Dict[int, float] = {}

        self.bookmark_manager = BookmarkManager(doc_path, doc)

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

        # ---- bookmarks --------------------------------------------------
        self.bookmarks_tree = QTreeWidget()
        self.bookmarks_tree.setHeaderHidden(True)
        self.bookmarks_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.bookmarks_tree.customContextMenuRequested.connect(self._on_bookmarks_context_menu)
        self.bookmarks_tree.itemActivated.connect(self._on_bookmark_activated)
        self.tabs.addTab(self.bookmarks_tree, "Bookmarks")

        self._build_thumbs()
        self._build_outline()
        self._build_bookmarks()
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

    # ------------------------------------------------------------------ bookmarks
    def _build_bookmarks(self) -> None:
        """Build the bookmarks tree from user bookmarks and PDF outline."""
        self.bookmarks_tree.clear()
        all_bookmarks = self.bookmark_manager.get_all_bookmarks()

        if not all_bookmarks:
            self.bookmarks_tree.addTopLevelItem(QTreeWidgetItem(["(no bookmarks)"]))
            return

        # Separate user and outline bookmarks
        user_bookmarks = [b for b in all_bookmarks if b.is_user]
        outline_bookmarks = [b for b in all_bookmarks if not b.is_user]

        # Build user bookmarks (flat list for now, can add hierarchy later)
        if user_bookmarks:
            user_root = QTreeWidgetItem(["📑 User Bookmarks"])
            user_root.setData(0, Qt.ItemDataRole.UserRole, -1)  # marker
            user_root.setExpanded(True)
            for bm in user_bookmarks:
                node = QTreeWidgetItem([f"  {bm.title} (p. {bm.page + 1})"])
                node.setData(0, Qt.ItemDataRole.UserRole, bm.page)
                node.setData(0, Qt.ItemDataRole.UserRole + 1, "user")  # type marker
                user_root.addChild(node)
            self.bookmarks_tree.addTopLevelItem(user_root)

        # Build outline bookmarks with hierarchy
        if outline_bookmarks:
            outline_root = QTreeWidgetItem(["📖 Document Outline"])
            outline_root.setData(0, Qt.ItemDataRole.UserRole, -1)
            outline_root.setExpanded(True)
            stack: Dict[int, QTreeWidgetItem] = {}
            for bm in outline_bookmarks:
                node = QTreeWidgetItem([bm.title])
                node.setData(0, Qt.ItemDataRole.UserRole, bm.page)
                node.setData(0, Qt.ItemDataRole.UserRole + 1, "outline")
                parent = stack.get(bm.level - 1) if bm.level > 1 else outline_root
                if parent is not None:
                    parent.addChild(node)
                else:
                    outline_root.addChild(node)
                stack[bm.level] = node
                for deeper in [k for k in stack if k > bm.level]:
                    stack.pop(deeper, None)
            self.bookmarks_tree.addTopLevelItem(outline_root)

        self.bookmarks_tree.expandAll()

    def _on_bookmark_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        """Navigate to the bookmark's page on double-click/Enter."""
        page = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and page >= 0:
            self.goto_page_requested.emit(page)

    def _on_bookmarks_context_menu(self, pos) -> None:
        """Show context menu for bookmarks (only for user bookmarks)."""
        item = self.bookmarks_tree.itemAt(pos)
        if not item:
            return

        bm_type = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if bm_type != "user":
            return  # only allow editing user bookmarks

        page = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(page, int) or page < 0:
            return

        menu = QMenu(self)
        act_rename = menu.addAction("Rename")
        act_delete = menu.addAction("Delete")

        act_rename.triggered.connect(lambda: self._rename_bookmark(item))
        act_delete.triggered.connect(lambda: self._delete_bookmark(item))
        menu.exec(self.bookmarks_tree.viewport().mapToGlobal(pos))

    def _rename_bookmark(self, item: QTreeWidgetItem) -> None:
        """Rename a user bookmark."""
        current_title = item.text(0).strip()
        # Extract title without page number
        if " (p. " in current_title:
            current_title = current_title.split(" (p. ")[0]
        new_title, ok = QInputDialog.getText(self, "Rename Bookmark", "Title:", text=current_title)
        if ok and new_title.strip():
            # Find the bookmark index in user bookmarks
            page = item.data(0, Qt.ItemDataRole.UserRole)
            user_bms = self.bookmark_manager.get_user_bookmarks()
            for i, bm in enumerate(user_bms):
                if bm.page == page and bm.title == current_title:
                    self.bookmark_manager.rename_user_bookmark(i, new_title.strip())
                    self._build_bookmarks()
                    break

    def _delete_bookmark(self, item: QTreeWidgetItem) -> None:
        """Delete a user bookmark."""
        current_title = item.text(0).strip()
        if " (p. " in current_title:
            current_title = current_title.split(" (p. ")[0]
        page = item.data(0, Qt.ItemDataRole.UserRole)
        user_bms = self.bookmark_manager.get_user_bookmarks()
        for i, bm in enumerate(user_bms):
            if bm.page == page and bm.title == current_title:
                reply = QMessageBox.question(
                    self, "Delete Bookmark",
                    f"Delete bookmark '{bm.title}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.bookmark_manager.remove_user_bookmark(i)
                    self._build_bookmarks()
                break

    def add_bookmark_at_current_page(self, page: int, title: Optional[str] = None) -> None:
        """Add a user bookmark at the given page. If title is None, prompt the user."""
        if title is None:
            title, ok = QInputDialog.getText(
                self, "Add Bookmark",
                f"Title for bookmark on page {page + 1}:"
            )
            if not ok or not title.strip():
                return
        self.bookmark_manager.add_user_bookmark(title.strip(), page)
        self._build_bookmarks()

    def shutdown(self) -> None:
        """Disconnect signals and clean up."""
        try:
            self.pool.result_ready.disconnect(self._on_render)
        except TypeError:
            pass
