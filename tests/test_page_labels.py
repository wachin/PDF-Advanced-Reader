"""F10-4q and F10-4u: what a page-tree step does to a book that numbers its pages.

``/PageLabels`` is a list of ranges over **positions**: "from sheet 0, roman,
starting at 1; from sheet 2, arabic, starting at 1".  Nothing in a range names a
sheet, so a reorder that leaves the catalog alone keeps the sequence ``i, ii, 1, 2,
3, 4`` and lets the sheets come to it — the sheet that lands on slot 0 is called *i*
whether or not it was, and the one that left slot 0 takes whatever number its new
slot carries.

That is the reading PDFAR takes, and the alternative is not a better answer but a
different question.  Carrying each sheet's number with it would turn a single
``1..N`` range into one range per slot — measured once, outside the suite, on a
540-sheet book whose one-entry catalog came back as 540 entries and 9,483 bytes more on
a 279 KB file — and it would break the case the reverse exists for.  On a book whose
sheets lie end to front on the scanner, the reverse puts the paper in order
(``1 2 3 4 5 6``); numbering the slots prints the same ``1 2 3 4 5 6`` down the new
order, while numbering the sheets follows the paper back down to ``6 5 4 3 2 1`` and
un-does the reverse in exactly the place the reader asked for it.  That last case is a
test below, because it is the argument the whole reading rests on.

So F10-4q's half of this file does not fill a gap; it pins a decision where it can
actually be broken, and F10-4u's half repairs what a delete leaves behind.
The move case is already pinned in ``tests/test_page_move.py`` on a one-range book.
What is pinned here is the shape that one-range books hide:

* **a boundary that can move** — two ranges, so a step that rewrote the catalog
  wrongly would show up as a range starting at the wrong sheet;
* **the reverse against the paper** — a book scanned end to front, where the label and
  the number printed on the sheet are two different things and only one of them is
  supposed to stay put;
* **the delete, which MuPDF re-aims by itself** — the second range's ``startpage``
  comes back 2 → 1 without any code of ours touching it, and the numbering stays
  contiguous rather than leaving a hole where the sheet was;
* **the two shapes re-aim does not survive** — a range that covered exactly the deleted
  sheet lands on the slot the range behind it already names, which is not a legal
  numbering, and a range whose start is left past the shortened book names a sheet that
  is gone.  ``DeletePages`` repairs both, the collision it made and a producer's that
  arrived in the same shape;
* **and the shape re-aim cannot be trusted with at all** — the library leaves the *first
  entry it reads* where it is and shifts the rest, so a producer that listed slot 4 before
  slot 0 has its roman range pushed to ``-1`` by deleting the front sheet.  That is why
  the repair runs before the sheets go as well as after.  A numbering that simply starts
  past the front is the same exemption with nothing to sort it out of, and is pinned here
  as **F10-4w**'s before picture;
* **undo and the saved file**, which are where a library-side fix can quietly not
  be carried;
* **the book that has no numbering at all**, which nothing here may invent one for
  (``Document.set_page_labels([])`` was measured *creating* a ``/PageLabels`` key on
  a catalog that had none, so the guard is that no step calls it);
* **a range written with a prefix and no style**, which must leave the file as
  byte-for-byte the entry the producer wrote.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from pdfar.save import save_document
from test_page_move import numbers

from pdfar.pages import DeletePages, MovePages, ReversePages

#: Six sheets: two of roman front matter, four of arabic body.
PAGES = 6

#: ``(startpage, style, firstpagenum)`` — the two ranges the tests number by.
TWO_RANGES = ((0, "r", 1), (2, "D", 1))

#: What those ranges print, slot by slot.
ROMAN_THEN_ARABIC = ["i", "ii", "1", "2", "3", "4"]


# ------------------------------------------------------------------- the numbered book


def numbered_pdf(path, ranges=TWO_RANGES, pages=PAGES):
    """Stamp *ranges* straight onto the catalog, the way a producer does.

    Written as raw ``/Nums`` rather than through ``set_page_labels`` so the file says
    what it says: the tests then ask whether a step rewrote it, and a setter that
    normalises on the way in would hide that.
    """
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    nums = " ".join(f"{start} << /S /{style} /St {first} >>"
                    for start, style, first in ranges)
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"<< /Nums [{nums}] >>")
    staged = path + ".numbered"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)
    return path


@pytest.fixture
def book(tmp_path):
    """A six-sheet book with a numbering whose boundaries can move."""
    return numbered_pdf(str(tmp_path / "numbered.pdf"))


def printed(doc):
    """What each slot is called, top to bottom."""
    return [doc.load_page(index).get_label() for index in range(doc.page_count)]


def paper(doc):
    """What each slot has *printed on it*, top to bottom — the number on the sheet."""
    return [doc.load_page(index).get_text().split()[-1]
            for index in range(doc.page_count)]


def ranges_of(doc):
    """The catalog's ranges as ``(startpage, style, firstpagenum)``."""
    return [(entry["startpage"], entry.get("style"), entry.get("firstpagenum"))
            for entry in doc.get_page_labels()]


