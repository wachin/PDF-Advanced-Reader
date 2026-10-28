"""F10-3t: the document's comments as a file another reader can open (export).

XFDF is the format Adobe defined for exchanging PDF annotations, so its whole
value is that someone else reads it.  Three properties decide whether PDFAR's
output is worth anything to that someone:

* the numbers in the file are the numbers the PDF holds — measured from the
  bottom-left of the page's own crop box, in the order the specification gives a
  quadrilateral — so a mark lands where it is, the right way up, in a reader that
  knows nothing of PDFAR's view;
* what is in the file is what PDFAR's list says the document has, including the
  scale of a measurement, which XFDF has no word for and which therefore travels
  in a namespace of PDFAR's own; and
* nothing is claimed that the file never said: a mark with no colour of its own,
  no name of its own or no opacity of its own goes out without one, rather than
  with a viewer's default standing in for it.

Each is checked against the PDF rather than against a picture of what the XML
should say, because the PDF is the thing being claimed.
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar import xfdf
from pdfar.annotations import (AnnotationManager, HighlightAnnotation,
                               InkAnnotation, MeasurementScale)
from pdfar.xfdf import export_xfdf

HIGHLIGHT = (10.0, 20.0, 100.0, 30.0)
SCALE = MeasurementScale(2.5, "cm", 2)

#: The crop box of the offset page below: 150 x 40, starting 36 pt in from the
#: media box's origin — how a scanned page arrives with its paste margins.
CROP = (36.0, 36.0, 186.0, 76.0)

#: What a number is worth when each side rounded it to four decimals of its own.
TOLERANCE = 1e-3

_FACTOR = re.compile(r"/C ([-.0-9]+)")
#: The name ``xml:space`` carries once a parser has resolved the prefix.
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def qname(tag: str) -> str:
    return f"{{{xfdf.XFDF_NAMESPACE}}}{tag}"


def local(element) -> str:
    """An element's name without the namespace a reader has to match anyway."""
    return element.tag.removeprefix(qname(""))


def pdfar(attribute: str) -> str:
    return f"{{{xfdf.PDFAR_NAMESPACE}}}{attribute}"


def _numbers(text):
    """Every number in an XFDF list, in order."""
    return [float(value) for value in re.findall(r"[-+0-9.eE]+", text or "")]


def _raw(doc, annot, key):
    """The numbers the annotation's own PDF array holds."""
    kind, value = doc.xref_get_key(annot.xref, key)
    assert kind == "array", (key, kind, value)
    return _numbers(value)


# ------------------------------------------------------------------- fixtures
@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


def over(path):
    """A manager over *path*, for a document that is not the plain sample."""
    return AnnotationManager(make_session(path))


def exported(made):
    """The elements the export wrote, in the order it wrote them."""
    return list(ET.fromstring(export_xfdf(made)).find(qname("annots")))


def one(made, tag):
    found = [element for element in exported(made)
             if element.tag == qname(tag)]
    assert len(found) == 1, (tag, [element.tag for element in found])
    return found[0]


def find(doc, ann_id):
    """The page and annotation object named *ann_id*."""
    for index in range(doc.page_count):
        page = doc.load_page(index)
        for annot in page.annots():
            if annot.info.get("id") == ann_id:
                return page, annot
    raise AssertionError(f"“{ann_id}” is not in the document")


def _array(values) -> str:
    return "[" + " ".join(f"{value:g}" for value in values) + "]"


def build_offset_page(path, *, mediabox=None, crop=CROP):
    """A page whose visible box starts away from the origin."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    if mediabox is not None:
        doc.xref_set_key(page.xref, "MediaBox", _array(mediabox))
    doc.xref_set_key(page.xref, "CropBox", _array(crop))
    doc.save(str(path))
    doc.close()
    return str(path)


def build_rotated_page(path, rotation=90):
    """A page the file itself asks to be shown turned — how a scan arrives."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    doc.xref_set_key(page.xref, "Rotate", str(rotation))
    doc.save(str(path))
    doc.close()
    return str(path)


