"""Annotation system for PDFAR.

The document holds the annotations (F10-3): a highlight is a PDF ``/Highlight``
annotation, a sticky note is a ``/Text`` one, a freehand drawing is an ``/Ink``
one, and every change to any of them is a
:class:`pdfar.edits.Edit`.  That is what makes a highlight undoable, what makes it
mark the document dirty, and what makes a background reader see it without a save
in between.  ``AnnotationManager`` is a *view* of the document: it re-reads the
list after a change rather than keeping a pending one that Save would have to
push in.

An annotation's id is its ``/NM`` name, so a sidebar row, a model key and a PDF
object all mean the same thing across an undo, a save and a reopen.
"""

from __future__ import annotations

import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QObject, pyqtSignal

from .edits import Edit, EditError, Scope
from .save import SaveError, save_document
from .session import DocumentSession

#: How solid a highlight paints.  Stored as the annotation's ``/CA``, so a model
#: read back out of a file reports what was written into it.
HIGHLIGHT_OPACITY = 0.4

#: How big MuPDF makes a note icon.  Used only to keep an icon inside the page.
NOTE_ICON_SIZE = 16.0

#: How thick a freehand stroke is by default, in PDF points.  Written into the
#: annotation's ``/BS``, so a drawing read back out of a file reports the width
#: it was drawn with rather than the viewer's.
INK_WIDTH = 2.0

#: The pen's default colour.  Ink is drawn to be read on the page, so unlike a
#: highlight — which sits *under* the text — it starts out opaque and dark.
INK_COLOR = "#000000"

#: The pen's palette.  These are readable inks rather than the highlight
#: pastels: a stroke is meant to be looked at, not looked through.
INK_COLORS = [
    ("Black", INK_COLOR),
    ("Red", "#D32F2F"),
    ("Blue", "#1565C0"),
    ("Green", "#2E7D32"),
    ("Orange", "#EF6C00"),
    ("Purple", "#6A1B9A"),
]

#: The thicknesses the pen offers, in PDF points.
INK_WIDTHS = (1.0, 2.0, 4.0, 8.0)

#: The shapes PDFAR writes, and the undo labels they use.  ``arrow`` is a
#: ``/Line`` with a closed arrowhead rather than a subtype of its own.
SHAPE_LABELS = {
    "rect": "Rectangle",
    "ellipse": "Ellipse",
    "line": "Line",
    "arrow": "Arrow",
    "polygon": "Polygon",
}

#: The fourteen rubber stamps the PDF specification defines, in its order:
#: the ``/Name`` the file keeps, the label the menu shows, MuPDF's constant.
#: The appearance is drawn from the name, so the name is the whole mark.
STAMP_NAMES = (
    ("Approved", "Approved", fitz.STAMP_Approved),
    ("Experimental", "Experimental", fitz.STAMP_Experimental),
    ("NotApproved", "Not Approved", fitz.STAMP_NotApproved),
    ("AsIs", "As Is", fitz.STAMP_AsIs),
    ("Expired", "Expired", fitz.STAMP_Expired),
    ("NotForPublicRelease", "Not For Public Release",
     fitz.STAMP_NotForPublicRelease),
    ("Confidential", "Confidential", fitz.STAMP_Confidential),
    ("Final", "Final", fitz.STAMP_Final),
    ("Sold", "Sold", fitz.STAMP_Sold),
    ("Departmental", "Departmental", fitz.STAMP_Departmental),
    ("ForComment", "For Comment", fitz.STAMP_ForComment),
    ("TopSecret", "Top Secret", fitz.STAMP_TopSecret),
    ("Draft", "Draft", fitz.STAMP_Draft),
    ("ForPublicRelease", "For Public Release", fitz.STAMP_ForPublicRelease),
)
STAMP_LABELS = {key: label for key, label, _value in STAMP_NAMES}
STAMP_VALUES = {key: value for key, _label, value in STAMP_NAMES}

#: The ``/Name`` a stamp of PDFAR's own making carries.  Its appearance is the
#: ``/AP/N`` stream the edit writes, not one MuPDF draws from a standard name.
CUSTOM_STAMP_NAME = "Custom"

#: How a text stamp is drawn: Helvetica-Bold, red like the standard ones, one
#: line with a box around it.  The font shrinks to fit a narrow page.
STAMP_CAPTION_FONT = 18.0
STAMP_CAPTION_MIN_FONT = 8.0
STAMP_CAPTION_PADDING = 10.0

#: A stamp's natural width and the width:height ratio of MuPDF's appearance.
#: ``add_stamp_annot`` fits its box to that ratio whatever rectangle it is
#: given, so :func:`stamp_box` asks for the same shape and gets what it clicked.
STAMP_WIDTH = 190.0
STAMP_ASPECT = 3.8

#: What a dimension line is drawn with: red, one point thick, with end ticks
#: and the reading above it.  The caption is written into the appearance
#: stream, because MuPDF draws a measurement line as a plain line.
MEASURE_COLOR = "#D32F2F"
MEASURE_WIDTH = 1.0
MEASURE_FONT = 9.0
MEASURE_TICK = 4.0
MEASURE_LABEL_GAP = 4.0

#: What a damaged annotation can raise while the list is being read.  MuPDF
#: signals its failures through these; asking for less than ``Exception`` means a
#: mistake in :func:`_highlight_model` still surfaces instead of quietly
#: emptying the sidebar.
ANNOT_READ_ERRORS = (RuntimeError, fitz.mupdf.FzErrorBase)


class AnnotationType(Enum):
    HIGHLIGHT = "highlight"
    NOTE = "note"
    FREEHAND = "freehand"
    SHAPE = "shape"
    STAMP = "stamp"
    MEASUREMENT = "measurement"


@dataclass
class Annotation:
    """Base annotation class."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    type: AnnotationType = AnnotationType.HIGHLIGHT
    page: int = 0                          # 0-based page index
    rects: List[tuple] = field(default_factory=list)  # List of (x0, y0, x1, y1) in PDF points
    color: str = "#FFFF00"                 # Hex color string
    opacity: float = HIGHLIGHT_OPACITY     # 0.0 - 1.0
    author: str = ""
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    modified: str = field(default_factory=lambda: datetime.now().isoformat())
    content: str = ""                      # For notes: the text content
    metadata: Dict[str, Any] = field(default_factory=dict)

    def has_note(self) -> bool:
        """Whether this annotation carries text of its own.

        On the base class because both kinds do: a note is a ``/Text``
        annotation's whole point, and a note on a highlight is that highlight's
        ``/Contents``.
        """
        return bool(self.content.strip())

    def get_note(self) -> str:
        """The annotation's text, which is ``""`` when it has none."""
        return self.content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "page": self.page,
            "rects": self.rects,
            "color": self.color,
            "opacity": self.opacity,
            "author": self.author,
            "created": self.created,
            "modified": self.modified,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Annotation":
        ann = cls(
            id=data.get("id", str(uuid.uuid4())),
            type=AnnotationType(data.get("type", "highlight")),
            page=data.get("page", 0),
            rects=data.get("rects", []),
            color=data.get("color", "#FFFF00"),
            opacity=data.get("opacity", HIGHLIGHT_OPACITY),
            author=data.get("author", ""),
            created=data.get("created", datetime.now().isoformat()),
            modified=data.get("modified", datetime.now().isoformat()),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
        )
        return ann


@dataclass
class HighlightAnnotation(Annotation):
    """Text highlight annotation.

    A note on a highlight is the highlight's own ``/Contents``, so it needs no
    second annotation and travels with the highlight it belongs to.
    """
    type: AnnotationType = AnnotationType.HIGHLIGHT


@dataclass
class NoteAnnotation(Annotation):
    """A standalone sticky note: a PDF ``/Text`` annotation.

    Anchored to a point rather than to a selection — ``position`` is where its
    icon sits, and the icon is the only part of it a reader paints (the text
    opens in a popup, which is why an empty note is still a note).
    """
    type: AnnotationType = AnnotationType.NOTE
    # position: (x, y) in PDF points where the note icon appears
    position: tuple = (0.0, 0.0)


@dataclass
class InkAnnotation(Annotation):
    """A freehand drawing: a PDF ``/Ink`` annotation.

    ``paths`` is the pen, one list of points per stroke, in PDF points — the
    same space a highlight's rectangles live in, so a rotated view does not move
    the drawing (F10-3g).  ``/InkList`` keeps every stroke of one drawing in one
    annotation, which is why one Ctrl+Z undoes one drawing and not one line of it.
    """
    type: AnnotationType = AnnotationType.FREEHAND
    paths: List[List[tuple]] = field(default_factory=list)
    width: float = INK_WIDTH


