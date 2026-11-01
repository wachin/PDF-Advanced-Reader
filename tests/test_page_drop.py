"""F10-4s: the gap a drop between two rows names, in the numbering the edit reads.

The strip is where a reorder becomes something a reader can *do*: gather a pick, drag
it, let go between two rows.  ``MovePages`` already knows what to do with the answer,
and it takes a *gap* — "the block's first sheet goes at slot *target*, counted in the
book with the block taken out" (F10-4p, ``_intended``).  A drop does not name that.  It
names a **line between two visible rows**, and the rows are numbered in the book *with*
the block still in them.  The two numberings agree only when the pick lies above the
drop; anywhere else they differ by the number of picked rows the line has passed, which
is the off-by-one a reader would feel as their block landing a page short of where they
let go.

So this step is one function, and it is entirely about that: :func:`gap_for_drop` turns
(the row under the pointer, which half of it, what is picked) into the gap the edit
takes.  Four readings of the strip, each of them tested rather than assumed:

* the pointer's **upper half** means the line above that row — the block lands *before*
  it — and the lower half means the line below it, *after*;
* the line is drawn **through the block** when the reader lets go inside what they are
  dragging, and a block dropped onto one of its own internal boundaries is the order it
  already had.  Measured on every contiguous pick of a five-sheet book: both halves of
  every row of the pick answer with the pick's own start slot, so the drop writes
  nothing — and the write-nothing includes the settle;
* **blank space** below the last row is the end of the book, and above the first is the
  front;
* the answer is always a legal gap, ``0 <= gap <= count - len(pick)``, so the clamp in
  ``MovePages._intended`` is never reached from the strip — it is there for a caller who
  counts differently.

The refusal is deliberately *not* here: an empty pick still names a gap, and the edit
that receives it says "No page was chosen to move".  One owner for each rule is the
reason a pick is checked in ``_checked_pick`` and not twice.
"""

from __future__ import annotations

import itertools
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_move import numbers
from test_page_settle import nested_pdf, own

from pdfar.edits import EditError
from pdfar.pages import MovePages, gap_for_drop

#: How many sheets the rule is checked on, pick by pick.
PAGES = 5

#: The real documents, as the rest of this roadmap reads them.
PAPERS = sorted(
    os.path.join("external", "two-column-test-pdfs", name)
    for name in os.listdir(os.path.join("external", "two-column-test-pdfs"))
    if name.endswith(".pdf")
)


def all_picks(count: int):
    """Every non-empty pick of a *count*-sheet book, shortest first."""
    return [pick for size in range(1, count + 1)
            for pick in itertools.combinations(range(count), size)]


def gap_across_the_line(count: int, pick, boundary: int) -> int:
    """The gap the *boundary*-th line names, stated without reference to the edit.

    ``boundary`` is a place between rows: ``0`` is above row 0 and ``count`` is below the
    last row.  A block lifted out of the rows leaves the others where they were, so the
    gap is how many of the rows **above the line** are not part of the block — written as
    a filter over the rows a reader can see, which is how the strip thinks, rather than
    as the bisect the implementation reaches for.
    """
    moved = set(pick)
    return len([row for row in range(boundary) if row not in moved])


def line_under(row, after: bool, count: int) -> int:
    """The line a pointer over *row* is asking for: its top edge or its bottom one."""
    if row is None:
        return 0 if not after else count
    return row + 1 if after else row


# ------------------------------------------------------------------ the translation


@pytest.mark.parametrize("pick", all_picks(PAGES))
@pytest.mark.parametrize("row", range(PAGES))
@pytest.mark.parametrize("after", [False, True])
def test_the_line_the_strip_draws_names_the_gap_the_edit_reads(pick, row, after):
    """Every pick, every row, both halves: one numbering read in the other's words."""
    gap = gap_for_drop(PAGES, pick, row, after)
    assert gap == gap_across_the_line(PAGES, pick, line_under(row, after, PAGES))
    assert 0 <= gap <= PAGES - len(set(pick))


@pytest.mark.parametrize("pick", all_picks(PAGES))
@pytest.mark.parametrize("row", range(PAGES))
@pytest.mark.parametrize("after", [False, True])
def test_the_block_lands_between_the_two_sheets_the_line_separates(pick, row, after):
    """What a reader is owed: the neighbours of the line stay the neighbours of it.

    The check is deliberately not the splice the edit uses.  The line ran between the
    last unpicked row above it and the first unpicked row below it, and afterwards those
    two sheets — when the book has them — have the block between them.
    """
    gap = gap_for_drop(PAGES, pick, row, after)
    order = MovePages(tuple(pick), gap)._intended(PAGES)
    moved = set(pick)
    boundary = line_under(row, after, PAGES)
    above = [index for index in range(boundary) if index not in moved]
    below = [index for index in range(boundary, PAGES) if index not in moved]

    assert sorted(order) == list(range(PAGES)), "a reorder lost or invented a sheet"
    assert [sheet for sheet in order if sheet not in moved] == above + below
    assert order[gap:gap + len(moved)] == sorted(moved)
    if above:
        assert order[gap - 1] == above[-1]
    else:
        assert gap == 0
    if below:
        assert order[gap + len(moved)] == below[0]
    else:
        assert gap + len(moved) == PAGES


