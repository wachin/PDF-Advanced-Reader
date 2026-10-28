"""XFDF: the document's comments as a file that can travel.

XFDF is Adobe's XML format for exchanging PDF annotations.  It is not a PDF and
does not change one: it is the list of marks — where each sits, what it looks
like, what it says — so another reader's comment system can be given PDFAR's
marks and PDFAR can be given another reader's.

This module writes it, and reads it back.  Three rules decide every value that
goes out, and a fourth says what may come in.

**The numbers are the file's, not the view's.**  An element's ``rect`` is the
annotation's ``/Rect``, measured upwards from the bottom-left of the page's media
box; PDFAR's models — like its rendering, its selection and its tools — measure
downwards from the top-left of its visible area.  So every coordinate is flipped
on the way out through the page's own crop box: the crop box and not the media
box, because a page whose visible area starts at (36, 36) has its annotations
measured from there.  Written in page space instead, every mark in the file would
sit upside down for every reader but PDFAR.

**A mark travels as this reader shows it.**  The colour and the opacity that go
into the file are the ones the model reports, which is why they are written only
for the kinds whose colour PDFAR itself puts in the annotation: a note icon or a
standard stamp paints whatever its appearance says, and exporting the default
colour a model fills in for a mark that has none would tell another reader
something the file never did.

**What cannot be said is not pretended.**  A measurement keeps its scale in the
PDF's ``/Measure`` dictionary, for which XFDF has no word, so the scale is
written as attributes in a namespace of PDFAR's own: a reader that does not know
them keeps the geometry and the reading, and only an import that read the
namespace may trust the number.  An image stamp is the case that cannot travel —
its mark is a picture, not a name — so it goes out as a stamp carrying its note,
and the caption of a text stamp goes out as its note for the same reason: the
appearance is the thing an exchange file has no place for.

**A file is read as a plan, never as a document.**  Nothing here opens a PDF or
changes one: :func:`parse_xfdf` turns the text into the edits that would put its
marks on the page, against page anchors handed in and the names the document
already holds.  A file that is not XFDF, is too big or is built in a way no reader
should attempt is refused entire, before anything is applied; a mark inside it
that PDFAR cannot describe is left out and counted, because another reader's
comments are not required to be only the kinds this one has words for.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from uuid import uuid4

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .annotations import (ANNOT_READ_ERRORS, CUSTOM_STAMP_NAME,
                          MEASURE_KINDS, STAMP_VALUES,
                          AddCustomStamp, AddHighlight, AddInk,
                          AddImportedMarks, AddMeasurement,
                          AddNote, AddShape, AddStamp, Annotation,
                          AnnotationManager, HighlightAnnotation,
                          HighlightSpec, InkAnnotation, MEASURE_INTENTS,
                          MEASURE_KIND_LABELS, MeasurementAnnotation,
                          MeasurementScale, NoteAnnotation, ShapeAnnotation,
                          StampAnnotation, _checked_measurement_points,
                          _clean_shape, checked_measure_scale,
                          measurement_reading)
from .edits import Edit, EditError

#: XFDF's own namespace; a file without it is not XFDF.
XFDF_NAMESPACE = "http://ns.adobe.com/xfdf/"

#: The namespace PDFAR's extra attributes are bound to.  An attribute that is
#: not part of the format is dangerous precisely when it *looks* as if it is, so
#: a scale that XFDF cannot hold carries a prefix a conforming reader treats as
#: somebody else's business.
PDFAR_NAMESPACE = "urn:pdfar:xfdf:1"

HEADER = '<?xml version="1.0" encoding="UTF-8"?>'

#: How :func:`pdfar.annotations.annot_key` names an annotation that came in with
#: no ``/NM`` of its own.  The object number behind such an id means nothing in
#: another copy of the file, so the mark is exported nameless rather than given a
#: name that would point an import at whoever happens to hold that number.
_FOREIGN_PREFIX = "anon:"

#: The icon a sticky note gets.  PDFAR writes ``/Note``, the classic page.
NOTE_ICON = "Note"

#: The element each shape kind becomes.  An arrow is a ``line`` whose ends say
#: so, which is also how the file stores it.
SHAPE_TAGS = {"rect": "square", "ellipse": "circle", "line": "line",
              "arrow": "line", "polygon": "polygon"}

#: The element each measurement kind becomes — the subtype it is in the file: a
#: distance is a line, a perimeter an open polyline, an area a polygon.
MEASURE_TAGS = {"distance": "line", "perimeter": "polyline", "area": "polygon"}

#: How a line ending is spelled in XFDF, for the one PDFAR draws: an arrowhead at
#: the far end.
ARROW_TAIL = "ClosedArrow"

#: Whitespace and control characters, as the two tables that need them.  A parser
#: collapses a tab or a newline inside an *attribute value* to a single space, so
#: those become references to survive; in text a tab and a newline are legal and
#: are kept, while a carriage return would be folded into a newline, so only it
#: is written as a reference.  A control character that is not whitespace cannot
#: appear in XML at all and is dropped.
_ATTR_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
                 "\t": "&#9;", "\n": "&#10;", "\r": "&#13;"}
_TEXT_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", "\r": "&#13;"}


def _translate_table(escapes: Dict[str, str]) -> Dict[int, Optional[str]]:
    """The ``str.translate`` map that writes *escapes* and drops the rest."""
    table: Dict[int, Optional[str]] = {
        ord(char): text for char, text in escapes.items()}
    for code in [c for c in range(0x20) if c not in (0x09, 0x0A, 0x0D)] \
            + [0x7F, 0xFFFE, 0xFFFF]:
        table.setdefault(code, None)
    return table


_ATTRIBUTE_TABLE = _translate_table(_ATTR_ESCAPES)
_TEXT_TABLE = _translate_table(_TEXT_ESCAPES)


def _attribute(text: str) -> str:
    """*text* safe to sit between quotes."""
    return text.translate(_ATTRIBUTE_TABLE)


def _text(text: str) -> str:
    """*text* safe to sit between two elements."""
    return text.translate(_TEXT_TABLE)


# ---------------------------------------------------------------- coordinates
def _number(value: object, places: int = 4) -> Optional[str]:
    """A coordinate as XFDF writes one: fixed decimals, trailing zeros trimmed.

    ``None`` for anything that is not a finite number.  One value that is not a
    number makes the geometry it belongs to meaningless, so the caller drops the
    mark rather than writing a shape at ``nan``.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    text = f"{number:.{places}f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _numbers(values: Sequence[float]) -> Optional[str]:
    """A comma list, or ``None`` when any value voids the whole list."""
    parts = []
    for value in values:
        text = _number(value)
        if text is None:
            return None
        parts.append(text)
    return ",".join(parts) if parts else None


