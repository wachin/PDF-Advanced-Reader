"""F10-4e: taking pages out of the document.

Deleting a page is the first page-tree edit that can destroy the document it is
asked politely to trim, and the first whose effects are not confined to the pages
it names: every page above the cut takes the number of the one below it, so the
marks, the outline, the saved bookmarks and the reader's own place in the book are
all pointing one page further along than they meant to.

Four measured facts carry the design.

* ``delete_page(-1)`` is **accepted** and deletes the *last* page: MuPDF counts a
  negative index from the end.  The range form, ``delete_pages(-1, -1)``, does
  refuse — so nothing here can rely on the file layer noticing an off-by-one in the
  single-page direction, and the range is checked by the edit either way, which is
  also where the message that names the page comes from.
* A document whose last page has been deleted **cannot be saved** — MuPDF raises
  ``ValueError: cannot save with zero pages`` at save time, long after the edit that
  promised it, and takes the page out first without comment.
* The cost of a delete is the cost of **one page-tree rebuild**, whatever the
  range: on the 540-page book, one page out took 44 ms and a hundred out took
  45 ms, while the same hundred removed one at a time took 2055 ms.  So a pick is
  coalesced into contiguous runs and each run goes in one call.
* An outline entry that named a deleted page is left behind pointing at page
  ``-1``, which a click cannot follow; entries naming survivors shift correctly.
  Pruning those rows can then leave a tree MuPDF refuses to write at all — a first
  entry deeper than level 1, or a step of two levels where the row between them went.
  Links, by contrast, MuPDF removes on its own.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session

from pdfar.annotations import AnnotationManager
from pdfar.edits import EditError, Scope
from pdfar.pages import DeletePages, contiguous_runs
from pdfar.save import save_document

#: A box on the sample page that no fixture text touches.
MARK = (120.0, 12.0, 190.0, 42.0)

#: How many pages the fixtures are built with; the sample's first line names its page.
PAGES = 5


def identities(doc):
    """The first line of every page, which says which original page sits where.

    The sample document writes ``"Page N content"`` on every page but the first, so
    a delete that moved a page over, or took the wrong one, shows up here rather
    than passing on a page count that happens to be right.
    """
    out = []
    for index in range(doc.page_count):
        lines = [line for line in doc[index].get_text().splitlines() if line.strip()]
        out.append(lines[0] if lines else "")
    return out


@pytest.fixture
def book(tmp_path):
    """Five pages, one mark on each, and an outline naming the first, third, fifth.

    The page text is the sample fixture's, so :func:`identities` says which page is
    where; the mark and the outline are added on top because both are what a delete
    has to account for.
    """
    path = str(tmp_path / "book.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        page = doc.new_page(width=200, height=100)
        first = ("Hello PDFAR world" if index == 0
                 else f"Page {index + 1} content")
        page.insert_text(fitz.Point(10, 30), first, fontsize=12)
        page.insert_text(fitz.Point(10, 70), "alpha beta gamma", fontsize=12)
        page.draw_rect(fitz.Rect(*MARK), color=(0, 0, 0), fill=None)
    doc.set_toc([[1, "front", 1], [2, "front-a", 3], [1, "back", 5]])
    doc.save(path)
    doc.close()
    return path


class RecordingDoc:
    """A document that keeps the calls rather than the pages.

    The number of page-tree rebuilds one edit asks for is invisible from the
    result: ten calls and one leave the same document.  It is only visible on the
    clock, which is no way to write a test, so the shape PyMuPDF exposes is
    mirrored here and the calls are watched directly.
    """

    def __init__(self, page_count: int = 10, toc=None):
        self.page_count = page_count
        self.toc = list(toc or [])
        self.calls: list = []

    def delete_pages(self, start: int, end: int) -> None:
        self.calls.append(("run", start, end))
        self.page_count -= end - start + 1

    def delete_page(self, index: int) -> None:
        self.calls.append(("page", index))
        self.page_count -= 1

    def get_toc(self, simple: bool = True) -> list:
        return [entry[:3] for entry in self.toc]

    def set_toc(self, toc: list) -> None:
        self.calls.append(("toc", len(toc)))
        self.toc = list(toc)


# --------------------------------------------------------------------------- the pages
def test_the_named_pages_leave_and_the_rest_keep_their_order(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(DeletePages(pages=(1,)))
        assert session.doc.page_count == 2
        assert identities(session.doc) == ["Hello PDFAR world", "Page 3 content"]
    finally:
        session.close()


def test_a_page_named_twice_leaves_once(tmp_path):
    """The list says *which* pages, not how many times it was written.

    Repeating an index would otherwise take a second page with it: measured on a
    three-page document, ``delete_page(2)`` twice removes page 3 and then page 2.
    """
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(DeletePages(pages=(2, 2)))
        assert identities(session.doc) == ["Hello PDFAR world", "Page 2 content"]
    finally:
        session.close()


def test_the_whole_pick_leaves_in_one_step(tmp_path):
    """The pages that stay are the ones nobody named, in the order they were."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=6)
    session = make_session(path)
    try:
        session.apply(DeletePages(pages=(1, 3, 4)))
        assert identities(session.doc) == ["Hello PDFAR world", "Page 3 content",
                                           "Page 6 content"]
    finally:
        session.close()


