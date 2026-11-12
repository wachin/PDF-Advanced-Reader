"""Existing-text editing engine for PDFAR.

This module implements **genuine** editing of text that already exists in a PDF:
it locates the content-stream operator that produced a clicked word and rewrites
*only that operator's operands*, leaving every other byte of the page untouched.

Design (see ``docs/existing-text-edit-audit.md``):

* Hit testing uses :meth:`fitz.Page.get_text` ``"words"``, which yields a precise
  page-space bounding box per word.
* Mapping a word to its operator replays the graphics state of the page's
  content streams: nested ``q``/``Q`` save/restore, composed ``cm`` matrices,
  ``Tm``/``Td``/``TD``/``T*`` positioning and the text state (``Tf``, ``Tc``,
  ``Tw``, ``Tz``, ``TL``, ``Ts``).  Each character's advance is computed from
  the real font program, so the clicked word is matched by glyph position, not
  just by text — which is what disambiguates a word drawn several times inside
  one operator.
* Form XObjects are followed through ``Do``: their ``/Matrix`` is composed with
  the invoking CTM, their own ``/Resources`` replace the page's, and their text
  becomes editable.  A shared XObject is never modified in place — the page
  gets a copy-on-write clone — and an object invoked twice on one page is
  refused, because editing it would change every copy.
* Replacement is *surgical*: the matched operator is re-serialized and spliced
  back into the original stream at ``[start_pos:end_pos)``.  No whole-stream
  re-serialization, so dictionaries, inline images and other operators survive
  byte-for-byte (Strategy A from the task spec).
* If the original font cannot represent a replacement glyph through its actual
  encoding, the edit refuses with a clear message (Strategy D) rather than
  producing a corrupted file.

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

    source: "_StreamRef"    # stream + resource context holding the operator
    op_index: int          # index within the parsed stream operators
    operator: str          # Tj, TJ, ', "
    text: str              # extracted visible text
    origin: Tuple[float, float]   # (x, y) in top-left page space
    font_resource: str     # resource name used by the preceding Tf
    font_size: float
    #: Which occurrence of the word inside the operator text (0-based).
    occurrence: int = 0
    #: How many times the containing Form XObject is invoked on this page.
    invocations: int = 1


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


# --------------------------------------------------------------------------- #
# Resource dictionaries (page tree and Form XObjects)
# --------------------------------------------------------------------------- #
_PDF_NAME_ENTRY_RE = re.compile(r"/([^\s/<>\[\](){}%]+)\s+(\d+)\s+0\s+R")
_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)")


def _pdf_name(name: str) -> str:
    """Decode ``#xx`` escapes in a PDF name."""
    return re.sub(
        r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), name)


def _resources_of(doc: fitz.Document, owner_xref: int):
    """Return ``(kind, value)`` for *owner_xref*'s ``/Resources``.

    Resources may be inherited from the page tree, so ``/Parent`` is followed
    when an object does not define its own.  ``kind`` is ``"xref"`` (value is
    ``"N 0 R"``) or ``"dict"`` (value is the dictionary source).
    """
    seen = set()
    xref = owner_xref
    while xref and xref not in seen:
        seen.add(xref)
        try:
            kind, value = doc.xref_get_key(xref, "Resources")
        except Exception:
            return None, None
        if kind in ("xref", "dict"):
            return kind, value
        try:
            pkind, pvalue = doc.xref_get_key(xref, "Parent")
        except Exception:
            return None, None
        if pkind != "xref":
            return None, None
        try:
            xref = int(pvalue.split()[0])
        except (IndexError, ValueError):
            return None, None
    return None, None


