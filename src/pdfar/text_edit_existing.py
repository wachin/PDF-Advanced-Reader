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

import re
from dataclasses import dataclass
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
    block_no: int = 0


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
        x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
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
    x0, y0, x1, y1, word, block_no, line_no, word_no = w[:8]
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
        block_no=block_no,
    )


def words_in_line(page: fitz.Page, hit: WordHit) -> List[WordHit]:
    """Return every word on *hit*'s text line, left to right."""
    out: List[WordHit] = []
    for w in page.get_text("words"):
        if w[5] == hit.block_no and w[6] == hit.line_no:
            out.append(_make_hit(page, w))
    out.sort(key=lambda h: h.rect[0])
    return out


def words_between(page: fitz.Page, start: WordHit,
                  end: WordHit) -> List[WordHit]:
    """Words on *start*'s line between *start* and *end* (inclusive)."""
    line = words_in_line(page, start)
    try:
        i0 = next(i for i, h in enumerate(line) if h.rect == start.rect)
        i1 = next(i for i, h in enumerate(line) if h.rect == end.rect)
    except StopIteration:
        return [start]
    lo, hi = sorted((i0, i1))
    return line[lo:hi + 1]


def combine_hits(hits: List[WordHit]) -> Optional[WordHit]:
    """Join adjacent word hits into one span hit (same page/line)."""
    if not hits:
        return None
    hits = sorted(hits, key=lambda h: h.rect[0])
    first = hits[0]
    return WordHit(
        page=first.page,
        text=" ".join(h.text for h in hits),
        rect=(min(h.rect[0] for h in hits), min(h.rect[1] for h in hits),
              max(h.rect[2] for h in hits), max(h.rect[3] for h in hits)),
        font=first.font,
        font_size=first.font_size,
        color=first.color,
        flags=first.flags,
        line_no=first.line_no,
        word_no=first.word_no,
        block_no=first.block_no,
    )


def _span_info_for_word(page: fitz.Page, w: tuple) -> Tuple[str, float, int, int]:
    """Find the span that produced *w* and return (font, size, color, flags)."""
    x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
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
# Font encoding: character code <-> Unicode (fail-closed validation)
# --------------------------------------------------------------------------- #
@dataclass
class _FontInfo:
    """One font resource as reported by :meth:`fitz.Page.get_fonts`."""

    xref: int
    ext: str
    type: str
    basefont: str
    name: str
    encoding: str

    @property
    def embedded(self) -> bool:
        return self.ext not in ("n/a", "", "none", None)


class _FontCodec:
    """Maps character codes to/from Unicode for one PDF font resource.

    A codec is built only from a trustworthy source — the font's
    ``/ToUnicode`` CMap or a known simple-font base encoding.  When neither is
    available the codec is ``None`` and callers must refuse the edit rather
    than guess, because a character being present in the font file does not
    prove it can be represented through the PDF's existing encoding.
    """

    def __init__(self, code_width: int, code_to_unicode: Dict[int, str],
                 unicode_to_code: Dict[str, int], source: str) -> None:
        self.code_width = code_width
        self.code_to_unicode = code_to_unicode
        self.unicode_to_code = unicode_to_code
        self.source = source

    def decode(self, data: bytes) -> str:
        """Decode raw string bytes into Unicode using the font's mapping."""
        if not data:
            return ""
        w = self.code_width
        out: List[str] = []
        usable = len(data) - (len(data) % w)
        for i in range(0, usable, w):
            code = int.from_bytes(data[i:i + w], "big")
            uni = self.code_to_unicode.get(code)
            if uni is None:
                uni = chr(code) if w == 1 else "\ufffd"
            out.append(uni)
        if usable < len(data):
            out.append("\ufffd")
        return "".join(out)

    def first_unencodable(self, text: str) -> Optional[str]:
        for ch in text:
            if ch not in self.unicode_to_code:
                return ch
        return None

    def encode(self, text: str) -> bytes:
        out = bytearray()
        for ch in text:
            code = self.unicode_to_code.get(ch)
            if code is None:
                raise ValueError(ch)
            out += int(code).to_bytes(self.code_width, "big")
        return bytes(out)