def _rect_of(rect: Sequence[float]) -> Optional[Tuple[float, float, float, float]]:
    """*rect* as four finite numbers, ordered, or ``None``."""
    if len(rect) < 4:
        return None
    values = [_number(value) for value in rect[:4]]
    if any(value is None for value in values):
        return None
    x0, y0, x1, y1 = (float(value) for value in values)
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def _pair(point: Sequence[float],
          anchor: Tuple[float, float]) -> Optional[str]:
    """One page-space point as the file holds it: ``x,y``."""
    if len(point) < 2:
        return None
    return _numbers([anchor[0] + point[0], anchor[1] - point[1]])


def _path(points: Sequence[tuple],
          anchor: Tuple[float, float]) -> Optional[str]:
    """``x,y;x,y;…`` — the way XFDF lists a run of points."""
    legs = []
    for point in points:
        text = _pair(point, anchor)
        if text is None:
            return None
        legs.append(text)
    return ";".join(legs) if legs else None


def _box(rects: Sequence[Sequence[float]],
         anchor: Tuple[float, float]) -> Optional[str]:
    """The union of page-space boxes, as the ``/Rect`` the file holds."""
    corners = [_rect_of(rect) for rect in rects]
    corners = [rect for rect in corners if rect is not None]
    if not corners:
        return None
    xs = [value for rect in corners for value in (rect[0], rect[2])]
    ys = [value for rect in corners for value in (rect[1], rect[3])]
    return _numbers([anchor[0] + min(xs), anchor[1] - max(ys),
                     anchor[0] + max(xs), anchor[1] - min(ys)])


def _quad(rect: Sequence[float],
          anchor: Tuple[float, float]) -> Optional[str]:
    """A highlighted line as the four corners ``/QuadPoints`` keeps.

    The order is the one the specification asks for — upper left, upper right,
    lower left, lower right, where "upper" is the larger y in the file's space —
    because a highlight stores its selection as a quadrilateral and not as a box,
    and corners in another order trace a bow tie instead of a line.
    """
    box = _rect_of(rect)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return _numbers([anchor[0] + x0, anchor[1] - y0,
                     anchor[0] + x1, anchor[1] - y0,
                     anchor[0] + x0, anchor[1] - y1,
                     anchor[0] + x1, anchor[1] - y1])


def _anchor(doc: fitz.Document, page_index: int,
            cache: Dict[int, Optional[Tuple[float, float]]]) -> Optional[tuple]:
    """The two numbers that place a mark on *page_index*.

    ``x`` is where the page's visible box begins and ``y`` is its top edge, which
    is also the height the y axis is flipped through — both in the file's own
    coordinates, measured up from the bottom-left of the page's media box.  A page
    that cannot be read gives ``None`` and its marks stay behind: a box guessed
    from the wrong page would put them somewhere the file does not say.

    PyMuPDF reports a page's boxes with y running down from the top of the media
    box, so the crop box's *height above the ground* has to be read back out of
    the media box's height: where its top edge lies in page space is how far the
    ground has to be raised to reach it.
    """
    if page_index not in cache:
        anchor: Optional[tuple] = None
        try:
            page = doc.load_page(page_index)
            cropbox, mediabox = page.cropbox, page.mediabox
            if cropbox.width > 0 and cropbox.height > 0:
                anchor = (cropbox.x0, mediabox.y1 - cropbox.y0)
        except ANNOT_READ_ERRORS:
            anchor = None
        cache[page_index] = anchor
    return cache[page_index]


# ------------------------------------------------------------------ elements
Attrs = List[Tuple[str, str]]


def _attrs(pairs: Attrs) -> str:
    return "".join(f' {name}="{_attribute(value)}"'
                   for name, value in pairs if value)


def _element(tag: str, attrs: Attrs, children: Sequence[str] = ()) -> str:
    """One element, on one line, the way a reader's parser expects them."""
    head = f"<{tag}{_attrs(attrs)}>"
    if not children:
        return f"{head}</{tag}>"
    return f"{head}{''.join(children)}</{tag}>"


def _identity(ann: Annotation, anchor: tuple) -> Attrs:
    """The attributes every mark shares: where it is, who put it, what it says."""
    attrs: Attrs = [("page", str(int(ann.page)))]
    rect = _box(ann.rects, anchor)
    if rect is not None:
        attrs.append(("rect", rect))
    if not ann.id.startswith(_FOREIGN_PREFIX):
        attrs.append(("name", ann.id))
    if ann.author:
        attrs.append(("title", ann.author))
    if ann.created:
        attrs.append(("creationdate", ann.created))
    if ann.modified:
        attrs.append(("date", ann.modified))
    return attrs


