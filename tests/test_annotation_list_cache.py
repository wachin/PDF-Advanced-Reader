"""F10-3h: an annotation edit re-reads the pages it touched, not the whole book.

The document is the annotation store, so every accepted edit means re-deriving the
list the interface shows.  Walking every page for that was measured at 170–300 ms
on the 540-page book against 60 ms for the edit itself (F10-3e) — the cost of the
list, not of the change.

An edit already knows which pages it wrote to, so the list now asks for those and
keeps what it had read of the rest.  The tests count page reads, because "the
answer is right either way" is not the property under test.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.annotations import AnnotationManager
from pdfar.edits import Edit, Scope, SetMetadata

PAGES = 8


@pytest.fixture(autouse=True)
def app(qapp):
    return qapp


@pytest.fixture
def session(tmp_path):
    handle = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=PAGES))
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def manager(session):
    return AnnotationManager(session)


@pytest.fixture
def page_reads(monkeypatch):
    """Every page the process loads, in order — the manager is the only reader here."""
    reads: list = []
    original = fitz.Document.load_page

    def counting(self, index, *args, **kwargs):
        reads.append(index)
        return original(self, index, *args, **kwargs)

    monkeypatch.setattr(fitz.Document, "load_page", counting)
    return reads


def reads_since(reads, mark):
    return reads[mark:]


class MysteryEdit(Edit):
    """An edit that changes a page's annotations and does not say which one."""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Mystery"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(4)
        page.add_text_annot(fitz.Point(20.0, 20.0), "unattributed")


# ------------------------------------------------------------------ what gets re-read
def test_an_edit_rereads_the_page_it_touched(manager, page_reads):
    manager.add_note(2, (30.0, 30.0), "on page three")
    manager.add_highlight(5, [(10.0, 10.0, 60.0, 20.0)])
    mark = len(page_reads)

    manager.add_note(7, (30.0, 30.0), "on page eight")

    reads = reads_since(page_reads, mark)
    assert 7 in reads
    assert 2 not in reads and 5 not in reads, \
        "an untouched page was walked again"


def test_an_untouched_page_keeps_the_model_it_had(manager, page_reads):
    """The cached row is the same object: the list was not rebuilt under it."""
    note = manager.add_note(2, (30.0, 30.0), "kept")
    highlight = manager.add_highlight(5, [(10.0, 10.0, 60.0, 20.0)])

    manager.add_note(7, (30.0, 30.0), "new")

    assert manager.get_annotation(note.id) is note
    assert manager.get_annotation(highlight.id) is highlight


def test_a_highlight_over_several_pages_rereads_each_of_them(manager, page_reads):
    mark = len(page_reads)

    manager.add_highlights({1: [(10.0, 10.0, 60.0, 20.0)],
                            3: [(10.0, 40.0, 60.0, 50.0)]})

    reads = reads_since(page_reads, mark)
    assert 1 in reads and 3 in reads
    assert 2 not in reads


def test_an_edit_that_touches_no_page_rereads_nothing(manager, page_reads):
    """A title is not a comment, and the list should not have to find that out."""
    manager.add_note(2, (30.0, 30.0), "there")
    mark = len(page_reads)

    manager.session.apply(SetMetadata((("title", "renamed"),)))
    manager.reload_from_document()

    assert reads_since(page_reads, mark) == []


def test_an_edit_that_cannot_say_rereads_the_document(manager, page_reads):
    """The safe default: an edit with no pages declared is a reason to look again."""
    manager.add_note(2, (30.0, 30.0), "there")
    mark = len(page_reads)

    manager.session.apply(MysteryEdit())
    manager.reload_from_document()

    reads = set(reads_since(page_reads, mark))
    assert reads == set(range(PAGES))
    assert manager.get_annotations(page=4), "the note the edit wrote is not listed"


def test_a_list_that_fell_behind_by_two_edits_rereads_everything(manager, page_reads):
    """``changed_pages`` only describes the last one, so two steps is a full read."""
    manager.add_note(2, (30.0, 30.0), "one")
    mark = len(page_reads)

    manager.session.apply(MysteryEdit())
    manager.session.apply(SetMetadata((("title", "two"),)))
    manager.reload_from_document()

    assert set(reads_since(page_reads, mark)) == set(range(PAGES))


def test_a_revert_rereads_the_document(manager, page_reads):
    manager.add_note(2, (30.0, 30.0), "before")
    mark = len(page_reads)

    manager.session.revert()
    manager.reload_from_document()

    assert set(reads_since(page_reads, mark)) == set(range(PAGES))
    assert manager.get_annotations() == []


# ------------------------------------------------------------------- and it is right
def test_undoing_an_edit_rereads_the_pages_it_had_touched(manager, page_reads):
    note = manager.add_note(2, (30.0, 30.0), "stays")
    highlight = manager.add_highlight(5, [(10.0, 10.0, 60.0, 20.0)])
    mark = len(page_reads)

    manager.session.undo()
    manager.reload_from_document()

    reads = reads_since(page_reads, mark)
    assert 5 in reads and 2 not in reads
    assert manager.get_annotation(highlight.id) is None
    assert manager.get_annotation(note.id) is note


def test_removing_the_last_annotation_on_a_page_empties_it(manager):
    note = manager.add_note(2, (30.0, 30.0), "only one")
    manager.add_highlight(5, [(10.0, 10.0, 60.0, 20.0)])

    assert manager.remove_annotation(note.id) is True

    assert manager.get_annotations(page=2) == []
    assert manager.get_notes_for_page(2) == []
    assert len(manager.get_highlights_for_page(5)) == 1


def test_a_page_read_after_the_document_changed_describes_that_document(manager):
    """An undo puts a different document in place; the re-read page follows it."""
    note = manager.add_note(2, (30.0, 30.0), "kept")
    manager.add_note(4, (30.0, 30.0), "dropped")

    manager.session.undo()
    manager.reload_from_document()

    assert [n.id for n in manager.get_annotations()] == [note.id]


def test_the_list_stays_in_page_order_across_partial_reads(manager):
    manager.add_note(6, (30.0, 30.0), "later")
    manager.add_note(1, (30.0, 30.0), "earlier")
    manager.add_note(3, (30.0, 30.0), "middle")

    assert [a.page for a in manager.get_annotations()] == [1, 3, 6]


def test_an_edit_to_a_page_updates_its_own_row_without_moving_another(manager):
    first = manager.add_note(2, (30.0, 30.0), "first text")
    second = manager.add_note(4, (30.0, 30.0), "second text")

    manager.set_note(first.id, "rewritten")

    assert manager.get_note(first.id) == "rewritten"
    assert manager.get_annotation(first.id).page == 2
    assert manager.get_annotation(second.id) is second


def test_a_note_costs_one_page_of_listening(manager, page_reads):
    """The item in one line: a page read per note, not a book read per note."""
    manager.add_note(3, (30.0, 30.0), "one")
    mark = len(page_reads)

    manager.add_note(6, (30.0, 30.0), "two")

    assert set(reads_since(page_reads, mark)) == {6}