@dataclass
class ShapeAnnotation(Annotation):
    """A geometric mark: a PDF ``/Square``, ``/Circle``, ``/Line`` or ``/Polygon``.

    ``kind`` names which, because the subtypes answer different questions: a
    rectangle or ellipse lives in ``rects``, while a line, arrow and polygon
    carry their vertices in ``points`` (page space) and use ``rects[0]`` only as
    the box a click or a jump needs.  An arrow is a line whose ``/LE`` says so.
    """
    type: AnnotationType = AnnotationType.SHAPE
    kind: str = "rect"
    points: List[tuple] = field(default_factory=list)
    width: float = INK_WIDTH


@dataclass
class StampAnnotation(Annotation):
    """A rubber stamp: a PDF ``/Stamp`` annotation.

    ``name`` is the ``/Name`` the file keeps: one of the standard names
    (``"Draft"``, ``"Approved"``…), which is also what MuPDF draws the
    appearance from, or ``"Custom"`` for a stamp whose appearance PDFAR wrote
    itself — a caption or an image.  The box lives in ``rects[0]``.
    ``content`` is a note the user attached, not the stamp's name: MuPDF fills
    ``/Contents`` with the standard name it already knows, and that caption is
    not something the user wrote.  A custom text stamp puts its caption there
    instead, so the comments panel can say what the mark says.
    """
    type: AnnotationType = AnnotationType.STAMP
    name: str = "Draft"


@dataclass(frozen=True)
class MeasurementScale:
    """How PDF points become real-world units for a measurement.

    The scale is kept in the annotation's rectilinear ``/Measure`` dictionary
    (``/Subtype /RL``), so the file itself says "1 pt = 0.353 mm" and a reader
    that understands dimensions shows the same number PDFAR does.  ``precision``
    is the number of decimals in the caption, stored as the number format's
    denominator: ``/D 10`` means one decimal.
    """
    units_per_point: float = 1.0
    unit: str = "pt"
    precision: int = 2

    @property
    def ratio(self) -> str:
        """The scale as the file writes it, e.g. ``1 pt = 0.352778 mm``."""
        return f"1 pt = {self.units_per_point:g} {self.unit}"

    def reading(self, length: float) -> str:
        """A page-space *length* in this scale, e.g. ``63.5 mm``."""
        return f"{length * self.units_per_point:.{self.precision}f} {self.unit}"


#: The scales the measure tool offers.  A point is 1/72 inch, so each factor is
#: that many real units; the first is the default because plans and drawings
#: are usually read in millimetres.
MEASURE_SCALES = (
    ("Millimetres", MeasurementScale(25.4 / 72.0, "mm", 1)),
    ("Centimetres", MeasurementScale(2.54 / 72.0, "cm", 2)),
    ("Inches", MeasurementScale(1.0 / 72.0, "in", 2)),
    ("Points", MeasurementScale(1.0, "pt", 1)),
)
MEASURE_DEFAULT_SCALE = MEASURE_SCALES[0][1]


def checked_measure_scale(scale: MeasurementScale) -> MeasurementScale:
    """The scale as the file can store it, or an edit that refuses.

    The factors go into PDF number format dictionaries as plain decimals, and
    the unit and ratio become literal strings; a scale outside these limits is
    one the file could not describe honestly.
    """
    if not math.isfinite(scale.units_per_point) \
            or not 1e-6 <= scale.units_per_point <= 1e6:
        raise EditError("That measurement scale is not a usable number")
    if not 0 <= scale.precision <= 4:
        raise EditError("A measurement keeps at most four decimals")
    unit = scale.unit.strip()
    if not unit or len(unit) > 16 or any(ord(c) < 32 for c in unit):
        raise EditError("That measurement unit cannot be written into a PDF")
    try:
        unit.encode("latin-1")
    except UnicodeEncodeError:
        raise EditError(
            "That measurement unit cannot be written into a PDF") from None
    return MeasurementScale(scale.units_per_point, unit, scale.precision)


@dataclass
class MeasurementAnnotation(Annotation):
    """A measured distance: a PDF ``/Line`` with ``/IT /LineDimension``.

    ``points`` are the two ends in page space.  ``scale`` is the ``/Measure``
    dictionary's scale, so the reading is a property of the file, not of the
    viewer that made it; ``reading`` turns the page-space distance into the
    text drawn above the line and kept in ``/Contents``.
    """
    type: AnnotationType = AnnotationType.MEASUREMENT
    points: List[tuple] = field(default_factory=list)
    scale: MeasurementScale = field(default_factory=lambda: MEASURE_DEFAULT_SCALE)
    width: float = MEASURE_WIDTH

    @property
    def reading(self) -> str:
        """The measured distance in the scale the annotation carries."""
        if len(self.points) != 2:
            return ""
        return self.scale.reading(math.dist(self.points[0], self.points[1]))


def stamp_box(width: float, height: float,
              center: Sequence[float],
              aspect: float = STAMP_ASPECT) -> Tuple[float, float, float, float]:
    """The box a stamp takes around *center*, inside a *width* x *height* page.

    The stamp keeps *aspect* (MuPDF's 3.8:1 appearance by default), is at most
    190 pt wide, and never covers more than 60 % of either side of a small
    page.  A click near an edge moves the whole box inwards rather than
    clipping it: half a stamp hanging off the paper is an annotation no reader
    can select.
    """
    box_width = min(STAMP_WIDTH, width * 0.6)
    box_height = box_width / aspect
    if box_height > height * 0.6:
        box_height = height * 0.6
        box_width = box_height * aspect
    cx, cy = float(center[0]), float(center[1])
    x0 = min(max(cx - box_width / 2.0, 0.0), width - box_width)
    y0 = min(max(cy - box_height / 2.0, 0.0), height - box_height)
    return (x0, y0, x0 + box_width, y0 + box_height)


def _caption_layout(caption: str,
                    page_width: float) -> Tuple[str, float, float, float]:
    """The text, box size and font size of a caption stamp.

    A caption is one line; whitespace collapses to single spaces.  The font
    starts at 18 pt and shrinks until the line fits inside 80 % of the page,
    never below 8 pt: a caption that would still not fit at 8 pt is refused
    instead of being written unreadably small.
    """
    text = " ".join(caption.split())
    if not text:
        raise EditError("A custom stamp needs a caption")
    try:
        text.encode("latin-1")
    except UnicodeEncodeError:
        raise EditError(
            "That caption has characters the stamp font cannot draw") from None
    limit = page_width * 0.8
    size = STAMP_CAPTION_FONT
    text_width = fitz.get_text_length(text, fontname="hebo", fontsize=size)
    while text_width + 2 * STAMP_CAPTION_PADDING > limit \
            and size > STAMP_CAPTION_MIN_FONT:
        size -= 1.0
        text_width = fitz.get_text_length(text, fontname="hebo", fontsize=size)
    width = text_width + 2 * STAMP_CAPTION_PADDING
    if width > limit:
        raise EditError("That caption is too long for this page")
    return text, width, size + 2 * STAMP_CAPTION_PADDING, size


def _pdf_string(text: str) -> str:
    """*text* as the bytes of a PDF literal string, escaped for WinAnsi."""
    out = []
    for byte in text.encode("latin-1"):
        if byte in (0x28, 0x29, 0x5C):
            out.append("\\" + chr(byte))
        elif 32 <= byte <= 126:
            out.append(chr(byte))
        else:
            out.append(f"\\{byte:03o}")
    return "".join(out)


def _new_stream_object(doc: fitz.Document, definition: str, content: bytes) -> int:
    """An xref holding *definition* as its dictionary and *content* as its stream.

    ``get_new_xref()`` creates an empty object and ``update_stream()`` only
    accepts one that is already a dictionary, so the dictionary goes first;
    then ``update_stream()`` adds ``/Length`` and the Flate filter itself.
    """
    xref = doc.get_new_xref()
    doc.update_object(xref, definition)
    doc.update_stream(xref, content)
    return xref


