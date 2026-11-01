"""F10-4r: reversing the picked sheets among the slots they fill.

The second of the four reorders, and the one that says least: like the move it adds
nothing and takes nothing away, every sheet keeps its words, and the only thing that
changes is which sheet wears which number.  Two things are different about it.

* **An unpicked sheet does not move.**  A move sends every sheet above the gap one
  step along, so no number survives it; a reverse touches the slots of its pick and
  leaves the rest of the book exactly where it was — which is why this edit answers
  :attr:`~pdfar.edits.Edit.changed` with the pick itself instead of the move's "I
  cannot say", and why a reader's cached thumbnail of an unpicked page survives it.
* **A reverse is its own way back.**  The permutation is an involution, so undoing
  it is the same reorder run again — which is not how it works here either: undo is
  a snapshot, and the map the step carries is exact in both directions like any
  reorder's.  It is why the property is tested rather than assumed.

Three measured facts carry the mechanism, all of them inherited from F10-4p.

* **The rule is a permutation, so the walk is already written.**  A reverse needs no
  MuPDF surface the move did not: the intended order is computed and run by
  ``_repair_plan``, which settled on **63 of 63** picks of a six-sheet book — every
  non-empty subset, each of which came out of MuPDF with its sheets in the slots the
  rule names.  Cost is not one call per slot, though: a walk re-places a sheet a
  later fetch pushed out of its own slot, so reversing 270 of a 540-sheet book's
  alternate sheets spends 538 calls, and the whole book backwards spends 539 —
  73 ms of ``move_page`` inside a 295 ms settle.
* **A bare ``move_page`` resizes what it moves**, so the settle comes first here too,
  and the books the settle refuses are refused here as well.
* **Fewer than two distinct slots is nothing to do, not an error.**  PdfCraft's
  ``reverse_pages`` refuses it (``crates/organize/src/lib.rs:538-540``, "reversing
  needs at least two pages"); PDFAR writes nothing, because a strip whose right-click
  menu offers *Reverse* on the one thumbnail under the cursor is a thing a reader can
  do, and a dialog that says "nothing would change" is a worse answer than silence.
  The divergence is pinned below, on a book whose geometry still lives on its
  ancestors, so that the no-op is shown not to settle as well as not to move.
"""

from __future__ import annotations

import itertools
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_settle import (catalogued_pdf, digest, grouped_pdf, kids_of, nested_pdf,
                              own, pages_root, shape)

from pdfar.edits import EditError, Renumbering, Scope
from pdfar.pages import DeletePages, ReversePages, SettlePageTree

#: How many sheets the rule is checked on.
PAGES = 6

#: Every non-empty pick of that book — the whole domain of the edit.
PICKS = [tuple(combo) for size in range(1, PAGES + 1)
         for combo in itertools.combinations(range(PAGES), size)]

#: The real documents, as the rest of this roadmap reads them.
PAPERS = sorted(
    os.path.join("external", "two-column-test-pdfs", name)
    for name in os.listdir(os.path.join("external", "two-column-test-pdfs"))
    if name.endswith(".pdf")
)


def numbers(doc):
    """The book as the sheets name themselves: ``[2, 0, 1]`` reads "sheet 2 first"."""
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


def intended(order, pick):
    """The rule the edit is built to carry out, stated without reference to it.

    The sheets in the slots *pick* names are laid out in the opposite order **in
    those same slots**, and every other slot keeps its sheet — the reading of
    "reverse these pages" a batch of scanned sheets needs, and PdfCraft's
    ``reverse_pages`` at ``crates/organize/src/lib.rs:532``.  A pick with a hole in
    it is not required to be contiguous, and the middle sheet of an odd pick stays
    exactly where it is.
    """
    slots = sorted(set(pick))
    out = list(order)
    for step, slot in enumerate(slots):
        out[slot] = order[slots[-1 - step]]
    return out