def labels_key(doc):
    """The raw ``/PageLabels`` value, or ``None`` when the catalog does not name one."""
    catalog = doc.pdf_catalog()
    for key in doc.xref_get_keys(catalog):
        if key == "PageLabels":
            return doc.xref_get_key(catalog, "PageLabels")
    return None


def label_of(doc, index):
    """What slot *index* is called, or ``None`` where the numbering does not reach it.

    A range that starts past the front of the book leaves the sheets above it with no
    answer to give: ``Page.get_label`` returns ``''`` only when the whole label list is
    empty, and otherwise hands the slot to ``utils.get_label_pno``, whose
    ``[x for x in labels if x[0] <= pgNo][-1]`` raises ``IndexError`` for a slot below
    every range — despite both docstrings promising an empty string.  So a numbering
    that starts at sheet 3 cannot be read with ``printed``.
    """
    try:
        return doc.load_page(index).get_label()
    except IndexError:
        return None


def stamp(doc, nums):
    """Write a raw ``/Nums`` array onto an open document, the way the fixtures do.

    Stamped rather than built with ``set_page_labels`` because these cases are about
    what the catalog *holds*, and a setter that tidied on the way in would hide it.
    """
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", f"<< /Nums [{nums}] >>")


# ----------------------------------------------------------- the reading, at the model


def test_a_move_leaves_the_numbering_on_the_slots_it_numbers(book):
    """The slot keeps its number and the sheet takes it, which is the decision.

    Pinning the labels alone would pass in a world where the catalog was rewritten to
    the same strings by different ranges, so the ranges are asserted too: nothing was
    invented, nothing was re-pointed.
    """
    doc = fitz.open(book)
    try:
        before, ranges_before = printed(doc), ranges_of(doc)
        MovePages(pages=(4,), target=0).apply(doc)
        assert numbers(doc) == [4, 0, 1, 2, 3, 5], "the block landed where the rule says"
        assert printed(doc) == before == ROMAN_THEN_ARABIC
        assert ranges_of(doc) == ranges_before
        # The sheet that moved is now called by the slot it arrived in.
        assert printed(doc)[0] == "i"
        # And the sheet it displaced took the number of *its* new slot.
        assert printed(doc)[1] == "ii"
    finally:
        doc.close()


def test_a_reverse_turns_the_sheets_under_a_numbering_that_stays(book):
    """The scanned-end-to-front book: the numbers must come out ascending, not follow.

    This is the case that decides the reading.  Before the reverse the numbering reads
    ``i, ii, 1, 2, 3, 4`` down the book; after it the same sequence reads down the
    book, over sheets that have turned to ``5, 4, 3, 2, 1, 0``.  Carrying each sheet's
    label with it would have put ``4, 3, 2, 1, ii, i`` at the top — the descending run
    the reader reversed the sheets to get away from, and measured as such on a book
    scanned end to front.
    """
    doc = fitz.open(book)
    try:
        ReversePages(pages=tuple(range(PAGES))).apply(doc)
        assert printed(doc) == ROMAN_THEN_ARABIC
        assert numbers(doc) == [5, 4, 3, 2, 1, 0]
    finally:
        doc.close()


