"""Phase 5 — paragraph model, line breaking and layout-aware reflow.

This module is the *analysis and planning* half of paragraph editing.  It does
not modify a document; :mod:`pdfar.paragraph_edit` takes a :class:`Paragraph`
and a :class:`ParagraphPlan` and writes the result.

Why a separate model
--------------------
A PDF content stream has no paragraphs.  It has text-showing operators, each
positioned by a text matrix, and PyMuPDF's ``get_text("dict")`` gives a
*reader's* interpretation (blocks, lines, spans) that does not carry operator
identity.  Editing a paragraph needs both:

* the geometric/typographic view — which lines belong together, where their
  baselines are, how they are aligned, how much room is available;
* the content-stream view — which operators draw those lines, with which font
  resource and text state, so the edit can replace exactly that span and
  nothing else.

:func:`extract_paragraphs` joins the two.  It groups lines with geometric
evidence (gap, indent, style change), then maps each line back to the
text-showing operators that produced it by replaying the graphics state with
:func:`pdfar.text_edit_existing.iter_page_runs`.  A line whose text cannot be
matched to the operators is marked unmappable and reflow refuses it rather than
guessing.

Layout is computed in **text space** (the space of the ``Tm`` matrix) using
real glyph advances, then mapped to page space through the same text-rendering
matrix the document uses.  That keeps rotation, scaling and horizontal scaling
consistent between the preview and the committed edit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import EditError
from .text_edit_existing import (
    StreamRef,
    TextRun,
    TextState,
    font_widths,
    iter_page_runs,
    page_font_codecs,
    page_font_programs,
)

_SUBSET_TAG_RE = re.compile(r"^[A-Z]{6}\+")
_EPS = 0.5
#: How far a run's baseline may sit from a line's baseline and still belong to it.
_BASELINE_TOL = 0.35
#: Extra room (points) given to a region so unchanged text never rewraps.
_REGION_SLACK = 0.5


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class ParagraphError(EditError):
    """A paragraph cannot be reflowed safely."""


class ParagraphUnsupported(ParagraphError):
    """The structure is understood but cannot be rebuilt without risk."""


class ParagraphOverflow(ParagraphError):
    """The new text does not fit the region or collides with other content."""

    def __init__(self, message: str, plan: "ParagraphPlan"):
        super().__init__(message)
        self.plan = plan


# --------------------------------------------------------------------------- #
# Geometry helpers
# --------------------------------------------------------------------------- #
def _apply(m: Sequence[float], x: float, y: float) -> Tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _aabb(points: Iterable[Tuple[float, float]]) -> Tuple[float, float, float, float]:
    pts = list(points)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _rect_area(rect: Sequence[float]) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersect(a: Sequence[float], b: Sequence[float]):
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]),
            max(a[2], b[2]), max(a[3], b[3]))


def _expand(rect, pad: float):
    return (rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)


def _contains(outer, inner, pad: float = 0.0) -> bool:
    return (outer[0] <= inner[0] + pad and outer[1] <= inner[1] + pad
            and outer[2] >= inner[2] - pad and outer[3] >= inner[3] - pad)


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _norm_len(v) -> float:
    return math.hypot(v[0], v[1])


def _unit(v) -> Tuple[float, float]:
    n = _norm_len(v)
    if n <= 1e-9:
        return (1.0, 0.0)
    return (v[0] / n, v[1] / n)


def _norm_text(text: str) -> str:
    """Whitespace-insensitive form used to match dict text to run text."""
    return "".join(text.replace("\u00a0", " ").split())


def _strip_subset(name: str) -> str:
    return _SUBSET_TAG_RE.sub("", name or "")


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SpanInfo:
    """One PyMuPDF span (a run of text with a single font/size/colour)."""

    text: str
    font: str
    size: float
    color: int
    flags: int
    origin: Tuple[float, float]
    bbox: Tuple[float, float, float, float]
    ascender: float
    descender: float


@dataclass(frozen=True)
class RunPiece:
    """The slice of a text-showing operator that draws part of one line."""

    source: StreamRef
    op_index: int
    text: str
    start: int
    end: int
    origin: Tuple[float, float]
    trm: Tuple[float, ...]
    tm: Optional[Tuple[float, ...]]
    ctm: Optional[Tuple[float, ...]]
    font_resource: str
    font_size: float
    text_state: TextState
    bbox: Tuple[float, float, float, float]

    @property
    def key(self) -> Tuple[int, int]:
        return (self.source.xref, self.op_index)


@dataclass(frozen=True)
class ParagraphLine:
    """One visual line, with both its dict view and its operators."""

    index: int                       # line index inside the block
    text: str
    rect: Tuple[float, float, float, float]
    baseline: Tuple[float, float]    # first span origin, page space
    font: str
    font_size: float
    color: int
    flags: int
    ascender: float
    descender: float
    spans: Tuple[SpanInfo, ...] = ()
    pieces: Tuple[RunPiece, ...] = ()
    block_number: int = -1
    #: Position in the page-wide reading order (block order, then line order).
    global_index: int = -1
    #: Unit writing direction in page space (PyMuPDF's line ``dir``).
    direction: Tuple[float, float] = (1.0, 0.0)

    @property
    def mapped(self) -> bool:
        return bool(self.pieces)

    @property
    def key(self) -> Tuple[int, int]:
        return (self.block_number, self.index)


@dataclass(frozen=True)
class Paragraph:
    """A run of consecutive lines treated as one editable block."""

    page_num: int
    block_number: int
    first_index: int
    last_index: int
    line_keys: Tuple[Tuple[int, int], ...]
    lines: Tuple[ParagraphLine, ...]
    rect: Tuple[float, float, float, float]
    text: str
    alignment: str
    leading: float
    font: str
    font_size: float
    color: int
    flags: int
    ascender: float
    descender: float
    angle: float
    confidence: float
    problems: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()
    body_left: float = 0.0
    body_right: float = 0.0
    indent: float = 0.0
    origin: Tuple[float, float] = (0.0, 0.0)
    trm: Tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    tm: Optional[Tuple[float, ...]] = None
    font_resource: str = ""
    res_owner: int = 0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    h_scale: float = 1.0
    rise: float = 0.0
    render_mode: int = 0

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def baseline_scale(self) -> float:
        """Page-space length of one text-space unit along the baseline."""
        return _norm_len((self.trm[0], self.trm[1]))

    @property
    def normal_scale(self) -> float:
        """Page-space length of one text-space unit along the line normal."""
        return _norm_len((self.trm[2], self.trm[3]))

    @property
    def rotated(self) -> bool:
        return abs(self.angle) > 1e-3 and abs(abs(self.angle) - 2 * math.pi) > 1e-3

    @property
    def bold(self) -> bool:
        return bool(self.flags & 16) or "bold" in self.font.lower()

    @property
    def italic(self) -> bool:
        return bool(self.flags & 2) or any(
            word in self.font.lower() for word in ("italic", "oblique"))

    @property
    def serif(self) -> bool:
        name = self.font.lower()
        return "serif" in name and "sans" not in name

    @property
    def monospaced(self) -> bool:
        name = self.font.lower()
        return any(word in name for word in ("courier", "mono", "consol"))

    @property
    def run_pieces(self) -> Tuple[RunPiece, ...]:
        out: List[RunPiece] = []
        for line in self.lines:
            out.extend(line.pieces)
        return tuple(out)

    @property
    def operator_keys(self) -> Tuple[Tuple[int, int], ...]:
        return tuple(sorted({p.key for p in self.run_pieces}))

    def describe(self) -> str:
        return (f"{self.font} {self.font_size:g} pt, {self.line_count} line"
                f"{'s' if self.line_count != 1 else ''}, "
                f"{self.alignment}")


@dataclass(frozen=True)
class Collision:
    """An obstacle the reflowed text would touch."""

    rect: Tuple[float, float, float, float]
    kind: str          # "text", "image" or "drawing"
    label: str = ""


@dataclass(frozen=True)
class PlannedLine:
    """One line of the new layout, in text and page space."""

    text: str
    x: float                     # text-space offset from the region's left edge
    width: float                 # text-space width
    gap_extra: float             # text-space extra between words (justify)
    justified: bool
    blank: bool
    hard_break: bool
    rect: Tuple[float, float, float, float]
    baseline: Tuple[float, float]


@dataclass
class ParagraphPlan:
    """Everything the preview and the writer need to agree on."""

    paragraph: Paragraph
    lines: Tuple[PlannedLine, ...] = ()
    rect: Tuple[float, float, float, float] = (0, 0, 0, 0)
    region_left: float = 0.0      # page units along the baseline
    region_width: float = 0.0
    font_scale: float = 1.0
    font_size: float = 0.0
    leading: float = 0.0
    #: Inter-word gap in em units (written as a TJ gap, not a space glyph).
    space_advance: float = 0.25
    feasible: bool = True
    problems: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    collisions: Tuple[Collision, ...] = ()
    needs_substitution: bool = False
    missing: Tuple[str, ...] = ()
    substitution_reason: str = ""
    line_count_delta: int = 0
    original_rect: Tuple[float, float, float, float] = (0, 0, 0, 0)
    overflow_bottom: float = 0.0
    overlong_word: bool = False
    alignment: str = "left"

    @property
    def line_count(self) -> int:
        return len(self.lines)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class LayoutOptions:
    """User-visible choices for one reflow."""

    alignment: Optional[str] = None       # None = keep the original
    allow_expand: bool = False
    allow_shrink: bool = False
    min_font_scale: float = 0.7
    bottom_margin: float = 0.0
    side_margin: float = 18.0
    preserve_indent: bool = True


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def _span_info(span: Dict) -> SpanInfo:
    return SpanInfo(
        text=str(span.get("text", "")),
        font=str(span.get("font", "")),
        size=float(span.get("size", 0.0) or 0.0),
        color=int(span.get("color", 0) or 0),
        flags=int(span.get("flags", 0) or 0),
        origin=tuple(span.get("origin", (0.0, 0.0))),  # type: ignore[arg-type]
        bbox=tuple(span.get("bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
        ascender=float(span.get("ascender", 0.8) or 0.8),
        descender=float(span.get("descender", -0.2) or -0.2),
    )


def _run_bbox(run: TextRun) -> Optional[Tuple[float, float, float, float]]:
    if not run.offsets:
        return None
    fs = run.font_size or 1.0
    width = run.offsets[-1]
    asc = 0.85 * fs
    desc = 0.30 * fs
    m = run.trm
    return _aabb([
        _apply(m, 0.0, -asc), _apply(m, width, -asc),
        _apply(m, 0.0, desc), _apply(m, width, desc),
    ])


def _line_baseline(line: Dict) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Return ``(baseline_point, direction)`` for a dict line."""
    spans = line.get("spans") or []
    if spans:
        origin = tuple(spans[0].get("origin", (0.0, 0.0)))
    else:
        bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
        origin = (bbox[0], bbox[3])
    direction = tuple(line.get("dir", (1.0, 0.0)))
    return origin, _unit(direction)


