"""Page view modes: layout, pairing, hit testing, fit, navigation, state."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QPoint

from pdfar.viewer import PDFView


def _make_pdf(path, pages, width=200, height=100):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((10, 40), f"Page {i + 1} alpha beta", fontsize=12)
        page.insert_text((10, 70), f"row {i + 1} gamma", fontsize=10)
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def five_pdf(tmp_path):
    return _make_pdf(tmp_path / "five.pdf", 5)


@pytest.fixture
def six_pdf(tmp_path):
    return _make_pdf(tmp_path / "six.pdf", 6)


@pytest.fixture
def view_factory(qapp):
    views = []

    def make(path, width=600, height=400):
        view = PDFView(path)
        view.resize(width, height)
        view.show()
        qapp.processEvents()
        views.append(view)
        return view

    yield make
    for view in views:
        view.shutdown()
        view.deleteLater()
    qapp.processEvents()


def wait_until(app, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_continuous_layout_matches_legacy_geometry(view_factory, sample_pdf,
                                                   qapp):
    view = view_factory(sample_pdf)
    assert view.view_mode == "continuous"
    assert view._page_tops == [16.0, 128.0, 240.0]
    assert view._content_h == 356.0
    assert view._content_w == 232.0
    assert view.visible_range() == (0, 2)
    # Pages stay horizontally centered in the content column.
    x0, y0 = view._page_origin(0)
    assert x0 == view._x_offset() + 16
    assert y0 == 16.0
    assert view._page_origin(2) == (x0, 240.0)


def test_single_page_centers_and_navigates(view_factory, sample_pdf,
                                           qapp):
    view = view_factory(sample_pdf)
    view.set_view_mode("single")
    qapp.processEvents()
    assert view._rows == [[0]]
    assert view._page_rects[1] is None
    x, y, w, h = view._page_rects[0]
    assert (w, h) == (200.0, 100.0)
    assert abs(x - (view.viewport().width() - w) / 2) <= 1
    assert abs(y - (view.viewport().height() - h) / 2) <= 1
    assert view.verticalScrollBar().maximum() == 0
    assert view.horizontalScrollBar().maximum() == 0

    view.goto_page(2)
    qapp.processEvents()
    assert view.current_page == 2
    assert view._rows == [[2]]
    assert view._page_rects[0] is None
    assert view._page_rects[2] is not None


def test_facing_pairs_pages(view_factory, five_pdf, qapp):
    view = view_factory(five_pdf)
    view.set_view_mode("facing")
    qapp.processEvents()
    assert view._rows == [[0, 1], [2, 3], [4]]

    left = view._page_rects[0]
    right = view._page_rects[1]
    assert left is not None and right is not None
    assert left[0] + left[2] + 12 == right[0]
    assert abs(left[1] - right[1]) <= 1

    view.goto_page(4)
    qapp.processEvents()
    lone = view._page_rects[4]
    assert lone is not None
    # The odd last page sits in the left column, right edge at the gutter.
    assert abs((lone[0] + lone[2]) - (view._content_w / 2 - 6)) <= 1
    assert view._page_rects[2] is None


def test_facing_centered_first_and_last(view_factory, six_pdf, five_pdf,
                                        qapp):
    view = view_factory(six_pdf)
    view.set_view_mode("facing_centered")
    qapp.processEvents()
    assert view._rows == [[0], [1, 2], [3, 4], [5]]
    first = view._page_rects[0]
    assert abs(first[0] - (view.viewport().width() - first[2]) / 2) <= 1

    view.goto_page(5)
    qapp.processEvents()
    last = view._page_rects[5]
    assert last is not None
    assert abs(last[0] - (view.viewport().width() - last[2]) / 2) <= 1

    view2 = view_factory(five_pdf)
    view2.set_view_mode("facing_centered")
    qapp.processEvents()
    assert view2._rows == [[0], [1, 2], [3, 4]]


def test_overview_three_column_grid(view_factory, tmp_path, qapp):
    path = _make_pdf(tmp_path / "seven.pdf", 7)
    view = view_factory(path)
    view.set_view_mode("overview")
    qapp.processEvents()
    assert view._rows == [[0, 1, 2], [3, 4, 5], [6]]

    rects = [view._page_rects[i] for i in range(3)]
    assert all(rect is not None for rect in rects)
    assert rects[0][0] < rects[1][0] < rects[2][0]
    assert view._page_rects[6] is None

    view.goto_page(6)
    qapp.processEvents()
    lone = view._page_rects[6]
    assert lone is not None
    assert abs(lone[0] - (view._content_w - lone[2]) / 2) <= 1


def test_hit_testing_selects_right_page_word(view_factory, sample_pdf,
                                             qapp):
    view = view_factory(sample_pdf)
    view.set_view_mode("facing")
    qapp.processEvents()
    words = view._page_words(1)
    assert words
    x0, y0, x1, y1 = view._word_rect_view(1, words[0])
    px, py = view._page_origin(1)
    pos = QPoint(
        int(px + (x0 + x1) / 2 * view.zoom
            - view.horizontalScrollBar().value()),
        int(py + (y0 + y1) / 2 * view.zoom
            - view.verticalScrollBar().value()))
    hit = view._word_at(pos)
    assert hit is not None and hit[0] == 1


def test_fit_width_and_page_use_the_spread(view_factory, sample_pdf, qapp):
    view = view_factory(sample_pdf)
    view.set_view_mode("facing")
    qapp.processEvents()
    vw = view.viewport().width() - 32
    vh = view.viewport().height() - 32
    spread_w = 200 + 12 + 200

    view.set_fit_mode("width")
    qapp.processEvents()
    assert view.zoom == pytest.approx(vw / spread_w, rel=1e-6)

    view.set_fit_mode("page")
    qapp.processEvents()
    assert view.zoom == pytest.approx(min(vw / spread_w, vh / 100.0),
                                      rel=1e-6)


def test_page_step_advances_rows(view_factory, tmp_path, sample_pdf, qapp):
    single = view_factory(sample_pdf)
    single.set_view_mode("single")
    single.page_step(+1)
    assert single.current_page == 1

    facing = view_factory(sample_pdf)
    facing.set_view_mode("facing")
    facing.page_step(+1)
    assert facing.current_page == 2      # next spread starts at page 3
    facing.page_step(-1)
    assert facing.current_page == 0

    overview = view_factory(_make_pdf(tmp_path / "seven.pdf", 7))
    overview.set_view_mode("overview")
    overview.page_step(+1)
    assert overview.current_page == 3
    overview.page_step(+1)
    assert overview.current_page == 6
    overview.page_step(+1)
    assert overview.current_page == 6    # clamped to the last row


def test_view_mode_state_round_trip(view_factory, sample_pdf, qapp):
    view = view_factory(sample_pdf)
    view.set_view_mode("facing")
    view.goto_page(1)
    state = view.get_state()
    assert state["view_mode"] == "facing"

    view.set_view_mode("continuous")
    view.restore_state(state)
    qapp.processEvents()
    assert view.view_mode == "facing"
    assert view.current_page == 1
    assert view._page_rects[0] is not None
    assert view._page_rects[1] is not None

    old = dict(state)
    old.pop("view_mode")
    view.restore_state(old)
    qapp.processEvents()
    assert view.view_mode == "continuous"
    assert view._page_rects[0] is not None


def test_reflow_ignores_view_mode_and_restores_it(view_factory, sample_pdf,
                                                  qapp):
    view = view_factory(sample_pdf)
    view.set_reflow_mode(True)
    assert wait_until(qapp, lambda: view._reflow_worker is None)
    view.set_view_mode("facing")
    assert view.view_mode == "facing"
    assert view.reflow_mode is True

    view.set_reflow_mode(False)
    qapp.processEvents()
    assert view._rows == [[0, 1], [2]]
    assert view._page_rects[0] is not None
    assert view._page_rects[1] is not None


def test_non_continuous_modes_have_no_scrollbars(view_factory, sample_pdf,
                                                 qapp):
    view = view_factory(sample_pdf)
    for mode in ("single", "facing"):
        view.set_view_mode(mode)
        qapp.processEvents()
        assert view.verticalScrollBar().maximum() == 0
        assert view.horizontalScrollBar().maximum() == 0
