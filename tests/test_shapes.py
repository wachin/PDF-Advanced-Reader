"""F10-3l: a drawn shape is a real ``/Square``, ``/Circle``, ``/Line`` or
``/Polygon``.

The pen (F10-3i) proved a user-drawn geometry can live in the file and survive
undo, save and another reader.  A shape is the same idea with PDF's own
primitives: a rectangle is not a rectangular ink path, it is the subtype every
viewer knows how to resize and select.  An arrow is a line whose ``/LE`` carries
an arrowhead, because PDF has no arrow subtype.

Nothing here touches the mouse; the tool that draws these is F10-3m.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    AddShape,
    AnnotationManager,
    AnnotationType,
    ShapeAnnotation,
)
from pdfar.edits import EditError, Scope
from pdfar.save import save_document

#: Geometry that fits the 200x100 sample page, one entry per kind.  Box shapes
#: carry ``rect``; the others carry page-space vertices.
SHAPES = {
    "rect": {"rect": (20.0, 20.0, 70.0, 60.0)},
    "ellipse": {"rect": (20.0, 20.0, 70.0, 60.0)},
    "line": {"points": ((20.0, 20.0), (80.0, 70.0))},
    "arrow": {"points": ((20.0, 20.0), (80.0, 70.0))},
    "polygon": {"points": ((20.0, 20.0), (80.0, 30.0), (50.0, 70.0))},
}

#: What the file has to say for each kind.  An arrow shares ``/Line`` with a
#: plain line; ``line_ends`` is what tells them apart.
SUBTYPES = {
    "rect": fitz.PDF_ANNOT_SQUARE,
    "ellipse": fitz.PDF_ANNOT_CIRCLE,
    "line": fitz.PDF_ANNOT_LINE,
    "arrow": fitz.PDF_ANNOT_LINE,
    "polygon": fitz.PDF_ANNOT_POLYGON,
}

LABELS = {
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "line": "Line",
    "arrow": "Arrow",
    "polygon": "Polygon",
}

SHAPE_SUBTYPES = (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE,
                  fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLYGON)
RED = "#FF0000"


def draw(manager, kind, page=0, **options):
    """Draw one shape of *kind* with its standard geometry."""
    geometry = dict(SHAPES[kind])
    geometry.update(options)
    return manager.add_shape(page, kind, **geometry)


def shape_pixels(pix) -> int:
    """How many pixels of a rendered patch are not plain white."""
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if pix.pixel(x, y)[:3] != (255, 255, 255))


def shapes_on(doc, page=0):
    """Every shape annotation on *page*, as the file holds it."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] not in SHAPE_SUBTYPES:
            continue
        rect = annot.rect
        out.append({
            "id": annot.info.get("id"),
            "subtype": annot.type[0],
            "name": annot.type[1],
            "points": ([(round(x, 3), round(y, 3)) for x, y in annot.vertices]
                       if annot.vertices else []),
            "line_ends": tuple(annot.line_ends) if annot.line_ends else (0, 0),
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
@pytest.mark.parametrize("kind", SHAPES)
def test_a_shape_is_in_the_document_the_moment_it_is_drawn(manager, kind):
    """Nothing waits for Save: the annotation is the shape."""
    session = manager.session

    made = draw(manager, kind, color=RED, width=2.0, author="Wachin")

    assert isinstance(made, ShapeAnnotation)
    assert made.type is AnnotationType.SHAPE
    assert made.kind == kind
    assert made.color == RED
    assert made.width == 2.0
    assert made.author == "Wachin"
    assert session.dirty is True
    assert session.version == 1
    held = shapes_on(manager.doc)
    assert len(held) == 1
    assert held[0]["id"] == made.id
    assert held[0]["subtype"] == SUBTYPES[kind]
    assert held[0]["width"] == 2.0
    assert held[0]["color"] == [1.0, 0.0, 0.0]


@pytest.mark.parametrize("kind", SHAPES)
def test_the_file_names_the_right_subtype(manager, kind):
    """A rectangle is /Square, not an ink path that happens to look square —
    that is what lets another reader select and resize it."""
    draw(manager, kind)

    held = shapes_on(manager.doc)
    assert held[0]["subtype"] == SUBTYPES[kind]


def test_an_arrow_is_a_line_that_says_it_has_an_arrowhead(manager):
    """PDF has no arrow subtype; ``/LE`` is how a line becomes one."""
    arrow = draw(manager, "arrow")
    line = draw(manager, "line", points=((100.0, 20.0), (150.0, 70.0)))

    held = {h["id"]: h for h in shapes_on(manager.doc)}
    ends = (fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
    assert held[arrow.id]["line_ends"] == ends
    assert held[line.id]["line_ends"] == (fitz.PDF_ANNOT_LE_NONE,
                                          fitz.PDF_ANNOT_LE_NONE)
    assert arrow.kind == "arrow"
    assert line.kind == "line"


def test_a_shape_reaches_the_pixels(manager):
    """MuPDF writes the appearance stream, so the page paints it unasked."""
    for kind in SHAPES:
        draw(manager, kind, color=RED, width=2.0)

    page = manager.doc.load_page(0)
    clip = fitz.Rect(0, 0, 200, 100)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    painted = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    assert shape_pixels(painted) > shape_pixels(bare), "the shapes are not drawn"


def test_the_box_covers_what_was_drawn(manager):
    """The view scrolls to and rings ``rects[0]``, so it has to cover the shape.

    MuPDF pads the box by half the stroke width; this asserts a container
    rather than an exact rectangle so it does not pin the library's padding.
    """
    rect = draw(manager, "rect", rect=(20.0, 20.0, 70.0, 60.0), width=2.0)
    arrow = draw(manager, "arrow", points=((10.0, 80.0), (150.0, 30.0)))

    x0, y0, x1, y1 = rect.rects[0]
    assert x0 <= 20.0 and y0 <= 20.0 and x1 >= 70.0 and y1 >= 60.0
    x0, y0, x1, y1 = arrow.rects[0]
    assert x0 <= 10.0 and y0 <= 30.0 and x1 >= 150.0 and y1 >= 80.0


def test_a_drawing_can_arrive_in_any_direction(manager):
    """The user may drag up and left; the file must still hold page order."""
    made = draw(manager, "rect", rect=(70.0, 60.0, 20.0, 20.0))

    x0, y0, x1, y1 = made.rects[0]
    assert x0 < x1 and y0 < y1
    assert (x0, y0, x1, y1) == pytest.approx((20.0, 20.0, 70.0, 60.0), abs=1.0)
    assert shapes_on(manager.doc)[0]["rect"][0] < shapes_on(manager.doc)[0]["rect"][2]


def test_the_points_come_back_in_page_space(manager):
    """A line and a polygon keep the vertices the user drew, not a bounding box."""
    line = draw(manager, "line")
    polygon = draw(manager, "polygon")

    assert line.points == [(20.0, 20.0), (80.0, 70.0)]
    assert polygon.points == [(20.0, 20.0), (80.0, 30.0), (50.0, 70.0)]
    assert draw(manager, "rect").points == []


# ---------------------------------------------------------------------- a refusal
@pytest.mark.parametrize("kind,geometry", [
    ("rect", {"rect": (20.0, 20.0, 20.0, 60.0)}),
    ("rect", {"rect": (20.0, 20.0, 70.0, 20.0)}),
    ("ellipse", {"rect": (20.0, 20.0, 20.004, 60.0)}),
    ("line", {"points": ((20.0, 20.0),)}),
    ("line", {"points": ((20.0, 20.0), (20.0, 20.0))}),
    ("arrow", {"points": ((20.0, 20.0), (20.0, 20.0))}),
    ("polygon", {"points": ((20.0, 20.0), (80.0, 30.0))}),
    ("star", {"rect": (20.0, 20.0, 70.0, 60.0)}),
])
def test_a_shape_with_no_area_is_refused(manager, kind, geometry):
    """A zero-extent shape is an annotation nobody can select or jump to.

    Refusing before MuPDF is handed it keeps the page, the history and the
    session's dirty flag untouched.
    """
    session = manager.session

    assert manager.add_shape(0, kind, **geometry) is None

    assert shapes_on(manager.doc) == []
    assert len(session.history) == 0
    assert session.dirty is False
    assert session.version == 0


def test_an_edit_that_draws_nothing_refuses_before_touching_the_document(manager):
    """The refusal is the edit's, not the manager's — so any caller is covered."""
    doc = manager.session.doc

    with pytest.raises(EditError):
        AddShape(ann_id="x", page=0, kind="line",
                 points=((10.0, 10.0), (10.0, 10.0))).apply(doc)
    with pytest.raises(EditError):
        AddShape(ann_id="x", page=0, kind="star").apply(doc)


# --------------------------------------------------------------------- undo / redo
@pytest.mark.parametrize("kind", SHAPES)
def test_one_undo_removes_the_whole_shape(manager, kind):
    made = draw(manager, kind)

    assert manager.session.undo()[1] is Scope.COMMENTS
    manager.reload_from_document()

    assert shapes_on(manager.doc) == []
    assert manager.get_annotation(made.id) is None


def test_redo_brings_back_the_same_shape(manager):
    made = draw(manager, "arrow")
    manager.session.undo()
    manager.reload_from_document()

    _version, scope = manager.session.redo()
    manager.reload_from_document()

    assert scope is Scope.COMMENTS
    assert [h["id"] for h in shapes_on(manager.doc)] == [made.id]
    restored = manager.get_annotation(made.id)
    assert isinstance(restored, ShapeAnnotation)
    assert restored.kind == "arrow"
    assert restored.points == list(SHAPES["arrow"]["points"])


@pytest.mark.parametrize("kind", SHAPES)
def test_the_undo_step_is_named_for_the_shape(manager, kind):
    draw(manager, kind)

    assert manager.session.history.undo_label == LABELS[kind]


# ----------------------------------------------------------------- saved and reopened
@pytest.mark.parametrize("kind", SHAPES)
def test_a_shape_survives_a_save_and_a_reopen(manager, kind):
    """The vertices are the shape, so a reopened file has to agree exactly."""
    made = draw(manager, kind, page=1, color=RED, width=2.5)
    path = save_document(manager.session).path
    manager.session.close()

    reopened = AnnotationManager(make_session(path))
    try:
        held = shapes_on(reopened.doc, page=1)
        assert [h["id"] for h in held] == [made.id]
        assert held[0]["subtype"] == SUBTYPES[kind]
        model = reopened.get_annotation(made.id)
        assert isinstance(model, ShapeAnnotation)
        assert model.kind == kind
        assert model.color == RED
        assert model.width == pytest.approx(2.5)
        if kind in ("line", "arrow", "polygon"):
            expected = SHAPES[kind]["points"]
            assert len(model.points) == len(expected)
            for got, want in zip(model.points, expected):
                assert got == pytest.approx(want, abs=0.01)
    finally:
        reopened.session.close()


# ------------------------------------------------------------------ the listing side
def test_a_shape_rereads_only_the_page_it_was_drawn_on(tmp_path, monkeypatch):
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

    draw(manager, "rect", page=3)

    assert set(reads) == {3}
    manager.session.close()


def test_a_shape_is_listed_with_the_others(manager):
    """The comments panel shows every kind, so the row has to be there."""
    rect = draw(manager, "rect")
    arrow = draw(manager, "arrow")
    note = manager.add_note(0, (30.0, 40.0), "by hand")
    highlight = manager.add_highlight(1, [(10.0, 47.1, 113.3, 63.6)])

    listed = manager.get_annotations(page=0)
    assert {a.id for a in listed} == {rect.id, arrow.id, note.id}
    assert {a.type for a in listed} == {AnnotationType.SHAPE, AnnotationType.NOTE}
    shapes = manager.get_annotations(page=0, ann_type=AnnotationType.SHAPE)
    assert {a.kind for a in shapes} == {"rect", "arrow"}
    assert manager.get_annotations(page=1,
                                  ann_type=AnnotationType.HIGHLIGHT)[0].id == highlight.id


def test_a_shape_can_be_deleted_and_annotated_like_any_other(manager):
    """Delete and ``/Contents`` were never highlight-specific."""
    made = draw(manager, "ellipse")

    assert manager.set_note(made.id, "check this") is not None
    assert manager.get_note(made.id) == "check this"
    assert shapes_on(manager.doc)[0]["id"] == made.id
    assert manager.doc.load_page(0).annots().__next__().info["content"] == "check this"

    assert manager.remove_annotation(made.id) is True
    assert shapes_on(manager.doc) == []


# --------------------------------------------------------------------- other programs
def test_a_shape_another_program_drew_is_listed_without_being_renamed(tmp_path):
    """Reading a file must not have to write into it, so an unnamed shape is
    keyed by its object number and stays that way."""
    seed = fitz.open(build_sample_pdf(tmp_path / "seed.pdf", pages=2))
    page = seed.load_page(1)
    foreign = page.add_rect_annot(fitz.Rect(20.0, 20.0, 60.0, 50.0))
    foreign.set_colors(stroke=(0.0, 0.0, 1.0))
    foreign.update()
    seed.xref_set_key(foreign.xref, "NM", "null")
    path = tmp_path / "foreign.pdf"
    seed.save(path, garbage=2, deflate=True)
    seed.close()

    manager = AnnotationManager(make_session(str(path)))
    try:
        listed = manager.get_annotations(page=1, ann_type=AnnotationType.SHAPE)
        assert len(listed) == 1
        assert listed[0].id.startswith("anon:")
        assert listed[0].kind == "rect"
        assert listed[0].color == "#0000FF"
        xref = int(listed[0].id.split(":")[1])
        assert manager.doc.xref_get_key(xref, "NM") == ("null", "null")
    finally:
        manager.session.close()