def _subdict(doc: fitz.Document, kind: Optional[str], value: Optional[str],
             key: str):
    """Return ``(kind, value)`` of *key* inside a dictionary."""
    if not kind or value is None:
        return None, None
    if kind == "xref":
        try:
            xref = int(value.split()[0])
            k, v = doc.xref_get_key(xref, key)
        except Exception:
            return None, None
        return (k, v) if k in ("xref", "dict", "array") else (None, None)
    m = re.search(r"/%s\s*(<<|\d+\s+0\s+R)" % re.escape(key), value)
    if not m:
        return None, None
    if m.group(1) != "<<":
        return "xref", m.group(1)
    start = m.end() - 2
    depth = 0
    i = start
    while i < len(value):
        if value.startswith("<<", i):
            depth += 1
            i += 2
        elif value.startswith(">>", i):
            depth -= 1
            i += 2
            if depth == 0:
                return "dict", value[start:i]
        else:
            i += 1
    return None, None


def _dict_refs(doc: fitz.Document, kind: Optional[str],
               value: Optional[str]) -> Dict[str, int]:
    """Map name -> xref for the indirect entries of a dictionary."""
    out: Dict[str, int] = {}
    if not kind or value is None:
        return out
    if kind == "xref":
        try:
            xref = int(value.split()[0])
            keys = doc.xref_get_keys(xref)
        except Exception:
            return out
        for key in keys:
            try:
                k, v = doc.xref_get_key(xref, key)
            except Exception:
                continue
            if k != "xref":
                continue
            try:
                out[str(key)] = int(v.split()[0])
            except (IndexError, ValueError):
                continue
        return out
    for name, xref in _PDF_NAME_ENTRY_RE.findall(value):
        out[_pdf_name(name)] = int(xref)
    return out


def _array_numbers(kind: Optional[str], value: Optional[str]) -> List[float]:
    """Parse a PDF array of numbers (e.g. a Form XObject ``/Matrix``)."""
    if kind != "array" or not value:
        return []
    try:
        return [float(n) for n in _NUMBER_RE.findall(value)]
    except ValueError:
        return []


def _font_info_by_xref(page: fitz.Page) -> Dict[int, _FontInfo]:
    """Map font xref -> info for every font PyMuPDF reports for *page*.

    ``Page.get_fonts()`` also reports fonts that are only used inside Form
    XObjects, which is what makes this a reliable lookup for XObject text.
    """
    out: Dict[int, _FontInfo] = {}
    for f in page.get_fonts():
        if len(f) < 6:
            continue
        xref = int(f[0])
        if xref in out:
            continue
        out[xref] = _FontInfo(
            xref=xref, ext=str(f[1]), type=str(f[2]),
            basefont=str(f[3]), name=str(f[4]), encoding=str(f[5]))
    return out


def _font_infos_for(page: fitz.Page,
                    owner_xref: Optional[int]) -> Dict[str, _FontInfo]:
    """Font resources in force for the content owned by *owner_xref*.

    A Form XObject carries its own ``/Resources`` (which may shadow a page
    font of the same name), so the mapping must be resolved per owner rather
    than from the page's flattened font list.
    """
    if not owner_xref:
        return _font_infos(page)
    doc = page.parent
    try:
        res_kind, res_value = _resources_of(doc, owner_xref)
        font_kind, font_value = _subdict(doc, res_kind, res_value, "Font")
        refs = _dict_refs(doc, font_kind, font_value)
    except Exception:
        refs = {}
    if not refs:
        return _font_infos(page)
    by_xref = _font_info_by_xref(page)
    page_infos = _font_infos(page)
    out: Dict[str, _FontInfo] = {}
    for name, xref in refs.items():
        info = by_xref.get(xref)
        if info is None:
            info = page_infos.get(name)
        if info is not None:
            out[name] = info
    return out or _font_infos(page)



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
    doc: fitz.Document, page: fitz.Page, font_resource: str, text: str,
    res_owner: Optional[int] = None,
) -> Tuple[bool, Optional[str], Optional[_FontCodec]]:
    """Fail-closed check that *text* can be drawn by the operator's font.

    Returns ``(ok, reason, codec)``.  The edit is allowed only when the font
    can be inspected, a trustworthy encoding is available, every glyph exists
    in the font program, and every character can be encoded through the font's
    actual PDF encoding.  Any inspection failure is a refusal, never an
    implicit approval.  *res_owner* selects the resource dictionary that
    applies (the page, or the Form XObject containing the operator).
    """
    try:
        infos = _font_infos_for(page, res_owner)
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