def _caption_appearance(doc: fitz.Document, text: str, width: float,
                        height: float, size: float) -> int:
    """The form XObject that draws a red box and *text* into it."""
    font = doc.get_new_xref()
    doc.update_object(
        font, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
              "/Encoding /WinAnsiEncoding >>")
    baseline = height / 2.0 - size * 0.26
    content = (
        f"q 1 0 0 RG 1 0 0 rg 2 w "
        f"0.5 0.5 {width - 1.0:.3f} {height - 1.0:.3f} re S "
        f"BT /F1 {size:.2f} Tf {STAMP_CAPTION_PADDING:.3f} {baseline:.3f} Td "
        f"({_pdf_string(text)}) Tj ET Q"
    ).encode("latin-1")
    return _new_stream_object(
        doc,
        f"<< /Type /XObject /Subtype /Form /FormType 1 "
        f"/BBox [0 0 {width:.3f} {height:.3f}] "
        f"/Resources << /Font << /F1 {font} 0 R >> >> >>",
        content)


def _image_appearance(doc: fitz.Document,
                      path: str) -> Tuple[int, float, float]:
    """The form XObject that shows the image at *path*, and its pixel size.

    The image becomes an image XObject; a source with transparency also gets a
    grey ``/SMask`` so the page shows through where the file is clear, which
    is what makes a logo or a scanned signature usable as a stamp.
    """
    name = os.path.basename(str(path)) or str(path)
    try:
        pix = fitz.Pixmap(os.fspath(path))
    except Exception:
        raise EditError(f"“{name}” is not an image PDFAR can read") from None
    if pix.width <= 0 or pix.height <= 0:
        raise EditError(f"“{name}” is empty")
    mask_samples = None
    if pix.alpha:
        samples, components = pix.samples, pix.n
        mask_samples = bytes(samples[i] for i in range(components - 1,
                                                      len(samples), components))
        plain = fitz.Pixmap(pix, 0)
    else:
        plain = pix
    if plain.n not in (1, 3):
        plain = fitz.Pixmap(fitz.csRGB, plain)
    image = _new_stream_object(
        doc,
        f"<< /Type /XObject /Subtype /Image /Width {plain.width} "
        f"/Height {plain.height} "
        f"/ColorSpace {'/DeviceGray' if plain.n == 1 else '/DeviceRGB'} "
        f"/BitsPerComponent 8 >>",
        plain.samples)
    if mask_samples is not None:
        mask = _new_stream_object(
            doc,
            f"<< /Type /XObject /Subtype /Image /Width {plain.width} "
            f"/Height {plain.height} /ColorSpace /DeviceGray "
            f"/BitsPerComponent 8 >>",
            mask_samples)
        doc.xref_set_key(image, "SMask", f"{mask} 0 R")
    form = _new_stream_object(
        doc,
        f"<< /Type /XObject /Subtype /Form /FormType 1 "
        f"/BBox [0 0 {plain.width} {plain.height}] "
        f"/Resources << /XObject << /Im0 {image} 0 R >> >> >>",
        f"q {plain.width} 0 0 {plain.height} 0 0 cm /Im0 Do Q".encode())
    return form, float(plain.width), float(plain.height)


def _place_custom_stamp(width: float, height: float, page_width: float,
                        page_height: float,
                        center: Sequence[float]
                        ) -> Tuple[float, float, float, float]:
    """A stamp of its own aspect at its natural size, shrunk to fit the page."""
    scale = min(1.0, page_width * 0.8 / width, page_height * 0.6 / height)
    box_width, box_height = width * scale, height * scale
    cx, cy = float(center[0]), float(center[1])
    x0 = min(max(cx - box_width / 2.0, 0.0), page_width - box_width)
    y0 = min(max(cy - box_height / 2.0, 0.0), page_height - box_height)
    return (x0, y0, x0 + box_width, y0 + box_height)


def _pdf_number(value: float, places: int = 3) -> str:
    """*value* as a plain PDF number, with trailing zeros trimmed."""
    text = f"{value:.{places}f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _measure_dictionary(scale: MeasurementScale) -> str:
    """The rectilinear ``/Measure`` dictionary a measurement annotation carries.

    ``/X`` converts page-space x to real units, and ``/D`` is the distance
    format (a factor relative to ``/X``).  The precision is the denominator of
    the number format, which is how the PDF specification says how many
    decimals to show.
    """
    unit = _pdf_string(scale.unit)
    denominator = 10 ** scale.precision
    distance = (f"<< /Type /NumberFormat /U ({unit}) "
                f"/C 1 /F /D /D {denominator} /FD true >>")
    return (f"<< /Type /Measure /Subtype /RL /R ({_pdf_string(scale.ratio)}) "
            f"/X [ << /Type /NumberFormat /U ({unit}) "
            f"/C {_pdf_number(scale.units_per_point, 6)} /F /D "
            f"/D {denominator} /FD true >> ] /D [ {distance} ] >>")


def _measurement_appearance(doc: fitz.Document, points: Sequence[tuple],
                            scale: MeasurementScale,
                            color: Tuple[float, float, float],
                            width: float, page_width: float,
                            page_height: float) -> Tuple[int, tuple, str]:
    """The form XObject that draws a dimension line, its ticks and reading.

    The appearance is PDFAR's own because MuPDF paints a measurement line as a
    plain line: without it neither PDFAR nor another viewer would show the
    number.  The box has to make room for the label above (or, at the top of
    the page, below) the line, and a Line annotation cannot be given that box
    through ``set_rect()`` — the caller writes ``/Rect`` directly.
    """
    (px0, py0), (px1, py1) = points
    label = scale.reading(math.dist(points[0], points[1]))
    text_width = fitz.get_text_length(label, fontname="helv",
                                      fontsize=MEASURE_FONT)
    if text_width + 2.0 > page_width:
        raise EditError("The measurement reading is too wide for this page")
    left = min(max((px0 + px1) / 2.0 - text_width / 2.0, 1.0),
               page_width - text_width - 1.0)
    top, bottom = min(py0, py1), max(py0, py1)
    if top - MEASURE_LABEL_GAP - MEASURE_FONT >= 1.0:
        baseline = top - MEASURE_LABEL_GAP
    elif bottom + MEASURE_LABEL_GAP + MEASURE_FONT <= page_height - 1.0:
        baseline = bottom + MEASURE_LABEL_GAP + MEASURE_FONT
    else:
        baseline = top - MEASURE_LABEL_GAP
    pad = MEASURE_TICK / 2.0 + max(1.0, width) + 0.5
    x0 = max(0.0, min(px0, px1, left) - pad)
    x1 = min(page_width, max(px0, px1, left + text_width) + pad)
    y0 = max(0.0, min(top, baseline - MEASURE_FONT) - pad)
    y1 = min(page_height, max(bottom, baseline) + pad)
    box = (x0, y0, x1, y1)
    box_width, box_height = x1 - x0, y1 - y0

    def fx(x: float) -> str:
        return _pdf_number(x - x0)

    def fy(y: float) -> str:
        # Form space grows upwards; the page grows downwards.
        return _pdf_number(y1 - y)

    dx, dy = px1 - px0, py1 - py0
    length = math.hypot(dx, dy)
    half = MEASURE_TICK / 2.0
    nx, ny = -dy / length * half, dx / length * half
    red, green, blue = color
    ink = f"{_pdf_number(red)} {_pdf_number(green)} {_pdf_number(blue)}"
    content = (
        f"q {ink} RG {ink} rg {_pdf_number(width)} w 1 J 0 J "
        f"{fx(px0)} {fy(py0)} m {fx(px1)} {fy(py1)} l S "
        f"{fx(px0 - nx)} {fy(py0 - ny)} m {fx(px0 + nx)} {fy(py0 + ny)} l S "
        f"{fx(px1 - nx)} {fy(py1 - ny)} m {fx(px1 + nx)} {fy(py1 + ny)} l S "
        f"BT /F1 {_pdf_number(MEASURE_FONT)} Tf "
        f"{fx(left)} {fy(baseline)} Td ({_pdf_string(label)}) Tj ET Q"
    ).encode("latin-1")
    font = doc.get_new_xref()
    doc.update_object(
        font, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
              "/Encoding /WinAnsiEncoding >>")
    form = _new_stream_object(
        doc,
        f"<< /Type /XObject /Subtype /Form /FormType 1 "
        f"/BBox [0 0 {_pdf_number(box_width)} {_pdf_number(box_height)}] "
        f"/Resources << /Font << /F1 {font} 0 R >> >> >>",
        content)
    return form, box, label