def build_inherited(path):
    """A file from another editor, with what such a file carries.

    A named square whose colour and border the dictionary holds; a highlight with
    an author, a note and both dates and *no* ``/CA``; a free-text comment PDFAR
    has no model for; and a link, which is page machinery rather than a comment.
    """
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    square = page.add_rect_annot(fitz.Rect(20.0, 20.0, 60.0, 50.0))
    square.set_colors(stroke=(0.0, 0.0, 1.0))
    square.set_border(width=3.0)
    square.update()
    doc.xref_set_key(square.xref, "NM", "(foreign-square)")
    highlight = page.add_highlight_annot([fitz.Rect(10.0, 60.0, 120.0, 70.0)])
    highlight.set_info(title="Inherited", content="kept",
                       creationDate="D:20260101120000+01'00'",
                       modDate="D:20260202130000-05'00'")
    highlight.update()
    free = page.add_freetext_annot(fitz.Rect(60.0, 80.0, 150.0, 95.0),
                                   "typed on the page")
    free.update()
    page.insert_link({"kind": fitz.LINK_URI,
                      "from": fitz.Rect(100.0, 20.0, 180.0, 40.0),
                      "uri": "https://example.com"})
    doc.save(str(path))
    doc.close()
    return str(path)


def draw_every_kind(made):
    """One mark of every kind PDFAR draws, on a page of its own."""
    made.add_highlight(0, [HIGHLIGHT, (10.0, 40.0, 120.0, 50.0)],
                        author="Wachin", note="two lines")
    made.add_note(0, (150.0, 10.0), "sticky")
    made.add_ink(1, [[(5.0, 5.0), (20.0, 20.0), (30.0, 25.0)],
                     [(60.0, 20.0), (90.0, 40.0)]], color="#00FF00", width=4)
    made.add_shape(1, "rect", rect=(20.0, 30.0, 60.0, 70.0), color="#000000")
    made.add_shape(1, "ellipse", rect=(80.0, 30.0, 120.0, 70.0),
                   color="#0000FF", width=2)
    made.add_shape(2, "line", points=[(10.0, 80.0), (150.0, 90.0)])
    made.add_shape(2, "arrow", points=[(10.0, 10.0), (60.0, 60.0)])
    made.add_shape(2, "polygon", points=[(10.0, 10.0), (60.0, 20.0),
                                         (40.0, 70.0), (12.0, 30.0)])
    made.add_stamp(0, "Approved", center=(100.0, 50.0))
    made.add_custom_stamp(1, (100.0, 85.0), caption="Verified")
    made.add_measurement(0, [(20.0, 30.0), (120.0, 30.0)], SCALE)
    made.add_measurement(1, [(20.0, 30.0), (60.0, 30.0), (60.0, 80.0)],
                         MeasurementScale(0.0123456789, "m", 3), kind="area")
    made.add_measurement(2, [(10.0, 10.0), (60.0, 10.0), (60.0, 60.0)], SCALE,
                         kind="perimeter")


# ---------------------------------------------------------------- the envelope
def test_the_file_is_xfdf_and_holds_nothing_but_the_comments(manager):
    """The envelope a reader looks for, and no field container to misread."""
    manager.add_note(0, (30.0, 30.0), "one note")
    text = export_xfdf(manager)

    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n')
    root = ET.fromstring(text)
    assert root.tag == qname("xfdf")
    # Whitespace inside a note is part of the note, so the envelope has to say so.
    assert root.get(_XML_SPACE) == "preserve"
    assert 'xml:space="preserve"' in text
    assert [element.tag for element in exported(manager)] == [qname("text")]
    # Form fields are not comments, and an empty <fields> could be read as an
    # instruction to clear every one of them.
    assert root.find(qname("fields")) is None


def test_a_document_without_comments_writes_a_file_a_reader_can_open(manager):
    assert exported(manager) == []


def test_every_kind_pdfar_draws_becomes_the_element_the_format_names(manager):
    """Thirteen marks, thirteen elements, each named for what it is."""
    draw_every_kind(manager)
    listed = exported(manager)
    tags = [local(element) for element in listed]

    assert sorted(tags) == sorted([
        "highlight", "text", "ink", "square", "circle", "line", "line",
        "polygon", "stamp", "stamp", "line", "polygon", "polyline"])
    assert len(listed) == len(manager.get_annotations())
    # Every mark PDFAR made carries its own name, so an import can tell a mark it
    # already wrote from one it has never seen.
    assert all(element.get("name") for element in listed)