# --------------------------------------------------------------------------- refusals
def test_an_empty_list_is_refused(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        with pytest.raises(EditError, match="No page was chosen"):
            session.apply(DeletePages(pages=()))
        assert session.doc.page_count == 3
        assert not session.history.can_undo
    finally:
        session.close()


@pytest.mark.parametrize("index", [5, 99])
def test_a_page_outside_the_document_is_refused(book, index):
    """Refused here, in a sentence, rather than as ``bad page number(s)``.

    MuPDF does refuse an out-of-range *range* — measured, ``delete_pages(4, 4)`` and
    ``delete_pages(99, 99)`` on a four-page document both raise that — so this check
    buys the wording, and the fact that nothing is cut before it is raised.
    """
    session = make_session(book)
    try:
        with pytest.raises(EditError, match="is not in this document"):
            session.apply(DeletePages(pages=(index,)))
        assert session.doc.page_count == PAGES
    finally:
        session.close()


@pytest.mark.parametrize("index", [-1, -5])
def test_a_negative_page_is_refused(book, index):
    """A negative index is not "no such page" to MuPDF: it counts from the end.

    Measured: ``delete_page(-1)`` on a four-page document succeeds and removes the
    last page.  So an off-by-one that reached the single-page API would silently take
    a page nobody named, at the other end of the book from the one intended.  The
    range API does refuse one, which is why this test has to read the sentence.
    """
    session = make_session(book)
    try:
        with pytest.raises(EditError, match="is not in this document"):
            session.apply(DeletePages(pages=(index,)))
        assert session.doc.page_count == PAGES
    finally:
        session.close()


def test_a_list_with_one_bad_page_removes_nothing(book):
    """Refused whole, so the pages that were named do not leave on the way out."""
    session = make_session(book)
    try:
        with pytest.raises(EditError, match="is not in this document"):
            session.apply(DeletePages(pages=(0, 99)))
        assert session.doc.page_count == PAGES
        assert not session.history.can_undo
    finally:
        session.close()


@pytest.mark.parametrize("pages, count", [((0,), 1), ((0, 1, 2), 3),
                                          ((0, 1, 2, 3, 4), 5)])
def test_a_request_that_empties_the_document_is_refused(tmp_path, pages, count):
    """A document with no pages cannot be saved, so it is not something to make.

    MuPDF takes the last page out without a word and raises ``cannot save with zero
    pages`` later — at the save, after the edit was accepted, undoable and shown to
    the user as done.  Refusing the request where it is made keeps that promise
    unbroken, and says which page is needed.
    """
    source = build_sample_pdf(tmp_path / "doc.pdf", pages=count)
    session = make_session(source)
    try:
        with pytest.raises(EditError, match="a page is needed to save it"):
            session.apply(DeletePages(pages=pages))
        assert session.doc.page_count == count
        assert not session.history.can_undo
    finally:
        session.close()


def test_all_but_one_page_stays_possible(book):
    """The refusal is about the empty document, not about a drastic one."""
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(0, 1, 2, 3)))
        assert session.doc.page_count == 1
    finally:
        session.close()


