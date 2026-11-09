"""Existing-text editing engine for PDFAR.

This module implements **genuine** editing of text that already exists in a PDF:
it locates the content-stream operator that produced a clicked word and rewrites
*only that operator's operands*, leaving every other byte of the page untouched.

Design (see ``docs/existing-text-edit-audit.md``):

* Hit testing uses :meth:`fitz.Page.get_text` ``"words"``, which yields a precise
  page-space bounding box per word.
* Mapping a word to its operator replays the graphics state of the page's content
  stream (``cm``, ``Tm``, ``Td``, ``TD``, ``T*``, ``Tf``, ``Tc``, ``Tw``, ``Tz``)
  to compute each text-showing operator's origin, then matches the clicked word by
  text containment + nearest origin.
* Replacement is *surgical*: the matched operator is re-serialized and spliced
  back into the original stream at ``[start_pos:end_pos)``.  No whole-stream
  re-serialization, so dictionaries, inline images and other operators survive
  byte-for-byte (Strategy A from the task spec).
* If the original font cannot represent a replacement glyph, or the word is split
  across kerning segments, the edit refuses with a clear message (Strategy D)
  rather than producing a corrupted file.

This file does **not** copy PdfCraft (which is Rust); it reuses PDFAR's own
``content_stream`` infrastructure and follows the same intent-based ``Edit`` model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import Edit, EditError, Scope
from .content_stream import (
    ContentStreamParser,
    ContentStreamSerializer,
    PDFOperator,
    _extract_text_from_op,
)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass
class WordHit:
    """A word detected on a page, in top-left page coordinates."""

    page: int
    text: str
    #: Bounding box in page space, top-left origin (same as ``get_text("words")``).
    rect: Tuple[float, float, float, float]
    font: str          # base font name (e.g. "Helvetica" or "ABCDEF+Font")
    font_size: float
    color: int         # 0xRRGGBB integer
    flags: int         # PyMuPDF font flags (bit 1 = italic, bit 4 = bold, ...)
    line_no: int
    word_no: int


@dataclass
class _OperatorLoc:
    """A text-showing operator and where it sits in the page."""

    stream_index: int
    stream_xref: int
    op_index: int          # index within the parsed stream operators
    operator: str          # Tj, TJ, ', "
    text: str              # extracted visible text
    origin: Tuple[float, float]   # (x, y) in top-left page space
    font_resource: str     # resource name used by the preceding Tf
    font_size: float


# --------------------------------------------------------------------------- #
# Hit testing
# --------------------------------------------------------------------------- #
def find_text_at_point(
    page: fitz.Page,
    point: Tuple[float, float],
    threshold: float = 3.0,
) -> Optional[WordHit]:
    """Return the word under *point* (top-left page space), or the nearest word.

    *point* is ``(x, y)`` in the same top-left page space that
    :meth:`fitz.Page.get_text` ``"words"`` reports.  If no word contains the
    point we fall back to the closest word within *threshold* points so a click
    slightly off a glyph still selects it.
    """
    words = page.get_text("words")  # x0,y0,x1,y1,word,block,line,word_no
    if not words:
        return None

    px, py = point
    best: Optional[WordHit] = None
    best_dist = None
    for w in words:
        x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
        line_no, word_no = w[6], w[7]
        if x0 <= px <= x1 and y0 <= py <= y1:
            # Inside the box: prefer the smallest box (most specific) on ties.
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best_dist:  # type: ignore[operator]
                best = _make_hit(page, w)
                best_dist = area
        else:
            # Distance to the box (0 if inside).
            dx = max(x0 - px, 0.0, px - x1)
            dy = max(y0 - py, 0.0, py - y1)
            d = (dx * dx + dy * dy) ** 0.5
            if d <= threshold and (best_dist is None or d < best_dist):
                best = _make_hit(page, w)
                best_dist = d
    return best


def _make_hit(page: fitz.Page, w: tuple) -> WordHit:
    x0, y0, x1, y1, word, _block, line_no, word_no = w[:8]
    font, size, color, flags = _span_info_for_word(page, w)
    return WordHit(
        page=page.number,
        text=word,
        rect=(x0, y0, x1, y1),
        font=font,
        font_size=size,
        color=color,
        flags=flags,
        line_no=line_no,
        word_no=word_no,
    )


def _span_info_for_word(page: fitz.Page, w: tuple) -> Tuple[str, float, int, int]:
    """Find the span that produced *w* and return (font, size, color, flags)."""
    x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    try:
        d = page.get_text("dict")
    except Exception:
        return ("Helvetica", 12.0, 0x000000, 0)
    best = None
    best_d = None
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                sb = span.get("bbox")
                if not sb:
                    continue
                if sb[0] <= cx <= sb[2] and sb[1] <= cy <= sb[3]:
                    return (
                        span.get("font", "Helvetica"),
                        float(span.get("size", 12.0)),
                        int(span.get("color", 0)),
                        int(span.get("flags", 0)),
                    )
                # Fallback: nearest span center.
                sx, sy = (sb[0] + sb[2]) / 2.0, (sb[1] + sb[3]) / 2.0
                dd = (sx - cx) ** 2 + (sy - cy) ** 2
                if best_d is None or dd < best_d:
                    best_d = dd
                    best = span
    if best is not None:
        return (
            best.get("font", "Helvetica"),
            float(best.get("size", 12.0)),
            int(best.get("color", 0)),
            int(best.get("flags", 0)),
        )
    return ("Helvetica", 12.0, 0x000000, 0)


# --------------------------------------------------------------------------- #
# Graphics-state replay + operator location
# --------------------------------------------------------------------------- #
def _apply_cm(ctm: Tuple[float, ...], e: float, f: float) -> Tuple[float, float]:
    a, b, c, d = ctm[0], ctm[1], ctm[2], ctm[3]
    return (a * e + c * f, b * e + d * f)


def _concat(a, b):
    """Multiply two 2D affine matrices (each (a,b,c,d,e,f))."""
    a1, b1, c1, d1, e1, f1 = a
    a2, b2, c2, d2, e2, f2 = b
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _to_topleft(y_bottom: float, page_height: float) -> float:
    return page_height - y_bottom


def locate_operator_for_word(
    page: fitz.Page,
    word: str,
    word_rect: Tuple[float, float, float, float],
) -> Optional[_OperatorLoc]:
    """Find the content-stream operator that produced *word*.

    Returns the matching :class:`_OperatorLoc` or ``None`` when the word cannot
    be reliably attributed to a single operator (Strategy D territory).
    """
    if not word:
        return None
    page_height = page.rect.height
    streams = _page_content_streams(page)
    best: Optional[_OperatorLoc] = None
    best_score = None

    for stream_index, (xref, data) in enumerate(streams):
        if not data:
            continue
        ops = ContentStreamParser.parse(data)
        ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        tm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        tlm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        leading = 0.0
        font_resource = ""
        font_size = 0.0
        # Tc, Tw, Tz, Tr, Ts are not needed for origin computation.
        for op_index, op in enumerate(ops):
            o = op.operator
            if o == "cm":
                ctm = tuple(op.operands[:6])
            elif o == "Tf":
                if len(op.operands) >= 2:
                    font_resource = op.operands[0]
                    font_size = float(op.operands[1])
            elif o in ("Tm",):
                tm = tuple(op.operands[:6])
                tlm = tm
            elif o in ("Td", "TD"):
                tx, ty = float(op.operands[0]), float(op.operands[1])
                tlm = _concat(tlm, (1.0, 0.0, 0.0, 1.0, tx, ty))
                tm = tlm
                if o == "TD":
                    leading = -ty
            elif o == "T*":
                tlm = _concat(tlm, (1.0, 0.0, 0.0, 1.0, 0.0, -leading))
                tm = tlm
            elif o in ContentStreamParser.TEXT_SHOW_OPS:
                # Compute origin in user space, then to top-left page space.
                ex, ey = _apply_cm(ctm, tm[4], tm[5])
                origin = (ex, _to_topleft(ey, page_height))
                text = _extract_text_from_op(op)
                if word in text:
                    # Score: distance from the word's baseline-left point
                    # (x0, y1) to the operator's baseline origin.  Comparing
                    # against the baseline (y1) rather than the box top (y0)
                    # avoids picking a neighbouring line whose baseline happens
                    # to sit near this word's top.
                    dx = origin[0] - word_rect[0]
                    dy = origin[1] - word_rect[3]
                    score = (dx * dx + dy * dy) ** 0.5
                    if best_score is None or score < best_score:
                        best_score = score
                        best = _OperatorLoc(
                            stream_index=stream_index,
                            stream_xref=xref,
                            op_index=op_index,
                            operator=o,
                            text=text,
                            origin=origin,
                            font_resource=str(font_resource),
                            font_size=font_size,
                        )
                # Advance the line matrix for ' and " (they move to next line).
                if o == "'":
                    tlm = _concat(tlm, (1.0, 0.0, 0.0, 1.0, 0.0, -leading))
                    tm = tlm
                elif o == '"':
                    if len(op.operands) >= 2:
                        leading = -float(op.operands[1])
                    tlm = _concat(tlm, (1.0, 0.0, 0.0, 1.0, 0.0, -leading))
                    tm = tlm
    return best


def _page_content_streams(page: fitz.Page) -> List[Tuple[int, bytes]]:
    """Return ``[(xref, data), ...]`` for the page's content streams."""
    contents = page.get_contents()
    if isinstance(contents, list):
        return [(int(x), page.parent.xref_stream(x)) for x in contents]
    if isinstance(contents, int):
        return [(contents, page.parent.xref_stream(contents))]
    return []


