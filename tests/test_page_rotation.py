"""F10-4: a page that carries its own ``/Rotate`` keeps its marks where they are painted.

Two coordinate systems meet on every page of a PDF.  MuPDF turns a page by its own
``/Rotate`` before it hands the picture over, so the bitmap — and ``page.rect``,
which is what the view measures a page by — is the page as it lies turned.  The
numbers the file states are counted on the sheet before the turn: an annotation's
``/Rect``, a highlight's quad points, a word's box.  The view had been mapping the
second set with the first set's size and only its own turn, so on a page that
arrives sideways every mark, every search hit, every selection rectangle and every
click sat a quarter of the page away from the ink it meant.

The ground truth here is MuPDF's own pixels.  Each test renders the page, measures
where the ink actually landed and holds the view's mapping to it — for the four
turns a page may carry and the four the view offers, tried together.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QPoint

from pdfar.geometry import map_rect
from pdfar.viewer import PDFView

#: The four turns a page may carry, and the four the view offers.
ROTATIONS = (0, 90, 180, 270)

#: Every page is built as this sheet, and carries one of the turns above.
SHEET = (200.0, 100.0)

#: A box on the sheet, clear of the two lines of text below it.
MARK = (30.0, 12.0, 130.0, 42.0)

#: Where the fixture writes its two lines, so their ink can be bounded.
LINES = ("Hello PDFAR world", "second line here")


def build_rotated_pdf(path, turns=ROTATIONS):
    """One page per turn, each with two lines of text on the same sheet.

    The text is written before the page is turned, which is what a producer does:
    the content keeps its sheet coordinates and ``/Rotate`` says how to hang it.
    """
    doc = fitz.open()
    for turn in turns:
        page = doc.new_page(width=SHEET[0], height=SHEET[1])
        page.insert_text(fitz.Point(10, 62), LINES[0], fontsize=12)
        page.insert_text(fitz.Point(10, 88), LINES[1], fontsize=10)
        page.set_rotation(turn)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture
def rotated_pdf(tmp_path):
    return build_rotated_pdf(tmp_path / "turned.pdf")


@pytest.fixture
def view(qapp, rotated_pdf):
    handle = PDFView(rotated_pdf)
    handle.resize(500, 400)
    handle.show()
    qapp.processEvents()
    try:
        yield handle
    finally:
        handle.shutdown()
        handle.deleteLater()
        qapp.processEvents()


def pixels(page, turn, annots):
    """The painted page, the way the render pool paints it."""
    matrix = fitz.Matrix(1, 1)
    if turn:
        matrix = matrix.prerotate(turn)
    return page.get_pixmap(matrix=matrix, annots=annots)


def painted(page, turn, marks):
    """``(x0, y0, x1, y1)`` of the ink on a page turned both ways.

    With *marks* the ink of the annotations alone, measured as the difference
    between the page with them and the page without; without them, the ink of the
    page's own content.
    """
    with_marks = pixels(page, turn, True)
    bare = pixels(page, turn, False)
    width, height = with_marks.width, with_marks.height
    found = []
    for y in range(height):
        for x in range(width):
            seen = with_marks.pixel(x, y)[:3] != (255, 255, 255)
            if marks:
                seen = with_marks.pixel(x, y)[:3] != bare.pixel(x, y)[:3]
            if seen:
                found.append((x, y))
    assert found, "nothing was painted, so the measurement says nothing"
    xs = [p[0] for p in found]
    ys = [p[1] for p in found]
    return (float(min(xs)), float(min(ys)),
            float(max(xs) + 1), float(max(ys) + 1))


def centre(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def live_copy(view):
    """A handle on the content the view is showing, for measuring its pixels.

    Read on the GUI thread before the copy is opened, so an edited document is
    measured from its revision and not from the file it was opened from.
    """
    return view.session.open_copy(view.session.read_version())


# ---------------------------------------------------------------------- what it reads
def test_the_view_reads_the_sheet_and_the_painted_page_apart(view):
    """The two sizes a rotated page has, and the turn between them."""
    assert tuple(view._sheet_turns) == ROTATIONS
    assert all(size == SHEET for size in view._sheet_sizes)
    assert tuple(view.page_sizes) == tuple(
        (SHEET[1], SHEET[0]) if turn % 180 else SHEET for turn in ROTATIONS)
    for index, turn in enumerate(ROTATIONS):
        assert view._sheet_space(index) == (SHEET[0], SHEET[1], turn)


@pytest.mark.parametrize("turn", ROTATIONS)
def test_the_page_turn_is_added_to_the_view_turn(view, turn):
    """One turn by the sum is the same place as two turns, for every page.

    The view's own turn is what the user asked for and the page's is what the file
    says, so the mapping the marks use carries both — and the picture the pool
    paints carries both too, because MuPDF turns the page before the matrix does.
    """
    view.set_rotation(turn)
    for index, page_turn in enumerate(ROTATIONS):
        assert view._sheet_space(index)[2] == page_turn + turn


# --------------------------------------------------------------------- where it paints
@pytest.mark.parametrize("turn", ROTATIONS)
def test_a_mark_is_painted_where_the_view_puts_it(view, turn, qapp):
    """The mapping and MuPDF's pixels agree, page turn by page turn.

    A rectangle drawn on the sheet is measured in the picture after both turns, so
    the test is the claim itself: the box the view maps is where the ink is.
    """
    view.set_rotation(turn)
    for index in range(view.page_count):
        assert view.annotation_manager.add_shape(
            index, "rect", rect=MARK) is not None
        qapp.processEvents()

    doc = live_copy(view)
    try:
        for index in range(view.page_count):
            want = map_rect(MARK, *view._sheet_space(index))
            got = painted(doc.load_page(index), turn, marks=True)
            # The border is a point wide and straddles the box, so the ink is a
            # point outside it on every side; the centre carries no slack at all.
            assert centre(got) == pytest.approx(centre(want), abs=1.0), index
            assert got == pytest.approx(want, abs=2.0), (index, turn)
    finally:
        doc.close()


@pytest.mark.parametrize("turn", ROTATIONS)
def test_a_click_on_the_painted_mark_is_a_click_on_its_box(view, turn, qapp):
    """The point a tool captures is the sheet's, wherever the page is turned.

    Clicking the middle of the ink has to return a point inside the rectangle the
    mark is stored in — otherwise a rotated page cannot be selected, moved or
    deleted by pointing at it.
    """
    view.set_rotation(turn)
    for index in range(view.page_count):
        assert view.annotation_manager.add_shape(
            index, "rect", rect=MARK) is not None
        qapp.processEvents()

    doc = live_copy(view)
    try:
        for index in range(view.page_count):
            ink = painted(doc.load_page(index), turn, marks=True)
            u, v = centre(ink)
            px, py = view._page_origin(index)
            hit = view._pdf_point_at(QPoint(round(px + u), round(py + v)))
            assert hit is not None, f"page {index} at view turn {turn}"
            page, x, y = hit
            assert page == index
            assert MARK[0] - 1 <= x <= MARK[2] + 1, (index, turn, x)
            assert MARK[1] - 1 <= y <= MARK[3] + 1, (index, turn, y)
    finally:
        doc.close()


@pytest.mark.parametrize("turn", ROTATIONS)
def test_the_words_are_where_the_view_puts_them(view, turn):
    """Search hits and selection rectangles are drawn from word boxes.

    The words come out of the file on the sheet, so their boxes have to be turned
    with the page to sit on the letters the reader can see: the ink has to be
    inside them, and they must not reach much beyond it.
    """
    view.set_rotation(turn)
    doc = live_copy(view)
    try:
        for index in range(view.page_count):
            words = view._page_words(index)
            assert words, index
            want = union(map_rect(w[:4], *view._sheet_space(index))
                         for w in words)
            got = painted(doc.load_page(index), turn, marks=False)
            assert got[0] >= want[0] - 1.0 and got[1] >= want[1] - 1.0, (index, turn)
            assert got[2] <= want[2] + 1.0 and got[3] <= want[3] + 1.0, (index, turn)
            # A box that is the ink's plus a line of slack is still the ink's box;
            # a quarter of the page away is not.
            assert want[2] - want[0] <= got[2] - got[0] + 24.0, (index, turn)
            assert want[3] - want[1] <= got[3] - got[1] + 24.0, (index, turn)
    finally:
        doc.close()


@pytest.mark.parametrize("turn", ROTATIONS)
def test_a_highlight_made_on_a_turned_page_covers_the_words_it_was_made_on(
        view, turn, qapp):
    """Selecting on a page that arrives sideways highlights the words under it.

    The selection is a screen action and a highlight is a file number: the words
    the user swept over are stored as they are counted in the file, and the ink the
    file then paints is back where the view drew the boxes.
    """
    view.set_rotation(turn)
    view.select_all()
    assert view.create_highlight_from_selection() is True
    qapp.processEvents()

    doc = live_copy(view)
    try:
        for index in range(view.page_count):
            rects = [r for h in view.annotation_manager.get_highlights_for_page(index)
                     for r in h.rects]
            assert rects, index
            want = union(map_rect(r, *view._sheet_space(index)) for r in rects)
            got = painted(doc.load_page(index), turn, marks=True)
            # The band is painted a few points wider than the quad it was handed —
            # padding, not place — so this is the agreement to demand of it.
            assert got == pytest.approx(want, abs=5.0), (index, turn, got, want)
    finally:
        doc.close()
