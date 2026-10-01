"""Ventana principal de la aplicación."""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QObject, QLocale, QSize
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QToolBar, QStatusBar,
    QScrollArea, QDockWidget, QMessageBox,
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox,
    QListWidget, QListWidgetItem,
)
import fitz

from .search import SearchParams, SearchHit, normalize_space
from .viewer import PDFViewer
from .i18n import I18n


class SearchWorker(QObject):
    """Worker para búsqueda asíncrona."""
    progress = pyqtSignal(int, int)
    done = pyqtSignal(list)

    def __init__(self, doc: fitz.Document, params: SearchParams):
        super().__init__()
        self.doc = doc
        self.params = params

    def run(self):
        hits: List[SearchHit] = []
        total = self.doc.page_count
        p = self.params

        if p.mode == 'PHRASE':
            query = p.query if p.case_sensitive else p.query.lower()
        else:
            terms = [t for t in re.split(r"\s+", p.query) if t]
            if not p.case_sensitive:
                terms = [t.lower() for t in terms]

        for i in range(total):
            self.progress.emit(i + 1, total)
            page = self.doc.load_page(i)
            text = page.get_text("text") or ""
            cmp_text = text if p.case_sensitive else text.lower()

            matched = False
            if p.mode == 'PHRASE':
                matched = query in cmp_text
            elif p.mode == 'AND':
                if p.whole_words:
                    matched = all(re.search(r'\b' + re.escape(term) + r'\b', cmp_text) for term in terms)
                else:
                    matched = all(term in cmp_text for term in terms)
            elif p.mode == 'OR':
                if p.whole_words:
                    matched = any(re.search(r'\b' + re.escape(term) + r'\b', cmp_text) for term in terms)
                else:
                    matched = any(term in cmp_text for term in terms)

            if matched:
                snippet = _make_snippet(text, p.query, p.case_sensitive)
                hits.append(SearchHit(page=i, snippet=snippet))

        self.done.emit(hits)


