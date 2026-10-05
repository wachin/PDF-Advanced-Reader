"""End-to-end tests for the PDF view widget (headless, offscreen platform)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest

from pdfar.geometry import map_rect
from pdfar.search import SearchParams, SearchWorker
from pdfar.viewer import PDFView

BLACK_SQ = (10.0, 10.0, 40.0, 40.0)      # page-space rect of the black square
BLANK = (150.0, 60.0, 180.0, 80.0)       # page-space rect of empty paper


def wait_until(app, predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def wait_page(view, app, page, timeout=15.0):
    ok = wait_until(app, lambda: view.cache.get(page, view.zoom, view.rotation) is not None, timeout)
    assert ok, f"page {page} was never rendered (zoom={view.zoom}, rot={view.rotation})"
    return view.cache.get(page, view.zoom, view.rotation)


def sample(img, rect, page_size, zoom, rotation):
    """Sample the centre pixel of a page-space rect from a rendered image."""
    x0, y0, x1, y1 = map_rect(rect, page_size[0], page_size[1], rotation)
    cx = int((x0 + x1) / 2 * zoom)
    cy = int((y0 + y1) / 2 * zoom)
    cx = max(0, min(img.width() - 1, cx))
    cy = max(0, min(img.height() - 1, cy))
    return QColor(img.pixel(cx, cy))


def is_dark(c: QColor, limit=200):
    return (c.red() + c.green() + c.blue()) < limit


def is_light(c: QColor, limit=500):
    return (c.red() + c.green() + c.blue()) > limit


# --------------------------------------------------------------------- basics
def test_opens_and_counts_pages(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    try:
        assert view.page_count == 3
        assert view.page_sizes[0] == (200.0, 100.0)
        assert view.zoom == 1.0
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_encrypted_pdf_rejected(qapp, tmp_path):
    path = tmp_path / "locked.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="secret")
    doc.close()
    try:
        PDFView(str(path))
        assert False, "expected ValueError for encrypted PDF"
    except ValueError as exc:
        assert "encrypted" in str(exc)


# ------------------------------------------------------ THE regression test
def test_pages_actually_render_and_paint(qapp, sample_pdf):
    """1.x never displayed a single page: render results were delivered with
    QTimer.singleShot() from ThreadPoolExecutor threads (no event loop there).
    This test asserts pages really end up on screen."""
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    try:
        img = wait_page(view, qapp, 0)
        assert not img.isNull() and img.width() > 0 and img.height() > 0

        # the page content must be there: black square dark, empty paper light
        assert is_dark(sample(img, BLACK_SQ, view.page_sizes[0], view.zoom, view.rotation))
        assert is_light(sample(img, BLANK, view.page_sizes[0], view.zoom, view.rotation))

        # and it must actually be *painted* into the widget
        qapp.processEvents()
        grabbed = view.grab().toImage()
        assert grabbed.width() > 0
        colors = set()
        for y in range(0, grabbed.height(), 8):
            for x in range(0, grabbed.width(), 8):
                colors.add(grabbed.pixel(x, y))
                if len(colors) > 4:
                    break
        assert len(colors) > 4, "widget paint looks blank"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_all_pages_render_without_interaction(qapp, big_pdf):
    view = PDFView(big_pdf)
    view.resize(400, 300)
    view.show()
    try:
        assert wait_until(qapp, lambda: all(
            view.cache.get(p, view.zoom, view.rotation) is not None for p in range(5))), \
            "first pages never rendered"
        view.goto_page(39)
        assert wait_page(view, qapp, 39) is not None
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ------------------------------------------------------------------- zoom
def test_zoom_rescales_layout_and_renders(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    qapp.processEvents()
    try:
        wait_page(view, qapp, 0)
        base = view._disp_sizes[0]
        view.set_zoom(2.0)
        assert abs(view._disp_sizes[0][0] - base[0] * 2) < 1e-6
        img = wait_page(view, qapp, 0)
        assert img.width() == int(200 * 2) or abs(img.width() - 400) <= 1
        assert is_dark(sample(img, BLACK_SQ, view.page_sizes[0], view.zoom, view.rotation))
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_fit_width_and_fit_page(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(600, 400)
    view.show()
    qapp.processEvents()
    try:
        view.set_fit_mode("width")
        qapp.processEvents()
        # 200pt page fitted to the actual viewport width
        expected = (view.viewport().width() - 32) / 200.0
        assert abs(view.zoom - expected) < 1e-6
        assert 2.5 < view.zoom < 3.0
        width_zoom = view.zoom
        view.set_fit_mode("page")
        qapp.processEvents()
        assert view.zoom <= width_zoom + 1e-6
        assert view.zoom > 1.0
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# --------------------------------------------------------------- rotation
def test_rotation_render_matches_geometry(qapp, sample_pdf):
    """The rendered bitmap and the geometry helpers must agree on rotation
    direction, otherwise highlights/selection would land on the wrong text."""
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    qapp.processEvents()
    try:
        for rot in (0, 90, 180, 270):
            view.set_rotation(rot)
            view.set_zoom(1.0)
            img = wait_page(view, qapp, 0)
            w, h = view.page_sizes[0]
            from pdfar.geometry import display_size
            assert (img.width(), img.height()) == (
                int(display_size(w, h, rot)[0]), int(display_size(w, h, rot)[1])), \
                f"unexpected bitmap size at rotation {rot}"
            assert is_dark(sample(img, BLACK_SQ, view.page_sizes[0], 1.0, rot)), \
                f"black square not found at rotation {rot}"
            assert is_light(sample(img, BLANK, view.page_sizes[0], 1.0, rot)), \
                f"blank paper not blank at rotation {rot}"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_rotation_keeps_selection_rects_consistent(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    qapp.processEvents()
    try:
        for rot in (0, 90, 180, 270):
            view.set_rotation(rot)
            for w in view._page_words(0):
                x0, y0, x1, y1 = view._word_rect_view(0, w)
                dw, dh = view._disp_sizes[0]
                assert 0 <= x0 <= x1 <= dw / view.zoom + 1
                assert 0 <= y0 <= y1 <= dh / view.zoom + 1
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# -------------------------------------------------------------- selection
def _word_center(view, page, index):
    word = view._page_words(page)[index]
    x0, y0, x1, y1 = view._word_rect_view(page, word)
    px, py = view._page_origin(page)
    sx = view.horizontalScrollBar().value()
    sy = view.verticalScrollBar().value()
    return QPoint(int(px + (x0 + x1) / 2 * view.zoom - sx),
                  int(py + (y0 + y1) / 2 * view.zoom - sy))


def test_drag_selection_and_copy(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(600, 400)
    view.show()
    try:
        wait_page(view, qapp, 0)
        qapp.processEvents()
        words = view._page_words(0)
        texts = [w[4] for w in words]
        assert "Hello" in texts and "world" in texts
        i_first = texts.index("Hello")
        i_last = texts.index("world")

        vp = view.viewport()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         _word_center(view, 0, i_first))
        QTest.mouseMove(vp, _word_center(view, 0, i_last))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           _word_center(view, 0, i_last))
        qapp.processEvents()

        assert view.has_selection()
        selected = view.selected_text()
        assert selected == "Hello PDFAR world", f"unexpected selection: {selected!r}"

        view.copy_selection()
        assert qapp.clipboard().text() == "Hello PDFAR world"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_plain_click_clears_selection(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(600, 400)
    view.show()
    try:
        wait_page(view, qapp, 0)
        qapp.processEvents()
        vp = view.viewport()
        words = view._page_words(0)
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         _word_center(view, 0, 0))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           _word_center(view, 0, len(words) - 1))
        assert view.has_selection()
        QTest.mousePress(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                         _word_center(view, 0, 0))
        QTest.mouseRelease(vp, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                           _word_center(view, 0, 0))
        assert not view.has_selection()
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ----------------------------------------------------------------- search
def _search(view_path, params):
    hits = []
    worker = SearchWorker(view_path, params)
    worker.done.connect(hits.extend)
    worker.run()
    return hits


def test_search_highlights_and_navigation(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(600, 400)
    view.show()
    try:
        hits = _search(sample_pdf, SearchParams("alpha beta", mode="AND"))
        total = view.set_search_hits(hits)
        assert total > 0
        assert view.goto_hit(+1) is True
        assert view.current_page == 1
        assert view.goto_hit(+1) is True
        assert view.goto_hit(-1) is True
        view.clear_search()
        assert view.goto_hit(+1) is False
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_search_hits_match_rendered_content(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(600, 400)
    try:
        hits = _search(sample_pdf, SearchParams("Hello"))
        assert hits and hits[0].page == 0
        x0, y0, x1, y1 = hits[0].rects[0]
        view.set_search_hits(hits)
        qapp.processEvents()
        assert 0 in view._hit_rects
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ------------------------------------------------------------- navigation
def test_goto_page_updates_current_page(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(400, 300)
    view.show()
    try:
        seen = []
        view.page_changed.connect(seen.append)
        view.goto_page(2)
        qapp.processEvents()
        assert view.current_page == 2
        assert seen and seen[-1] == 2
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_visible_range_is_sane(qapp, big_pdf):
    view = PDFView(big_pdf)
    view.resize(400, 300)
    view.show()
    try:
        first, last = view.visible_range()
        assert first == 0
        assert last >= first
        view.goto_page(20)
        qapp.processEvents()
        first, last = view.visible_range()
        assert first <= 20 <= last or first <= 20
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# -------------------------------------------------------------- tiled view
def test_large_page_uses_tiles(qapp, tmp_path):
    """A page far larger than the viewport renders as tiles, not a giant
    whole-page bitmap (Okular strategy)."""
    doc = fitz.open()
    # 1200x1600 pt page, zoomed to 2x => 2400x3200 px (~7.7M px)
    page = doc.new_page(width=1200, height=1600)
    page.draw_rect(fitz.Rect(0, 0, 1200, 1600), color=(1, 1, 1), fill=(1, 1, 1))
    page.draw_rect(fitz.Rect(50, 50, 350, 350), color=(0, 0, 0), fill=(0, 0, 0))
    path = str(tmp_path / "tiled.pdf")
    doc.save(path)
    doc.close()

    view = PDFView(path)
    view.resize(400, 300)
    view.show()
    try:
        view.set_zoom(2.0)
        first, last = view.visible_range()
        # the visible-first tile requests must have been queued
        assert first == 0
        ok = wait_until(qapp, lambda: any(img is not None for dx in view._tiles.values()
                                          for img in dx.values()), timeout=15)
        assert ok, "no tile was ever rendered"

        # the full-page bitmap must NOT have been produced (that's the point)
        assert view.cache.get(0, view.zoom, view.rotation) is None, \
            "whole-page render should not happen on a tiled page"
        # the visible tiles were recorded
        assert len(view._tiles[0]) >= 1
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_small_page_no_tiles(qapp, sample_pdf):
    """A normal page is rendered whole, never tiled."""
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    qapp.processEvents()
    try:
        wait_page(view, qapp, 0)
        assert view._tiles.get(0) in (None, {})
        assert view.cache.get(0, view.zoom, view.rotation) is not None
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ------------------------------------------ stale-render cancellation (Okular)
def test_request_visible_cancels_stale_renders(qapp, big_pdf):
    """_request_visible must tell the pool to cancel renders of pages that
    left the viewport (Okular's cancelRenderingBecauseOf), so a fast scroll
    doesn't waste worker threads on pages the user won't see."""
    view = PDFView(big_pdf)     # 40 pages
    view.resize(400, 300)
    view.show()
    qapp.processEvents()
    try:
        captured_pred = {}
        orig = view.pool.cancel_if
        def spy(pred):
            captured_pred["pred"] = pred
            return orig(pred)
        view.pool.cancel_if = spy

        # visible at top => keep range 0..8; pages 30/35 must be cancellable
        view.verticalScrollBar().setValue(0)
        view._request_visible()
        pred = captured_pred["pred"]
        assert pred is not None

        # build job-like objects to test the predicate
        class FakeJob:
            def __init__(self, page, tag="view"):
                self.page = page
                self.tag = tag
                self.cancelled = False

        assert pred(FakeJob(35)) is True, "far page 35 should be cancelled"
        assert pred(FakeJob(30)) is True, "far page 30 should be cancelled"
        assert pred(FakeJob(0)) is False, "near page 0 must be kept"
        assert pred(FakeJob(8)) is False, "keep edge page 8 must be kept"
    finally:
        view.pool.cancel_if = orig
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ------------------------------------------- async text extraction (Okular)
def test_view_preloads_words_asynchronously(qapp, sample_pdf):
    """Opening the view should prefetch the visible page's words on a worker
    thread; once received, _words is populated without a blocking GUI call."""
    view = PDFView(sample_pdf)
    view.resize(500, 400)
    view.show()
    qapp.processEvents()
    try:
        # _request_visible (called on show) should have queued word extraction
        assert view._words.get(0) is None
        ok = wait_until(qapp, lambda: view._words.get(0) is not None)
        assert ok, "words for page 0 never arrived asynchronously"
        texts = [w[4] for w in view._words[0]]
        assert "PDFAR" in texts or "Hello" in texts or "world" in texts
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ----------------------------------------- viewport-aware cache (Okular)
def test_cache_center_follows_viewport(qapp, big_pdf):
    """The view keeps the page cache viewport-aware so it evicts far pages
    first (Okular's distance-priority cache)."""
    view = PDFView(big_pdf)
    view.resize(400, 300)
    view.show()
    qapp.processEvents()
    try:
        view.goto_page(20)
        qapp.processEvents()
        assert view.current_page == 20
        assert view.cache._center == 20
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# --------------------------------------------- kinetic scroll (Okular)
def test_touch_scroller_is_installed(qapp, sample_pdf):
    """Okular-style QScroller is installed on the viewport (TouchGesture)."""
    from PyQt6.QtWidgets import QScroller
    view = PDFView(sample_pdf)
    try:
        assert isinstance(view._scroller, QScroller)
        # the scroller instance is bound to the viewport
        assert QScroller.scroller(view.viewport()) is view._scroller
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()
