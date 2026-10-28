"""F10-3u/3v: another reader's XFDF as the marks it asks for, as one edit.

The export was judged by what a PDF says; the parser is judged by what a file
says, because that is the only thing it is given.  Four properties decide
whether an import is worth offering to the user:

* a number in the file becomes the page-space point it was flipped from, through
  the page's own crop box — the same rule in the other direction, so a mark that
  travels out and back has not moved and a file written by somebody else lands on
  the part of the sheet it names;
* an edit in the plan is an edit that will work: every value is checked against
  the rules the edits themselves enforce before it is offered, so nothing is
  applied that would be refused a moment later;
* what cannot be read is said out loud — the file as a whole is refused when
  it is not XFDF or is built to exhaust a reader, and a single mark is left out
  with a reason when it is merely not PDFAR's to draw;
* and the marks that do arrive arrive **together**: one import is one undo step,
  and an import that cannot be carried out leaves no part of the file behind.

Most of these files are written by hand rather than produced by the export,
because the point of an exchange is that the other reader is not you.
"""

from __future__ import annotations

import os
import re
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf, make_session
from pdfar import xfdf
from pdfar.annotations import (HIGHLIGHT_OPACITY, AddImportedMarks,
                               AddNote, AnnotationManager, measurement_reading)
from pdfar.edits import EditError, Scope
from pdfar.xfdf import (ImportPlan, XfdfError, export_xfdf, page_anchors,
                        parse_xfdf)
from test_xfdf import (SCALE, TOLERANCE, build_offset_page, draw_every_kind)

#: The sample page's height: what a file measures every y coordinate from.
HEIGHT = 100.0


def envelope(*marks: str, namespaced: bool = True) -> str:
    """A whole XFDF file holding *marks*."""
    root = (f'<xfdf xmlns="{xfdf.XFDF_NAMESPACE}" '
            f'xmlns:pdfar="{xfdf.PDFAR_NAMESPACE}">') if namespaced else "<xfdf>"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + root
            + "<annots>" + "".join(marks) + "</annots></xfdf>")


def box_file(box) -> str:
    """A page-space box as the four numbers the file holds."""
    x0, y0, x1, y1 = box
    return ",".join(f"{value:g}" for value in (x0, HEIGHT - y1, x1, HEIGHT - y0))


def quad_file(box) -> str:
    """A page-space box as the eight corners of one highlighted line."""
    x0, y0, x1, y1 = box
    return ",".join(f"{value:g}" for value in (
        x0, HEIGHT - y0, x1, HEIGHT - y0,
        x0, HEIGHT - y1, x1, HEIGHT - y1))


def points_file(points) -> str:
    """Page-space points as the file's ``x,y;x,y``."""
    return ";".join(f"{x:g},{HEIGHT - y}" for x, y in points)