def _extent(rect, u) -> Tuple[float, float]:
    pts = ((rect[0], rect[1]), (rect[2], rect[1]),
           (rect[0], rect[3]), (rect[2], rect[3]))
    vals = [_dot(p, u) for p in pts]
    return min(vals), max(vals)


def _map_line(line: ParagraphLine, runs: Sequence[TextRun],
              origin: Tuple[float, float], u: Tuple[float, float]
              ) -> Tuple[RunPiece, ...]:
    """Find the operators that drew *line*, or ``()`` when they do not match."""
    v = (-u[1], u[0])
    line_lo, line_hi = _extent(line.rect, u)
    candidates: List[Tuple[float, float, RunPiece]] = []
    for run in runs:
        if not run.offsets or not run.text.strip():
            continue
        box = _run_bbox(run)
        if box is None:
            continue
        # The run's baseline must be the line's baseline, measured
        # perpendicular to the writing direction (works for rotated text).
        rel = (run.trm[4] - line.baseline[0], run.trm[5] - line.baseline[1])
        distance = abs(_dot(rel, v))
        if distance > max(1.0, _BASELINE_TOL * max(line.font_size, 1.0)):
            continue
        lo, hi = _extent(box, u)
        if hi < line_lo - 2.0 or lo > line_hi + 2.0:
            continue
        candidates.append((lo, distance, RunPiece(
            source=run.source,
            op_index=run.op_index,
            text=run.text,
            start=0,
            end=len(run.text),
            origin=(run.trm[4], run.trm[5]),
            trm=tuple(run.trm),
            tm=tuple(run.tm) if run.tm else None,
            ctm=tuple(run.ctm) if run.ctm else None,
            font_resource=run.font_resource,
            font_size=run.font_size,
            text_state=run.text_state,
            bbox=box,
        )))
    if not candidates:
        return ()
    candidates.sort(key=lambda item: (item[0], item[1]))
    pieces = tuple(item[2] for item in candidates)
    joined = _norm_text("".join(p.text for p in pieces))
    if joined != _norm_text(line.text):
        return ()
    # A single operator must not be claimed twice by the same line.
    seen = set()
    for piece in pieces:
        if piece.key in seen:
            return ()
        seen.add(piece.key)
    return pieces