def _paint(ann: Annotation, attrs: Attrs) -> None:
    """Colour and solidity, for a mark whose colour the file carries.

    A mark with no ``/CA`` reports ``-1`` through the model and paints as its
    appearance says, which is why only a number below one becomes an
    ``opacity``: that attribute then means "this mark is see-through", which is
    what the file said, and nothing is written for a mark that was never
    translucent.
    """
    if ann.color:
        attrs.append(("color", ann.color))
    if math.isfinite(ann.opacity) and 0.0 <= ann.opacity < 1.0:
        attrs.append(("opacity", _number(ann.opacity) or ""))


def _width(width: Optional[float], attrs: Attrs) -> None:
    """The line thickness, for the marks that are drawn with one."""
    if width is None:
        return
    text = _number(width, 3)
    if text is not None:
        attrs.append(("width", text))


def _contents(note: str) -> List[str]:
    """The ``contents`` child, or none for a mark with nothing to say."""
    return [f"<contents>{_text(note)}</contents>"] if note else []


def _ends(points: Sequence[tuple], anchor: tuple) -> Attrs:
    """``start`` and ``end`` for a two-point mark, or nothing if it has none."""
    if len(points) != 2:
        return []
    start = _pair(points[0], anchor)
    end = _pair(points[1], anchor)
    if start is None or end is None:
        return []
    return [("start", start), ("end", end)]


def _highlight(ann: HighlightAnnotation, anchor: tuple) -> Optional[str]:
    attrs = _identity(ann, anchor)
    quads = [_quad(rect, anchor) for rect in ann.rects]
    if not quads or any(quad is None for quad in quads):
        return None
    attrs.append(("coords", ",".join(quad for quad in quads if quad)))
    _paint(ann, attrs)
    return _element("highlight", attrs, _contents(ann.content))


def _note(ann: NoteAnnotation, anchor: tuple) -> Optional[str]:
    attrs = _identity(ann, anchor)
    attrs.append(("icon", NOTE_ICON))
    return _element("text", attrs, _contents(ann.content))


def _ink(ann: InkAnnotation, anchor: tuple) -> Optional[str]:
    if not ann.paths:
        # An ink with no stroke is the broken annotation AddInk refuses, and one
        # unreadable path voids the drawing rather than the lines beside it.
        return None
    strokes = [_path(points, anchor) for points in ann.paths]
    if any(stroke is None for stroke in strokes):
        return None
    attrs = _identity(ann, anchor)
    _paint(ann, attrs)
    _width(ann.width, attrs)
    children = ["<inklist>"
                + "".join(f"<gesture>{stroke}</gesture>"
                          for stroke in strokes if stroke)
                + "</inklist>"]
    children += _contents(ann.content)
    return _element("ink", attrs, children)


def _shape(ann: ShapeAnnotation, anchor: tuple) -> Optional[str]:
    tag = SHAPE_TAGS.get(ann.kind)
    if tag is None:
        return None
    attrs = _identity(ann, anchor)
    children = _contents(ann.content)
    if tag == "line":
        ends = _ends(ann.points, anchor)
        if not ends:
            return None
        attrs += ends
        if ann.kind == "arrow":
            attrs.append(("head", "None"))
            attrs.append(("tail", ARROW_TAIL))
    elif tag == "polygon":
        if not ann.points:
            return None
        vertices = _path(ann.points, anchor)
        if vertices is None:
            return None
        children = [f"<vertices>{vertices}</vertices>"] + children
    _paint(ann, attrs)
    _width(ann.width, attrs)
    return _element(tag, attrs, children)


def _stamp(ann: StampAnnotation, anchor: tuple) -> Optional[str]:
    """A stamp, named by the appearance the file holds.

    The fourteen standard names travel exactly: a reader draws ``Approved`` from
    its own copy of the appearance.  ``Custom`` is PDFAR's own mark, whose picture
    cannot travel, so what comes across is that a stamp is there and what the user
    wrote on it or about it.
    """
    attrs = _identity(ann, anchor)
    attrs.append(("icon", ann.name or CUSTOM_STAMP_NAME))
    return _element("stamp", attrs, _contents(ann.content))


def _measurement(ann: MeasurementAnnotation, anchor: tuple) -> Optional[str]:
    """A dimension, with the scale that makes its number mean something.

    The geometry and the ``intent`` are XFDF's own words, so another reader sees
    the line or the polygon it is; the scale is not, and goes in PDFAR's namespace
    with the same six decimals the file's ``/Measure`` keeps, so the number the
    two of them print is the same number.
    """
    tag = MEASURE_TAGS.get(ann.kind)
    if tag is None:
        return None
    attrs = _identity(ann, anchor)
    children = _contents(ann.content)
    if tag == "line":
        ends = _ends(ann.points, anchor)
        if not ends:
            return None
        attrs += ends
    else:
        if not ann.points:
            return None
        vertices = _path(ann.points, anchor)
        if vertices is None:
            return None
        children = [f"<vertices>{vertices}</vertices>"] + children
    attrs.append(("subject", MEASURE_KIND_LABELS.get(ann.kind, ann.kind)))
    # A name attribute in XFDF carries the name, not the PDF slash it is written
    # with in the file.
    attrs.append(("intent", MEASURE_INTENTS.get(ann.kind, "").lstrip("/")))
    _paint(ann, attrs)
    _width(ann.width, attrs)
    attrs.append(("pdfar:measure", ann.kind))
    attrs.append(("pdfar:scale", _number(ann.scale.units_per_point, 6) or ""))
    attrs.append(("pdfar:unit", ann.scale.unit))
    attrs.append(("pdfar:precision", str(int(ann.scale.precision))))
    return _element(tag, attrs, children)