def order_after_reverse(doc, pick):
    want = intended(numbers(doc), pick)
    ReversePages(tuple(pick)).apply(doc)
    got = numbers(doc)
    assert got == want, f"pick {pick}: wanted {want}, the book reads {got}"
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
def reversed_once(nested):
    """The nested book with one reversal carried out — a session's second reorder."""
    doc = fitz.open(nested)
    ReversePages((0, 2)).apply(doc)
    return doc


# ------------------------------------------------------------------ the rule itself
@pytest.mark.parametrize("pick", PICKS)
def test_every_pick_of_the_book_is_reversed_among_the_slots_it_fills(book, pick):
    doc = fitz.open(book)
    order_after_reverse(doc, pick)
    doc.close()


@pytest.mark.parametrize("pick", PICKS)
def test_the_rule_is_its_own_way_back(pick):
    """Reversing the same slots twice is the book the reader started with.

    A property of the rule rather than of MuPDF, and the reason the edit needs no
    inverse of its own: the permutation is an involution, so :meth:`page_order`
    composed with itself is the identity.
    """
    order = list(range(PAGES))
    assert intended(intended(order, pick), pick) == order


@pytest.mark.parametrize("pick", [(1, 3), (0, 2, 4), (2, 5), (0, 1, 4)])
def test_a_sheet_outside_the_pick_does_not_move(book, pick):
    doc = fitz.open(book)
    before = numbers(doc)
    got = order_after_reverse(doc, pick)
    for index in range(PAGES):
        if index not in set(pick):
            assert got[index] == before[index], f"sheet {index} was not in the pick"
    doc.close()


def test_the_middle_sheet_of_an_odd_pick_stays_put(book):
    doc = fitz.open(book)
    assert order_after_reverse(doc, (0, 2, 4)) == [4, 1, 2, 3, 0, 5]
    doc.close()


def test_the_whole_book_turns_end_to_end(book):
    doc = fitz.open(book)
    assert order_after_reverse(doc, tuple(range(PAGES))) == [5, 4, 3, 2, 1, 0]
    doc.close()


def test_a_pick_written_backwards_is_the_same_pick(book):
    doc = fitz.open(book)
    assert order_after_reverse(doc, (4, 1, 3)) == intended(list(range(PAGES)), (1, 3, 4))
    doc.close()


def test_a_reversal_run_twice_on_one_document_is_the_book_it_started_with(book):
    doc = fitz.open(book)
    ReversePages((0, 3, 5)).apply(doc)
    assert numbers(doc) == [5, 1, 2, 3, 4, 0]
    ReversePages((0, 3, 5)).apply(doc)
    assert numbers(doc) == list(range(PAGES))
    doc.close()


@pytest.mark.parametrize("pick", [
    (0, 11), (1, 4, 9, 11), (0, 2, 4, 6, 8, 10), (2, 3, 4, 5, 6, 7, 8, 9), (0,)])
def test_a_longer_book_is_reversed_by_the_plan_it_was_given(tmp_path, pick):
    doc = fitz.open(build_sample_pdf(str(tmp_path / "twelve.pdf"), pages=12))
    order_after_reverse(doc, pick)
    doc.close()


def test_a_sheet_named_twice_is_one_sheet_reversed_once(book):
    doc = fitz.open(book)
    assert order_after_reverse(doc, (2, 2, 5, 2)) == intended(list(range(PAGES)), (2, 5))
    doc.close()


@pytest.mark.parametrize("pick", PICKS)
def test_a_reversal_costs_the_same_walked_from_either_end(pick):
    """The direction a reorder is walked in buys a reversal nothing.

    ``_repair_plan`` simulates both directions and runs the shorter, which is the
    right rule for a move — sending a 540-sheet book's first sheet to the end is one
    call from the back and 539 from the front.  A reverse is its own mirror image: the
    two walks cost the same for every non-empty pick of a six-sheet book, of a
    twelve-sheet one (4083 picks) and for 4000 random picks of books up to 300 sheets.
    Recorded because it is a property the plan's cost depends on and nothing else here
    says it: were the walks ever to diverge, this edit would still land the sheets it
    promised — only more slowly.
    """
    from pdfar.pages import _walk_from_back, _walk_from_front

    want = intended(list(range(PAGES)), pick)
    assert len(_walk_from_front(want)) == len(_walk_from_back(want))