def _line_from_dict(index: int, line: Dict) -> ParagraphLine:
    spans = tuple(_span_info(s) for s in line.get("spans", [])
                  if str(s.get("text", "")).strip())
    text = "".join(s.text for s in spans)
    baseline, _direction = _line_baseline(line)
    size = max((s.size for s in spans), default=0.0)
    return ParagraphLine(
        index=index,
        text=text,
        rect=tuple(line.get("bbox", (0.0, 0.0, 0.0, 0.0))),  # type: ignore[arg-type]
        baseline=baseline,
        font=spans[0].font if spans else "",
        font_size=size,
        color=spans[0].color if spans else 0,
        flags=spans[0].flags if spans else 0,
        ascender=max((s.ascender for s in spans), default=0.8),
        descender=min((s.descender for s in spans), default=-0.2),
        spans=spans,
    )


def _same_paragraph(prev: ParagraphLine, line: ParagraphLine,
                    body_lo: float, body_width: float) -> bool:
    """Geometric/typographic evidence that *line* continues *prev*.

    All measurements are projected on the pair's writing direction, so the
    same rules work for horizontal, vertical and rotated text.
    """
    if abs(line.font_size - prev.font_size) > 1.2:
        return False
    if _strip_subset(line.font) != _strip_subset(prev.font):
        return False
    if line.color != prev.color:
        return False
    u = line.direction
    if _dot(u, prev.direction) < 0.99:
        return False
    v = (-u[1], u[0])
    size = max(prev.font_size, line.font_size, 1.0)

    # Distance between baselines along the line normal: adjacent lines are
    # roughly one leading apart, a blank line is much further, and two
    # columns sharing a row are at (almost) the same baseline.
    delta = (line.baseline[0] - prev.baseline[0],
             line.baseline[1] - prev.baseline[1])
    distance = _dot(delta, v)
    if distance < 0.35 * size or distance > 1.7 * size:
        return False

    prev_lo, prev_hi = _extent(prev.rect, u)
    line_lo, line_hi = _extent(line.rect, u)
    prev_w = prev_hi - prev_lo
    line_w = line_hi - line_lo
    narrower = min(prev_w, line_w)
    overlap = min(prev_hi, line_hi) - max(prev_lo, line_lo)
    if narrower > 0 and overlap < 0.35 * narrower:
        return False

    # A short line followed by a full line at the body edge is the classic
    # paragraph break in left-aligned/justified text.
    if (body_width > 0 and prev_w < 0.62 * body_width
            and abs(line_lo - body_lo) < 0.6 * size
            and distance > 0.45 * size):
        return False

    tol = max(1.5, 0.06 * max(prev_w, line_w))
    dl = abs(prev_lo - line_lo)
    dr = abs(prev_hi - line_hi)
    dc = abs((prev_lo + prev_hi) - (line_lo + line_hi)) / 2.0
    if dl <= tol or dr <= tol or dc <= tol:
        return True

    # A first-line indent is the only way two lines with no shared edge can
    # belong together: the indented line starts the paragraph.
    indent = line_lo - body_lo
    if indent > 0.8 * size and body_width > 0 and prev_w < 0.82 * body_width:
        return False
    prev_indent = prev_lo - body_lo
    if prev_indent > 0.8 * size and abs(line_lo - body_lo) < 0.6 * size:
        return True
    return False


def _group_lines(lines: Sequence[ParagraphLine]) -> List[List[ParagraphLine]]:
    if not lines:
        return []
    # Body edges in each writing direction (a page can mix horizontal and
    # rotated text; each frame is measured separately).
    frames: Dict[Tuple[float, float], List[ParagraphLine]] = {}
    for line in lines:
        key = (round(line.direction[0], 2), round(line.direction[1], 2))
        frames.setdefault(key, []).append(line)
    bounds: Dict[Tuple[float, float], Tuple[float, float]] = {}
    for key, group in frames.items():
        extents = [_extent(line.rect, key) for line in group]
        lo = min(e[0] for e in extents)
        hi = max(e[1] for e in extents)
        bounds[key] = (lo, hi - lo)

    groups: List[List[ParagraphLine]] = [[lines[0]]]
    for prev, line in zip(lines, lines[1:]):
        key = (round(line.direction[0], 2), round(line.direction[1], 2))
        body_lo, body_width = bounds.get(key, (0.0, 0.0))
        if _same_paragraph(prev, line, body_lo, body_width):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _alignment(lines: Sequence[ParagraphLine], u: Tuple[float, float]) -> str:
    if len(lines) <= 1:
        return "left"
    extents = [_extent(line.rect, u) for line in lines]
    lefts = [e[0] for e in extents]
    rights = [e[1] for e in extents]
    body_left, body_right = min(lefts), max(rights)
    width = body_right - body_left
    if width <= 0:
        return "left"
    tol = max(0.02 * width, 1.0)
    left_spread = max(lefts) - min(lefts)
    right_spread = max(rights) - min(rights)
    centres = [(lo + hi) / 2.0 for lo, hi in zip(lefts, rights)]
    centre_spread = max(centres) - min(centres)

    if centre_spread < tol and (left_spread > tol or right_spread > tol):
        return "center"
    if right_spread < tol and left_spread > tol:
        return "right"
    if left_spread < tol:
        # All lines share the left edge.  Justification is only inferred when
        # at least three lines share the right edge and the last one is
        # clearly shorter; two lines are ambiguous and stay left-aligned.
        if (len(lines) >= 3
                and all(abs(rights[i] - body_right) < tol
                        for i in range(len(lines) - 1))
                and rights[-1] < body_right - max(2.0, 0.1 * width)):
            return "justify"
        return "left"
    return "left"