@pytest.fixture
def target(tmp_path):
    """A manager over a document like the sample's and with nothing on it."""
    session = make_session(build_sample_pdf(tmp_path / "blank.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


def read(text: str, doc, taken=()) -> ImportPlan:
    """*text* as the plan it asks for, against the pages of *doc*."""
    return parse_xfdf(text, page_anchors(doc), taken=taken)


def only(plan: ImportPlan):
    """The one edit a plan holds, or a failure that says what it found."""
    assert plan.count == 1, (plan.summary(), plan.skipped)
    return plan.edits[0]


def flat(values) -> list:
    """A run of points or boxes as one list of numbers: pytest.approx compares a
    sequence of numbers, not a sequence of tuples."""
    return [number for value in values for number in value]


def named(count: int) -> str:
    """*count* notes, each with a name of the file's own."""
    return "".join(f'<text page="0" name="n{index}" rect="10,90,26,74"/>'
                   for index in range(count))


def marks(count: int, **attributes) -> str:
    """*count* unnamed notes, for the files that say nothing per mark."""
    extra = "".join(f' {name}="{value}"' for name, value in attributes.items())
    return f'<text page="0" rect="10,90,26,74"{extra}/>' * count


def reasons(plan: ImportPlan) -> list:
    return [reason for _label, reason in plan.skipped]


# ------------------------------------------------------------------ the envelope
def test_a_file_that_is_not_xml_is_refused(target):
    with pytest.raises(XfdfError):
        read("the quick brown fox", target.doc)


def test_a_file_whose_root_is_not_xfdf_is_refused(target):
    with pytest.raises(XfdfError, match="not XFDF"):
        read('<?xml version="1.0"?><comments><text/></comments>', target.doc)


def test_a_file_that_declares_another_namespace_is_refused(target):
    """A root element called ``xfdf`` in somebody else's namespace is a collision,
    not a dialect: reading it as XFDF would be guessing."""
    with pytest.raises(XfdfError, match="namespace"):
        read('<xfdf xmlns="urn:other:format"><annots/></xfdf>', target.doc)


def test_a_file_with_no_marks_in_it_is_read_as_having_none(target):
    """An envelope with nothing to say is a valid one: no error, no edits."""
    assert read(f'<xfdf xmlns="{xfdf.XFDF_NAMESPACE}"/>', target.doc).count == 0
    assert read(envelope(), target.doc).count == 0
    assert read(envelope().replace("<annots></annots>", "<fields/>"),
                target.doc).count == 0


def test_a_file_that_declares_no_namespace_is_still_read(target):
    """Namespaces qualify names; they do not change what the marks are."""
    plan = read(envelope(f'<text page="0" rect="10,{HEIGHT - 30},'
                         f'26,{HEIGHT - 14}"/>', namespaced=False), target.doc)
    assert only(plan).label == "Add note"


# ---------------------------------------------------------------------- the bounds
def test_a_document_type_or_entity_is_refused_before_it_is_parsed(target):
    """Definitions are the whole of what makes an XML file dangerous, and none of
    what a comment exchange needs: a file that carries them is refused unread."""
    bomb = ('<!DOCTYPE xfdf [<!ENTITY a "a">]>'
            f'<xfdf xmlns="{xfdf.XFDF_NAMESPACE}"><annots/></xfdf>')
    with pytest.raises(XfdfError, match="entities"):
        read(bomb, target.doc)


def test_tags_nested_past_any_real_file_are_refused(target):
    """Four levels is as deep as XFDF goes (``xfdf``, ``annots``, a mark, its
    contents), so a file that nests by the hundred is not read at all."""
    deep = "<a>" * 60 + envelope() + "</a>" * 60
    with pytest.raises(XfdfError, match="deeply"):
        read(deep, target.doc)


def test_a_file_larger_than_the_cap_is_refused(target, monkeypatch):
    """The size is checked while the text is still only text."""
    monkeypatch.setattr(xfdf, "MAX_INPUT_CHARS", 200)
    with pytest.raises(XfdfError, match="too large"):
        read(envelope(marks(1, title="x" * 400)), target.doc)


def test_a_file_of_too_many_elements_is_refused(target, monkeypatch):
    monkeypatch.setattr(xfdf, "MAX_ELEMENTS", 6)
    with pytest.raises(XfdfError, match="too many"):
        read(envelope(marks(8)), target.doc)


def test_more_marks_than_one_edit_can_take_are_refused(target, monkeypatch):
    """An import is one undo step; a file claiming more marks than that is not a
    comment exchange about this document."""
    monkeypatch.setattr(xfdf, "MAX_MARKS", 2)
    with pytest.raises(XfdfError, match="more marks"):
        read(envelope(marks(5)), target.doc)


# -------------------------------------------------------------------- the numbers
def test_the_numbers_are_turned_back_through_the_pages_own_box(tmp_path):
    """A page whose visible box starts away from the origin is measured from
    there, in both directions: what the export flipped out, the parser flips back.

    The box a shape carries is the file's own ``/Rect``, which is a point larger
    than the box PDFAR drew because it holds the room the stroke needs; the mark
    that comes back is the box the file states, on the sheet the file means.
    """
    made = AnnotationManager(make_session(build_offset_page(
        tmp_path / "offset.pdf")))
    try:
        drawn = made.add_shape(0, "rect", rect=(10.0, 10.0, 60.0, 30.0),
                               color="#FF0000")
        line = made.add_highlight(0, [(10.0, 20.0, 90.0, 28.0)])
        listed = {ann.id: ann for ann in made.get_annotations()}
        # The names are this document's own, so read the file as a foreign one.
        plan = read(re.sub(r'name="[^"]*"', "", export_xfdf(made)), made.doc)
        shapes = [edit for edit in plan.edits if edit.label == "Rectangle"]
        highlights = [edit for edit in plan.edits if edit.label == "Highlight"]
        assert len(shapes) == 1 and len(highlights) == 1
        assert shapes[0].rect == pytest.approx(listed[drawn.id].rects[0],
                                               abs=TOLERANCE)
        assert flat(highlights[0].specs[0].rects) == pytest.approx(
            flat(listed[line.id].rects), abs=TOLERANCE)
        # And inside this page's own visible area, which a flip through the media
        # box's height would not leave them in.
        assert 0.0 <= shapes[0].rect[0] and shapes[0].rect[2] <= 150.0
        assert 0.0 <= shapes[0].rect[1] and shapes[0].rect[3] <= 40.0
    finally:
        made.session.close()


def test_a_highlight_reads_one_box_per_line_it_covers(target):
    """Sixteen numbers are two quadrilaterals, and a highlight is the two lines
    they select — which is what makes a selection across lines one mark."""
    first, second = (10.0, 20.0, 100.0, 30.0), (10.0, 40.0, 120.0, 50.0)
    edit = only(read(envelope(
        f'<highlight page="0" coords="{quad_file(first)},{quad_file(second)}"/>'
    ), target.doc))
    assert edit.specs[0].page == 0
    assert flat(edit.specs[0].rects) == pytest.approx(
        flat((first, second)), abs=TOLERANCE)


def test_a_highlight_with_no_quadrilaterals_reads_the_box_it_names(target):
    """A reader that only said where, in a rectangle, takes the whole of it as one
    line: a mark in the right place beats a mark refused for the half the file
    left out."""
    box = (10.0, 20.0, 90.0, 40.0)
    edit = only(read(envelope(
        f'<highlight page="0" rect="{box_file(box)}"/>'), target.doc))
    assert flat(edit.specs[0].rects) == pytest.approx(
        flat((box,)), abs=TOLERANCE)


def test_a_selection_written_in_pieces_that_do_not_close_is_left_out(target):
    """Fourteen numbers are not two quads.  Falling back on the box then would
    merge every line of the selection into one mark wider than any of them, so a
    file that contradicts itself is not completed by guesswork."""
    broken = ",".join("10" for _ in range(14))
    plan = read(envelope(f'<highlight page="0" rect="10,60,120,80"'
                         f' coords="{broken}"/>'), target.doc)
    assert plan.count == 0
    assert "quadrilaterals" in reasons(plan)[0]


def test_one_number_that_is_not_a_number_voids_the_mark(target):
    """``inf``, ``nan`` and a word among the coordinates leave the geometry
    meaningless, and a mark placed at a guess is a mark in the wrong place."""
    for attribute in ('coords="10,90,100,90,10,70,100,nan"',
                      'rect="10,90,inf,70"',
                      'rect="10,90,26"'):
        plan = read(envelope(f'<highlight page="0" {attribute}/>'), target.doc)
        assert plan.count == 0, attribute


def test_a_colour_of_three_digits_is_the_colour_it_wrote(target):
    """``#f80`` is ``#FF8800``, which is the only way a shorter form means
    anything."""
    edit = only(read(envelope(
        f'<square page="0" rect="10,{HEIGHT - 40},60,{HEIGHT - 10}"'
        ' color="#f80"/>'), target.doc))
    assert edit.color == "#FF8800"


def test_a_colour_that_is_not_one_leaves_the_tools_default_stand(target):
    """No colour is not black: a mark the file says nothing about keeps the colour
    PDFAR's own tools would have given it."""
    edit = only(read(envelope(
        f'<square page="0" rect="10,{HEIGHT - 40},60,{HEIGHT - 10}"'
        ' color="not-a-colour"/>'), target.doc))
    assert edit.color == "#000000"


def test_a_solidity_outside_all_and_nothing_is_no_solidity(target):
    """``opacity`` is a fraction; a file that says seven, or nothing, is not
    answered with an opacity of seven."""
    for opacity in ("7", "-0.2", "quite"):
        plan = read(envelope(
            f'<highlight page="0" rect="10,60,100,80" opacity="{opacity}"/>'
        ), target.doc)
        assert only(plan).opacity == HIGHLIGHT_OPACITY, opacity
    assert only(read(envelope(
        '<highlight page="0" rect="10,60,100,80" opacity="0.5"/>'
    ), target.doc)).opacity == 0.5


# ------------------------------------------------------------------- each kind
def test_a_note_keeps_where_it_sits_and_what_it_says(target):
    """The icon is the mark and its top-left corner is what a click lands on."""
    edit = only(read(envelope(
        f'<text page="1" rect="150,{HEIGHT - 26},166,{HEIGHT - 10}"'
        ' title="Wachin"><contents>sticky</contents></text>'), target.doc))
    assert (edit.page, edit.position) == (1, (150.0, 10.0))
    assert (edit.note, edit.author) == ("sticky", "Wachin")


def test_a_notes_text_is_its_own_and_not_the_files_whitespace(target):
    """What sits between ``contents`` is the comment, newlines included, because
    the format says the whitespace is part of the text."""
    edit = only(read(envelope(
        f'<text page="0" rect="10,{HEIGHT - 26},26,{HEIGHT - 10}">'
        "<contents>\n  two lines  \n</contents></text>"), target.doc))
    assert edit.note == "\n  two lines  \n"


def test_a_notes_icon_is_its_own_business_and_still_a_note(target):
    """PDFAR draws one note shape.  Another reader's preference for a key or a
    flag is not a different kind of comment, and not a reason to leave it out."""
    edit = only(read(envelope(
        f'<text page="0" icon="Key" rect="10,{HEIGHT - 26},26,'
        f'{HEIGHT - 10}"><popup open="yes"/>'
        "<contents>a pinned note</contents></text>"), target.doc))
    assert (edit.note, edit.position) == ("a pinned note", (10.0, 10.0))


def test_the_page_machinery_around_the_marks_is_not_read_as_one(target):
    """``template``, ``idmap`` and ``page`` describe how a form is filled in, not
    where a mark sits: the container PDFAR looks in is ``annots`` and nothing
    else, so a file that carries both is read for its comments."""
    text = (f'<?xml version="1.0"?>\n'
            f'<xfdf xmlns="{xfdf.XFDF_NAMESPACE}">'
            '<template href="other.xfdf"/><idmap><map key="k" value="v"/></idmap>'
            '<page def="p0" name="0"/>'
            "<annots>"
            f'<text page="0" rect="10,{HEIGHT - 26},26,{HEIGHT - 10}"/>'
            "</annots></xfdf>")
    assert read(text, target.doc).count == 1


def test_an_ink_reads_every_gesture_as_one_stroke(target):
    """Two gestures are two strokes of one drawing, whichever way the file grouped
    them; a stroke of one point draws no line, so it draws nothing."""
    strokes = [((5.0, 5.0), (20.0, 20.0), (30.0, 25.0)),
               ((60.0, 20.0), (90.0, 40.0))]
    nested = ("<inklist>"
              + "".join(f"<gesture>{points_file(s)}</gesture>" for s in strokes)
              + "</inklist>")
    loose = "".join(f"<gesture>{points_file(s)}</gesture>"
                    for s in strokes)
    for body in (nested, loose):
        edit = only(read(envelope(
            f'<ink page="0" color="#00FF00" width="4">{body}</ink>'),
            target.doc))
        assert flat(edit.paths) == pytest.approx(flat(strokes),
                                                 abs=TOLERANCE)
        assert (edit.color, edit.width) == ("#00FF00", 4.0)

    plan = read(envelope(
        f'<ink page="0"><gesture>{points_file([(7.0, 7.0)])}</gesture></ink>'),
        target.doc)
    assert plan.count == 0
    assert "no stroke" in reasons(plan)[0]


def test_a_gesture_holding_its_points_in_an_attribute_is_still_a_stroke(target):
    """Adobe writes a gesture's numbers as its content; a writer that puts them in
    the ``vertices`` attribute the format uses for a polygon says the same drawing
    in another punctuation, and losing the sketch over it would be the worse
    reading.  (Found by the hand-written file the visual check reads.)"""
    stroke = ((60.0, 100.0), (180.0, 76.0), (300.0, 100.0))
    edit = only(read(envelope(
        f'<ink page="0"><inklist><gesture '
        f'vertices="{points_file(stroke)}"/></inklist></ink>'),
        target.doc))
    assert flat(edit.paths) == pytest.approx(flat([stroke]), abs=TOLERANCE)


def test_a_square_and_a_circle_arrive_as_the_shapes_they_are(target):
    box = (10.0, 20.0, 60.0, 40.0)
    plan = read(envelope(
        f'<square page="0" rect="{box_file(box)}"/>'
        f'<circle page="2" rect="{box_file(box)}"/>'), target.doc)
    assert sorted(edit.kind for edit in plan.edits) == ["ellipse", "rect"]
    assert all(edit.rect == pytest.approx(box, abs=TOLERANCE)
               for edit in plan.edits)
    assert [edit.page for edit in plan.edits] == [0, 2]


def test_a_shape_with_no_room_in_it_is_left_out(target):
    """A box with no area is not a thin rectangle but a broken annotation, and the
    edit that would draw it refuses it — so the parser refuses it first."""
    plan = read(envelope(
        f'<square page="0" rect="10,{HEIGHT - 30},10,{HEIGHT - 30}"/>'),
        target.doc)
    assert plan.count == 0
    assert "box with area" in reasons(plan)[0]


def test_only_the_arrowhead_pdfar_writes_reads_back_as_an_arrow(target):
    """An arrow is a line whose far end is closed.  A head on the near end keeps
    its geometry and loses the decoration, because moving an arrowhead to the
    other end points it somewhere the file did not draw it."""
    ends = 'start="10,20" end="150,10"'
    arrow = only(read(envelope(
        f'<line page="0" {ends} head="None" tail="ClosedArrow"/>'), target.doc))
    assert arrow.kind == "arrow"
    line = only(read(envelope(
        f'<line page="0" {ends} head="ClosedArrow" tail="None"/>'), target.doc))
    assert line.kind == "line"
    assert flat(line.points) == pytest.approx(
        flat([(10.0, 80.0), (150.0, 90.0)]), abs=TOLERANCE)


def test_a_line_with_one_end_is_no_line(target):
    plan = read(envelope('<line page="0" start="10,20"/>'), target.doc)
    assert plan.count == 0
    assert "ends" in reasons(plan)[0]


def test_a_polygon_is_the_corners_it_encloses(target):
    corners = [(10.0, 10.0), (60.0, 20.0), (40.0, 70.0), (12.0, 30.0)]
    edit = only(read(envelope(
        f'<polygon page="1"><vertices>{points_file(corners)}</vertices>'
        "</polygon>"), target.doc))
    assert flat(edit.points) == pytest.approx(flat(corners), abs=TOLERANCE)


def test_a_polygon_of_two_corners_is_left_out(target):
    plan = read(envelope(
        f'<polygon page="0"><vertices>{points_file([(1.0, 2.0), (3.0, 4.0)])}'
        "</vertices></polygon>"), target.doc)
    assert plan.count == 0
    assert "three points" in reasons(plan)[0]


def test_an_open_path_is_left_out_because_pdfar_has_no_shape_for_it(target):
    """A polyline is neither a closed polygon nor a line between two points, and
    drawing the edge the file did not draw would be inventing geometry."""
    corners = [(5.0, 5.0), (25.0, 5.0), (25.0, 40.0)]
    plan = read(envelope(
        '<polyline page="0"><vertices>' + points_file(corners)
        + "</vertices></polyline>"), target.doc)
    assert plan.count == 0
    assert "open path" in reasons(plan)[0]


def test_a_stamp_arrives_by_the_name_it_carries(target):
    """The fourteen standard stamps need no picture to travel: a reader draws
    ``Approved`` from its own copy, so the name is the whole mark."""
    box = (40.0, 34.0, 160.0, 66.0)
    edit = only(read(envelope(
        f'<stamp page="0" rect="{box_file(box)}" icon="Approved"/>'),
        target.doc))
    assert edit.name == "Approved"
    assert edit.center == pytest.approx((100.0, 50.0), abs=TOLERANCE)


def test_a_stamp_of_pdfs_own_making_comes_back_as_its_word(target):
    """PDFAR's ``Custom`` stamp travels as the caption it was drawn from, which is
    the only part of it an exchange file could hold."""
    edit = only(read(envelope(
        f'<stamp page="0" rect="{box_file((57.0, 62.0, 143.0, 100.0))}"'
        ' icon="Custom"><contents>Verified</contents></stamp>'), target.doc))
    assert edit.caption == "Verified"
    assert edit.image == ""


def test_a_stamp_whose_picture_stayed_behind_is_not_invented(target):
    """A custom mark with no word in it is a picture no file carried; a blank box
    is not that mark, so it is left out with the reason."""
    plan = read(envelope(
        f'<stamp page="0" rect="{box_file((57.0, 62.0, 143.0, 100.0))}"'
        ' icon="Custom"/>'), target.doc)
    assert plan.count == 0
    assert "cannot travel" in reasons(plan)[0]


def test_another_readers_stamp_name_is_not_one_pdfar_draws(target):
    """``Watermark`` is a name PDFAR has no appearance for, and a box of the wrong
    shape called by the wrong name would claim to be it."""
    plan = read(envelope(
        f'<stamp page="0" rect="{box_file((40.0, 34.0, 160.0, 66.0))}"'
        ' icon="Watermark"/>'), target.doc)
    assert plan.count == 0
    assert "Watermark" in reasons(plan)[0]


# ---------------------------------------------------------------- measurements
SCALE_ATTRIBUTES = (f'pdfar:measure="distance" pdfar:scale="'
                    f'{SCALE.units_per_point}" pdfar:unit="{SCALE.unit}" '
                    f'pdfar:precision="{SCALE.precision}"')


def measured(*attributes: str) -> str:
    """A two-point line in file space, with whatever else the test asks for."""
    return ('<line page="0" start="20,70" end="120,70" '
            + " ".join(attributes) + "/>")


def test_a_dimension_is_only_a_dimension_with_a_scale(target):
    """XFDF has no word for ``/Measure``, so the scale travels in PDFAR's own
    namespace.  A line with it is a measurement; the same line without it is a
    line, because printing a reading the file never stated would be a lie about
    the distance."""
    measurement = only(read(envelope(measured(
        'intent="LineDimension"', SCALE_ATTRIBUTES)), target.doc))
    assert (measurement.kind, measurement.page) == ("distance", 0)
    assert flat(measurement.points) == pytest.approx(
        flat([(20.0, 30.0), (120.0, 30.0)]), abs=TOLERANCE)
    assert (measurement.units_per_point, measurement.unit,
            measurement.precision) == (SCALE.units_per_point, SCALE.unit,
                                       SCALE.precision)

    plain = only(read(envelope(measured()), target.doc))
    assert (plain.kind, plain.label) == ("line", "Line")


def test_a_measurement_scale_the_file_does_not_carry_is_no_measurement(target):
    """A missing or unusable scale is not answered with PDFAR's default: a number
    from the viewer rather than the file is the one thing a measure must not be."""
    for incomplete in ('pdfar:unit="cm"', 'pdfar:scale="2.5"',
                       'pdfar:precision="x"', 'pdfar:scale="-2.5"'):
        plan = read(envelope(measured(
            'pdfar:measure="distance"', incomplete)), target.doc)
        assert plan.count == 0, incomplete
        assert "scale" in reasons(plan)[0]


def test_a_measure_made_of_a_shape_it_does_not_use_is_left_out(target):
    """A distance is two ends and an area is a closed path; a file that says one
    and draws the other is not read as either."""
    plan = read(envelope(
        f'<polygon page="0" pdfar:measure="distance" pdfar:scale="2.5"'
        ' pdfar:unit="cm" pdfar:precision="2">'
        f"<vertices>{points_file([(10.0, 10.0), (60.0, 10.0), (60.0, 60.0)])}"
        "</vertices></polygon>"), target.doc)
    assert plan.count == 0
    assert "does not measure that way" in reasons(plan)[0]


def test_a_kind_pdfar_does_not_measure_is_left_out(target):
    plan = read(envelope(measured(
        'pdfar:measure="volume"', 'pdfar:scale="2.5"', 'pdfar:unit="cm"',
        'pdfar:precision="2"')), target.doc)
    assert plan.count == 0
    assert "volume" in reasons(plan)[0]


def test_a_reading_the_file_copied_off_the_geometry_is_left_to_be_rewritten(
        target):
    """A caption that is only the number the mark prints carries no information:
    dropped, so the mark reads itself again — and still reads correctly once the
    user drags one of its points.  A note of the reader's own is kept."""
    points = ((20.0, 30.0), (120.0, 30.0))
    reading = measurement_reading("distance", points, SCALE)
    ends = f'start="20,70" end="120,70" {SCALE_ATTRIBUTES}'
    edit = only(read(envelope(
        f'<line page="0" {ends}><contents>{reading}</contents></line>'),
        target.doc))
    assert edit.note == ""
    kept = only(read(envelope(
        f'<line page="0" {ends}><contents>measure the span</contents></line>'),
        target.doc))
    assert kept.note == "measure the span"


def test_an_area_written_with_its_corner_repeated_is_still_one_area(target):
    """A file that repeats the first corner to close the path is describing the
    same three corners; the repeat is noise and the reading is unchanged."""
    corners = [(20.0, 30.0), (60.0, 30.0), (60.0, 80.0)]
    mark = (f'<polygon page="1" pdfar:measure="area" pdfar:scale="2.5"'
            ' pdfar:unit="cm" pdfar:precision="2">'
            f"<vertices>{points_file(corners + [corners[0]])}</vertices>"
            "</polygon>")
    edit = only(read(envelope(mark), target.doc))
    assert list(edit.points) == pytest.approx(corners, abs=TOLERANCE)


# --------------------------------------------------------------------- identity
def test_a_name_the_document_already_holds_claims_no_second_mark(target):
    """Two marks with one name are one mark to PDFAR: the panel lists them
    together and one delete takes both."""
    file = '<text page="0" name="already" rect="10,90,26,74"/>'
    assert read(envelope(file), target.doc).count == 1
    plan = read(envelope(file), target.doc, taken=["already"])
    assert plan.count == 0
    assert "already holds" in reasons(plan)[0]


def test_a_repeated_name_in_one_file_takes_the_first_copy(target):
    file = '<text page="0" name="twice" rect="10,90,26,74"/>'
    plan = read(envelope(file, file), target.doc)
    assert plan.count == 1
    assert "already holds" in reasons(plan)[0]


def test_importing_the_same_file_twice_is_importing_it_once(target):
    """The ids the first plan handed out are the names the second one collides
    with, which is the whole of what makes a second import change nothing."""
    first = read(envelope(named(3)), target.doc)
    names = sorted(edit.ann_id for edit in first.edits)
    assert names == ["n0", "n1", "n2"]
    second = read(envelope(named(3)), target.doc, taken=names)
    assert second.count == 0
    assert len(second.skipped) == 3


def test_a_mark_with_no_usable_name_gets_one_of_its_own(target):
    """PDFAR's ids are UUIDs, and the name goes into the file between parentheses
    unescaped: a ``)`` or a space in a reader's name would end the string and
    break the object that holds the page's marks.  So an unusable name is dropped
    and the mark arrives as the new one it is."""
    for name in ("two words", "ends badly)", "#hash", "a" * 200):
        edit = only(read(envelope(
            f'<text page="0" name="{name}" rect="10,90,26,74"/>'), target.doc))
        assert edit.ann_id != name
        assert uuid.UUID(edit.ann_id)


def test_two_nameless_marks_are_two_marks(target):
    """A file that names nothing is not a file of one mark: each gets its own id,
    so the panel lists them apart and a delete takes one at a time."""
    plan = read(envelope(marks(3)), target.doc)
    assert plan.count == 3
    assert len({edit.ann_id for edit in plan.edits}) == 3


# ----------------------------------------------------------------------- pages
def test_a_page_the_document_does_not_have_is_not_a_mark(target):
    """There is nowhere to put it, and putting it on another page would be a mark
    the file never made."""
    said = set()
    for page in ("7", "-1", "one", ""):
        plan = read(envelope(
            f'<text page="{page}" rect="10,90,26,74"/>'), target.doc)
        assert plan.count == 0, page
        said.update(reasons(plan))
    assert said == {"names a page this document does not have",
                    "does not say which page it is on"}


def test_a_mark_that_names_no_page_is_left_where_it_cannot_be_placed(target):
    plan = read(envelope('<text rect="10,90,26,74"/>'), target.doc)
    assert plan.count == 0
    assert "which page" in reasons(plan)[0]


# ----------------------------------------------------------------------- the plan
def test_a_file_of_other_readers_marks_counts_what_it_left_out(target):
    """A free-text comment is somebody else's kind of mark, not a corrupt byte:
    it is left out and the user is told, and everything else in the file arrives."""
    box = (10.0, 20.0, 60.0, 40.0)
    plan = read(envelope(
        f'<square page="0" rect="{box_file(box)}"/>',
        f'<freetext page="0" rect="{box_file(box)}">typed on the page</freetext>',
        f'<strikeout page="0" coords="{quad_file(box)}"/>'), target.doc)
    assert plan.count == 1
    assert plan.summary() == "1 mark, 2 not taken"
    assert [label for label, _reason in plan.skipped] == [
        "freetext on page 1", "strikeout on page 1"]
    assert "kind of mark" in reasons(plan)[0]


def test_a_plan_says_how_many_marks_it_holds(target):
    assert read(envelope(marks(4)), target.doc).summary() == "4 marks"
    assert read(envelope(marks(1)), target.doc).summary() == "1 mark"


def test_reading_a_file_changes_nothing(target):
    """The plan is a plan: no annotation is written, no page changes, no model
    moves until the edit that applies it is accepted."""
    read(envelope(marks(3)), target.doc)
    assert target.get_annotations() == []
    assert sum(len(list(target.doc.load_page(index).annots()))
               for index in range(target.doc.page_count)) == 0


def test_every_kind_pdfar_draws_survives_the_file(tmp_path, target):
    """The export's own vocabulary, read back: one edit for every element it
    wrote, and each of them an edit a document accepts."""
    made = AnnotationManager(make_session(build_sample_pdf(
        tmp_path / "source.pdf", pages=3)))
    try:
        draw_every_kind(made)
        plan = read(export_xfdf(made), target.doc)
        assert plan.count == len(made.get_annotations()) == 13
        assert not plan.skipped
        assert {type(edit).__name__ for edit in plan.edits} == {
            "AddHighlight", "AddNote", "AddInk", "AddShape", "AddStamp",
            "AddCustomStamp", "AddMeasurement"}
        for edit in plan.edits:
            target.session.apply(edit)
        target.reload_from_document()
        assert len(target.get_annotations()) == plan.count
    finally:
        made.session.close()


# ------------------------------------------------------------------- applying
def take(manager, text: str) -> ImportPlan:
    """*text* imported through the manager, or a failure that says so."""
    plan = manager.import_xfdf(text)
    assert plan is not None, "the import was refused"
    return plan


def test_an_import_is_one_undo_step(target):
    """Three marks over two pages arrive together and go away together, because
    the user imported once."""
    plan = take(target, envelope(
        f'<text page="0" rect="{box_file((10.0, 10.0, 26.0, 26.0))}" name="a"/>',
        f'<text page="0" rect="{box_file((40.0, 40.0, 56.0, 56.0))}" name="b"/>',
        f'<square page="2" rect="{box_file((10.0, 10.0, 40.0, 40.0))}" '
        f'name="c"/>'))
    assert plan.count == 3
    assert sorted(ann.id for ann in target.get_annotations()) == ["a", "b", "c"]

    target.session.undo()
    target.reload_from_document()
    assert target.get_annotations() == []
    target.session.redo()
    target.reload_from_document()
    assert sorted(ann.id for ann in target.get_annotations()) == ["a", "b", "c"]


def test_the_import_is_a_comments_edit_on_the_pages_it_writes(target):
    """The scope decides what the view throws away, so an import that touched
    three pages must not claim to have repainted the document, and must not
    promise the list that it wrote a page it did not."""
    plan = read(envelope(
        marks(2),
        f'<square page="2" rect="{box_file((10.0, 10.0, 40.0, 40.0))}"/>'),
        target.doc)
    edit = plan.edit
    assert isinstance(edit, AddImportedMarks)
    assert edit.marks == plan.edits
    assert int(edit.scope) == int(Scope.COMMENTS)
    # Two marks share page 0; the page is named once, and in order.
    assert edit.pages == (0, 2)


def test_the_undo_entry_names_the_import_and_counts_it(target):
    take(target, envelope(marks(3)))
    assert target.session.history.undo_label == "Import 3 comments"
    target.session.undo()
    assert target.session.history.redo_label == "Import 3 comments"

    take(target, envelope('<text page="0" rect="10,90,26,74"/>'))
    assert target.session.history.undo_label == "Import 1 comment"


def test_an_import_that_cannot_be_applied_leaves_nothing_behind(target):
    """Half a conversation on the pages is the failure this edit exists to
    prevent: the mark that goes in first comes out again with the one that
    failed, because the document is only adopted if all of them were written."""
    version = target.session.version
    with pytest.raises(EditError):
        target.session.apply(AddImportedMarks(marks=(
            AddNote(ann_id="fine", page=0, position=(10.0, 10.0)),
            AddNote(ann_id="gone", page=99, position=(10.0, 10.0)))))
    target.reload_from_document()
    assert target.get_annotations() == []
    assert target.session.version == version


def test_an_import_reports_what_it_left_out(target):
    box = (10.0, 20.0, 60.0, 40.0)
    plan = take(target, envelope(
        f'<square page="0" rect="{box_file(box)}" name="mine"/>',
        f'<freetext page="0" rect="{box_file(box)}">typed by someone else</freetext>',
        f'<polyline page="1" vertices="{points_file([(5.0, 5.0), (9.0, 9.0)])}"/>'))
    assert plan.summary() == "1 mark, 2 not taken"
    assert [ann.id for ann in target.get_annotations()] == ["mine"]


def test_importing_the_same_file_twice_through_the_manager_imports_it_once(target):
    """The second click is the one that does nothing, because the names the first
    import wrote are now the names the document holds."""
    version = target.session.version
    file = envelope(named(3))
    assert take(target, file).count == 3
    again = take(target, file)
    assert again.count == 0
    assert len(again.skipped) == 3
    assert target.session.version == version + 1
    assert sorted(ann.id for ann in target.get_annotations()) == ["n0", "n1", "n2"]


def test_a_file_with_nothing_to_take_makes_no_undo_step(target):
    """An empty step in the history is one the user pays for and cannot see, so a
    file that arrives with no marks of its own leaves the document alone."""
    version = target.session.version
    assert take(target, envelope()).count == 0
    assert take(target, envelope('<polyline page="0" vertices="1,99;3,97"/>')
                ).count == 0
    assert target.session.version == version
    assert target.session.history.undo_label is None


def test_a_file_that_is_not_xfdf_is_refused_before_anything_is_applied(target):
    version = target.session.version
    with pytest.raises(XfdfError):
        target.import_xfdf("the quick brown fox")
    assert target.session.version == version
    assert target.get_annotations() == []