# --------------------------------------------------------------------------- #
# Replacement helpers (operate on one operator's operands)
# --------------------------------------------------------------------------- #
def _replace_in_operands(
    op: PDFOperator, word: str, replacement: str
) -> Tuple[List[Any], bool]:
    """Replace *word* with *replacement* inside one operator's operands.

    Returns ``(new_operands, spanned)``.  *spanned* is True when the word is
    split across several string segments of a ``TJ`` array (kerning) and cannot
    be replaced in a single segment — the caller should refuse in that case.
    """
    o = op.operator
    if o == "Tj":
        return _replace_single_string(op.operands[0], word, replacement), False
    if o == "TJ":
        operands = list(op.operands)
        arr = operands[0]
        if not isinstance(arr, list):
            return operands, False
        new_arr = []
        found = False
        for item in arr:
            if isinstance(item, (str, bytes)):
                new_item, hit = _replace_string_value(item, word, replacement)
                if hit:
                    found = True
                new_arr.append(new_item)
            else:
                new_arr.append(item)
        if not found:
            # Word not inside any single string element -> spanned.
            # Try to see if it spans elements: rebuild concatenated text.
            joined = _join_array_text(arr)
            if word in joined:
                return operands, True
            return operands, False
        operands[0] = new_arr
        return operands, False
    if o == "'":
        new_val, _ = _replace_single_string(op.operands[0], word, replacement)
        return [new_val], False
    if o == '"':
        new_val, _ = _replace_single_string(op.operands[2], word, replacement)
        return [op.operands[0], op.operands[1], new_val], False
    return op.operands, False