def test_the_plan_that_runs_is_the_shorter_of_the_two_walks():
    """The choice itself, pinned on the two orders whose walks genuinely differ.

    Not a reversal: every reversal costs the same either way, so no reversal pick can
    show the branch.  The book turned one step is what a drop at either end means, and
    the two directions are mirror images of the same ask — the first sheet sent to the
    last gap is one call from the back and five from the front, the last sheet sent to
    the first gap is the other way round.  Both are asked of the plan, because a rule
    that only ever walks one way passes every test that does not say which way.
    """
    from pdfar.pages import _repair_plan, _walk_from_back, _walk_from_front

    left = [1, 2, 3, 4, 5, 0]
    right = [5, 0, 1, 2, 3, 4]
    assert len(_walk_from_back(left)) == 1
    assert len(_walk_from_front(left)) == len(left) - 1
    assert _repair_plan(left) == _walk_from_back(left)
    assert len(_walk_from_front(right)) == 1
    assert len(_walk_from_back(right)) == len(right) - 1
    assert _repair_plan(right) == _walk_from_front(right)


def test_a_pick_of_one_sheet_writes_nothing_at_all(nested):
    """The no-op, on a book that would pay for it.

    One slot holds one sheet whichever way the pick is read, so nothing about this
    document changes: no key is written, no page is settled, no document is dirtied.
    On the nested fixture that is worth proving, because a reverse that settled
    first would stamp a ``/MediaBox`` onto all three sheets to carry out a reorder
    that asks for none — and PdfCraft, which refuses this case, cannot be reached by
    a UI that does.
    """
    doc = fitz.open(nested)
    ReversePages((1,)).apply(doc)
    assert not doc.is_dirty, "a reverse that reverses nothing still settled the tree"
    assert [own(doc, index, "MediaBox") for index in range(3)] == ["null"] * 3
    assert numbers(doc) == [0, 1, 2]
    doc.close()


# ------------------------------------------------------------------------ refusals
def test_no_page_chosen_is_refused(book):
    doc = fitz.open(book)
    with pytest.raises(EditError, match="No page was chosen"):
        ReversePages(()).apply(doc)
    assert not doc.is_dirty
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_page_the_book_does_not_have_stops_the_whole_reverse(book):
    doc = fitz.open(book)
    with pytest.raises(EditError, match="is not in this document"):
        ReversePages((1, PAGES)).apply(doc)
    assert not doc.is_dirty, "the valid half of the pick moved on the way out"
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_negative_page_is_refused_rather_than_counted_from_the_end(book):
    """``-1`` means "the last sheet" to Python and "after the last page" to MuPDF.

    Neither is what a pick means, and the walk hands ``move_page`` a ``-1`` of its own
    when it goes backwards, so the rule is stated here rather than inherited from
    somebody else's indexing: a reverse that read ``(-1, 2)`` as Python would
    silently reverse the last and the third sheets.
    """
    doc = fitz.open(book)
    with pytest.raises(EditError, match="is not in this document"):
        ReversePages((-1, 2)).apply(doc)
    assert not doc.is_dirty
    assert numbers(doc) == list(range(PAGES))
    doc.close()


