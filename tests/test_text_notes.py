"""F10-3d: a standalone note is a real ``/Text`` annotation.

Highlights became edits in F10-3a and a note *on* a highlight was already the
highlight's own ``/Contents``.  A sticky note of its own — the thing a reader
program shows as a small icon you click to open a popup — was the other half:
``NoteAnnotation`` existed as a dataclass that nothing constructed, and
``get_notes_for_page()`` always answered ``[]`` because the manager skipped
everything that was not a highlight.

Here the document becomes the store for notes too: ``AddNote`` writes a
``/Text`` annotation named by ``/NM`` like any other, the manager lists both
kinds, and the note-editing methods stop being highlight-specific — the same
``/Contents`` key, the same identity, the same undo.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    AddNote,
    AnnotationManager,
    AnnotationType,
    NoteAnnotation,
    SetAnnotationNote,
)
from pdfar.edits import EditError, Scope
from pdfar.save import save_document

#: The sample page is 200x100, so this leaves the whole icon inside it.
AT = (30.0, 40.0)
TEXT = "check the figures"


def ink_pixels(pix) -> int:
    """How many pixels of a rendered patch are not plain white."""
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if pix.pixel(x, y)[:3] != (255, 255, 255))


def notes_on(doc, page=0):
    """Every ``/Text`` annotation on *page*, as the file holds it."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_TEXT:
            continue
        rect = annot.rect
        out.append({"id": annot.info.get("id"), "note": annot.info.get("content"),
                    "author": annot.info.get("title"),
                    "position": (round(rect.x0, 1), round(rect.y0, 1)),
                    "icon": annot.info.get("name")})
    return out


@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


# ------------------------------------------------------- the document is the store
def test_a_note_is_in_the_document_the_moment_it_is_made(manager):
    """No pending list waiting for Save: the annotation is the note."""
    session = manager.session

    made = manager.add_note(0, AT, TEXT, author="Wachin")

    assert isinstance(made, NoteAnnotation)
    assert made.type is AnnotationType.NOTE
    assert made.content == TEXT
    assert made.author == "Wachin"
    assert session.dirty is True
    assert session.version == 1
    held = notes_on(manager.doc)
    assert held == [{"id": made.id, "note": TEXT, "author": "Wachin",
                     "position": AT, "icon": "Note"}]


def test_a_note_with_no_text_yet_still_exists(manager):
    """The icon is what the user clicks to type, so an empty note is a note.

    MuPDF writes no ``/Contents`` for it, which the model reports as ``""``; the
    distinction that matters is between *empty* and *absent*, and the annotation
    is present either way.
    """
    made = manager.add_note(0, AT)

    assert made.content == ""
    assert len(notes_on(manager.doc)) == 1
    assert manager.get_notes_for_page(0)[0].id == made.id


def test_an_empty_note_still_paints_its_icon(manager):
    """An annotation with no appearance stream is an invisible one."""
    manager.add_note(0, AT)

    page = manager.doc.load_page(0)
    clip = fitz.Rect(28, 38, 48, 58)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    painted = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    assert ink_pixels(painted) > ink_pixels(bare), "the icon is not being drawn"


def test_a_note_near_the_page_edge_stays_on_the_page(manager):
    """An icon hanging off the page cannot be clicked, so it is pulled in."""
    made = manager.add_note(0, (195.0, 95.0))      # page is 200x100

    assert made.position == (184.0, 84.0)
    assert notes_on(manager.doc)[0]["position"] == (184.0, 84.0)


def test_a_note_is_listed_as_a_note_and_not_as_a_highlight(manager):
    """The two kinds share a list and stay distinguishable in it."""
    note = manager.add_note(0, AT, TEXT)
    highlight = manager.add_highlight(0, [(10.0, 51.0, 120.0, 63.0)])

    assert manager.get_highlight(note.id) is None
    assert manager.get_highlights_for_page(0) == [highlight]
    assert manager.get_notes_for_page(0) == [note]
    assert note.type is AnnotationType.NOTE
    assert highlight.type is AnnotationType.HIGHLIGHT
    assert note.rects == [(AT[0], AT[1], AT[0] + 16.0, AT[1] + 16.0)]