def _analyse_style(lines: Sequence[ParagraphLine]) -> Tuple[List[str], str, float, int, int, float, float]:
    """Return ``(problems, font, size, color, flags, ascender, descender)``."""
    problems: List[str] = []
    spans = [s for line in lines for s in line.spans if s.text.strip()]
    if not spans:
        return (["The paragraph has no visible text."], "", 0.0, 0, 0, 0.8, -0.2)

    first = spans[0]
    names = {_strip_subset(s.font) for s in spans}
    sizes = {round(s.size, 1) for s in spans}
    colours = {s.color for s in spans}
    bold = {bool(s.flags & 16) for s in spans}
    italic = {bool(s.flags & 2) for s in spans}

    if len(names) > 1:
        problems.append(
            "The paragraph mixes fonts (" + ", ".join(sorted(names)) + "). "
            "Editing it as one block would lose the other styles; edit the "
            "individual lines instead.")
    if len(sizes) > 1:
        problems.append(
            "The paragraph mixes font sizes ("
            + ", ".join(f"{s:g}" for s in sorted(sizes))
            + " pt). Editing it as one block would change the appearance.")
    if len(colours) > 1:
        problems.append(
            "The paragraph uses more than one text colour, so it cannot be "
            "rebuilt as a single block.")
    if len(bold) > 1 or len(italic) > 1:
        problems.append(
            "The paragraph mixes bold/italic runs, so it cannot be rebuilt "
            "with one font style.")
    return (problems, first.font, first.size, first.color, first.flags,
            max(s.ascender for s in spans), min(s.descender for s in spans))


def _paragraph_from_lines(page: fitz.Page, lines: Sequence[ParagraphLine],
                          confidence: float,
                          notes: Sequence[str] = ()) -> Paragraph:
    first = lines[0]
    rect = first.rect
    for line in lines[1:]:
        rect = _union(rect, line.rect)
    problems, font, size, colour, flags, asc, desc = _analyse_style(lines)

    pieces = [p for line in lines for p in line.pieces]
    if not pieces:
        problems.append(
            "The text could not be matched to the content stream. It may be "
            "drawn by an unsupported structure or a different font encoding.")
        origin = first.baseline
        u = (1.0, 0.0)
        trm = (1.0, 0.0, 0.0, 1.0, origin[0], origin[1])
        tm = None
        angle = 0.0
        font_resource = ""
        res_owner = page.xref
        ts = TextState()
    else:
        first_piece = pieces[0]
        origin = first_piece.origin
        trm = first_piece.trm
        tm = first_piece.tm
        u = _unit((trm[0], trm[1]))
        angle = math.atan2(trm[1], trm[0])
        # A rotated block is often drawn with the rotation in the enclosing
        # ``cm`` (graphics state) while the text matrix itself stays axis
        # aligned.  Detect that too so such paragraphs are recognised as
        # rotated and not silently treated as horizontal.
        if abs(angle) < 1e-3 and first_piece.ctm is not None:
            ctm_angle = math.atan2(first_piece.ctm[1], first_piece.ctm[0])
            if abs(ctm_angle) > 1e-3:
                angle = ctm_angle
        font_resource = first_piece.font_resource
        res_owner = first_piece.source.res_owner
        ts = first_piece.text_state

    if tm is None and pieces:
        problems.append(
            "The text matrix of the paragraph could not be recovered, so a "
            "new layout cannot be written in the same coordinate system.")

    if len({p.source.xref for p in pieces}) > 1:
        problems.append(
            "The paragraph is drawn across more than one content stream. "
            "Rebuilding it would require reordering streams, which this phase "
            "does not do.")
    if any(p.text_state.render_mode != 0 for p in pieces):
        problems.append(
            "The paragraph uses a non-filling text render mode (invisible or "
            "stroked text), so it cannot be reflowed safely.")

    # Alignment and body edges, projected along the writing direction.
    extents = [_extent(line.rect, u) for line in lines]
    body_left = min(e[0] for e in extents)
    body_right = max(e[1] for e in extents)
    alignment = _alignment(lines, u)

    # Leading: median baseline distance, projected on the line normal.
    v = (-u[1], u[0])
    gaps: List[float] = []
    for prev, line in zip(lines, lines[1:]):
        rel = (line.baseline[0] - prev.baseline[0],
               line.baseline[1] - prev.baseline[1])
        gap = _dot(rel, v)
        if gap > 0.1:
            gaps.append(gap)
    if gaps:
        leading = _median(gaps)
    else:
        scale = _norm_len((trm[2], trm[3])) or 1.0
        leading = 1.2 * max(size, 1.0) * scale

    # First-line indent relative to the body edge (left-aligned text only).
    indent = 0.0
    if alignment in ("left", "justify"):
        indent_page = extents[0][0] - body_left
        if indent_page > 0.35 * max(size, 1.0):
            indent = indent_page

    return Paragraph(
        page_num=page.number,
        block_number=first.block_number,
        first_index=first.global_index,
        last_index=lines[-1].global_index,
        line_keys=tuple(line.key for line in lines),
        lines=tuple(lines),
        rect=rect,
        text="\n".join(line.text for line in lines),
        alignment=alignment,
        leading=leading,
        font=font,
        font_size=size,
        color=colour,
        flags=flags,
        ascender=asc,
        descender=desc,
        angle=angle,
        confidence=confidence,
        problems=tuple(problems),
        notes=tuple(notes),
        body_left=body_left,
        body_right=body_right,
        indent=indent,
        origin=origin,
        trm=trm,
        tm=tm,
        font_resource=font_resource,
        res_owner=res_owner,
        char_spacing=ts.char_spacing,
        word_spacing=ts.word_spacing,
        h_scale=ts.h_scale or 1.0,
        rise=ts.rise,
        render_mode=ts.render_mode,
    )