@pytest.mark.parametrize("pick", all_picks(4))
@pytest.mark.parametrize("row", range(4))
@pytest.mark.parametrize("after", [False, True])
def test_a_drop_changes_nothing_only_when_the_line_is_one_the_block_is_already_on(
        pick, row, after):
    """Identity, both ways round.

    Forward: a line at the block's own edge names the gap the block fills.  Back: the
    only picks that can answer with the book they started in are the ones already lying
    in those slots — which is why a scattered pick never gets a silent drop, however
    carefully it is let go.
    """
    gap = gap_for_drop(4, pick, row, after)
    changed = MovePages(pick, gap)._intended(4) != list(range(4))
    occupies_its_own_slots = set(pick) == set(
        range(gap, gap + len(set(pick))))
    assert changed != occupies_its_own_slots


def test_a_drop_inside_a_contiguous_block_names_the_gap_it_already_fills():
    """The case a hand-written rule gets wrong by answering "before that row".

    A line between two sheets of the pick separates the pick from *itself*, so it names
    the boundary the pick is already sitting on: the drop is silent.  Both halves of both
    interior rows, and both edges of the block.
    """
    for pick in ((0, 1, 2), (1, 2, 3), (2, 3, 4), (0, 1, 2, 3, 4), (1, 2, 3, 4)):
        start = pick[0]
        for row, after in itertools.product(pick, (False, True)):
            assert gap_for_drop(PAGES, pick, row, after) == start


@pytest.mark.parametrize("row", range(PAGES))
@pytest.mark.parametrize("after", [False, True])
def test_a_single_sheet_lands_on_the_far_side_of_the_sheet_it_was_dropped_against(
        row, after):
    pick = (2,)
    gap = gap_for_drop(PAGES, pick, row, after)
    order = MovePages(pick, gap)._intended(PAGES)
    assert order[gap] == 2
    assert sorted(order) == list(range(PAGES))


def test_the_top_of_the_first_row_is_the_front_and_the_bottom_of_the_last_the_end():
    for pick in all_picks(PAGES):
        assert gap_for_drop(PAGES, pick, 0, False) == gap_across_the_line(PAGES, pick, 0)
        assert gap_for_drop(PAGES, pick, PAGES - 1, True) == gap_across_the_line(
            PAGES, pick, PAGES)


def test_blank_space_below_the_book_names_the_end_and_above_it_the_front():
    """``None`` is the row the pointer found no sheet under, in the strip's own words."""
    assert gap_for_drop(PAGES, (1, 3), None, True) == PAGES - 2
    assert gap_for_drop(PAGES, (1, 3), None, False) == 0
    assert gap_for_drop(PAGES, (1, 3), None, True) == gap_for_drop(PAGES, (1, 3),
                                                                 PAGES - 1, True)
    assert gap_for_drop(PAGES, (1, 3), None, False) == gap_for_drop(PAGES, (1, 3),
                                                                   0, False)
    assert gap_for_drop(PAGES, tuple(range(PAGES)), None, True) == 0


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 8])
def test_the_translation_holds_for_every_book_the_strip_might_be_shown(count):
    for pick in all_picks(count):
        for row, after in itertools.product(range(count), (False, True)):
            gap = gap_for_drop(count, pick, row, after)
            assert gap == gap_across_the_line(count, pick, row + 1 if after else row)
            assert 0 <= gap <= count - len(set(pick))


def test_a_pick_named_twice_is_the_block_it_is_dragging():
    """The strip cannot hand a sheet twice, but the rule must not multiply it either."""
    assert gap_for_drop(PAGES, (2, 2, 0), 4, True) == gap_for_drop(PAGES, (0, 2), 4,
                                                                  True)
    assert MovePages((2, 2, 0), gap_for_drop(PAGES, (2, 2, 0), 4, True))._intended(
        PAGES) == MovePages((0, 2), 4)._intended(PAGES)


def test_an_empty_pick_names_a_gap_and_the_edit_is_the_one_that_refuses(tmp_path):
    """One owner per rule: the translation is total, ``MovePages`` checks the pick."""
    book = build_sample_pdf(str(tmp_path / "book.pdf"), pages=PAGES)
    session = make_session(book)
    try:
        doc = session.doc
        gap = gap_for_drop(doc.page_count, (), 3, True)
        assert gap == 4
        with pytest.raises(EditError, match="No page was chosen"):
            MovePages((), gap).apply(doc)
    finally:
        session.close()


# -------------------------------------------------------------------- the document


@pytest.fixture
def book(tmp_path):
    """Five sheets, each of which says which sheet it is."""
    return build_sample_pdf(str(tmp_path / "book.pdf"), pages=PAGES)


@pytest.fixture
def nested(tmp_path):
    """The three-sheet book whose geometry lives on two ancestors."""
    return nested_pdf(str(tmp_path / "nested.pdf"))