_MEASURE_UNIT = re.compile(r"/U\s*\(((?:\\.|[^()\\])*)\)")
_MEASURE_FACTOR = re.compile(r"/C\s+([-+]?(?:\d+\.?\d*|\.\d+))")
_MEASURE_DECIMALS = re.compile(r"/D\s+(\d+)")


def _unescape_pdf_string(text: str) -> str:
    """The text inside a PDF literal string, with its escapes resolved."""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in "()\\":
                out.append(following)
                index += 2
                continue
            if following in "01234567":
                end = index + 1
                while (end < min(index + 4, len(text))
                       and text[end] in "01234567"):
                    end += 1
                out.append(chr(int(text[index + 1:end], 8)))
                index = end
                continue
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _read_measurement_scale(doc: fitz.Document,
                            annot_xref: int) -> Optional[MeasurementScale]:
    """The scale an annotation's ``/Measure`` dictionary holds, if usable.

    A measurement from another program may use a compound or geospatial
    measure dictionary; only the rectilinear scale PDFAR writes is understood,
    and anything else is left to be read as the line it also is.
    """
    kind, value = doc.xref_get_key(annot_xref, "Measure")
    if kind == "xref":
        try:
            target = int(value.split()[0])
        except (ValueError, IndexError):
            return None
        if doc.xref_get_key(target, "Subtype") != ("name", "/RL"):
            return None
        kind, value = doc.xref_get_key(target, "X")
    elif kind == "dict":
        if doc.xref_get_key(annot_xref, "Measure/Subtype") != ("name", "/RL"):
            return None
        kind, value = doc.xref_get_key(annot_xref, "Measure/X")
    else:
        return None
    if kind != "array":
        return None
    unit_match = _MEASURE_UNIT.search(value)
    factor_match = _MEASURE_FACTOR.search(value)
    if unit_match is None or factor_match is None:
        return None
    try:
        factor = float(factor_match.group(1))
    except ValueError:
        return None
    precision = 2
    decimals = _MEASURE_DECIMALS.search(value)
    if decimals is not None:
        precision = {1: 0, 10: 1, 100: 2, 1000: 3, 10000: 4}.get(
            int(decimals.group(1)), 2)
    try:
        return checked_measure_scale(MeasurementScale(
            factor, _unescape_pdf_string(unit_match.group(1)), precision))
    except EditError:
        return None


# -------------------------------------------------------------------------- color
def _hex_to_rgb(text: str) -> Tuple[float, float, float]:
    """``#RRGGBB`` as the three floats a PDF color is."""
    digits = (text or "").lstrip("#")
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    if len(digits) != 6:
        raise EditError(f"“{text}” is not a color")
    try:
        red, green, blue = (int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        raise EditError(f"“{text}” is not a color") from None
    return (red, green, blue)


def _rgb_to_hex(color: Sequence[float]) -> str:
    """A PDF color as upper-case hex, which is how :data:`HIGHLIGHT_COLORS` writes
    them — so a highlight read back out of a saved file still matches its color
    name in the sidebar instead of being listed as "Custom"."""
    channels = [round(min(1.0, max(0.0, c)) * 255) for c in tuple(color)[:3]]
    return "#{:02X}{:02X}{:02X}".format(*channels)


# ----------------------------------------------------------------------- identity
def annot_key(annot: fitz.Annot) -> str:
    """The model id of a document annotation: its ``/NM`` name.

    PyMuPDF reports ``/NM`` as ``info["id"]`` and gives every annotation it makes
    a unique one, which is what lets an id survive a save and a clone.  An
    annotation from another program may have none, and naming it on open would
    turn reading the file into changing it, so those are keyed by object number.
    """
    return annot.info.get("id") or f"anon:{annot.xref}"


def _name(doc: fitz.Document, annot: fitz.Annot, ann_id: str) -> None:
    """Stamp an annotation with the id the interface already holds.

    PDFAR's ids are UUIDs, whose characters are exactly the ones a PDF literal
    string needs, so nothing in them has to be escaped.
    """
    doc.xref_set_key(annot.xref, "NM", f"({ann_id})")


def _find_annotation(page: fitz.Page, ann_id: str) -> fitz.Annot:
    """The annotation *ann_id* names on *page*, or an edit that refuses.

    Matching by name rather than by position matters: an undo can leave the page
    with no such annotation, and changing whatever annotation happened to sit at
    that object number instead would damage the document.
    """
    for annot in page.annots():
        if annot_key(annot) == ann_id:
            return annot
    raise EditError("That annotation is not in this document any more.")


def _quad_rects(annot: fitz.Annot) -> List[tuple]:
    """One rectangle per highlight quad — the shape the model stores.

    The quads are the only faithful record of what was selected, and writing them
    back as one multi-quad annotation reproduces the annotation unchanged, so a
    highlight survives a reload without drifting.
    """
    vertices = annot.vertices or []
    rects = []
    for i in range(0, len(vertices) - 3, 4):
        quad = vertices[i:i + 4]
        # Indexed, not `.x`: PyMuPDF hands these back as plain (x, y) tuples on
        # some releases and as Point objects on others.
        xs = [corner[0] for corner in quad]
        ys = [corner[1] for corner in quad]
        rects.append((min(xs), min(ys), max(xs), max(ys)))
    if rects:
        return rects
    rect = annot.rect        # a foreign highlight drawn without quad points
    return [(rect.x0, rect.y0, rect.x1, rect.y1)]


def _highlight_model(annot: fitz.Annot, page_idx: int) -> HighlightAnnotation:
    """Read one document annotation into the model."""
    info = annot.info
    opacity = annot.opacity
    return HighlightAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=_quad_rects(annot),
        color=_rgb_to_hex(annot.colors.get("stroke") or (1.0, 1.0, 0.0)),
        opacity=HIGHLIGHT_OPACITY if opacity is None else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
    )


def _note_model(annot: fitz.Annot, page_idx: int) -> NoteAnnotation:
    """Read one document annotation into the note model.

    The icon's rectangle is the whole geometry a note has: ``position`` is where
    the user clicked, and the rectangle is what the view has to repaint and what
    a click has to hit.
    """
    info = annot.info
    rect = annot.rect
    return NoteAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=[(rect.x0, rect.y0, rect.x1, rect.y1)],
        position=(rect.x0, rect.y0),
        color=_rgb_to_hex(annot.colors.get("stroke") or (1.0, 1.0, 0.0)),
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
    )


def _ink_model(annot: fitz.Annot, page_idx: int) -> InkAnnotation:
    """Read one document annotation into the freehand model.

    ``vertices`` hands back the ``/InkList`` in page space, which is the space
    ``add_ink_annot`` was given — so a drawing that goes out of the file and
    comes back is the same drawing, point for point.
    """
    info = annot.info
    paths = [[(float(x), float(y)) for x, y in line]
             for line in (annot.vertices or [])]
    rect = annot.rect
    opacity = annot.opacity
    width = (annot.border or {}).get("width")
    return InkAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=[(rect.x0, rect.y0, rect.x1, rect.y1)],
        color=_rgb_to_hex(annot.colors.get("stroke") or (0.0, 0.0, 1.0)),
        # An ink with no /CA paints solid, and -1 is how MuPDF reports "unset".
        opacity=1.0 if opacity is None or opacity < 0 else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
        paths=paths,
        width=INK_WIDTH if width is None else float(width),
    )


def _shape_model(annot: fitz.Annot, page_idx: int,
                 kind: str) -> ShapeAnnotation:
    """Read one document shape into the model.

    A ``/Line`` becomes ``"arrow"`` when an ``/LE`` end is an arrowhead, so the
    model says what the user drew and not which PDF subtype stores it.
    ``vertices`` hands back a line's two points and a polygon's corners in page
    space; a box shape keeps its geometry in ``rects`` like every other model.
    """
    info = annot.info
    rect = annot.rect
    opacity = annot.opacity
    width = (annot.border or {}).get("width")
    points = ([(float(x), float(y)) for x, y in annot.vertices]
              if kind in ("line", "polygon") else [])
    if kind == "line" and any(annot.line_ends):
        kind = "arrow"
    return ShapeAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=[(rect.x0, rect.y0, rect.x1, rect.y1)],
        color=_rgb_to_hex(annot.colors.get("stroke") or (0.0, 0.0, 0.0)),
        # A shape with no /CA paints solid, and -1 is how MuPDF reports "unset".
        opacity=1.0 if opacity is None or opacity < 0 else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
        kind=kind,
        points=points,
        width=INK_WIDTH if width is None else float(width),
    )


