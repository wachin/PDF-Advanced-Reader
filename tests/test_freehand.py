"""F10-3i: a freehand stroke is a real ``/Ink`` annotation.

Highlights (F10-3a) and sticky notes (F10-3d) made the document the store for
two kinds; this is the third, and the first one whose geometry is the user's
own drawing rather than a text selection or a click.  A pen path arrives as a
list of strokes of points in page space, goes into ``/InkList``, and comes back
out of the file with the same points — which is what makes it undoable through
F10-3c's revision files and visible to other readers without PDFAR painting it
twice.

The interface that draws a stroke is F10-3j; everything here is testable with
no mouse involved.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    AddInk,
    AnnotationManager,
    AnnotationType,
    InkAnnotation,
)
from pdfar.edits import EditError, Scope
from pdfar.save import save_document

#: The sample page is 200x100.  Two strokes, so one annotation has to carry both.
STROKES = (
    ((10.0, 20.0), (30.0, 40.5), (60.0, 25.0), (90.0, 60.0)),
    ((120.0, 15.0), (150.0, 80.0)),
)
RED = "#FF0000"


def ink_pixels(pix) -> int:
    """How many pixels of a rendered patch are not plain white."""
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if pix.pixel(x, y)[:3] != (255, 255, 255))


def inks_on(doc, page=0):
    """Every ``/Ink`` annotation on *page*, as the file holds it."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_INK:
            continue
        rect = annot.rect
        out.append({
            "id": annot.info.get("id"),
            "paths": [[(round(x, 3), round(y, 3)) for x, y in line]
                      for line in annot.vertices],
            "color": [round(c, 3) for c in (annot.colors.get("stroke") or [])],
            "width": annot.border.get("width"),
            "rect": (round(rect.x0, 1), round(rect.y0, 1),
                     round(rect.x1, 1), round(rect.y1, 1)),
        })
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
def test_a_stroke_is_in_the_document_the_moment_it_is_drawn(manager):
    """Nothing waits for Save: the annotation is the drawing."""
    session = manager.session

    made = manager.add_ink(0, STROKES, color=RED, width=3.0, author="Wachin")

    assert isinstance(made, InkAnnotation)
    assert made.type is AnnotationType.FREEHAND
    assert made.paths == [list(line) for line in STROKES]
    assert made.color == RED
    assert made.width == 3.0
    assert made.author == "Wachin"
    assert session.dirty is True
    assert session.version == 1
    held = inks_on(manager.doc)
    assert len(held) == 1
    assert held[0]["id"] == made.id
    assert held[0]["width"] == 3.0
    assert held[0]["color"] == [1.0, 0.0, 0.0]


def test_every_stroke_of_one_drawing_is_one_annotation(manager):
    """The user drew once, so ``/InkList`` holds both lines in one object."""
    manager.add_ink(0, STROKES)

    assert len(inks_on(manager.doc)) == 1
    assert len(inks_on(manager.doc)[0]["paths"]) == 2


def test_a_drawing_reaches_the_pixels(manager):
    """MuPDF writes the appearance stream, so the page paints it unasked."""
    manager.add_ink(0, STROKES, color=RED, width=3.0)

    page = manager.doc.load_page(0)
    clip = fitz.Rect(0, 0, 200, 100)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    painted = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    assert ink_pixels(painted) > ink_pixels(bare), "the stroke is not being drawn"


def test_the_bounding_box_is_what_was_drawn_on(manager):
    """The view scrolls to and rings ``rects[0]``, so it has to cover the ink.

    MuPDF pads the box by half the stroke width, which is why this asserts a
    container rather than an exact rectangle: the padding is the library's, and
    pinning it to a number would only record which version wrote the file.
    """
    made = manager.add_ink(0, STROKES, width=3.0)

    x0, y0, x1, y1 = made.rects[0]
    assert x0 <= 10.0 and y0 <= 15.0, "the box clips the drawing"
    assert x1 >= 150.0 and y1 >= 80.0, "the box clips the drawing"


# ---------------------------------------------------------------------- a refusal
def test_drawing_nothing_is_refused(manager):
    """An empty ``/InkList`` is an annotation with an infinite box.

    MuPDF accepts it and gives it the whole coordinate range as its rectangle,
    which would invalidate every tile and show a row the user cannot click on.
    Refusing is cheaper than explaining it.
    """
    session = manager.session

    for paths in ([], [[]], [()]):
        assert manager.add_ink(0, paths) is None

    assert inks_on(manager.doc) == []
    assert len(manager.session.history) == 0
    assert session.dirty is False
    assert session.version == 0


def test_an_edit_that_draws_nothing_refuses_before_touching_the_document(manager):
    """The refusal is the edit's, not the manager's — so any caller is covered."""
    doc = manager.session.doc

    with pytest.raises(EditError):
        AddInk(ann_id="x", page=0, paths=()).apply(doc)