def test_a_book_scanned_end_to_front_reads_ascending_after_the_reverse(tmp_path):
    """The reader's own question, asked of the paper and the number together.

    Six sheets lie on the scanner glass back to front, so the file holds ``sheet 6``
    first; the producer numbered the slots 1..6, which is what a reader who has just
    reversed them expects to see.  A label that travelled with its sheet would print
    ``6 5 4 3 2 1`` over sheets that now read ``1 2 3 4 5 6`` — the reverse undone in
    the one place the reader was looking.
    """
    path = str(tmp_path / "scanned.pdf")
    doc = fitz.open()
    for sheet in (6, 5, 4, 3, 2, 1):
        doc.new_page(width=200, height=100).insert_text(
            fitz.Point(10, 40), f"sheet {sheet}", fontsize=14)
    doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1}])
    staged = path + ".labelled"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)

    doc = fitz.open(path)
    try:
        assert paper(doc) == ["6", "5", "4", "3", "2", "1"], "the fixture is the bad scan"
        ReversePages(pages=tuple(range(PAGES))).apply(doc)
        assert paper(doc) == printed(doc) == [str(n) for n in range(1, PAGES + 1)]
    finally:
        doc.close()


def test_a_delete_moves_the_ranges_that_start_below_the_hole(book):
    """MuPDF re-aims the catalog itself, so the body keeps starting at 1.

    The second range began at sheet 2; deleting sheet 1 has to bring it to sheet 1 or
    every surviving sheet of the body prints one number too far along.  Measured here
    rather than assumed because it is the library's work, not ours — and a step that
    ever moves to ``Document.select`` would lose it without a word.
    """
    doc = fitz.open(book)
    try:
        DeletePages(pages=(1,)).apply(doc)
        assert ranges_of(doc) == [(0, "r", 1), (1, "D", 1)], (
            "the body's range did not come up with the hole")
        assert printed(doc) == ["i", "1", "2", "3", "4"]
    finally:
        doc.close()


def test_a_delete_that_empties_a_range_gives_way_to_the_numbering_behind_it(book):
    """F10-4u: the range whose only sheet went must not sit on the survivor's slot.

    MuPDF shifts every range below the hole up by itself and pins the F10-4q reading,
    but it never merges: deleting the one sheet a range covered leaves ``/Nums`` with
    two entries naming the same slot — not a legal numbering — and the sheets after the
    gap then read off the *dead* range, so the producer's ``5 6 7 8 9`` is gone from the
    view and from the saved file.  The repair keeps the later range, which is the
    numbering that resumes over the gap.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /r /St 1 >> 1 << /S /D /St 5 >>")
        assert printed(doc) == ["i", "5", "6", "7", "8", "9"], printed(doc)
        DeletePages(pages=(0,)).apply(doc)
        assert ranges_of(doc) == [(0, "D", 5)], ranges_of(doc)
        assert printed(doc) == ["5", "6", "7", "8", "9"], printed(doc)
    finally:
        doc.close()


def test_a_range_emptied_in_the_middle_does_not_swallow_the_one_after_it(book):
    """Four ranges, one of them one sheet wide: the hole is inside, not at the edge.

    The roman run and the ``D 5`` run below it both have width and both stay; only the
    emptied ``a`` run gives way, and to the ``D 3`` run that was already following it.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /r /St 1 >> 1 << /S /D /St 5 >> "
                    "2 << /S /a /St 1 >> 3 << /S /D /St 3 >>")
        assert printed(doc) == ["i", "5", "a", "3", "4", "5"], printed(doc)
        DeletePages(pages=(2,)).apply(doc)
        assert ranges_of(doc) == [(0, "r", 1), (1, "D", 5), (2, "D", 3)], ranges_of(doc)
        assert printed(doc) == ["i", "5", "3", "4", "5"], printed(doc)
    finally:
        doc.close()