def test_writing_the_same_document_twice_writes_the_same_file(manager):
    """No timestamp and no ordering change: an export is a function of the file."""
    draw_every_kind(manager)
    assert export_xfdf(manager) == export_xfdf(manager)


# ----------------------------------------------------- the numbers are the file's
def test_a_box_is_the_page_space_box_flipped_through_the_page(manager):
    """The one rule, stated as a number: y runs up from the bottom.

    The sample page is 100 pt tall, so a highlight whose top edge lies 20 pt below
    the top of the page is written 80 pt above its bottom — and its corners come
    in the order the specification gives a quadrilateral, which traces the line
    rather than a bow tie.
    """
    manager.add_highlight(0, [HIGHLIGHT])
    element = one(manager, "highlight")

    assert element.get("coords") == "10,80,100,80,10,70,100,70"
    assert element.get("rect") == "10,70,100,80"


def test_the_numbers_written_are_the_numbers_the_pdf_holds(manager):
    """Every mark's geometry, against the array the annotation object holds."""
    draw_every_kind(manager)
    doc = manager.doc
    compared = 0
    for element in exported(manager):
        ann = manager.get_annotation(element.get("name"))
        assert ann is not None, element.get("name")
        _page, annot = find(doc, ann.id)
        subtype = annot.type[1]

        if subtype == "Highlight":
            # A highlight stores its selection as corners, not as a box: the box
            # around two separate lines would say one band, so the corners are the
            # claim and the only numbers worth comparing.
            assert _numbers(element.get("coords")) == pytest.approx(
                _raw(doc, annot, "QuadPoints"), abs=TOLERANCE)
        else:
            if subtype == "Ink":
                strokes = [_numbers(gesture.text) for gesture in
                           element.findall(
                               f"{qname('inklist')}/{qname('gesture')}")]
                assert [value for stroke in strokes
                        for value in stroke] == pytest.approx(
                            _raw(doc, annot, "InkList"), abs=TOLERANCE)
            elif subtype in ("Line", "PolyLine", "Polygon"):
                if subtype == "Line":
                    written = _numbers(
                        f"{element.get('start')} {element.get('end')}")
                    held = "L"
                else:
                    written = _numbers(element.find(qname("vertices")).text)
                    held = "Vertices"
                assert written == pytest.approx(_raw(doc, annot, held),
                                                abs=TOLERANCE), ann
            assert _numbers(element.get("rect")) == pytest.approx(
                _raw(doc, annot, "Rect"), abs=TOLERANCE), subtype
        compared += 1
    assert compared == len(manager.get_annotations())


def test_a_mark_stays_where_it_was_when_the_page_is_turned(manager):
    """The view's rotation is not part of a mark's address.

    The numbers come from the page's unrotated box, so a comment a reader shows
    turned — or one PDFAR is showing turned — travels to the same words.
    """
    draw_every_kind(manager)
    straight = {element.get("name"): element.get("rect")
                for element in exported(manager)}

    manager.doc.load_page(0).set_rotation(90)
    added = manager.add_shape(0, "rect", rect=(20.0, 30.0, 60.0, 70.0))
    turned = {element.get("name"): element.get("rect")
              for element in exported(manager)}

    assert set(turned) == set(straight) | {added.id}
    assert [turned[name] for name in straight] == [straight[name]
                                                   for name in straight]
    _page, annot = find(manager.doc, added.id)
    assert _numbers(turned[added.id]) == pytest.approx(
        _raw(manager.doc, annot, "Rect"), abs=TOLERANCE)


def test_a_page_the_file_itself_turns_is_still_measured_unturned(tmp_path):
    """A mark's numbers belong to the unturned sheet, not to the angle shown.

    A scan often carries ``/Rotate`` in the file, and a reader draws it by turning
    the sheet rather than the mark — so the rectangle and the quadrilateral a mark
    stores are the page's own, measured as if it lay flat.  MuPDF reports them to
    PDFAR that way, and this is where that shows: the numbers out of the file and
    the numbers out of the export agree on a turned page, for a box and for a
    selection both.
    """
    made = over(build_rotated_page(tmp_path / "turned.pdf"))
    try:
        assert made.doc.load_page(0).rotation == 90
        added = made.add_shape(0, "rect", rect=(10.0, 20.0, 40.0, 90.0),
                               color="#FF0000")
        marked = made.add_highlight(0, [(5.0, 30.0, 60.0, 40.0)])

        _page, annot = find(made.doc, added.id)
        assert _numbers(one(made, "square").get("rect")) == pytest.approx(
            _raw(made.doc, annot, "Rect"), abs=TOLERANCE)
        _page, annot = find(made.doc, marked.id)
        assert _numbers(one(made, "highlight").get("coords")) == pytest.approx(
            _raw(made.doc, annot, "QuadPoints"), abs=TOLERANCE)
    finally:
        made.session.close()


