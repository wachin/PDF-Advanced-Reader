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
import html
import time

from PyQt6.QtCore import QEvent, QObject, QPoint, QSettings, QSize, QThread, QTimer, Qt
from PyQt6.QtGui import (
    QAction, QActionGroup, QCloseEvent, QColor, QDragEnterEvent,
    QDropEvent, QIcon, QKeySequence, QPainter, QPalette, QPixmap, QShortcut,
    QTextDocument,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QColorDialog, QComboBox, QDockWidget,
    QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar,
    QPushButton, QSpinBox, QStatusBar, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTabWidget, QToolBar, QToolButton, QToolTip,
    QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .annotations import (
    INK_COLORS, INK_WIDTH, INK_WIDTHS, MEASURE_DEFAULT_SCALE,
    MEASURE_KIND_LABELS, MEASURE_KINDS, MEASURE_SCALES,
    SHAPE_LABELS, STAMP_LABELS, MeasurementScale, checked_measure_scale,
)
from .bookmarks import settings_key_for
from .edits import EditError
from .i18n import I18n
from .icons import asset_path as _asset_path, tinted_icon
from .page_numbers import (
    EraseSettings, PageNumbersPanel, PageNumberSettings, erase_copy,
    erased_copy_path, numbered_copy_path, stamp_copy,
)
from .save import ProtectionError, SaveError, SaveReport, save_document
from .search import (
    SearchParams, SearchWorker, compile_regex, highlight_spans, normalize_space,
)
from .sidebar import (
    Sidebar, ThumbnailSettings, THUMB_MAX_WIDTH, THUMB_MIN_WIDTH,
    TAB_ATTACHMENTS,
)
from .tabs import DocumentTab
from .viewer import PDFView, MEMORY_LEVELS, VIEW_MODE_LABELS

ZOOM_STEP_PRESETS = (0.025, 0.05, 0.075, 0.10, 0.125,
                     0.15, 0.175, 0.20, 0.225, 0.25)
DEFAULT_ZOOM_STEP = 0.10

HIGHLIGHT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

MEASURE_KIND_TIPS = {
    "distance": "Measure the straight distance between two points",
    "perimeter": "Measure the length along a path of corners",
    "area": "Measure the area a polygon encloses",
}


def view_state_key(path: str) -> str:
    """The QSettings key under which *path*'s reading position lives.

    Canonicalized because Save As has to move the entry: without one agreed
    function, the key written at close and the key looked up at migration could
    name the same file differently and the state would be left behind.
    """
    return f"viewState/{os.path.abspath(path)}"


SEARCH_MODE_DESCRIPTIONS = (
    "AND — every word must appear on the same page.\n\n"
    "Example: \"invoice 2024\" finds pages containing both words, in any order.\n\n"
    "Example: \"budget approved\" finds a page that mentions both concepts.\n\n"
    "Use AND to narrow results with several required keywords. "
    "For exact wording or word order, use PHRASE instead.",
    "OR — any of the words may appear on the page.\n\n"
    "Example: \"invoice receipt\" finds pages with either word.\n\n"
    "Example: \"color colour\" covers both spellings.\n\n"
    "Example: \"cat dog\" finds notes about either animal.\n\n"
    "Use OR to gather pages about alternatives, synonyms or spelling variants.",
    "PHRASE — the whole query must appear as one exact phrase.\n\n"
    "Example: \"terms and conditions\" only matches that exact sequence of words.\n\n"
    "Example: \"John Smith\" keeps the first and last name together.\n\n"
    "Example: \"Figure 3\" finds that caption without pages that merely mention "
    "\"figure\" and \"3\" separately.\n\n"
    "Use PHRASE for names, titles, quotations and any wording where order matters.",
    "REGEX — match a regular expression (Python syntax).\n\n"
    "Example: \\d{4}-\\d{2}-\\d{2} finds dates such as 2026-10-26.\n\n"
    "Example: (invoice|receipt)s? finds singular or plural forms of either word.\n\n"
    "Example: colou?r finds both \"color\" and \"colour\".\n\n"
    "Use REGEX for patterns, optional parts and structured data. "
    "\"Match case\" controls case sensitivity.",
)

# Detailed, always-visible explanations for the preload memory levels shown in
# Tools > Settings.  Keys match viewer.MEMORY_LEVELS.
MEMORY_LEVEL_DESCRIPTIONS = {
    "low": (
        "Renders only the pages you can currently see and preloads nothing, "
        "so memory use stays minimal.\n\n"
        "Use it on computers with limited RAM, or for very large scanned "
        "documents you open only occasionally.\n\n"
        "Trade-off: after fast scrolling or a page jump, newly visible pages "
        "briefly show a placeholder while they render."
    ),
    "normal": (
        "Renders the visible pages first, then quietly preloads a few pages "
        "before and after the current view (about 3 behind, 8 ahead).\n\n"
        "Best for everyday reading: scrolling feels smooth because the next "
        "pages are usually ready before they appear, while memory stays "
        "bounded no matter how large the document is.\n\n"
        "Trade-off: jumping far away can still show a brief placeholder on "
        "the target page."
    ),
    "greedy": (
        "Preloads the entire document into the render cache as fast as "
        "possible, so every page is ready before you reach it.\n\n"
        "Use it for small-to-medium documents when you want the most fluid "
        "experience: scrolling, page jumps and search-result navigation all "
        "feel instant.\n\n"
        "Trade-off: memory use grows with the document size. For very large "
        "PDFs or low-RAM systems, prefer Normal or Low."
    ),
}


class DelayedItemTooltip(QObject):
    """Show a combo-box item's ToolTipRole text after the pointer rests on it."""

    def __init__(self, combo: QComboBox, delay_ms: int = 1000) -> None:
        super().__init__(combo)
        self.delay_ms = delay_ms
        self._combo = combo
        self._view = combo.view()
        self._index = -1
        self._pos = QPoint()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._show)
        self._view.setMouseTracking(True)
        self._viewport = self._view.viewport()
        self._viewport.installEventFilter(self)
        popup = self._view.window()
        if popup is not self._view:
            popup.installEventFilter(self)
        # the closed selector should explain the mode it currently shows
        self._combo.setMouseTracking(True)
        self._combo.installEventFilter(self)
        self._combo.currentIndexChanged.connect(self._reset)

    def _text(self, index: int) -> str:
        if index < 0:
            return ""
        try:
            return self._combo.itemData(index, Qt.ItemDataRole.ToolTipRole) or ""
        except RuntimeError:  # the combo is being destroyed
            return ""

    def _reset(self) -> None:
        self._index = -1
        self._timer.stop()
        QToolTip.hideText()

    def _hover(self, index: int, pos: QPoint) -> None:
        if index != self._index:
            self._index = index
            self._timer.stop()
            QToolTip.hideText()
            if self._text(index):
                self._timer.start()
        self._pos = pos

    def _show(self) -> None:
        text = self._text(self._index)
        if not text:
            return
        try:
            # the popup view is hidden while hovering the closed selector, so
            # visibility is judged by the selector itself
            visible = self._combo.isVisible()
        except RuntimeError:  # the combo is being destroyed
            return
        if visible:
            QToolTip.showText(self._pos, text, self._viewport)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        etype = event.type()
        if obj is self._viewport:
            if etype == QEvent.Type.MouseMove:
                try:
                    index = self._view.indexAt(event.position().toPoint()).row()
                except RuntimeError:  # the combo is being destroyed
                    return False
                self._hover(index, event.globalPosition().toPoint())
            elif etype in (QEvent.Type.Leave, QEvent.Type.Wheel, QEvent.Type.Hide):
                self._reset()
            elif etype == QEvent.Type.ToolTip:
                return True  # suppress the popup's built-in tooltip
        elif obj is self._combo:
            if etype == QEvent.Type.MouseMove:
                try:
                    index = self._combo.currentIndex()
                except RuntimeError:  # the combo is being destroyed
                    return False
                self._hover(index, event.globalPosition().toPoint())
            elif etype in (QEvent.Type.Leave, QEvent.Type.MouseButtonPress):
                self._reset()
            elif etype == QEvent.Type.ToolTip:
                return True  # we show the selected item's description ourselves
        elif etype == QEvent.Type.Hide:
            self._reset()
        return False



def _highlighted_html(text: str, spans, color: str) -> str:
    """HTML for *text*, with every match painted on a yellow background."""
    parts = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            parts.append(
                f'<span style="color:{color};">'
                f'{html.escape(text[cursor:start])}</span>')
        parts.append(
            '<span style="background-color:#ffeb3b; color:#202020;">'
            f'{html.escape(text[start:end])}</span>')
        cursor = end
    if cursor < len(text):
        parts.append(
            f'<span style="color:{color};">'
            f'{html.escape(text[cursor:])}</span>')
    return "".join(parts)