def test_two_ranges_emptied_by_one_pick_each_give_way(book):
    """A scattered pick can empty two ranges, and both collisions are the same repair.

    Deleting the ``i`` sheet and the ``a`` sheet out of one six-sheet book leaves two
    pairs on two slots; numbering each pair by its later range is what makes the
    survivors read ``5 7 8 9`` — the numbers the sheets next to the holes were wearing.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /r /St 1 >> 1 << /S /D /St 5 >> "
                    "2 << /S /a /St 1 >> 3 << /S /D /St 7 >>")
        assert printed(doc) == ["i", "5", "a", "7", "8", "9"], printed(doc)
        DeletePages(pages=(0, 2)).apply(doc)
        assert ranges_of(doc) == [(0, "D", 5), (1, "D", 7)], ranges_of(doc)
        assert printed(doc) == ["5", "7", "8", "9"], printed(doc)
    finally:
        doc.close()


def test_a_range_whose_sheets_all_went_is_not_left_naming_a_slot(book):
    """The tail case: a range can end up starting past the last sheet of the book.

    MuPDF counts only the deletions *above* a range's start, so numbering the last two
    sheets of a book and deleting them leaves ``/Nums`` naming slot 4 of a four-sheet
    file.  It covers nothing, and a numbering that covers nothing is not a numbering.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /D /St 1 >> 4 << /S /r /St 1 >>")
        assert printed(doc) == ["1", "2", "3", "4", "i", "ii"], printed(doc)
        DeletePages(pages=(4, 5)).apply(doc)
        assert doc.page_count == 4
        assert ranges_of(doc) == [(0, "D", 1)], ranges_of(doc)
        assert printed(doc) == ["1", "2", "3", "4"], printed(doc)
    finally:
        doc.close()


def test_the_last_numbering_going_leaves_the_book_plain_but_says_so(book):
    """When every range covered deleted sheets, the numbering is gone — not invented.

    This book numbers its last three sheets and leaves the first three above the
    numbering, which is a shape a producer can write and the only one where a delete
    can empty every range at once: a range that starts at slot 0 always has a sheet
    left under it.  ``set_page_labels([])`` keeps the catalog key and writes an empty
    ``/Nums``, which is what a book with no numbering looks like to the library — and
    it is the safer of the two ways to say it, because the sheets above a *live*
    range's start have no label to read while the sheets of an empty numbering are
    handed ``''``.  What F10-4q forbids is reaching for that setter on a book that
    never had a ``/PageLabels`` at all.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "3 << /S /D /St 9 >>")
        assert [label_of(doc, n) for n in (3, 4, 5)] == ["9", "10", "11"]
        assert [label_of(doc, n) for n in (0, 1, 2)] == [None, None, None], (
            "the fixture is a numbering that starts past the front of the book")
        DeletePages(pages=(3, 4, 5)).apply(doc)
        assert doc.page_count == 3
        assert ranges_of(doc) == [], ranges_of(doc)
        assert printed(doc) == ["", "", ""], printed(doc)
    finally:
        doc.close()


def test_a_numbering_no_delete_touched_is_not_written_again(book):
    """The repair rewrites the catalog only when the delete actually broke it.

    ``set_page_labels`` re-creates every range object, so a delete that emptied nothing
    must leave the producer's ``/Nums`` byte-for-byte where it was — the same rule the
    outline prune runs by, for the same reason.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /r /St 1 >> 2 << /S /D /St 5 >>")
        raw = labels_key(doc)
        DeletePages(pages=(3,)).apply(doc)
        assert labels_key(doc) == raw, "an untouched numbering was rewritten"
        assert printed(doc) == ["i", "ii", "5", "6", "7"], printed(doc)
    finally:
        doc.close()