# -------------------------------------------------------------------------- the runs
def test_a_pick_is_cut_into_contiguous_runs():
    """Each run is one page-tree rebuild, so a block is asked for as one run."""
    assert contiguous_runs((1, 2, 3, 7)) == ((1, 3), (7, 7))
    assert contiguous_runs((4,)) == ((4, 4),)
    assert contiguous_runs((3, 1, 2, 9)) == ((1, 3), (9, 9))
    assert contiguous_runs((5, 5, 5)) == ((5, 5),)
    assert contiguous_runs(()) == ()


def test_a_block_is_removed_in_one_rebuild_not_one_per_page():
    """The cost contract, watched on the calls rather than on the clock."""
    doc = RecordingDoc(page_count=10)
    DeletePages(pages=(1, 2, 3, 7)).apply(doc)
    assert doc.calls == [("run", 7, 7), ("run", 1, 3)]
    assert doc.page_count == 6


def test_a_scattered_pick_asks_for_one_rebuild_per_page():
    """Nothing is invented to make the worst case cheap — it is named in the test.

    Four pages, none of them beside another, are four runs, and MuPDF rebuilds the
    page tree for each.  The edit still coalesces rather than deleting one page at
    a time, because a *block* is what a reader actually picks.
    """
    doc = RecordingDoc(page_count=20)
    DeletePages(pages=(1, 3, 5, 7)).apply(doc)
    assert [call for call in doc.calls if call[0] == "run"] == [
        ("run", 7, 7), ("run", 5, 5), ("run", 3, 3), ("run", 1, 1)]


# ---------------------------------------------------------------------------- outline
def test_the_entry_that_named_a_deleted_page_is_dropped(book):
    """``-1`` is where MuPDF leaves it: an outline row that goes nowhere.

    Measured on a five-page document: after the page it named was deleted the
    entry reads ``[2, 'front-a', -1]`` with its destination demoted to kind 0,
    while the entries naming survivors shift to the pages they now mean.
    """
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(2,)))
        assert [(level, title, page)
                for level, title, page in session.doc.get_toc()] == [
            (1, "front", 1), (1, "back", 4)]
    finally:
        session.close()


def test_the_entries_that_survive_keep_their_level_and_their_destination(book):
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(4,)))
        toc = session.doc.get_toc(simple=False)
        assert [(entry[0], entry[1], entry[2]) for entry in toc] == [
            (1, "front", 1), (2, "front-a", 3)]
        assert [entry[3]["to"] for entry in toc] == [fitz.Point(72.0, 36.0),
                                                      fitz.Point(72.0, 36.0)]
    finally:
        session.close()


def test_an_outline_that_lost_nothing_is_left_alone():
    """Rewriting the outline costs new objects for every entry that stayed.

    MuPDF's ``set_toc`` writes the whole tree again, so pruning when no entry died
    would churn the file for a change the user cannot see.
    """
    doc = RecordingDoc(page_count=10, toc=[[1, "one", 5]])
    DeletePages(pages=(0,)).apply(doc)
    assert [call for call in doc.calls if call[0] == "toc"] == []


def test_the_children_of_a_deleted_row_are_promoted_not_dropped(book):
    """Pruning the row a reader can still use would be worse than the dead one.

    MuPDF refuses an outline whose first entry is not level 1, so the tree that is
    left when a top-level row's page goes has to be lifted rather than written as
    it stands — measured: ``set_toc`` raises ``hierarchy level of item 0 must be 1``.
    """
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(0,)))
        assert [(level, title, page)
                for level, title, page in session.doc.get_toc()] == [
            (1, "front-a", 2), (1, "back", 4)]
    finally:
        session.close()