#: Picks, rows and halves that cover the interesting boundaries without asking MuPDF
#: for 620 documents: down, up, from the front, to the end, a scattered pick, a line
#: through the middle of the block, and a block moved onto the book's own ends.
DROPS = [
    ((0,), 4, True),              # the first sheet dragged to the bottom of the book
    ((4,), 0, False),             # the last sheet dragged to the front
    ((1,), 3, False),             # downward, landing above the row pointed at
    ((3,), 1, True),              # upward, landing below it
    ((0, 2, 4), 3, False),        # scattered pick, line between two of its members
    ((1, 3), 1, True),            # the block's own lower edge
    ((0, 1, 2), 1, False),        # a line inside a contiguous block
    ((1, 2, 3, 4), 0, False),
    ((0, 1, 2, 3), 4, True),
    ((0, 1), 2, True),
    ((2, 3), 4, True),
]


@pytest.mark.parametrize("pick, row, after", DROPS)
def test_the_book_comes_out_with_the_block_against_the_sheet_it_was_dropped_against(
        book, pick, row, after):
    """The translation, through MuPDF, on the sheets themselves."""
    session = make_session(book)
    try:
        doc = session.doc
        before = numbers(doc)
        moved = set(pick)
        boundary = row + 1 if after else row
        above = [sheet for sheet in before[:boundary] if sheet not in moved]
        below = [sheet for sheet in before[boundary:] if sheet not in moved]
        gap = gap_for_drop(doc.page_count, pick, row, after)

        MovePages(pick, gap).apply(doc)

        order = numbers(doc)
        assert sorted(order) == before, "a reorder lost or invented a sheet"
        assert [sheet for sheet in order if sheet not in moved] == above + below
        assert order[gap:gap + len(moved)] == sorted(moved)
        if above:
            assert order[gap - 1] == above[-1]
        if below:
            assert order[gap + len(moved)] == below[0]
    finally:
        session.close()


@pytest.mark.parametrize("pick, row, after", [
    ((0, 1, 2), 1, False),
    ((0, 1, 2), 1, True),
    ((1, 2), 2, True),
    ((1, 2, 3), 3, True),
    ((3, 4), 3, False),
    ((2,), 2, False),
    ((2,), 2, True),
    ((0, 1, 2, 3, 4), 2, True),
])
def test_a_let_go_inside_the_block_writes_nothing_at_all(book, pick, row, after):
    """Including the settle: a reorder that asks for none must not touch the tree."""
    session = make_session(book)
    try:
        doc = session.doc
        MovePages(pick, gap_for_drop(doc.page_count, pick, row, after)).apply(doc)
        assert not doc.is_dirty
    finally:
        session.close()


def test_a_drop_on_an_unsettled_book_writes_no_keys_when_it_changes_nothing(nested):
    """The cost of the short-circuit, on the book where that is not invisible.

    Every row and both halves, with the whole book picked: every line runs through the
    block, so every answer is the order the book already had — and a move that settled
    first would stamp a box and a turn onto each of the three sheets of a book whose
    reorder asks for nothing at all.
    """
    doc = fitz.open(nested)
    count = doc.page_count
    whole = tuple(range(count))
    for row, after in itertools.product(range(count), (False, True)):
        MovePages(whole, gap_for_drop(count, whole, row, after)).apply(doc)
    assert not doc.is_dirty, "a drop that reorders nothing still settled the tree"
    assert [own(doc, index, "MediaBox") for index in range(count)] == ["null"] * count
    doc.close()


def test_a_moved_block_survives_a_save_and_a_reopen(book, tmp_path):
    """The drop a reader will judge with their eyes: two sheets, out of their own order.

    Sheets 1 and 2, let go on the line under row 4 — the fourth of five.  The block is
    lifted out of the front, so the line the reader aimed at is two rows further along
    the remaining sheets than the gap it names, and the book has to come out reading
    ``3 4 1 2 5`` for that to be what they let go of.
    """
    session = make_session(book)
    try:
        doc = session.doc
        pick = (0, 1)
        gap = gap_for_drop(doc.page_count, pick, 3, True)
        assert gap == 2
        MovePages(pick, gap).apply(doc)
        assert numbers(doc) == [2, 3, 0, 1, 4]
        out = str(tmp_path / "after.pdf")
        doc.save(out, deflate=False)
    finally:
        session.close()
    reopened = fitz.open(out)
    try:
        assert numbers(reopened) == [2, 3, 0, 1, 4]
    finally:
        reopened.close()


@pytest.mark.parametrize("paper", PAPERS)
def test_a_real_paper_lands_the_block_on_the_far_side_of_the_line(paper):
    """Object identity, not text: the sheets a reader moved are the ones they picked."""
    session = make_session(paper)
    try:
        doc = session.doc
        count = doc.page_count
        pick = (0, count - 1)
        row = count // 2
        gap = gap_for_drop(count, pick, row, False)
        xrefs = [doc.load_page(index).xref for index in range(count)]
        wanted = [xref for index, xref in enumerate(xrefs) if index not in set(pick)]
        wanted[gap:gap] = [xrefs[0], xrefs[-1]]

        MovePages(pick, gap).apply(doc)

        assert [doc.load_page(index).xref for index in range(count)] == wanted
    finally:
        session.close()