# --------------------------------------------------------------------- undo / redo
def test_one_undo_removes_the_whole_drawing(manager):
    made = manager.add_ink(0, STROKES)

    assert manager.session.undo()[1] is Scope.COMMENTS
    manager.reload_from_document()

    assert inks_on(manager.doc) == []
    assert manager.get_annotation(made.id) is None


def test_redo_brings_back_the_same_annotation(manager):
    made = manager.add_ink(0, STROKES)
    manager.session.undo()
    manager.reload_from_document()

    _version, scope = manager.session.redo()
    manager.reload_from_document()

    assert scope is Scope.COMMENTS
    assert [h["id"] for h in inks_on(manager.doc)] == [made.id]
    restored = manager.get_annotation(made.id)
    assert isinstance(restored, InkAnnotation)
    assert restored.paths == [list(line) for line in STROKES]


def test_the_undo_step_is_named_for_the_drawing(manager):
    manager.add_ink(0, STROKES)

    assert manager.session.history.undo_label == "Freehand"


# ----------------------------------------------------------------- saved and reopened
def test_a_drawing_survives_a_save_and_a_reopen(manager):
    """The points are the drawing, so a reopened file has to agree exactly."""
    made = manager.add_ink(1, STROKES, color=RED, width=2.5)
    path = save_document(manager.session).path
    manager.session.close()

    reopened = AnnotationManager(make_session(path))
    try:
        held = inks_on(reopened.doc, page=1)
        assert [h["id"] for h in held] == [made.id]
        stored = held[0]["paths"]
        for line, expected in zip(stored, STROKES):
            for got, want in zip(line, expected):
                assert got == pytest.approx(want, abs=0.01)
        model = reopened.get_annotation(made.id)
        assert isinstance(model, InkAnnotation)
        assert model.color == RED
        assert model.width == pytest.approx(2.5)
    finally:
        reopened.session.close()


# ------------------------------------------------------------------ the listing side
def test_a_drawing_rereads_only_the_page_it_was_drawn_on(tmp_path, monkeypatch):
    """F10-3h's rule, applied to a new kind: one page, not the book."""
    reads = []
    real_load = fitz.Document.load_page

    def counting_load(self, index, *args, **kwargs):
        reads.append(index)
        return real_load(self, index, *args, **kwargs)

    monkeypatch.setattr(fitz.Document, "load_page", counting_load)
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=8))
    manager = AnnotationManager(session)
    reads.clear()

    manager.add_ink(3, STROKES)

    assert set(reads) == {3}
    manager.session.close()


def test_a_drawing_is_listed_with_the_others(manager):
    """The comments panel shows every kind, so the row has to be there."""
    ink = manager.add_ink(0, STROKES)
    note = manager.add_note(0, (30.0, 40.0), "by hand")
    highlight = manager.add_highlight(1, [(10.0, 47.1, 113.3, 63.6)])

    listed = manager.get_annotations(page=0)
    assert {a.id for a in listed} == {ink.id, note.id}
    assert {a.type for a in listed} == {AnnotationType.FREEHAND, AnnotationType.NOTE}
    assert manager.get_annotations(page=1,
                                  ann_type=AnnotationType.HIGHLIGHT)[0].id == highlight.id


def test_a_drawing_can_be_deleted_and_annotated_like_any_other(manager):
    """Delete and ``/Contents`` were never highlight-specific."""
    made = manager.add_ink(0, STROKES)

    assert manager.set_note(made.id, "signed here") is not None
    assert manager.get_note(made.id) == "signed here"
    assert inks_on(manager.doc)[0]["id"] == made.id
    assert manager.doc.load_page(0).annots().__next__().info["content"] == "signed here"

    assert manager.remove_annotation(made.id) is True
    assert inks_on(manager.doc) == []


# --------------------------------------------------------------------- other programs
def test_a_stroke_another_program_drew_is_listed_without_being_renamed(tmp_path):
    """Reading a file must not have to write into it, so an unnamed ink is keyed
    by its object number and stays that way."""
    seed = fitz.open(build_sample_pdf(tmp_path / "seed.pdf", pages=2))
    page = seed.load_page(1)
    foreign = page.add_ink_annot([[(20.0, 20.0), (40.0, 40.0)]])
    foreign.set_colors(stroke=(0.0, 0.0, 1.0))
    foreign.update()
    seed.xref_set_key(foreign.xref, "NM", "null")
    path = tmp_path / "foreign.pdf"
    seed.save(path, garbage=2, deflate=True)
    seed.close()

    manager = AnnotationManager(make_session(str(path)))
    try:
        listed = manager.get_annotations(page=1, ann_type=AnnotationType.FREEHAND)
        assert len(listed) == 1
        assert listed[0].id.startswith("anon:")
        assert listed[0].color == "#0000FF"
        assert listed[0].paths == [[(20.0, 20.0), (40.0, 40.0)]]
        xref = int(listed[0].id.split(":")[1])
        assert manager.doc.xref_get_key(xref, "NM") == ("null", "null")
    finally:
        manager.session.close()
