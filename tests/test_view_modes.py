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
    assert all(rect is not None for rect in view._page_rects)

    left = view._page_rects[0]
    right = view._page_rects[1]
    assert left[0] + left[2] + 12 == right[0]
    assert abs(left[1] - right[1]) <= 1
    # Every spread is stacked below the previous one.
    assert view._page_rects[2][1] > view._page_rects[0][1]
    assert view._page_rects[4][1] > view._page_rects[2][1]
    assert view._content_h > view._page_rects[4][1]

    lone = view._page_rects[4]
    # The odd last page sits in the left column, right edge at the gutter.
    assert abs((lone[0] + lone[2]) - (view._content_w / 2 - 6)) <= 1


def test_facing_centered_first_and_last(view_factory, six_pdf, five_pdf,
                                        qapp):
    view = view_factory(six_pdf)
    view.set_view_mode("facing_centered")
    qapp.processEvents()
    assert view._rows == [[0], [1, 2], [3, 4], [5]]
    assert all(rect is not None for rect in view._page_rects)
    first = view._page_rects[0]
    assert abs(first[0] - (view.viewport().width() - first[2]) / 2) <= 1
    assert view._page_rects[3][1] > view._page_rects[1][1]

    last = view._page_rects[5]
    assert abs(last[0] - (view.viewport().width() - last[2]) / 2) <= 1
    assert last[1] > view._page_rects[3][1]

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
    assert all(rect is not None for rect in view._page_rects)

    rects = [view._page_rects[i] for i in range(3)]
    assert rects[0][0] < rects[1][0] < rects[2][0]
    assert view._page_rects[3][1] > rects[0][1]
    assert view._page_rects[6][1] > view._page_rects[3][1]

    # The short last row is centered as a group.
    lone = view._page_rects[6]
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


def test_page_step_scrolls_grid_modes(view_factory, five_pdf, qapp):
    single = view_factory(five_pdf)
    single.set_view_mode("single")
    single.page_step(+1)
    assert single.current_page == 1      # Single advances one page

    facing = view_factory(five_pdf)
    facing.set_view_mode("facing")
    facing.set_zoom(2.0)
    qapp.processEvents()
    vbar = facing.verticalScrollBar()
    assert vbar.maximum() > 0
    facing.page_step(+1)
    qapp.processEvents()
    assert vbar.value() == min(vbar.maximum(),
                               facing.viewport().height() - 60)
    assert facing.current_page == 2      # next spread is [2, 3]


def test_facing_scrolls_through_spreads(view_factory, five_pdf, qapp):
    view = view_factory(five_pdf)
    view.set_view_mode("facing")
    view.set_zoom(2.0)
    qapp.processEvents()
    vbar = view.verticalScrollBar()
    assert vbar.maximum() > 0

    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    first, last = view.visible_range()
    assert first <= 2 <= last
    assert last == 4                     # lone last page is reachable
    assert view.current_page == 2        # the [2, 3] spread
    lone = view._page_rects[4]
    assert lone is not None
    assert lone[1] + lone[3] <= view._content_h


def test_facing_centered_rows_stack_and_scroll(view_factory, six_pdf, qapp):
    view = view_factory(six_pdf)
    view.set_view_mode("facing_centered")
    view.set_zoom(2.0)
    qapp.processEvents()
    assert all(rect is not None for rect in view._page_rects)
    assert view._page_rects[1][1] > view._page_rects[0][1]
    assert view._page_rects[3][1] > view._page_rects[1][1]
    assert view._page_rects[5][1] > view._page_rects[3][1]

    last = view._page_rects[5]
    assert abs(last[0] - (view._content_w - last[2]) / 2) <= 1

    vbar = view.verticalScrollBar()
    assert vbar.maximum() > 0
    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    _, last_page = view.visible_range()
    assert last_page == 5


def test_overview_all_rows_scroll(view_factory, tmp_path, qapp):
    path = _make_pdf(tmp_path / "seven.pdf", 7)
    view = view_factory(path)
    view.set_view_mode("overview")
    view.set_zoom(2.0)
    qapp.processEvents()
    assert all(rect is not None for rect in view._page_rects)
    short = view._page_rects[6]
    assert abs(short[0] - (view._content_w - short[2]) / 2) <= 1

    vbar = view.verticalScrollBar()
    assert vbar.maximum() > 0
    vbar.setValue(vbar.maximum())
    qapp.processEvents()
    _, last = view.visible_range()
    assert last == 6


def test_view_mode_state_round_trip(view_factory, five_pdf, qapp):
    view = view_factory(five_pdf)
    view.set_view_mode("facing")
    view.set_zoom(2.0)
    view.goto_page(2)
    state = view.get_state()
    assert state["view_mode"] == "facing"
    assert state["vscroll"] > 0

    view.set_view_mode("continuous")
    view.restore_state(state)
    qapp.processEvents()
    assert view.view_mode == "facing"
    assert view.current_page == 2
    assert view.verticalScrollBar().value() == state["vscroll"]
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


def test_single_has_no_scrollbars_but_facing_scrolls(view_factory, five_pdf,
                                                     qapp):
    view = view_factory(five_pdf)
    view.set_view_mode("single")
    qapp.processEvents()
    assert view.verticalScrollBar().maximum() == 0
    assert view.horizontalScrollBar().maximum() == 0

    view.set_view_mode("facing")
    qapp.processEvents()
    assert view.verticalScrollBar().maximum() == 0   # stack fits
    view.set_zoom(2.0)
    qapp.processEvents()
    assert view.verticalScrollBar().maximum() > 0   # stack overflows
