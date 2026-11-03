"""F10-4q: what a page-tree step does to a book that numbers its pages.

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

So this file does not fill a gap; it pins a decision where it can actually be broken.
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
* **the one shape that re-aim does not survive** — a range covering exactly the deleted
  sheet collides with the range behind it on the same ``startpage``, which is a defect
  and is pinned here as the before picture F10-4u has to change;
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


def test_a_delete_that_empties_a_range_leaves_two_ranges_on_the_same_slot(book):
    """The one shape MuPDF's own re-aim gets wrong, pinned so the repair is measured.

    A range that covered exactly the deleted sheet keeps its (now shared) ``startpage``
    instead of giving way to the range behind it: ``/Nums`` comes back
    ``0 <<r>> 0 <<D 5>>``, which is not a legal numbering — the spec asks for unique
    keys — and every surviving sheet then reads off the roman run, so the producer's
    ``5 6 7 8 9`` is gone from the reader's view and from the saved file.  PdfCraft's
    delete merges the collision instead (``crates/organize/src/labels.rs:412-416``).
    This is a defect, not a decision; F10-4u is the step that fixes it, and this test is
    the before picture it has to change.
    """
    doc = fitz.open(book)
    try:
        catalog = doc.pdf_catalog()
        doc.xref_set_key(catalog, "PageLabels",
                         "<< /Nums [0 << /S /r /St 1 >> 1 << /S /D /St 5 >>] >>")
        assert printed(doc) == ["i", "5", "6", "7", "8", "9"], printed(doc)
        DeletePages(pages=(0,)).apply(doc)
        assert ranges_of(doc) == [(0, "r", 1), (0, "D", 5)], (
            "the collision merged: F10-4u has landed, and this test should be reading "
            "the merged numbering, not the collision")
        assert printed(doc) == ["i", "ii", "iii", "iv", "v"], printed(doc)
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
