"""Main window and application entry point."""

from __future__ import annotations

import os
import sys
from typing import List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
import hashlib
import time

from PyQt6.QtCore import QSettings, QSize, QThread, Qt
from PyQt6.QtGui import (
    QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QIcon,
    QKeySequence, QPainter, QPixmap, QShortcut,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton, QSpinBox,
    QStatusBar, QTabWidget, QToolBar, QToolButton, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .i18n import I18n
from .icons import asset_path as _asset_path, tinted_icon
from .search import SearchParams, SearchWorker, normalize_space
from .sidebar import (
    Sidebar, ThumbnailSettings, THUMB_MAX_WIDTH, THUMB_MIN_WIDTH,
    TAB_ATTACHMENTS,
)
from .tabs import DocumentTab
from .viewer import PDFView, MEMORY_LEVELS

ZOOM_STEP_PRESETS = (0.025, 0.05, 0.075, 0.10, 0.125,
                     0.15, 0.175, 0.20, 0.225, 0.25)
DEFAULT_ZOOM_STEP = 0.10


def _format_zoom(zoom: float) -> str:
    """Format a zoom factor as a percentage (half-points when needed)."""
    pct = zoom * 100.0
    if abs(pct - round(pct)) < 0.01:
        return f"{int(round(pct))}%"
    return f"{pct:.1f}%"


class MainWindow(QMainWindow):
    """PDFAR main window."""

    def __init__(self):
        super().__init__()
        self.tabs = QTabWidget()
        self._connected_tab: Optional[DocumentTab] = None
        self._search_tab: Optional[DocumentTab] = None
        self._presentation = False
        self.memory_level = "normal"     # preload level applied to new views
        self.zoom_step = DEFAULT_ZOOM_STEP
        self.ctrl_wheel_step = True
        self.search_thread: Optional[QThread] = None
        self.search_worker: Optional[SearchWorker] = None
        self.settings = QSettings(APP_NAME, APP_NAME)

        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self._close_tab)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        plus = QPushButton("+")
        plus.setFlat(True)
        plus.setToolTip("Open a PDF in a new tab")
        plus.clicked.connect(self.open_dialog)
        self.tabs.setCornerWidget(plus, Qt.Corner.TopRightCorner)
        self.setCentralWidget(self.tabs)

        self.setWindowTitle(f"{APP_NAME} — Advanced PDF Reader")
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        # Set window icon
        self.setWindowIcon(QIcon(_asset_path("icon.svg")))

        self._build_actions()
        self._build_menubar()
        self._build_toolbar()
        self._build_search_dock()
        self._build_statusbar()
        self._build_shortcuts()
        self._restore_state()

    # --------------------------------------------- active document accessors
    def _active_tab(self) -> Optional[DocumentTab]:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DocumentTab) else None

    @property
    def view(self) -> Optional[PDFView]:
        tab = self._active_tab()
        return tab.view if tab else None

    @property
    def sidebar(self) -> Optional[Sidebar]:
        tab = self._active_tab()
        return tab.sidebar if tab else None

    @property
    def doc_path(self) -> Optional[str]:
        tab = self._active_tab()
        return tab.doc_path if tab else None

    # ------------------------------------------------------------- UI
    def _build_actions(self) -> None:
        # File menu actions
        self.act_new = QAction("&New", self)
        self.act_new.setShortcut(QKeySequence.StandardKey.New)
        self.act_new.triggered.connect(self.open_dialog)

        self.act_open = QAction("&Open…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)

        self.act_save = QAction("&Save", self)
        self.act_save.setShortcut(QKeySequence.StandardKey.Save)
        self.act_save.setToolTip("Save document (annotations, bookmarks, etc.)")
        self.act_save.triggered.connect(self._save_document)

        self.act_save_as = QAction("Save &As…", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_save_as.triggered.connect(self._save_document_as)

        self.act_print = QAction("&Print…", self)
        self.act_print.setShortcut(QKeySequence.StandardKey.Print)
        self.act_print.triggered.connect(self._print_document)

        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)

        # Edit menu actions
        self.act_copy = QAction("&Copy", self)
        self.act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        self.act_copy.triggered.connect(self._copy)

        self.act_select_all = QAction("Select &All", self)
        self.act_select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        self.act_select_all.triggered.connect(self._select_all)

        # View menu actions
        self.act_presentation = QAction("Presentation", self)
        self.act_presentation.setShortcut("F11")
        self.act_presentation.triggered.connect(self.toggle_presentation)

        self.act_sidebar = QAction("Sidebar", self)
        self.act_sidebar.setCheckable(True)
        self.act_sidebar.setChecked(True)
        self.act_sidebar.setShortcut("F9")
        self.act_sidebar.setToolTip("Toggle sidebar (F9)")
        self.act_sidebar.triggered.connect(self._toggle_sidebar)

        self.act_dark_mode = QAction("Dark Mode", self)
        self.act_dark_mode.setCheckable(True)
        self.act_dark_mode.setShortcut("Ctrl+D")
        self.act_dark_mode.setToolTip("Toggle dark mode (Ctrl+D)")
        self.act_dark_mode.triggered.connect(self._toggle_dark_mode)

        self.act_reflow = QAction("Reflow", self)
        self.act_reflow.setCheckable(True)
        self.act_reflow.setShortcut("Ctrl+R")
        self.act_reflow.setToolTip("Reflow (Ctrl+R)")
        self.act_reflow.triggered.connect(self._toggle_reflow)

        # Navigation actions
        self.act_prev = QAction("Previous Page", self)
        self.act_prev.setShortcut("PgUp")
        self.act_prev.setToolTip("Previous page (PgUp)")
        self.act_prev.triggered.connect(self._prev_page)

        self.act_next = QAction("Next Page", self)
        self.act_next.setShortcut("PgDown")
        self.act_next.setToolTip("Next page (PgDown)")
        self.act_next.triggered.connect(self._next_page)

        self.act_first = QAction("First Page", self)
        self.act_first.setShortcut("Home")
        self.act_first.setToolTip("First page (Home)")
        self.act_first.triggered.connect(self._goto_first_page)

        self.act_last = QAction("Last Page", self)
        self.act_last.setShortcut("End")
        self.act_last.setToolTip("Last page (End)")
        self.act_last.triggered.connect(self._goto_last_page)

        # Zoom actions
        self.act_zin = QAction("Zoom In", self)
        self.act_zin.setShortcut("Ctrl++")
        self.act_zin.setToolTip("Zoom in (Ctrl++)")
        self.act_zin.triggered.connect(self._zoom_in)

        self.act_zout = QAction("Zoom Out", self)
        self.act_zout.setShortcut("Ctrl+-")
        self.act_zout.setToolTip("Zoom out (Ctrl+-)")
        self.act_zout.triggered.connect(self._zoom_out)

        self.act_zoom_100 = QAction("100%", self)
        self.act_zoom_100.setShortcut("Ctrl+0")
        self.act_zoom_100.setToolTip("Actual size (Ctrl+0)")
        self.act_zoom_100.triggered.connect(self._zoom_100)

        self.act_fit_width = QAction("Fit Width", self)
        self.act_fit_width.setShortcut("Ctrl+1")
        self.act_fit_width.setToolTip("Fit width (Ctrl+1)")
        self.act_fit_width.triggered.connect(self._fit_width)

        self.act_fit_page = QAction("Fit Page", self)
        self.act_fit_page.setShortcut("Ctrl+2")
        self.act_fit_page.setToolTip("Fit page (Ctrl+2)")
        self.act_fit_page.triggered.connect(self._fit_page)

        self.act_ctrl_wheel_step = QAction("Ctrl+Wheel Uses Zoom Step", self)
        self.act_ctrl_wheel_step.setCheckable(True)
        self.act_ctrl_wheel_step.setChecked(True)
        self.act_ctrl_wheel_step.setToolTip(
            "When checked, Ctrl+wheel zooms by the selected step; "
            "otherwise it zooms fluidly")
        self.act_ctrl_wheel_step.triggered.connect(self._toggle_ctrl_wheel_step)

        # Tools menu actions
        self.act_rotate_left = QAction("Rotate Left", self)
        self.act_rotate_left.setShortcut("Ctrl+Left")
        self.act_rotate_left.setToolTip("Rotate left (Ctrl+Left)")
        self.act_rotate_left.triggered.connect(self._rot_left)

        self.act_rotate_right = QAction("Rotate Right", self)
        self.act_rotate_right.setShortcut("Ctrl+Right")
        self.act_rotate_right.setToolTip("Rotate right (Ctrl+Right)")
        self.act_rotate_right.triggered.connect(self._rot_right)

        self.act_add_bookmark = QAction("Add Bookmark", self)
        self.act_add_bookmark.setShortcut("Ctrl+B")
        self.act_add_bookmark.setToolTip("Add bookmark at current page (Ctrl+B)")
        self.act_add_bookmark.triggered.connect(self._add_bookmark)

        self.act_highlight = QAction("Highlight", self)
        self.act_highlight.setShortcut("Ctrl+H")
        self.act_highlight.setToolTip("Create highlight from selection (Ctrl+H)")
        self.act_highlight.triggered.connect(self._create_highlight)

        self.act_next_highlight_color = QAction("Next Highlight Color", self)
        self.act_next_highlight_color.setShortcut("Ctrl+Shift+H")
        self.act_next_highlight_color.setToolTip("Cycle highlight color (Ctrl+Shift+H)")
        self.act_next_highlight_color.triggered.connect(self._cycle_highlight_color)

        self.act_save_annotations = QAction("Save Annotations", self)
        self.act_save_annotations.setToolTip("Save annotations to PDF (Ctrl+S)")
        self.act_save_annotations.triggered.connect(self._save_annotations)

        self.act_properties = QAction("Properties", self)
        self.act_properties.setShortcut("Ctrl+I")
        self.act_properties.setToolTip("Document Properties (Ctrl+I)")
        self.act_properties.triggered.connect(self._show_properties)

        self.act_attachments = QAction("Attachments", self)
        self.act_attachments.setShortcut("Ctrl+Shift+A")
        self.act_attachments.setToolTip("Show Attachments (Ctrl+Shift+A)")
        self.act_attachments.triggered.connect(self._show_attachments)

        # Settings and Help menu actions
        self.act_settings = QAction("Settings…", self)
        self.act_settings.setShortcut("Ctrl+,")
        self.act_settings.setToolTip("Configure thumbnail appearance (Ctrl+,)")
        self.act_settings.triggered.connect(self._show_settings)

        self.act_about = QAction("About", self)
        self.act_about.triggered.connect(self._show_about)

        self.act_recent = QAction("Recent ▾", self)
        self.menu_recent = QMenu("Recent files", self)
        self.act_recent.setMenu(self.menu_recent)

        self.act_find = QAction("Find", self)
        self.act_find.setShortcut(QKeySequence.StandardKey.Find)
        self.act_find.triggered.connect(self._focus_search)

    def _build_menubar(self) -> None:
        """Create the menu bar with File, Edit, View, Tools, Help menus."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")
        file_menu.addAction(self.act_new)
        file_menu.addAction(self.act_open)
        file_menu.addSeparator()
        file_menu.addAction(self.act_recent)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self.act_print)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction(self.act_copy)
        edit_menu.addAction(self.act_select_all)

        # View menu
        view_menu = menubar.addMenu("&View")
        view_menu.addAction(self.act_presentation)
        view_menu.addSeparator()
        view_menu.addAction(self.act_first)
        view_menu.addAction(self.act_prev)
        view_menu.addAction(self.act_next)
        view_menu.addAction(self.act_last)
        view_menu.addSeparator()
        view_menu.addAction(self.act_zoom_100)
        view_menu.addAction(self.act_fit_width)
        view_menu.addAction(self.act_fit_page)
        view_menu.addSeparator()
        view_menu.addAction(self.act_zin)
        view_menu.addAction(self.act_zout)
        view_menu.addAction(self.act_ctrl_wheel_step)
        view_menu.addSeparator()
        view_menu.addAction(self.act_sidebar)
        view_menu.addAction(self.act_dark_mode)
        view_menu.addAction(self.act_reflow)
        view_menu.addAction(self.act_presentation)

        # Tools menu
        tools_menu = menubar.addMenu("&Tools")
        tools_menu.addAction(self.act_find)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_highlight)
        tools_menu.addAction(self.act_next_highlight_color)
        tools_menu.addAction(self.act_add_bookmark)
        tools_menu.addAction(self.act_properties)
        tools_menu.addAction(self.act_attachments)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_rotate_left)
        tools_menu.addAction(self.act_rotate_right)
        tools_menu.addAction(self.act_save_annotations)
        tools_menu.addSeparator()
        tools_menu.addAction(self.act_settings)

        # Help menu
        help_menu = menubar.addMenu("&Help")
        help_menu.addAction(self.act_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setObjectName("mainToolbar")
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)
        self.toolbar = tb

        # Navigation
        tb.addAction(self.act_prev)
        tb.addAction(self.act_next)
        tb.addSeparator()

        # Page navigation
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.setPrefix("Page ")
        self.page_spin.setFixedWidth(96)
        self.page_spin.editingFinished.connect(self._page_spin_edited)
        tb.addWidget(self.page_spin)

        self.page_label = QLabel("/ 0")
        tb.addWidget(self.page_label)
        tb.addSeparator()

        # Zoom controls
        tb.addAction(self.act_zout)
        tb.addAction(self.act_zin)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(52)
        tb.addWidget(self.zoom_label)

        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["Fit Page", "Fit Width",
                                  "50%", "62.5%", "75%", "87.5%",
                                  "100%", "112.5%", "125%", "137.5%",
                                  "150%", "162.5%", "175%", "200%",
                                  "250%", "300%", "350%", "400%"])
        self.zoom_combo.setCurrentIndex(1)
        self.zoom_combo.setFixedWidth(104)
        self.zoom_combo.currentTextChanged.connect(self._apply_zoom_preset)
        tb.addWidget(self.zoom_combo)

        step_label = QLabel("Step")
        step_label.setToolTip("Zoom increment used by Zoom In/Out, Ctrl++/Ctrl+- "
                              "and Ctrl+wheel")
        tb.addWidget(step_label)
        self.zoom_step_combo = QComboBox()
        for step in ZOOM_STEP_PRESETS:
            self.zoom_step_combo.addItem(f"{step * 100:g}%", step)
        default_index = min(
            range(self.zoom_step_combo.count()),
            key=lambda i: abs(self.zoom_step_combo.itemData(i) - DEFAULT_ZOOM_STEP))
        self.zoom_step_combo.setCurrentIndex(default_index)
        self.zoom_step_combo.setFixedWidth(62)
        self.zoom_step_combo.setToolTip(
            "Zoom step for Zoom In/Out, Ctrl++/Ctrl+- and Ctrl+wheel")
        self.zoom_step_combo.currentIndexChanged.connect(self._apply_zoom_step)
        tb.addWidget(self.zoom_step_combo)
        tb.addSeparator()

        # Memory level
        tb.addWidget(QLabel("Memory:"))
        self.mem_combo = QComboBox()
        self.mem_combo.addItems(["Low", "Normal", "Greedy"])
        self.mem_combo.setCurrentText("Normal")
        self.mem_combo.setFixedWidth(96)
        self.mem_combo.setToolTip("Preload memory level (Okular): how much of "
                                  "the document to render ahead of the viewport")
        self.mem_combo.currentTextChanged.connect(self._apply_memory_level)
        tb.addWidget(self.mem_combo)
        tb.addSeparator()

        # Rotation
        self.act_rotl = QAction("Rotate Left", self)
        self.act_rotl.setToolTip("Rotate left")
        self.act_rotr = QAction("Rotate Right", self)
        self.act_rotr.setToolTip("Rotate right")
        self.act_rotl.triggered.connect(self._rot_left)
        self.act_rotr.triggered.connect(self._rot_right)
        tb.addAction(self.act_rotl)
        tb.addAction(self.act_rotr)
        tb.addSeparator()

        # Reflow mode
        tb.addAction(self.act_reflow)
        tb.addSeparator()

        self._set_compact_toolbar_buttons()
        self._update_zoom_actions(False)

    # --------------------------------------------------- presentation mode
    def toggle_presentation(self) -> None:
        """Enter/exit fullscreen presentation: hide all chrome, fit page."""
        if getattr(self, "_presentation", False):
            self._exit_presentation()
        else:
            self._enter_presentation()

    def _enter_presentation(self) -> None:
        if self._active_tab() is None:
            return
        self._presentation = True
        self._prev_fit_mode = self.view.fit_mode
        self.toolbar.hide()
        self.statusBar().hide()
        self.search_dock.hide()
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self._side_dock_was_visible = self.side_dock.isVisible()
            self.side_dock.hide()
        self.tabs.tabBar().hide()
        self.tabs.cornerWidget().hide()
        self.showFullScreen()
        self._set_fit("page")

    def _exit_presentation(self) -> None:
        self._presentation = False
        self.showNormal()
        self.toolbar.show()
        self.statusBar().show()
        self.search_dock.show()
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setVisible(getattr(self, "_side_dock_was_visible", True))
        self.tabs.tabBar().show()
        self.tabs.cornerWidget().show()
        if getattr(self, "_prev_fit_mode", "width"):
            self._set_fit(self._prev_fit_mode)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_presentation", False) and event.key() == Qt.Key.Key_Escape:
            self._exit_presentation()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_search_dock(self) -> None:
        dock = QDockWidget("Search", self)
        dock.setObjectName("searchDock")
        dock.setMinimumWidth(280)
        panel = QWidget()
        v = QVBoxLayout(panel)

        row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText('Words or "exact phrase"')
        self.query_edit.returnPressed.connect(self.start_search)
        row.addWidget(self.query_edit, 1)
        v.addLayout(row)

        self.mode = QComboBox()
        self.mode.addItems(["AND", "OR", "PHRASE", "REGEX"])
        self.mode.setToolTip("AND: all terms must match; OR: any term matches; PHRASE: exact phrase; REGEX: regular expression")
        v.addWidget(self.mode)

        opts = QHBoxLayout()
        self.cb_case = QCheckBox("Match case")
        self.cb_whole = QCheckBox("Whole words")
        opts.addWidget(self.cb_case)
        opts.addWidget(self.cb_whole)
        v.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self.start_search)
        self.btn_prev = QPushButton("◀")
        self.btn_next = QPushButton("▶")
        self.btn_prev.clicked.connect(self._hit_prev)
        self.btn_next.clicked.connect(self._hit_next)
        btns.addWidget(self.btn_search)
        btns.addWidget(self.btn_prev)
        btns.addWidget(self.btn_next)
        v.addLayout(btns)

        self.search_progress = QProgressBar()
        self.search_progress.setVisible(False)
        self.search_progress.setTextVisible(False)
        self.search_progress.setFixedHeight(6)
        v.addWidget(self.search_progress)

        self.results = QListWidget()
        self.results.itemClicked.connect(self._on_result_clicked)
        v.addWidget(self.results, 1)

        self.hit_label = QLabel("")
        v.addWidget(self.hit_label)

        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.search_dock = dock

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_left = QLabel("No document")
        sb.addWidget(self.status_left, 1)
        self.status_right = QLabel("")
        sb.addPermanentWidget(self.status_right)

    def _build_shortcuts(self) -> None:
        # Only keys that are not already owned by a QAction.  Registering a
        # second shortcut for the same sequence makes Qt report an ambiguous
        # shortcut and the key silently stops working.
        self._shortcut("F3", self._hit_next)
        self._shortcut("Shift+F3", self._hit_prev)
        self._shortcut("Ctrl+Shift+C", self._copy)
        self._shortcut("Ctrl+L", self._focus_search)
        self._shortcut("Ctrl+W", self._close_active_tab)
        self._shortcut("Ctrl+Tab", self._next_tab)
        self._shortcut("Ctrl+Shift+Tab", self._prev_tab)

    def _shortcut(self, sequence: str, slot) -> None:
        """Create a shortcut whose handler is a bound method (not a lambda),
        so Qt manages the connection lifetime and teardown cannot crash."""
        sc = QShortcut(QKeySequence(sequence), self)
        sc.activated.connect(slot)
        if not hasattr(self, "_shortcuts"):
            self._shortcuts = []
        self._shortcuts.append(sc)

    # ------------------------------------------------------- slot methods
    def _prev_page(self) -> None:
        self._goto(self._current_page() - 1)

    def _next_page(self) -> None:
        self._goto(self._current_page() + 1)

    def _page_spin_edited(self) -> None:
        self._goto(self.page_spin.value() - 1)

    def _zoom_in(self) -> None:
        if not self.view:
            return
        if self.view.reflow_mode:
            self.view.reflow_text_bigger()
        else:
            self.view.zoom_in()

    def _zoom_out(self) -> None:
        if not self.view:
            return
        if self.view.reflow_mode:
            self.view.reflow_text_smaller()
        else:
            self.view.zoom_out()

    def _zoom_100(self) -> None:
        if self.view and self.view.reflow_mode:
            self.view.set_reflow_font_scale(1.0)
        else:
            self._set_zoom(1.0)

    def _fit_page(self) -> None:
        self._set_fit("page")

    def _fit_width(self) -> None:
        self._set_fit("width")

    def _copy(self) -> None:
        if self.view:
            self.view.copy_selection()

    def _rot_left(self) -> None:
        self._rotate(-90)

    def _rot_right(self) -> None:
        self._rotate(90)

    def _hit_prev(self) -> None:
        self._step_hit(-1)

    def _hit_next(self) -> None:
        self._step_hit(+1)

    def _add_bookmark(self) -> None:
        """Add a bookmark at the current page."""
        if not self.view or not self.sidebar:
            return
        page = self.view.current_page
        self.sidebar.add_bookmark_at_current_page(page)

    def _create_highlight(self) -> None:
        """Create a highlight from the current selection."""
        if self.view and self.view.has_selection():
            self.view.create_highlight_from_selection()

    def _cycle_highlight_color(self) -> None:
        """Cycle to the next highlight color."""
        if self.view:
            color = self.view.next_highlight_color()
            # Update sidebar color combo if annotations tab is visible
            if self.sidebar and hasattr(self.sidebar, 'ann_color_combo'):
                for i in range(self.sidebar.ann_color_combo.count()):
                    if self.sidebar.ann_color_combo.itemData(i) == color:
                        self.sidebar.ann_color_combo.setCurrentIndex(i)
                        break

    def _save_annotations(self) -> None:
        """Save all annotations to the PDF file."""
        if self.view:
            success = self.view.save_annotations()
            if success:
                self.status_left.setText("Annotations saved to PDF")
            else:
                self.status_left.setText("Failed to save annotations")

    def _save_document(self) -> None:
        """Save the document (annotations, bookmarks, etc.)"""
        if self.view:
            success = self.view.save_annotations()
            if success:
                self.status_left.setText("Document saved")
            else:
                self.status_left.setText("Failed to save document")

    def _save_document_as(self) -> None:
        """Save the document to a new location."""
        if not self.view:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Document As", "",
                                              "PDF Files (*.pdf)")
        if path:
            try:
                self.view.doc.save(path)
                self.status_left.setText(f"Document saved to {path}")
            except Exception as exc:
                QMessageBox.critical(self, "Save As", f"Could not save document:\n{exc}")

    def _print_document(self) -> None:
        """Print the document."""
        if self.view:
            QMessageBox.information(self, "Print", "Print functionality not yet implemented")

    def _select_all(self) -> None:
        """Select all text in the current page."""
        if self.view:
            self.view.select_all()

    def _goto_first_page(self) -> None:
        """Go to the first page."""
        if self.view:
            self.view.goto_page(0)

    def _goto_last_page(self) -> None:
        """Go to the last page."""
        if self.view:
            self.view.goto_page(self.view.page_count - 1)

    def _show_settings(self) -> None:
        """Show the settings dialog."""
        self._build_settings_dialog().exec()

    def _build_settings_dialog(self):
        """Build the Settings dialog (separate so it can be tested)."""
        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
        )

        current = ThumbnailSettings.load()
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setWindowIcon(QIcon(_asset_path("icon.svg")))
        dlg.setModal(True)
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        group = QGroupBox("Thumbnails", dlg)
        form = QFormLayout(group)

        size = QSpinBox(group)
        size.setRange(THUMB_MIN_WIDTH, THUMB_MAX_WIDTH)
        size.setSingleStep(8)
        size.setSuffix(" px")
        size.setValue(current.width)
        form.addRow("Page size:", size)

        border = QComboBox(group)
        for px in (1, 2, 3):
            border.addItem(f"{px} px", px)
        border.setCurrentIndex(current.border_width - 1)
        form.addRow("Border width:", border)

        shadow = QCheckBox("Draw a drop shadow behind each page", group)
        shadow.setChecked(current.shadow)
        form.addRow("Shadow:", shadow)

        layout.addWidget(group)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            dlg,
        )

        def apply() -> None:
            settings = ThumbnailSettings(
                width=size.value(),
                border_width=int(border.currentData()),
                shadow=shadow.isChecked(),
            )
            settings.save()
            self._apply_thumbnail_settings(settings)
            dlg.accept()

        buttons.accepted.connect(apply)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        return dlg

    def _apply_thumbnail_settings(self, settings: ThumbnailSettings) -> None:
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab) and getattr(tab, "sidebar", None):
                tab.sidebar.apply_thumbnail_settings(settings)

    def _on_thumbnail_size_changed(self, width: int) -> None:
        current = ThumbnailSettings.load()
        settings = ThumbnailSettings(
            width=width,
            border_width=current.border_width,
            shadow=current.shadow,
        )
        settings.save()
        self._apply_thumbnail_settings(settings)

    def _show_about(self) -> None:
        """Show the about dialog."""
        self._build_about_dialog().exec()

    def _build_about_dialog(self):
        """Build the About dialog (separate so it can be tested)."""
        from PyQt6.QtWidgets import QDialog, QDialogButtonBox

        dlg = QDialog(self)
        dlg.setWindowTitle(f"About {APP_NAME}")
        dlg.setWindowIcon(QIcon(_asset_path("icon.svg")))
        dlg.setModal(True)
        dlg.setMinimumWidth(720)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(28, 28, 28, 18)
        content = QHBoxLayout()
        content.setSpacing(26)

        icon_side = 160
        pixmap = QPixmap(icon_side, icon_side)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(_asset_path("icon.svg"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter)
        painter.end()
        icon_label = QLabel()
        icon_label.setPixmap(pixmap)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setStyleSheet("background: transparent;")
        content.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignCenter)

        link_color = "#7dd3fc" if self.act_dark_mode.isChecked() else "#2563eb"
        text = QLabel(
            f"<h2 style='margin:0'>{APP_NAME}</h2>"
            "<p style='margin:0 0 10px 0'><b>Advanced PDF Reader</b></p>"
            "<p style='margin:0 0 10px 0'>A fast, feature-rich PDF reader for "
            "Linux: continuous scrolling, text search and selection, reflow "
            "mode, annotations, and tiled rendering that stays smooth on large "
            "documents.</p>"
            "<p style='margin:0 0 10px 0'><b>Technologies used</b><br>"
            "Python 3 · PyQt6 · PyMuPDF (fitz)</p>"
            "<p style='margin:0'><b>Copyright:</b> © 2026 Washington "
            "Indacochea Delgado<br>"
            f"<b>Email:</b> <a href='mailto:linuxfrontier@proton.me' "
            f"style='color:{link_color}'>linuxfrontier@proton.me</a><br>"
            "<b>License:</b> GPL3<br>"
            "<b>Website:</b> <a "
            "href='https://github.com/wachin/PDF-Advanced-Reader' "
            f"style='color:{link_color}'>"
            "https://github.com/wachin/PDF-Advanced-Reader</a></p>"
            f"<p style='color:gray; margin:10px 0 0 0'>Version {__version__}</p>")
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setMaximumWidth(470)
        text.setOpenExternalLinks(True)
        text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction)
        content.addWidget(text, 1)

        layout.addLayout(content)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if self.act_dark_mode.isChecked():
            buttons.setStyleSheet(
                "QPushButton { background-color: #3d3d3d; color: #e0e0e0;"
                " border: 1px solid #505050; padding: 6px 18px;"
                " border-radius: 6px; }"
                "QPushButton:hover { background-color: #4a4a4a; }")
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        return dlg

    def _toggle_dark_mode(self) -> None:
        """Toggle dark mode on/off."""
        if self.view:
            dark_mode = not self.view.dark_mode
            self.view.set_dark_mode(dark_mode)
            self.act_dark_mode.setChecked(dark_mode)
            self.settings.setValue("dark_mode", dark_mode)
            # Apply to all open tabs
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if isinstance(tab, DocumentTab):
                    tab.view.set_dark_mode(dark_mode)
                    tab.sidebar.apply_icon_palette(dark_mode)
            # Apply theme to entire application
            self._apply_dark_mode(dark_mode)
            self._update_zoom_actions(self.view.reflow_mode)

    def _toggle_reflow(self) -> None:
        """Toggle reflow mode for the active document.

        Reflow is a per-document reading mode (a scanned PDF may not support
        it), unlike dark mode which is a global preference.
        """
        if self.view:
            reflow = not self.view.reflow_mode
            self.view.set_reflow_mode(reflow)
            self.act_reflow.setChecked(reflow)
            self._update_zoom_actions(reflow)
            self._update_zoom_label(self.view)
            if reflow:
                self.status_left.setText("Preparing reflow…")
            else:
                self.status_left.setText("Reflow off")
        else:
            self.act_reflow.setChecked(False)
            self._update_zoom_actions(False)

    def _on_reflow_progress(self, done: int, total: int) -> None:
        # Only the active document's progress belongs in the status bar.
        if self.sender() is not self.view or not total:
            return
        self.status_left.setText(
            f"Preparing reflow… {done}/{total} ({int(done * 100 / total)}%)")

    def _on_reflow_ready(self, has_text: bool, empty_pages: int) -> None:
        if self.sender() is not self.view:
            return
        if not has_text:
            if self.view.reflow_has_media:
                self.status_left.setText(
                    "Reflow ready — no text layer; showing page images")
            else:
                self.status_left.setText(
                    "Reflow unavailable: no extractable text (scanned PDF?)")
        elif empty_pages:
            self.status_left.setText(
                f"Reflow ready — {empty_pages} page(s) without content")
        else:
            self.status_left.setText("Reflow ready")

    def _on_reflow_font_scale_changed(self, scale: float) -> None:
        if self.sender() is not self.view:
            return
        self._update_zoom_label(self.view)
        self._update_status()

    def _show_properties(self) -> None:
        """Show document properties dialog with multiple tabs."""
        if not self.view:
            return
        path = self.view.doc_path
        try:
            with fitz.open(path) as doc:
                meta = doc.metadata or {}
                page_count = doc.page_count
                page_size = doc[0].rect if page_count else fitz.Rect()
                unique_fonts = {}
                for i in range(page_count):
                    for font in doc[i].get_fonts():
                        name = font[3] if len(font) > 3 else "Unknown"
                        ftype = font[2] if len(font) > 2 else "Unknown"
                        embedded = font[4] if len(font) > 4 else False
                        unique_fonts.setdefault((name, ftype),
                                                (name, ftype, embedded))
                permissions = doc.permissions
                is_encrypted = doc.is_encrypted
        except Exception as exc:
            QMessageBox.critical(self, "Properties", f"Could not read document:\n{exc}")
            return

        file_size = os.path.getsize(path)
        if file_size < 1024:
            size_text = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_text = f"{file_size / 1024:.1f} KB"
        elif file_size < 1024 * 1024 * 1024:
            size_text = f"{file_size / (1024 * 1024):.1f} MB"
        else:
            size_text = f"{file_size / (1024 * 1024 * 1024):.1f} GB"
        try:
            with open(path, "rb") as fh:
                file_hash = hashlib.sha256(fh.read()).hexdigest()
        except OSError as exc:
            file_hash = f"(unavailable: {exc})"

        from PyQt6.QtWidgets import (
            QDialog, QDialogButtonBox, QFormLayout, QLabel, QTabWidget,
            QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
        )

        dlg = QDialog(self)
        dlg.setWindowTitle("Document Properties")
        dlg.setMinimumSize(560, 460)
        layout = QVBoxLayout(dlg)
        tabs = QTabWidget()

        # --- General Tab ---
        general_tab = QWidget()
        general_form = QFormLayout(general_tab)
        for label, value in (
            ("Title:", meta.get("title")),
            ("Author:", meta.get("author")),
            ("Subject:", meta.get("subject")),
            ("Keywords:", meta.get("keywords")),
            ("Creator:", meta.get("creator")),
            ("Producer:", meta.get("producer")),
            ("Creation Date:", meta.get("creationDate")),
            ("Modification Date:", meta.get("modDate")),
        ):
            general_form.addRow(label, QLabel(value or "(none)"))
        general_form.addRow("Pages:", QLabel(str(page_count)))
        general_form.addRow("Page Size:", QLabel(
            f"{page_size.width:.1f} × {page_size.height:.1f} pts"))
        general_form.addRow("File Size:", QLabel(size_text))
        general_form.addRow("File:", QLabel(path))
        tabs.addTab(general_tab, "General")

        # --- Fonts Tab ---
        fonts_tab = QWidget()
        fonts_layout = QVBoxLayout(fonts_tab)
        fonts_tree = QTreeWidget()
        fonts_tree.setHeaderLabels(["Font Name", "Type", "Embedded"])
        fonts_tree.setColumnWidth(0, 220)
        fonts_tree.setColumnWidth(1, 120)
        for name, ftype, embedded in unique_fonts.values():
            item = QTreeWidgetItem([name, ftype, "Yes" if embedded else "No"])
            if not embedded:
                item.setForeground(2, Qt.GlobalColor.red)
            fonts_tree.addTopLevelItem(item)
        fonts_tree.resizeColumnToContents(0)
        fonts_tree.resizeColumnToContents(1)
        fonts_layout.addWidget(fonts_tree)
        fonts_info = QLabel(f"Total unique fonts: {len(unique_fonts)}")
        fonts_info.setStyleSheet("color: #888; font-size: 11px; padding: 4px;")
        fonts_layout.addWidget(fonts_info)
        tabs.addTab(fonts_tab, "Fonts")

        # --- Security Tab ---
        security_tab = QWidget()
        security_form = QFormLayout(security_tab)
        security_form.addRow("Encrypted:", QLabel("Yes" if is_encrypted else "No"))
        if is_encrypted:
            perms = []
            for mask, name in (
                (fitz.PDF_PERM_PRINT, "Print"),
                (fitz.PDF_PERM_MODIFY, "Modify"),
                (fitz.PDF_PERM_COPY, "Copy"),
                (fitz.PDF_PERM_ANNOTATE, "Annotate"),
            ):
                if permissions & mask:
                    perms.append(name)
            security_form.addRow("Permissions:",
                                 QLabel(", ".join(perms) if perms else "None"))
        tabs.addTab(security_tab, "Security")

        # --- File Info Tab ---
        file_tab = QWidget()
        file_form = QFormLayout(file_tab)
        file_form.addRow("File Path:", QLabel(path))
        file_form.addRow("File Size:", QLabel(size_text))
        file_form.addRow("SHA-256:", QLabel(file_hash))
        file_form.addRow("Modified:", QLabel(time.ctime(os.path.getmtime(path))))
        file_form.addRow("Created:", QLabel(time.ctime(os.path.getctime(path))))
        tabs.addTab(file_tab, "File Info")

        # --- Metadata Tab ---
        custom_tab = QWidget()
        custom_layout = QVBoxLayout(custom_tab)
        meta_tree = QTreeWidget()
        meta_tree.setHeaderLabels(["Key", "Value"])
        meta_tree.setColumnWidth(0, 160)
        for key, value in meta.items():
            if value:
                meta_tree.addTopLevelItem(QTreeWidgetItem([key, str(value)]))
        meta_tree.resizeColumnToContents(0)
        custom_layout.addWidget(QLabel("All Metadata:"))
        custom_layout.addWidget(meta_tree)
        tabs.addTab(custom_tab, "Metadata")

        layout.addWidget(tabs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)
        dlg.exec()

    def _show_attachments(self) -> None:
        """Switch sidebar to Attachments tab."""
        if not self.sidebar:
            return
        self.sidebar.tabs.setCurrentIndex(TAB_ATTACHMENTS)
        # Ensure sidebar is visible
        self.act_sidebar.setChecked(True)
        self._toggle_sidebar(True)

    def _apply_dark_mode(self, enabled: bool) -> None:
        """Apply dark mode to the entire application UI."""
        if enabled:
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #1e1e1e;
                    color: #e0e0e0;
                }
                QToolBar {
                    background-color: #2d2d2d;
                    border: none;
                    color: #e0e0e0;
                }
                QToolButton {
                    background-color: transparent;
                    color: #e0e0e0;
                    border: none;
                    padding: 4px 8px;
                }
                QToolButton:hover {
                    background-color: #3d3d3d;
                }
                QToolButton:pressed {
                    background-color: #4d4d4d;
                }
                QMenuBar {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                }
                QMenuBar::item:selected {
                    background-color: #3d3d3d;
                }
                QMenu {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                }
                QMenu::item:selected {
                    background-color: #3d3d3d;
                }
                QDockWidget {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    titlebar-close-icon: url(none);
                    titlebar-normal-icon: url(none);
                }
                QDockWidget::title {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    padding: 4px;
                }
                QTabWidget::pane {
                    border: 1px solid #3d3d3d;
                    background-color: #1e1e1e;
                }
                QTabBar::tab {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    padding: 6px 12px;
                    border: 1px solid #3d3d3d;
                }
                QTabBar::tab:selected {
                    background-color: #1e1e1e;
                    border-bottom-color: #1e1e1e;
                }
                QTabBar::tab:hover {
                    background-color: #3d3d3d;
                }
                QDockWidget > QWidget {
                    background-color: #1e1e1e;
                    color: #e0e0e0;
                }
                QListWidget, QTreeWidget {
                    background-color: #1e1e1e;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                    outline: none;
                }
                QListWidget::item:hover, QTreeWidget::item:hover {
                    background-color: #3d3d3d;
                }
                QListWidget::item:selected, QTreeWidget::item:selected {
                    background-color: #0078d7;
                    color: white;
                }
                QComboBox {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                    padding: 4px 8px;
                }
                QComboBox:hover {
                    border-color: #0078d7;
                }
                QComboBox::drop-down {
                    border: none;
                }
                QComboBox QAbstractItemView {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    selection-background-color: #0078d7;
                    border: 1px solid #3d3d3d;
                }
                QSpinBox, QLineEdit {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                    padding: 4px 8px;
                }
                QSpinBox:hover, QLineEdit:hover {
                    border-color: #0078d7;
                }
                QLabel {
                    color: #e0e0e0;
                }
                QStatusBar {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                }
                QProgressBar {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                    text-align: center;
                }
                QProgressBar::chunk {
                    background-color: #0078d7;
                }
                QScrollBar:vertical, QScrollBar:horizontal {
                    background-color: #1e1e1e;
                    border: none;
                    width: 12px;
                    height: 12px;
                }
                QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
                    background-color: #505050;
                    min-height: 20px;
                    border-radius: 6px;
                }
                QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
                    background-color: #606060;
                }
                QScrollBar::add-line, QScrollBar::sub-line {
                    background: none;
                    height: 0px;
                }
                QScrollBar::add-page, QScrollBar::sub-page {
                    background: none;
                }
                QMessageBox {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                }
                QMessageBox QLabel {
                    color: #e0e0e0;
                }
                QDialog {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                }
                QCheckBox, QRadioButton {
                    color: #e0e0e0;
                    spacing: 8px;
                }
                QCheckBox::indicator, QRadioButton::indicator {
                    width: 16px;
                    height: 16px;
                }
                QTabWidget::pane {
                    border: 1px solid #3d3d3d;
                    background-color: #1e1e1e;
                }
                QTabBar::tab {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    padding: 6px 12px;
                    border: 1px solid #3d3d3d;
                }
                QTabBar::tab:selected {
                    background-color: #1e1e1e;
                    border-bottom-color: #1e1e1e;
                }
                QTabBar::tab:hover {
                    background-color: #3d3d3d;
                }
                /* Toolbar-specific styles for dark mode */
                QToolBar::separator {
                    background-color: #3d3d3d;
                    width: 1px;
                    height: 20px;
                    margin: 4px 4px;
                }
                QToolBar::handle {
                    background-color: #3d3d3d;
                }
                /* Overflow button (the >> button when toolbar overflows) */
                QToolBar::overflow-button {
                    background-color: #2d2d2d;
                    color: #ffffff;
                    border: 1px solid #3d3d3d;
                    padding: 4px 8px;
                    border-radius: 4px;
                }
                QToolBar::overflow-button:hover {
                    background-color: #3d3d3d;
                    color: #ffffff;
                }
                QToolBar::overflow-button:pressed {
                    background-color: #4d4d4d;
                    color: #ffffff;
                }
                /* Style toolbar buttons that have menus (used for overflow) */
                QToolBar QToolButton:has-menu {
                    color: #e0e0e0;
                    border: none;
                    padding: 4px 8px;
                }
                QToolBar QToolButton:has-menu:hover {
                    background-color: #3d3d3d;
                    color: #ffffff;
                }
                QToolBar QToolButton:has-menu:pressed {
                    background-color: #4d4d4d;
                    color: #ffffff;
                }
                /* Ensure toolbar buttons don't have unwanted borders */
                QToolBar QToolButton {
                    border: none;
                    margin: 2px 2px;
                }
                /* Ensure the overflow button text is visible */
                QToolBar::overflow-button::menu-indicator {
                    image: none;
                }
            """)
        else:
            self.setStyleSheet("")

    # ---------------------------------------------------------- document
    def open_dialog(self) -> None:
        start = self.settings.value("lastDir", os.path.expanduser("~"))
        fn, _ = QFileDialog.getOpenFileName(self, "Open PDF", start, "PDF files (*.pdf)")
        if fn:
            self.open_document(fn)

    def open_document(self, path: str, password: str = "") -> bool:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Open", f"File not found:\n{path}")
            return False

        try:
            doc = fitz.open(path)
        except Exception as exc:
            QMessageBox.critical(self, "Open", f"Could not open PDF:\n{exc}")
            return False

        if doc.needs_pass:
            doc.close()
            if not password:
                password, ok = QInputDialog.getText(
                    self, "Password", "This PDF is protected.\nPassword:",
                    QLineEdit.EchoMode.Password)
                if not ok or not password:
                    return False
            return self.open_document(path, password=password)

        page_count = doc.page_count
        doc.close()

        # same path already open?  just switch to that tab
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab) and tab.doc_path == path:
                self.tabs.setCurrentIndex(i)
                return True

        try:
            tab = DocumentTab(path, password=password)
        except ValueError:
            QMessageBox.critical(self, "Open", "Wrong password.")
            return False
        except Exception as exc:
            QMessageBox.critical(self, "Open", f"Could not render PDF:\n{exc}")
            return False

        tab.view.set_zoom_step(self.zoom_step)
        tab.view.set_ctrl_wheel_step(self.ctrl_wheel_step)

        # left dock: created once, its widget follows the active tab
        if not hasattr(self, "side_dock") or self.side_dock is None:
            self.side_dock = QDockWidget("Pages", self)
            self.side_dock.setObjectName("sideDock")
            self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.side_dock)
        index = self.tabs.addTab(tab, tab.title)
        tab.view.set_memory_level(self.memory_level)
        self.tabs.setCurrentIndex(index)

        self.search_dock.widget().setVisible(True)

        self.page_spin.setMaximum(page_count)
        self.page_spin.setValue(1)
        self.page_label.setText(f"/ {page_count}")

        self._set_fit("width")
        # Restore saved view state for this document (after initial fit mode)
        self._restore_tab_state(tab.view, path)
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_NAME}")
        self.status_left.setText(f"Loaded: {path}")
        self._remember_file(path)
        self._update_status()
        tab.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    # --------------------------------------------------------- tab lifecycle
    def _connect_tab(self, tab: DocumentTab) -> None:
        tab.view.page_changed.connect(self._on_page_changed)
        tab.view.zoom_changed.connect(self._on_zoom_changed)
        tab.view.hit_changed.connect(self._on_hit_changed)
        tab.view.selection_changed.connect(self._update_status)
        tab.view.reflow_progress.connect(self._on_reflow_progress)
        tab.view.reflow_ready.connect(self._on_reflow_ready)
        tab.view.reflow_font_scale_changed.connect(self._on_reflow_font_scale_changed)
        tab.sidebar.goto_page_requested.connect(self._goto)
        tab.sidebar.thumbnail_size_changed.connect(self._on_thumbnail_size_changed)
        # Connect annotation manager to sidebar
        tab.sidebar.set_annotation_manager(tab.view.annotation_manager)
        self._connected_tab = tab

    def _disconnect_tab(self, tab: DocumentTab) -> None:
        try:
            tab.view.page_changed.disconnect(self._on_page_changed)
            tab.view.zoom_changed.disconnect(self._on_zoom_changed)
            tab.view.hit_changed.disconnect(self._on_hit_changed)
            tab.view.selection_changed.disconnect(self._update_status)
            tab.view.reflow_progress.disconnect(self._on_reflow_progress)
            tab.view.reflow_ready.disconnect(self._on_reflow_ready)
            tab.view.reflow_font_scale_changed.disconnect(self._on_reflow_font_scale_changed)
            tab.sidebar.goto_page_requested.disconnect(self._goto)
            tab.sidebar.thumbnail_size_changed.disconnect(self._on_thumbnail_size_changed)
        except TypeError:
            pass
        if self._connected_tab is tab:
            self._connected_tab = None

    def _on_tab_changed(self, index: int) -> None:
        # Save state for the previously active tab before switching
        if self._connected_tab is not None:
            self._save_tab_state(self._connected_tab)
            self._disconnect_tab(self._connected_tab)
        tab = self.tabs.widget(index)
        if not isinstance(tab, DocumentTab):
            return
        # hand the side dock to the newly active tab
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setWidget(tab.sidebar)
            tab.sidebar.show()
            self.side_dock.setVisible(self.act_sidebar.isChecked())
        self._connect_tab(tab)
        # Restore saved view state for the newly active tab (immediate for tab switching)
        self._restore_tab_state(tab.view, tab.doc_path, deferred=False)

        view = tab.view
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(view.page_count)
        self.page_spin.setValue(view.current_page + 1)
        self.page_spin.blockSignals(False)
        self.page_label.setText(f"/ {view.page_count}")
        self._update_zoom_label(view)
        self.act_reflow.setChecked(view.reflow_mode)
        self.act_dark_mode.setChecked(view.dark_mode)
        tab.sidebar.apply_icon_palette(self.act_dark_mode.isChecked())
        self._update_zoom_actions(view.reflow_mode)
        # search results belong to the previous document
        self.results.clear()
        self.hit_label.setText("")
        self.setWindowTitle(f"{os.path.basename(tab.doc_path)} — {APP_NAME}")
        self._update_status()

    def _save_tab_state(self, tab: DocumentTab) -> None:
        """Save the view state for a document tab."""
        if not tab or not tab.view:
            return
        state = tab.view.get_state()
        key = f"viewState/{tab.doc_path}"
        self.settings.setValue(key, state)

    def _restore_tab_state(self, view: 'PDFView', doc_path: str, deferred: bool = True) -> None:
        """Restore the view state for a document.
        
        Args:
            view: The PDFView to restore state for
            doc_path: The document path
            deferred: If True, defer restoration until after initial layout
                     (use True for initial open, False for tab switching)
        """
        key = f"viewState/{doc_path}"
        state = self.settings.value(key)
        if state:
            if deferred:
                # Defer restoration until after the event loop processes initial layout
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: view.restore_state(state) if view else None)
            else:
                # Immediate restoration for tab switching (layout already established)
                view.restore_state(state)

    def _close_tab(self, index: int) -> None:
        if index < 0 or index >= self.tabs.count():
            return
        tab = self.tabs.widget(index)
        # Save state before closing
        if isinstance(tab, DocumentTab):
            self._save_tab_state(tab)
        self.tabs.removeTab(index)
        if isinstance(tab, DocumentTab):
            self._disconnect_tab(tab)
            tab.shutdown()
            tab.deleteLater()
        if self.tabs.count() == 0:
            self._enter_empty_state()

    def _enter_empty_state(self) -> None:
        self.page_spin.setMaximum(1)
        self.page_spin.setValue(1)
        self.page_label.setText("/ 0")
        self.zoom_label.setText("100%")
        self.zoom_label.setToolTip("Zoom level")
        self.act_reflow.setChecked(False)
        self._update_zoom_actions(False)
        self.results.clear()
        self.hit_label.setText("")
        self.status_left.setText("No document")
        self.status_right.setText("")
        self.setWindowTitle(f"{APP_NAME} — Advanced PDF Reader")
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setVisible(False)

    def _close_document(self) -> None:
        """Close the active document tab (kept for compatibility)."""
        if self._active_tab() is not None:
            self._close_tab(self.tabs.currentIndex())

    def _close_active_tab(self) -> None:
        self._close_document()

    def _next_tab(self) -> None:
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + 1) % count)

    def _prev_tab(self) -> None:
        count = self.tabs.count()
        if count > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() - 1) % count)

    def _close_all_tabs(self) -> None:
        while self.tabs.count() > 0:
            self._close_tab(self.tabs.count() - 1)

    # ------------------------------------------------------------- zoom
    def _set_zoom(self, zoom: float) -> None:
        if self.view:
            self.view.fit_mode = "none"
            self.view.set_zoom(zoom)

    def _set_fit(self, mode: str) -> None:
        if self.view:
            self.view.set_fit_mode(mode)

    def _apply_zoom_preset(self, text: str) -> None:
        if not self.view:
            return
        if text == "Fit Page":
            self._set_fit("page")
        elif text == "Fit Width":
            self._set_fit("width")
        elif text.endswith("%"):
            self._set_zoom(float(text[:-1]) / 100.0)

    def _apply_zoom_step(self, _index: int = -1) -> None:
        step = self.zoom_step_combo.currentData()
        if step is None:
            return
        self.zoom_step = float(step)
        self.settings.setValue("zoom_step", self.zoom_step)
        self._apply_zoom_settings()

    def _toggle_ctrl_wheel_step(self, checked: bool) -> None:
        self.ctrl_wheel_step = checked
        self.settings.setValue("ctrl_wheel_zoom_step", checked)
        self._apply_zoom_settings()

    def _apply_zoom_settings(self) -> None:
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab):
                tab.view.set_zoom_step(self.zoom_step)
                tab.view.set_ctrl_wheel_step(self.ctrl_wheel_step)

    def _apply_memory_level(self, text: str) -> None:
        """Apply the chosen preload memory level to the active view and persist
        it for new documents (Okular-style)."""
        level = text.strip().lower()
        if level not in MEMORY_LEVELS:
            level = "normal"
        self.memory_level = level
        self.settings.setValue("memory_level", level)
        if self.view:
            self.view.set_memory_level(level)

    def _rotate(self, degrees: int) -> None:
        if self.view:
            self.view.rotate_by(degrees)

    # ---------------------------------------------------------- navigate
    def _current_page(self) -> int:
        return self.view.current_page if self.view else 0

    def _goto(self, page: int) -> None:
        if self.view:
            self.view.goto_page(page)
            self.page_spin.setValue(page + 1)

    def _on_page_changed(self, page: int) -> None:
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page + 1)
        self.page_spin.blockSignals(False)
        if self.sidebar is not None:
            self.sidebar.set_current_page(page)
        self._update_status()

    def _on_zoom_changed(self, zoom: float) -> None:
        self._update_zoom_label()
        self._update_status()

    def _update_zoom_label(self, view: Optional['PDFView'] = None) -> None:
        """Show the zoom factor, or the reflow text-size multiplier."""
        if view is None:
            view = self.view
        if view is not None and view.reflow_mode:
            self.zoom_label.setText(f"{int(round(view.reflow_font_scale * 100))}%")
            self.zoom_label.setToolTip("Reflow text size (Ctrl++ / Ctrl+-)")
        else:
            zoom = _format_zoom(view.zoom) if view is not None else "100%"
            self.zoom_label.setText(zoom)
            self.zoom_label.setToolTip("Zoom level")

    def _set_compact_toolbar_buttons(self) -> None:
        """Show these toolbar actions as icons only; hover shows the label."""
        toolbar = getattr(self, "toolbar", None)
        if toolbar is None:
            return
        for action in (self.act_prev, self.act_next,
                       self.act_zout, self.act_zin, self.act_reflow,
                       self.act_rotl, self.act_rotr):
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _update_zoom_actions(self, reflow: bool) -> None:
        """Keep Ctrl +/- labelled for what they actually do in this mode."""
        ink = "#e6e6e6" if self.act_dark_mode.isChecked() else "#3c3c3c"
        self.act_prev.setIcon(tinted_icon("previous_page.svg", ink))
        self.act_next.setIcon(tinted_icon("next_page.svg", ink))
        self.act_rotl.setIcon(tinted_icon("rotate_left.svg", ink))
        self.act_rotr.setIcon(tinted_icon("rotate_right.svg", ink))
        self.act_reflow.setIcon(tinted_icon("reflow.svg", ink))
        if reflow:
            self.act_zin.setIcon(tinted_icon("text_increase.svg", ink))
            self.act_zout.setIcon(tinted_icon("text_decrease.svg", ink))
            self.act_zin.setText("Increase Text Size")
            self.act_zin.setToolTip("Increase Text Size (Ctrl++)")
            self.act_zout.setText("Decrease Text Size")
            self.act_zout.setToolTip("Decrease Text Size (Ctrl+-)")
            self.act_zoom_100.setText("Default Text Size")
            self.act_zoom_100.setToolTip("Reset reflow text size (Ctrl+0)")
        else:
            self.act_zin.setIcon(tinted_icon("zoom_in.svg", ink))
            self.act_zout.setIcon(tinted_icon("zoom_out.svg", ink))
            self.act_zin.setText("Zoom In")
            self.act_zin.setToolTip("Zoom in (Ctrl++)")
            self.act_zout.setText("Zoom Out")
            self.act_zout.setToolTip("Zoom out (Ctrl+-)")
            self.act_zoom_100.setText("100%")
            self.act_zoom_100.setToolTip("Actual size (Ctrl+0)")

    def _update_status(self) -> None:
        if not self.view:
            self.status_right.setText("")
            return
        sel = self.view.selected_text()
        extra = f"   |   {len(sel)} chars selected" if sel else ""
        if self.view.reflow_mode:
            self.status_right.setText(
                f"Page {self.view.current_page + 1}/{self.view.page_count}   |   "
                f"Reflow — text {int(round(self.view.reflow_font_scale * 100))}%"
                f"{extra}")
            return
        self.status_right.setText(
            f"Page {self.view.current_page + 1}/{self.view.page_count}   |   "
            f"{_format_zoom(self.view.zoom)}   |   "
            f"{'Fit ' + self.view.fit_mode.title() if self.view.fit_mode != 'none' else 'Manual'}"
            f"{extra}")

    # ------------------------------------------------------------ search
    def _focus_search(self) -> None:
        self.search_dock.show()
        self.search_dock.raise_()
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def start_search(self) -> None:
        if not self.view or not self.doc_path:
            return
        query = normalize_space(self.query_edit.text())
        if not query:
            return
        mode = self.mode.currentText()
        params = SearchParams(
            query=query,
            mode=mode,
            case_sensitive=self.cb_case.isChecked(),
            whole_words=self.cb_whole.isChecked(),
            regex_timeout_ms=5000,
            max_regex_hits=10000,
        )

        self.results.clear()
        self.btn_search.setEnabled(False)
        self.search_progress.setVisible(True)
        self.search_progress.setRange(0, 0)

        if self.search_thread is not None and self.search_thread.isRunning():
            self.search_thread.quit()
            self.search_thread.wait(1000)

        self.search_thread = QThread(self)
        self.search_worker = SearchWorker(self.doc_path, params)
        self.search_worker.moveToThread(self.search_thread)
        self.search_thread.started.connect(self.search_worker.run)
        self.search_worker.progress.connect(self._on_search_progress)
        # bound method of a QObject => queued to the GUI thread automatically
        self.search_worker.done.connect(self._on_search_done)
        self.search_worker.done.connect(self.search_thread.quit)
        self.search_thread.start()
        # search hits belong to the document they were run on
        self._search_tab = self._active_tab()

    def _on_search_progress(self, done: int, total: int) -> None:
        self.search_progress.setRange(0, max(1, total))
        self.search_progress.setValue(done)

    def _on_search_done(self, hits: list) -> None:
        self.btn_search.setEnabled(True)
        self.search_progress.setVisible(False)

        self.results.clear()
        for hit in hits:
            item = QListWidgetItem(f"Page {hit.page + 1}: {hit.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, hit.page)
            self.results.addItem(item)

        if self._search_tab is not None and self.tabs.indexOf(self._search_tab) != -1:
            total = self._search_tab.view.set_search_hits(hits)
            if hits:
                self._step_hit(+1)
                self.status_left.setText(f"{len(hits)} page(s) matched, {total} hit(s)")
            else:
                self._search_tab.view.clear_search()
                self.status_left.setText("No results")

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and self.view:
            self.view.goto_page(page)
            self._step_hit(+1)

    def _step_hit(self, step: int) -> None:
        if self.view:
            self.view.goto_hit(step)

    def _on_hit_changed(self, current: int, total: int) -> None:
        self.hit_label.setText(f"Hit {current} / {total}" if total else "")

    # ------------------------------------------------------------- misc
    def _toggle_sidebar(self, checked: bool) -> None:
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setVisible(checked)

    def _open_recent(self) -> None:
        """Open the file attached to the menu action that sent this."""
        act = self.sender()
        path = act.data() if act is not None else None
        if isinstance(path, str) and path:
            self.open_document(path)

    def _rebuild_recent_menu(self) -> None:
        self.menu_recent.clear()
        recent = [p for p in (self.settings.value("recent", []) or []) if os.path.isfile(p)]
        if not recent:
            self.menu_recent.addAction("(empty)").setEnabled(False)
            return
        for path in recent[:10]:
            act = self.menu_recent.addAction(path)
            act.setData(path)
            act.triggered.connect(self._open_recent)

    def _remember_file(self, path: str) -> None:
        self.settings.setValue("lastDir", os.path.dirname(path))
        recent: List[str] = list(self.settings.value("recent", []) or [])
        recent = [p for p in recent if p != path]
        recent.insert(0, path)
        self.settings.setValue("recent", recent[:10])
        self._rebuild_recent_menu()

    def _restore_state(self) -> None:
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        # restore the preload memory level (Okular) and sync the toolbar combo
        lvl = self.settings.value("memory_level", "normal")
        if lvl not in MEMORY_LEVELS:
            lvl = "normal"
        self.memory_level = lvl
        self.mem_combo.setCurrentText(lvl.capitalize())
        # restore the additive zoom step and the Ctrl+wheel behaviour
        try:
            step = float(self.settings.value("zoom_step", DEFAULT_ZOOM_STEP,
                                             type=float))
        except (TypeError, ValueError):
            step = DEFAULT_ZOOM_STEP
        step = max(0.001, min(1.0, step))
        idx = min(range(self.zoom_step_combo.count()),
                  key=lambda i: abs(self.zoom_step_combo.itemData(i) - step))
        self.zoom_step = float(self.zoom_step_combo.itemData(idx))
        self.zoom_step_combo.blockSignals(True)
        self.zoom_step_combo.setCurrentIndex(idx)
        self.zoom_step_combo.blockSignals(False)
        self.ctrl_wheel_step = self.settings.value(
            "ctrl_wheel_zoom_step", True, type=bool)
        self.act_ctrl_wheel_step.setChecked(self.ctrl_wheel_step)
        # restore dark mode
        dark = self.settings.value("dark_mode", False, type=bool)
        self.act_dark_mode.setChecked(dark)
        # NOTE: we deliberately do NOT restore the Qt windowState. saveState()
        # persists dock/toolbar/tabbar visibility, and a previous session saved
        # while the UI was collapsed (e.g. presentation mode) makes the window
        # look completely empty on next launch. Only the geometry is restored;
        # the toolbar and docks start visible every run.
        self.toolbar.setVisible(True)
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setVisible(True)
        if hasattr(self, "search_dock") and self.search_dock is not None:
            self.search_dock.setVisible(True)
        self.tabs.setVisible(True)
        if hasattr(self.tabs, "tabBar"):
            self.tabs.tabBar().show()
            if self.tabs.cornerWidget() is not None:
                self.tabs.cornerWidget().show()
        self._rebuild_recent_menu()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.settings.setValue("geometry", self.saveGeometry())
        # windowState is intentionally not saved to avoid restoring a hidden UI.
        self._close_all_tabs()
        if self.search_thread is not None and self.search_thread.isRunning():
            self.search_thread.quit()
            self.search_thread.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------- drag & drop
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls() and any(
                u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.open_document(path)
        event.acceptProposedAction()


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)
    i18n = I18n(app)
    i18n.load_translations()
    app._pdfar_i18n = i18n          # keep the translator alive

    win = MainWindow()
    win.show()

    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        win.open_document(files[0])

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
