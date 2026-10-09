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
        assert res.ok and (res.samples or res.png) and res.width > 0 and res.height > 0
        img = res.to_qimage()
        assert not img.isNull()
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


def test_clip_renders_subregion(qapp, tmp_path):
    """A clipped render returns a smaller image than the full page."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)   # 400x400 pt
    page.draw_rect(fitz.Rect(0, 0, 400, 400), color=(0, 0, 0), fill=(1, 1, 1))
    path = str(tmp_path / "clip.pdf")
    doc.save(path)
    doc.close()

    pool = RenderPool(path, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        # full page at zoom 1 -> 400x400 px
        pool.request(0, 1.0, priority=0)
        # top-left half clip (0..200 pt) -> 200x200 px
        pool.request(0, 1.0, priority=0, clip=(0, 0, 200, 200))
        assert wait_until(qapp, lambda: len(collector.results) >= 2)
        full = next(r for r in collector.results if r.clip is None)
        clipped = next(r for r in collector.results if r.clip is not None)
        assert (full.width, full.height) == (400, 400)
        assert (clipped.width, clipped.height) == (200, 200)
        assert clipped.clip == (0, 0, 200, 200)
    finally:
        pool.shutdown(wait=True)


def test_clip_with_different_zoom_has_distinct_key(qapp, tmp_path):
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    path = str(tmp_path / "clip_zoom.pdf")
    doc.save(path)
    doc.close()

    pool = RenderPool(path, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        assert pool.request(0, 1.0, clip=(0, 0, 100, 100)) is True
        assert pool.request(0, 1.0, clip=(0, 0, 100, 100)) is False   # dup
        assert pool.request(0, 2.0, clip=(0, 0, 100, 100)) is True    # different zoom
        assert wait_until(qapp, lambda: len(collector.results) >= 2)
    finally:
        pool.shutdown(wait=True)


def test_request_words_delivers_text(qapp, sample_pdf):
    """request_words extracts the page's words on a worker thread and delivers
    them in RenderResult.words with tag='text' (no pixmap)."""
    pool = RenderPool(sample_pdf, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        assert pool.request_words(0) is True
        assert wait_until(qapp, lambda: len(collector.results) >= 1), \
            "word extraction result never arrived"
        res = collector.results[0]
        assert isinstance(res, RenderResult)
        assert res.tag == "text"
        assert res.ok and res.png is None
        assert res.words is not None
        texts = [w[4] for w in res.words]
        assert "Hello" in texts and "world" in texts
    finally:
        pool.shutdown(wait=True)


def test_request_words_deduplicated(qapp, sample_pdf):
    """Two request_words for the same page collapse into one in-flight job."""
    pool = RenderPool(sample_pdf, workers=1)
    collector = Collector()
    pool.result_ready.connect(collector.on_result)
    try:
        assert pool.request_words(0) is True
        assert pool.request_words(0) is False   # already in flight
        assert wait_until(qapp, lambda: len(collector.results) >= 1)
    finally:
        pool.shutdown(wait=True)


def test_cancel_if_marks_queued_jobs(qapp, tmp_path):
    """cancel_if() cancels queued jobs matching the predicate (Okular's
    cancelRenderingBecauseOf), but leaves non-matching jobs intact."""
    doc = fitz.open()
    doc.new_page(width=200, height=200)
    path = str(tmp_path / "cancel.pdf")
    doc.save(path)
    doc.close()

    import threading
    barrier = threading.Event()

    # A single worker thread: jobs stay queued until the worker is free.
    pool = RenderPool(path, workers=1)

    class SlowCollector(Collector):
        def __init__(self, event):
            super().__init__()
            self._event = event

        def on_result(self, result):
            # let the worker finish the FIRST job, then hold it briefly
            super().on_result(result)
            if len(self.results) == 1:
                self._event.wait(2.0)

    collector = SlowCollector(barrier)
    pool.result_ready.connect(collector.on_result)

    try:
        # queue page 0 (will run first) then pages 2..4 (sit queued)
        assert pool.request(0, 1.0, priority=0) is True
        for p in (2, 3, 4):
            assert pool.request(p, 1.0, priority=1) is True

        # cancel the queued far pages (keep only page 2)
        n = pool.cancel_if(lambda job: job.page in (3, 4))
        assert n == 2, f"expected 2 cancelled, got {n}"

        # release the first job so the worker drains the heap
        barrier.set()
        # only page 0 and page 2 should be delivered; 3 and 4 were cancelled
        assert wait_until(qapp, lambda: len(collector.results) >= 2)
        delivered = {r.page for r in collector.results}
        assert delivered == {0, 2}, f"unexpected delivered pages: {delivered}"
    finally:
        pool.shutdown(wait=True)