def test_a_row_left_two_levels_deep_is_lifted_one(tmp_path):
    """The same repair, where the gap is in the middle rather than at the head.

    With ``a / a1 / a1a`` stacked, deleting the page ``a1`` named leaves ``a1a``
    under ``a`` — two levels down where one was.  MuPDF calls that
    ``bad hierarchy level in row 1`` and refuses the whole outline.
    """
    path = str(tmp_path / "stack.pdf")
    doc = fitz.open()
    for _ in range(4):
        doc.new_page(width=200, height=100)
    doc.set_toc([[1, "a", 1], [2, "a1", 2], [3, "a1a", 3], [1, "b", 4]])
    doc.save(path)
    doc.close()

    session = make_session(path)
    try:
        session.apply(DeletePages(pages=(1,)))
        assert session.doc.get_toc() == [[1, "a", 1], [2, "a1a", 2], [1, "b", 3]]
    finally:
        session.close()


def test_an_outline_that_named_only_deleted_pages_is_gone(book):
    """Nothing left to lift: the outline goes with the pages it described."""
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(0, 2, 4)))
        assert session.doc.page_count == 2
        assert session.doc.get_toc() == []
    finally:
        session.close()


# ------------------------------------------------------------- intent vs invalidation
def test_the_edit_is_a_structure_edit_that_cannot_name_the_pages_it_changed(book):
    """Naming the pages it removes is not naming the pages that moved.

    ``pages`` is what the user asked for; ``changed`` is what a cache keyed by page
    number has to look at again.  A delete shifts every page above the cut onto a
    new number, which is not a set the edit can write down, so it says nothing and
    the reader re-reads the document — the safe direction, and the one the base
    class already defines.
    """
    edit = DeletePages(pages=(2,))
    assert edit.scope == Scope.STRUCTURE
    assert edit.pages == (2,)
    assert edit.changed is None
    assert edit.label == "Delete Page"
    assert DeletePages(pages=(2, 3, 4)).label == "Delete 3 Pages"


def test_a_deletion_restamps_the_marks_that_moved_up(book):
    """The list in the comments panel follows the pages, not the numbers they had.

    With ``changed`` left at the deleted index the reload reads that one page and
    keeps every entry above it under the number it was read from — so the mark on
    the last page of a five-page book is listed on a page that now holds someone
    else's ink, and clicking it in the panel jumps past the mark it names.
    """
    session = make_session(book)
    manager = AnnotationManager(session)
    try:
        marks = [manager.add_shape(index, "rect", rect=MARK)
                 for index in range(PAGES)]
        assert all(marks)
        manager.reload_from_document()
        assert [manager.get_annotation(mark.id).page for mark in marks] == (
            list(range(PAGES)))

        session.apply(DeletePages(pages=(1,)))
        manager.reload_from_document()

        kept = [manager.get_annotation(mark.id) for mark in marks]
        assert kept[1] is None, "the mark on the page that went is off the list"
        assert [ann.page for ann in kept if ann] == [0, 1, 2, 3], (
            "the marks above the cut moved with their pages")
    finally:
        session.close()


# ----------------------------------------------------------------------- undo and file
def test_undo_returns_every_page_and_redo_takes_it_again(book):
    session = make_session(book)
    manager = AnnotationManager(session)
    try:
        marks = [manager.add_shape(index, "rect", rect=MARK)
                 for index in range(PAGES)]
        before = identities(session.doc)
        session.apply(DeletePages(pages=(1, 2, 3)))
        assert identities(session.doc) == [before[0], before[4]]

        session.undo()
        assert identities(session.doc) == before
        manager.reload_from_document()
        assert [manager.get_annotation(mark.id).page for mark in marks] == (
            list(range(PAGES))), "the marks come back on the pages they were made on"
        assert session.doc.get_toc() == [[1, "front", 1], [2, "front-a", 3],
                                         [1, "back", 5]]

        session.redo()
        manager.reload_from_document()
        assert identities(session.doc) == [before[0], before[4]]
        assert [ann.page if ann else None for ann in
                (manager.get_annotation(mark.id) for mark in marks)] == (
            [0, None, None, None, 1])
        assert [(level, title, page) for level, title, page
                in session.doc.get_toc()] == [(1, "front", 1), (1, "back", 2)]
    finally:
        session.close()


def test_the_document_on_disk_is_the_one_with_the_pages_out(book):
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(0, 1)))
        saved = save_document(session).path
        names = identities(session.doc)
    finally:
        session.close()
    with fitz.open(saved) as back:
        assert identities(back) == names
        assert back.page_count == PAGES - 2