def _page_font_codecs(page: fitz.Page, res_owner: Optional[int] = None):
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
                infos = _font_infos_for(page, res_owner)
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


# --------------------------------------------------------------------------- #
# Graphics-state replay + operator location
# --------------------------------------------------------------------------- #
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_mul(m1, m2):
    """Return ``m1 × m2`` for ``(a,b,c,d,e,f)`` matrices.

    The convention is "apply *m1*, then *m2*" (PDF row-vector form), matching
    :meth:`fitz.Matrix.__mul__`.
    """
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _matrix_tuple(m) -> Tuple[float, float, float, float, float, float]:
    """Convert a :class:`fitz.Matrix` to an ``(a,b,c,d,e,f)`` tuple."""
    return (float(m.a), float(m.b), float(m.c),
            float(m.d), float(m.e), float(m.f))


def _float(operands, index: int, default: float = 0.0) -> float:
    try:
        return float(operands[index])
    except (IndexError, TypeError, ValueError):
        return default


@dataclass
class _TextState:
    """The text-state parameters that affect glyph positioning."""

    font_resource: str = ""
    font_size: float = 0.0
    char_spacing: float = 0.0      # Tc
    word_spacing: float = 0.0      # Tw
    h_scale: float = 1.0           # Tz / 100
    leading: float = 0.0           # TL
    rise: float = 0.0              # Ts
    render_mode: int = 0           # Tr

    def copy(self) -> "_TextState":
        return _TextState(
            self.font_resource, self.font_size, self.char_spacing,
            self.word_spacing, self.h_scale, self.leading, self.rise,
            self.render_mode)


@dataclass(frozen=True)
class _StreamRef:
    """Where a text operator lives and which resources apply to it."""

    xref: int
    #: Index into ``page.get_contents()``; ``-1`` for a Form XObject stream.
    page_index: int = -1
    #: xref of the object whose ``/Resources`` are in force for the stream.
    res_owner: int = 0
    in_xobject: bool = False
    #: Name under which the parent invokes this XObject (for copy-on-write).
    resource_name: str = ""
    #: 0 for a page content stream, 1 for a Form XObject invoked by the page,
    #: 2+ for an XObject invoked from inside another XObject.
    depth: int = 0


@dataclass
class _TextRun:
    """A text-showing operator with its computed positioning data."""

    source: _StreamRef
    op_index: int
    operator: str
    text: str
    #: Text-space x offset of each character relative to the operator origin.
    #: ``None`` when a glyph advance could not be determined.
    offsets: Optional[List[float]]
    #: Combined ``Tm × CTM × page.transformation_matrix``.
    trm: Tuple[float, ...]
    font_resource: str
    font_size: float

    def point_at(self, char_index: int) -> Tuple[float, float]:
        """Page-space ``(x, y)`` of the character at *char_index*."""
        if self.offsets is None or char_index >= len(self.offsets):
            return (self.trm[4], self.trm[5])
        d = self.offsets[char_index]
        return (self.trm[0] * d + self.trm[4],
                self.trm[1] * d + self.trm[5])


def _advance_fn(font, info, codec):
    """Return a ``char -> advance (em fraction)`` function, or ``None``."""
    if font is None or info is None:
        return lambda _ch: None
    if info.type == "Type0":
        def adv(ch):
            cid = codec.unicode_to_code.get(ch) if codec is not None else None
            if cid is None or cid >= font.glyph_count:
                return None
            return font.glyph_advance(cid)
        return adv

    def adv(ch):
        gid = font.has_glyph(ord(ch))
        if not gid:
            return None
        return font.glyph_advance(gid)
    return adv