def _dict_blocks(page: fitz.Page) -> List[Dict]:
    try:
        data = page.get_text("dict")
    except Exception:
        return []
    return [b for b in data.get("blocks", []) if b.get("type") == 0]


def _page_lines(page: fitz.Page,
                runs: Optional[Sequence[TextRun]] = None,
                ) -> List[ParagraphLine]:
    """Every text line on the page, in reading order, mapped to operators.

    PyMuPDF blocks are a useful hint but not a paragraph model: centred and
    right-aligned paragraphs are routinely split into one block per line.
    Lines are therefore collected page-wide and grouped afterwards.
    """
    if runs is None:
        runs = iter_page_runs(page)
    out: List[ParagraphLine] = []
    for block in _dict_blocks(page):
        number = int(block.get("number", -1))
        for index, raw in enumerate(block.get("lines") or []):
            line = _line_from_dict(index, raw)
            if not line.text.strip():
                continue
            direction = _unit(tuple(raw.get("dir", (1.0, 0.0))))
            pieces = _map_line(line, runs, line.baseline, direction)
            out.append(ParagraphLine(
                index=index, text=line.text, rect=line.rect,
                baseline=line.baseline, font=line.font,
                font_size=line.font_size, color=line.color,
                flags=line.flags, ascender=line.ascender,
                descender=line.descender, spans=line.spans,
                pieces=pieces, block_number=number,
                global_index=len(out), direction=direction))
    return out


def page_lines(page: fitz.Page,
               runs: Optional[Sequence[TextRun]] = None,
               ) -> List[ParagraphLine]:
    """Public accessor for the page-wide line list (dialog line ranges)."""
    return _page_lines(page, runs)


def line_count(page: fitz.Page) -> int:
    """Number of visible text lines on the page."""
    return len(_page_lines(page))


def extract_paragraphs(page: fitz.Page,
                       runs: Optional[Sequence[TextRun]] = None,
                       ) -> List[Paragraph]:
    """Group the page's lines into paragraphs and map them to operators."""
    lines = _page_lines(page, runs)
    if not lines:
        return []
    groups = _group_lines(lines)
    out: List[Paragraph] = []
    for group in groups:
        confidence = 0.9 if len(group) > 1 else 0.6
        notes: List[str] = []
        if len(group) == 1:
            notes.append("A single line: check that the selection covers the "
                         "whole paragraph.")
        if len({line.block_number for line in group}) > 1:
            notes.append("The paragraph was assembled from several text "
                         "blocks; check the boundaries.")
        out.append(_paragraph_from_lines(page, group, confidence, notes))
    return out


def extract_range(page: fitz.Page, first_index: int, last_index: int,
                  runs: Optional[Sequence[TextRun]] = None) -> Paragraph:
    """Re-extract the page lines ``[first_index, last_index]`` (inclusive)."""
    if first_index < 0 or last_index < first_index:
        raise ParagraphUnsupported("The selected line range is invalid.")
    lines = _page_lines(page, runs)
    if last_index >= len(lines):
        raise ParagraphUnsupported(
            "The selected line range is outside the page. The document may "
            "have changed since the paragraph was selected.")
    selected = lines[first_index:last_index + 1]
    if not selected:
        raise ParagraphUnsupported("The selected lines contain no visible text.")
    notes: List[str] = []
    if len(selected) == 1:
        notes.append("A single line: check that the selection covers the "
                     "whole paragraph.")
    return _paragraph_from_lines(page, selected, 1.0, notes)


def paragraph_at(page: fitz.Page, block_number: int, line_index: int,
                 paragraphs: Optional[Sequence[Paragraph]] = None,
                 ) -> Optional[Paragraph]:
    """Return the paragraph that contains a given block line."""
    if paragraphs is None:
        paragraphs = extract_paragraphs(page)
    key = (block_number, line_index)
    for para in paragraphs:
        if key in para.line_keys:
            return para
    return None


def paragraph_for_word(page: fitz.Page, hit,
                       paragraphs: Optional[Sequence[Paragraph]] = None,
                       ) -> Optional[Paragraph]:
    """Return the paragraph containing a :class:`WordHit`."""
    return paragraph_at(page, hit.block_no, hit.line_no, paragraphs)


# --------------------------------------------------------------------------- #
# Font metrics for layout
# --------------------------------------------------------------------------- #
@dataclass
class _Metrics:
    advance: Callable[[str], Optional[float]]   # char -> em advance
    missing: Tuple[str, ...] = ()
    needs_substitution: bool = False
    reason: str = ""
    codec: object = None
    #: Advance of one inter-word gap, in em units.
    space: float = 0.25


_ADVANCE_CACHE: Dict[Tuple[str, int], Dict[str, float]] = {}


def _face_advance_map(face, chars: Iterable[str]) -> Dict[str, float]:
    """Per-character advances (em units) for *face*, cached across previews."""
    from .font_discovery import face_advances

    key = (face.path, face.index)
    cache = _ADVANCE_CACHE.setdefault(key, {})
    wanted = [ch for ch in dict.fromkeys(chars) if ch not in cache]
    if not wanted:
        return cache
    advances = face_advances(face, "".join(wanted))
    if advances is not None:
        for ch, value in zip(wanted, advances):
            cache[ch] = float(value)
        return cache
    # One or more characters are missing; find out which, so the rest cache.
    for ch in wanted:
        single = face_advances(face, ch)
        if single is not None:
            cache[ch] = float(single[0])
    return cache


