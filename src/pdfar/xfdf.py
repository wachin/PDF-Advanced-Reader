"""XFDF: the document's comments as a file that can travel.

XFDF is Adobe's XML format for exchanging PDF annotations.  It is not a PDF and
does not change one: it is the list of marks — where each sits, what it looks
like, what it says — so another reader's comment system can be given PDFAR's
marks and PDFAR can be given another reader's.

This module writes it.  Three rules decide every value in the output.

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
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .annotations import (ANNOT_READ_ERRORS, CUSTOM_STAMP_NAME, Annotation,
                          AnnotationManager, HighlightAnnotation,
                          InkAnnotation, MEASURE_INTENTS, MEASURE_KIND_LABELS,
                          MeasurementAnnotation, NoteAnnotation,
                          ShapeAnnotation, StampAnnotation)

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