def _operator_char_offsets(op: PDFOperator, codec: Optional[_FontCodec],
                           advance, ts: _TextState):
    """Return ``(text, offsets)`` for a text-showing operator.

    ``offsets[i]`` is the text-space x offset of character *i* relative to the
    operator's origin.  Returns ``(text, None)`` when an advance is unknown.
    """
    chars: List[str] = []
    offsets: List[float] = []
    x = 0.0
    th = ts.h_scale
    fs = ts.font_size

    def add_string(s: str) -> bool:
        nonlocal x
        ok_local = True
        for ch in s:
            offsets.append(x)
            chars.append(ch)
            w = advance(ch)
            if w is None:
                # Keep the character so the operator's text stays complete;
                # the caller must not trust the offsets in this case.
                ok_local = False
                continue
            x += ((w * fs) + ts.char_spacing
                  + (ts.word_spacing if ch == " " else 0.0)) * th
        return ok_local

    def add_number(n: float) -> None:
        nonlocal x
        x -= (n / 1000.0) * fs * th

    o = op.operator
    if o == "Tj":
        s = _operand_text(op.operands[0], codec) if op.operands else ""
        ok = add_string(s)
    elif o == "TJ":
        arr = op.operands[0] if op.operands else []
        ok = isinstance(arr, list)
        if ok:
            for item in arr:
                if isinstance(item, (bytes, str)):
                    if not add_string(_operand_text(item, codec) or ""):
                        ok = False
                elif isinstance(item, (int, float)) and not isinstance(item, bool):
                    add_number(float(item))
    elif o == "'":
        s = _operand_text(op.operands[0], codec) if op.operands else ""
        ok = add_string(s)
    elif o == '"':
        s = (_operand_text(op.operands[2], codec)
             if len(op.operands) >= 3 else "")
        ok = add_string(s)
    else:
        ok = False
    text = "".join(chars)
    return text, (offsets if ok else None)


def _page_font_programs(page: fitz.Page, res_owner: Optional[int] = None):
    """Return a ``resource -> (info, font)`` resolver for *page*."""
    cache: Dict[str, Tuple[Optional[_FontInfo], Optional[fitz.Font]]] = {}
    infos: Optional[Dict[str, _FontInfo]] = None
    tried = False

    def resolve(resource: str):
        nonlocal infos, tried
        if resource in cache:
            return cache[resource]
        if not tried:
            tried = True
            try:
                infos = _font_infos_for(page, res_owner)
            except Exception:
                infos = None
        info = infos.get(resource) if infos is not None else None
        font = _load_font_program(page.parent, info) if info is not None else None
        result = (info, font)
        cache[resource] = result
        return result

    return resolve


#: How deep to follow Form XObjects nested inside Form XObjects.
_MAX_XOBJECT_DEPTH = 8


def _iter_page_runs(page: fitz.Page,
                    counts: Optional[Dict[int, int]] = None):
    """Yield :class:`_TextRun` objects for all text drawn on *page*.

    The graphics state is replayed faithfully: ``q``/``Q`` save and restore the
    CTM and text state, ``cm`` concatenates (rather than replaces) the CTM,
    state carries across content streams (they are logically concatenated), and
    content coordinates are mapped to ``get_text`` page space through
    ``page.transformation_matrix`` (which handles CropBox offsets and rotation).

    Form XObjects are followed: their ``/Matrix`` is composed with the CTM in
    force at the invoking ``Do``, and their own ``/Resources`` (when present)
    replace the page's.  *counts* is filled with the number of times each
    XObject xref is invoked on this page, which the caller uses to detect
    shared XObjects before editing them.
    """
    if counts is None:
        counts = {}
    page_tm = _matrix_tuple(page.transformation_matrix)
    for index, (xref, data) in enumerate(_page_content_streams(page)):
        if not data:
            continue
        ref = _StreamRef(xref=xref, page_index=index, res_owner=page.xref)
        yield from _iter_stream_runs(
            page, data, ref, _IDENTITY, _TextState(), page_tm, 0, (), counts)


