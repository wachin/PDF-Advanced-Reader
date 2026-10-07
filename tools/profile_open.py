#!/usr/bin/env python3
"""Profile opening path breakdown step-by-step."""

import time
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pymupdf as fitz
from PyQt6.QtWidgets import QApplication
from pdfar.viewer import PDFView
from pdfar.sidebar import Sidebar
from pdfar.annotations import AnnotationManager
from pdfar.bookmarks import BookmarkManager

pdf_path = "external/LibreOffice-Getting-Started-Guides-PDF-BackUp/GS262-GettingStarted.pdf"

print(f"Profiling open path for {pdf_path}...")
app = QApplication(sys.argv)

t0 = time.perf_counter()
doc = fitz.open(pdf_path)
t_fitz_open = time.perf_counter() - t0
print(f"1. fitz.open(): {t_fitz_open:.4f}s (page_count={doc.page_count})")

# Measure PDFView init
t0 = time.perf_counter()
view = PDFView(pdf_path)
t_view_init = time.perf_counter() - t0
print(f"2. PDFView.__init__ (includes 540 page rect loads & RenderPool): {t_view_init:.4f}s")

# Measure Sidebar init
t0 = time.perf_counter()
sidebar = Sidebar(view.doc, view.pool, pdf_path)
t_sidebar_init = time.perf_counter() - t0
print(f"3. Sidebar.__init__ (includes BookmarkManager, 540 page rect loads for thumbs, TOC, & 540 thumb requests queued): {t_sidebar_init:.4f}s")

# Measure AnnotationManager init
t0 = time.perf_counter()
annot_mgr = AnnotationManager(pdf_path, view.doc)
t_annot_init = time.perf_counter() - t0
print(f"4. AnnotationManager.__init__ (scans all 540 pages for annots): {t_annot_init:.4f}s")

print(f"Total synchronous open time: {t_fitz_open + t_view_init + t_sidebar_init + t_annot_init:.4f}s")
print(f"Jobs pending in pool after open: {view.pool.pending()}")

view.shutdown()
sidebar.shutdown()
doc.close()
