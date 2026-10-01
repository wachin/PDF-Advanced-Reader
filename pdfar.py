#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lector de PDF con PyQt6 y búsqueda avanzada por página (Debian 12).

Características principales:
- Abrir PDF (PyMuPDF)
- Render de TODAS las páginas en un visor con scroll vertical
- Búsqueda avanzada por página:
  * Todas las palabras (AND)
  * Cualquier palabra (OR)
  * Frase exacta
  * AND a distancia N (proximidad)
  * Sensible a mayúsculas/minúsculas y palabra completa
- Lista de resultados con fragmentos, clic para saltar a la página
- Resaltado de coincidencias en las páginas
- Zoom con:
  * Botones Zoom ±
  * Atajos Ctrl + + y Ctrl + - (incluye Ctrl + = por layouts)
  * Presets: Ajustar página, Ajustar anchura, Expandir la ventana hasta ajustar,
    50%, 60%, 70%, 80%, 90%, 100%, 150%, 200%, 250%, 300%, 350%, 400%, 450%, 500%, 550%
  * Indicador textual del zoom actual
- Re-render de TODO el documento al cambiar zoom
- Reajuste automático del zoom cuando cambie el tamaño de la ventana si está en modo “ajustar”

Dependencias:
    sudo apt update
    sudo apt install -y python3-pyqt6 python3-pip
    pip3 install --user PyMuPDF

Ejecutar:
    python3 pdf_advanced_reader.py
"""

from __future__ import annotations

import sys
import re
import weakref
from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict

from PyQt6.QtCore import (
    Qt, QSize, QRectF, pyqtSignal, QObject, QThread, QTimer
)
from PyQt6.QtGui import (
    QAction, QPixmap, QPainter, QBrush, QColor, QImage, QPen,
    QKeySequence, QShortcut
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QFileDialog, QToolBar, QStatusBar,
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QLineEdit, QComboBox, QCheckBox, QSpinBox,
    QListWidget, QListWidgetItem, QDockWidget, QMessageBox, QScrollArea
)

import fitz  # PyMuPDF

# ============================= Utilidades ==================================

def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class SearchParams:
    query: str
    mode: str  # 'AND', 'OR', 'PHRASE', 'PROX'
    case_sensitive: bool
    whole_words: bool
    proximity: int = 5


@dataclass
class SearchHit:
    page: int
    snippet: str


class PageCache:
    """Cache de imágenes renderizadas por página."""
    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.cache: Dict[Tuple[int, float], QPixmap] = {}
    
    def get(self, page_idx: int, zoom: float) -> Optional[QPixmap]:
        key = (page_idx, round(zoom, 2))
        return self.cache.get(key)
    
    def put(self, page_idx: int, zoom: float, pixmap: QPixmap):
        key = (page_idx, round(zoom, 2))
        self.cache[key] = pixmap
        # Evict oldest if over capacity
        if len(self.cache) > self.max_size:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
    
    def clear(self):
        self.cache.clear()
    
    def invalidate_zoom(self, old_zoom: float, new_zoom: float):
        """Remove all entries that don't match new zoom."""
        keys_to_remove = [k for k in self.cache if abs(k[1] - new_zoom) > 0.01]
        for k in keys_to_remove:
            del self.cache[k]


# Paleta para resaltados
HIGHLIGHT_COLOR = QColor(255, 235, 59, 110)  # amarillo
HIGHLIGHT_PEN = QPen(Qt.PenStyle.NoPen)


# ======================= Funciones de coincidencia ==========================

def _word_bound_pattern(term: str) -> str:
    # límites de palabra "clásicos"
    return rf"(?<![\w]){re.escape(term)}(?![\w])"


def _contains_word(text: str, term: str, whole: bool) -> bool:
    if whole:
        return re.search(_word_bound_pattern(term), text) is not None
    return term in text


def _tokenize(text: str) -> List[str]:
    # tokens alfanuméricos sencillos
    return re.findall(r"\w+", text)


