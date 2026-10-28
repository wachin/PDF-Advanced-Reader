"""F10-3x: marks that arrive sharing a name are still separate marks.

An annotation's ``/NM`` is the identity everything else is built on: the sidebar
row, the delete, the note, the XFDF exchange.  PyMuPDF invents one for every
annotation it adds without a name — ``fitz-A0``, ``fitz-A1`` … with the counter
restarting per page — so a document from another PyMuPDF-based program carries
``fitz-A0`` on every page, and a list keyed by the name shows **one** mark where
the file holds 540 (found by F10-3t).

The rule these tests hold: a name is an identity only while it names one mark.
The first page in reading order claims it, and the copies after it are listed by
object number, which is unique within a document.  Three properties follow, and
each is worth testing on its own because each is a different way for the fix to
be incomplete: nothing is dropped, every row still reaches the mark it shows,
and nothing that was already unique is disturbed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.annotations import AnnotationManager
from pdfar.xfdf import export_xfdf


def note_at(page, index, text):
    """One sticky note, added the way PyMuPDF adds it: with no name given."""
    annot = page.add_text_annot((40.0 + index * 6, 40.0), text)
    annot.update()
    return annot


def reread(made):
    """Throw the cached list away and read every page of the document again.

    Two generations back, because that is what asks for the whole document rather
    than the pages the last step wrote: the point is to read the file again, not to
    reuse what the first read left in the cache.
    """
    made._generation = -2
    made.reload_from_document()
    return made.get_annotations()


def foreign_pdf(path, pages=3):
    """A document as another PyMuPDF-based program writes it: one note per page,
    every one of them named ``fitz-A0`` because the counter restarts per page."""
    source = build_sample_pdf(path, pages=pages)
    with fitz.open(source) as doc:
        for index in range(doc.page_count):
            note_at(doc.load_page(index), index, f"note on page {index}")
        doc.saveIncr()
    return source


@pytest.fixture
def foreign(tmp_path):
    """A manager over a document whose marks all carry the same name."""
    session = make_session(foreign_pdf(tmp_path / "foreign.pdf"))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        session.close()


# ------------------------------------------------------------------ nothing lost
def test_marks_that_share_a_name_are_all_listed(foreign):
    """Three pages, three marks, three rows — whatever the names say."""
    marks = foreign.get_annotations()
    assert len(marks) == 3
    assert sorted(mark.page for mark in marks) == [0, 1, 2]
    assert [mark.content for mark in marks] == [
        "note on page 0", "note on page 1", "note on page 2"]


def test_every_mark_is_listed_under_an_id_that_names_only_it(foreign):
    """The list is keyed, so an id repeated anywhere in it is a mark missing."""
    ids = [mark.id for mark in foreign.get_annotations()]
    assert len(ids) == len(set(ids))


def test_the_first_page_in_reading_order_keeps_the_name(foreign):
    """A claim is decided by reading order, so the ids do not move between reads."""
    marks = foreign.get_annotations()
    assert len(marks) == 3
    assert marks[0].id == "fitz-A0"
    assert all(mark.id.startswith("anon:") for mark in marks[1:])


def test_reading_the_document_twice_gives_the_same_ids(foreign):
    """Rows the user is looking at cannot be renumbered by a reload."""
    first = [mark.id for mark in foreign.get_annotations()]
    assert [mark.id for mark in reread(foreign)] == first


def test_two_marks_named_alike_on_one_page_are_both_listed(foreign):
    """The collapse is not only between pages: a file may repeat a name anywhere.

    PyMuPDF's own counter does not do this, but another reader's might, and the
    page list is keyed the same way the document list is.
    """
    copy = note_at(foreign.doc.load_page(0), 9, "the one that copies its neighbour")
    foreign.doc.xref_set_key(copy.xref, "NM", "(fitz-A0)")
    marks = reread(foreign)
    assert len(marks) == 4
    assert [mark.id for mark in marks[:2]] == [
        "fitz-A0", f"anon:{copy.xref}"]
    assert {mark.content for mark in marks} == {
        "note on page 0", "the one that copies its neighbour",
        "note on page 1", "note on page 2"}


# ----------------------------------------------------------------- still reachable
def test_a_mark_listed_by_object_number_can_still_be_rewritten(foreign):
    """The row that lost the name is not a row that lost its actions."""
    borrowed = foreign.get_annotations()[1]
    changed = foreign.set_note(borrowed.id, "edited through a borrowed id")
    assert changed is not None
    assert changed.content == "edited through a borrowed id"
    assert foreign.get_annotation(borrowed.id).page == 1
    # The other two are the marks the file says they are, not this one's twins.
    assert foreign.get_annotations()[0].content == "note on page 0"
    assert foreign.get_annotations()[2].content == "note on page 2"


def test_a_mark_listed_by_object_number_can_still_be_removed(foreign):
    """Deleting through the object number takes that mark and only that mark."""
    marks = foreign.get_annotations()
    assert foreign.remove_annotation(marks[2].id) is True
    left = foreign.get_annotations()
    assert [mark.content for mark in left] == [
        "note on page 0", "note on page 1"]
    assert foreign.session.undo()
    foreign.reload_from_document()
    assert len(foreign.get_annotations()) == 3


def test_an_edit_through_a_shared_name_reaches_the_mark_that_claimed_it(foreign):
    """``fitz-A0`` belongs to page 0, and a lookup by that name may not wander.

    The list gives every other copy a different id, but the name is still in the
    file on their pages, so the search that resolves an id has to stop at the page
    it was given — and on that page the name is the one mark it ever named.
    """
    holder = foreign.get_annotation("fitz-A0")
    assert holder is not None and holder.page == 0
    assert foreign.remove_annotation("fitz-A0") is True
    assert [mark.content for mark in foreign.get_annotations()] == [
        "note on page 1", "note on page 2"]


# ---------------------------------------------------------------- not disturbed
def test_marks_that_name_themselves_apart_keep_their_names(tmp_path):
    """PDFAR's own marks are named by UUIDs, and none of this touches them."""
    session = make_session(build_sample_pdf(tmp_path / "own.pdf", pages=2))
    made = AnnotationManager(session)
    try:
        first = made.add_note(0, (40.0, 40.0), note="mine")
        second = made.add_note(1, (40.0, 40.0), note="also mine")
        assert [mark.id for mark in made.get_annotations()] == [
            first.id, second.id]
        assert not any(mark.id.startswith("anon:")
                       for mark in made.get_annotations())
    finally:
        session.close()


def test_a_file_written_from_shared_names_names_its_marks_apart(foreign):
    """An export of a collapsed document must not export one mark.

    The names it writes are the ids the list settled on, so the other reader gets
    three marks and one of them keeps the name the file already held.
    """
    text = export_xfdf(foreign)
    assert text.count("<text ") == 3
    names = [chunk.split('name="', 1)[1].split('"', 1)[0]
             for chunk in text.split("<text ") if 'name="' in chunk]
    assert len(names) == len(set(names))