def test_other_annotation_kinds_are_left_out_of_the_list(manager):
    """Only what PDFAR can edit is claimed; a shape is not a note, and a free
    text box is not listed at all.

    Since F10-3l a square *is* editable and since F10-3n so is a stamp, so both
    appear.  A free-text annotation is still only read, and listing it would
    offer the user an action on it that rewrites a document they did not ask to
    change.
    """
    page = manager.doc.load_page(0)
    square = page.add_rect_annot(fitz.Rect(10, 70, 40, 95))
    square.set_colors(stroke=(1.0, 0.0, 0.0))
    square.update()
    stamp = page.add_stamp_annot(fitz.Rect(10, 20, 40, 50))
    stamp.update()
    text = page.add_freetext_annot(fitz.Rect(60, 10, 150, 40), "from elsewhere")
    text.update()

    listed = AnnotationManager(manager.session)      # read from the top

    assert {ann.type for ann in listed.get_annotations()} == {
        AnnotationType.SHAPE, AnnotationType.STAMP}
    assert listed.get_notes_for_page(0) == []


def test_a_foreign_note_is_listed_without_the_file_being_rewritten(manager):
    """A note from another program has no ``/NM`` of ours; reading is not editing."""
    page = manager.doc.load_page(0)
    foreign = page.add_text_annot(fitz.Point(60, 20), "from elsewhere")
    foreign.update()
    # PyMuPDF names what it creates; a note from another producer may have no
    # name at all, which is the case the model has to cope with.
    manager.doc.xref_set_key(foreign.xref, "NM", "null")

    listed = AnnotationManager(manager.session)      # read from the top
    notes = listed.get_notes_for_page(0)

    assert [n.content for n in notes] == ["from elsewhere"]
    assert notes[0].id == f"anon:{foreign.xref}"
    assert listed.session.dirty is False
    assert manager.doc.xref_get_key(foreign.xref, "NM") == ("null", "null")


# -------------------------------------------------------------------- editing one
def test_editing_a_note_keeps_its_identity(manager):
    """The id a sidebar row holds is the id the annotation keeps."""
    made = manager.add_note(0, AT, TEXT)

    updated = manager.set_note(made.id, "corrected")
    again = manager.set_note(made.id, "final")

    assert updated.content == "corrected"
    assert again.id == made.id
    assert again.content == "final"
    assert notes_on(manager.doc)[0]["note"] == "final"


def test_removing_a_note_empties_it_and_keeps_the_icon(manager):
    """"Remove note" is a request about the text, not about the annotation."""
    made = manager.add_note(0, AT, TEXT)

    assert manager.remove_note(made.id) is True

    assert manager.get_note(made.id) == ""
    assert notes_on(manager.doc)[0]["id"] == made.id
    assert notes_on(manager.doc)[0]["note"] in (None, "")


def test_deleting_a_note_takes_the_annotation_with_it(manager):
    made = manager.add_note(0, AT, TEXT)

    assert manager.remove_annotation(made.id) is True

    assert notes_on(manager.doc) == []
    assert manager.get_annotation(made.id) is None


def test_the_note_edits_name_their_undo_steps(manager):
    """Ctrl+Z says what it will undo, and a note step is no exception."""
    history = manager.session.history
    made = manager.add_note(0, AT, TEXT)
    assert history.undo_label == "Add note"

    manager.set_note(made.id, "more")
    assert history.undo_label == "Edit note"

    manager.remove_note(made.id)
    assert history.undo_label == "Remove note"

    manager.remove_annotation(made.id)
    assert history.undo_label == "Delete annotation"