def _annotation(ann: Annotation, anchor: tuple) -> Optional[str]:
    """One model as one element, or ``None`` if it cannot be described."""
    if isinstance(ann, HighlightAnnotation):
        return _highlight(ann, anchor)
    if isinstance(ann, NoteAnnotation):
        return _note(ann, anchor)
    if isinstance(ann, InkAnnotation):
        return _ink(ann, anchor)
    if isinstance(ann, MeasurementAnnotation):
        return _measurement(ann, anchor)
    if isinstance(ann, ShapeAnnotation):
        return _shape(ann, anchor)
    if isinstance(ann, StampAnnotation):
        return _stamp(ann, anchor)
    return None


def export_xfdf(manager: AnnotationManager) -> str:
    """Every mark PDFAR lists in the comments panel, as an XFDF file.

    The list is the document's own: :class:`AnnotationManager` reads it out of the
    live ``fitz.Document``, so what is exported is what is on the page and not
    what the interface last remembered.  Marks of a kind PDFAR does not model — a
    free-text comment from another program, say — are not in that list, so they
    are not in the file either: an exchange that leaves a mark out is a smaller
    loss than one that writes a mark it cannot describe.

    No file name goes in the envelope.  An XFDF is applied to the document that is
    open, and a path a reader cannot resolve is a worse answer than no path.  A
    form-field container is left out for the same kind of reason: an empty
    ``<fields>`` could be read as instructions to clear every field in the file.
    """
    doc = manager.doc
    anchors: Dict[int, Optional[Tuple[float, float]]] = {}
    elements = []
    for ann in manager.get_annotations():
        anchor = _anchor(doc, int(ann.page), anchors)
        if anchor is None:
            continue
        element = _annotation(ann, anchor)
        if element is not None:
            elements.append(element)
    lines = [HEADER,
             f'<xfdf xmlns="{XFDF_NAMESPACE}" xmlns:pdfar="{PDFAR_NAMESPACE}"'
             ' xml:space="preserve">',
             "<annots>", *elements, "</annots>", "</xfdf>"]
    return "\n".join(lines) + "\n"


# ==================================================================== reading
# -------------------------------------------------------------------- bounds
#: How much text PDFAR will read.  The count happens before the parse because a
#: parser is the worst place to find out that a file is not what it said; here the
#: characters are still only characters.
MAX_INPUT_CHARS = 32_000_000

#: How deep tags may nest and how many of them a file may hold.  Twenty-four
#: levels is far above the four a real XFDF uses — ``xfdf``, ``annots``, a mark,
#: its ``contents`` — and both numbers are checked against the raw text, so a file
#: built to exhaust a reader is refused instead of attempted.
MAX_DEPTH = 24
MAX_ELEMENTS = 200_000

#: Marks a file may carry.  An import is one edit, and a file that claims a
#: hundred thousand of them is not a comment exchange about this document.
MAX_MARKS = 10_000


class XfdfError(Exception):
    """A file PDFAR will not read at all, so none of it is applied.

    Raised for a file that is not XFDF, is too big, or is built in a way a reader
    should not attempt.  A mark that merely cannot be described is not this
    error's business: it is left out and counted.
    """


class _Skip(Exception):
    """This element does not become a mark.

    Carries the reason the user is shown beside the element's own description, so
    "3 marks not taken" can also say which three and why.
    """


_TAG = re.compile(r"<[^>]*", re.S)
_BANNED = re.compile(r"<!\s*(doctype|entity)", re.I)
_SEPARATOR = re.compile(r"[,;\s]+")
_HEX = re.compile(r"#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})")
_NAME = re.compile(r"[A-Za-z0-9._:-]+")

#: The shape elements and the kind PDFAR draws each as.  ``polyline`` is not in
#: the table because an open path is none of PDFAR's five shapes: closing it would
#: invent the edge the file did not draw, and leaving it open needs a shape kind
#: the model does not have.
_SHAPE_KINDS = {"square": "rect", "circle": "ellipse", "line": "line",
                "polygon": "polygon"}


def _guard(text: str) -> None:
    """Refuse a file that is too big, too deep, or carries definitions.

    A bound rather than a parse: tags are counted as they are found without
    deciding what they mean — an instruction or a comment is neither opened nor
    closed, a closing tag backs the count out, a self-closing tag does nothing.
    What the scan exists for are the two things no reader should be allowed to
    reach.  A document type declaration can define an entity that expands until
    the machine stops, and a nesting depth past what the format uses kills a
    parser that recurses through it rather than reporting the file.

    The parse itself is left to Python's XML reader, which fetches nothing outside
    the document and refuses an entity no definition inside it provides; that is
    why the definitions are what this function denies, and why a file whose marks
    expand into each other never reaches a tree at all.
    """
    if len(text) > MAX_INPUT_CHARS:
        raise XfdfError("That file is too large to read.")
    if _BANNED.search(text):
        raise XfdfError("That file declares document types or entities, "
                        "which PDFAR does not read.")
    depth = 0
    elements = 0
    for match in _TAG.finditer(text):
        tag = match.group(0)[1:]
        if tag.startswith(("?", "!")):
            continue
        if tag.startswith("/"):
            depth -= 1
            continue
        elements += 1
        if elements > MAX_ELEMENTS:
            raise XfdfError("That file holds too many elements to read.")
        if tag.endswith("/"):
            continue
        depth += 1
        if depth > MAX_DEPTH:
            raise XfdfError("That file nests its marks too deeply to read.")


# -------------------------------------------------------------------- reading
def _local(element: ET.Element) -> str:
    """A tag without its namespace: ``{http://…xfdf/}highlight`` is a highlight.

    A namespace qualifies a name, it does not change its meaning, and a file that
    writes its marks inside the one XFDF defines should not be read differently
    for doing it properly.
    """
    return element.tag.rpartition("}")[2]