def _utf16be(hex_digits: str) -> str:
    """Decode a UTF-16BE hex fragment from a ToUnicode CMap."""
    h = re.sub(r"[^0-9A-Fa-f]", "", hex_digits)
    if len(h) % 4:
        h = h.rjust((len(h) + 3) // 4 * 4, "0")
    try:
        return bytes.fromhex(h).decode("utf-16-be", "ignore")
    except ValueError:
        return ""


def _parse_tounicode_cmap(cmap: str) -> Tuple[int, Dict[int, str]]:
    """Parse a ToUnicode CMap into ``(code_width, {code: unicode})``."""
    code_to_unicode: Dict[int, str] = {}
    m = re.search(
        r"begincodespacerange\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", cmap)
    width = len(m.group(1)) // 2 if m else 1
    width = max(1, min(width, 4))
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for src, dst in re.findall(
                r"<([0-9A-Fa-f\s]+)>\s*<([0-9A-Fa-f\s]+)>", block):
            code_to_unicode[int(re.sub(r"\s", "", src), 16)] = _utf16be(dst)
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        # Array form: <lo> <hi> [<d1> <d2> ...]
        for lo, hi, arr in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]",
                block, re.S):
            dsts = re.findall(r"<([0-9A-Fa-f\s]+)>", arr)
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            for k, code in enumerate(range(lo_i, hi_i + 1)):
                if k < len(dsts):
                    code_to_unicode[code] = _utf16be(dsts[k])
        # Sequential form: <lo> <hi> <dst_start>
        for lo, hi, dst in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
                block):
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            base = int(dst, 16)
            for code in range(lo_i, hi_i + 1):
                code_to_unicode[code] = chr(base + (code - lo_i))
    return width, code_to_unicode


#: Simple-font base encodings we can reproduce with a Python codec.  Anything
#: else (custom ``/Differences`` arrays, MacExpert, …) is treated as
#: unverifiable and refused.
_SIMPLE_ENCODINGS = {
    "WinAnsiEncoding": "cp1252",
    "MacRomanEncoding": "mac_roman",
    "PDFDocEncoding": "latin-1",
}


def _font_infos(page: fitz.Page) -> Dict[str, _FontInfo]:
    """Map resource name -> :class:`_FontInfo` for the page's fonts."""
    out: Dict[str, _FontInfo] = {}
    for f in page.get_fonts():
        if len(f) < 6:
            continue
        out[str(f[4])] = _FontInfo(
            xref=int(f[0]), ext=str(f[1]), type=str(f[2]),
            basefont=str(f[3]), name=str(f[4]), encoding=str(f[5]))
    return out


def _build_font_codec(doc: fitz.Document,
                      info: _FontInfo) -> Optional[_FontCodec]:
    """Build a codec from ``/ToUnicode``, else a known simple base encoding."""
    try:
        kind, val = doc.xref_get_key(info.xref, "ToUnicode")
    except Exception:
        kind, val = "null", "null"
    if kind == "xref":
        try:
            cmap = doc.xref_stream(int(val.split()[0])).decode(
                "latin-1", "ignore")
        except Exception:
            cmap = ""
        if cmap:
            cmap_width, code_to_unicode = _parse_tounicode_cmap(cmap)
            if code_to_unicode:
                # The code width follows the font type: simple fonts always
                # use one-byte codes even when their ToUnicode CMap declares a
                # wider codespace range (seen in real documents).
                if info.type == "Type0":
                    width = cmap_width
                else:
                    width = 1
                    code_to_unicode = {
                        c: u for c, u in code_to_unicode.items() if c <= 0xFF}
                unicode_to_code: Dict[str, int] = {}
                for code, uni in code_to_unicode.items():
                    if len(uni) == 1 and uni not in unicode_to_code:
                        unicode_to_code[uni] = code
                if unicode_to_code:
                    return _FontCodec(width, code_to_unicode, unicode_to_code,
                                      "tounicode")
    if info.type == "Type0":
        # Type0/CID fonts without a ToUnicode CMap cannot be reverse-mapped
        # safely: the byte codes are CIDs, not Unicode.
        return None
    enc_name = (info.encoding or "").split()[0] if info.encoding else ""
    pyenc = _SIMPLE_ENCODINGS.get(enc_name)
    if pyenc is None:
        return None
    code_to_unicode = {}
    unicode_to_code = {}
    for code in range(256):
        try:
            ch = bytes([code]).decode(pyenc)
        except Exception:
            continue
        code_to_unicode[code] = ch
        unicode_to_code.setdefault(ch, code)
    if not code_to_unicode:
        return None
    return _FontCodec(1, code_to_unicode, unicode_to_code, "simple")