def test_a_page_whose_box_starts_away_from_the_origin_is_the_mirror(tmp_path):
    """Flipped through the crop box, because the file measures from it.

    A page's visible box need not begin at its media box's origin — that is how a
    scan arrives, with its paste margins cut away — and the media box itself may
    sit away from zero.  Both are offset here, so neither box's numbers can pass
    for the other's: a flip through the wrong box puts every mark on the wrong
    part of the sheet.
    """
    cases = [("crop-only", None, CROP),
             ("media-and-crop", (10.0, 20.0, 210.0, 120.0),
              (46.0, 56.0, 196.0, 96.0))]
    for name, mediabox, crop in cases:
        made = over(build_offset_page(tmp_path / f"{name}.pdf",
                                      mediabox=mediabox, crop=crop))
        try:
            added = made.add_shape(0, "rect", rect=(10.0, 10.0, 60.0, 30.0),
                                   color="#FF0000")
            element = one(made, "square")
            _page, annot = find(made.doc, added.id)
            rect = _raw(made.doc, annot, "Rect")

            assert _numbers(element.get("rect")) == pytest.approx(
                rect, abs=TOLERANCE), name
            # The mark lies inside this page's own box and no wider, so a flip
            # through the media box's height of 100 is caught by these bounds.
            assert crop[0] - 2 <= rect[0] and rect[2] <= crop[2] + 2, name
            assert crop[1] - 2 <= rect[1] and rect[3] <= crop[3] + 2, name
        finally:
            made.session.close()


# ------------------------------------------------------- what each kind has to say
def test_what_a_mark_says_survives_the_characters_that_would_break_the_file(
        manager):
    """Angle brackets, an ampersand, quotes, a tab and both line endings."""
    note = 'He said "hi" & <b>left</b>\ttab\r\nnewlines — dash 中文'
    manager.add_note(0, (30.0, 30.0), note)

    assert one(manager, "text").find(qname("contents")).text == note


def test_a_measurement_reading_and_a_note_travel_as_the_mark_s_contents(manager):
    """The text a mark carries is the text that goes out, whatever the kind."""
    manager.add_highlight(0, [HIGHLIGHT], note="why this line")
    manager.add_shape(1, "rect", rect=(20.0, 30.0, 60.0, 70.0),
                      note="around this")
    manager.add_measurement(2, [(10.0, 30.0), (110.0, 30.0)], SCALE)

    assert one(manager, "highlight").find(qname("contents")).text == (
        "why this line")
    assert one(manager, "square").find(qname("contents")).text == (
        "around this")
    # A measurement with no note keeps the reading the file was given: the mark
    # spans 100 pt and this scale says 2.5 cm to the point.
    assert one(manager, "line").find(qname("contents")).text == "250.00 cm"


def test_an_arrow_is_a_line_that_says_where_its_head_is(manager):
    manager.add_shape(2, "arrow", points=[(10.0, 10.0), (60.0, 60.0)])
    manager.add_shape(2, "line", points=[(10.0, 80.0), (150.0, 90.0)])
    lines = [element for element in exported(manager)
             if element.get("start")]

    assert len(lines) == 2
    arrow = [element for element in lines if element.get("tail")]
    assert len(arrow) == 1
    assert arrow[0].get("head") == "None"
    assert arrow[0].get("tail") == "ClosedArrow"
    # A plain line says nothing about its ends, because it has no head to say.
    plain = [element for element in lines if element is not arrow[0]][0]
    assert plain.get("head") is None
    assert plain.get("tail") is None