def _namespace(tag: str) -> str:
    """The namespace a tag carries, or ``""`` for a file that declares none."""
    head, brace, _rest = tag.partition("}")
    return head[1:] if brace and head.startswith("{") else ""


def _child(element: ET.Element, name: str) -> Optional[ET.Element]:
    """The first child called *name*, whichever namespace it came in."""
    for child in element:
        if _local(child) == name:
            return child
    return None


def _children(element: ET.Element, name: str) -> List[ET.Element]:
    return [child for child in element if _local(child) == name]


def _extension(element: ET.Element, name: str) -> str:
    """One of PDFAR's own attributes, in PDFAR's own namespace only.

    An unprefixed ``measure`` is some other vendor's attribute.  Trusting it would
    be the mirror image of the mistake the export guards: it would read a number
    as a scale when no namespace ever promised that it was one.
    """
    return (element.get(f"{{{PDFAR_NAMESPACE}}}{name}") or "").strip()


def _split_numbers(text: str) -> Optional[List[float]]:
    """The numbers in *text*, separated the way XFDF separates them.

    ``None`` when a value in the list is not a number: one bad number makes the
    geometry it belongs to meaningless, and a mark placed at a guess is a mark in
    the wrong place.
    """
    values: List[float] = []
    for part in _SEPARATOR.split(text.strip()):
        if not part:
            continue
        try:
            value = float(part)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        values.append(value)
    return values or None


def _list(element: ET.Element, name: str) -> Optional[List[float]]:
    """A number list held in an attribute: ``rect``, ``coords``, ``start``."""
    return _split_numbers(element.get(name) or "")