def test_a_producer_who_wrote_two_ranges_on_one_slot_is_cleaned_up(book):
    """The collision need not come from us: a file can arrive this way.

    Repairing what a delete made means repairing the same shape a producer wrote, and
    the answer is the same — the numbering that resumes is the one named last.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /S /r /St 1 >> 0 << /S /D /St 5 >>")
        DeletePages(pages=(1,)).apply(doc)
        assert ranges_of(doc) == [(0, "D", 5)], ranges_of(doc)
        assert printed(doc) == ["5", "6", "7", "8", "9"], printed(doc)
    finally:
        doc.close()


def test_the_merged_numbering_survives_undo_and_the_saved_file(book, tmp_path):
    """The repair is a catalog write, so both places a write can be lost have to be asked.

    Undo restores the numbering the delete found, and Save As carries the merged one out
    of the application — which is the whole point: the file a reader hands on must not
    be the one that reads ``i ii iii iv v`` where the producer wrote ``5 6 7 8 9``.
    """
    path = numbered_pdf(str(tmp_path / "one-wide.pdf"),
                        ranges=((0, "r", 1), (1, "D", 5)))
    session = make_session(path)
    try:
        session.apply(DeletePages(pages=(0,)))
        assert ranges_of(session.doc) == [(0, "D", 5)], ranges_of(session.doc)
        saved = save_document(session, str(tmp_path / "merged.pdf")).path
        session.undo()
        assert ranges_of(session.doc) == [(0, "r", 1), (1, "D", 5)], ranges_of(session.doc)
        assert printed(session.doc) == ["i", "5", "6", "7", "8", "9"]
    finally:
        session.close()

    with fitz.open(saved) as back:
        assert ranges_of(back) == [(0, "D", 5)], ranges_of(back)
        assert printed(back) == ["5", "6", "7", "8", "9"], printed(back)


def test_a_prefix_only_range_is_carried_by_the_repair_not_flattened(book):
    """The entry the tests cannot read is the one the repair must still write faithfully.

    A range with a ``/P`` and no ``/S`` comes back from ``get_page_labels()`` with no
    ``style`` key at all; handing that dict straight back is what keeps the prefix, and
    putting a default in its place would print a number where the producer printed none.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "0 << /P (A-) >> 1 << /S /r /St 1 >> 2 << /S /D /St 5 >>")
        assert printed(doc) == ["A-", "i", "5", "6", "7", "8"], printed(doc)
        DeletePages(pages=(1,)).apply(doc)
        assert printed(doc) == ["A-", "5", "6", "7", "8"], printed(doc)
        assert ranges_of(doc) == [(0, None, 1), (1, "D", 5)], ranges_of(doc)
    finally:
        doc.close()


def test_a_numbering_the_producer_wrote_as_a_number_tree_is_still_repaired(book):
    """``/PageLabels`` may be a number *tree*, and the delete has to be repaired there too.

    MuPDF flattens the tree into a ``/Nums`` array while re-aiming it — measured: the
    ``/Kids`` form comes back ``<</Nums[0<</S/r/St 1>>2<</S/D/St 5>>]>>`` from a bare
    ``delete_page``, and a move leaves the tree alone — so the walk reads the flat form
    either way.  The case is here because that is how a real producer writes a long
    numbering, and the one-wide roman run inside it collides exactly as the array form
    does.
    """
    doc = fitz.open(book)
    try:
        doc.xref_set_key(
            doc.pdf_catalog(), "PageLabels",
            "<< /Kids [ << /Limits [0 0] /Nums [0 << /S /r /St 1 >>] >>"
            " << /Limits [1 5] /Nums [1 << /S /D /St 5 >>] >> ] /Limits [0 5] >>")
        assert printed(doc) == ["i", "5", "6", "7", "8", "9"], printed(doc)
        DeletePages(pages=(0,)).apply(doc)
        assert ranges_of(doc) == [(0, "D", 5)], ranges_of(doc)
        assert printed(doc) == ["5", "6", "7", "8", "9"], printed(doc)
    finally:
        doc.close()