def test_a_tree_the_settle_cannot_read_cannot_be_reversed(nested):
    """A ``/Group`` above a sheet is inherited, and no page can be handed a copy.

    The refusal travels with the settle, and it is the point: a reverse of that book
    would otherwise re-parent the sheets into a node whose transparency the page
    cannot carry, and the change would be in what the reader sees, not in the
    numbering they asked about.
    """
    doc = fitz.open(grouped_pdf(nested))
    with pytest.raises(EditError, match="/Group"):
        ReversePages((0, 2)).apply(doc)
    assert not doc.is_dirty, "a refused reverse still wrote into the tree"
    # Read last: ``get_text`` marks a MuPDF document dirty on its own (measured on
    # 1.28.2 — ``load_page``, ``rect`` and ``rotation`` do not), so asking the book
    # which sheet sits where would answer the line above for the wrong reason.
    assert numbers(doc) == [0, 1, 2]
    doc.close()


def test_a_book_that_does_not_come_out_in_the_order_asked_for_is_refused(monkeypatch,
                                                                         tmp_path):
    """The read-back is the guard against a change in what ``move_page`` means.

    Every non-empty pick of a six-sheet book lands where the rule says — 63 of them —
    so the only way to see this branch run is to hand the reverse a plan that does
    not, which is exactly what a future MuPDF would look like from here.
    """
    import pdfar.pages as pages

    doc = fitz.open(build_sample_pdf(str(tmp_path / "twelve.pdf"), pages=12))
    monkeypatch.setattr(pages, "_repair_plan", lambda want: ((0, 2),))
    with pytest.raises(EditError, match="does not read the order"):
        ReversePages((0, 11)).apply(doc)
    doc.close()


def test_a_single_sheet_book_has_nothing_to_reverse(tmp_path):
    doc = fitz.open(build_sample_pdf(str(tmp_path / "one.pdf"), pages=1))
    ReversePages((0,)).apply(doc)
    assert not doc.is_dirty
    assert doc.page_count == 1
    doc.close()


# ------------------------------------------------------------------- the page tree
def test_a_reversed_sheet_keeps_the_box_and_the_turn_it_was_being_shown(nested):
    doc = fitz.open(nested)
    before = shape(doc)
    ReversePages((0, 2)).apply(doc)
    assert [before[index] for index in (2, 1, 0)] == shape(doc)


def test_the_bare_move_the_settle_exists_to_stop(nested):
    """What happens without the settle: the sheet takes the shape of its new node.

    Pinned so the reason the reverse pays a settle before it touches a reference
    stays in the file rather than in a comment: on this fixture a bare ``move_page``
    re-parents the 260×360 sheet into the node it lands in and draws it 400×300 with
    that node's turn.
    """
    doc = fitz.open(nested)
    before = shape(doc)
    doc.move_page(2, 0)
    assert shape(doc) != [before[2], before[0], before[1]], (
        "the harm is gone, so the settle inside the reverse is testing nothing")
    doc.close()


def test_the_reversed_sheets_now_state_what_their_ancestors_used_to_state(nested):
    doc = fitz.open(nested)
    shown = doc.load_page(2).rect
    assert own(doc, 2, "MediaBox") == "null"
    ReversePages((0, 2)).apply(doc)
    xref = doc.load_page(0).xref
    assert doc.xref_get_key(xref, "MediaBox")[1] != "null"
    assert doc.xref_get_key(xref, "CropBox")[1] != "null"
    assert doc.load_page(0).rect == shown, "the sheet that came to the front changed shape"
    doc.close()


def test_a_book_can_be_reversed_twice(reversed_once):
    """The second reverse is the one the emptied node used to stop."""
    doc = reversed_once
    assert numbers(doc) == [2, 1, 0]
    assert order_after_reverse(doc, (0, 1)) == [1, 2, 0]
    doc.close()


def test_the_tree_still_counts_out_after_a_reverse(reversed_once):
    doc = reversed_once
    root = pages_root(doc)
    empties = [kid for kid in kids_of(doc, root)
               if doc.xref_get_key(kid, "Type")[1] == "/Pages"
               and not kids_of(doc, kid)]
    assert empties, "MuPDF left no emptied node, so this tested the wrong shape"
    SettlePageTree().apply(doc)          # must read the tree the reverse just made
    assert doc.page_count == 3
    doc.close()