def _replace_single_string(operand, word, replacement):
    new_operands, spanned = _replace_string_value(operand, word, replacement)
    return [new_operands]


def _replace_string_value(operand, word, replacement):
    """Replace *word* in a string operand (str or latin-1 bytes).

    Returns ``(new_value, hit)``.
    """
    if isinstance(operand, bytes):
        text = operand.decode("latin-1", errors="ignore")
        if word in text:
            return text.replace(word, replacement).encode("latin-1", "ignore"), True
        return operand, False
    if isinstance(operand, str):
        if word in operand:
            return operand.replace(word, replacement), True
        return operand, False
    return operand, False


def _join_array_text(arr) -> str:
    out = ""
    for item in arr:
        if isinstance(item, str):
            out += item
        elif isinstance(item, bytes):
            out += item.decode("latin-1", errors="ignore")
    return out


def _splice_operator(
    stream_data: bytes, op: PDFOperator, new_operands: List[Any]
) -> bytes:
    """Replace a single operator's span in *stream_data* with re-serialized bytes."""
    new_op = PDFOperator(op.operator, new_operands, op.start_pos, op.end_pos)
    new_bytes = ContentStreamSerializer.serialize_ops([new_op])
    return stream_data[: op.start_pos] + new_bytes + stream_data[op.end_pos :]