def test_a_numbering_the_producer_listed_out_of_order_is_sorted_before_the_delete(book):
    """A merge may only fold entries that name the *same* slot, so the walk sorts first.

    ``get_page_labels()`` hands back the file's order, not a sorted one — measured: a
    catalog listing slot 4 before slot 0 reads back ``[(4, 'D', 40), (0, 'r', 1)]`` — so a
    walk that folded "anything not after the entry before it" would collapse two ranges
    that do not touch into one and delete a numbering the pick never touched.

    Sorting *before* the delete is the other half, and it is not tidiness either: the
    library's re-aim leaves the first entry it reads where it is and shifts the rest, so
    on this file it shifts the roman range out of the document — measured, a bare
    ``delete_page(0)`` writes ``-1 << /S /r /St 1 >>`` and the book reads
    ``ii iii iv v 40``.  Put in order first, the exempt entry is the one at slot 0, which
    has nowhere to go, and every range below the hole comes up by one as it should.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "4 << /S /D /St 40 >> 0 << /S /r /St 1 >>")
        assert ranges_of(doc) == [(4, "D", 40), (0, "r", 1)], ranges_of(doc)
        assert printed(doc) == ["i", "ii", "iii", "iv", "40", "41"], (
            "a viewer reading the file sorts it already")
        DeletePages(pages=(0,)).apply(doc)
        assert ranges_of(doc) == [(0, "r", 1), (3, "D", 40)], ranges_of(doc)
        assert printed(doc) == ["i", "ii", "iii", "40", "41"], printed(doc)
    finally:
        doc.close()


def test_a_numbering_that_starts_past_the_front_is_left_there_by_the_delete(book):
    """The before picture for **F10-4w**: the re-aim exempts the first entry it reads.

    ``1 << /S /r /St 1 >> 4 << /S /D /St 40 >>`` numbers sheets 1-3 and 4-5 and leaves
    sheet 0 out, which a producer may write and MuPDF may read.  Deleting sheet 0 ought to
    bring the roman run down to slot 0; measured, it does not move — the re-aim shifts
    every entry *after* the first one — while the arabic run comes up from 4 to 3, so the
    book is left ``nothing | i ii 40 41``: the front sheet has no label to read and the
    numbering is a sheet short at the bottom.  Sorting is not the answer (this array is
    already in order) and clamping slot 0 would invent a numbering for a sheet the
    producer deliberately left out, so F10-4u repairs nothing here and this case pins what
    was found instead.
    """
    doc = fitz.open(book)
    try:
        stamp(doc, "1 << /S /r /St 1 >> 4 << /S /D /St 40 >>")
        DeletePages(pages=(0,)).apply(doc)
        assert ranges_of(doc) == [(1, "r", 1), (3, "D", 40)], ranges_of(doc)
        assert [label_of(doc, n) for n in range(PAGES - 1)] == (
            [None, "i", "ii", "40", "41"]), "slot 0 is left with no range to read"
    finally:
        doc.close()


def test_a_numbering_with_nothing_to_say_about_a_step_is_left_alone(tmp_path):
    """A book with no ``/PageLabels`` must not gain one, in any of the three steps.

    ``set_page_labels([])`` was measured *writing* the key onto a catalog that had
    none, so the guard is that no page-tree step reaches for the setter at all.
    """
    path = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    for edit in (MovePages(pages=(0,), target=2), ReversePages(pages=(1, 3)),
                 DeletePages(pages=(2,))):
        doc = fitz.open(path)
        try:
            assert labels_key(doc) is None, "the fixture is not a numbered book"
            edit.apply(doc)
            assert labels_key(doc) is None, edit
            assert doc.get_page_labels() == []
        finally:
            doc.close()


def test_a_range_the_tests_do_not_understand_leaves_the_file_as_it_was(book):
    """A prefix with no style is not a numbering, and nothing here normalises it.

    Rewriting a catalog the step did not need to rewrite would turn a reorder into a
    repair job on the producer's file; the raw value is compared, not the reading of
    it, because two different ``/Nums`` arrays can print the same labels.
    """
    doc = fitz.open(book)
    try:
        catalog = doc.pdf_catalog()
        doc.xref_set_key(catalog, "PageLabels",
                         " << /Nums [0 << /P (A-) >> 1 << /S /D /St 10 >>] >> ")
        raw = doc.xref_get_key(catalog, "PageLabels")
        assert printed(doc)[0] == "A-", printed(doc)
        MovePages(pages=(0,), target=3).apply(doc)
        assert doc.xref_get_key(catalog, "PageLabels") == raw
    finally:
        doc.close()


# ------------------------------------------------------------- through the session


def test_undo_puts_back_the_ranges_the_delete_moved(book):
    """The delete's re-aim is library work on the live copy, so undo has to carry it.

    A session that restored the page tree but not the catalog would leave the reader
    with a book whose body starts at 2 — the one number too far, in the other
    direction.
    """
    session = make_session(book)
    try:
        with fitz.open(book) as before:
            labels_before, ranges_before = printed(before), ranges_of(before)
        session.apply(DeletePages(pages=(1,)))
        assert ranges_of(session.doc) == [(0, "r", 1), (1, "D", 1)]
        session.undo()
        assert printed(session.doc) == labels_before
        assert ranges_of(session.doc) == ranges_before
    finally:
        session.close()


def test_the_saved_file_numbers_the_slots_the_reader_reordered(book, tmp_path):
    """What leaves the application has to be what the reader was shown.

    Save As is a full rewrite, so this is the one place the numbering is read back out
    of bytes rather than out of the document MuPDF was editing.
    """
    session = make_session(book)
    try:
        session.apply(MovePages(pages=(4,), target=0))
        saved = save_document(session, str(tmp_path / "moved.pdf")).path
        live_labels, live_sheets = printed(session.doc), numbers(session.doc)
    finally:
        session.close()

    with fitz.open(saved) as back:
        assert printed(back) == live_labels == ROMAN_THEN_ARABIC
        assert numbers(back) == live_sheets
        assert ranges_of(back) == [(0, "r", 1), (2, "D", 1)]


def test_a_bookmark_follows_its_sheet_while_the_number_stays_with_the_slot(book,
                                                                           tmp_path):
    """The two answers in one file, because a reader will meet them together.

    The outline is re-derived by MuPDF from the page objects, so an entry travels with
    the sheet it named; the numbering names slots and does not.  Neither is a bug —
    the bookmark says *where that content is now* and the number says *what this slot
    is called* — and the pair is pinned so a future change to either half has to
    disagree with the other on purpose.
    """
    path = book
    doc = fitz.open(path)
    doc.set_toc([[1, "Front matter", 1], [1, "Body", 3], [1, "Last sheet", 6]])
    staged = path + ".toc"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)

    session = make_session(path)
    try:
        with fitz.open(path) as opened:
            body_before = dict((title, page)
                               for _level, title, page in opened.get_toc())["Body"]
            number_before = printed(opened)[body_before - 1]
        session.apply(MovePages(pages=(4,), target=0))
        doc = session.doc
        rows = {title: page for _level, title, page in doc.get_toc()}
        assert rows["Body"] == 4, "the entry travelled with the sheet it named"
        assert numbers(doc)[rows["Body"] - 1] == 2, "and that is still the same sheet"
        assert printed(doc)[rows["Body"] - 1] == "2" != number_before, (
            "the number stayed with the slot, so the sheet's own changed")
    finally:
        session.close()