def test_a_drawing_keeps_every_stroke_that_made_it(manager):
    manager.add_ink(1, [[(5.0, 5.0), (20.0, 20.0)], [(60.0, 20.0), (90.0, 40.0)]],
                    color="#00FF00", width=4)
    element = one(manager, "ink")
    gestures = element.findall(f"{qname('inklist')}/{qname('gesture')}")

    assert [gesture.text for gesture in gestures] == ["5,95;20,80", "60,80;90,60"]
    assert element.get("color") == "#00FF00"
    assert element.get("width") == "4"


def test_the_scale_of_a_measurement_travels_in_pdfar_s_own_namespace(manager):
    """The reading means nothing without the scale, and XFDF has no word for it.

    The number written here is the number the file's ``/Measure`` keeps, to the
    same six decimals, so the reading in the exchange file and the reading on the
    page are the same reading.
    """
    added = manager.add_measurement(0, [(20.0, 30.0), (120.0, 30.0)],
                                    MeasurementScale(25.4 / 72.0, "mm", 1))
    element = one(manager, "line")
    _page, annot = find(manager.doc, added.id)
    kind, measure = manager.doc.xref_get_key(annot.xref, "Measure/X")

    assert kind == "array"
    # The PDF writes a bare ``.352778`` where XML wants ``0.352778``; the number
    # is what has to agree, and to the precision the file itself keeps.
    assert float(element.get(pdfar("scale"))) == pytest.approx(
        float(_FACTOR.search(measure).group(1)), abs=5e-7)
    assert element.get("subject") == "Distance"
    # A name attribute carries the name, not the PDF slash it is written with.
    assert element.get("intent") == "LineDimension"
    assert element.get(pdfar("measure")) == "distance"
    assert element.get(pdfar("unit")) == "mm"
    assert element.get(pdfar("precision")) == "1"
    assert element.find(qname("contents")).text == added.reading
    assert added.reading == "35.3 mm"


def test_a_perimeter_and_an_area_keep_their_own_intent_and_geometry(manager):
    manager.add_measurement(1, [(20.0, 30.0), (60.0, 30.0), (60.0, 80.0)],
                            SCALE, kind="perimeter")
    manager.add_measurement(2, [(20.0, 10.0), (120.0, 10.0), (120.0, 60.0)],
                            SCALE, kind="area")

    polyline = one(manager, "polyline")
    assert polyline.get("intent") == "PolyLineDimension"
    assert polyline.get("subject") == "Perimeter"
    assert _numbers(polyline.find(qname("vertices")).text) == [
        20, 70, 60, 70, 60, 20]
    area = one(manager, "polygon")
    assert area.get("intent") == "PolygonDimension"
    assert area.get(pdfar("measure")) == "area"
    assert area.get("width") == "1"


def test_a_standard_stamp_travels_by_name_and_a_custom_one_by_its_text(manager):
    """The fourteen names are the mark; PDFAR's own picture cannot travel."""
    manager.add_stamp(0, "TopSecret", center=(100.0, 50.0))
    manager.add_custom_stamp(1, (100.0, 85.0), caption="Verified")
    stamps = {element.get("icon"): element for element in exported(manager)
              if element.tag == qname("stamp")}

    assert set(stamps) == {"TopSecret", "Custom"}
    assert _numbers(stamps["TopSecret"].get("rect")) == pytest.approx(
        [40, 34.2105, 160, 65.7895], abs=TOLERANCE)
    assert stamps["Custom"].find(qname("contents")).text == "Verified"


def test_a_note_is_the_classic_icon_and_holds_its_text_without_a_colour(
        manager):
    """PDFAR writes no ``/C`` on a note, so the file says nothing about one."""
    manager.add_note(0, (30.0, 30.0), "sticky")
    element = one(manager, "text")

    assert element.get("icon") == "Note"
    assert element.get("color") is None
    assert element.get("opacity") is None


# -------------------------------------------------- what the file does not claim
def test_a_highlight_is_translucent_only_when_the_file_said_so(tmp_path):
    manager = over(build_inherited(tmp_path / "inherited.pdf"))
    try:
        inherited = [element for element in exported(manager)
                     if element.tag == qname("highlight")][0]
        assert inherited.get("opacity") is None
        assert inherited.get("coords")

        mine = over(build_sample_pdf(tmp_path / "ours.pdf", pages=1))
        try:
            mine.add_highlight(0, [HIGHLIGHT])
            assert one(mine, "highlight").get("opacity") == "0.4"
        finally:
            mine.session.close()
    finally:
        manager.session.close()