def _match_proximity(text: str, terms: List[str], prox: int, whole: bool) -> bool:
    """
    Devuelve True si todas las palabras aparecen y existe una ventana
    donde la diferencia entre la posición de la palabra más lejana y la
    más cercana es <= prox (en número de tokens).
    """
    if not terms:
        return False
    tokens = _tokenize(text)
    if not tokens:
        return False

    # índice por token -> posiciones
    pos: Dict[str, List[int]] = {}
    for idx, tok in enumerate(tokens):
        pos.setdefault(tok, []).append(idx)

    def positions_for(term: str) -> List[int]:
        if whole:
            return pos.get(term, [])
        return [i for i, tok in enumerate(tokens) if term in tok]

    lists = [positions_for(t) for t in terms]
    if any(len(L) == 0 for L in lists):
        return False

    # deslizante multi-puntero
    idxs = [0] * len(lists)
    while True:
        cur = [lists[k][idxs[k]] for k in range(len(lists))]
        span = max(cur) - min(cur)
        if span <= prox:
            return True
        kmin = min(range(len(lists)), key=lambda k: lists[k][idxs[k]])
        idxs[kmin] += 1
        if idxs[kmin] >= len(lists[kmin]):
            break
    return False


def make_snippet(text: str, query: str, case_sensitive: bool, width: int = 160) -> str:
    src = text if case_sensitive else text.lower()
    q0 = query if case_sensitive else query.lower()
    m = None
    for token in re.findall(r"\w+|\S", q0):
        p = src.find(token)
        if p != -1:
            m = p
            break
    if m is None:
        m = 0
    start = max(0, m - width // 2)
    end = min(len(text), start + width)
    return normalize_space(text[start:end])


# =========================== Worker de búsqueda =============================

class SearchWorker(QObject):
    progress = pyqtSignal(int, int)  # current, total
    done = pyqtSignal(list)  # List[SearchHit]

    def __init__(self, doc: fitz.Document, params: SearchParams):
        super().__init__()
        self.doc = doc
        self.params = params

    def run(self):
        hits: List[SearchHit] = []
        total = self.doc.page_count
        p = self.params

        if p.mode == 'PHRASE':
            phrase = p.query if p.case_sensitive else p.query.lower()
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
                matched = phrase in cmp_text
            elif p.mode == 'AND':
                matched = all(_contains_word(cmp_text, t, p.whole_words) for t in terms)
            elif p.mode == 'OR':
                matched = any(_contains_word(cmp_text, t, p.whole_words) for t in terms)
            elif p.mode == 'PROX':
                matched = _match_proximity(cmp_text, terms, p.proximity, p.whole_words)

            if matched:
                snippet = make_snippet(text, p.query, p.case_sensitive)
                hits.append(SearchHit(page=i, snippet=snippet))

        self.done.emit(hits)


# ========================== Visor con scroll ================================

class PDFScrollViewer(QWidget):
    """
    Renderiza páginas bajo demanda con caché para documentos grandes.
    Gestiona zoom y resaltados.
    """
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
        
        # Placeholders por página (para mantener el scroll correcto)
        self.labels: List[QLabel] = []
        self._build_placeholders()

    # ------------------------ API pública ---------------------------------
    def set_zoom(self, zoom: float):
        self.zoom = zoom
        self.cache.invalidate_zoom(zoom - 0.01, zoom)
        self._clear()
        self._build_placeholders()
        self._render_visible_pages()

    def set_highlights(self, per_page_rects: Dict[int, List[fitz.Rect]]):
        self.highlights = per_page_rects or {}
        self._render_visible_pages()

    def ensure_page_widget(self, page_index: int) -> Optional[QWidget]:
        if 0 <= page_index < len(self.labels):
            return self.labels[page_index]
        return None

    # ------------------------ Render interno ------------------------------
    def _build_placeholders(self):
        self.labels.clear()
        for i in range(self.doc.page_count):
            lbl = QLabel("Página " + str(i + 1))
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
        """Render solo las páginas visibles o cercanas al viewport."""
        if not hasattr(self, "viewer") or not self.viewer:
            return
        bar = self.scroll_area.verticalScrollBar()
        viewport_h = self.scroll_area.viewport().height()
        scroll_y = bar.value()
        viewport_top = scroll_y
        viewport_bottom = scroll_y + viewport_h
        
        for i, lbl in enumerate(self.labels):
            if not lbl.pos():
                continue
            y = lbl.pos().y()
            h = lbl.height() if lbl.height() > 0 else 100
            # Render si es visible o próxima
            if (y + h > viewport_top and y < viewport_bottom) or i < 2:
                self._render_page(i)

    def _render_page(self, page_index: int):
        """Render una página específica con caché."""
        if not self.doc or not (0 <= page_index < self.doc.page_count):
            return
        
        # Verificar caché
        cached = self.cache.get(page_index, self.zoom)
        if cached:
            self._apply_pixmap(page_index, cached)
            return
        
        # Agregar a pendientes (para renderizar asíncronamente)
        if page_index not in self.pending_render_pages:
            self.pending_render_pages.add(page_index)
            QTimer.singleShot(50, lambda: self._schedule_render(page_index))
    
    def _schedule_render(self, page_index: int):
        self.render_timer.start(100)  # Debounce
    
    def _flush_pending_renders(self):
        pages = list(self.pending_render_pages)
        self.pending_render_pages.clear()
        for p in pages:
            self._do_render(p)
    
    def _do_render(self, page_index: int):
        """Render síncrono de una página."""
        if not self.doc:
            return
        
        page = self.doc.load_page(page_index)
        mat = fitz.Matrix(self.zoom, self.zoom)
        pix = page.get_pixmap(matrix=mat, alpha=True)
        qimg = QImage.fromData(pix.tobytes("png"))
        pm = QPixmap.fromImage(qimg)
        
        # Guardar en caché
        self.cache.put(page_index, self.zoom, pm)
        
        # Aplicar resaltados
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


# ============================== Ventana principal ===========================

class PDFAdvancedReader(QMainWindow):
    FIT_NONE = 'none'
    FIT_PAGE = 'page'
    FIT_WIDTH = 'width'
    FIT_BEST = 'best'  # alias de ajustar página (mejor ajuste)

    ZOOM_PRESETS = [
        "Ajustar página",
        "Ajustar anchura",
        "Expandir la ventana hasta ajustar",
        "50%","60%","70%","80%","90%","100%",
        "150%","200%","250%","300%","350%","400%","450%","500%","550%",
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Lector PDF • Búsqueda Avanzada (scroll + zoom)")
        self.resize(1200, 840)

        self.doc: Optional[fitz.Document] = None
        self.viewer: Optional[PDFScrollViewer] = None
        self.zoom = 1.0
        self.fit_mode = self.FIT_NONE
        self.search_rects_per_page: Dict[int, List[fitz.Rect]] = {}

        self._build_ui()

    # -------------------------- UI ----------------------------------------
    def _build_ui(self):

        # ---- Toolbar con navegación y zoom ----
        tb = QToolBar("Acciones")
        tb.setIconSize(QSize(18, 18))
        self.addToolBar(tb)

        # Abrir
        act_open = QAction("Abrir", self)
        act_open.triggered.connect(self.open_pdf)
        tb.addAction(act_open)

        # Anterior / Siguiente (actualizan self.current_page y hacen scroll)
        tb.addSeparator()
        self.current_page = 0
        act_prev = QAction("Anterior", self)
        act_next = QAction("Siguiente", self)
        act_prev.triggered.connect(lambda: self.goto_page(max(0, self.current_page - 1)))
        act_next.triggered.connect(lambda: self.goto_page(self.current_page + 1))
        tb.addAction(act_prev)
        tb.addAction(act_next)

        # Zoom + / -
        tb.addSeparator()
        act_zin = QAction("Zoom +", self)
        act_zout = QAction("Zoom -", self)
        act_zin.triggered.connect(lambda: self._set_zoom(self.zoom * 1.25))
        act_zout.triggered.connect(lambda: self._set_zoom(self.zoom / 1.25))
        tb.addAction(act_zin)
        tb.addAction(act_zout)

        # Atajos Ctrl + + / Ctrl + - (soporta '=' por layouts)
        QShortcut(QKeySequence("Ctrl++"), self, activated=lambda: self._set_zoom(self.zoom * 1.25))
        QShortcut(QKeySequence("Ctrl+="), self, activated=lambda: self._set_zoom(self.zoom * 1.25))
        QShortcut(QKeySequence("Ctrl+-"), self, activated=lambda: self._set_zoom(self.zoom / 1.25))

        # Indicador de zoom y presets
        tb.addSeparator()
        tb.addWidget(QLabel("Zoom:"))
        self.lbl_zoom = QLabel("100%")
        tb.addWidget(self.lbl_zoom)

        self.cmb_zoom = QComboBox()
        self.cmb_zoom.setMinimumWidth(260)
        self.cmb_zoom.addItems([
            "Ajustar página",
            "Ajustar anchura",
            "Expandir la ventana hasta ajustar",
            "50%","60%","70%","80%","90%","100%",
            "150%","200%","250%","300%","350%","400%","450%","500%","550%",
        ])
        self.cmb_zoom.currentTextChanged.connect(self._apply_zoom_preset)
        tb.addWidget(self.cmb_zoom)

        # ---- Status ----
        self.status = QStatusBar()
        self.setStatusBar(self.status)

        # ---- Scroll continuo (una sola área) ----
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)  # IMPORTANTE para scroll fluido
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCentralWidget(self.scroll_area)

        # ---- Dock de búsqueda (igual que lo tenías) ----
        dock = QDockWidget("Búsqueda avanzada", self)
        dock.setMinimumWidth(340)
        panel = QWidget()
        v = QVBoxLayout(panel)

        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Escribe palabras o \"frases exactas\"")
        v.addWidget(QLabel("Consulta de búsqueda:"))
        v.addWidget(self.query_edit)

        self.mode = QComboBox()
        self.mode.addItems(["AND", "OR", "PHRASE", "PROX"])
        v.addWidget(self.mode)

        self.cb_case = QCheckBox("Mayúsculas/minúsculas")
        self.cb_whole = QCheckBox("Palabra completa")
        v.addWidget(self.cb_case)
        v.addWidget(self.cb_whole)

        self.spn_prox = QSpinBox()
        self.spn_prox.setRange(1, 100)
        self.spn_prox.setValue(5)
        v.addWidget(self.spn_prox)

        self.btn_search = QPushButton("Buscar")
        self.btn_search.clicked.connect(self.start_search)
        v.addWidget(self.btn_search)

        self.results = QListWidget()
        self.results.itemClicked.connect(self.goto_result)
        v.addWidget(self.results, 1)

        dock.setWidget(panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        # ---- Atajos de navegación por scroll (trackpad/PgUp/PgDn/Space) ----
        # PAGE DOWN / SPACE: baja una "pantalla"
        QShortcut(QKeySequence("PgDown"), self, activated=lambda: self._scroll_page(direction=+1))
        QShortcut(QKeySequence("Space"), self, activated=lambda: self._scroll_page(direction=+1))
        # PAGE UP / SHIFT+SPACE: sube una "pantalla"
        QShortcut(QKeySequence("PgUp"), self, activated=lambda: self._scroll_page(direction=-1))
        QShortcut(QKeySequence("Shift+Space"), self, activated=lambda: self._scroll_page(direction=-1))
        # HOME / END
        QShortcut(QKeySequence("Home"), self, activated=lambda: self._scroll_to(ext="top"))
        QShortcut(QKeySequence("End"), self, activated=lambda: self._scroll_to(ext="bottom"))

        # Estado inicial
        self.zoom = 1.0
        self.fit_mode = "none"

    # ------------------------- Scroll page ---------------------------------

    def _scroll_page(self, direction: int):
        """Desplaza una 'pantalla' hacia abajo/arriba y actualiza current_page aproximado."""
        bar = self.scroll_area.verticalScrollBar()
        step = max(1, self.scroll_area.viewport().height() - 40)  # solapa para lectura
        bar.setValue(bar.value() + direction * step)
        # actualizar current_page estimando qué página está más centrada
        if hasattr(self, "viewer") and self.viewer:
            center_y = self.scroll_area.verticalScrollBar().value() + self.scroll_area.viewport().height() // 2
            best_idx, best_dist = 0, 1e18
            y = 0
            for i in range(self.doc.page_count):
                w = self.viewer.page_widget(i)
                if not w:
                    continue
                top = w.y()
                h = w.height()
                mid = top + h // 2
                d = abs(mid - center_y)
                if d < best_dist:
                    best_dist, best_idx = d, i
            self.current_page = best_idx

    def _scroll_to(self, ext: str):
        bar = self.scroll_area.verticalScrollBar()
        if ext == "top":
            bar.setValue(bar.minimum())
            self.current_page = 0
        elif ext == "bottom":
            bar.setValue(bar.maximum())
            self.current_page = (self.doc.page_count - 1) if self.doc else 0

    # ------------------------- Event filter -------------------------------------
    def eventFilter(self, obj, event):
        # Reenviar eventos de rueda/touch del visor al scroll_area
        try:
            from PyQt6.QtCore import QEvent
            if obj is getattr(self, "viewer", None) and event.type() in (QEvent.Type.Wheel, QEvent.Type.Gesture):
                return self.scroll_area.event(event)
        except Exception:
            pass
        return super().eventFilter(obj, event)


    # ------------------------- Eventos -------------------------------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._maybe_auto_fit()

    # ------------------------- Abrir/render --------------------------------
    def open_pdf(self):
        fn, _ = QFileDialog.getOpenFileName(self, "Abrir PDF", "", "PDF (*.pdf)")
        if not fn:
            return
        try:
            self.doc = fitz.open(fn)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"No se pudo abrir el PDF:\n{e}")
            return

        self.current_page = 0
        self.zoom = 1.0
        self.fit_mode = "none"

        # Crear visor con scroll continuo (labels apilados)
        self.viewer = PDFScrollViewer(self.doc, zoom=self.zoom)
        self.viewer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.viewer.installEventFilter(self)  # <- para reenviar rueda si hace falta

        self.scroll_area.setWidget(self.viewer)
        self.scroll_area.verticalScrollBar().setSingleStep(24)  # paso fino de rueda
        self._update_zoom_label()

        self.status.showMessage(f"Cargado: {fn} — {self.doc.page_count} páginas", 5000)

    def _render_document(self):
        if not self.doc:
            return
        if self.viewer is None:
            self.viewer = PDFScrollViewer(self.doc, zoom=self.zoom)
            self.scroll_area.setWidget(self.viewer)
        else:
            self.viewer.set_zoom(self.zoom)
        # aplicar resaltados si existen
        if self.search_rects_per_page:
            self.viewer.set_highlights(self.search_rects_per_page)
        self._update_zoom_label()

    # ------------------------- Zoom ----------------------------------------
    def _update_zoom_label(self):
        pct = int(round(self.zoom * 100))
        self.lbl_zoom.setText(f"{pct}%")

    def _set_zoom(self, factor: float):
        # limitar 50%..550%
        factor = max(0.5, min(5.5, factor))
        if not hasattr(self, "viewer") or self.viewer is None:
            self.zoom = factor
            self._update_zoom_label()
            return
        if abs(factor - self.zoom) < 1e-6:
            return
        self.fit_mode = "none"  # salimos del modo auto-ajuste al hacer zoom manual
        self.zoom = factor
        self.viewer.set_zoom(self.zoom)
        self._update_zoom_label()
        # mantener a la vista la página actual
        self.goto_page(getattr(self, "current_page", 0))

    def _apply_zoom_preset(self, text: str):
        if not self.doc:
            if text.endswith('%'):
                try:
                    self.zoom = int(text[:-1]) / 100.0
                    self._update_zoom_label()
                except Exception:
                    pass
            return

        # tamaño de la primera página a zoom 1.0
        page0 = self.doc.load_page(0)
        rect = page0.rect
        page_w, page_h = rect.width, rect.height

        vp = self.scroll_area.viewport().size()
        vp_w = max(1, vp.width() - 16)
        vp_h = max(1, vp.height() - 16)

        if text == "Ajustar anchura":
            self.fit_mode = "width"
            scale = vp_w / page_w
            self._set_zoom(scale)
            return
        elif text in ("Ajustar página", "Expandir la ventana hasta ajustar"):
            self.fit_mode = "page"
            scale = min(vp_w / page_w, vp_h / page_h)
            self._set_zoom(scale)
            return
        else:
            if text.endswith('%'):
                try:
                    pct = int(text[:-1])
                    self.fit_mode = "none"
                    self._set_zoom(pct / 100.0)
                    return
                except ValueError:
                    pass

    def _maybe_auto_fit(self):
        if not self.doc or self.fit_mode == "none":
            return

        page0 = self.doc.load_page(0)
        rect = page0.rect
        page_w, page_h = rect.width, rect.height

        vp = self.scroll_area.viewport().size()
        vp_w = max(1, vp.width() - 16)
        vp_h = max(1, vp.height() - 16)

        if self.fit_mode == "width":
            scale = vp_w / page_w
        else:  # "page"
            scale = min(vp_w / page_w, vp_h / page_h)

        if abs(scale - self.zoom) > 1e-3 and hasattr(self, "viewer") and self.viewer:
            self.zoom = scale
            self.viewer.set_zoom(self.zoom)
            self._update_zoom_label()

    # ------------------------- Búsqueda ------------------------------------
    def _params_from_ui(self) -> SearchParams:
        mode_map = {
            0: 'AND',
            1: 'OR',
            2: 'PHRASE',
            3: 'PROX',
        }
        return SearchParams(
            query=normalize_space(self.query_edit.text()),
            mode=mode_map[self.mode.currentIndex()],
            case_sensitive=self.cb_case.isChecked(),
            whole_words=self.cb_whole.isChecked(),
            proximity=self.spn_prox.value(),
        )

    def start_search(self):
        if not self.doc:
            QMessageBox.information(self, "Abrir PDF", "Primero abre un archivo PDF.")
            return
        params = self._params_from_ui()
        if not params.query:
            return

        self.results.clear()
        self.lbl_progress.setText("Buscando…")
        self.btn_search.setEnabled(False)

        self.worker_thread = QThread(self)
        self.worker = SearchWorker(self.doc, params)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._on_search_progress)
        self.worker.done.connect(lambda hits: self._on_search_done(hits, params))
        self.worker.done.connect(self.worker_thread.quit)
        self.worker_thread.start()

    def _on_search_progress(self, cur: int, total: int):
        self.lbl_progress.setText(f"{cur}/{total}")

    def _on_search_done(self, hits: List[SearchHit], params: SearchParams):
        self.lbl_progress.setText(f"{len(hits)} páginas")
        self.btn_search.setEnabled(True)

        # construir resaltados por página
        self.search_rects_per_page.clear()

        # preparar términos para rectángulos
        if params.mode == 'PHRASE':
            terms = [params.query]
        else:
            terms = [t for t in params.query.split() if t]

        for h in hits:
            # item en la lista
            item = QListWidgetItem(f"Página {h.page + 1}: {h.snippet}")
            item.setData(Qt.ItemDataRole.UserRole, h.page)
            self.results.addItem(item)

            # rectángulos
            rects: List[fitz.Rect] = []
            page = self.doc.load_page(h.page)
            for t in terms:
                try:
                    rects.extend(page.search_for(
                        t, match_case=params.case_sensitive, match_whole=params.whole_words
                    ) or [])
                except TypeError:
                    # Compatibilidad con versiones antiguas de PyMuPDF
                    rects.extend(page.search_for(t) or [])
            self.search_rects_per_page[h.page] = rects

        # aplicar resaltados
        if self.viewer:
            self.viewer.set_highlights(self.search_rects_per_page)

        if hits:
            # desplazar a la primera coincidencia
            self.goto_page(hits[0].page)
        else:
            QMessageBox.information(self, "Sin resultados", "No se encontraron páginas que cumplan los criterios.")

    # ------------------------- Navegación de resultados ---------------------
    def goto_page(self, page_index: int):
        if not hasattr(self, "viewer") or self.viewer is None or not self.doc:
            return
        page_index = max(0, min(page_index, self.doc.page_count - 1))
        w = self.viewer.page_widget(page_index)
        if w:
            self.scroll_area.ensureWidgetVisible(w, xMargin=0, yMargin=20)
            self.current_page = page_index

    def goto_result(self, item: QListWidgetItem):
        page = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page, int):
            self.goto_page(page)

# ============================== main =======================================

def main():
    app = QApplication(sys.argv)
    w = PDFAdvancedReader()
    w.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