def _number_in(text: Optional[str]) -> Optional[float]:
    """One number, or ``None`` for anything that is not a finite one."""
    try:
        value = float((text or "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _color(text: Optional[str]) -> str:
    """A colour as the model holds it: ``#RRGGBB`` in upper case, or ``""``."""
    match = _HEX.fullmatch((text or "").strip())
    if match is None:
        return ""
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(digit + digit for digit in digits)
    return "#" + digits.upper()


def _opacity(text: Optional[str]) -> Optional[float]:
    """How solid a mark is — only for a file that says between nothing and all."""
    value = _number_in(text)
    return value if value is not None and 0.0 <= value <= 1.0 else None


def _thickness(text: Optional[str]) -> Optional[float]:
    """A line thickness, or ``None``: only a positive one draws anything."""
    value = _number_in(text)
    return value if value is not None and value > 0.0 else None


def _contents_of(element: ET.Element) -> str:
    """The note a mark carries, exactly as the file wrote it.

    Not stripped and not folded: XFDF says the whitespace inside ``contents`` is
    part of the text, which is why the export puts ``xml:space="preserve"`` on the
    document, and a reader that honoured that deserves the same honour.
    """
    child = _child(element, "contents")
    if child is None:
        return ""
    return child.text or ""


# ------------------------------------------------------------------ geometry
def _point_at(numbers: Optional[Sequence[float]], index: int,
              anchor: Tuple[float, float]) -> Optional[tuple]:
    """The *index*-th point of *numbers*, turned back into page space.

    The export's flip is its own inverse: x measured from the crop box's left, y
    counted down from the top edge the anchor names.  A point off the end of the
    list is no point at all, which is how a truncated ``coords`` is refused.
    """
    if numbers is None or index + 1 >= len(numbers):
        return None
    return (numbers[index] - anchor[0], anchor[1] - numbers[index + 1])


def _corner(a: tuple, b: tuple) -> tuple:
    """Two opposite corners of a box, ordered the way the model orders them."""
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _page_point(numbers: Optional[Sequence[float]],
                anchor: Tuple[float, float]) -> Optional[tuple]:
    """A lone ``x,y`` pair from the file as a page-space point."""
    if numbers is None or len(numbers) != 2:
        return None
    return _point_at(numbers, 0, anchor)


def _page_points(numbers: Optional[Sequence[float]],
                 anchor: Tuple[float, float]) -> Optional[List[tuple]]:
    """A whole list of ``x,y`` pairs as page-space points."""
    if numbers is None or len(numbers) % 2:
        return None
    return [(x - anchor[0], anchor[1] - y)
            for x, y in zip(numbers[0::2], numbers[1::2])]


def _page_box(numbers: Optional[Sequence[float]],
              anchor: Tuple[float, float]) -> Optional[tuple]:
    """A ``rect`` from the file as a box in page space."""
    if numbers is None or len(numbers) != 4:
        return None
    return _corner((numbers[0] - anchor[0], anchor[1] - numbers[1]),
                   (numbers[2] - anchor[0], anchor[1] - numbers[3]))


def _page_quads(numbers: Optional[Sequence[float]],
                anchor: Tuple[float, float]) -> Optional[List[tuple]]:
    """A ``coords`` list as one box per highlighted line.

    Eight numbers to a quadrilateral, in the order the specification asks for —
    upper left, upper right, lower left, lower right.  All four corners are boxed
    instead of pairing two of them: what PDFAR stores for a selection is the
    rectangle around it, a quad drawn skewed has no opposite corners to pair, and
    the union is the box the export started from either way.
    """
    if numbers is None or len(numbers) % 8:
        return None
    boxes = []
    for start in range(0, len(numbers), 8):
        corners = [_point_at(numbers, start + offset, anchor)
                   for offset in (0, 2, 4, 6)]
        found = [corner for corner in corners if corner is not None]
        if len(found) != 4:
            return None
        xs = [corner[0] for corner in found]
        ys = [corner[1] for corner in found]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
    return boxes or None


def _strokes(element: ET.Element) -> List[Optional[List[float]]]:
    """The number lists of an ``ink`` mark's gestures.

    Adobe writes the numbers as a ``gesture``'s content, and nests the gestures in
    an ``inklist``; a file that hands them straight to the mark, or holds them in
    the ``vertices`` attribute the format uses for a polygon elsewhere, says the
    same drawing in a punctuation another writer chose.  Either is read, because
    losing a sketch over how its pen was lifted is a worse reading than taking it.
    """
    found: List[ET.Element] = []
    for child in element:
        name = _local(child)
        if name == "gesture":
            found.append(child)
        elif name == "inklist":
            found.extend(_children(child, "gesture"))
    lists = []
    for gesture in found:
        numbers = _split_numbers(gesture.text or "")
        lists.append(numbers if numbers is not None
                     else _list(gesture, "vertices"))
    return lists


def _ends_points(element: ET.Element,
                 anchor: Tuple[float, float]) -> Optional[List[tuple]]:
    """A ``line`` mark's two ends."""
    start = _page_point(_list(element, "start"), anchor)
    end = _page_point(_list(element, "end"), anchor)
    return [start, end] if start is not None and end is not None else None


# ------------------------------------------------------------------- identity
def _claim(name: str, taken: Set[str]) -> Optional[str]:
    """The ``/NM`` a mark may arrive with, or ``None`` if it may not arrive.

    A name is an identity, and two marks with one name are one mark to PDFAR: the
    panel lists them together and one delete takes both.  So a name the document
    already holds claims no new mark, and a name a file repeats claims nothing the
    second time either — the first copy wins, which is also what makes importing
    the same file twice leave the document as the first import left it.

    ``""`` says the mark has no usable name and gets an id of its own.  Usable
    means the characters a PDF literal string can hold, because that is where the
    id goes: :func:`pdfar.annotations._name` writes it between parentheses without
    escaping, and a ``)`` in another reader's name would end the string and break
    the object that holds every mark in the file.
    """
    if not name or len(name) > 128 or not _NAME.fullmatch(name):
        return ""
    if name in taken:
        return None
    taken.add(name)
    return name


@dataclass(frozen=True)
class _Arrival:
    """What every kind of mark has in common: where, who, what it says."""

    page: int
    name: str
    author: str = ""
    note: str = ""
    color: str = ""
    width: Optional[float] = None
    opacity: Optional[float] = None


class _Reader:
    """The state a file is read against: the pages it may point at, the names taken."""

    def __init__(self, anchors: Sequence[Optional[Tuple[float, float]]],
                 taken: Iterable[str]):
        self.anchors = list(anchors)
        self.claimed: Set[str] = set(taken)

    def anchor(self, element: ET.Element) -> Tuple[int, tuple]:
        """The page a mark names, and the anchor that turns its numbers.

        A page outside the document is not a mark, and neither is one this reader
        cannot measure: putting a mark by a page whose crop box is unknown would
        place it where the file did not say.
        """
        page = _number_in(element.get("page"))
        if page is None or page != int(page):
            raise _Skip("does not say which page it is on")
        index = int(page)
        if not 0 <= index < len(self.anchors):
            raise _Skip("names a page this document does not have")
        anchor = self.anchors[index]
        if anchor is None:
            raise _Skip("names a page this document cannot read")
        return index, anchor


def _arrival(reader: _Reader,
             element: ET.Element) -> Tuple[_Arrival, tuple]:
    """The attributes every mark shares, in the model's own shapes."""
    page, anchor = reader.anchor(element)
    name = _claim((element.get("name") or "").strip(), reader.claimed)
    if name is None:
        raise _Skip("carries a name the document already holds")
    return (_Arrival(page=page,
                     name=name or str(uuid4()),
                     author=(element.get("title") or "").strip(),
                     note=_contents_of(element),
                     color=_color(element.get("color")),
                     width=_thickness(element.get("width")),
                     opacity=_opacity(element.get("opacity"))),
            anchor)


def _stroke_paint(arrival: _Arrival) -> Dict[str, Any]:
    """Colour and thickness for a mark drawn with a pen.

    Only what the file said is passed.  A mark the file describes without a colour
    then arrives in the colour PDFAR's own tools use, which is honest about the one
    thing the exchange did not state.
    """
    paint: Dict[str, Any] = {}
    if arrival.color:
        paint["color"] = arrival.color
    if arrival.width is not None:
        paint["width"] = arrival.width
    return paint


def _selection_paint(arrival: _Arrival) -> Dict[str, Any]:
    """Colour and solidity for a mark laid over text."""
    paint: Dict[str, Any] = {}
    if arrival.color:
        paint["color"] = arrival.color
    if arrival.opacity is not None:
        paint["opacity"] = arrival.opacity
    return paint


# ------------------------------------------------------------------- builders
def _read_highlight(reader: _Reader, element: ET.Element) -> Edit:
    arrival, anchor = _arrival(reader, element)
    coords = _list(element, "coords")
    if coords is None:
        # A reader that wrote no quadrilaterals still said where the mark is, and
        # the box it names is the best there is to work from: one line over the
        # whole selection beats refusing a mark for geometry the file lacks.
        box = _page_box(_list(element, "rect"), anchor)
        boxes = [box] if box else []
    else:
        # Numbers that are there and do not add up to quads are not a file saying
        # less: they are a file contradicting itself, and the box it also carries
        # would merge every line of the selection into one mark wider than any of
        # them.
        boxes = _page_quads(coords, anchor) or []
        if not boxes:
            raise _Skip("writes its selection in pieces that do not make "
                        "quadrilaterals")
    if not boxes:
        raise _Skip("does not say what it highlights")
    return AddHighlight(
        specs=(HighlightSpec(ann_id=arrival.name, page=arrival.page,
                             rects=tuple(boxes)),),
        author=arrival.author, note=arrival.note, **_selection_paint(arrival))


def _read_note(reader: _Reader, element: ET.Element) -> Edit:
    arrival, anchor = _arrival(reader, element)
    box = _page_box(_list(element, "rect"), anchor)
    if box is None:
        raise _Skip("does not say where it sits")
    # The icon is the mark and its top-left corner is where a click lands.  The
    # file's ``icon`` is not read: PDFAR has one note shape, and another reader's
    # preference for a key or a flag is not a different kind of comment.
    return AddNote(ann_id=arrival.name, page=arrival.page,
                   position=(box[0], box[1]), note=arrival.note,
                   author=arrival.author)


def _read_ink(reader: _Reader, element: ET.Element) -> Edit:
    arrival, anchor = _arrival(reader, element)
    strokes = []
    for numbers in _strokes(element):
        points = _page_points(numbers, anchor)
        if points and len(points) > 1:
            strokes.append(tuple(points))
    if not strokes:
        raise _Skip("has no stroke in it")
    return AddInk(ann_id=arrival.name, page=arrival.page, paths=tuple(strokes),
                  author=arrival.author, note=arrival.note,
                  **_stroke_paint(arrival))


def _read_shape(reader: _Reader, element: ET.Element) -> Edit:
    """A ``square``, ``circle``, ``line`` or ``polygon`` as PDFAR draws it.

    A box is taken the size the file states it, which for a square or a circle is
    a point wider than the box PDFAR first drew: a shape's ``/Rect`` holds the room
    its stroke needs, and a mark read back is measured from there.  Nothing is
    subtracted for it, because that room is MuPDF's own and another reader's is
    another matter — believing the file is the one rule that works for both.
    """
    arrival, anchor = _arrival(reader, element)
    tag = _local(element)
    box: Optional[tuple] = None
    points: Tuple[tuple, ...] = ()
    if tag == "line":
        ends = _ends_points(element, anchor)
        if ends is None:
            raise _Skip("does not say where its ends are")
        # Only the arrowhead PDFAR writes is read back as an arrow.  A mark with
        # its head on the other end keeps its geometry and loses the decoration,
        # because an arrowhead moved to the far end would point somewhere the
        # file did not draw it.
        kind = ("arrow" if (element.get("tail") or "").strip() == ARROW_TAIL
                else "line")
        points = tuple(ends)
    elif tag in ("square", "circle"):
        kind = _SHAPE_KINDS[tag]
        box = _page_box(_list(element, "rect"), anchor)
        if box is None:
            raise _Skip("does not say where it sits")
    else:
        kind = "polygon"
        vertices = _child(element, "vertices")
        found = _page_points(_split_numbers(vertices.text or "")
                             if vertices is not None else None, anchor)
        if not found:
            raise _Skip("does not say what it encloses")
        points = tuple(found)
    try:
        geometry = _clean_shape(kind, box, points)
    except EditError as exc:
        raise _Skip(str(exc)) from None
    if kind in ("rect", "ellipse"):
        return AddShape(ann_id=arrival.name, page=arrival.page, kind=kind,
                        rect=geometry, author=arrival.author,
                        note=arrival.note, **_stroke_paint(arrival))
    return AddShape(ann_id=arrival.name, page=arrival.page, kind=kind,
                    points=geometry, author=arrival.author,
                    note=arrival.note, **_stroke_paint(arrival))


def _read_stamp(reader: _Reader, element: ET.Element) -> Edit:
    """A stamp, taken by the appearance the file says it has.

    The fourteen standard names arrive exactly: a reader draws ``Approved`` from
    its own copy of the appearance, so the name is the whole mark.  ``Custom`` is
    PDFAR's own, whose picture did not travel — what comes back is the word the
    export carried, stamped again.  Anything else is another reader's art, and a
    box of the wrong shape would claim to be it.
    """
    arrival, anchor = _arrival(reader, element)
    box = _page_box(_list(element, "rect"), anchor)
    if box is None:
        raise _Skip("does not say where it sits")
    # Where the mark sits is read as where it was clicked: its size is PDFAR's
    # own, since the appearance the file describes is not in the exchange.
    center = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    icon = (element.get("icon") or "").strip()
    if icon in STAMP_VALUES:
        return AddStamp(ann_id=arrival.name, page=arrival.page, name=icon,
                        center=center, author=arrival.author,
                        note=arrival.note)
    if icon == CUSTOM_STAMP_NAME:
        if not arrival.note:
            raise _Skip("is a stamp whose picture cannot travel")
        return AddCustomStamp(ann_id=arrival.name, page=arrival.page,
                              caption=arrival.note, center=center,
                              author=arrival.author)
    raise _Skip(f"is a “{icon or 'unnamed'}” stamp, "
                "which is not one PDFAR draws")


def _scale_of(element: ET.Element) -> MeasurementScale:
    """The scale in PDFAR's own attributes, or a mark that is not a measurement.

    XFDF has no word for the ``/Measure`` dictionary, which is why the export
    invented these three attributes.  A file with the geometry and not the scale
    cannot be given a number, and printing one from PDFAR's default would tell the
    user a distance that no reader ever stated.
    """
    factor = _number_in(_extension(element, "scale") or None)
    unit = _extension(element, "unit")
    precision = _extension(element, "precision")
    if factor is None or not unit or not precision.isdigit():
        raise _Skip("is a measurement whose scale the file does not carry")
    return MeasurementScale(factor, unit, int(precision))


def _read_measurement(reader: _Reader, element: ET.Element,
                      kind: str) -> Edit:
    """A dimension, with the scale that makes its number mean something."""
    if MEASURE_TAGS.get(kind) != _local(element):
        raise _Skip("measures a shape PDFAR does not measure that way")
    arrival, anchor = _arrival(reader, element)
    if kind == "distance":
        points = _ends_points(element, anchor)
    else:
        vertices = _child(element, "vertices")
        points = _page_points(_split_numbers(vertices.text or "")
                              if vertices is not None else None, anchor)
    if not points:
        raise _Skip("does not say what it measures")
    scale = _scale_of(element)
    try:
        checked = _checked_measurement_points(kind, points)
        scale = checked_measure_scale(scale)
    except EditError as exc:
        raise _Skip(str(exc)) from None
    # A caption copied out of the geometry says nothing the edit will not write
    # again, so it is dropped and the mark's own reading stands: that is how a
    # measure comes back with the number it went out with, and still reads
    # correctly after the user drags one of its points.
    note = arrival.note
    if note == measurement_reading(kind, checked, scale):
        note = ""
    return AddMeasurement(ann_id=arrival.name, page=arrival.page,
                          points=tuple(checked), kind=kind,
                          units_per_point=scale.units_per_point,
                          unit=scale.unit, precision=scale.precision,
                          author=arrival.author, note=note,
                          **_stroke_paint(arrival))


def _build(reader: _Reader, element: ET.Element) -> Edit:
    """One element as the edit that would put its mark on the page."""
    tag = _local(element)
    kind = _extension(element, "measure")
    if kind:
        if kind not in MEASURE_KINDS:
            raise _Skip(f"is measured as “{kind}”, which PDFAR does not measure")
        return _read_measurement(reader, element, kind)
    if tag == "highlight":
        return _read_highlight(reader, element)
    if tag == "text":
        return _read_note(reader, element)
    if tag == "ink":
        return _read_ink(reader, element)
    if tag == "stamp":
        return _read_stamp(reader, element)
    if tag == "polyline":
        raise _Skip("draws an open path, which PDFAR has no shape for")
    if tag in _SHAPE_KINDS:
        return _read_shape(reader, element)
    raise _Skip("is not a kind of mark PDFAR can draw")


def _describe(tag: str, element: ET.Element) -> str:
    """How a mark that was not taken is pointed out."""
    page = _number_in(element.get("page"))
    if page is None or page != int(page):
        return tag
    return f"{tag} on page {int(page) + 1}"


# ---------------------------------------------------------------------- plan
@dataclass(frozen=True)
class ImportPlan:
    """The edits a file asks for, and the marks it could not deliver.

    Nothing here has touched a document: the plan is the whole of the decision, so
    the code that applies it has nothing left to interpret and nothing to refuse
    for.  :attr:`edit` is that code's one call — the whole plan as the single
    undoable step an import is.
    """

    edits: Tuple[Edit, ...] = ()
    skipped: Tuple[Tuple[str, str], ...] = ()

    @property
    def count(self) -> int:
        return len(self.edits)

    @property
    def edit(self) -> Edit:
        """Every mark of the file as one edit: all of it, or none of it."""
        return AddImportedMarks(marks=self.edits)

    def summary(self) -> str:
        """What the file gave, and what it did not, for the user to be told."""
        taken = f"{self.count} mark" + ("" if self.count == 1 else "s")
        if not self.skipped:
            return taken
        return f"{taken}, {len(self.skipped)} not taken"


def page_anchors(doc: fitz.Document) -> List[Optional[Tuple[float, float]]]:
    """Where each page's numbers begin, by the rule the export uses.

    One entry per page, ``None`` for a page that cannot be read.  Import and
    export have to flip the y axis through the same line or a mark that goes out
    and comes back has moved, so the anchor is not worked out a second time: this
    is :func:`_anchor` over every page of the document.
    """
    cache: Dict[int, Optional[Tuple[float, float]]] = {}
    return [_anchor(doc, index, cache) for index in range(doc.page_count)]


def parse_xfdf(text: str, anchors: Sequence[Optional[Tuple[float, float]]],
               taken: Iterable[str] = ()) -> ImportPlan:
    """An XFDF file as the edits that would put its marks on the page.

    A pure function of the text, the per-page anchors and the names the document
    already holds: no document is opened, no annotation is read, nothing is
    written.  Every number is turned into page space here, every value PDFAR
    cannot store is refused or left out here, and what comes back can be judged
    before a page changes under it.

    :class:`XfdfError` is the whole of the refusal — a file that is not XFDF, is
    too big, or is built in a way no reader should attempt, none of it applied.
    Everything else is read one element at a time, and an element that cannot
    become a mark is left out with a reason rather than condemning the file: a
    document type PDFAR does not model is somebody else's comment, not a corrupt
    byte.  Marks the file repeats, or names a mark the document already has, are
    left out too, so an import twice is the import once.
    """
    _guard(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise XfdfError(f"That file is not XML PDFAR can read: {exc}") from exc
    if _local(root) != "xfdf":
        raise XfdfError("That file has no xfdf element, so it is not XFDF.")
    namespace = _namespace(root.tag)
    if namespace and namespace != XFDF_NAMESPACE:
        raise XfdfError("That file is not XFDF: it says otherwise in its "
                        f"namespace, “{namespace}”.")
    annots = _child(root, "annots")
    if annots is None:
        return ImportPlan()
    reader = _Reader(anchors, taken)
    edits: List[Edit] = []
    skipped: List[Tuple[str, str]] = []
    for element in annots:
        if len(edits) >= MAX_MARKS:
            raise XfdfError("That file carries more marks than PDFAR will take "
                            "in one edit.")
        try:
            edits.append(_build(reader, element))
        except _Skip as skip:
            skipped.append((_describe(_local(element), element), str(skip)))
    return ImportPlan(edits=tuple(edits), skipped=tuple(skipped))