def _coverage_chars(text: str) -> Tuple[str, ...]:
    """Characters that need a glyph.

    Layout whitespace (newlines, tabs) and the ordinary space are excluded:
    spaces are written as ``TJ`` gaps, which is how producers draw them in the
    first place and avoids requiring a space glyph the font may have dropped.
    """
    return tuple(ch for ch in dict.fromkeys(text)
                 if ch not in "\n\r\t ")


def _original_metrics(page: fitz.Page, para: Paragraph, text: str) -> _Metrics:
    try:
        codec = page_font_codecs(page, para.res_owner)(para.font_resource)
        info, font = page_font_programs(page, para.res_owner)(para.font_resource)
    except Exception:
        codec = None
        info = font = None
    widths = font_widths(page.parent, info) if info is not None else None
    if codec is None or (font is None and widths is None):
        return _Metrics(
            advance=lambda _ch: None,
            needs_substitution=True,
            reason=("The original font program or its encoding could not be "
                    "read, so the replacement cannot be measured with it."),
            codec=codec,
        )

    missing: List[str] = []
    for ch in _coverage_chars(text):
        code = codec.unicode_to_code.get(ch)
        if code is None:
            missing.append(ch)
            continue
        if widths is not None and code in widths[0]:
            continue
        if font is not None and font.has_glyph(ord(ch)):
            continue
        missing.append(ch)

    space = 0.25
    if widths is not None:
        code = codec.unicode_to_code.get(" ")
        if code is not None:
            value = widths[0].get(code)
            if value and value > 0:
                space = float(value)
        if space == 0.25 and widths[1] > 0:
            space = float(widths[1])
    if space == 0.25 and font is not None and font.has_glyph(32):
        space = float(font.glyph_advance(32))
    if space <= 0.0:
        space = 0.25

    def advance(ch: str) -> Optional[float]:
        if ch == " ":
            return space
        code = codec.unicode_to_code.get(ch)
        if widths is not None and code is not None:
            value = widths[0].get(code)
            if value is not None and value > 0.0:
                return value
        if font is not None and font.has_glyph(ord(ch)):
            return font.glyph_advance(ord(ch))
        return None

    return _Metrics(
        advance=advance,
        missing=tuple(missing),
        needs_substitution=bool(missing),
        reason=(f"The original font '{_strip_subset(para.font)}' cannot draw "
                + ", ".join(repr(c) for c in missing) + ".") if missing else "",
        codec=codec,
        space=space,
    )


def _face_metrics(face, text: str) -> _Metrics:
    chars = tuple(ch for ch in _coverage_chars(text) if ch != " ")
    table = _face_advance_map(face, chars)
    missing = tuple(ch for ch in chars if ch not in table)
    space = table.get(" ", 0.25) or 0.25
    return _Metrics(
        advance=lambda ch: space if ch == " " else table.get(ch),
        missing=missing,
        needs_substitution=False,
        reason="",
        space=space,
    )


def resolve_metrics(page: fitz.Page, para: Paragraph, text: str,
                    face=None) -> _Metrics:
    """Choose the font that will draw *text* and expose its advances."""
    if face is not None:
        return _face_metrics(face, text)
    return _original_metrics(page, para, text)


# --------------------------------------------------------------------------- #
# Line breaking
# --------------------------------------------------------------------------- #
def _char_width(metrics: _Metrics, ch: str, font_size: float,
                char_spacing: float, word_spacing: float,
                h_scale: float) -> Optional[float]:
    adv = metrics.advance(ch)
    if adv is None:
        return None
    extra = char_spacing + (word_spacing if ch == " " else 0.0)
    return (adv * font_size + extra) * h_scale


def _text_width(metrics: _Metrics, text: str, font_size: float,
                char_spacing: float, word_spacing: float,
                h_scale: float) -> Optional[float]:
    total = 0.0
    for ch in text:
        width = _char_width(metrics, ch, font_size, char_spacing,
                            word_spacing, h_scale)
        if width is None:
            return None
        total += width
    return total


@dataclass
class _Wrapped:
    text: str
    width: float
    blank: bool
    hard_break: bool
    gap_extra: float = 0.0
    justified: bool = False


def _wrap(metrics: _Metrics, text: str, width: float, font_size: float,
          char_spacing: float, word_spacing: float, h_scale: float,
          alignment: str, indent: float) -> Tuple[List[_Wrapped], bool]:
    """Greedy wrap *text* to *width* (text-space units).

    Returns the lines and whether a single word was wider than the region.
    """
    out: List[_Wrapped] = []
    overlong = False
    space_w = _text_width(metrics, " ", font_size, char_spacing,
                          word_spacing, h_scale) or 0.0
    source_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    first_emitted = True

    for source in source_lines:
        words = source.split()
        if not words:
            out.append(_Wrapped("", 0.0, True, True))
            first_emitted = False
            continue
        current: List[str] = []
        current_w = 0.0

        def available() -> float:
            # Only the very first visual line of the paragraph is indented.
            value = width - (indent if first_emitted else 0.0)
            return value if value > 1.0 else width

        def emit(parts: List[str], part_w: float, hard: bool) -> None:
            nonlocal first_emitted
            line_text = " ".join(parts)
            out.append(_Wrapped(line_text, part_w, False, hard))
            first_emitted = False

        for word in words:
            avail = available()
            word_w = _text_width(metrics, word, font_size, char_spacing,
                                 word_spacing, h_scale)
            if word_w is None:
                # The metric is incomplete; substitution should have been
                # requested, but keep the word so the caller can report it.
                word_w = 0.0
                overlong = True
            if current and current_w + space_w + word_w > avail + 1e-6:
                emit(current, current_w, False)
                current = [word]
                current_w = word_w
            else:
                if current:
                    current_w += space_w
                current.append(word)
                current_w += word_w
            if word_w > avail + 1e-6:
                overlong = True
        emit(current, current_w, True)

    if alignment == "justify":
        for line in out:
            if line.blank or line.hard_break:
                continue
            gaps = line.text.count(" ")
            if gaps <= 0:
                continue
            avail = width
            extra = avail - line.width
            if extra > 0.5:
                line.gap_extra = extra / gaps
                line.justified = True
    return out, overlong