def _iter_stream_runs(page: fitz.Page, data: bytes, ref: _StreamRef,
                      ctm, text_state: _TextState, page_tm,
                      depth: int, stack: Tuple[int, ...],
                      counts: Dict[int, int]):
    """Replay one content stream (the page's or a Form XObject's)."""
    doc = page.parent
    ctm = tuple(ctm)
    ts = text_state.copy()
    tm = _IDENTITY
    tlm = _IDENTITY
    gstack: List[Tuple[Tuple[float, ...], _TextState]] = []
    resolve_codec = _page_font_codecs(page, ref.res_owner)
    resolve_font = _page_font_programs(page, ref.res_owner)
    try:
        res_kind, res_value = _resources_of(doc, ref.res_owner)
    except Exception:
        res_kind, res_value = None, None

    for op_index, op in enumerate(ContentStreamParser.parse(data)):
        o = op.operator
        if o == "q":
            gstack.append((ctm, ts.copy()))
        elif o == "Q":
            if gstack:
                ctm, ts = gstack.pop()
        elif o == "cm":
            m = tuple(_float(op.operands, i) for i in range(6))
            ctm = _mat_mul(m, ctm)
        elif o == "BT":
            tm = _IDENTITY
            tlm = _IDENTITY
        elif o == "Tf":
            if len(op.operands) >= 2:
                ts.font_resource = str(op.operands[0])
                ts.font_size = _float(op.operands, 1)
        elif o == "Tc":
            ts.char_spacing = _float(op.operands, 0)
        elif o == "Tw":
            ts.word_spacing = _float(op.operands, 0)
        elif o == "Tz":
            ts.h_scale = _float(op.operands, 0, 100.0) / 100.0
        elif o == "TL":
            ts.leading = _float(op.operands, 0)
        elif o == "Ts":
            ts.rise = _float(op.operands, 0)
        elif o == "Tr":
            ts.render_mode = int(_float(op.operands, 0))
        elif o == "Tm":
            try:
                tm = tuple(float(v) for v in op.operands[:6])
                tlm = tm
            except (TypeError, ValueError):
                pass
        elif o in ("Td", "TD"):
            tx = _float(op.operands, 0)
            ty = _float(op.operands, 1)
            tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
            tm = tlm
            if o == "TD":
                ts.leading = -ty
        elif o == "T*":
            tlm = _mat_mul(
                (1.0, 0.0, 0.0, 1.0, 0.0, -ts.leading), tlm)
            tm = tlm
        elif o == "Do" and op.operands and depth < _MAX_XOBJECT_DEPTH:
            yield from _iter_xobject_runs(
                page, str(op.operands[0]), res_kind, res_value,
                ctm, ts, page_tm, ref, depth, stack, counts)
        elif o in ContentStreamParser.TEXT_SHOW_OPS:
            if o == "'":
                tlm = _mat_mul(
                    (1.0, 0.0, 0.0, 1.0, 0.0, -ts.leading), tlm)
                tm = tlm
            elif o == '"':
                ts.word_spacing = _float(op.operands, 0)
                ts.char_spacing = _float(op.operands, 1)
                tlm = _mat_mul(
                    (1.0, 0.0, 0.0, 1.0, 0.0, -ts.leading), tlm)
                tm = tlm
            codec = resolve_codec(ts.font_resource)
            info, font = resolve_font(ts.font_resource)
            text, offsets = _operator_char_offsets(
                op, codec, _advance_fn(font, info, codec), ts)
            # Ts offsets the text rendering matrix vertically (in text space,
            # before Tm), so include it between the glyph origin and Tm.
            rise = (1.0, 0.0, 0.0, 1.0, 0.0, ts.rise)
            trm = _mat_mul(_mat_mul(_mat_mul(rise, tm), ctm), page_tm)
            yield _TextRun(
                source=ref, op_index=op_index, operator=o, text=text,
                offsets=offsets, trm=trm, font_resource=ts.font_resource,
                font_size=ts.font_size)