def _stamp_model(annot: fitz.Annot, page_idx: int) -> StampAnnotation:
    """Read one document stamp into the model.

    The name is ``/Name``, the key MuPDF drew the appearance from; it is not
    the same as ``/Contents``, which MuPDF fills with that same name by
    default.  Treating that caption as a note would put a note on every stamp
    the user never wrote, so it is only a note when it differs from the name.
    A custom stamp names itself ``Custom`` and puts its own caption or note in
    ``/Contents``, so the two never match.
    """
    info = annot.info
    rect = annot.rect
    opacity = annot.opacity
    name = info.get("name") or ""
    content = info.get("content") or ""
    if content and content == name:
        content = ""
    return StampAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=[(rect.x0, rect.y0, rect.x1, rect.y1)],
        color=_rgb_to_hex(annot.colors.get("stroke") or (1.0, 0.0, 0.0)),
        # A stamp with no /CA paints as its appearance stream says, and -1 is
        # how MuPDF reports "unset".
        opacity=1.0 if opacity is None or opacity < 0 else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=content,
        name=name,
    )


def _measurement_model(doc: fitz.Document, annot: fitz.Annot,
                       page_idx: int) -> Optional[MeasurementAnnotation]:
    """Read one dimension line into the model, or ``None`` if it is not one.

    A ``/Line`` is only a measurement when its ``/IT`` says so and a
    rectilinear ``/Measure`` scale can be read; a line with an unfamiliar
    measure dictionary is still a line, and the caller reads it as one.
    """
    if doc.xref_get_key(annot.xref, "IT") != ("name", "/LineDimension"):
        return None
    scale = _read_measurement_scale(doc, annot.xref)
    points = [(float(x), float(y)) for x, y in (annot.vertices or [])]
    if scale is None or len(points) != 2:
        return None
    info = annot.info
    rect = annot.rect
    opacity = annot.opacity
    width = (annot.border or {}).get("width")
    return MeasurementAnnotation(
        id=annot_key(annot),
        page=page_idx,
        rects=[(rect.x0, rect.y0, rect.x1, rect.y1)],
        color=_rgb_to_hex(annot.colors.get("stroke") or (0.0, 0.0, 0.0)),
        # A measurement with no /CA paints solid, and -1 is "unset".
        opacity=1.0 if opacity is None or opacity < 0 else opacity,
        author=info.get("title") or "",
        created=info.get("creationDate") or "",
        modified=info.get("modDate") or "",
        content=info.get("content") or "",
        points=points,
        scale=scale,
        width=MEASURE_WIDTH if width is None else float(width),
    )


# -------------------------------------------------------------------------- edits
@dataclass(frozen=True)
class HighlightSpec:
    """One page's share of a highlight: the id it will have, and its rectangles."""

    ann_id: str
    page: int
    rects: Tuple[tuple, ...]


class SinglePageEdit(Edit):
    """An edit whose ``page`` field names the one page it writes to.

    Says so for the list's sake: a page nobody wrote to cannot have changed, so
    there is nothing there to re-read (F10-3h).
    """

    page: int

    @property
    def pages(self) -> Tuple[int, ...]:
        return (self.page,)


@dataclass(frozen=True)
class AddHighlight(Edit):
    """Highlight the selected rectangles.

    One edit carries every page the selection touched, because the user selected
    once — an edit per page would make Ctrl+Z undo the answer a page at a time.
    """

    specs: Tuple[HighlightSpec, ...]
    color: str = "#FFFF00"
    opacity: float = HIGHLIGHT_OPACITY
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Highlight"

    @property
    def pages(self) -> Tuple[int, ...]:
        return tuple(spec.page for spec in self.specs)

    def apply(self, doc: fitz.Document) -> None:
        if not self.specs:
            raise EditError("There is nothing to highlight")
        stroke = _hex_to_rgb(self.color)
        for spec in self.specs:
            page = doc.load_page(spec.page)
            # One annotation holding every line of the selection: quads are how
            # a PDF records a selection that broke across lines.
            annot = page.add_highlight_annot([fitz.Rect(*rect) for rect in spec.rects])
            annot.set_colors(stroke=stroke)
            annot.set_opacity(self.opacity)
            if self.author:
                annot.set_info(title=self.author)
            if self.note:
                annot.set_info(content=self.note)
            annot.update()
            _name(doc, annot, spec.ann_id)


@dataclass(frozen=True)
class AddNote(SinglePageEdit):
    """Put a sticky note at *position* — a PDF ``/Text`` annotation.

    The point is the icon's top-left.  It is pulled inside the page if the user
    clicked near the bottom or right edge, because an icon that hangs off the
    page is one no reader lets them click.
    """

    ann_id: str
    page: int
    position: tuple
    note: str = ""
    author: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Add note"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(self.page)
        box = page.rect
        x = max(box.x0, min(float(self.position[0]), box.x1 - NOTE_ICON_SIZE))
        y = max(box.y0, min(float(self.position[1]), box.y1 - NOTE_ICON_SIZE))
        # ``add_text_annot`` writes the text as the annotation's /Contents, which
        # is the same key a note on a highlight uses; an empty one is allowed,
        # since the icon is what the user asked for and the popup is where the
        # text will go.
        annot = page.add_text_annot(fitz.Point(x, y), self.note)
        if self.author:
            annot.set_info(title=self.author)
        annot.update()
        _name(doc, annot, self.ann_id)


def _clean_paths(paths: Sequence[Sequence[tuple]]) -> List[List[tuple]]:
    """The strokes as MuPDF wants them, or an edit that refuses.

    The points stay plain number pairs: ``add_ink_annot`` checks for a sequence
    of length two, and a ``fitz.Point`` is not one — passing it is a ``ValueError``
    that reads like the drawing itself was malformed.

    An empty ``/InkList`` is not an invisible annotation but a broken one: MuPDF
    gives it the whole coordinate range as its rectangle, so the page would be
    repainted for nothing and the row in the panel would be unclickable.
    """
    cleaned = [[(float(x), float(y)) for x, y in line]
               for line in paths if line]
    if not cleaned:
        raise EditError("There is nothing to draw")
    return cleaned


@dataclass(frozen=True)
class AddInk(SinglePageEdit):
    """Draw *paths* on a page — a PDF ``/Ink`` annotation.

    Every stroke is one annotation, because the user drew once: an edit per
    stroke would make Ctrl+Z lift the drawing off a line at a time.  The points
    are in page space, the space the annotation lives in — a view that has been
    turned must not move what was drawn (F10-3g).
    """

    ann_id: str
    page: int
    paths: Tuple[tuple, ...]
    color: str = INK_COLOR
    width: float = INK_WIDTH
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Freehand"

    def apply(self, doc: fitz.Document) -> None:
        lines = _clean_paths(self.paths)
        page = doc.load_page(self.page)
        annot = page.add_ink_annot(lines)
        annot.set_colors(stroke=_hex_to_rgb(self.color))
        annot.set_border(width=float(self.width))
        if self.author:
            annot.set_info(title=self.author)
        if self.note:
            annot.set_info(content=self.note)
        # MuPDF writes the appearance stream here, which is what makes other
        # readers show the stroke without PDFAR drawing it a second time.
        annot.update()
        _name(doc, annot, self.ann_id)


def _clean_shape(kind: str, rect, points) -> tuple:
    """The geometry to hand MuPDF, or an edit that refuses.

    A shape with no extent is not an invisible annotation: a zero-height
    ``/Square`` is a line, and MuPDF stores a degenerate box that no reader can
    select or the panel can ring.  The corners may arrive in any order, because
    the user may drag up and to the left; they are put back in page order here.
    """
    if kind not in SHAPE_LABELS:
        raise EditError(f"“{kind}” is not a shape")
    if kind in ("rect", "ellipse"):
        if rect is None or len(rect) != 4:
            raise EditError("A shape needs a box")
        x0, y0, x1, y1 = (float(v) for v in rect)
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left < 0.01 or bottom - top < 0.01:
            raise EditError("A shape needs a box with area")
        return (left, top, right, bottom)
    cleaned = [(float(x), float(y)) for x, y in points]
    if kind in ("line", "arrow"):
        if len(cleaned) != 2:
            raise EditError("A line needs two points")
        (ax, ay), (bx, by) = cleaned
        if abs(ax - bx) < 0.01 and abs(ay - by) < 0.01:
            raise EditError("A line needs two different points")
        return tuple(cleaned)
    if len(cleaned) < 3:
        raise EditError("A polygon needs at least three points")
    return tuple(cleaned)


