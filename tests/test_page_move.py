"""F10-4p: moving pages inside the document.

The reorder is the edit F10-4o exists for, and the first one whose *own* numbers
are what a reader is holding: nothing is added and nothing is taken away, every
sheet keeps its content, and the only thing that changes is which sheet wears
which number — so the bookmarks, the strip's pick, the reader's place and the
search's rows all have to be re-aimed at once, and a map that clamps (the
delete's answer) would be a lie here.

Five measured facts carry the design.

* **A bare ``move_page`` resizes the sheet it moves.**  On the nested book the
  sheet that reads 260×360 came out 400×300 — the crop it was being shown and the
  turn it had were replaced by what the node it landed under states.  So the move
  settles the tree before it moves anything, and refuses the books the settle
  refuses: a ``/Group`` above a sheet is a thing a move changes that a page cannot
  be handed a copy of.
* **MuPDF's move re-parents into the node it lands in and empties the node it
  leaves** — measured ``Count=0 kids=[]`` left behind, with every sheet's
  ``/Parent`` updated.  The tree stays consistent; it simply holds a node with no
  children, which is why the settle's "holds no children" refusal had to become a
  walk.  Without that, the *second* move of a session fails on the tree the first
  one produced.
* **There is no one-call reorder.**  ``select`` builds a sub-PDF — rejected in
  F10-4o, it strips the catalog — and PyMuPDF's whole reorder surface is
  ``move_page``, which means "put this sheet in front of the sheet currently
  numbered *to*", a numbering the caller has to track because the removal shifts
  everything above it.  So the intended order is computed first and realised by a
  repair walk, run from the front or from the back, whichever needs fewer calls:
  **236 of 236** picks and gaps on a six-sheet book came out of MuPDF in the order
  the plan asked for.
* **The calls are cheap.**  0.3 ms each on the settled 540-sheet book, against a
  delete's 44 ms page-tree rebuild: a hundred-page block costs 9 ms and the worst
  plan measured — every other sheet to the end, 269 calls — costs 28 ms, all of it
  inside a settle that costs 291 ms on its own.
* **The document follows its own sheets; the numbering does not, on purpose.**  MuPDF
  re-derives the outline rows and the link destinations from the page objects, so
  an entry travels with the sheet it named.  ``/PageLabels`` does not: it numbers
  positions, so a sheet that moves changes the number printed on it.  That is the
  reading PDFAR takes — a numbering that followed the sheets would un-do the reverse
  a batch scanned back to front is reversed for — and it is pinned with its
  measurement in ``tests/test_page_labels.py``.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_settle import (catalogued_pdf, digest, grouped_pdf, kids_of, nested_pdf,
                              own, pages_root, shape)

from pdfar.edits import EditError, Renumbering, Scope
from pdfar.pages import DeletePages, MovePages, SettlePageTree

#: How many sheets the rule is checked on.
PAGES = 6

#: The real documents, as the rest of this roadmap reads them.
PAPERS = sorted(
    os.path.join("external", "two-column-test-pdfs", name)
    for name in os.listdir(os.path.join("external", "two-column-test-pdfs"))
    if name.endswith(".pdf")
)


def numbers(doc):
    """The book as the sheets name themselves: ``[2, 0, 1]`` reads "sheet 2 first".

    The sample writes ``"Page N content"`` on every page but the first, which says
    which sheet sits where — a move that got the page count right and the sheets
    wrong shows up here and nowhere else.
    """
    out = []
    for index in range(doc.page_count):
        line = " ".join(doc.load_page(index).get_text().split())
        if line.startswith("SHEET"):
            out.append(int(line.split()[1]))
        elif line.startswith("Hello"):
            out.append(0)
        else:
            out.append(int(line.removeprefix("Page ").split()[0]) - 1)
    return out


def intended(order, pick, target):
    """The rule the edit is built to carry out, stated without reference to it.

    The picked sheets keep their relative order, the rest keep theirs, and the
    block's first sheet starts at *target* counted in the list **without** the
    block — which is the numbering a thumbnail strip uses when a reader drops a
    pick into the gap between two rows.  A target past the last gap is the end.
    """
    moved = sorted(set(pick))
    block = [order[index] for index in moved]
    rest = [label for index, label in enumerate(order) if index not in set(moved)]
    at = min(target, len(rest))
    return rest[:at] + block + rest[at:]


def order_after_move(doc, pick, target):
    want = intended(numbers(doc), pick, target)
    MovePages(tuple(pick), target).apply(doc)
    got = numbers(doc)
    assert got == want, f"pick {pick} into gap {target}: wanted {want}, the book reads {got}"
    return got


@pytest.fixture
def book(tmp_path):
    """Six sheets, each of which says which sheet it is."""
    return build_sample_pdf(str(tmp_path / "book.pdf"), pages=PAGES)


@pytest.fixture
def nested(tmp_path):
    """The three-sheet book whose geometry lives on two ancestors."""
    return nested_pdf(str(tmp_path / "nested.pdf"))


@pytest.fixture
def moved_once(nested):
    """The nested book after one move — where the tree has a node with no children."""
    doc = fitz.open(nested)
    MovePages((2,), 0).apply(doc)
    return doc


# --------------------------------------------------------------- the rule itself
@pytest.mark.parametrize("pick", [
    (0,), (3,), (5,), (0, 1), (2, 3), (1, 5), (0, 4), (0, 2, 4), (1, 3, 5), (2, 3, 4)])
@pytest.mark.parametrize("target", [0, 1, 2, 3])
def test_every_pick_and_every_gap_lands_the_block_where_the_reader_dropped_it(book,
                                                                              pick,
                                                                              target):
    doc = fitz.open(book)
    if target > doc.page_count - len(set(pick)):
        pytest.skip("a gap past the end is its own test")
    order_after_move(doc, pick, target)
    doc.close()


def test_a_target_past_the_last_gap_sends_the_block_to_the_end(book):
    doc = fitz.open(book)
    assert order_after_move(doc, (0, 1), 99) == [2, 3, 4, 5, 0, 1]
    doc.close()


def test_the_gap_is_counted_without_the_block_being_in_it(book):
    # Lift sheets 1 and 2 out and the book reads [0, 3, 4, 5]; gap 3 is the one in
    # front of sheet 5, so the block lands there — not at index 3 of the book the
    # reader picked from, which is where the arithmetic went wrong once.
    doc = fitz.open(book)
    assert order_after_move(doc, (1, 2), 3) == [0, 3, 4, 1, 2, 5]
    doc.close()


def test_a_move_that_changes_nothing_writes_nothing(book):
    doc = fitz.open(book)
    MovePages((2,), 2).apply(doc)
    assert not doc.is_dirty, "a sheet dropped into the gap it already fills still wrote"
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_drop_into_its_own_gap_leaves_an_unsettled_book_alone(nested):
    """The short-circuit runs *before* the settle, which is the part that costs.

    On a settled book a wasted settle writes nothing and the mistake is invisible; on
    this one it stamps a box and a turn onto every sheet of a book whose reorder asks
    for nothing at all.
    """
    doc = fitz.open(nested)
    MovePages((1,), 1).apply(doc)
    assert not doc.is_dirty, "a move that reorders nothing still settled the tree"
    assert [own(doc, index, "MediaBox") for index in range(3)] == ["null"] * 3
    doc.close()


def test_the_whole_book_put_back_at_the_front_writes_nothing(book):
    doc = fitz.open(book)
    MovePages(tuple(range(PAGES)), 0).apply(doc)
    assert not doc.is_dirty
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_sheet_named_twice_is_one_sheet_moved_once(book):
    doc = fitz.open(book)
    assert order_after_move(doc, (4, 4), 0) == [4, 0, 1, 2, 3, 5]
    assert doc.page_count == PAGES
    doc.close()


def test_the_block_keeps_its_own_order_and_the_rest_keep_theirs(book):
    doc = fitz.open(book)
    assert order_after_move(doc, (5, 1, 3), 1) == [0, 1, 3, 5, 2, 4]
    doc.close()


@pytest.mark.parametrize("pick,target", [
    ((0,), 11),                       # the front sheet to the very end: one call
    ((11,), 0),                       # and the same trip back
    ((1, 3, 7, 11), 5),               # scattered, landing in the middle
    ((0, 2, 4, 6, 8, 10), 6),         # every other sheet, over the ones left
    ((2, 3, 4, 5, 6, 7, 8, 9), 1),    # a long block that has nowhere to grow
])
def test_a_longer_book_is_reordered_by_the_plan_it_was_given(tmp_path, pick, target):
    """The walk, on a book too long to be an accident.

    Six sheets a wrong call can still land on the right slot by luck; twelve give the
    plan four times as many chances to be one off, which is the arithmetic the two
    directions exist to get right — including the one-call answer to a pick the front
    walk would spend the whole book on.
    """
    doc = fitz.open(build_sample_pdf(str(tmp_path / "long.pdf"), pages=12))
    order_after_move(doc, pick, target)
    doc.close()


# --------------------------------------------------------------------- refusals
def test_no_page_chosen_is_refused(book):
    doc = fitz.open(book)
    with pytest.raises(EditError, match="No page"):
        MovePages((), 2).apply(doc)
    assert not doc.is_dirty
    doc.close()


def test_a_page_the_book_does_not_have_stops_the_whole_move(book):
    doc = fitz.open(book)
    with pytest.raises(EditError, match="is not in this document"):
        MovePages((1, 9), 0).apply(doc)
    assert not doc.is_dirty, "the valid half of the pick moved on the way out"
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_negative_page_is_refused_rather_than_counted_from_the_end(book):
    # MuPDF refuses a negative *source* and accepts a negative *target* as "the
    # end"; both are named here rather than folded into somebody else's rule.
    doc = fitz.open(book)
    with pytest.raises(EditError, match="is not in this document"):
        MovePages((-1,), 0).apply(doc)
    with pytest.raises(EditError, match="gap"):
        MovePages((0,), -1).apply(doc)
    assert not doc.is_dirty
    doc.close()


def test_a_gap_past_the_end_is_the_end_rather_than_a_refusal(book):
    # Clamped, not refused: PdfCraft clamps the same way (``to.min(rest.len())``),
    # and a reader who drags past the last row means the last row.
    doc = fitz.open(book)
    assert order_after_move(doc, (0,), PAGES) == [1, 2, 3, 4, 5, 0]
    doc.close()


def test_a_tree_the_settle_cannot_read_cannot_be_moved(nested):
    path = grouped_pdf(nested)
    doc = fitz.open(path)
    with pytest.raises(EditError, match="/Group"):
        MovePages((2,), 0).apply(doc)
    assert not doc.is_dirty, "a refused move still wrote into the tree"
    # Read last: ``get_text`` marks a MuPDF document dirty on its own (measured on
    # 1.28.2 — ``load_page``, ``rect`` and ``rotation`` do not), so asking the book
    # which sheet sits where would answer the line above for the wrong reason.
    assert numbers(doc) == [0, 1, 2]
    doc.close()


def test_a_single_sheet_book_cannot_be_moved_anywhere_new(tmp_path):
    doc = fitz.open(build_sample_pdf(str(tmp_path / "one.pdf"), pages=1))
    MovePages((0,), 0).apply(doc)
    assert not doc.is_dirty
    assert numbers(doc) == [0]
    doc.close()


def test_a_book_that_does_not_come_out_in_the_order_asked_for_is_refused(monkeypatch,
                                                                         tmp_path):
    """The walk is read back against the plan, and the read-back is attached.

    Nothing in the plan is expected to fail — every non-empty pick and every target on
    a book of three to six sheets lands where the rule says, 846 of them — so the only
    way to see this guard work is to hand the move a plan that does not, which is what
    a change to ``move_page``'s semantics in a future MuPDF would look like from here.
    """
    import pdfar.pages as pages

    doc = fitz.open(build_sample_pdf(str(tmp_path / "twelve.pdf"), pages=12))
    monkeypatch.setattr(pages, "_repair_plan", lambda want: ((0, 2),))
    with pytest.raises(EditError, match="does not read the order"):
        MovePages((0,), 2).apply(doc)
    doc.close()


# ---------------------------------------------------------------- the page tree
def test_a_moved_sheet_keeps_the_box_and_the_turn_it_was_being_shown(nested):
    doc = fitz.open(nested)
    before = shape(doc)
    MovePages((2,), 0).apply(doc)
    assert shape(doc) == [before[2], before[0], before[1]]
    doc.close()


def test_the_bare_move_the_settle_exists_to_stop(nested):
    doc = fitz.open(nested)
    before = shape(doc)
    doc.move_page(2, 0)
    assert shape(doc) != [before[2], before[0], before[1]], (
        "the harm is gone, so the settle inside the move is testing nothing")
    doc.close()


def test_the_moved_sheet_now_states_what_its_ancestor_used_to_state(nested):
    doc = fitz.open(nested)
    shown = doc.load_page(2).rect
    assert doc.xref_get_key(doc.load_page(2).xref, "MediaBox")[1] == "null"
    MovePages((2,), 0).apply(doc)
    xref = doc.load_page(0).xref
    assert doc.xref_get_key(xref, "MediaBox")[1] != "null"
    assert doc.xref_get_key(xref, "CropBox")[1] != "null"
    assert doc.load_page(0).rect == shown
    doc.close()


def test_a_book_can_be_moved_twice(moved_once):
    """The second move is the one the emptied node used to stop."""
    doc = moved_once
    assert numbers(doc) == [2, 0, 1]
    assert order_after_move(doc, (0,), 2) == [0, 1, 2]
    doc.close()


def test_the_tree_still_counts_out_after_a_move(moved_once):
    doc = moved_once
    root = pages_root(doc)
    empties = [kid for kid in kids_of(doc, root)
               if doc.xref_get_key(kid, "Type")[1] == "/Pages"
               and not kids_of(doc, kid)]
    assert empties, "MuPDF left no emptied node, so this tested the wrong shape"
    SettlePageTree().apply(doc)          # must read the tree the move just made
    assert doc.page_count == 3
    doc.close()


def test_a_moved_book_saves_and_reopens_in_the_same_order(nested, tmp_path):
    doc = fitz.open(nested)
    MovePages((2, 0), 1).apply(doc)
    want, sizes = numbers(doc), shape(doc)
    out = str(tmp_path / "moved.pdf")
    doc.save(out, deflate=False)
    doc.close()
    again = fitz.open(out)
    assert numbers(again) == want
    assert shape(again) == sizes
    again.close()


# ---------------------------------------------------------------------- numbers
def test_the_step_answers_for_every_sheet_about_where_it_came_from(book):
    doc = fitz.open(book)
    edit = MovePages((1, 2, 3), 5)
    order = edit.page_order(doc.page_count)
    assert len(order) == doc.page_count
    edit.apply(doc)
    assert list(order) == numbers(doc), "the map the step publishes is not the book"
    doc.close()


def test_the_map_is_a_permutation_so_nothing_lands_on_a_neighbour(book):
    edit = MovePages((4,), 0)
    order = edit.page_order(PAGES)
    assert sorted(order) == list(range(PAGES)), "a move that lost or invented a sheet"
    mapping = Renumbering(order)
    for page in range(PAGES):
        assert mapping.where(page) == mapping.followed(page), (
            "a map that clamps is a delete's answer, not a move's")
    assert not mapping.backward


def test_the_way_back_is_the_same_map_turned_round(book):
    edit = MovePages((1, 2), 4)
    order = edit.page_order(PAGES)
    forward, backward = Renumbering(order), Renumbering(order, backward=True)
    for new, old in enumerate(order):
        assert backward.where(new) == old, "undoing the step does not run it home"
        assert forward.where(old) == new
        assert forward.followed(old) == new
        assert backward.followed(new) == old


def test_the_step_is_structure_and_leaves_nothing_to_re_read():
    edit = MovePages((1,), 3)
    assert edit.scope == Scope.STRUCTURE
    assert edit.changed is None
    assert edit.pages == (1,)


def test_the_label_counts_the_sheets_the_reader_asked_for():
    assert MovePages((2,), 0).label == "Move Page"
    assert MovePages((2, 4), 0).label == "Move 2 Pages"


# ------------------------------------------------------ what the document carries
def test_the_outline_rows_and_the_links_follow_their_own_sheets(nested):
    doc = fitz.open(nested)
    doc.load_page(0).insert_link({"kind": fitz.LINK_GOTO,
                                  "from": fitz.Rect(20, 20, 120, 60), "page": 2})
    doc.set_toc([[1, "First sheet", 1], [1, "Last sheet", 3]])
    MovePages((2,), 0).apply(doc)
    rows = {title: page for _level, title, page in doc.get_toc()}
    assert rows["Last sheet"] == 1, rows      # its sheet is the first one now
    assert rows["First sheet"] == 2, rows     # and it slipped one back
    targets = [link.get("page") for page in range(doc.page_count)
               for link in doc.load_page(page).get_links()]
    assert targets == [0], targets            # the link named the sheet, not the number
    doc.close()


def test_a_page_label_stays_with_the_position_it_was_written_for(tmp_path):
    """The numbering stays with the slot it was written for, which is the decision.

    ``/PageLabels`` numbers positions, not sheets: the catalog says "from index 0,
    count 101", so after a move the sheet that arrived at the front is called 101
    and the one that used to be 101 reads 103.  Nothing here writes labels, and a
    reader who numbers by slot — which is what a producer's numbering is — asked for
    exactly that.  The argument and the measurement of the alternative are in
    ``tests/test_page_labels.py``; this is the same invariant on the nested book.
    """
    path = catalogued_pdf(nested_pdf(str(tmp_path / "cat.pdf")))
    doc = fitz.open(path)
    before = [doc.load_page(index).get_label() for index in range(doc.page_count)]
    MovePages((0,), 2).apply(doc)
    assert numbers(doc) == [1, 2, 0]
    after = [doc.load_page(index).get_label() for index in range(doc.page_count)]
    assert after == before, (
        "the labels followed their sheets: that is the other reading, and it renumbers "
        "a book the reader only moved a sheet in")
    doc.close()


@pytest.mark.parametrize("path", PAPERS)
def test_a_real_paper_carries_one_page_without_losing_anything_else(path):
    doc = fitz.open(path)
    count = doc.page_count
    if count < 3:
        pytest.skip("too short to move a page past the rest of the book")
    catalog = doc.pdf_catalog()
    keys = list(doc.xref_get_keys(catalog))
    moved = digest(doc, 0)
    others = {index: digest(doc, index) for index in range(1, count)}
    MovePages((0,), count - 1).apply(doc)
    assert doc.page_count == count
    assert list(doc.xref_get_keys(catalog)) == keys, f"{path} lost a catalog key"
    assert digest(doc, count - 1) == moved, f"{path} re-rendered the moved sheet"
    for index in range(1, count):
        assert digest(doc, index - 1) == others[index], f"{path} page {index}"
    doc.close()


# --------------------------------------------------------------------- session
def test_the_session_undoes_a_move_and_the_redo_puts_it_back(book):
    session = make_session(book)
    try:
        _version, scope = session.apply(MovePages((0,), 4))
        assert scope == Scope.STRUCTURE
        forward = numbers(session.doc)
        assert forward == [1, 2, 3, 4, 0, 5]
        assert session.renumbering is not None
        assert [session.renumbering.where(page) for page in range(PAGES)] == \
            [forward.index(page) for page in range(PAGES)]
        session.undo()
        assert numbers(session.doc) == list(range(PAGES))
        session.redo()
        assert numbers(session.doc) == forward
    finally:
        session.close()


def test_a_move_followed_by_a_delete_numbers_the_book_once(book):
    session = make_session(book)
    try:
        session.apply(MovePages((5, 0), 3))
        after_move = numbers(session.doc)
        assert after_move == [1, 2, 3, 0, 5, 4]
        session.apply(DeletePages((0,)))
        assert numbers(session.doc) == after_move[1:]
        assert len(session.renumbering.order) == session.doc.page_count == PAGES - 1
    finally:
        session.close()
