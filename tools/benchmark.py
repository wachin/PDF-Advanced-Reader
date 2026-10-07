#!/usr/bin/env python3
"""PDFAR Baseline Benchmark Tool.

Measures opening, rendering, memory, and queuing performance metrics.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import resource
from typing import Dict, Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from PyQt6.QtWidgets import QApplication
from pdfar.main import MainWindow


def get_peak_rss_mb() -> float:
    """Return peak RSS in MB."""
    ru = resource.getrusage(resource.RUSAGE_SELF)
    # On Linux, ru_maxrss is in kilobytes
    if sys.platform.startswith("linux"):
        return ru.ru_maxrss / 1024.0
    return ru.ru_maxrss / (1024.0 * 1024.0)


def run_benchmark(pdf_path: str) -> Dict[str, Any]:
    """Run comprehensive performance benchmark for a given PDF."""
    results: Dict[str, Any] = {}
    
    app = QApplication.instance() or QApplication(sys.argv)
    
    # 1. Startup / Init
    t0 = time.perf_counter()
    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    app.processEvents()
    t_win_init = time.perf_counter() - t0
    
    # Track jobs queued during open
    t_open_start = time.perf_counter()
    ok = win.open_document(pdf_path)
    t_open_done = time.perf_counter() - t_open_start
    
    if not ok or not win.view:
        print(f"Error: failed to open {pdf_path}")
        win.close()
        return {}
    
    view = win.view
    pool = view.pool
    
    results["pdf_path"] = pdf_path
    results["page_count"] = view.page_count
    results["open_document_time_sec"] = t_open_done
    results["jobs_queued_during_open"] = pool.pending()
    
    # Time to first visible page render
    t_render_start = time.perf_counter()
    
    def first_page_ready():
        return view.cache.get(0, view.zoom, view.rotation) is not None or any(
            img is not None for tiles_dict in view._tiles.values() for img in tiles_dict.values()
        )
    
    deadline = time.perf_counter() + 30.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if first_page_ready():
            break
        time.sleep(0.005)
    
    t_first_page = time.perf_counter() - t_render_start
    results["time_to_first_page_sec"] = t_first_page
    
    # Time to complete viewport
    t_viewport_start = time.perf_counter()
    def viewport_ready():
        first, last = view.visible_range()
        for p in range(first, last + 1):
            if view.cache.get(p, view.zoom, view.rotation) is None and p not in view._tiles:
                return False
        return True

    deadline = time.perf_counter() + 30.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if viewport_ready():
            break
        time.sleep(0.005)
    t_viewport = time.perf_counter() - t_viewport_start
    results["time_to_viewport_complete_sec"] = t_viewport
    
    # Jump to distant page (e.g., page 200 or last page)
    target_page = min(200, view.page_count - 1)
    t_jump_start = time.perf_counter()
    win._goto(target_page)
    
    def target_page_ready():
        return view.cache.get(target_page, view.zoom, view.rotation) is not None or target_page in view._tiles
    
    deadline = time.perf_counter() + 30.0
    while time.perf_counter() < deadline:
        app.processEvents()
        if target_page_ready():
            break
        time.sleep(0.005)
    t_jump = time.perf_counter() - t_jump_start
    results["jump_to_distant_page_sec"] = t_jump
    
    # Zoom operations (100% -> 200% -> 300%)
    t_zoom_start = time.perf_counter()
    win._set_zoom(2.0)
    app.processEvents()
    win._set_zoom(3.0)
    app.processEvents()
    t_zoom = time.perf_counter() - t_zoom_start
    results["zoom_sequence_sec"] = t_zoom
    
    # Peak Memory
    results["peak_rss_mb"] = get_peak_rss_mb()
    
    win._close_document()
    win.close()
    win.deleteLater()
    app.processEvents()
    
    return results


def main():
    parser = argparse.ArgumentParser(description="PDFAR Benchmark")
    parser.add_argument("pdf", nargs="?", default="external/LibreOffice-Getting-Started-Guides-PDF-BackUp/GS262-GettingStarted.pdf")
    args = parser.parse_args()

    if not os.path.exists(args.pdf):
        print(f"PDF file not found: {args.pdf}")
        sys.exit(1)

    print(f"=== Running Benchmark on {args.pdf} ===")
    res = run_benchmark(args.pdf)
    
    print("\n--- RESULTS ---")
    for k, v in res.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