@dataclass(frozen=True)
class AddShape(SinglePageEdit):
    """Draw one shape on a page — a PDF ``/Square``, ``/Circle``, ``/Line`` or
    ``/Polygon``.

    ``kind`` picks the subtype: ``rect`` and ``ellipse`` use ``rect``, while
    ``line``, ``arrow`` and ``polygon`` use ``points``.  An arrow is a ``/Line``
    whose end carries a closed arrowhead, because that is how PDF draws one.
    """

    ann_id: str
    page: int
    kind: str
    rect: Optional[Tuple[float, float, float, float]] = None
    points: Tuple[tuple, ...] = ()
    color: str = INK_COLOR
    width: float = INK_WIDTH
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return SHAPE_LABELS.get(self.kind, "Shape")

    def apply(self, doc: fitz.Document) -> None:
        geometry = _clean_shape(self.kind, self.rect, self.points)
        page = doc.load_page(self.page)
        if self.kind == "rect":
            annot = page.add_rect_annot(fitz.Rect(*geometry))
        elif self.kind == "ellipse":
            annot = page.add_circle_annot(fitz.Rect(*geometry))
        elif self.kind in ("line", "arrow"):
            annot = page.add_line_annot(*geometry)
            if self.kind == "arrow":
                annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE,
                                    fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        else:
            annot = page.add_polygon_annot(list(geometry))
        annot.set_colors(stroke=_hex_to_rgb(self.color))
        annot.set_border(width=float(self.width))
        if self.author:
            annot.set_info(title=self.author)
        if self.note:
            annot.set_info(content=self.note)
        # MuPDF writes the appearance stream here, so the shape shows in other
        # readers even though PDFAR only stores its geometry.
        annot.update()
        _name(doc, annot, self.ann_id)


@dataclass(frozen=True)
class AddStamp(SinglePageEdit):
    """Put a standard rubber stamp on a page — a PDF ``/Stamp`` annotation.

    ``center`` is where the user clicked; the box around it comes from
    :func:`stamp_box`, so the tool places exactly what the model stores.  The
    appearance needs no work of ours: MuPDF draws it from ``/Name``, which is
    also what makes the mark readable in any other viewer.
    """

    ann_id: str
    page: int
    name: str = "Draft"
    center: Tuple[float, float] = (0.0, 0.0)
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Stamp"

    def apply(self, doc: fitz.Document) -> None:
        if self.name not in STAMP_VALUES:
            raise EditError(f"“{self.name}” is not a stamp")
        page = doc.load_page(self.page)
        annot = page.add_stamp_annot(
            fitz.Rect(*stamp_box(page.rect.width, page.rect.height,
                                 self.center)),
            stamp=STAMP_VALUES[self.name])
        if self.author:
            annot.set_info(title=self.author)
        if self.note:
            annot.set_info(content=self.note)
        annot.update()
        _name(doc, annot, self.ann_id)


@dataclass(frozen=True)
class AddCustomStamp(SinglePageEdit):
    """Put a stamp PDFAR draws itself on a page.

    A *caption* becomes a red box with text; an *image* is embedded with its
    transparency intact.  Either way the appearance is a form XObject in the
    annotation's ``/AP/N``, so any reader shows what PDFAR put there, and
    ``/Name`` is the non-standard ``"Custom"`` so no viewer regenerates a
    standard stamp over it.  *center* is where the user clicked; the box keeps
    the stamp's own aspect and stays inside the page.
    """

    ann_id: str
    page: int
    caption: str = ""
    image: str = ""
    center: Tuple[float, float] = (0.0, 0.0)
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Custom stamp"

    def apply(self, doc: fitz.Document) -> None:
        caption = " ".join(self.caption.split())
        if not caption and not self.image:
            raise EditError("A custom stamp needs a caption or an image")
        page = doc.load_page(self.page)
        if self.image:
            form, width, height = _image_appearance(doc, self.image)
            box = stamp_box(page.rect.width, page.rect.height, self.center,
                            aspect=width / height)
        else:
            text, width, height, size = _caption_layout(
                caption, page.rect.width)
            form = _caption_appearance(doc, text, width, height, size)
            box = _place_custom_stamp(width, height, page.rect.width,
                                      page.rect.height, self.center)
        annot = page.add_stamp_annot(fitz.Rect(*box))
        if self.author:
            annot.set_info(title=self.author)
        content = self.note or caption
        if content:
            annot.set_info(content=content)
        # update() regenerates the standard Draft appearance (and a caption)
        # the annotation was born with, and refits the rect to that 3.8:1
        # shape.  Everything the custom stamp is goes on after it: our box,
        # our appearance, and /Name /Custom so no viewer regenerates a
        # standard stamp over it.  A stamp with no note keeps no /Contents.
        annot.update()
        annot.set_rect(fitz.Rect(*box))
        if not content:
            doc.xref_set_key(annot.xref, "Contents", "null")
        _name(doc, annot, self.ann_id)
        doc.xref_set_key(annot.xref, "AP", f"<< /N {form} 0 R >>")
        doc.xref_set_key(annot.xref, "Name", f"/{CUSTOM_STAMP_NAME}")


@dataclass(frozen=True)
class AddMeasurement(SinglePageEdit):
    """Measure the distance between two page points — a ``/LineDimension``.

    The annotation is a real measurement: ``/IT /LineDimension`` tells a reader
    what it is and ``/Measure`` says how the page distance becomes the reading.
    Its appearance is PDFAR's own, because MuPDF draws only the bare line; the
    ticks and the number live in ``/AP/N`` like a custom stamp's caption.
    """

    ann_id: str
    page: int
    points: Tuple[tuple, ...]
    units_per_point: float = MEASURE_DEFAULT_SCALE.units_per_point
    unit: str = MEASURE_DEFAULT_SCALE.unit
    precision: int = MEASURE_DEFAULT_SCALE.precision
    color: str = MEASURE_COLOR
    width: float = MEASURE_WIDTH
    author: str = ""
    note: str = ""

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Measurement"

    def apply(self, doc: fitz.Document) -> None:
        points = [(float(x), float(y)) for x, y in self.points]
        if len(points) != 2:
            raise EditError("A measurement needs two points")
        if math.dist(points[0], points[1]) < 0.01:
            raise EditError("A measurement needs two different points")
        scale = checked_measure_scale(MeasurementScale(
            float(self.units_per_point), str(self.unit), int(self.precision)))
        color = _hex_to_rgb(self.color)
        page = doc.load_page(self.page)
        # Annotation points are page-space (unrotated) coordinates, so the page
        # box is the mediabox even when the view is turned.
        form, box, label = _measurement_appearance(
            doc, points, scale, color, float(self.width),
            page.mediabox.width, page.mediabox.height)
        x0, y0, x1, y1 = box
        annot = page.add_line_annot(fitz.Point(*points[0]),
                                    fitz.Point(*points[1]))
        annot.set_colors(stroke=color)
        annot.set_border(width=float(self.width))
        if self.author:
            annot.set_info(title=self.author)
        # update() refits /Rect to the bare line and draws a plain line; the
        # measurement's own box and appearance go on afterwards.
        annot.update()
        # /Rect is stored in PDF space, whose y axis runs up from the mediabox
        # bottom; a Line annotation refuses set_rect(), so it is written here.
        height = page.mediabox.height
        doc.xref_set_key(
            annot.xref, "Rect",
            f"[{_pdf_number(x0)} {_pdf_number(height - y1)} "
            f"{_pdf_number(x1)} {_pdf_number(height - y0)}]")
        doc.xref_set_key(annot.xref, "IT", "/LineDimension")
        doc.xref_set_key(annot.xref, "Measure", _measure_dictionary(scale))
        doc.xref_set_key(annot.xref, "Subj", "(Distance)")
        doc.xref_set_key(annot.xref, "Contents",
                         f"({_pdf_string(self.note or label)})")
        doc.xref_set_key(annot.xref, "AP", f"<< /N {form} 0 R >>")
        _name(doc, annot, self.ann_id)