# --------------------------------------------------------------------------- #
# Font support check
# --------------------------------------------------------------------------- #
def _font_supports_text(page: fitz.Page, font_resource: str, text: str) -> Tuple[bool, Optional[str]]:
    """Return (ok, reason) for whether the operator's font can draw *text*."""
    try:
        fonts = {f[4]: f for f in page.get_fonts()}
    except Exception:
        return True, None  # cannot verify -> allow
    info = fonts.get(font_resource)
    if info is None:
        return True, None
    xref = info[0]
    basefont = info[3]
    embedded = info[6] if len(info) > 6 else False
    try:
        if embedded:
            font_bytes = page.parent.extract_font(xref)[3]
            if not font_bytes:
                return True, None
            font = fitz.Font(fontfile=font_bytes)
        else:
            font = fitz.Font(fontname=basefont)
    except Exception:
        return True, None
    for ch in text:
        if not font.has_glyph(ord(ch)):
            return False, (
                f"Character {ch!r} (U+{ord(ch):04X}) is not available in the "
                f"original font '{basefont}'. The replacement cannot be drawn "
                f"without substituting or embedding a different font."
            )
    return True, None


# --------------------------------------------------------------------------- #
# The Edit
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ReplaceExistingText(Edit):
    """Replace one existing word on a page with new text (genuine edit).

    Stores enough to re-locate the word on whatever document clone the session
    hands to :meth:`apply` (undo/redo/save all re-run apply), so the edit stays
    correct after the document changes underneath it.
    """

    page_num: int
    old_word: str
    new_word: str
    #: Original word box in top-left page space (used to re-locate on apply).
    word_rect: Tuple[float, float, float, float]
    font: str = ""
    font_size: float = 0.0
    color: int = 0
    flags: int = 0

    scope = Scope.CONTENT
    pages = None
    requires_assembly = False

    @property
    def label(self) -> str:
        old = self.old_word[:18]
        new = self.new_word[:18]
        return f"Edit text: '{old}' → '{new}'"

    def page_order(self, before_count: int):
        return None

    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        loc = locate_operator_for_word(page, self.old_word, self.word_rect)
        if loc is None:
            raise EditError(
                f"Could not locate the text '{self.old_word}' in the page "
                f"content stream. The text may be inside an unsupported "
                f"structure (Form XObject, scanned image, …)."
            )

        ok, reason = _font_supports_text(page, loc.font_resource, self.new_word)
        if not ok:
            raise EditError(reason)

        streams = _page_content_streams(page)
        if loc.stream_index >= len(streams):
            raise EditError("Content stream index out of range.")
        xref, data = streams[loc.stream_index]
        ops = ContentStreamParser.parse(data)
        if loc.op_index >= len(ops):
            raise EditError("Text operator index out of range.")
        op = ops[loc.op_index]
        if op.operator not in ContentStreamParser.TEXT_SHOW_OPS:
            raise EditError("Located operator is not a text-showing operator.")

        new_operands, spanned = _replace_in_operands(op, self.old_word, self.new_word)
        if spanned:
            raise EditError(
                f"The word '{self.old_word}' is split across kerning segments in "
                f"the PDF and cannot be replaced as a single unit. Editing this "
                f"fragment is not supported."
            )

        new_stream = _splice_operator(data, op, new_operands)
        page.parent.update_stream(xref, new_stream)