def test_the_author_and_dates_a_file_carries_travel_as_pdf_dates(tmp_path):
    """Verbatim, because XFDF dates are the PDF's own format.

    A mark PDFAR made carries no dates — none of the edits write ``/M`` — so it
    gets no date attribute rather than a timestamp made up at export, which would
    make the file differ every time it were written.
    """
    made = over(build_inherited(tmp_path / "inherited.pdf"))
    try:
        made.add_note(0, (150.0, 10.0), "ours")
        highlight = one(made, "highlight")

        assert highlight.get("title") == "Inherited"
        assert highlight.get("creationdate") == "D:20260101120000+01'00'"
        assert highlight.get("date") == "D:20260202130000-05'00'"
        assert highlight.find(qname("contents")).text == "kept"
        assert one(made, "text").get("date") is None
        assert one(made, "text").get("creationdate") is None
    finally:
        made.session.close()


def test_a_mark_pdfar_does_not_model_is_left_out_of_the_file(tmp_path):
    """Free text is a comment; PDFAR has no model for it and writes no claim.

    Leaving a mark out of an exchange file is a smaller loss than writing one it
    cannot describe, and the file says so by holding only what the panel lists.  A
    link is left out for the same reason and by the same route: MuPDF does not
    report links among a page's annotations at all.
    """
    made = over(build_inherited(tmp_path / "inherited.pdf"))
    try:
        kept = made.add_note(0, (150.0, 10.0), "ours")
        text = export_xfdf(made)

        assert "typed on the page" not in text
        assert "example.com" not in text
        assert [local(element) for element in exported(made)] \
            == ["square", "highlight", "text"]
        assert exported(made)[-1].get("name") == kept.id
        # The square keeps the colour and the width the file holds for it.
        square = one(made, "square")
        assert square.get("name") == "foreign-square"
        assert square.get("color") == "#0000FF"
        assert square.get("width") == "3"
    finally:
        made.session.close()


def test_a_mark_that_came_without_a_name_travels_without_one(tmp_path):
    """An object number is not an identity another copy of the file can use."""
    path = build_inherited(tmp_path / "inherited.pdf")
    doc = fitz.open(path)
    page = doc.load_page(0)
    unnamed = page.add_rect_annot(fitz.Rect(80.0, 20.0, 120.0, 50.0))
    unnamed.update()
    doc.xref_set_key(unnamed.xref, "NM", "null")
    doc.save(path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    made = over(path)
    try:
        listed = exported(made)
        nameless = [element for element in listed if element.get("name") is None]

        assert len(nameless) == 1
        assert len(listed) == 3
        assert all(element.get("name") is not None
                   for element in listed if element is not nameless[0])
    finally:
        made.session.close()


# ------------------------------------------------------------- what cannot be said
def test_a_coordinate_that_is_not_a_number_costs_the_mark_and_not_the_file():
    """One unusable value voids the geometry it belongs to, and that mark only."""
    anchor = (0.0, 100.0)
    broken = InkAnnotation(page=0, rects=[(0.0, 0.0, 10.0, 10.0)],
                           paths=[[(float("nan"), 1.0)]])
    whole = InkAnnotation(page=0, rects=[(0.0, 0.0, 10.0, 10.0)],
                          paths=[[(1.0, 2.0)]])
    assert xfdf._annotation(broken, anchor) is None
    assert "<gesture>1,98</gesture>" in xfdf._annotation(whole, anchor)

    assert xfdf._annotation(InkAnnotation(page=0, rects=[(0.0, 0.0, 10.0,
                                                          10.0)],
                                          paths=[]), anchor) is None
    assert xfdf._annotation(HighlightAnnotation(page=0, rects=[]),
                            anchor) is None
    assert xfdf._annotation(HighlightAnnotation(
        page=0, rects=[(0.0, 0.0, float("inf"), 10.0)]), anchor) is None


def test_text_that_cannot_exist_in_xml_is_dropped_rather_than_written():
    """A control character would make the whole file unparsable."""
    assert xfdf._text("a\x00b\x1fc\uffff d") == "abc d"
    assert xfdf._attribute('q "x" &\n<>\x01') == (
        'q &quot;x&quot; &amp;&#10;&lt;&gt;')