def _load_font_program(doc: fitz.Document, info: _FontInfo) -> Optional[fitz.Font]:
    """Load the font program so glyph presence can be verified."""
    try:
        if info.embedded:
            content = doc.extract_font(info.xref)[3]
            if not content:
                return None
            return fitz.Font(fontbuffer=content)
        return fitz.Font(fontname=info.basefont)
    except Exception:
        return None


def _validate_replacement(
    doc: fitz.Document, page: fitz.Page, font_resource: str, text: str
) -> Tuple[bool, Optional[str], Optional[_FontCodec]]:
    """Fail-closed check that *text* can be drawn by the operator's font.

    Returns ``(ok, reason, codec)``.  The edit is allowed only when the font
    can be inspected, a trustworthy encoding is available, every glyph exists
    in the font program, and every character can be encoded through the font's
    actual PDF encoding.  Any inspection failure is a refusal, never an
    implicit approval.
    """
    try:
        infos = _font_infos(page)
    except Exception:
        return (False,
                "Could not inspect the page fonts, so the replacement cannot "
                "be verified safely.", None)
    info = infos.get(font_resource)
    if info is None:
        return (False,
                f"Could not find font resource '{font_resource}' on the page, "
                f"so the replacement cannot be verified safely.", None)
    codec = _build_font_codec(doc, info)
    if codec is None:
        return (False,
                f"Cannot verify how characters are encoded by font "
                f"'{info.basefont}'. Editing text in this font is not "
                f"supported because the replacement could be written "
                f"incorrectly.", None)
    font = _load_font_program(doc, info)
    if font is None:
        return (False,
                f"Could not load the font program for '{info.basefont}' to "
                f"verify glyphs, so the replacement cannot be verified "
                f"safely.", None)
    # 1) Glyph presence in the font program.
    for ch in text:
        if info.type == "Type0":
            cid = codec.unicode_to_code.get(ch)
            if cid is None:
                continue  # encoding check below reports it
            if cid >= font.glyph_count:
                return (False,
                        f"Character {ch!r} (U+{ord(ch):04X}) has no glyph in "
                        f"the embedded font '{info.basefont}'. The replacement "
                        f"cannot be drawn without embedding a different "
                        f"font.", None)
        elif not font.has_glyph(ord(ch)):
            return (False,
                    f"Character {ch!r} (U+{ord(ch):04X}) is not available in "
                    f"the original font '{info.basefont}'. The replacement "
                    f"cannot be drawn without substituting or embedding a "
                    f"different font.", None)
    # 2) Encodability through the font's actual PDF encoding.
    bad = codec.first_unencodable(text)
    if bad is not None:
        return (False,
                f"Character {bad!r} (U+{ord(bad):04X}) cannot be encoded "
                f"through the existing encoding of font '{info.basefont}'. "
                f"The replacement would not display correctly.", None)
    return True, None, codec


def _page_font_codecs(page: fitz.Page):
    """Return a ``resource -> Optional[_FontCodec]`` resolver for *page*."""
    cache: Dict[str, Optional[_FontCodec]] = {}
    infos: Optional[Dict[str, _FontInfo]] = None
    tried = False

    def resolve(resource: str) -> Optional[_FontCodec]:
        nonlocal infos, tried
        if resource in cache:
            return cache[resource]
        if not tried:
            tried = True
            try:
                infos = _font_infos(page)
            except Exception:
                infos = None
        codec: Optional[_FontCodec] = None
        if infos is not None:
            info = infos.get(resource)
            if info is not None:
                codec = _build_font_codec(page.parent, info)
        cache[resource] = codec
        return codec

    return resolve