def _make_snippet(text: str, query: str, case_sensitive: bool, width: int = 160) -> str:
    src = text if case_sensitive else text.lower()
    q = query if case_sensitive else query.lower()
    m = src.find(q.split()[0]) if q.split() else 0
    if m == -1:
        m = 0
    start = max(0, m - width // 2)
    end = min(len(text), start + width)
    return " ".join(text[start:end].split())


class PDFAR(QMainWindow):
    """Ventana principal de PDFAR."""

    def __init__(self):
        super().__init__()
        self.doc: Optional[fitz.Document] = None
        self.viewer: Optional[PDFViewer] = None
        self.zoom = 1.0
        self.fit_mode = "none"
        self.current_page = 0
        self.search_rects_per_page: Dict[int, List[fitz.Rect]] = {}
        self.i18n: I18n | None = None
        self._build_ui()

    def _build_ui(self):
        self.setWindowTitle("PDFAR - Advanced PDF Reader")
        self.resize(1200, 800)

        # Toolbar
        tb = QToolBar("Actions")
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        act_open = QAction("Open", self)
        act_open.triggered.connect(self.open_pdf)
        tb.addAction(act_open)

        tb.addSeparator()
        act_prev = QAction("Previous", self)
        act_next = QAction("Next", self)
        act_prev.triggered.connect(lambda: self.goto_page(max(0, self.current_page - 1)))
        act_next.triggered.connect(lambda: self.goto_page(self.current_page + 1))
        tb.addAction(act_prev)
        tb.addAction(act_next)

        tb.addSeparator()
        act_zin = QAction("Zoom +", self)
        act_zout = QAction("Zoom -", self)
        act_zin.triggered.connect(lambda: self._set_zoom(self.zoom * 1.25))
        act_zout.triggered.connect(lambda: self._set_zoom(self.zoom / 1.25))
        tb.addAction(act_zin)
        tb.addAction(act_zout)

        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self._set_zoom(self.zoom * 1.25))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self._set_zoom(self.zoom / 1.25))

        tb.addSeparator()
        tb.addWidget(QLabel("Zoom:"))
        self.lbl_zoom = QLabel("100%")
        tb.addWidget(self.lbl_zoom)

        self.cmb_zoom = QComboBox()
        self.cmb_zoom.addItems([
            "Fit Page", "Fit Width",
            "50%", "100%", "150%", "200%", "250%", "300%"
        ])
        self.cmb_zoom.currentTextChanged.connect(self._apply_zoom_preset)
        tb.addWidget(self.cmb_zoom)

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.setCentralWidget(self.scroll_area)

        # Search dock
        dock = QDockWidget("Search", self)
        dock.setMinimumWidth(300)
        panel = QWidget()
        v = QVBoxLayout(panel)

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Type words or \"exact phrases\"")
        v.addWidget(self.query_edit)

        self.mode = QComboBox()
        self.mode.addItems(["AND", "OR", "PHRASE"])
        v.addWidget(self.mode)

        self.cb_case = QCheckBox("Case sensitive")
        self.cb_whole = QCheckBox("Whole words")
        v.addWidget(self.cb_case)
        v.addWidget(self.cb_whole)

        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self.start_search)
        v.addWidget(self.btn_search)

        self.results = QListWidget()
        self.results.itemClicked.connect(self.goto_result)
        v.addWidget(self.results, 1)

        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        QShortcut(QKeySequence("Space"), self, activated=lambda: self._scroll_page(+1))
        QShortcut(QKeySequence("PgDown"), self, activated=lambda: self._scroll_page(+1))

    def _scroll_page(self, direction: int):
        bar = self.scroll_area.verticalScrollBar()
        step = max(1, self.scroll_area.viewport().height() - 40)
        bar.setValue(bar.value() + direction * step)

    def open_pdf(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Open PDF", "", "PDF (*.pdf)")
        if not fn:
            return
        try:
            self.doc = fitz.open(fn)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open PDF:\n{e}")
            return

        self.current_page = 0
        self.zoom = 1.0
        self.fit_mode = "none"

        self.viewer = PDFViewer(fn, zoom=self.zoom)
        self.scroll_area.setWidget(self.viewer)
        
        scroll_bar = self.scroll_area.verticalScrollBar()
        scroll_bar.valueChanged.connect(
            lambda val: self.viewer.set_scroll_position(
                val, self.scroll_area.viewport().height()
            )
        )
        scroll_bar.valueChanged.connect(
            lambda: self.viewer.scroll_timer.start(50)
        )
        
        self._update_zoom_label()

        self.status.showMessage(f"Loaded: {fn} — {self.doc.page_count} pages", 5000)

    def goto_page(self, page_index: int):
        if not self.viewer or not self.doc:
            return
        page_index = max(0, min(page_index, self.doc.page_count - 1))
        w = self.viewer.get_page_widget(page_index)
        if w:
            self.scroll_area.ensureWidgetVisible(w, xMargin=0, yMargin=20)
        self.current_page = page_index
        self.viewer._render_page(page_index, priority=0)

    def goto_result(self, item: QListWidgetItem):
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int):
            self.goto_page(page)

    def _update_zoom_label(self):
        self.lbl_zoom.setText(f"{int(round(self.zoom * 100))}%")

    def _set_zoom(self, factor: float):
        factor = max(0.5, min(2.5, factor))
        self.fit_mode = "none"
        self.zoom = factor
        if self.viewer:
            self.viewer.set_zoom(self.zoom)
        self._update_zoom_label()

    def _apply_zoom_preset(self, text: str):
        if not self.doc:
            return
        if text in ("Fit Page", "Fit Width"):
            page0 = self.doc.load_page(0)
            vp = self.scroll_area.viewport().size()
            if text == "Fit Width":
                scale = vp.width() / page0.rect.width
            else:
                scale = min(vp.width() / page0.rect.width, vp.height() / page0.rect.height)
            self._set_zoom(scale)
        elif text.endswith('%'):
            self._set_zoom(int(text[:-1]) / 100.0)

    def _params_from_ui(self) -> SearchParams:
        return SearchParams(
            query=normalize_space(self.query_edit.text()),
            mode=self.mode.currentText(),
            case_sensitive=self.cb_case.isChecked(),
            whole_words=self.cb_whole.isChecked(),
            proximity=5,
        )

    def start_search(self):
        if not self.doc:
            QMessageBox.information(self, "Open PDF", "Please open a PDF file first.")
            return
        params = self._params_from_ui()
        if not params.query:
            return

        self.results.clear()
        self.btn_search.setEnabled(False)

        self.worker_thread = QThread(self)
        self.worker = SearchWorker(self.doc, params)
        self.worker.moveToThread(self.worker_thread)
        self.worker.done.connect(lambda hits: self._on_search_done(hits, params))
        self.worker.done.connect(self.worker_thread.quit)
        self.worker_thread.start()

    def _on_search_done(self, hits: List[SearchHit], params: SearchParams):
        self.btn_search.setEnabled(True)

        self.search_rects_per_page.clear()
        terms = [params.query] if params.mode == 'PHRASE' else [t for t in params.query.split() if t]

        for h in hits:
            item = QListWidgetItem(f"Page {h.page + 1}: {h.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, h.page)
            self.results.addItem(item)

            rects: List[fitz.Rect] = []
            page = self.doc.load_page(h.page)
            for t in terms:
                try:
                    rects.extend(page.search_for(t, match_case=params.case_sensitive) or [])
                except TypeError:
                    rects.extend(page.search_for(t) or [])
            self.search_rects_per_page[h.page] = rects

        if self.viewer:
            self.viewer.set_highlights(self.search_rects_per_page)

        if hits:
            self.goto_page(hits[0].page)
        else:
            QMessageBox.information(self, "No results", "No results found.")


def main():
    app = QApplication(sys.argv)
    
    # Set up internationalization
    i18n = I18n(app)
    # Try to load user's system locale, default to English
    i18n.load_translations()
    
    w = PDFAR()
    w.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
