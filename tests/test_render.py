"""Tests for the asynchronous render pool.

The critical regression test here: results must reach the GUI thread even
though workers are plain Python threads (the 1.x code used QTimer.singleShot
from such threads and silently never delivered anything).
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QImage

from pdfar.render import RenderPool, RenderResult


class Collector(QObject):
    """Receives render results on the GUI thread via a bound method."""

    def __init__(self):
        super().__init__()
        self.results = []

    def on_result(self, result):
        self.results.append(result)


def wait_until(app, predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_results_are_delivered_to_gui_thread(qapp, sample_pdf):
    pool = RenderPool(sample_pdf, workers=2)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)   # QObject slot => queued
    try:
        assert pool.request(0, 1.0, priority=0) is True
        assert wait_until(qapp, lambda: len(collector.results) >= 1), \
            "render result never reached the GUI thread"
        res = collector.results[0]
        assert isinstance(res, RenderResult)
        assert res.ok and res.png and res.width > 0 and res.height > 0
        img = QImage()
        assert img.loadFromData(res.png, "PNG")
    finally:
        pool.shutdown(wait=True)


def test_worker_threads_have_no_qt_event_loop_but_still_work(qapp, sample_pdf):
    """Regression: the 1.x delivery mechanism was QTimer.singleShot(0) from a
    ThreadPoolExecutor callback.  Worker threads have no event loop, so the
    timer never fired and no page was ever shown."""
    pool = RenderPool(sample_pdf, workers=3)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        for i in range(3):
            pool.request(i, 1.0, priority=i)
        assert wait_until(qapp, lambda: len(collector.results) >= 3)
        assert all(r.ok for r in collector.results)
    finally:
        pool.shutdown(wait=True)


def test_duplicate_requests_are_dropped(qapp, sample_pdf):
    pool = RenderPool(sample_pdf, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        assert pool.request(0, 1.0) is True
        assert pool.request(0, 1.0) is False      # same key, already in flight
        assert pool.request(0, 2.0) is True       # different zoom is fine
        assert wait_until(qapp, lambda: len(collector.results) >= 2)
    finally:
        pool.shutdown(wait=True)


def test_priority_order_prefers_low_priority_number(qapp, sample_pdf):
    pool = RenderPool(sample_pdf, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        # queue back-to-front with the "visible" page last
        for i in range(1, 6):
            pool.request(i, 1.0, priority=5)
        pool.request(0, 1.0, priority=0)
        assert wait_until(qapp, lambda: len(collector.results) >= 6)
        pages = [r.page for r in collector.results]
        assert pages[0] == 0, f"page 0 (priority 0) should render first, got {pages}"
    finally:
        pool.shutdown(wait=True)


def test_large_document_many_pages(qapp, big_pdf):
    pool = RenderPool(big_pdf, workers=4)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        n = fitz.open(big_pdf).page_count
        for i in range(n):
            pool.request(i, 0.5, priority=3)
        assert wait_until(qapp, lambda: len(collector.results) >= n, timeout=30)
        assert all(r.ok for r in collector.results)
    finally:
        pool.shutdown(wait=True)


def test_rotation_is_applied(qapp, sample_pdf):
    pool = RenderPool(sample_pdf, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        pool.request(0, 1.0, rotation=0)
        pool.request(0, 1.0, rotation=90)
        assert wait_until(qapp, lambda: len(collector.results) >= 2)
        by_rot = {r.rotation: r for r in collector.results}
        assert by_rot[0].width != by_rot[90].width or by_rot[0].height != by_rot[90].height
    finally:
        pool.shutdown(wait=True)
