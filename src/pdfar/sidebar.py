"""Left sidebar: page thumbnails, document outline, bookmarks, and annotations."""

from __future__ import annotations

from typing import Dict, List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QImage, QPixmap, QAction, QColor
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QMenu, QInputDialog, QMessageBox, QHBoxLayout,
    QPushButton, QComboBox,
)

from .render import RenderPool, RenderResult
from .bookmarks import BookmarkManager, Bookmark
from .annotations import AnnotationManager, HighlightAnnotation, HIGHLIGHT_COLORS, AnnotationType

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

        # ---- annotations ------------------------------------------------
        self.annotations_panel = QWidget()
        ann_layout = QVBoxLayout(self.annotations_panel)
        ann_layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar for annotations
        ann_toolbar = QHBoxLayout()
        self.ann_new_highlight = QPushButton("New Highlight")
        self.ann_new_highlight.setToolTip("Create highlight from current selection (Ctrl+H)")
        self.ann_new_highlight.clicked.connect(self._on_new_highlight)
        ann_toolbar.addWidget(self.ann_new_highlight)

        self.ann_color_combo = QComboBox()
        for name, hex_color in HIGHLIGHT_COLORS:
            self.ann_color_combo.addItem(name, hex_color)
        self.ann_color_combo.setCurrentIndex(0)
        self.ann_color_combo.setToolTip("Highlight color")
        self.ann_color_combo.currentIndexChanged.connect(self._on_highlight_color_changed)
        ann_toolbar.addWidget(self.ann_color_combo)

        self.ann_save_btn = QPushButton("Save to PDF")
        self.ann_save_btn.setToolTip("Save all annotations to the PDF file")
        self.ann_save_btn.clicked.connect(self._on_save_annotations)
        ann_toolbar.addWidget(self.ann_save_btn)

        ann_toolbar.addStretch()
        ann_layout.addLayout(ann_toolbar)

        self.annotations_tree = QTreeWidget()
        self.annotations_tree.setHeaderHidden(True)
        self.annotations_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.annotations_tree.customContextMenuRequested.connect(self._on_annotations_context_menu)
        self.annotations_tree.itemActivated.connect(self._on_annotation_activated)
        ann_layout.addWidget(self.annotations_tree)

        self.tabs.addTab(self.annotations_panel, "Annotations")

        self._build_thumbs()
        self._build_outline()
        self._build_bookmarks()
        self._build_annotations()
        self.pool.result_ready.connect(self._on_render)

        # Lazy thumbnail loading: only render visible items in the thumbnail view
        self.thumbs.verticalScrollBar().valueChanged.connect(self._request_visible_thumbs)
        self.tabs.currentChanged.connect(self._on_sidebar_tab_changed)
        self._request_visible_thumbs()

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._request_visible_thumbs()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._request_visible_thumbs()

    def _on_sidebar_tab_changed(self, index: int) -> None:
        if self.tabs.tabText(index) == "Thumbnails":
            self._request_visible_thumbs()

    def _request_visible_thumbs(self) -> None:
        """Request thumbnails only for items currently visible in the sidebar widget."""
        if not self.thumbs.count():
            return

        # If thumbnails tab or sidebar is not visible, only queue a tiny initial batch
        if not self.isVisible() or self.tabs.currentWidget() != self.thumbs:
            start, end = 0, min(self.thumbs.count() - 1, 4)
        else:
            vp_rect = self.thumbs.viewport().rect()
            start_idx = None
            end_idx = None

            # Find range of items intersecting the viewport
            for i in range(self.thumbs.count()):
                item = self.thumbs.item(i)
                if item and self.thumbs.visualItemRect(item).intersects(vp_rect):
                    if start_idx is None:
                        start_idx = i
                    end_idx = i

            if start_idx is None:
                start_idx, end_idx = 0, min(self.thumbs.count() - 1, 5)

            start = max(0, start_idx - 2)
            end = min(self.thumbs.count() - 1, end_idx + 5)

        # Cancel thumbnail jobs outside the visible area
        self.pool.cancel_if(
            lambda job: job.tag == "thumb" and (job.page < start or job.page > end)
        )

        # Queue visible thumbnails with low priority (priority 8)
        for i in range(start, end + 1):
            item = self.thumbs.item(i)
            if item is not None and item.icon().isNull():
                zoom = self._thumb_zoom.get(i, 0.2)
                self.pool.request(i, zoom, priority=8, tag="thumb")

    def _build_thumbs(self) -> None:
        for i in range(self.doc.page_count):
            item = QListWidgetItem(f"{i + 1}")
            item.setData(Qt.ItemDataRole.UserRole, i)
            item.setIcon(QIcon())
            self.thumbs.addItem(item)
            try:
                r = self.doc[i].rect
                w0 = max(1.0, r.width)
            except Exception:
                w0 = 595.0
            zoom = THUMB_W / w0
            self._thumb_zoom[i] = zoom

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
        if not result.ok or (result.samples is None and result.png is None):
            return
        img = result.to_qimage()
        if img.isNull():
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

    # ------------------------------------------------------------------ annotations
    def _build_annotations(self) -> None:
        """Build the annotations tree from the annotation manager."""
        self.annotations_tree.clear()

        # We need access to the view's annotation manager
        # This will be set by the main window when the tab is activated
        if not hasattr(self, 'annotation_manager') or self.annotation_manager is None:
            self.annotations_tree.addTopLevelItem(QTreeWidgetItem(["(no annotations)"]))
            return

        all_annotations = self.annotation_manager.get_annotations()
        if not all_annotations:
            self.annotations_tree.addTopLevelItem(QTreeWidgetItem(["(no annotations)"]))
            return

        # Group by page
        from collections import defaultdict
        by_page = defaultdict(list)
        for ann in all_annotations:
            by_page[ann.page].append(ann)

        for page_idx in sorted(by_page.keys()):
            page_root = QTreeWidgetItem([f"Page {page_idx + 1}"])
            page_root.setData(0, Qt.ItemDataRole.UserRole, -1)
            page_root.setExpanded(True)

            for ann in by_page[page_idx]:
                if ann.type == AnnotationType.HIGHLIGHT:
                    color_name = next((name for name, hex_c in HIGHLIGHT_COLORS if hex_c == ann.color), "Custom")
                    text = f"  🖍️ Highlight ({color_name})"
                    if ann.has_note():
                        text += f" 💬 {ann.content[:40]}"
                else:
                    text = f"  {ann.type.value}: {ann.content[:50]}"

                node = QTreeWidgetItem([text])
                node.setData(0, Qt.ItemDataRole.UserRole, ann.id)
                node.setData(0, Qt.ItemDataRole.UserRole + 1, ann.page)
                node.setData(0, Qt.ItemDataRole.UserRole + 2, ann.type.value)

                # Show color indicator
                if ann.type == AnnotationType.HIGHLIGHT:
                    color = QColor(ann.color)
                    node.setForeground(0, color)

                page_root.addChild(node)

            self.annotations_tree.addTopLevelItem(page_root)

        self.annotations_tree.expandAll()

    def set_annotation_manager(self, annotation_manager: AnnotationManager) -> None:
        """Set the annotation manager and rebuild the tree."""
        self.annotation_manager = annotation_manager
        if hasattr(annotation_manager, 'annotations_changed'):
            try:
                annotation_manager.annotations_changed.disconnect(self._build_annotations)
            except TypeError:
                pass
            annotation_manager.annotations_changed.connect(self._build_annotations)
        self._build_annotations()

    def _on_annotation_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        """Navigate to the annotation's page on double-click/Enter."""
        ann_id = item.data(0, Qt.ItemDataRole.UserRole)
        page = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if isinstance(page, int) and page >= 0:
            self.goto_page_requested.emit(page)

    def _on_annotations_context_menu(self, pos) -> None:
        """Show context menu for annotations."""
        item = self.annotations_tree.itemAt(pos)
        if not item:
            return

        ann_id = item.data(0, Qt.ItemDataRole.UserRole)
        ann_type = item.data(0, Qt.ItemDataRole.UserRole + 2)
        if not ann_id:
            return

        menu = QMenu(self)
        act_delete = menu.addAction("Delete Annotation")
        act_delete.triggered.connect(lambda: self._delete_annotation(ann_id))

        if ann_type == "highlight":
            # Get the highlight to check if it has a note
            highlight = None
            if self.annotation_manager and ann_id in self.annotation_manager._annotations:
                highlight = self.annotation_manager._annotations[ann_id]

            menu.addSeparator()

            if highlight and highlight.has_note():
                act_edit_note = menu.addAction("Edit Note…")
                act_edit_note.triggered.connect(lambda: self._edit_note(ann_id))
                
                act_remove_note = menu.addAction("Remove Note")
                act_remove_note.triggered.connect(lambda: self._remove_note(ann_id))
            else:
                act_add_note = menu.addAction("Add Note…")
                act_add_note.triggered.connect(lambda: self._edit_note(ann_id))

        act_color = menu.addMenu("Change Color")
        for name, hex_color in HIGHLIGHT_COLORS:
            color_act = act_color.addAction(name)
            color_act.triggered.connect(lambda checked, a=ann_id, c=hex_color: self._change_annotation_color(a, c))

        menu.exec(self.annotations_tree.viewport().mapToGlobal(pos))

    def _edit_note(self, annotation_id: str) -> None:
        """Edit a note for a highlight."""
        if not self.annotation_manager or annotation_id not in self.annotation_manager._annotations:
            return
        ann = self.annotation_manager._annotations[annotation_id]
        if ann.type != AnnotationType.HIGHLIGHT:
            return

        current_note = ann.get_note()
        new_note, ok = QInputDialog.getMultiLineText(
            self, "Edit Note",
            f"Note for highlight on page {ann.page + 1}:",
            current_note
        )
        if ok:
            if new_note.strip():
                self.annotation_manager.add_note_to_highlight(annotation_id, new_note.strip())
            else:
                # Empty note = remove it
                self.annotation_manager.remove_note_from_highlight(annotation_id)

    def _remove_note(self, annotation_id: str) -> None:
        """Remove the note from a highlight."""
        if self.annotation_manager:
            self.annotation_manager.remove_note_from_highlight(annotation_id)

    def _delete_annotation(self, annotation_id: str) -> None:
        """Delete an annotation."""
        if self.annotation_manager:
            self.annotation_manager.remove_annotation(annotation_id)

    def _change_annotation_color(self, annotation_id: str, color: str) -> None:
        """Change an annotation's color."""
        if self.annotation_manager:
            self.annotation_manager.update_annotation(annotation_id, color=color)

    def _on_new_highlight(self) -> None:
        """Create a highlight from the current view selection."""
        # This is called from the sidebar toolbar
        # The actual creation happens in the view via the main window
        pass  # Handled by main window

    def _on_highlight_color_changed(self, index: int) -> None:
        """Handle highlight color change."""
        if hasattr(self, 'view') and self.view:
            hex_color = self.ann_color_combo.itemData(index)
            self.view.set_highlight_color(hex_color)

    def _on_save_annotations(self) -> None:
        """Save annotations to PDF."""
        if self.annotation_manager:
            success = self.annotation_manager.save_to_pdf()
            if success:
                QMessageBox.information(self, "Save Annotations", "Annotations saved to PDF.")
            else:
                QMessageBox.warning(self, "Save Annotations", "Failed to save annotations.")
