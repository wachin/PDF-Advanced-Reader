"""F10-3g: a highlight follows the words it was made on, not the way the page is turned.

Selecting text is a screen action, but a highlight is stored in the page: PDF
points, which do not care how the view happens to be rotated.  The selection path
had been handing the edit the rectangles as they appear on screen, so a highlight
made at 90° landed a quarter of the page away and merged its lines along the
wrong axis — four quads where the text has two.

These tests fix the PDF-space answer with a rotation of 0 and then hold every
other rotation to it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf
from pdfar.geometry import map_rect

ROTATIONS = (0, 90, 180, 270)

#: The two lines ``build_sample_pdf`` writes on page 1, in PDF points.
PAGE_ONE_LINES = [(10.0, 47.1, 113.3, 63.6), (120.0, 79.2, 193.9, 93.0)]


@pytest.fixture
def win(qapp):
    from pdfar.main import MainWindow
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def view(qapp, win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2, width=200, height=100))
    assert win.open_document(path)
    qapp.processEvents()
    return win._active_tab().view


def union(rects):
    """Bounding box of ``(x0, y0, x1, y1)`` rectangles."""
    rects = list(rects)
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def selected_pdf_rects(view, page):
    """What the selection means in the page: the words it covers."""
    return [w[:4] for p, _i, w in view._iter_selected_words() if p == page]


def stored_rects(view, page):
    """Every rectangle the highlights on *page* ended up with."""
    return [r for h in view.annotation_manager.get_highlights_for_page(page)
            for r in h.rects]


def assert_rects(actual, expected, tol=1.0):
    """Compare whole rectangle lists one at a time, which is what
    ``pytest.approx`` will not do for a list of tuples."""
    assert len(actual) == len(expected), (actual, expected)
    for got, want in zip(sorted(actual), sorted(expected)):
        assert got == pytest.approx(want, abs=tol), (got, want)


def highlight_everything(view, rotation, qapp):
    """Select the document and highlight it with the page turned *rotation*."""
    view.set_rotation(rotation)
    view.select_all()
    expected = {page: union(selected_pdf_rects(view, page))
                for page in {p for p, _i, _w in view._iter_selected_words()}}
    screen = {page: union(view._selection_rects_for_page(page))
              for page in expected}
    assert view.create_highlight_from_selection() is True
    qapp.processEvents()
    return expected, screen


# ------------------------------------------------------------------- where it lands
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_a_highlight_covers_the_words_it_was_made_on(view, rotation, qapp):
    """The page is turned; the selection still means the same words."""
    expected, _screen = highlight_everything(view, rotation, qapp)

    for page, want in expected.items():
        assert union(stored_rects(view, page)) == pytest.approx(want, abs=1.0), \
            f"rotation {rotation}, page {page}"


@pytest.mark.parametrize("rotation", ROTATIONS)
def test_the_highlight_is_under_the_selection_the_user_saw(view, rotation, qapp):
    """Mapped back onto the screen, it is where the selection was painted."""
    _expected, screen = highlight_everything(view, rotation, qapp)
    w0, h0 = view.page_sizes[0]

    assert union(map_rect(r, w0, h0, rotation)
                 for r in stored_rects(view, 0)) == pytest.approx(screen[0], abs=1.0)


# ------------------------------------------------------------------------ how it merges
def test_the_selection_merges_into_one_rectangle_per_page_line(view, qapp):
    """Ground truth first: unrotated, page 1 is two lines and gets two quads."""
    view.set_rotation(0)
    view.select_all()
    assert union(selected_pdf_rects(view, 0)) == pytest.approx(
        (10.0, 47.1, 193.9, 93.0), abs=1.0)
    assert view.create_highlight_from_selection() is True
    qapp.processEvents()

    assert_rects(stored_rects(view, 0), PAGE_ONE_LINES)


def test_turning_the_page_does_not_move_the_highlight(view, qapp):
    """One selection at 0° and the same selection at 90° are the same edit.

    The rotation had also broken the merge: sorting the words by their screen row
    split each text line into a quad per word, because a horizontal line runs
    down the rotated page.
    """
    highlight_everything(view, 0, qapp)
    straight = stored_rects(view, 0)

    assert view.undo_edit() is True
    qapp.processEvents()
    assert stored_rects(view, 0) == []

    highlight_everything(view, 90, qapp)

    assert_rects(stored_rects(view, 0), straight)


def test_a_short_selection_at_90_highlights_the_words_it_reaches(view, qapp):
    """Not the whole page: one line of it, chosen while the view is sideways."""
    view.set_rotation(90)
    words = view._page_words(0)
    line = [i for i, w in enumerate(words) if w[4] in ("Hello", "PDFAR", "world")]
    assert len(line) == 3
    view._sel_anchor, view._sel_caret = (0, line[0]), (0, line[-1])

    assert view.create_highlight_from_selection() is True
    qapp.processEvents()

    assert_rects(stored_rects(view, 0),
                 [union(w[:4] for i, w in enumerate(words) if i in line)])