def _align_x(line: _Wrapped, width: float, alignment: str,
             indent: float, first: bool) -> float:
    start = indent if (first and alignment in ("left", "justify")) else 0.0
    avail = width - start
    if alignment == "right":
        return max(start, width - line.width)
    if alignment == "center":
        return start + max(0.0, (avail - line.width) / 2.0)
    return start


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
def _page_obstacles(page: fitz.Page, para: Paragraph,
                    ) -> List[Tuple[Tuple[float, float, float, float], str, str]]:
    """Everything on the page that new text must not touch."""
    out: List[Tuple[Tuple[float, float, float, float], str, str]] = []
    page_area = max(1.0, page.rect.width * page.rect.height)

    # Text: every line outside the paragraph's own selected lines.
    skip = set(para.line_keys)
    for block in _dict_blocks(page):
        number = int(block.get("number", -1))
        for index, line in enumerate(block.get("lines") or []):
            if (number, index) in skip:
                continue
            bbox = line.get("bbox")
            if not bbox:
                continue
            text = "".join(s.get("text", "") for s in line.get("spans", []))
            if not text.strip():
                continue
            out.append((tuple(bbox), "text", text.strip()[:40]))

    # Raster images.
    try:
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if bbox:
                out.append((tuple(bbox), "image", "image"))
    except Exception:
        pass

    # Vector drawings: skip page-sized or white backgrounds.
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for drawing in drawings:
        rect = drawing.get("rect")
        if not rect:
            continue
        rect = tuple(rect)
        if _rect_area(rect) > 0.45 * page_area:
            continue
        # A filled shape that contains the paragraph is the panel the text
        # sits on, not an obstacle.  A stroked-only rectangle is a border and
        # stays, so text cannot grow out of a table cell unnoticed.
        if drawing.get("fill") and _contains(rect, para.rect, pad=0.5):
            continue
        out.append((rect, "drawing", "drawing"))
    return out


def _clear_to_right(page: fitz.Page, para: Paragraph, body_right: float,
                    limit: float,
                    obstacles: Sequence[Tuple[Tuple[float, float, float, float], str, str]],
                    ) -> bool:
    """True when nothing occupies the band to the right of the paragraph."""
    band = para.rect
    for rect, _kind, _label in obstacles:
        if rect[0] < body_right - 0.5 or rect[0] >= limit:
            continue
        if rect[3] <= band[1] + 0.5 or rect[1] >= band[3] - 0.5:
            continue
        return False
    return True


def _build_lines(metrics: _Metrics, text: str, width: float,
                 font_size: float, leading: float,
                 para: Paragraph, alignment: str, indent: float,
                 left_off: float,
                 ) -> Tuple[List[PlannedLine], bool]:
    wrapped, overlong = _wrap(
        metrics, text, width, font_size, para.char_spacing, para.word_spacing,
        para.h_scale, alignment, indent)

    trm = para.trm
    origin = para.origin
    asc = para.ascender * font_size
    desc = para.descender * font_size
    leading_text = leading / (para.normal_scale or 1.0)

    lines: List[PlannedLine] = []
    for index, item in enumerate(wrapped):
        # ``x`` is absolute in text space, measured from the Tm origin: the
        # region's left edge is ``left_off`` and the alignment offset is added.
        x = left_off + _align_x(item, width, alignment, indent, index == 0)
        # Text-space origin of this line, then page space.
        bx = origin[0] + trm[0] * x + trm[2] * (-leading_text * index)
        by = origin[1] + trm[1] * x + trm[3] * (-leading_text * index)
        corners = [
            (bx + trm[0] * u + trm[2] * v, by + trm[1] * u + trm[3] * v)
            for u in (0.0, item.width) for v in (desc, asc)
        ]
        rect = _aabb(corners)
        lines.append(PlannedLine(
            text=item.text, x=x, width=item.width,
            gap_extra=item.gap_extra, justified=item.justified,
            blank=item.blank, hard_break=item.hard_break,
            rect=rect, baseline=(bx, by)))
    return lines, overlong


