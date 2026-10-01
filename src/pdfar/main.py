"""Main window and application entry point."""

from __future__ import annotations

import os
import sys
from typing import List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSettings, QThread, Qt
from PyQt6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
    QSpinBox, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from . import APP_NAME, __version__
from .i18n import I18n
from .search import SearchParams, SearchWorker, normalize_space
from .sidebar import Sidebar
from .viewer import PDFView


class MainWindow(QMainWindow):
    """PDFAR main window."""

    def __init__(self):
        super().__init__()
        self.doc_path: Optional[str] = None
        self.view: Optional[PDFView] = None
        self.sidebar: Optional[Sidebar] = None
        self.search_thread: Optional[QThread] = None
        self.search_worker: Optional[SearchWorker] = None
        self.settings = QSettings(APP_NAME, APP_NAME)

        self.setWindowTitle(f"{APP_NAME} — Advanced PDF Reader")
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        self._build_actions()
        self._build_toolbar()
        self._build_search_dock()
        self._build_statusbar()
        self._build_shortcuts()
        self._restore_state()

    # ------------------------------------------------------------- UI
    def _build_actions(self) -> None:
        self.act_open = QAction("&Open…", self)
        self.act_open.setShortcut(QKeySequence.StandardKey.Open)
        self.act_open.triggered.connect(self.open_dialog)

        self.act_recent = QAction("Recent ▾", self)
        self.menu_recent = QMenu("Recent files", self)
        self.act_recent.setMenu(self.menu_recent)

        self.act_sidebar = QAction("Sidebar", self)
        self.act_sidebar.setCheckable(True)
        self.act_sidebar.setChecked(True)
        self.act_sidebar.triggered.connect(self._toggle_sidebar)

        self.act_quit = QAction("&Quit", self)
        self.act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        self.act_quit.triggered.connect(self.close)

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setIconSize(tb.iconSize())
        self.addToolBar(tb)

        tb.addAction(self.act_open)
        tb.addAction(self.act_recent)
        tb.addSeparator()

        self.act_prev = QAction("◀ Prev", self)
        self.act_next = QAction("Next ▶", self)
        self.act_prev.triggered.connect(self._prev_page)
        self.act_next.triggered.connect(self._next_page)
        tb.addAction(self.act_prev)
        tb.addAction(self.act_next)

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

        self.act_zin = QAction("Zoom +", self)
        self.act_zout = QAction("Zoom −", self)
        self.act_zin.triggered.connect(self._zoom_in)
        self.act_zout.triggered.connect(self._zoom_out)
        tb.addAction(self.act_zout)
        tb.addAction(self.act_zin)

        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(52)
        tb.addWidget(self.zoom_label)

        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems(["Fit Page", "Fit Width", "50%", "75%", "100%",
                                  "125%", "150%", "200%", "300%", "400%"])
        self.zoom_combo.setCurrentIndex(1)
        self.zoom_combo.setFixedWidth(104)
        self.zoom_combo.currentTextChanged.connect(self._apply_zoom_preset)
        tb.addWidget(self.zoom_combo)
        tb.addSeparator()

        self.act_rotl = QAction("Rotate ⟲", self)
        self.act_rotr = QAction("Rotate ⟳", self)
        self.act_rotl.triggered.connect(self._rot_left)
        self.act_rotr.triggered.connect(self._rot_right)
        tb.addAction(self.act_rotl)
        tb.addAction(self.act_rotr)
        tb.addSeparator()

        self.act_copy = QAction("Copy", self)
        self.act_copy.triggered.connect(self._copy)
        tb.addAction(self.act_copy)
        tb.addSeparator()

        self.act_find = QAction("Find…", self)
        self.act_find.setShortcut(QKeySequence.StandardKey.Find)
        self.act_find.triggered.connect(self._focus_search)
        tb.addAction(self.act_find)
        tb.addAction(self.act_sidebar)

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
        self.mode.addItems(["AND", "OR", "PHRASE"])
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
        self._shortcut("F3", self._hit_next)
        self._shortcut("Shift+F3", self._hit_prev)
        self._shortcut("Ctrl+Shift+C", self._copy)
        self._shortcut("Ctrl+Left", self._rot_left)
        self._shortcut("Ctrl+Right", self._rot_right)
        self._shortcut("Ctrl+L", self._focus_search)
        self._shortcut("Ctrl+0", self._fit_page)
        self._shortcut("Ctrl+1", self._zoom_100)
        self._shortcut("Ctrl+2", self._fit_width)

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
        if self.view:
            self.view.zoom_in()

    def _zoom_out(self) -> None:
        if self.view:
            self.view.zoom_out()

    def _zoom_100(self) -> None:
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

        self._close_document()
        try:
            self.view = PDFView(path, parent=self, password=password)
        except ValueError:
            QMessageBox.critical(self, "Open", "Wrong password.")
            return False
        except Exception as exc:
            QMessageBox.critical(self, "Open", f"Could not render PDF:\n{exc}")
            return False

        self.doc_path = path
        self.setCentralWidget(self.view)
        self.sidebar = Sidebar(self.view.doc, self.view.pool, self)
        self.sidebar.goto_page_requested.connect(self._goto)
        self.search_dock.widget().setVisible(True)

        # left dock
        self.side_dock = QDockWidget("Pages", self)
        self.side_dock.setObjectName("sideDock")
        self.side_dock.setWidget(self.sidebar)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.side_dock)
        self.side_dock.setVisible(self.act_sidebar.isChecked())

        # wiring
        self.view.page_changed.connect(self._on_page_changed)
        self.view.zoom_changed.connect(self._on_zoom_changed)
        self.view.hit_changed.connect(self._on_hit_changed)
        self.view.selection_changed.connect(self._update_status)

        self.page_spin.setMaximum(page_count)
        self.page_spin.setValue(1)
        self.page_label.setText(f"/ {page_count}")

        self._set_zoom(1.0)
        self._set_fit("width")
        self.setWindowTitle(f"{os.path.basename(path)} — {APP_NAME}")
        self.status_left.setText(f"Loaded: {path}")
        self._remember_file(path)
        self._update_status()
        self.view.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def _close_document(self) -> None:
        if self.search_thread is not None and self.search_thread.isRunning():
            self.search_thread.quit()
            self.search_thread.wait(2000)
        if self.view is not None:
            self.view.shutdown()
            self.view.deleteLater()
            self.view = None
        if self.sidebar is not None:
            self.sidebar.deleteLater()
            self.sidebar = None
        if hasattr(self, "side_dock") and self.side_dock is not None:
            self.side_dock.deleteLater()
            self.side_dock = None

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
            self._set_zoom(int(text[:-1]) / 100.0)

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
        self.zoom_label.setText(f"{int(round(zoom * 100))}%")
        self._update_status()

    def _update_status(self) -> None:
        if not self.view:
            self.status_right.setText("")
            return
        sel = self.view.selected_text()
        extra = f"   |   {len(sel)} chars selected" if sel else ""
        self.status_right.setText(
            f"Page {self.view.current_page + 1}/{self.view.page_count}   |   "
            f"{int(round(self.view.zoom * 100))}%   |   "
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
        params = SearchParams(query=query, mode=self.mode.currentText(),
                              case_sensitive=self.cb_case.isChecked(),
                              whole_words=self.cb_whole.isChecked())

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

        if self.view:
            total = self.view.set_search_hits(hits)
            if hits:
                self._step_hit(+1)
                self.status_left.setText(f"{len(hits)} page(s) matched, {total} hit(s)")
            else:
                self.view.clear_search()
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
        state = self.settings.value("windowState")
        if state is not None:
            self.restoreState(state)
        self._rebuild_recent_menu()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self._close_document()
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
                break
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