def _iter_xobject_runs(page: fitz.Page, name: str, res_kind, res_value,
                       ctm, ts: _TextState, page_tm, ref: _StreamRef,
                       depth: int, stack: Tuple[int, ...],
                       counts: Dict[int, int]):
    """Follow a ``Do`` invocation when it names a Form XObject."""
    doc = page.parent
    try:
        x_kind, x_value = _subdict(doc, res_kind, res_value, "XObject")
        xref = _dict_refs(doc, x_kind, x_value).get(name)
    except Exception:
        return
    if xref is None or xref in stack:
        return  # missing resource, or a cycle we must not follow
    try:
        s_kind, s_value = doc.xref_get_key(xref, "Subtype")
        if s_kind != "name" or s_value.strip() != "/Form":
            return  # an image or another non-form XObject
        data = doc.xref_stream(xref)
    except Exception:
        return
    if not data:
        return

    counts[xref] = counts.get(xref, 0) + 1
    try:
        m_kind, m_value = doc.xref_get_key(xref, "Matrix")
        matrix = _array_numbers(m_kind, m_value)
    except Exception:
        matrix = []
    if len(matrix) != 6:
        matrix = list(_IDENTITY)

    try:
        sub_res_kind, _ = _resources_of(doc, xref)
    except Exception:
        sub_res_kind = None
    sub_ref = _StreamRef(
        xref=xref, page_index=-1,
        res_owner=xref if sub_res_kind else ref.res_owner,
        in_xobject=True, resource_name=name, depth=depth + 1)
    yield from _iter_stream_runs(
        page, data, sub_ref, _mat_mul(tuple(matrix), ctm), ts, page_tm,
        depth + 1, stack + (xref,), counts)


def _occurrences(text: str, word: str) -> List[int]:
    """Return the start index of every (non-overlapping) occurrence of *word*."""
    out: List[int] = []
    start = 0
    while word:
        i = text.find(word, start)
        if i < 0:
            break
        out.append(i)
        start = i + len(word)
    return out


def _nth_index(text: str, word: str, n: int) -> int:
    """Return the start index of the *n*-th occurrence of *word*, or ``-1``."""
    if n < 0:
        return -1
    start = 0
    idx = -1
    for _ in range(n + 1):
        idx = text.find(word, start)
        if idx < 0:
            return -1
        start = idx + len(word)
    return idx


def _rect_distance(point: Tuple[float, float],
                   rect: Tuple[float, float, float, float]) -> float:
    """Distance from *point* to *rect* (0 when the point is inside)."""
    px, py = point
    x0, y0, x1, y1 = rect
    dx = max(x0 - px, 0.0, px - x1)
    dy = max(y0 - py, 0.0, py - y1)
    return (dx * dx + dy * dy) ** 0.5


def locate_operator_for_word(
    page: fitz.Page,
    word: str,
    word_rect: Tuple[float, float, float, float],
    counts: Optional[Dict[int, int]] = None,
) -> Optional[_OperatorLoc]:
    """Find the content-stream operator and occurrence that produced *word*.

    When the word occurs more than once inside one operator, the occurrence is
    chosen by comparing the computed glyph position of each candidate against
    the clicked word's page-space box.  If the advance widths cannot be
    determined, repeated words are refused rather than guessed.

    *counts*, when given, is filled with how often each Form XObject is
    invoked on the page, so the caller can refuse to edit a shared object.
    """
    if not word:
        return None
    if counts is None:
        counts = {}
    best: Optional[_OperatorLoc] = None
    best_score = None

    for run in _iter_page_runs(page, counts):
        if not run.text or word not in run.text:
            continue
        occurrences = _occurrences(run.text, word)
        if not occurrences:
            continue
        if run.offsets is None:
            if len(occurrences) > 1:
                # Cannot disambiguate without glyph advances.
                continue
            candidates = [(0, 0)]
        else:
            candidates = list(enumerate(occurrences))
        for occurrence, char_index in candidates:
            point = run.point_at(char_index)
            score = _rect_distance(point, word_rect)
            if run.offsets is not None:
                # Reject a run whose computed position is nowhere near the
                # word: it is a different copy of the same text.
                tolerance = max(6.0, 2.0 * abs(run.font_size))
                if score > tolerance:
                    continue
            if best_score is None or score < best_score:
                best_score = score
                best = _OperatorLoc(
                    source=run.source,
                    op_index=run.op_index,
                    operator=run.operator,
                    text=run.text,
                    origin=point,
                    font_resource=run.font_resource,
                    font_size=run.font_size,
                    occurrence=occurrence,
                    invocations=1,
                )
    if best is not None and best.source.in_xobject:
        # The generator has now seen every invocation, so the final count is
        # authoritative (a run is yielded before later copies are reached).
        best.invocations = counts.get(best.source.xref, 1)
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
                      codec: Optional[_FontCodec], occurrence: int = 0):
    """Replace one occurrence of *word* inside a string operand.

    Returns ``(new_value, status)`` with status ``"ok"`` or ``"not_found"``.
    """
    if isinstance(value, str) and codec is not None and codec.code_width != 1:
        # A literal string in a multi-byte code space cannot be sliced safely.
        return value, "not_found"
    text = _operand_text(value, codec)
    if text is None:
        return value, "not_found"
    idx = _nth_index(text, word, occurrence)
    if idx < 0:
        return value, "not_found"
    return (_splice_segment(value, text, idx, idx + len(word),
                            replacement, codec), "ok")