def _collisions(lines: Sequence[PlannedLine],
                obstacles: Sequence[Tuple[Tuple[float, float, float, float], str, str]],
                ) -> Tuple[Collision, ...]:
    found: List[Collision] = []
    for line in lines:
        if line.blank:
            continue
        probe = _expand(line.rect, -0.5)
        if probe[2] <= probe[0] or probe[3] <= probe[1]:
            probe = line.rect
        for rect, kind, label in obstacles:
            inter = _intersect(probe, rect)
            if inter is None or _rect_area(inter) <= 0.75:
                continue
            found.append(Collision(rect=tuple(rect), kind=kind, label=label))
    # De-duplicate identical obstacles.
    unique: List[Collision] = []
    seen = set()
    for item in found:
        key = (item.rect, item.kind, item.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def _attempt(metrics: _Metrics, para: Paragraph, text: str, width: float,
             font_size: float, leading: float, alignment: str, indent: float,
             obstacles, page: fitz.Page, bottom_limit: float,
             left_off: float = 0.0,
             ) -> ParagraphPlan:
    lines, overlong = _build_lines(
        metrics, text, width, font_size, leading, para, alignment, indent,
        left_off)
    rect = lines[0].rect if lines else para.rect
    for line in lines[1:]:
        rect = _union(rect, line.rect)
    collisions = _collisions(lines, obstacles)

    bottom = max((line.rect[3] for line in lines), default=para.rect[3])
    overflow = max(0.0, bottom - bottom_limit)
    problems: List[str] = []
    warnings: List[str] = []

    if overflow > 0.5:
        problems.append(
            f"The reflowed paragraph would extend {overflow:.1f} pt past the "
            f"bottom of the available area.")
    if collisions:
        kinds = sorted({c.kind for c in collisions})
        problems.append(
            "The reflowed paragraph would overlap existing content ("
            + ", ".join(kinds) + ").")
    if overlong:
        warnings.append(
            "At least one word is wider than the text region and cannot be "
            "split. Widen the region or shorten the word.")
    if rect[2] > page.rect.x1 - 1.0 or rect[0] < page.rect.x0 + 1.0:
        problems.append("The reflowed text would run past the page edge.")
    if not lines:
        problems.append("The paragraph text is empty.")

    return ParagraphPlan(
        paragraph=para,
        lines=tuple(lines),
        rect=rect,
        feasible=not problems,
        problems=tuple(problems),
        warnings=tuple(warnings),
        collisions=collisions,
        line_count_delta=len(lines) - para.line_count,
        original_rect=para.rect,
        overflow_bottom=overflow,
        overlong_word=overlong,
        alignment=alignment,
        font_size=font_size,
        leading=leading,
        space_advance=metrics.space,
    )


def plan_reflow(page: fitz.Page, para: Paragraph, text: str,
                options: Optional[LayoutOptions] = None,
                face=None,
                region_width: Optional[float] = None,
                ) -> ParagraphPlan:
    """Lay *text* out inside *para*'s region and report fit/collisions.

    The same function is used by the preview dialog and by the edit, so what
    the user sees is what will be written.  It never modifies *page*.
    """
    options = options or LayoutOptions()
    if para.problems:
        raise ParagraphUnsupported(" ".join(para.problems))
    if para.tm is None:
        raise ParagraphUnsupported(
            "The text matrix of the paragraph could not be recovered.")
    if not text.strip():
        raise ParagraphUnsupported("The paragraph text is empty.")

    metrics = resolve_metrics(page, para, text, face)
    alignment = options.alignment or para.alignment
    if alignment not in ("left", "center", "right", "justify"):
        alignment = "left"

    if metrics.needs_substitution and face is None:
        # The original font cannot draw the text.  Laying it out with missing
        # metrics would produce a preview that cannot be committed, so stop
        # here and let the caller choose a substitute.
        return ParagraphPlan(
            paragraph=para,
            feasible=False,
            problems=(metrics.reason or
                      "The original font cannot draw the new text.",),
            needs_substitution=True,
            missing=metrics.missing,
            substitution_reason=metrics.reason,
            original_rect=para.rect,
            alignment=alignment,
            font_size=para.font_size,
            leading=para.leading,
            space_advance=metrics.space,
        )

    obstacles = _page_obstacles(page, para)
    scale = para.baseline_scale or 1.0

    # Region: measured along the baseline from the paragraph's origin.
    body_width = (para.body_right - para.body_left) / scale
    left_off = (para.body_left - _dot(para.origin, _unit(
        (para.trm[0], para.trm[1])))) / scale
    width_text = body_width
    if region_width is not None:
        width_text = max(1.0, float(region_width) / scale)
    elif alignment == "left" and options.allow_expand and not para.rotated:
        limit = page.rect.x1 - options.side_margin
        candidate = (limit - para.body_left) / scale
        if candidate > body_width and _clear_to_right(
                page, para, para.body_right, limit, obstacles):
            width_text = candidate
    width_text = max(width_text, body_width) + _REGION_SLACK / scale

    indent_text = (para.indent / scale) if options.preserve_indent else 0.0
    bottom_limit = page.rect.y1 - options.bottom_margin

    plan = _attempt(
        metrics, para, text, width_text, para.font_size, para.leading,
        alignment, indent_text, obstacles, page, bottom_limit, left_off)
    plan.region_left = left_off
    plan.region_width = width_text * scale
    plan.needs_substitution = metrics.needs_substitution
    plan.missing = metrics.missing
    plan.substitution_reason = metrics.reason
    plan.space_advance = metrics.space

    if plan.feasible or not options.allow_shrink:
        return plan

    # Explicitly approved font-size reduction: try the smallest scale that
    # fits, never below the configured floor.
    first_baseline = plan.lines[0].baseline[1] if plan.lines else para.rect[1]
    available = max(1.0, bottom_limit - first_baseline)
    needed = max(1.0, plan.rect[3] - first_baseline)
    estimate = max(options.min_font_scale,
                   min(0.99, available / needed))
    candidates = sorted({estimate, 0.9, 0.8, options.min_font_scale},
                        reverse=True)
    best = plan
    for candidate in candidates:
        if candidate >= 0.999:
            continue
        attempt = _attempt(
            metrics, para, text, width_text, para.font_size * candidate,
            para.leading * candidate, alignment, indent_text * candidate,
            obstacles, page, bottom_limit, left_off)
        attempt.region_left = left_off
        attempt.region_width = width_text * scale
        attempt.needs_substitution = metrics.needs_substitution
        attempt.missing = metrics.missing
        attempt.substitution_reason = metrics.reason
        attempt.font_scale = candidate
        if attempt.feasible:
            attempt.warnings = attempt.warnings + (
                f"The font size was reduced to {candidate:.0%} of the "
                f"original at your request.",)
            return attempt
        best = attempt
    best.warnings = best.warnings + (
        "Even the smallest approved font size does not fit.",)
    return best


# --------------------------------------------------------------------------- #
# Font substitution helpers for the UI
# --------------------------------------------------------------------------- #
def font_query_for(para: Paragraph, text: str, advances: Sequence[float] = ()):
    """Build a :class:`font_discovery.FontQuery` describing the paragraph."""
    from .font_discovery import FontQuery

    return FontQuery(
        basefont=para.font,
        bold=para.bold,
        italic=para.italic,
        serif=para.serif,
        monospaced=para.monospaced,
        text=text,
        advances=tuple(advances),
    )


def original_advances(page: fitz.Page, para: Paragraph, text: str,
                      ) -> Tuple[float, ...]:
    """Per-character em advances of *text* in the paragraph's own font."""
    metrics = _original_metrics(page, para, text)
    out: List[float] = []
    for ch in text:
        adv = metrics.advance(ch)
        if adv is None:
            return ()
        out.append(float(adv))
    return tuple(out)