class _ResultHighlightDelegate(QStyledItemDelegate):
    """Draw a result row with its searched words marked in yellow."""

    def paint(self, painter, option, index):  # noqa: N802
        spans = index.data(HIGHLIGHT_ROLE)
        if not spans:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        text = opt.text
        opt.text = ""
        style.drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)

        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        role = (QPalette.ColorRole.HighlightedText if selected
                else QPalette.ColorRole.Text)
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        doc.setDefaultFont(opt.font)
        doc.setHtml(_highlighted_html(
            text, spans, opt.palette.color(role).name()))
        rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        painter.save()
        painter.translate(rect.topLeft())
        doc.drawContents(painter)
        painter.restore()


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
        self._numbering_prior_reflow = False
        self.memory_level = "normal"     # preload level applied to new views
        self.zoom_step = DEFAULT_ZOOM_STEP
        self.ctrl_wheel_step = True
        self.search_thread: Optional[QThread] = None
        self.search_worker: Optional[SearchWorker] = None
        self._search_generation = 0
        self._search_params: Optional[SearchParams] = None
        self.settings = QSettings(APP_NAME, APP_NAME)
        self._ui_dark = False
        self._tool_mode = "text"
        self._last_selection = "text"
        self._shape_kind = "rect"
        self._stamp_name = "Draft"
        self._measure_kind = "distance"
        self._measure_scale = MEASURE_DEFAULT_SCALE
        self._start_maximized = True
        self._was_maximized = False
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(500)
        self._geometry_timer.timeout.connect(self._save_geometry)

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
        self.act_save.setToolTip("Write the document back to its file")
        self.act_save.setEnabled(False)
        self.act_save.triggered.connect(self._save_document)

        self.act_save_as = QAction("Save &As…", self)
        self.act_save_as.setShortcut(QKeySequence.StandardKey.SaveAs)
        self.act_save_as.triggered.connect(self._save_document_as)

        self.act_revert = QAction("Revert to &Saved", self)
        self.act_revert.setToolTip("Discard every change and reopen the file")
        self.act_revert.setEnabled(False)
        self.act_revert.triggered.connect(self._revert_document)

        self.act_print = QAction("&Print…", self)
        self.act_print.setShortcut(QKeySequence.StandardKey.Print)
        self.act_print.triggered.connect(self._print_document)

        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)

        # Edit menu actions
        self.act_undo = QAction("&Undo", self)
        self.act_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.act_undo.setToolTip("Undo the last change to this document")
        self.act_undo.setEnabled(False)
        self.act_undo.triggered.connect(self._undo_edit)

        self.act_redo = QAction("&Redo", self)
        self.act_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.act_redo.setToolTip("Redo the last undone change")
        self.act_redo.setEnabled(False)
        self.act_redo.triggered.connect(self._redo_edit)

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

        self.act_sidebar = QAction("Pages Panel", self)
        self.act_sidebar.setCheckable(True)
        self.act_sidebar.setChecked(True)
        self.act_sidebar.setShortcut("F9")
        self.act_sidebar.setToolTip("Show or hide the Pages panel (F9)")
        self.act_sidebar.triggered.connect(self._toggle_sidebar)

        self.act_tools_panel = QAction("Tools Panel", self)
        self.act_tools_panel.setCheckable(True)
        self.act_tools_panel.setChecked(True)
        self.act_tools_panel.setToolTip(
            "Show or hide the Tools panel (Search and Page Numbers)")
        self.act_tools_panel.triggered.connect(self._toggle_tools_panel)

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

        # Mouse tools (Okular-style): explore, text, area and table selection
        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)

        self.act_explore = QAction("Explore", self)
        self.act_explore.setCheckable(True)
        self.act_explore.setToolTip("Explore: drag to pan the document")
        self.act_explore.triggered.connect(
            lambda: self._set_tool_mode("browse"))

        self.act_sel_text = QAction("Text Selection", self)
        self.act_sel_text.setCheckable(True)
        self.act_sel_text.setChecked(True)
        self.act_sel_text.setToolTip("Text Selection")
        self.act_sel_text.triggered.connect(
            lambda: self._set_tool_mode("text"))

        self.act_sel_area = QAction("Area Selection", self)
        self.act_sel_area.setCheckable(True)
        self.act_sel_area.setToolTip(
            "Area Selection: copy the text or image inside a rectangle")
        self.act_sel_area.triggered.connect(
            lambda: self._set_tool_mode("area"))

        self.act_sel_table = QAction("Table Selection", self)
        self.act_sel_table.setCheckable(True)
        self.act_sel_table.setToolTip(
            "Table Selection: copy a rectangle as tab-separated text")
        self.act_sel_table.triggered.connect(
            lambda: self._set_tool_mode("table"))

        self.act_draw = QAction("Freehand", self)
        self.act_draw.setCheckable(True)
        self.act_draw.setToolTip("Freehand: drag to draw on the page")
        self.act_draw.triggered.connect(
            lambda: self._set_tool_mode("draw"))

        # Shape tools: one action per kind, all under one toolbar button.
        self._shape_tips = {
            "rect": "drag out a rectangle",
            "ellipse": "drag out an ellipse",
            "line": "drag from one end to the other",
            "arrow": "drag from tail to head",
            "polygon": "trace the outline; it closes itself",
        }
        self._shape_actions = {}
        for kind, label in SHAPE_LABELS.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(f"{label}: {self._shape_tips[kind]}")
            action.triggered.connect(
                lambda _checked=False, k=kind: self._choose_shape(k))
            self._shape_actions[kind] = action
        self.act_shape_rect = self._shape_actions["rect"]
        self.act_shape_ellipse = self._shape_actions["ellipse"]
        self.act_shape_line = self._shape_actions["line"]
        self.act_shape_arrow = self._shape_actions["arrow"]
        self.act_shape_polygon = self._shape_actions["polygon"]

        # Stamps: the fourteen names the PDF specification defines.
        self._stamp_actions = {}
        for key, label in STAMP_LABELS.items():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(f"Stamp: {label}")
            action.triggered.connect(
                lambda _checked=False, k=key: self._choose_stamp(k))
            setattr(self, f"act_stamp_{key.lower()}", action)
            self._stamp_actions[key] = action

        # Stamps PDFAR draws itself: a typed caption or an image file.
        self._custom_stamp = None
        self._custom_stamp_actions = {}
        for kind, label, tip in (
                ("text", "Custom Text…",
                 "Type a caption, then click the page"),
                ("image", "Custom Image…",
                 "Choose an image, then click the page")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setToolTip(tip)
            action.triggered.connect(
                lambda _checked=False, k=kind: self._choose_custom_stamp(k))
            setattr(self, f"act_stamp_custom_{kind}", action)
            self._custom_stamp_actions[kind] = action

        # Measurement: a distance, perimeter or area in units the user chooses.
        self.act_measure = QAction("Measure", self)
        self.act_measure.setCheckable(True)
        self.act_measure.setToolTip(self._measure_tip(self._measure_scale))
        self.act_measure.triggered.connect(
            lambda: self._set_tool_mode("measure"))
        self._measure_kind_actions = {}
        for kind in MEASURE_KINDS:
            action = QAction(MEASURE_KIND_LABELS[kind], self)
            action.setCheckable(True)
            action.setChecked(kind == self._measure_kind)
            action.setToolTip(MEASURE_KIND_TIPS[kind])
            action.triggered.connect(
                lambda _checked=False, k=kind: self._choose_measure_kind(k))
            self._measure_kind_actions[kind] = action
        self._measure_scale_actions = {}
        for label, scale in MEASURE_SCALES:
            action = QAction(label, self)
            action.setCheckable(True)
            action.setData(scale)
            action.setToolTip(f"Measure in {label.lower()} (1 pt = "
                              f"{scale.units_per_point:g} {scale.unit})")
            action.triggered.connect(
                lambda _checked=False, s=scale: self._choose_measure_scale(s))
            self._measure_scale_actions[label] = action
        self.act_measure_custom = QAction("Custom Scale…", self)
        self.act_measure_custom.setToolTip(
            "Type how many real units one PDF point stands for")
        self.act_measure_custom.triggered.connect(self._choose_custom_scale)

        for action in (self.act_explore, self.act_sel_text,
                       self.act_sel_area, self.act_sel_table, self.act_draw,
                       *self._shape_actions.values(),
                       *self._stamp_actions.values(),
                       *self._custom_stamp_actions.values(),
                       self.act_measure):
            self._tool_group.addAction(action)

        # Page view modes (Okular-inspired).  Per document; the active view
        # is the source of truth, this window only mirrors it in the UI.
        self._view_mode = "continuous"
        self._view_group = QActionGroup(self)
        self._view_group.setExclusive(True)
        self._view_actions = {}
        for mode in ("single", "facing", "facing_centered", "overview",
                     "continuous"):
            action = QAction(VIEW_MODE_LABELS[mode], self)
            action.setCheckable(True)
            action.setToolTip(VIEW_MODE_LABELS[mode])
            action.triggered.connect(
                lambda _checked=False, m=mode: self._set_view_mode(m))
            self._view_group.addAction(action)
            self._view_actions[mode] = action
        self.act_view_single = self._view_actions["single"]
        self.act_view_facing = self._view_actions["facing"]
        self.act_view_facing_centered = self._view_actions["facing_centered"]
        self.act_view_overview = self._view_actions["overview"]
        self.act_view_continuous = self._view_actions["continuous"]
        self.act_view_continuous.setChecked(True)

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
        self.act_save_annotations.setToolTip("The same write as File ▸ Save")
        self.act_save_annotations.triggered.connect(self._save_annotations)

        self.act_properties = QAction("Properties", self)
        self.act_properties.setShortcut("Ctrl+I")
        self.act_properties.setToolTip("Document Properties (Ctrl+I)")
        self.act_properties.triggered.connect(self._show_properties)

        self.act_attachments = QAction("Attachments", self)
        self.act_attachments.setShortcut("Ctrl+Shift+A")
        self.act_attachments.setToolTip("Show Attachments (Ctrl+Shift+A)")
        self.act_attachments.triggered.connect(self._show_attachments)

        self.act_page_numbers = QAction("Page &Numbers…", self)
        self.act_page_numbers.setToolTip(
            "Number pages with a live preview and save a new copy")
        self.act_page_numbers.triggered.connect(self._show_page_numbers)

        # Settings and Help menu actions
        self.act_settings = QAction("Settings…", self)
        self.act_settings.setShortcut("Ctrl+,")
        self.act_settings.setToolTip(
            "Configure thumbnails, page preloading and window startup "
            "(Ctrl+,)")
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
        file_menu.addAction(self.act_revert)
        file_menu.addSeparator()
        file_menu.addAction(self.act_print)
        file_menu.addSeparator()
        file_menu.addAction(self.act_quit)

        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction(self.act_undo)
        edit_menu.addAction(self.act_redo)
        edit_menu.addSeparator()
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
        page_view_menu = view_menu.addMenu("Page View")
        for mode in ("single", "facing", "facing_centered", "overview",
                     "continuous"):
            page_view_menu.addAction(self._view_actions[mode])
        view_menu.addSeparator()
        view_menu.addAction(self.act_sidebar)
        view_menu.addAction(self.act_tools_panel)
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
        tools_menu.addAction(self.act_page_numbers)
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

        # Viewing modes: reflow, mouse tools and page layout
        tb.addAction(self.act_reflow)

        # Mouse tools
        tb.addAction(self.act_explore)
        self.tool_split = QToolButton(tb)
        self.tool_split.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.tool_split.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.tool_split.setAutoRaise(True)
        self.tool_split.setToolTip("Selection Tools")
        select_menu = QMenu(self.tool_split)
        select_menu.addAction(self.act_sel_text)
        select_menu.addAction(self.act_sel_area)
        select_menu.addAction(self.act_sel_table)
        select_menu.addSeparator()
        select_menu.addAction(self.act_draw)
        self.tool_split.setMenu(select_menu)
        self.tool_split.setDefaultAction(self.act_sel_text)
        tb.addWidget(self.tool_split)

        # Shapes: the button draws the kind picked from its menu.
        self.shape_split = QToolButton(tb)
        self.shape_split.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.shape_split.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.shape_split.setAutoRaise(True)
        shape_menu = QMenu(self.shape_split)
        for action in self._shape_actions.values():
            shape_menu.addAction(action)
        self.shape_split.setMenu(shape_menu)
        self.shape_split.setDefaultAction(self.act_shape_rect)
        tb.addWidget(self.shape_split)

        # Stamps: the button places the stamp picked from its menu.
        self.stamp_split = QToolButton(tb)
        self.stamp_split.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.stamp_split.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.stamp_split.setAutoRaise(True)
        stamp_menu = QMenu(self.stamp_split)
        for action in self._stamp_actions.values():
            stamp_menu.addAction(action)
        stamp_menu.addSeparator()
        for action in self._custom_stamp_actions.values():
            stamp_menu.addAction(action)
        self.stamp_split.setMenu(stamp_menu)
        self.stamp_split.setDefaultAction(self._stamp_actions["Draft"])
        tb.addWidget(self.stamp_split)

        # Measurement: the button draws a distance in the scale in its menu.
        self.measure_split = QToolButton(tb)
        self.measure_split.setPopupMode(
            QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.measure_split.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.measure_split.setAutoRaise(True)
        measure_menu = QMenu(self.measure_split)
        for action in self._measure_kind_actions.values():
            measure_menu.addAction(action)
        measure_menu.addSeparator()
        for action in self._measure_scale_actions.values():
            measure_menu.addAction(action)
        measure_menu.addSeparator()
        measure_menu.addAction(self.act_measure_custom)
        self.measure_split.setMenu(measure_menu)
        self.measure_split.setDefaultAction(self.act_measure)
        tb.addWidget(self.measure_split)

        # Page view mode menu button (icon reflects the active mode)
        self.view_mode_button = QToolButton(tb)
        self.view_mode_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.view_mode_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.view_mode_button.setAutoRaise(True)
        self.view_mode_button.setToolTip("Page View: Continuous")
        page_view_menu = QMenu(self.view_mode_button)
        for mode in ("single", "facing", "facing_centered", "overview",
                     "continuous"):
            page_view_menu.addAction(self._view_actions[mode])
        self.view_mode_button.setMenu(page_view_menu)
        tb.addWidget(self.view_mode_button)

        self._build_pen_options(tb)

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
        self._was_maximized = self.isMaximized()
        self._prev_fit_mode = self.view.fit_mode
        self.view.clear_numbering_preview()
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
        if getattr(self, "_was_maximized", False):
            self.showMaximized()
        else:
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
        self._apply_numbering_preview()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if getattr(self, "_presentation", False) and event.key() == Qt.Key.Key_Escape:
            self._exit_presentation()
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_search_dock(self) -> None:
        dock = QDockWidget("Tools", self)
        dock.setObjectName("searchDock")
        dock.setMinimumWidth(280)

        self.search_panel = QWidget()
        v = QVBoxLayout(self.search_panel)

        row = QHBoxLayout()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText('Words or "exact phrase"')
        self.query_edit.returnPressed.connect(self.start_search)
        row.addWidget(self.query_edit, 1)
        v.addLayout(row)

        self.mode = QComboBox()
        self.mode.addItems(["AND", "OR", "PHRASE", "REGEX"])
        for index, description in enumerate(SEARCH_MODE_DESCRIPTIONS):
            self.mode.setItemData(index, description, Qt.ItemDataRole.ToolTipRole)
        self._mode_tooltips = DelayedItemTooltip(self.mode)
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
        self._result_delegate = _ResultHighlightDelegate(self.results)
        self.results.setItemDelegate(self._result_delegate)
        self.results.itemClicked.connect(self._on_result_clicked)
        v.addWidget(self.results, 1)

        self.hit_label = QLabel("")
        self.hit_label.setWordWrap(True)
        v.addWidget(self.hit_label)

        self.page_numbers_panel = PageNumbersPanel(
            0, PageNumberSettings.load(), EraseSettings.load())
        self.page_numbers_panel.settings_changed.connect(
            self._on_numbering_settings)
        self.page_numbers_panel.save_requested.connect(
            self._save_numbered_copy)

        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.setTabPosition(QTabWidget.TabPosition.North)
        self.right_tabs.tabBar().setExpanding(False)
        self.right_tabs.tabBar().setStyleSheet(
            "QTabBar::tab { padding: 4px 6px; }")
        self.right_tabs.setIconSize(QSize(18, 18))
        self.right_tabs.addTab(self.search_panel, QIcon(), "")
        self.right_tabs.addTab(self.page_numbers_panel, QIcon(), "")
        for index, label in enumerate(("Search", "Page Numbers")):
            self.right_tabs.setTabToolTip(index, label)
            self.right_tabs.tabBar().setAccessibleTabName(index, label)
        self.right_tabs.currentChanged.connect(self._on_right_tab_changed)

        dock.setWidget(self.right_tabs)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.search_dock = dock
        # Closing the dock with its X must update the View menu check mark.
        dock.visibilityChanged.connect(self.act_tools_panel.setChecked)
        self._apply_right_tab_icons()

    def _apply_right_tab_icons(self) -> None:
        """Tint the right dock tabs to match the theme actually on screen."""
        if not hasattr(self, "right_tabs"):
            return
        ink = "#e6e6e6" if self._ui_dark else "#3c3c3c"
        self.right_tabs.setTabIcon(0, tinted_icon("search.svg", ink))
        self.right_tabs.setTabIcon(
            1, tinted_icon("page_numbers.svg", ink))

    def _numbering_panel_active(self) -> bool:
        return (hasattr(self, "right_tabs")
                and self.right_tabs.currentWidget() is self.page_numbers_panel)

    def _apply_numbering_preview(self) -> None:
        """Show the preview only while the Page Numbers tab is selected."""
        view = self.view
        if view is None or not hasattr(self, "page_numbers_panel"):
            return
        self.page_numbers_panel.set_page_count(view.page_count)
        if self._numbering_panel_active() and not view.reflow_mode:
            view.set_numbering_preview(
                self.page_numbers_panel.preview_settings())
        else:
            view.clear_numbering_preview()

    def _on_numbering_settings(self, settings) -> None:
        view = self.view
        if view is None or not self._numbering_panel_active():
            return
        if not view.reflow_mode:
            view.set_numbering_preview(settings)

    def _on_numbering_dragged(self, settings) -> None:
        """Reflect an in-page drag back into the matching control page."""
        if isinstance(settings, EraseSettings):
            self.page_numbers_panel.apply_erase_settings(settings)
        else:
            self.page_numbers_panel.apply_settings(settings)

    def _on_right_tab_changed(self, index: int) -> None:
        del index
        active = self._numbering_panel_active()
        view = self.view
        if active and view is not None and view.reflow_mode:
            self._numbering_prior_reflow = True
            view.set_reflow_mode(False)
            self.act_reflow.setChecked(False)
            self._update_zoom_actions(False)
            self._update_zoom_label(view)
        elif (not active and self._numbering_prior_reflow
              and view is not None):
            self._numbering_prior_reflow = False
            if not view.reflow_mode:
                view.set_reflow_mode(True)
                self.act_reflow.setChecked(True)
                self._update_zoom_actions(True)
                self._update_zoom_label(view)
        self._apply_numbering_preview()

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
        """Save the highlights to the PDF file — the same write as Save."""
        tab = self._active_tab()
        if tab is not None and self._save_tab(tab):
            self.status_left.setText("Annotations saved to PDF")

    def _save_document(self) -> None:
        """Ctrl+S: write the document back to the file it came from."""
        tab = self._active_tab()
        if tab is not None:
            self._save_tab(tab)

    def _save_tab(self, tab: DocumentTab) -> bool:
        """Write *tab*'s document back to its own file."""
        return self._write_tab(tab)

    def _write_tab(self, tab: DocumentTab, path: Optional[str] = None, *,
                   full: bool = False, before: str = "") -> bool:
        """The one place a document reaches the disk.

        There is nothing to prepare: the annotations are in the document already
        (F10-3), so the writer saves exactly what the user is looking at.
        """
        session = tab.view.session
        title = "Save" if path is None else "Save As"
        try:
            report = save_document(session, path, full=full)
        except ProtectionError as exc:
            return self._save_with_new_owner_password(tab, str(exc), path, full, before)
        except SaveError as exc:
            QMessageBox.critical(self, title, str(exc))
            self._on_document_changed(tab)
            return False
        return self._finish_save(tab, report, before)

    def _save_with_new_owner_password(self, tab: DocumentTab, reason: str,
                                      path: Optional[str], full: bool,
                                      before: str) -> bool:
        """Ask before writing a copy whose protection is weaker than the original.

        PDFAR never saw the password that guards changes to this file, so it
        cannot rebuild one; replacing it with a fresh random secret is the user's
        decision, not this function's.
        """
        answer = QMessageBox.question(
            self, "Protected document",
            f"{reason}\n\nSave it with a new owner password?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Save:
            return False
        try:
            report = save_document(tab.view.session, path, full=full,
                                   allow_weakened=True)
        except SaveError as exc:
            QMessageBox.critical(self, "Save", str(exc))
            self._on_document_changed(tab)
            return False
        return self._finish_save(tab, report, before)

    def _finish_save(self, tab: DocumentTab, report: Optional[SaveReport],
                     before: str = "") -> bool:
        """One place decides what a completed save says and what it clears."""
        if report is not None and before:
            self._migrate_document_state(before, report.path)
        self._on_document_changed(tab)
        if report is None:
            self.status_left.setText("Nothing to save")
            return True
        size = report.size / 1024
        how = "appended to" if report.incremental else "written"
        self.status_left.setText(
            f"{os.path.basename(report.path)} {how} — {size:.0f} KB")
        return True

    def _save_document_as(self) -> None:
        """Save the document to a new location."""
        tab = self._active_tab()
        if tab is not None:
            self._save_tab_as(tab)

    def _save_tab_as(self, tab: DocumentTab) -> bool:
        """Write *tab*'s document to a file the user picks, and move its state.

        Save As is always a full rewrite: a copy that still carried the previous
        revision in an appended trailer would keep deleted content in a file the
        user believes they have cleaned.  ``before`` is the path as it was on
        entry, because the write retargets the session and the state keys have to
        be moved from the name the document used to have.
        """
        path, _ = QFileDialog.getSaveFileName(self, "Save Document As", tab.doc_path,
                                              "PDF Files (*.pdf)")
        if not path:
            return False
        return self._write_tab(tab, path, full=True, before=tab.doc_path)

    def _migrate_document_state(self, old_path: str, new_path: str) -> None:
        """Carry the per-document settings over a rename.

        Both the saved view state and the user bookmarks are keyed by path, so
        without this the document silently loses its reading position and its
        bookmarks the first time it is saved under a new name.
        """
        if not old_path:
            return
        pairs = [(view_state_key(old_path), view_state_key(new_path)),
                 (settings_key_for(old_path), settings_key_for(new_path))]
        for old_key, new_key in pairs:
            if old_key == new_key or not self.settings.contains(old_key):
                continue
            self.settings.setValue(new_key, self.settings.value(old_key))
            self.settings.remove(old_key)

    def _revert_document(self) -> None:
        """Revert the current document to the file on disk."""
        tab = self._active_tab()
        if tab is not None:
            self._revert_tab(tab)

    def _revert_tab(self, tab: DocumentTab) -> bool:
        """Discard every unsaved change and read the file again."""
        session = tab.view.session
        if not tab.dirty:
            self.status_left.setText("There are no unsaved changes to revert")
            return True
        lost = len(session.history)
        answer = QMessageBox.question(
            self, "Revert document",
            f"Discard the changes in “{tab.title}” and reopen the file as it is "
            f"on disk?\n{lost} change{'s' if lost != 1 else ''} will be lost.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return False
        try:
            _version, scope = session.revert()
        except SaveError as exc:
            QMessageBox.critical(self, "Revert", str(exc))
            return False
        # Revert hands back a new document and ``invalidate`` re-reads the
        # annotation list from it, so a highlight the user never saved goes away
        # with the file instead of going back in on the next Save.
        tab.view.invalidate(scope)
        self._on_document_changed(tab)
        self.status_left.setText(f"Reverted to the file on disk: {session.path}")
        return True

    def _show_page_numbers(self) -> None:
        """Reveal the Page Numbers tab with its live in-page preview."""
        if not self.view:
            return
        self.search_dock.show()
        self.search_dock.raise_()
        self.right_tabs.setCurrentWidget(self.page_numbers_panel)

    def _save_numbered_copy(self) -> None:
        """Stamp the panel's current settings into a new PDF copy."""
        view = self.view
        if not view:
            return
        if self.page_numbers_panel.erase_active():
            self._save_erased_copy()
            return

        settings = self.page_numbers_panel.settings()
        source_path = os.path.realpath(view.doc_path)
        default_path = numbered_copy_path(view.doc_path)
        while True:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Numbered Copy", default_path,
                "PDF Files (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            if os.path.realpath(path) != source_path:
                break
            QMessageBox.warning(
                self, "Page Numbers",
                "Choose a different file name: the numbered copy never "
                "overwrites the document you have open.")
            default_path = numbered_copy_path(view.doc_path)
        path = os.path.abspath(path)

        self.status_left.setText("Saving numbered copy…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            numbered = stamp_copy(view.doc, path, settings)
        except Exception as exc:
            error = exc
        else:
            error = None
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            self.status_left.setText("Could not save numbered copy")
            QMessageBox.critical(
                self, "Page Numbers",
                f"Could not save the numbered copy:\n{error}")
            return

        settings.save()
        self.status_left.setText(f"Saved numbered copy: {path}")
        open_copy = QMessageBox.question(
            self, "Page Numbers",
            f"Added numbers to {numbered} page(s).\n\nSaved to:\n{path}\n\n"
            "Open the new copy?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if open_copy == QMessageBox.StandardButton.Yes:
            self.open_document(path)

    def _save_erased_copy(self) -> None:
        """Cover old page numbers with blank masks in a new PDF copy."""
        view = self.view
        if not view:
            return

        settings = self.page_numbers_panel.erase_settings()
        source_path = os.path.realpath(view.doc_path)
        default_path = erased_copy_path(view.doc_path)
        while True:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Erased Copy", default_path,
                "PDF Files (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            if os.path.realpath(path) != source_path:
                break
            QMessageBox.warning(
                self, "Page Numbers",
                "Choose a different file name: the erased copy never "
                "overwrites the document you have open.")
            default_path = erased_copy_path(view.doc_path)
        path = os.path.abspath(path)

        self.status_left.setText("Saving erased copy…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            erased = erase_copy(view.doc, path, settings)
        except Exception as exc:
            error = exc
        else:
            error = None
        finally:
            QApplication.restoreOverrideCursor()
        if error is not None:
            self.status_left.setText("Could not save erased copy")
            QMessageBox.critical(
                self, "Page Numbers",
                f"Could not save the erased copy:\n{error}")
            return

        settings.save()
        self.status_left.setText(f"Saved erased copy: {path}")
        open_copy = QMessageBox.question(
            self, "Page Numbers",
            f"Covered {erased} page(s) with the erase mask.\n\n"
            f"Saved to:\n{path}\n\nOpen the new copy?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if open_copy == QMessageBox.StandardButton.Yes:
            self.open_document(path)

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
            QDialog, QDialogButtonBox, QFormLayout, QRadioButton, QSizePolicy,
            QTabWidget,
        )

        current = ThumbnailSettings.load()
        dlg = QDialog(self)
        dlg.setWindowTitle("Settings")
        dlg.setWindowIcon(QIcon(_asset_path("icon.svg")))
        dlg.setModal(True)
        dlg.setMinimumWidth(420)

        layout = QVBoxLayout(dlg)
        tabs = QTabWidget(dlg)

        # --- Thumbnails tab ---
        thumbs_tab = QWidget()
        form = QFormLayout(thumbs_tab)

        size = QSpinBox(thumbs_tab)
        size.setRange(THUMB_MIN_WIDTH, THUMB_MAX_WIDTH)
        size.setSingleStep(8)
        size.setSuffix(" px")
        size.setValue(current.width)
        form.addRow("Page size:", size)

        border = QComboBox(thumbs_tab)
        for px in (1, 2, 3):
            border.addItem(f"{px} px", px)
        border.setCurrentIndex(current.border_width - 1)
        form.addRow("Border width:", border)

        shadow = QCheckBox("Draw a drop shadow behind each page", thumbs_tab)
        shadow.setObjectName("thumbnailShadow")
        shadow.setChecked(current.shadow)
        form.addRow("Shadow:", shadow)

        tabs.addTab(thumbs_tab, "Thumbnails")

        # --- Page preloading tab (Okular memory levels) ---
        # Radio buttons with the detailed description always visible under
        # each option.
        mem_tab = QWidget()
        mem_layout = QVBoxLayout(mem_tab)
        mem_radios: dict = {}
        for level, title in (("low", "Low"),
                             ("normal", "Normal (recommended)"),
                             ("greedy", "Greedy")):
            radio = QRadioButton(title, mem_tab)
            radio.setObjectName(f"memory{level.capitalize()}")
            radio.setChecked(self.memory_level == level)
            desc = QLabel(MEMORY_LEVEL_DESCRIPTIONS[level], mem_tab)
            desc.setWordWrap(True)
            # sizeHint is the floor: the layout must not shrink the wrapped
            # description below its full height (clips the last paragraph).
            desc.setSizePolicy(QSizePolicy.Policy.Preferred,
                               QSizePolicy.Policy.Minimum)
            desc.setContentsMargins(24, 0, 8, 6)
            mem_layout.addWidget(radio)
            mem_layout.addWidget(desc)
            mem_radios[level] = radio
        mem_layout.addStretch(1)
        tabs.addTab(mem_tab, "Page preloading")

        # --- Window tab ---
        window_tab = QWidget()
        window_form = QFormLayout(window_tab)
        start_maximized = QCheckBox("Open the window maximized", window_tab)
        start_maximized.setObjectName("windowStartMaximized")
        start_maximized.setChecked(
            self.settings.value("window_maximized", True, type=bool))
        window_form.addRow("Startup:", start_maximized)
        remember = QLabel(
            "When this is off, PDFAR reopens at the size and position you "
            "last used. Resize or move the window to change them.",
            window_tab)
        remember.setWordWrap(True)
        window_form.addRow(remember)
        tabs.addTab(window_tab, "Window")

        layout.addWidget(tabs)

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
            self._apply_window_preference(start_maximized.isChecked())
            level = next((lvl for lvl, radio in mem_radios.items()
                          if radio.isChecked()), "normal")
            self._set_memory_level(level)
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
            "Python 3 · PyQt6 · PyMuPDF (fitz)<br>"
            "Page view modes inspired by KDE Okular (GPL-2.0+/3.0+)</p>"
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
                if hasattr(self, "page_numbers_panel"):
                    self.view.clear_numbering_preview()
            else:
                self.status_left.setText("Reflow off")
                self._apply_numbering_preview()
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
            # Read through the session: while nothing is edited this is the file
            # on disk, afterwards it is the in-memory content being shown.
            with self.view.session.open_copy() as doc:
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
        self._ui_dark = enabled
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
                QSpinBox, QDoubleSpinBox, QLineEdit {
                    background-color: #2d2d2d;
                    color: #e0e0e0;
                    border: 1px solid #3d3d3d;
                    padding: 4px 8px;
                }
                QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
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
                QToolBar QToolButton:checked {
                    background-color: #4a4a4a;
                    border: 1px solid #6a6a6a;
                    border-radius: 4px;
                }
                /* Ensure the overflow button text is visible */
                QToolBar::overflow-button::menu-indicator {
                    image: none;
                }
            """)
        else:
            self.setStyleSheet("")
        self._apply_right_tab_icons()

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
            # Closing the dock with its X must update the View menu check mark.
            self.side_dock.visibilityChanged.connect(
                self.act_sidebar.setChecked)
        index = self.tabs.addTab(tab, tab.title)
        tab.document_changed.connect(self._on_document_changed)
        tab.view.set_memory_level(self.memory_level)
        self.tabs.setCurrentIndex(index)

        self.search_dock.widget().setVisible(True)

        self.page_spin.setMaximum(page_count)
        self.page_spin.setValue(1)
        self.page_label.setText(f"/ {page_count}")

        self._set_fit("width")
        # Restore saved view state for this document (after initial fit mode)
        self._restore_tab_state(tab.view, path)
        self._set_window_title(tab)
        self._refresh_history_actions()
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
        tab.view.numbering_changed.connect(self._on_numbering_dragged)
        tab.view.transient_message.connect(self._show_transient_message)
        tab.view.view_mode_changed.connect(self._on_view_mode_changed)
        tab.view.content_changed.connect(tab.sidebar.invalidate_thumbnails)
        tab.sidebar.goto_page_requested.connect(self._goto)
        # A comment row belongs to its own view: the id means nothing to a tab
        # that is not showing the document it came from.
        tab.sidebar.annotation_activated.connect(tab.view.select_annotation)
        tab.sidebar.thumbnail_size_changed.connect(self._on_thumbnail_size_changed)
        tab.sidebar.save_requested.connect(self._save_annotations)
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
            tab.view.numbering_changed.disconnect(self._on_numbering_dragged)
            tab.view.transient_message.disconnect(self._show_transient_message)
            tab.view.view_mode_changed.disconnect(self._on_view_mode_changed)
            tab.view.content_changed.disconnect(tab.sidebar.invalidate_thumbnails)
            tab.sidebar.goto_page_requested.disconnect(self._goto)
            tab.sidebar.annotation_activated.disconnect(tab.view.select_annotation)
            tab.sidebar.thumbnail_size_changed.disconnect(self._on_thumbnail_size_changed)
            tab.sidebar.save_requested.disconnect(self._save_annotations)
        except TypeError:
            pass
        tab.view.clear_numbering_preview()
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
        self._numbering_prior_reflow = False
        if self._numbering_panel_active() and view.reflow_mode:
            self._numbering_prior_reflow = True
            view.set_reflow_mode(False)
            self.act_reflow.setChecked(False)
        self._update_zoom_actions(view.reflow_mode)
        self._update_zoom_label(view)
        self._apply_numbering_preview()
        # search results belong to the previous document
        self._cancel_search()
        self.results.clear()
        self.hit_label.setText("")
        self.setWindowTitle(f"{os.path.basename(tab.doc_path)}[*] — {APP_NAME}")
        self.setWindowModified(tab.dirty)
        self._refresh_history_actions()
        self._update_status()

    # ------------------------------------------------------- document changes
    def _set_window_title(self, tab: Optional[DocumentTab]) -> None:
        """Title bar for *tab*.  ``[*]`` is where Qt puts the modified marker,
        which follows ``setWindowModified`` — so the asterisk cannot lie."""
        if tab is None:
            self.setWindowModified(False)
            self.setWindowTitle(f"{APP_NAME} — Advanced PDF Reader")
            return
        self.setWindowTitle(f"{os.path.basename(tab.doc_path)}[*] — {APP_NAME}")
        self.setWindowModified(tab.dirty)

    def _on_document_changed(self, tab: DocumentTab) -> None:
        """Something was edited, undone or redone: refresh what derives from it."""
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, tab.display_title)
        active = self._active_tab()
        if tab is active:
            self._set_window_title(tab)
        self._refresh_history_actions()

    def _refresh_history_actions(self) -> None:
        """Enable Undo/Redo and name them after the step they would perform."""
        tab = self._active_tab()
        session = tab.view.session if tab is not None else None
        history = session.history if session is not None else None
        for action, verb, label in (
            (self.act_undo, "&Undo",
             history.undo_label if history is not None else None),
            (self.act_redo, "&Redo",
             history.redo_label if history is not None else None),
        ):
            # A label exists exactly when there is a step of that direction:
            # ``EditHistory.__len__`` counts the undo stack, so never test the
            # object itself for truthiness — an empty stack is falsy.
            action.setEnabled(label is not None)
            # A label is document text: an "&" in it must not become a mnemonic.
            action.setText(f"{verb} {label.replace('&', '&&')}" if label else verb)
        # Not gated on dirty, unlike Revert: a document that matches its file can
        # still be asked for a copy of itself under a new name, and Ctrl+S on it
        # has a truthful answer of its own ("Nothing to save") rather than a
        # greyed-out menu item the user has to reason about.
        self.act_save.setEnabled(tab is not None)
        self.act_save_as.setEnabled(tab is not None)
        self.act_revert.setEnabled(session is not None and session.dirty)

    def _undo_edit(self) -> None:
        tab = self._active_tab()
        if tab is not None:
            tab.view.undo_edit()

    def _redo_edit(self) -> None:
        tab = self._active_tab()
        if tab is not None:
            tab.view.redo_edit()

    def _confirm_close(self, tab: DocumentTab) -> bool:
        """Ask before throwing away edits that are not in any file.

        ``Save`` writes to the document's own file, which is what the user is
        being promised: since F10-2 a close no longer has to divert through a
        file chooser to be honest about where the bytes go.  A refused save keeps
        the tab open — losing the work here would be the whole point of asking.
        """
        if not tab.dirty:
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes",
            f"“{tab.title}” has changes that are not in the file.\n"
            "Save them before closing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Save:
            return self._save_tab(tab)
        return True

    def _confirm_discard_all(self) -> bool:
        """Ask about each document with unsaved edits, in tab order."""
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab) and not self._confirm_close(tab):
                return False
        return True

    def _save_tab_state(self, tab: DocumentTab) -> None:
        """Save the view state for a document tab."""
        if not tab or not tab.view:
            return
        state = tab.view.get_state()
        self.settings.setValue(view_state_key(tab.doc_path), state)

    def _restore_tab_state(self, view: 'PDFView', doc_path: str, deferred: bool = True) -> None:
        """Restore the view state for a document.
        
        Args:
            view: The PDFView to restore state for
            doc_path: The document path
            deferred: If True, defer restoration until after initial layout
                     (use True for initial open, False for tab switching)
        """
        key = view_state_key(doc_path)
        state = self.settings.value(key)
        if state:
            if deferred:
                # Defer restoration until after the event loop processes initial layout
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(0, lambda: view.restore_state(state) if view else None)
            else:
                # Immediate restoration for tab switching (layout already established)
                view.restore_state(state)

    def _close_tab(self, index: int, confirm: bool = True) -> bool:
        """Close a tab.  False means the user chose to keep it open."""
        if index < 0 or index >= self.tabs.count():
            return False
        tab = self.tabs.widget(index)
        # Save state before closing
        if isinstance(tab, DocumentTab):
            if confirm and not self._confirm_close(tab):
                return False
            self._save_tab_state(tab)
        self.tabs.removeTab(index)
        if isinstance(tab, DocumentTab):
            self._disconnect_tab(tab)
            tab.shutdown()
            tab.deleteLater()
        if self.tabs.count() == 0:
            self._enter_empty_state()
        return True

    def _enter_empty_state(self) -> None:
        self._cancel_search()
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
        self._set_window_title(None)
        self._refresh_history_actions()
        self._numbering_prior_reflow = False
        if hasattr(self, "page_numbers_panel"):
            self.page_numbers_panel.set_page_count(0)
        if hasattr(self, "side_dock") and self.side_dock is not None:
            # Hide it because there is no document, but keep the user's
            # Pages Panel preference for the next document.
            self.side_dock.blockSignals(True)
            self.side_dock.setVisible(False)
            self.side_dock.blockSignals(False)

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

    def _close_all_tabs(self, confirm: bool = True) -> bool:
        """Close every tab.  False when the user stopped us to keep working."""
        while self.tabs.count() > 0:
            if not self._close_tab(self.tabs.count() - 1, confirm):
                return False
        return True

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

    def _set_memory_level(self, level: str) -> None:
        """Apply the chosen preload memory level (Okular-style) to every open
        document, persist it, and inherit it in newly opened documents."""
        if level not in MEMORY_LEVELS:
            level = "normal"
        self.memory_level = level
        self.settings.setValue("memory_level", level)
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if isinstance(tab, DocumentTab) and tab.view:
                tab.view.set_memory_level(level)

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

    def _tool_action(self, mode: str) -> QAction:
        if mode == "shape":
            return self._shape_actions[self._shape_kind]
        if mode == "stamp":
            if self._custom_stamp is not None:
                return self._custom_stamp_actions[self._custom_stamp["kind"]]
            return self._stamp_actions[self._stamp_name]
        return {
            "browse": self.act_explore,
            "text": self.act_sel_text,
            "area": self.act_sel_area,
            "table": self.act_sel_table,
            "draw": self.act_draw,
            "measure": self.act_measure,
        }[mode]

    def _measure_tip(self, scale: MeasurementScale) -> str:
        """What the measure tool will draw and read in, and how."""
        label = MEASURE_KIND_LABELS[self._measure_kind]
        how = ("drag from one end to the other"
               if self._measure_kind == "distance"
               else "click each point, then double-click or press Enter")
        return (f"Measure {label.lower()}: 1 pt = "
                f"{scale.units_per_point:g} {scale.unit} — {how}")

    def _set_tool_mode(self, mode: str) -> None:
        """Activate a mouse tool and apply it to the current view."""
        if mode not in ("browse", "text", "area", "table", "draw", "shape",
                        "stamp", "measure"):
            return
        view = self.view
        if (view is not None and view.reflow_mode
                and mode in ("area", "table", "draw", "shape", "stamp",
                             "measure")):
            mode = "text"
        self._tool_mode = mode
        action = self._tool_action(mode)
        action.setChecked(True)
        if mode != "browse":
            self._last_selection = mode
            if mode == "shape":
                self.shape_split.setDefaultAction(action)
            elif mode == "stamp":
                self.stamp_split.setDefaultAction(action)
            elif mode == "measure":
                self.measure_split.setDefaultAction(action)
            else:
                self.tool_split.setDefaultAction(action)
        if view is not None:
            view.set_shape_kind(self._shape_kind)
            if mode == "stamp":
                if self._custom_stamp is not None:
                    view.set_custom_stamp(**self._custom_stamp)
                else:
                    view.set_stamp_name(self._stamp_name)
            elif mode == "measure":
                view.set_measure_kind(self._measure_kind)
                view.set_measure_scale(self._measure_scale)
            view.set_tool_mode(mode)
        self._sync_pen_options()

    def _choose_shape(self, kind: str) -> None:
        """Pick the shape the button draws and switch to that tool."""
        if kind not in self._shape_actions:
            return
        self._shape_kind = kind
        action = self._shape_actions[kind]
        action.setChecked(True)
        self.shape_split.setDefaultAction(action)
        self._set_tool_mode("shape")

    def _choose_stamp(self, name: str) -> None:
        """Pick the stamp the button places and switch to that tool."""
        if name not in self._stamp_actions:
            return
        self._stamp_name = name
        self._custom_stamp = None
        action = self._stamp_actions[name]
        action.setChecked(True)
        self.stamp_split.setDefaultAction(action)
        self._set_tool_mode("stamp")

    def _custom_stamp_tip(self, definition: dict) -> str:
        """The split button's tooltip for a custom stamp."""
        if definition.get("kind") == "text":
            return f"Custom stamp: {definition.get('caption', '')}"
        return f"Custom stamp: {os.path.basename(definition.get('image', ''))}"

    def _set_custom_stamp(self, definition: dict) -> None:
        """Arm the stamp tool with a definition the user just gave."""
        kind = definition.get("kind")
        if kind not in self._custom_stamp_actions:
            return
        self._custom_stamp = {"kind": kind,
                              "caption": definition.get("caption", ""),
                              "image": definition.get("image", "")}
        self._stamp_name = "Custom"
        action = self._custom_stamp_actions[kind]
        action.setToolTip(self._custom_stamp_tip(self._custom_stamp))
        action.setChecked(True)
        self.stamp_split.setDefaultAction(action)
        self._set_tool_mode("stamp")
        self.statusBar().showMessage(
            "Custom stamp ready — click the page to place it", 5000)

    def _choose_custom_stamp(self, kind: str) -> None:
        """Ask for a caption or an image and arm the stamp tool with it."""
        previous = (self._tool_action(self._tool_mode)
                    if self._tool_mode == "stamp"
                    else self._stamp_actions.get(self._stamp_name,
                                                 self._stamp_actions["Draft"]))

        def cancel() -> None:
            # The action was checked when the menu item fired; put the check
            # back where it was so the menu still says what the tool places.
            previous.setChecked(True)
            self.stamp_split.setDefaultAction(previous)

        if kind == "text":
            current = (self._custom_stamp or {}).get("caption", "")
            caption, ok = QInputDialog.getText(
                self, "Custom Stamp", "Stamp text:", text=current)
            if not ok or not caption.strip():
                cancel()
                return
            self._set_custom_stamp({"kind": "text",
                                    "caption": caption.strip(), "image": ""})
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Custom Stamp Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if not path:
            cancel()
            return
        try:
            fitz.Pixmap(path)
        except Exception:
            QMessageBox.warning(self, "Custom Stamp",
                                "PDFAR cannot read that image.")
            cancel()
            return
        self._set_custom_stamp({"kind": "image", "caption": "", "image": path})

    def _choose_measure_kind(self, kind: str) -> None:
        """Pick what the measure button draws and switch to that tool."""
        if kind not in self._measure_kind_actions:
            return
        self._measure_kind = kind
        self._measure_kind_actions[kind].setChecked(True)
        self._sync_measure_scale()
        self._set_tool_mode("measure")

    def _choose_measure_scale(self, scale: MeasurementScale) -> None:
        """Pick the units the measure tool reads in and switch to that tool."""
        self._measure_scale = scale
        self._sync_measure_scale()
        self._set_tool_mode("measure")

    def _sync_measure_scale(self) -> None:
        """Show the active kind and scale on the tool and in its menu."""
        self.act_measure.setToolTip(self._measure_tip(self._measure_scale))
        for kind, action in self._measure_kind_actions.items():
            action.setChecked(kind == self._measure_kind)
        for label, scale in MEASURE_SCALES:
            action = self._measure_scale_actions[label]
            action.setChecked(scale == self._measure_scale)
        self.measure_split.setToolTip(self._measure_tip(self._measure_scale))

    def _choose_custom_scale(self) -> None:
        """Ask for "how many real units one PDF point is" and use that scale."""
        current = f"{self._measure_scale.units_per_point:g} " \
                  f"{self._measure_scale.unit}"
        text, ok = QInputDialog.getText(
            self, "Measurement Scale",
            "How many real units does one PDF point equal?", text=current)
        if not ok:
            return
        parts = text.split()
        if len(parts) < 2:
            QMessageBox.warning(self, "Measurement Scale",
                                "Give a number and a unit, for example "
                                "“0.3528 mm”.")
            return
        try:
            factor = float(parts[0])
        except ValueError:
            factor = 0.0
        unit = " ".join(parts[1:])
        try:
            scale = checked_measure_scale(MeasurementScale(factor, unit, 2))
        except EditError as exc:
            QMessageBox.warning(self, "Measurement Scale", str(exc))
            return
        self._choose_measure_scale(scale)
        self.statusBar().showMessage(
            f"Measure in {scale.unit}: {scale.ratio}", 5000)

    def _set_view_mode(self, mode: str) -> None:
        """Apply a page view mode to the active document."""
        if mode not in VIEW_MODE_LABELS:
            return
        view = self.view
        if view is None:
            self._view_mode = mode
            self._update_view_mode_actions()
            return
        if view.reflow_mode:
            self.statusBar().showMessage(
                "Page view modes are unavailable in Reflow", 5000)
            self._update_view_mode_actions(True)
            return
        self._view_mode = mode
        view.set_view_mode(mode)
        self._update_view_mode_actions(False)

    def _on_view_mode_changed(self, mode: str) -> None:
        """The active view changed its layout (menu, restore, shortcut)."""
        if self.sender() is not self.view or mode not in VIEW_MODE_LABELS:
            return
        self._view_mode = mode
        self._refresh_view_mode_checks(mode)
        self._update_view_mode_button()

    def _refresh_view_mode_checks(self, mode: str) -> None:
        action = self._view_actions.get(mode)
        if action is not None and not action.isChecked():
            action.setChecked(True)

    def _update_view_mode_actions(self, reflow: Optional[bool] = None) -> None:
        """Enable view modes only for a document outside Reflow."""
        view = self.view
        if reflow is None:
            reflow = bool(view is not None and view.reflow_mode)
        enabled = view is not None and not reflow
        for action in self._view_actions.values():
            action.setEnabled(enabled)
        button = getattr(self, "view_mode_button", None)
        if button is not None:
            button.setEnabled(enabled)
        if view is not None:
            self._view_mode = view.view_mode
            self._refresh_view_mode_checks(view.view_mode)
        self._update_view_mode_button()

    def _update_view_mode_button(self) -> None:
        button = getattr(self, "view_mode_button", None)
        if button is None:
            return
        mode = (self._view_mode if self._view_mode in VIEW_MODE_LABELS
                else "continuous")
        ink = "#e6e6e6" if self.act_dark_mode.isChecked() else "#3c3c3c"
        button.setIcon(tinted_icon(f"view_{mode}.svg", ink))
        button.setToolTip(f"Page View: {VIEW_MODE_LABELS[mode]}")

    def _update_tool_actions(self, reflow: bool) -> None:
        """Enable the tools that make sense and reapply the global mode."""
        view = self.view
        has_doc = view is not None
        selection_ok = has_doc and not reflow
        self.act_explore.setEnabled(has_doc)
        self.act_sel_text.setEnabled(has_doc)
        self.act_sel_area.setEnabled(selection_ok)
        self.act_sel_table.setEnabled(selection_ok)
        self.act_draw.setEnabled(selection_ok)
        for action in self._shape_actions.values():
            action.setEnabled(selection_ok)
        for action in self._stamp_actions.values():
            action.setEnabled(selection_ok)
        for action in self._custom_stamp_actions.values():
            action.setEnabled(selection_ok)
        for action in self._measure_kind_actions.values():
            action.setEnabled(selection_ok)
        self.act_measure.setEnabled(selection_ok)
        split = getattr(self, "tool_split", None)
        if split is None:
            return
        split.setEnabled(has_doc)
        shape_split = getattr(self, "shape_split", None)
        if shape_split is not None:
            shape_split.setEnabled(has_doc)
        stamp_split = getattr(self, "stamp_split", None)
        if stamp_split is not None:
            stamp_split.setEnabled(has_doc)
        measure_split = getattr(self, "measure_split", None)
        if measure_split is not None:
            measure_split.setEnabled(has_doc)
        if reflow and self._tool_mode in ("area", "table", "draw", "shape",
                                          "stamp", "measure"):
            self._set_tool_mode("text")
            self.statusBar().showMessage(
                "Area, Table, Freehand, Shape, Stamp and Measure tools are "
                "unavailable in Reflow", 5000)
            return
        if view is not None:
            view.set_tool_mode(self._tool_mode)
            # Stamp state is viewer state too, so a tab switch shows the tab's
            # own standard stamp or custom definition, not the previous tab's.
            self._custom_stamp = view.custom_stamp
            if view.stamp_name in self._stamp_actions:
                self._stamp_name = view.stamp_name
            self._measure_scale = view.measure_scale
            self._measure_kind = view.measure_kind
            if self._tool_mode == "shape":
                # The kind is viewer state, like the pen's colour, so the tab
                # the user switched to keeps the shape it was drawing.
                self._shape_kind = view.shape_kind
                self.shape_split.setDefaultAction(
                    self._shape_actions[self._shape_kind])
            elif self._tool_mode == "stamp":
                if self._custom_stamp is not None:
                    action = self._custom_stamp_actions[
                        self._custom_stamp["kind"]]
                    action.setToolTip(self._custom_stamp_tip(
                        self._custom_stamp))
                else:
                    action = self._stamp_actions[self._stamp_name]
                self.stamp_split.setDefaultAction(action)
            elif self._tool_mode == "measure":
                self.measure_split.setDefaultAction(self.act_measure)
        self._tool_action(self._tool_mode).setChecked(True)
        self._sync_measure_scale()
        self._sync_pen_options()

    def _show_transient_message(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)

    def _set_compact_toolbar_buttons(self) -> None:
        """Show these toolbar actions as icons only; hover shows the label."""
        toolbar = getattr(self, "toolbar", None)
        if toolbar is None:
            return
        for action in (self.act_prev, self.act_next, self.act_explore,
                       self.act_zout, self.act_zin, self.act_reflow,
                       self.act_rotl, self.act_rotr):
            button = toolbar.widgetForAction(action)
            if isinstance(button, QToolButton):
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _build_pen_options(self, tb: QToolBar) -> None:
        """The pen's colour and thickness, next to the tool that uses them."""
        self.pen_options = QWidget(tb)
        layout = QHBoxLayout(self.pen_options)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.pen_color_button = QToolButton(self.pen_options)
        self.pen_color_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.pen_color_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.pen_color_button.setAutoRaise(True)
        self.pen_color_menu = QMenu(self.pen_color_button)
        self._pen_color_actions = []
        for name, hex_color in INK_COLORS:
            action = self.pen_color_menu.addAction(name)
            action.setCheckable(True)
            action.setData(hex_color)
            action.triggered.connect(
                lambda _checked=False, c=hex_color: self._set_ink_color(c))
            self._pen_color_actions.append(action)
        self.pen_color_menu.addSeparator()
        self.pen_custom_color = self.pen_color_menu.addAction("Custom…")
        self.pen_custom_color.triggered.connect(self._pick_ink_color)
        self.pen_color_button.setMenu(self.pen_color_menu)
        layout.addWidget(self.pen_color_button)

        self.pen_width_combo = QComboBox(self.pen_options)
        for width in INK_WIDTHS:
            self.pen_width_combo.addItem(f"{width:g} pt", width)
        self.pen_width_combo.setCurrentIndex(
            max(0, self.pen_width_combo.findData(INK_WIDTH)))
        self.pen_width_combo.setFixedWidth(68)
        self.pen_width_combo.setToolTip("Line thickness")
        self.pen_width_combo.currentIndexChanged.connect(
            self._on_ink_width_changed)
        layout.addWidget(self.pen_width_combo)

        self.pen_options.hide()
        self.pen_action: Optional[QAction] = None

    def _sync_pen_options(self) -> None:
        """Show the drawing controls with the tools that draw, mirroring the view.

        The widget is put into the toolbar and taken out again rather than
        merely hidden: a toolbar re-shows the widgets it owns when it lays
        out, and hiding a widget that lives inside one is not reliable enough
        to depend on.  Removing the action takes the widget out of the layout,
        and inserting it puts it back where it belongs.
        """
        toolbar = getattr(self, "toolbar", None)
        if toolbar is None:
            return
        view = self.view
        visible = view is not None and self._tool_mode in ("draw", "shape",
                                                           "measure")
        present = (self.pen_action is not None
                   and self.pen_action in toolbar.actions())
        if visible and not present:
            self.pen_action = toolbar.addWidget(self.pen_options)
            self.pen_options.show()
        elif not visible and present:
            toolbar.removeAction(self.pen_action)
            self.pen_options.hide()
        if view is None:
            return
        color = view.ink_color
        if self._tool_mode == "shape":
            icon = f"shape_{view.shape_kind}.svg"
        elif self._tool_mode == "measure":
            icon = "measure.svg"
        else:
            icon = "draw_freehand.svg"
        self.pen_color_button.setIcon(tinted_icon(icon, color))
        self.pen_color_button.setToolTip(f"Drawing colour: {color}")
        for action in self._pen_color_actions:
            action.setChecked(action.data() == color)
        self.pen_width_combo.blockSignals(True)
        index = self.pen_width_combo.findData(view.ink_width)
        if index >= 0:
            self.pen_width_combo.setCurrentIndex(index)
        self.pen_width_combo.blockSignals(False)

    def _set_ink_color(self, color: str) -> None:
        view = self.view
        if view is None:
            return
        view.set_ink_color(color)
        self._sync_pen_options()

    def _pick_ink_color(self) -> None:
        """Ask for a colour outside the palette."""
        view = self.view
        if view is None:
            return
        chosen = QColorDialog.getColor(QColor(view.ink_color), self,
                                       "Pen Colour")
        if chosen.isValid():
            self._set_ink_color(chosen.name().upper())

    def _on_ink_width_changed(self, index: int) -> None:
        view = self.view
        width = self.pen_width_combo.itemData(index)
        if view is not None and width is not None:
            view.set_ink_width(float(width))

    def _update_zoom_actions(self, reflow: bool) -> None:
        """Keep Ctrl +/- labelled for what they actually do in this mode."""
        ink = "#e6e6e6" if self.act_dark_mode.isChecked() else "#3c3c3c"
        self.act_prev.setIcon(tinted_icon("previous_page.svg", ink))
        self.act_next.setIcon(tinted_icon("next_page.svg", ink))
        self.act_rotl.setIcon(tinted_icon("rotate_left.svg", ink))
        self.act_rotr.setIcon(tinted_icon("rotate_right.svg", ink))
        self.act_reflow.setIcon(tinted_icon("reflow.svg", ink))
        self.act_explore.setIcon(tinted_icon("explore.svg", ink))
        self.act_sel_text.setIcon(tinted_icon("select_text.svg", ink))
        self.act_sel_area.setIcon(tinted_icon("select_area.svg", ink))
        self.act_sel_table.setIcon(tinted_icon("select_table.svg", ink))
        self.act_draw.setIcon(tinted_icon("draw_freehand.svg", ink))
        for kind, action in self._shape_actions.items():
            action.setIcon(tinted_icon(f"shape_{kind}.svg", ink))
        stamp_icon = tinted_icon("stamp.svg", ink)
        for action in self._stamp_actions.values():
            action.setIcon(stamp_icon)
        for action in self._custom_stamp_actions.values():
            action.setIcon(stamp_icon)
        self.act_measure.setIcon(tinted_icon("measure.svg", ink))
        for mode in ("single", "facing", "facing_centered", "overview",
                     "continuous"):
            self._view_actions[mode].setIcon(
                tinted_icon(f"view_{mode}.svg", ink))
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
        self._update_view_mode_actions(reflow)
        self._update_tool_actions(reflow)

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
        self.right_tabs.setCurrentWidget(self.search_panel)
        self.query_edit.setFocus()
        self.query_edit.selectAll()

    def _cancel_search(self) -> None:
        """Invalidate pending results and stop the current worker."""
        self._search_generation += 1
        worker = self.search_worker
        if worker is not None:
            worker.cancel()
        thread = self.search_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(1000)

    def _show_search_error(self, message: str) -> None:
        self.btn_search.setEnabled(True)
        self.search_progress.setVisible(False)
        self.results.clear()
        if self.view is not None:
            self.view.clear_search()
        self.hit_label.setText(message)
        self.status_left.setText(message)

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

        if mode == "REGEX":
            try:
                compile_regex(params)
            except ValueError as exc:
                self._cancel_search()
                self._show_search_error(str(exc))
                return

        self._search_params = params
        self._cancel_search()
        generation = self._search_generation

        self.results.clear()
        self.hit_label.setText("")
        if self.view is not None:
            self.view.clear_search()
        self.btn_search.setEnabled(False)
        self.search_progress.setVisible(True)
        self.search_progress.setRange(0, 0)

        self.search_thread = QThread(self)
        self.search_worker = SearchWorker(self.view.session, params)
        self.search_worker.moveToThread(self.search_thread)
        self.search_thread.started.connect(self.search_worker.run)
        self.search_worker.progress.connect(
            lambda done, total, g=generation: self._on_search_progress(done, total, g))
        # bound method of a QObject => queued to the GUI thread automatically
        self.search_worker.done.connect(
            lambda hits, g=generation: self._on_search_done(hits, g))
        self.search_worker.done.connect(self.search_thread.quit)
        self.search_worker.failed.connect(
            lambda message, g=generation: self._on_search_failed(message, g))
        self.search_worker.failed.connect(self.search_thread.quit)
        self.search_thread.start()
        # search hits belong to the document they were run on
        self._search_tab = self._active_tab()

    def _on_search_progress(self, done: int, total: int, generation: int) -> None:
        if generation != self._search_generation:
            return
        self.search_progress.setRange(0, max(1, total))
        self.search_progress.setValue(done)

    def _on_search_done(self, hits: list, generation: int) -> None:
        if generation != self._search_generation:
            return
        self.btn_search.setEnabled(True)
        self.search_progress.setVisible(False)

        self.results.clear()
        params = self._search_params
        for hit in hits:
            prefix = f"Page {hit.page + 1}: "
            item = QListWidgetItem(prefix + hit.snippet)
            item.setData(Qt.ItemDataRole.UserRole, hit.page)
            if params is not None:
                offset = len(prefix)
                spans = [(start + offset, end + offset)
                         for start, end in highlight_spans(
                             hit.snippet, params)]
                if spans:
                    item.setData(HIGHLIGHT_ROLE, spans)
            self.results.addItem(item)

        if self._search_tab is not None and self.tabs.indexOf(self._search_tab) != -1:
            total = self._search_tab.view.set_search_hits(hits)
            if hits:
                self._step_hit(+1)
                self.status_left.setText(f"{len(hits)} page(s) matched, {total} hit(s)")
            else:
                self._search_tab.view.clear_search()
                self.status_left.setText("No results")

    def _on_search_failed(self, message: str, generation: int) -> None:
        if generation != self._search_generation:
            return
        self._show_search_error(message)

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and self.view:
            index = self.view.first_hit_index(page)
            if index >= 0:
                self.view.goto_hit_index(index)
            else:
                self.view.goto_page(page)

    def _step_hit(self, step: int) -> None:
        if self.view:
            self.view.goto_hit(step)

    def _on_hit_changed(self, current: int, total: int) -> None:
        self.hit_label.setText(f"Hit {current} / {total}" if total else "")
        if not current or not self.view:
            return
        page = self.view.hit_page(current - 1)
        if page < 0:
            return
        for row in range(self.results.count()):
            item = self.results.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == page:
                if self.results.currentRow() != row:
                    self.results.setCurrentRow(row)
                break

    # ------------------------------------------------------------- misc
    def _toggle_sidebar(self, checked: bool) -> None:
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.setVisible(checked)

    def _toggle_tools_panel(self, checked: bool) -> None:
        if hasattr(self, "search_dock") and self.search_dock is not None:
            self.search_dock.setVisible(checked)
            if checked:
                self.search_dock.raise_()

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
        self._restore_geometry()
        # restore the preload memory level (Okular); the Settings dialog reads
        # it from self.memory_level when it opens.
        lvl = self.settings.value("memory_level", "normal")
        if lvl not in MEMORY_LEVELS:
            lvl = "normal"
        self.memory_level = lvl
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
        self._apply_right_tab_icons()
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

    def _restore_geometry(self) -> None:
        """Restore the saved startup mode and the remembered geometry."""
        self._start_maximized = self.settings.value(
            "window_maximized", True, type=bool)
        geo = self.settings.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)

    def _save_geometry(self) -> None:
        """Persist geometry, ignoring fullscreen and mid-close states."""
        if self.isFullScreen():
            return
        self.settings.setValue("geometry", self.saveGeometry())

    def show_initial(self) -> None:
        """Open maximized, or at the remembered size and position."""
        if self._start_maximized:
            self.showMaximized()
        else:
            self.show()
            # restoreGeometry() also restores a saved maximized state.
            if self.isMaximized():
                self.showNormal()

    def _apply_window_preference(self, maximized: bool) -> None:
        """Persist the startup preference and apply it to the live window."""
        self._start_maximized = maximized
        self.settings.setValue("window_maximized", maximized)
        if getattr(self, "_presentation", False):
            return
        if maximized:
            self.showMaximized()
        elif self.isMaximized() or self.isFullScreen():
            self.showNormal()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._geometry_timer.start()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if getattr(self, "_presentation", False):
            self._exit_presentation()
        # Ask about every document *before* closing any of them, so a Cancel
        # late in the list cannot have already thrown away earlier work.
        if not self._confirm_discard_all():
            event.ignore()
            return
        self._geometry_timer.stop()
        # windowState is intentionally not saved to avoid restoring a hidden UI.
        self._save_geometry()
        self._close_all_tabs(confirm=False)
        self._cancel_search()
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
    win.show_initial()

    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        win.open_document(files[0])

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