def _replace_in_tj_array(arr: List[Any], word: str, replacement: str,
                         codec: Optional[_FontCodec], occurrence: int = 0):
    """Replace one occurrence of *word* across a ``TJ`` array's segments.

    Only kerning numbers that fall strictly inside the matched range are
    dropped (they positioned the old glyphs); numbers before and after are
    preserved so the surrounding text keeps its position.  The replacement is
    inserted exactly once.  Returns ``(new_array, status)``.
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
    start = _nth_index(joined, word, occurrence)
    if start < 0:
        return list(arr), "not_found"
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
    codec: Optional[_FontCodec] = None, occurrence: int = 0,
) -> Tuple[List[Any], str]:
    """Replace one occurrence of *word* inside one operator's operands.

    Returns ``(new_operands, status)`` where status is ``"ok"`` or
    ``"not_found"``.  When *codec* is given, strings are decoded/encoded
    through the font's real encoding; otherwise latin-1 is used (unit tests).
    """
    o = op.operator
    if o == "Tj":
        value = op.operands[0] if op.operands else ""
        new_value, status = _replace_in_value(
            value, word, replacement, codec, occurrence)
        return [new_value], status
    if o == "TJ":
        operands = list(op.operands)
        arr = operands[0] if operands else []
        if not isinstance(arr, list):
            return operands, "not_found"
        new_arr, status = _replace_in_tj_array(
            arr, word, replacement, codec, occurrence)
        operands[0] = new_arr
        return operands, status
    if o == "'":
        value = op.operands[0] if op.operands else ""
        new_value, status = _replace_in_value(
            value, word, replacement, codec, occurrence)
        return [new_value], status
    if o == '"':
        value = op.operands[2] if len(op.operands) >= 3 else ""
        new_value, status = _replace_in_value(
            value, word, replacement, codec, occurrence)
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
# Form XObject copy-on-write
# --------------------------------------------------------------------------- #
def _clone_object(doc: fitz.Document, xref: int) -> int:
    """Create a new indirect object that is a copy of *xref*."""
    new_xref = doc.get_new_xref()
    doc.update_object(new_xref, "<<>>")
    doc.xref_copy(xref, new_xref)
    if doc.xref_is_stream(xref):
        # Re-store the decoded stream so /Length is correct and a stale
        # /Filter cannot corrupt later updates.
        doc.update_stream(new_xref, doc.xref_stream(xref))
    return new_xref


def _copy_on_write_xobject(page: fitz.Page, loc: "_OperatorLoc") -> int:
    """Clone the Form XObject behind *loc* and point this page at the clone.

    The original object is never modified: any other page (or other place on
    this page) that shares it keeps rendering exactly as before.  Returns the
    xref of the clone, which is safe to rewrite.
    """
    doc = page.parent
    name = loc.source.resource_name
    if not name or any(ch in name for ch in "/ #()[]<>%"):
        raise EditError(
            f"The Form XObject resource name '{name}' cannot be rewritten "
            f"safely.")

    try:
        res_kind, res_value = _resources_of(doc, page.xref)
    except Exception:
        res_kind, res_value = None, None
    if res_kind is None:
        raise EditError(
            "The page has no resource dictionary, so the Form XObject cannot "
            "be copied safely.")

    # 1) Give the page a private resource dictionary.  It may be inherited
    #    from the page tree or shared with other pages.
    if res_kind == "xref":
        try:
            shared_xref = int(res_value.split()[0])
        except (IndexError, ValueError):
            raise EditError("The page's resource dictionary is malformed.")
        res_xref = _clone_object(doc, shared_xref)
        doc.xref_set_key(page.xref, "Resources", f"{res_xref} 0 R")
        inline_res = False
        res_owner = res_xref
    else:
        inline_res = True
        res_owner = page.xref

    prefix = "Resources/" if inline_res else ""

    # 2) Make the /XObject sub-dictionary private too.
    x_kind, x_value = _subdict(
        doc, "dict" if inline_res else "xref",
        res_value if inline_res else f"{res_owner} 0 R", "XObject")
    if x_kind == "xref":
        try:
            xdict_xref = _clone_object(doc, int(x_value.split()[0]))
        except (IndexError, ValueError):
            raise EditError("The page's /XObject dictionary is malformed.")
        doc.xref_set_key(res_owner, f"{prefix}XObject",
                         f"{xdict_xref} 0 R")
        target = xdict_xref
    elif x_kind == "dict":
        target = res_owner
    else:
        raise EditError(
            "The page's resources have no usable /XObject dictionary, so the "
            "Form XObject cannot be copied safely.")

    # 3) Clone the XObject and point this page's private dictionary at it.
    new_xref = _clone_object(doc, loc.source.xref)
    if target == res_owner:
        key = f"{prefix}XObject/{name}"
    else:
        # The cloned object *is* the /XObject dictionary.
        key = name
    doc.xref_set_key(target, key, f"{new_xref} 0 R")
    return new_xref


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
        counts: Dict[int, int] = {}
        loc = locate_operator_for_word(
            page, self.old_word, self.word_rect, counts)
        if loc is None:
            raise EditError(
                f"Could not locate the text '{self.old_word}' in the page "
                f"content stream. The text may be inside an unsupported "
                f"structure (nested Form XObject, scanned image, …)."
            )

        if loc.source.in_xobject:
            if loc.source.depth > 1:
                raise EditError(
                    f"The text '{self.old_word}' is drawn inside a Form "
                    f"XObject that is nested in another Form XObject. Editing "
                    f"it is not supported because the enclosing object cannot "
                    f"be isolated safely."
                )
            if loc.invocations > 1:
                raise EditError(
                    f"The text '{self.old_word}' is drawn by a Form XObject "
                    f"that is used {loc.invocations} times on this page. "
                    f"Editing it would change every copy, so the edit was "
                    f"refused."
                )

        ok, reason, codec = _validate_replacement(
            doc, page, loc.font_resource, self.new_word, loc.source.res_owner)
        if not ok:
            raise EditError(reason or "The replacement could not be verified.")

        if loc.source.in_xobject:
            data = doc.xref_stream(loc.source.xref)
            if not data:
                raise EditError("The Form XObject content stream is empty.")
            target_xref = None  # decided only after the splice succeeds
        else:
            streams = _page_content_streams(page)
            if loc.source.page_index >= len(streams):
                raise EditError("Content stream index out of range.")
            target_xref, data = streams[loc.source.page_index]

        ops = ContentStreamParser.parse(data)
        if loc.op_index >= len(ops):
            raise EditError("Text operator index out of range.")
        op = ops[loc.op_index]
        if op.operator not in ContentStreamParser.TEXT_SHOW_OPS:
            raise EditError("Located operator is not a text-showing operator.")

        new_operands, status = _replace_in_operands(
            op, self.old_word, self.new_word, codec, loc.occurrence)
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
        if target_xref is None:
            # Never modify a shared object: this page gets its own copy.  Done
            # only now so a failed replacement leaves the document untouched.
            target_xref = _copy_on_write_xobject(page, loc)
        doc.update_stream(target_xref, new_stream)