@dataclass(frozen=True)
class RemoveAnnotation(SinglePageEdit):
    """Delete the annotation *ann_id* names."""

    ann_id: str
    page: int

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Delete annotation"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(self.page)
        page.delete_annot(_find_annotation(page, self.ann_id))


@dataclass(frozen=True)
class SetHighlightColor(SinglePageEdit):
    """Recolor an existing highlight."""

    ann_id: str
    page: int
    color: str
    opacity: float = HIGHLIGHT_OPACITY

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        return "Change highlight color"

    def apply(self, doc: fitz.Document) -> None:
        page = doc.load_page(self.page)
        annot = _find_annotation(page, self.ann_id)
        annot.set_colors(stroke=_hex_to_rgb(self.color))
        annot.set_opacity(self.opacity)
        # The color is baked into the appearance stream, so without a new
        # appearance the page keeps painting the color it was drawn with.
        annot.update()


@dataclass(frozen=True)
class SetAnnotationNote(SinglePageEdit):
    """Set or clear the text an annotation carries.

    The same step for both kinds: a standalone note's text and a note on a
    highlight live in the same ``/Contents`` key.
    """

    ann_id: str
    page: int
    note: str
    #: Whether the annotation already had text when the user asked.  Only the
    #: manager knows that, and it decides which of these three steps is called.
    had_note: bool = False

    scope = Scope.COMMENTS

    @property
    def label(self) -> str:
        if not self.note.strip():
            return "Remove note"
        return "Edit note" if self.had_note else "Add note"

    def apply(self, doc: fitz.Document) -> None:
        # ``page`` stays named and in scope: PyMuPDF unbinds an ``Annot`` the
        # moment its ``Page`` is garbage, and writing to a handle in that state
        # raises "annotation not bound to any page".
        page = doc.load_page(self.page)
        annot = _find_annotation(page, self.ann_id)
        if self.note:
            annot.set_info(content=self.note)
            return
        # ``set_info`` reads an empty string as "nothing to change", so clearing a
        # note means removing the key from the annotation object itself.
        doc.xref_set_key(annot.xref, "Contents", "null")