def test_every_note_edit_undoes_and_redoes(manager):
    """Undo is a revision walk, so each kind of edit has to survive one."""
    session = manager.session
    made = manager.add_note(0, AT, TEXT)
    manager.set_note(made.id, "more")
    manager.remove_note(made.id)

    for _ in range(3):
        session.undo()
        manager.reload_from_document()
    assert notes_on(session.doc) == []
    assert manager.get_annotations() == []

    for _ in range(3):
        session.redo()
        manager.reload_from_document()
    assert [n["note"] for n in notes_on(session.doc)] == [""]
    assert [n.id for n in manager.get_notes_for_page(0)] == [made.id]

    session.undo()
    manager.reload_from_document()
    assert [n["note"] for n in notes_on(session.doc)] == ["more"]


def test_a_note_edit_that_targets_a_gone_annotation_refuses(manager):
    """Writing to whatever shares an id with a deleted note would damage the file."""
    session = manager.session
    made = manager.add_note(0, AT, TEXT)
    highlight = manager.add_highlight(0, [(10.0, 51.0, 120.0, 63.0)])
    manager.remove_annotation(made.id)              # the note is out of the file
    version = session.version

    with pytest.raises(EditError):
        session.apply(SetAnnotationNote(ann_id=made.id, page=0, note="orphan"))

    assert session.version == version
    assert notes_on(session.doc) == []
    assert manager.get_highlight(highlight.id) is not None


def test_a_note_edit_on_a_page_that_is_not_there_refuses(manager):
    """An out-of-range page is a refusal, not a half-applied edit."""
    with pytest.raises(EditError):
        manager.session.apply(AddNote(ann_id="x", page=99, position=AT))

    assert manager.session.version == 0
    assert len(manager.session.history) == 0


def test_a_note_reaches_a_background_reader(manager):
    """The version stamp is how the render pool sees the note without a save."""
    made = manager.add_note(0, AT, TEXT)
    version = manager.session.read_version()

    with manager.session.open_copy(version) as copy:
        held = notes_on(copy)

    assert [n["id"] for n in held] == [made.id]
    assert held[0]["note"] == TEXT


# --------------------------------------------------------------------- on the disk
def test_a_note_survives_a_save_and_a_reopen(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    manager = AnnotationManager(session)
    made = manager.add_note(0, AT, TEXT, author="Wachin")
    manager.set_note(made.id, "still here")
    report = save_document(session)
    session.close()

    reopened = fitz.open(path)
    held = notes_on(reopened)
    manager2 = AnnotationManager(make_session(path))

    assert report is not None
    assert held == [{"id": made.id, "note": "still here", "author": "Wachin",
                     "position": AT, "icon": "Note"}]
    assert [n.content for n in manager2.get_notes_for_page(0)] == ["still here"]
    manager2.session.close()
    reopened.close()


def test_a_note_and_a_highlight_survive_together(tmp_path):
    """Two kinds in one document: neither edit disturbs the other."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    manager = AnnotationManager(session)
    note = manager.add_note(0, AT, TEXT)
    highlight = manager.add_highlight(0, [(10.0, 51.0, 120.0, 63.0)],
                                      note="on the band")
    save_document(session)
    session.close()

    reopened = fitz.open(path)
    kinds = [a.type[1] for a in reopened.load_page(0).annots()]
    manager2 = AnnotationManager(make_session(path))

    assert sorted(kinds) == ["Highlight", "Text"]
    assert [n.id for n in manager2.get_notes_for_page(0)] == [note.id]
    assert manager2.get_highlight(highlight.id).content == "on the band"
    manager2.session.close()
    reopened.close()


def test_a_note_edit_is_a_comments_change(manager):
    """The scope decides what the view throws away; a note paints on the page."""
    assert AddNote(ann_id="x", page=0, position=AT).scope is Scope.COMMENTS
    assert SetAnnotationNote(ann_id="x", page=0, note="n").scope is Scope.COMMENTS
