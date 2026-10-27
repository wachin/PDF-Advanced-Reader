"""Left sidebar: page thumbnails, document outline, bookmarks, annotations, and attachments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import (
    QPoint, QRect, QSettings, QSize, Qt, pyqtSignal,
)
from PyQt6.QtGui import (
    QColor, QIcon, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget, QMenu, QInputDialog, QMessageBox, QHBoxLayout,
    QPushButton, QComboBox, QFileDialog, QProgressBar, QStyle,
    QStyledItemDelegate, QLabel, QToolButton, QLayout, QLayoutItem,
)
from . import APP_NAME
from .edits import Scope
from .icons import tinted_icon
from .render import RenderPool, RenderResult
from .session import DocumentSession
from .bookmarks import BookmarkManager
from .annotations import AnnotationManager, HIGHLIGHT_COLORS, AnnotationType

THUMB_DEFAULT_WIDTH = 140
THUMB_MIN_WIDTH = 96
THUMB_MAX_WIDTH = 200
THUMB_RATIO = 1.414

# Sidebar tabs are icon-only; the label is the hover tooltip.
TAB_LABELS = ("Thumbnails", "Index", "Bookmarks", "Annotations", "Attachments")
TAB_ICONS = ("thumbnails.svg", "index.svg", "bookmarks.svg",
             "annotations.svg", "attachments.svg")
(TAB_THUMBNAILS, TAB_INDEX, TAB_BOOKMARKS, TAB_ANNOTATIONS,
 TAB_ATTACHMENTS) = range(len(TAB_LABELS))


@dataclass
class ThumbnailSettings:
    """User preferences for the thumbnail strip."""

    width: int = THUMB_DEFAULT_WIDTH
    border_width: int = 1
    shadow: bool = True

    @property
    def height(self) -> int:
        return int(self.width * THUMB_RATIO)

    @classmethod
    def load(cls) -> "ThumbnailSettings":
        settings = QSettings(APP_NAME, APP_NAME)
        width = int(settings.value("thumbnail_width", THUMB_DEFAULT_WIDTH))
        border = int(settings.value("thumbnail_border_width", 1))
        return cls(
            width=max(THUMB_MIN_WIDTH, min(THUMB_MAX_WIDTH, width)),
            border_width=max(1, min(3, border)),
            shadow=settings.value("thumbnail_shadow", True, type=bool),
        )

    def save(self) -> None:
        settings = QSettings(APP_NAME, APP_NAME)
        settings.setValue("thumbnail_width", self.width)
        settings.setValue("thumbnail_border_width", self.border_width)
        settings.setValue("thumbnail_shadow", self.shadow)


class ThumbnailDelegate(QStyledItemDelegate):
    """Draw one page thumbnail and its label centered in a full-width row."""

    PAD = 6

    def __init__(self, settings: ThumbnailSettings,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.settings = settings

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(
            self.settings.width + 24,
            self.PAD * 2 + self.settings.height + 2 + option.fontMetrics.height(),
        )

    def paint(self, painter, option, index) -> None:  # noqa: N802 (Qt naming)
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.fillRect(option.rect, option.palette.highlight())

        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(icon, QIcon):
            pixmap = icon.pixmap(QSize(self.settings.width, self.settings.height))
            if not pixmap.isNull():
                x = option.rect.center().x() - pixmap.width() // 2
                y = option.rect.top() + self.PAD + (self.settings.height - pixmap.height()) // 2
                if self.settings.shadow:
                    for offset, alpha in ((3, 26), (2, 20), (1, 12)):
                        painter.fillRect(
                            QRect(x + offset, y + offset, pixmap.width(), pixmap.height()),
                            QColor(0, 0, 0, alpha),
                        )
                painter.drawPixmap(x, y, pixmap)
                painter.setPen(QPen(QColor(0, 0, 0), self.settings.border_width))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRect(x, y, pixmap.width() - 1, pixmap.height() - 1))

        painter.setPen(option.palette.highlightedText().color() if selected
                       else option.palette.text().color())
        text_rect = QRect(
            option.rect.left(),
            option.rect.top() + self.PAD + self.settings.height + 2,
            option.rect.width(),
            option.fontMetrics.height(),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            str(index.data(Qt.ItemDataRole.DisplayRole)),
        )
        painter.restore()


class ThumbnailList(QListWidget):
    """Page thumbnails in one centered column, regardless of sidebar width."""

    def __init__(self, settings: ThumbnailSettings,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.delegate = ThumbnailDelegate(settings, self)
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setMovement(QListWidget.Movement.Static)
        self.setUniformItemSizes(True)
        self.setIconSize(QSize(settings.width, settings.height))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setItemDelegate(self.delegate)

    def apply_settings(self, settings: ThumbnailSettings) -> None:
        self.delegate.settings = settings
        self.setIconSize(QSize(settings.width, settings.height))
        self.doItemsLayout()
        self.viewport().update()


class _FlowLayout(QLayout):
    """Lay widgets left to right, wrapping onto new rows when space runs out.

    The annotations and attachments toolbars use this so they do not force a
    wide minimum width on the sidebar dock.
    """

    def __init__(self, parent=None, spacing: int = 4):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: N802 - Qt API
        self._items.append(item)

    def count(self) -> int:  # noqa: N802 - Qt API
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 - Qt API
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):  # noqa: N802 - Qt API
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._layout_rows(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt API
        super().setGeometry(rect)
        self._layout_rows(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt API
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size.expandedTo(QSize(
            margins.left() + margins.right(),
            margins.top() + margins.bottom()))

    def _layout_rows(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(),
                             -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        spacing = self.spacing()
        for item in self._items:
            hint = item.sizeHint()
            next_x = x + hint.width() + spacing
            if next_x - spacing > area.right() and row_height > 0:
                x = area.x()
                y += row_height + spacing
                next_x = x + hint.width() + spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


class Sidebar(QWidget):
    """Thumbnails + table of contents + user bookmarks."""

    goto_page_requested = pyqtSignal(int)
    thumbnail_size_changed = pyqtSignal(int)
    # The window owns saving: it is the one place that can explain a refusal, and
    # a second opinion from here would only be able to say "failed".
    save_requested = pyqtSignal()

    def __init__(self, session: DocumentSession, pool: RenderPool, parent=None):
        super().__init__(parent)
        self.session = session
        self.pool = pool
        self._thumb_zoom: Dict[int, float] = {}
        self._thumb_settings = ThumbnailSettings.load()

        self.bookmark_manager = BookmarkManager(session)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setIconSize(QSize(18, 18))
        # Icon-only tabs need less padding than the theme's default, otherwise
        # five tabs overflow the sidebar dock in dark mode.
        self.tabs.tabBar().setStyleSheet("QTabBar::tab { padding: 4px 6px; }")
        layout.addWidget(self.tabs)

        # ---- thumbnails -------------------------------------------------
        self.thumbs = ThumbnailList(self._thumb_settings)
        self.thumbs.itemDoubleClicked.connect(self._on_thumb)
        self.thumbs_tab = QWidget()
        thumbs_layout = QVBoxLayout(self.thumbs_tab)
        thumbs_layout.setContentsMargins(0, 0, 0, 0)
        thumbs_layout.setSpacing(0)
        thumbs_layout.addWidget(self._build_thumb_size_row())
        thumbs_layout.addWidget(self.thumbs, 1)
        self.tabs.addTab(self.thumbs_tab, "Thumbnails")

        # ---- outline ----------------------------------------------------
        self.outline = QTreeWidget()
        self.outline.setHeaderHidden(True)
        self.outline.itemActivated.connect(self._on_outline)
        self.outline_panel = QWidget()
        outline_layout = QVBoxLayout(self.outline_panel)
        outline_layout.setContentsMargins(0, 0, 0, 0)
        outline_layout.setSpacing(0)
        self.outline_toolbar = self._build_outline_toolbar()
        outline_layout.addWidget(self.outline_toolbar)
        outline_layout.addWidget(self.outline, 1)
        self.tabs.addTab(self.outline_panel, "Index")

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
        ann_toolbar = _FlowLayout()
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

        ann_layout.addLayout(ann_toolbar)

        self.annotations_tree = QTreeWidget()
        self.annotations_tree.setHeaderHidden(True)
        self.annotations_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.annotations_tree.customContextMenuRequested.connect(self._on_annotations_context_menu)
        self.annotations_tree.itemActivated.connect(self._on_annotation_activated)
        ann_layout.addWidget(self.annotations_tree)

        self.tabs.addTab(self.annotations_panel, "Annotations")

        # ---- attachments ------------------------------------------------
        self.attachments_panel = QWidget()
        att_layout = QVBoxLayout(self.attachments_panel)
        att_layout.setContentsMargins(4, 4, 4, 4)

        # Toolbar for attachments
        att_toolbar = _FlowLayout()
        self.att_extract_btn = QPushButton("Extract")
        self.att_extract_btn.setToolTip("Extract selected attachment")
        self.att_extract_btn.clicked.connect(self._extract_attachment)
        self.att_extract_btn.setEnabled(False)
        att_toolbar.addWidget(self.att_extract_btn)

        self.att_extract_all_btn = QPushButton("Extract All")
        self.att_extract_all_btn.setToolTip("Extract all attachments")
        self.att_extract_all_btn.clicked.connect(self._extract_all_attachments)
        att_toolbar.addWidget(self.att_extract_all_btn)

        att_layout.addLayout(att_toolbar)

        self.attachments_tree = QTreeWidget()
        self.attachments_tree.setHeaderHidden(True)
        self.attachments_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.attachments_tree.customContextMenuRequested.connect(self._on_attachments_context_menu)
        self.attachments_tree.itemSelectionChanged.connect(self._on_attachment_selection_changed)
        self.attachments_tree.itemActivated.connect(self._on_attachment_activated)
        att_layout.addWidget(self.attachments_tree)

        self.tabs.addTab(self.attachments_panel, "Attachments")
        self._install_tab_icons()

        self._build_thumbs()
        self._build_outline()
        self._build_bookmarks()
        self._build_annotations()
        self.pool.result_ready.connect(self._on_render)

        # Lazy thumbnail loading: only render visible items in the thumbnail view
        self.thumbs.verticalScrollBar().valueChanged.connect(self._request_visible_thumbs)
        self.tabs.currentChanged.connect(self._on_sidebar_tab_changed)
        self._request_visible_thumbs()

    # ----------------------------------------------------------------- doc
    @property
    def doc(self) -> fitz.Document:
        """The live document; an accepted edit replaces the object."""
        return self.session.doc

    @property
    def doc_path(self) -> str:
        return self.session.path

    def _build_thumb_size_row(self) -> QWidget:
        """Slim row with minus, current size, and plus controls."""
        row = QWidget()
        row.setFixedHeight(24)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)

        self.thumb_minus = QToolButton(row)
        self.thumb_minus.setText("−")
        self.thumb_minus.setAutoRaise(True)
        self.thumb_minus.setFixedSize(20, 20)
        self.thumb_minus.setToolTip("Smaller thumbnails")
        self.thumb_minus.clicked.connect(lambda: self._step_thumb_size(-8))
        layout.addStretch(1)
        layout.addWidget(self.thumb_minus)

        self.thumb_size_label = QLabel(f"{self._thumb_settings.width} px", row)
        self.thumb_size_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_size_label.setToolTip("Page thumbnail size")
        layout.addWidget(self.thumb_size_label)

        self.thumb_plus = QToolButton(row)
        self.thumb_plus.setText("+")
        self.thumb_plus.setAutoRaise(True)
        self.thumb_plus.setFixedSize(20, 20)
        self.thumb_plus.setToolTip("Larger thumbnails")
        self.thumb_plus.clicked.connect(lambda: self._step_thumb_size(8))
        layout.addWidget(self.thumb_plus)
        layout.addStretch(1)

        self._update_thumb_size_controls()
        return row

    def _build_outline_toolbar(self) -> QWidget:
        """Slim T1–T4 level controls above the outline tree."""
        row = QWidget()
        row.setFixedHeight(24)
        row.setStyleSheet(
            "QToolButton { border-radius: 3px; }"
            "QToolButton:checked {"
            "  background-color: palette(highlight);"
            "  color: palette(highlighted-text);"
            "}")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(2)
        self.outline_buttons = []
        for level in range(1, 5):
            button = QToolButton(row)
            button.setText(f"T{level}")
            button.setAutoRaise(True)
            button.setCheckable(True)
            button.setFixedSize(28, 20)
            if level == 1:
                hint = "Show only Heading 1 entries"
            else:
                hint = f"Expand headings up to level {level}"
            button.setToolTip(hint + "; click again to expand everything")
            button.clicked.connect(
                lambda _checked=False, lvl=level:
                self._apply_outline_level(lvl))
            button.setEnabled(False)
            layout.addWidget(button)
            self.outline_buttons.append(button)
        layout.addStretch(1)
        return row

    def _apply_outline_level(self, level: int) -> None:
        """Collapse to *level* headings, or expand everything when repeated."""
        button = self.outline_buttons[level - 1]
        active = button.isChecked()
        for other in self.outline_buttons:
            other.setChecked(other is button and active)
        if not active:
            self.outline.expandAll()
            return
        root = self.outline.invisibleRootItem()
        for index in range(root.childCount()):
            self._set_outline_depth(root.child(index), 1, level)

    def _set_outline_depth(self, item: QTreeWidgetItem, depth: int,
                           level: int) -> None:
        item.setExpanded(depth < level)
        for index in range(item.childCount()):
            self._set_outline_depth(item.child(index), depth + 1, level)

    def _step_thumb_size(self, delta: int) -> None:
        width = self._thumb_settings.width + delta
        if THUMB_MIN_WIDTH <= width <= THUMB_MAX_WIDTH:
            self.thumbnail_size_changed.emit(width)

    def _update_thumb_size_controls(self) -> None:
        self.thumb_size_label.setText(f"{self._thumb_settings.width} px")
        self.thumb_minus.setEnabled(self._thumb_settings.width > THUMB_MIN_WIDTH)
        self.thumb_plus.setEnabled(self._thumb_settings.width < THUMB_MAX_WIDTH)

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._request_visible_thumbs()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._request_visible_thumbs()

    def _install_tab_icons(self) -> None:
        """Show the tabs as icons only; each label appears on hover."""
        for index, (label, _name) in enumerate(zip(TAB_LABELS, TAB_ICONS)):
            self.tabs.setTabText(index, "")
            self.tabs.setTabToolTip(index, label)
            self.tabs.tabBar().setAccessibleTabName(index, label)
        self.apply_icon_palette(False)

    def apply_icon_palette(self, dark: bool) -> None:
        """Re-tint the tab icons so they stay visible in the active theme."""
        ink = "#e6e6e6" if dark else "#3c3c3c"
        self._icon_color = ink
        for index, name in enumerate(TAB_ICONS):
            self.tabs.setTabIcon(index, tinted_icon(name, ink))

    def _on_sidebar_tab_changed(self, index: int) -> None:
        if self.tabs.widget(index) is self.thumbs_tab:
            self._request_visible_thumbs()

    def _request_visible_thumbs(self) -> None:
        """Request thumbnails only for items currently visible in the sidebar widget."""
        if not self.thumbs.count():
            return

        # If thumbnails tab or sidebar is not visible, only queue a tiny initial batch
        if not self.isVisible() or self.tabs.currentWidget() != self.thumbs_tab:
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
            self._thumb_zoom[i] = self._thumb_settings.width / w0

    #: What can make a thumbnail stale.  Metadata is not in it: a title change
    #: moves no pixel, and clearing 540 thumbnails for one is the waste F10-1
    #: scope grading exists to avoid.
    _THUMB_SCOPES = Scope.STRUCTURE | Scope.CONTENT | Scope.COMMENTS | Scope.FORM

    def invalidate_thumbnails(self, version: int = -1, scope: int = Scope.FULL) -> None:
        """Drop thumbnails after a change to the document.

        Connected to ``PDFView.content_changed``, which reports the scope of the
        edit that caused it; ``scope`` decides how much of the sidebar is rebuilt.
        """
        affected = Scope(scope)
        if not affected & self._THUMB_SCOPES:
            return
        if affected & Scope.STRUCTURE:
            # The page tree itself moved: the list can be the wrong length.
            self.thumbs.clear()
            self._thumb_zoom.clear()
            self._build_thumbs()
            self.outline.clear()
            self._build_outline()
        else:
            for i in range(self.thumbs.count()):
                item = self.thumbs.item(i)
                if item is not None:
                    item.setIcon(QIcon())
        self._request_visible_thumbs()

    def apply_thumbnail_settings(self, settings: ThumbnailSettings) -> None:
        previous_width = self._thumb_settings.width
        self._thumb_settings = settings
        self.thumbs.apply_settings(settings)
        self._update_thumb_size_controls()

        if settings.width != previous_width:
            scale = settings.width / previous_width
            for page in self._thumb_zoom:
                self._thumb_zoom[page] *= scale
            self.invalidate_thumbnails()

    def _build_outline(self) -> None:
        try:
            toc = self.doc.get_toc() or []
        except Exception:
            toc = []
        for button in self.outline_buttons:
            button.setChecked(False)
            button.setEnabled(bool(toc))
        if not toc:
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
        if result.version != self.pool.session.version:
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
        """Build the bookmarks tree from user bookmarks only."""
        self.bookmarks_tree.clear()
        user_bookmarks = self.bookmark_manager.get_user_bookmarks()

        if not user_bookmarks:
            self.bookmarks_tree.addTopLevelItem(QTreeWidgetItem(["(no bookmarks)"]))
            return

        # Flat list for now, can add hierarchy later
        user_root = QTreeWidgetItem(["📑 User Bookmarks"])
        user_root.setData(0, Qt.ItemDataRole.UserRole, -1)  # marker
        user_root.setExpanded(True)
        for bm in user_bookmarks:
            node = QTreeWidgetItem([f"  {bm.title} (p. {bm.page + 1})"])
            node.setData(0, Qt.ItemDataRole.UserRole, bm.page)
            node.setData(0, Qt.ItemDataRole.UserRole + 1, "user")  # type marker
            user_root.addChild(node)
        self.bookmarks_tree.addTopLevelItem(user_root)
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

    def _build_attachments(self) -> None:
        """Build the attachments tree from the PDF's embedded files."""
        self.attachments_tree.clear()
        
        try:
            emb_count = self.doc.embfile_count()
        except Exception:
            emb_count = 0
        
        if emb_count == 0:
            self.attachments_tree.addTopLevelItem(QTreeWidgetItem(["(no attachments)"]))
            return
        
        for i in range(emb_count):
            try:
                info = self.doc.embfile_info(i)
                name = info.get('filename', f'attachment_{i}')
                size = info.get('size', 0)
                desc = info.get('desc', '')
                mimetype = info.get('mimetype', '')
                
                # Format size
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"
                
                # Create display text
                display = f"  {name} ({size_str})"
                if desc:
                    display += f" - {desc}"
                if mimetype:
                    display += f" [{mimetype}]"
                
                node = QTreeWidgetItem([display])
                node.setData(0, Qt.ItemDataRole.UserRole, i)  # attachment index
                node.setData(0, Qt.ItemDataRole.UserRole + 1, name)  # filename
                node.setData(0, Qt.ItemDataRole.UserRole + 2, size)  # size
                node.setData(0, Qt.ItemDataRole.UserRole + 3, mimetype)  # mimetype
                self.attachments_tree.addTopLevelItem(node)
            except Exception:
                pass  # Skip problematic attachments
        
        self.attachments_tree.expandAll()

    def _on_attachment_selection_changed(self) -> None:
        """Enable/disable extract button based on selection."""
        has_selection = len(self.attachments_tree.selectedItems()) > 0
        self.att_extract_btn.setEnabled(has_selection)
        self.att_extract_all_btn.setEnabled(self.attachments_tree.topLevelItemCount() > 0)

    def _on_attachment_activated(self, item: QTreeWidgetItem, _col: int) -> None:
        """Extract attachment on double-click/Enter."""
        self._extract_attachment(item)

    def _on_attachments_context_menu(self, pos) -> None:
        """Show context menu for attachments."""
        item = self.attachments_tree.itemAt(pos)
        if not item:
            return
        
        menu = QMenu(self)
        act_extract = menu.addAction("Extract")
        act_extract.triggered.connect(lambda: self._extract_attachment(item))
        
        menu.addSeparator()
        
        act_extract_all = menu.addAction("Extract All")
        act_extract_all.triggered.connect(self._extract_all_attachments)
        
        menu.exec(self.attachments_tree.viewport().mapToGlobal(pos))

    def _extract_attachment(self, item: QTreeWidgetItem) -> None:
        """Extract a single attachment."""
        if not self.doc:
            return
        
        idx = item.data(0, Qt.ItemDataRole.UserRole)
        name = item.data(0, Qt.ItemDataRole.UserRole + 1)
        if not isinstance(idx, int) or not isinstance(name, str):
            return
        
        # Ask for save location
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Extract Attachment", name, "All Files (*)"
        )
        if not save_path:
            return
        
        try:
            data = self.doc.embfile_get(idx)
            with open(save_path, 'wb') as f:
                f.write(data)
            QMessageBox.information(self, "Extract Attachment", 
                f"Attachment '{name}' extracted successfully to:\n{save_path}")
        except Exception as exc:
            QMessageBox.warning(self, "Extract Attachment", 
                f"Failed to extract attachment:\n{exc}")

    def _extract_all_attachments(self) -> None:
        """Extract all attachments to a directory."""
        if not self.doc:
            return
        
        try:
            emb_count = self.doc.embfile_count()
        except Exception:
            emb_count = 0
        
        if emb_count == 0:
            return
        
        # Ask for directory
        dir_path = QFileDialog.getExistingDirectory(
            self, "Extract All Attachments", ""
        )
        if not dir_path:
            return
        
        # Progress dialog
        from PyQt6.QtWidgets import QProgressDialog
        progress = QProgressDialog("Extracting attachments...", "Cancel", 0, emb_count, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        extracted = 0
        failed = 0
        for i in range(emb_count):
            if progress.wasCanceled():
                break
            
            progress.setValue(i)
            app = QApplication.instance()
            if app:
                app.processEvents()
            
            try:
                info = self.doc.embfile_info(i)
                name = info.get('filename', f'attachment_{i}')
                data = self.doc.embfile_get(i)
                save_path = f"{dir_path}/{name}"
                with open(save_path, 'wb') as f:
                    f.write(data)
                extracted += 1
            except Exception:
                failed += 1
        
        progress.setValue(emb_count)
        QMessageBox.information(self, "Extract All Attachments",
            f"Extracted: {extracted}\nFailed: {failed}\nSaved to: {dir_path}")

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
                    # A note is named for what it is, not for its subtype string.
                    kind = ann.type.value.replace("_", " ").title()
                    text = f"  📝 {kind}"
                    if ann.has_note():
                        text += f": {ann.content[:50]}"

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

        menu = self._annotation_menu(ann_id, ann_type)
        menu.exec(self.annotations_tree.viewport().mapToGlobal(pos))

    def _annotation_menu(self, ann_id: str, ann_type: str) -> QMenu:
        """The menu for one annotation row: delete it, note on it, recolor it.

        A note is offered on either kind because a highlight's text and a
        sticky note's text are the same ``/Contents``; a color is offered only
        where there is a color to change.
        """
        menu = QMenu(self)
        act_delete = menu.addAction("Delete Annotation")
        act_delete.triggered.connect(lambda: self._delete_annotation(ann_id))

        ann = (self.annotation_manager.get_annotation(ann_id)
               if self.annotation_manager else None)

        menu.addSeparator()
        if ann is not None and ann.has_note():
            act_edit_note = menu.addAction("Edit Note…")
            act_edit_note.triggered.connect(lambda: self._edit_note(ann_id))
            act_remove_note = menu.addAction("Remove Note")
            act_remove_note.triggered.connect(lambda: self._remove_note(ann_id))
        else:
            act_add_note = menu.addAction("Add Note…")
            act_add_note.triggered.connect(lambda: self._edit_note(ann_id))

        if ann_type == "highlight":
            # Only a highlight has a color PDFAR can change; a note row would
            # get a submenu of items that quietly do nothing.
            act_color = menu.addMenu("Change Color")
            for name, hex_color in HIGHLIGHT_COLORS:
                color_act = act_color.addAction(name)
                color_act.triggered.connect(
                    lambda checked, a=ann_id, c=hex_color:
                    self._change_annotation_color(a, c))
        return menu

    def _edit_note(self, annotation_id: str) -> None:
        """Edit the text an annotation carries."""
        if not self.annotation_manager:
            return
        ann = self.annotation_manager.get_annotation(annotation_id)
        if ann is None:
            return

        current_note = ann.get_note()
        new_note, ok = QInputDialog.getMultiLineText(
            self, "Edit Note",
            f"Note on page {ann.page + 1}:",
            current_note
        )
        if ok:
            if new_note.strip():
                self.annotation_manager.set_note(annotation_id, new_note.strip())
            else:
                # Empty note = remove it
                self.annotation_manager.remove_note(annotation_id)

    def _remove_note(self, annotation_id: str) -> None:
        """Remove the note from an annotation."""
        if self.annotation_manager:
            self.annotation_manager.remove_note(annotation_id)

    def _delete_annotation(self, annotation_id: str) -> None:
        """Delete an annotation."""
        if self.annotation_manager:
            self.annotation_manager.remove_annotation(annotation_id)

    def _change_annotation_color(self, annotation_id: str, color: str) -> None:
        """Change an annotation's color."""
        if self.annotation_manager:
            self.annotation_manager.set_highlight_color(annotation_id, color)

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
        """Ask the window to save; it reports, because only it can explain why."""
        self.save_requested.emit()