class AnnotationManager(QObject):
    """The annotations the document holds, as a list the interface can show.

    A change is an :class:`pdfar.edits.Edit` applied through the session, so it
    undoes, marks the document dirty and reaches the render workers as a new
    content version.  The list is re-read from the document afterwards: it is a
    cache of what the file would say, never a change waiting for Save.
    """

    annotations_changed = pyqtSignal()
    #: An edit was accepted; carries the ``Scope`` the view has to invalidate.
    annotations_edited = pyqtSignal(int)
    #: An edit the user asked for did not happen, and why.
    annotation_edit_failed = pyqtSignal(str)

    def __init__(self, session: DocumentSession, parent=None):
        super().__init__(parent)
        self.session = session
        #: What each page's read produced, so a page that did not change does not
        #: have to be read again.
        self._by_page: Dict[int, Dict[str, Annotation]] = {}
        self._annotations: Dict[str, Annotation] = {}
        # -1 so that the first read happens even for a session at generation 0.
        self._generation = -1
        self.reload_from_document()

    @property
    def doc(self) -> fitz.Document:
        """The live document; an accepted edit replaces the object."""
        return self.session.doc

    # ---------------------------------------------------------------- the model
    def reload_from_document(self) -> None:
        """Re-read the list when it no longer describes the document we hold.

        An adopted edit, an undo or a revert hands back a different
        ``fitz.Document``, which is what ``session.generation`` counts.  Re-read
        then, so the page and the id a row carries come from the document the user
        is looking at rather than from the one that was edited.

        Only the pages that step wrote to are read again: an annotation belongs to
        one page, so a page nobody wrote to cannot have changed, and on the 540-page
        book walking all of them cost three to five times the edit itself (F10-3h).
        """
        generation = self.session.generation
        if self._generation == generation:
            return
        doc = self.doc
        changed = self._changed_pages(generation)
        if changed is None:
            self._by_page = {}
            to_read = list(range(doc.page_count))
        else:
            to_read = [page for page in sorted(set(changed))
                       if 0 <= page < doc.page_count]
        for page_idx in to_read:
            models = self._read_page(doc, page_idx)
            if models:
                self._by_page[page_idx] = models
            else:
                self._by_page.pop(page_idx, None)
        self._annotations = {ann_id: model
                             for page in sorted(self._by_page)
                             for ann_id, model in self._by_page[page].items()}
        self._generation = generation
        self.annotations_changed.emit()

    def _changed_pages(self, generation: int) -> Optional[Sequence[int]]:
        """The pages to read again, or ``None`` for the whole document.

        ``session.changed_pages`` describes the step that produced this generation,
        so it can only be trusted when this list is one step behind.  Anything else
        — an edit that could not say which pages it wrote, a list nobody asked to
        read for several edits — and the safe answer is to look at everything.
        """
        if generation > self._generation + 1:
            return None
        return self.session.changed_pages

    def _read_page(self, doc: fitz.Document, page_idx: int) -> Dict[str, Annotation]:
        """The models for one page, keyed by the id the document gives them."""
        found: Dict[str, Annotation] = {}
        try:
            page = doc.load_page(page_idx)
            for annot in page.annots():
                kind = annot.type[0]
                if kind == fitz.PDF_ANNOT_HIGHLIGHT:
                    model = _highlight_model(annot, page_idx)
                elif kind == fitz.PDF_ANNOT_TEXT:
                    model = _note_model(annot, page_idx)
                elif kind == fitz.PDF_ANNOT_INK:
                    model = _ink_model(annot, page_idx)
                elif kind == fitz.PDF_ANNOT_SQUARE:
                    model = _shape_model(annot, page_idx, "rect")
                elif kind == fitz.PDF_ANNOT_CIRCLE:
                    model = _shape_model(annot, page_idx, "ellipse")
                elif kind == fitz.PDF_ANNOT_LINE:
                    # A measurement is a /Line with an /IT; read it first so a
                    # dimension is not shown as a plain line.
                    model = (_measurement_model(doc, annot, page_idx)
                             or _shape_model(annot, page_idx, "line"))
                elif kind == fitz.PDF_ANNOT_POLYGON:
                    model = _shape_model(annot, page_idx, "polygon")
                elif kind == fitz.PDF_ANNOT_STAMP:
                    model = _stamp_model(annot, page_idx)
                else:
                    # A free-text annotation PDFAR cannot edit yet: listing it
                    # would offer an action on something the user brought in
                    # from another program.
                    continue
                found[model.id] = model
        except ANNOT_READ_ERRORS:
            # Annotations are a linked chain, so one that the file cannot
            # read ends this page's list.  What was read before it stays, and
            # the rest of the document is unaffected.  The guard covers the
            # data errors only, so a mistake in here still surfaces instead
            # of quietly emptying the sidebar.
            pass
        return found

    def save_to_pdf(self) -> bool:
        """Write the document, which already holds every annotation.

        The write itself is F10-2's: staged, verified, incremental while the
        document is still the file handle it came from.  A refusal is reported
        as ``False`` here; the main window's Save command surfaces the reason.
        """
        try:
            return save_document(self.session) is not None
        except SaveError:
            return False

    # --------------------------------------------------------------- mutators
    def _run(self, edit: Edit) -> bool:
        """Carry out one annotation edit: document first, then list, then view.

        The session applies to a copy and adopts it only on success, so a refused
        edit leaves the page, the version and the undo stack exactly as they were.
        """
        try:
            _version, scope = self.session.apply(edit)
        except EditError as exc:
            self.annotation_edit_failed.emit(str(exc))
            return False
        self.reload_from_document()
        self.annotations_edited.emit(int(scope))
        return True

    def add_highlights(self, by_page: Dict[int, Sequence[tuple]],
                       color: str = "#FFFF00", author: str = "",
                       note: str = "") -> List[HighlightAnnotation]:
        """Highlight a selection that may cover several pages, as one edit.

        Pages with nothing on them are dropped, so an empty selection cannot
        become an undo step that appears to do nothing.
        """
        specs = tuple(
            HighlightSpec(ann_id=str(uuid.uuid4()), page=int(page),
                          rects=tuple(rects))
            for page, rects in by_page.items() if rects)
        if not specs:
            return []
        if not self._run(AddHighlight(specs=specs, color=color, author=author,
                                      note=note)):
            return []
        return [h for h in (self.get_highlight(spec.ann_id) for spec in specs)
                if h is not None]

    def add_highlight(self, page: int, rects: Sequence[tuple],
                      color: str = "#FFFF00", author: str = "",
                      note: str = "") -> Optional[HighlightAnnotation]:
        """Highlight one page's rectangles.  ``None`` if the edit was refused."""
        made = self.add_highlights({page: rects}, color, author, note)
        return made[0] if made else None

    def add_note(self, page: int, position: tuple, note: str = "",
                 author: str = "") -> Optional[NoteAnnotation]:
        """Put a standalone note at *position*.  ``None`` if the edit was refused."""
        ann_id = str(uuid.uuid4())
        if not self._run(AddNote(ann_id=ann_id, page=int(page),
                                position=tuple(position), note=note,
                                author=author)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, NoteAnnotation) else None

    def add_ink(self, page: int, paths: Sequence[Sequence[tuple]],
                color: str = INK_COLOR, width: float = INK_WIDTH,
                author: str = "",
                note: str = "") -> Optional[InkAnnotation]:
        """Draw *paths* — one list of points per stroke — on *page*.

        ``None`` if the edit was refused, which today means the drawing was
        empty.  Unlike a multi-page highlight there is nothing to drop here: one
        drawing is one edit, and it is either on a page or on none.
        """
        ann_id = str(uuid.uuid4())
        if not self._run(AddInk(ann_id=ann_id, page=int(page),
                                paths=tuple(tuple(line) for line in paths),
                                color=color, width=width, author=author,
                                note=note)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, InkAnnotation) else None

    def add_shape(self, page: int, kind: str, *,
                  rect: Optional[Sequence[float]] = None,
                  points: Sequence[tuple] = (),
                  color: str = INK_COLOR, width: float = INK_WIDTH,
                  author: str = "",
                  note: str = "") -> Optional[ShapeAnnotation]:
        """Draw one shape on *page*.  ``None`` if the edit was refused.

        ``rect`` is the box for a rectangle or ellipse; ``points`` are the
        vertices of a line, arrow or polygon, in page space.  One shape is one
        edit, so one Ctrl+Z takes it back.
        """
        ann_id = str(uuid.uuid4())
        box = tuple(float(v) for v in rect) if rect is not None else None
        if not self._run(AddShape(ann_id=ann_id, page=int(page), kind=kind,
                                  rect=box,
                                  points=tuple((float(x), float(y))
                                               for x, y in points),
                                  color=color, width=width, author=author,
                                  note=note)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, ShapeAnnotation) else None

    def add_stamp(self, page: int, name: str = "Draft",
                  center: Sequence[float] = (0.0, 0.0),
                  author: str = "",
                  note: str = "") -> Optional[StampAnnotation]:
        """Place one standard rubber stamp on *page*, centred on *center*.

        One stamp is one edit, so one Ctrl+Z takes the mark off.  ``None`` if
        the name is not one of the fourteen the file format defines.
        """
        ann_id = str(uuid.uuid4())
        if not self._run(AddStamp(
                ann_id=ann_id, page=int(page), name=name,
                center=(float(center[0]), float(center[1])),
                author=author, note=note)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, StampAnnotation) else None

    def add_custom_stamp(self, page: int,
                         center: Sequence[float],
                         caption: str = "", image: str = "",
                         author: str = "",
                         note: str = "") -> Optional[StampAnnotation]:
        """Place a caption or image stamp on *page*, centred on *center*.

        One stamp is one edit, so one Ctrl+Z takes the mark off.  ``None`` if
        the edit was refused — an empty definition, a caption too long for the
        page, or a file that is not an image PDFAR can read.
        """
        ann_id = str(uuid.uuid4())
        if not self._run(AddCustomStamp(
                ann_id=ann_id, page=int(page), caption=caption,
                image=str(image) if image else "",
                center=(float(center[0]), float(center[1])),
                author=author, note=note)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, StampAnnotation) else None

    def add_measurement(self, page: int,
                        points: Sequence[tuple],
                        scale: MeasurementScale = MEASURE_DEFAULT_SCALE,
                        color: str = MEASURE_COLOR,
                        width: float = MEASURE_WIDTH,
                        author: str = "",
                        note: str = "") -> Optional[MeasurementAnnotation]:
        """Measure the distance between two page points, in *scale*.

        One measurement is one edit, so one Ctrl+Z takes the dimension away.
        ``None`` if the edit was refused — two points that are the same, or a
        scale or unit the file cannot describe.
        """
        ann_id = str(uuid.uuid4())
        if not self._run(AddMeasurement(
                ann_id=ann_id, page=int(page),
                points=tuple((float(x), float(y)) for x, y in points),
                units_per_point=scale.units_per_point, unit=scale.unit,
                precision=scale.precision, color=color, width=width,
                author=author, note=note)):
            return None
        made = self.get_annotation(ann_id)
        return made if isinstance(made, MeasurementAnnotation) else None

    def remove_annotation(self, annotation_id: str) -> bool:
        """Remove an annotation by ID."""
        ann = self._annotations.get(annotation_id)
        if ann is None:
            return False
        return self._run(RemoveAnnotation(ann_id=annotation_id, page=ann.page))

    def set_highlight_color(self, annotation_id: str, color: str) -> bool:
        """Recolor a highlight, keeping how solid it paints."""
        highlight = self.get_highlight(annotation_id)
        if highlight is None:
            return False
        return self._run(SetHighlightColor(ann_id=annotation_id,
                                           page=highlight.page, color=color,
                                           opacity=highlight.opacity))

    def set_note(self, annotation_id: str, note_text: str) -> Optional[Annotation]:
        """Add or update the text of an annotation, of any kind."""
        if not self._set_note(annotation_id, note_text):
            return None
        return self.get_annotation(annotation_id)

    def remove_note(self, annotation_id: str) -> bool:
        """Empty an annotation's text, keeping the annotation itself."""
        return self._set_note(annotation_id, "")

    def _set_note(self, annotation_id: str, note: str) -> bool:
        ann = self.get_annotation(annotation_id)
        if ann is None:
            return False
        return self._run(SetAnnotationNote(ann_id=annotation_id, page=ann.page,
                                          note=note,
                                          had_note=ann.has_note()))

    # ------------------------------------------------------------------- reads
    def get_annotation(self, annotation_id: str) -> Optional[Annotation]:
        """One annotation by id, or ``None`` if there is no such annotation."""
        return self._annotations.get(annotation_id)

    def get_highlight(self, annotation_id: str) -> Optional[HighlightAnnotation]:
        """One highlight by id; ``None`` for any other kind of annotation."""
        ann = self._annotations.get(annotation_id)
        return ann if isinstance(ann, HighlightAnnotation) else None

    def get_annotations(self, page: Optional[int] = None,
                        ann_type: Optional[AnnotationType] = None) -> List[Annotation]:
        """Get all annotations, optionally filtered by page and type."""
        result = []
        for ann in self._annotations.values():
            if page is not None and ann.page != page:
                continue
            if ann_type is not None and ann.type != ann_type:
                continue
            result.append(ann)
        return result

    def get_annotations_for_page(self, page: int) -> List[Annotation]:
        """Get all annotations for a specific page."""
        return self.get_annotations(page=page)

    def get_highlights_for_page(self, page: int) -> List[HighlightAnnotation]:
        """Get all highlights for a specific page."""
        return [a for a in self.get_annotations(page=page, ann_type=AnnotationType.HIGHLIGHT)
                if isinstance(a, HighlightAnnotation)]

    def get_notes_for_page(self, page: int) -> List[NoteAnnotation]:
        """Get all standalone notes for a specific page."""
        return [a for a in self.get_annotations(page=page, ann_type=AnnotationType.NOTE)
                if isinstance(a, NoteAnnotation)]

    def get_note(self, annotation_id: str) -> Optional[str]:
        """The text an annotation carries, or ``None`` if there is no such annotation."""
        ann = self._annotations.get(annotation_id)
        return ann.content if ann is not None else None


# Standard highlight colors (Okular-style)
HIGHLIGHT_COLORS = [
    ("Yellow", "#FFFF00"),
    ("Green", "#90EE90"),
    ("Blue", "#87CEEB"),
    ("Pink", "#FFB6C1"),
    ("Orange", "#FFD580"),
    ("Purple", "#DDA0DD"),
    ("Red", "#FF6B6B"),
    ("Cyan", "#80FFFF"),
]