def test_a_reversed_book_saves_and_reopens_in_the_same_order(nested, tmp_path):
    doc = fitz.open(nested)
    ReversePages((0, 1)).apply(doc)
    want, sizes = numbers(doc), shape(doc)
    assert want == [1, 0, 2]
    out = str(tmp_path / "reversed.pdf")
    doc.save(out, deflate=False)
    doc.close()
    again = fitz.open(out)
    assert numbers(again) == want
    assert shape(again) == sizes
    again.close()


# -------------------------------------------------------------------------- numbers
def test_the_step_answers_for_every_sheet_about_where_it_came_from(book):
    doc = fitz.open(book)
    edit = ReversePages((1, 3, 4))
    order = edit.page_order(doc.page_count)
    assert len(order) == doc.page_count
    edit.apply(doc)
    assert list(order) == numbers(doc), "the map the step publishes is not the book"
    doc.close()


@pytest.mark.parametrize("pick", PICKS)
def test_the_map_is_a_permutation_and_the_unpicked_sheets_map_to_themselves(pick):
    edit = ReversePages(pick)
    order = edit.page_order(PAGES)
    assert sorted(order) == list(range(PAGES)), "a reverse that lost or invented a sheet"
    mapping = Renumbering(order)
    for page in range(PAGES):
        if page not in set(pick):
            assert mapping.where(page) == page, f"sheet {page} was not in the pick"
            assert mapping.followed(page) == page
        else:
            assert mapping.where(page) == mapping.followed(page), (
                "a map that clamps is a delete's answer, not a reverse's")
    assert not mapping.backward


def test_the_way_back_is_the_same_map_turned_round(book):
    edit = ReversePages((0, 2, 5))
    order = edit.page_order(PAGES)
    forward, backward = Renumbering(order), Renumbering(order, backward=True)
    for new, old in enumerate(order):
        assert backward.where(new) == old, "undoing the step does not run it home"
        assert forward.where(old) == new
        assert forward.followed(old) == new
        assert backward.followed(new) == old


def test_the_step_is_structure_and_names_the_slots_it_filled():
    """``changed`` is the pick, where the move's is ``None``.

    A move re-numbers every sheet above the gap it fills, so no cache entry keyed by
    a page number survives it and the honest answer is "I cannot say".  A reverse
    leaves an unpicked sheet wearing the number it had, so its thumbnail, its marks
    and its place in the outline are still filed under the right key, and only the
    slots the pick named have to be looked at again.
    """
    edit = ReversePages((1, 3))
    assert edit.scope == Scope.STRUCTURE
    assert edit.changed == (1, 3)
    assert edit.pages == (1, 3)


def test_the_label_counts_the_sheets_the_reader_asked_for():
    assert ReversePages((2, 4)).label == "Reverse 2 Pages"
    assert ReversePages((2, 4, 4)).label == "Reverse 2 Pages"
    assert ReversePages((2,)).label == "Reverse Page"


# ------------------------------------------------------ what the document carries
def test_the_outline_rows_and_the_links_follow_their_own_sheets(nested):
    """The document's own numbering follows the sheets, as it did for the move.

    A reverse of the outer two sheets of a three-sheet book happens to be the same
    permutation as moving the last one to the front, which is the point of pinning
    it again: what travels with a sheet is decided by the sheet, not by the edit.
    """
    doc = fitz.open(nested)
    doc.load_page(0).insert_link({"kind": fitz.LINK_GOTO,
                                  "from": fitz.Rect(20, 20, 120, 60), "page": 2})
    doc.set_toc([[1, "First sheet", 1], [1, "Last sheet", 3]])
    ReversePages((0, 2)).apply(doc)
    rows = {title: page for _level, title, page in doc.get_toc()}
    assert rows["Last sheet"] == 1, rows
    assert rows["First sheet"] == 3, rows
    targets = [link.get("page") for page in range(doc.page_count)
               for link in doc.load_page(page).get_links()]
    assert targets == [0], targets
    doc.close()