def _decode_text_operand(op: PDFOperator, codec: Optional[_FontCodec]) -> str:
    """Extract the visible text of a text-showing operator via *codec*."""

    def dec(value) -> str:
        if isinstance(value, bytes):
            return codec.decode(value) if codec is not None else \
                value.decode("latin-1", "ignore")
        if isinstance(value, str):
            return value
        return ""

    o = op.operator
    if o == "Tj":
        return dec(op.operands[0]) if op.operands else ""
    if o == "TJ":
        parts: List[str] = []
        for operand in op.operands:
            if isinstance(operand, list):
                for item in operand:
                    parts.append(dec(item))
            else:
                parts.append(dec(operand))
        return "".join(parts)
    if o == "'":
        return dec(op.operands[0]) if op.operands else ""
    if o == '"':
        return dec(op.operands[2]) if len(op.operands) >= 3 else ""
    return ""


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
    resolve_codec = _page_font_codecs(page)
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
                    try:
                        font_size = float(op.operands[1])
                    except (TypeError, ValueError):
                        font_size = 0.0
            elif o in ("Tm",):
                try:
                    tm = tuple(float(v) for v in op.operands[:6])
                    tlm = tm
                except (TypeError, ValueError):
                    pass
            elif o in ("Td", "TD"):
                try:
                    tx, ty = float(op.operands[0]), float(op.operands[1])
                except (TypeError, ValueError, IndexError):
                    continue
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
                text = _decode_text_operand(
                    op, resolve_codec(str(font_resource)))
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
def _decode_bytes(raw: bytes, codec: Optional[_FontCodec]) -> str:
    if codec is not None:
        return codec.decode(raw)
    return raw.decode("latin-1", errors="ignore")