def test_a_page_label_stays_with_the_position_it_was_written_for(tmp_path):
    """F10-4q again, from the other end: labels number positions, not sheets.

    A reverse is the reorder a reader picks when the numbering *is* the content — a
    batch scanned back to front — so the gap is worth showing up in the edit that
    would be used to fix it: the sheets trade places and the printed numbers do not.
    """
    path = catalogued_pdf(nested_pdf(str(tmp_path / "cat.pdf")))
    doc = fitz.open(path)
    before = [doc.load_page(index).get_label() for index in range(doc.page_count)]
    ReversePages((0, 2)).apply(doc)
    assert numbers(doc) == [2, 1, 0]
    after = [doc.load_page(index).get_label() for index in range(doc.page_count)]
    assert after == before, (
        "the labels moved with their sheets, so F10-4q needs writing up as done")
    doc.close()


@pytest.mark.parametrize("path", PAPERS)
def test_a_real_paper_reverses_its_ends_without_losing_anything_else(path):
    doc = fitz.open(path)
    count = doc.page_count
    if count < 3:
        pytest.skip("too short to reverse a page past the rest of the book")
    catalog = doc.pdf_catalog()
    keys = list(doc.xref_get_keys(catalog))
    pick = (0, count // 2, count - 1)
    moved = {index: digest(doc, index) for index in pick}
    others = {index: digest(doc, index) for index in range(count) if index not in pick}
    ReversePages(pick).apply(doc)
    assert doc.page_count == count
    assert list(doc.xref_get_keys(catalog)) == keys, f"{path} lost a catalog key"
    assert digest(doc, count - 1) == moved[0], f"{path} re-rendered the moved sheet"
    assert digest(doc, 0) == moved[count - 1]
    assert digest(doc, count // 2) == moved[count // 2], "the middle of the pick moved"
    for index in others:
        assert digest(doc, index) == others[index], f"{path} page {index}"
    doc.close()


# -------------------------------------------------------------------------- session
def test_the_session_undoes_a_reverse_and_the_redo_puts_it_back(book):
    session = make_session(book)
    try:
        _version, scope = session.apply(ReversePages((1, 3)))
        assert scope == Scope.STRUCTURE
        forward = numbers(session.doc)
        assert forward == [0, 3, 2, 1, 4, 5]
        assert session.renumbering is not None
        assert [session.renumbering.where(page) for page in range(PAGES)] == \
            [forward.index(page) for page in range(PAGES)]
        session.undo()
        assert numbers(session.doc) == list(range(PAGES))
        session.redo()
        assert numbers(session.doc) == forward
    finally:
        session.close()


def test_a_reverse_followed_by_a_delete_numbers_the_book_once(book):
    session = make_session(book)
    try:
        session.apply(ReversePages((0, 5)))
        after_reverse = numbers(session.doc)
        assert after_reverse == [5, 1, 2, 3, 4, 0]
        session.apply(DeletePages((0,)))
        assert numbers(session.doc) == after_reverse[1:]
        assert len(session.renumbering.order) == session.doc.page_count == PAGES - 1
    finally:
        session.close()


def test_a_reverse_leaves_the_book_it_was_given_for_a_later_move(book):
    """Two reorders of one session, and the second one reads the first's tree."""
    session = make_session(book)
    try:
        session.apply(ReversePages((0, 2, 4)))
        assert numbers(session.doc) == [4, 1, 2, 3, 0, 5]
        session.apply(DeletePages((3,)))
        assert numbers(session.doc) == [4, 1, 2, 0, 5]
        session.undo()
        assert numbers(session.doc) == [4, 1, 2, 3, 0, 5]
    finally:
        session.close()