def _operand_text(value, codec: Optional[_FontCodec]) -> Optional[str]:
    """The visible text of one string operand.

    Literal strings arrive from the parser already decoded as ``str``; use them
    as-is so the text seen by location and by replacement is identical.  Hex
    strings arrive as ``bytes`` and are decoded through the font codec.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return _decode_bytes(value, codec)
    return None


def _splice_segment(item, text: str, local_start: int, local_end: int,
                    replacement: str,
                    codec: Optional[_FontCodec]) -> bytes:
    """Return *item* with ``text[local_start:local_end]`` replaced.

    Untouched prefix/suffix bytes are preserved verbatim; only the replacement
    is encoded.  This avoids corrupting characters that are already correct in
    the stream but are not reverse-mapped by the font's encoding.
    """
    width = codec.code_width if codec is not None else 1
    if codec is not None:
        repl = codec.encode(replacement)
    else:
        repl = replacement.encode("latin-1", "ignore")
    if isinstance(item, bytes):
        return (item[: local_start * width] + repl
                + item[local_end * width:])

    # A literal string: re-encode its untouched parts, keeping their byte
    # values when the codec has no mapping for a character.
    def enc(part: str) -> bytes:
        out = bytearray()
        for ch in part:
            code = codec.unicode_to_code.get(ch) if codec is not None else None
            if code is not None:
                out += int(code).to_bytes(width, "big")
            else:
                out += (ord(ch) & 0xFF).to_bytes(1, "big")
        return bytes(out)

    return enc(text[:local_start]) + repl + enc(text[local_end:])


def _replace_in_value(value, word: str, replacement: str,
                      codec: Optional[_FontCodec]):
    """Replace *word* inside one string operand.

    Returns ``(new_value, status)`` with status ``"ok"``, ``"not_found"`` or
    ``"ambiguous"``.
    """
    if isinstance(value, str) and codec is not None and codec.code_width != 1:
        # A literal string in a multi-byte code space cannot be sliced safely.
        return value, "not_found"
    text = _operand_text(value, codec)
    if text is None:
        return value, "not_found"
    count = text.count(word)
    if count == 0:
        return value, "not_found"
    if count > 1:
        return value, "ambiguous"
    idx = text.index(word)
    return (_splice_segment(value, text, idx, idx + len(word),
                            replacement, codec), "ok")


def _replace_in_tj_array(arr: List[Any], word: str, replacement: str,
                         codec: Optional[_FontCodec]):
    """Replace *word* across the string segments of a ``TJ`` array.

    The concatenated text is searched for *word*.  Only kerning numbers that
    fall strictly inside the matched range are dropped (they positioned the old
    glyphs); numbers before and after are preserved so the surrounding text
    keeps its position.  The replacement is inserted exactly once.  Returns
    ``(new_array, status)``.
    """
    if codec is not None and codec.code_width != 1:
        # Literal strings inside a multi-byte code space are not safe to slice.
        if any(isinstance(item, str) for item in arr):
            return list(arr), "not_found"
    segments: List[Tuple[int, str]] = []
    joined_parts: List[str] = []
    for idx, item in enumerate(arr):
        if isinstance(item, (bytes, str)):
            text = _operand_text(item, codec) or ""
            segments.append((idx, text))
            joined_parts.append(text)
    joined = "".join(joined_parts)
    count = joined.count(word)
    if count == 0:
        return list(arr), "not_found"
    if count > 1:
        return list(arr), "ambiguous"
    start = joined.index(word)
    end = start + len(word)

    # Character offset at the start of each string segment.
    seg_start: Dict[int, int] = {}
    off = 0
    for idx, text in segments:
        seg_start[idx] = off
        off += len(text)
    seg_text = {idx: text for idx, text in segments}

    new_arr: List[Any] = []
    inserted = False
    for idx, item in enumerate(arr):
        if isinstance(item, (bytes, str)):
            text = seg_text[idx]
            t0 = seg_start[idx]
            t1 = t0 + len(text)
            if t1 <= start or t0 >= end:
                new_arr.append(item)
                continue
            local_start = max(0, start - t0)
            local_end = min(len(text), end - t0)
            repl = ""
            if not inserted and t0 <= start < t1:
                repl = replacement
                inserted = True
            new_item = _splice_segment(
                item, text, local_start, local_end, repl, codec)
            if new_item:
                new_arr.append(new_item)
        else:
            # A kerning adjustment: keep it unless it sits inside the match.
            num_off = 0
            for sidx, stext in segments:
                if sidx < idx:
                    num_off += len(stext)
                else:
                    break
            if start < num_off < end:
                continue
            new_arr.append(item)
    if not inserted:
        return list(arr), "ambiguous"
    return new_arr, "ok"


def _replace_in_operands(
    op: PDFOperator, word: str, replacement: str,
    codec: Optional[_FontCodec] = None,
) -> Tuple[List[Any], str]:
    """Replace *word* with *replacement* inside one operator's operands.

    Returns ``(new_operands, status)`` where status is ``"ok"``, ``"not_found"``
    or ``"ambiguous"``.  When *codec* is given, strings are decoded/encoded
    through the font's real encoding; otherwise latin-1 is used (unit tests).
    """
    o = op.operator
    if o == "Tj":
        value = op.operands[0] if op.operands else ""
        new_value, status = _replace_in_value(value, word, replacement, codec)
        return [new_value], status
    if o == "TJ":
        operands = list(op.operands)
        arr = operands[0] if operands else []
        if not isinstance(arr, list):
            return operands, "not_found"
        new_arr, status = _replace_in_tj_array(arr, word, replacement, codec)
        operands[0] = new_arr
        return operands, status
    if o == "'":
        value = op.operands[0] if op.operands else ""
        new_value, status = _replace_in_value(value, word, replacement, codec)
        return [new_value], status
    if o == '"':
        value = op.operands[2] if len(op.operands) >= 3 else ""
        new_value, status = _replace_in_value(value, word, replacement, codec)
        return [op.operands[0], op.operands[1], new_value], status
    return list(op.operands), "not_found"


def _splice_operator(
    stream_data: bytes, op: PDFOperator, new_operands: List[Any]
) -> bytes:
    """Replace a single operator's span in *stream_data* with re-serialized bytes."""
    new_op = PDFOperator(op.operator, new_operands, op.start_pos, op.end_pos)
    new_bytes = ContentStreamSerializer.serialize_ops([new_op])
    return stream_data[: op.start_pos] + new_bytes + stream_data[op.end_pos :]


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

        ok, reason, codec = _validate_replacement(
            doc, page, loc.font_resource, self.new_word)
        if not ok:
            raise EditError(reason or "The replacement could not be verified.")

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

        new_operands, status = _replace_in_operands(
            op, self.old_word, self.new_word, codec)
        if status == "not_found":
            raise EditError(
                f"The text '{self.old_word}' could not be found inside the "
                f"located text operator. The page may use an unsupported text "
                f"layout."
            )
        if status == "ambiguous":
            raise EditError(
                f"The text '{self.old_word}' appears more than once inside the "
                f"located text operator and cannot be replaced unambiguously."
            )

        new_stream = _splice_operator(data, op, new_operands)
        page.parent.update_stream(xref, new_stream)
