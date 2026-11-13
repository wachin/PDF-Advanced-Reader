"""Font metrics and encoding for existing PDF text.

Independent Python implementation of the parts of ``pdfcraft-fonts`` that the
text engine needs: code splitting, glyph widths, Unicode mapping through
``/ToUnicode`` (and the simple-font encodings), and encoding new text back into
the font's own codes so glyphs and kerning are reused.

PyMuPDF supplies the PDF object access (``xref_get_key``, ``xref_stream``,
``extract_font``) and, for the standard 14 fonts, the published metrics.

Adapted concepts (not code) from PdfCraft, MIT OR Apache-2.0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ._compat import fitz

from .stream import Name, Ref, _parse_value


# ---------------------------------------------------------------------------
# PDF object access helpers
# ---------------------------------------------------------------------------


def _ref_num(value: object) -> Optional[int]:
    if isinstance(value, Ref):
        return value.num
    return None


def get_key(doc: fitz.Document, xref: int, key: str):
    """Read one key of an object, resolving names/arrays/dicts into Python values."""
    kind, text = doc.xref_get_key(xref, key)
    if kind == "xref":
        return Ref(*[int(v) for v in text.split()[:2]])
    if kind == "null":
        return None
    if kind == "name":
        return Name(text[1:] if text.startswith("/") else text)
    if kind in ("array", "dict"):
        value, _ = _parse_value(text.encode("latin-1"), 0)
        return value
    if kind == "string":
        value, _ = _parse_value(text.encode("latin-1"), 0)
        return value
    if kind in ("number", "int", "float"):
        return float(text) if "." in text else int(text)
    if kind == "bool":
        return text == "true"
    return text


def object_source(doc: fitz.Document, value):
    """Resolve a Ref to a parsed object (array/dict), reading object or stream source."""
    num = _ref_num(value)
    if num is None:
        return value
    if doc.xref_is_stream(num):
        try:
            data = doc.xref_stream(num)
        except Exception:
            return None
        parsed, _ = _parse_value(data, 0)
        return parsed
    try:
        source = doc.xref_object(num, compressed=False)
    except Exception:
        return None
    parsed, _ = _parse_value(source.encode("latin-1"), 0)
    return parsed


def stream_bytes(doc: fitz.Document, value) -> bytes:
    num = _ref_num(value)
    if num is None:
        return b""
    try:
        return doc.xref_stream(num) or b""
    except Exception:
        return b""


# ---------------------------------------------------------------------------
# Encodings and glyph names
# ---------------------------------------------------------------------------


# A compact Adobe Glyph List subset: the names producers put in /Differences.
_AGL = {
    "space": " ",
    "exclam": "!",
    "quotedbl": '"',
    "numbersign": "#",
    "dollar": "$",
    "percent": "%",
    "ampersand": "&",
    "quotesingle": "'",
    "parenleft": "(",
    "parenright": ")",
    "asterisk": "*",
    "plus": "+",
    "comma": ",",
    "hyphen": "-",
    "period": ".",
    "slash": "/",
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "colon": ":",
    "semicolon": ";",
    "less": "<",
    "equal": "=",
    "greater": ">",
    "question": "?",
    "at": "@",
    "bracketleft": "[",
    "backslash": "\\",
    "bracketright": "]",
    "asciicircum": "^",
    "underscore": "_",
    "grave": "`",
    "braceleft": "{",
    "bar": "|",
    "braceright": "}",
    "asciitilde": "~",
    "quoteright": "\u2019",
    "quoteleft": "\u2018",
    "quotedblleft": "\u201c",
    "quotedblright": "\u201d",
    "endash": "\u2013",
    "emdash": "\u2014",
    "bullet": "\u2022",
    "ellipsis": "\u2026",
    "fi": "fi",
    "fl": "fl",
    "ff": "ff",
    "ffi": "ffi",
    "ffl": "ffl",
    "germandbls": "\u00df",
    "ae": "\u00e6",
    "oe": "\u0153",
    "oslash": "\u00f8",
    "aring": "\u00e5",
    "ccedilla": "\u00e7",
    "eacute": "\u00e9",
    "egrave": "\u00e8",
    "agrave": "\u00e0",
    "udieresis": "\u00fc",
    "odieresis": "\u00f6",
    "adieresis": "\u00e4",
    "ntilde": "\u00f1",
}

_GLYPH_UNI = re.compile(r"^uni([0-9A-Fa-f]{4,6})$")
_GLYPH_U = re.compile(r"^u([0-9A-Fa-f]{4,6})$")


def glyph_unicode(name: str) -> Optional[str]:
    if name in _AGL:
        return _AGL[name]
    if len(name) == 1:
        return name
    match = _GLYPH_UNI.match(name) or _GLYPH_U.match(name)
    if match:
        try:
            return chr(int(match.group(1), 16))
        except ValueError:
            return None
    # A few common suffixed names: "a.sc", "one.oldstyle".
    base = name.split(".")[0]
    if base != name:
        return glyph_unicode(base)
    return None


def _simple_encoding_table(name: str) -> Dict[int, str]:
    """code -> Unicode for the common simple-font base encodings."""
    table: Dict[int, str] = {}
    if name in ("WinAnsiEncoding", "PDFDocEncoding"):
        codec = "cp1252"
    elif name == "MacRomanEncoding":
        codec = "mac_roman"
    elif name == "StandardEncoding":
        # Close enough for the printable ASCII range; producers using
        # StandardEncoding almost always carry /Differences or /ToUnicode.
        codec = "latin-1"
    else:
        codec = "latin-1"
    for code in range(32, 256):
        try:
            table[code] = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            continue
    table[32] = " "
    return table


# ---------------------------------------------------------------------------
# Standard 14
# ---------------------------------------------------------------------------

_STD14 = {
    "Helvetica": "helv",
    "Helvetica-Bold": "hebo",
    "Helvetica-Oblique": "heit",
    "Helvetica-BoldOblique": "hebi",
    "Times-Roman": "tiro",
    "Times-Bold": "tibo",
    "Times-Italic": "tiit",
    "Times-BoldItalic": "tibi",
    "Courier": "cour",
    "Courier-Bold": "cobo",
    "Courier-Oblique": "coit",
    "Courier-BoldOblique": "cobi",
    "Symbol": "symb",
    "ZapfDingbats": "zadb",
}


def _std14_name(base_font: str) -> Optional[str]:
    stripped = strip_subset_tag(base_font)
    if stripped in _STD14:
        return _STD14[stripped]
    # Common aliases and the names PyMuPDF itself writes.
    aliases = {
        "Arial": "helv",
        "Arial-Bold": "hebo",
        "Arial-Italic": "heit",
        "Arial-BoldItalic": "hebi",
        "Liberation Sans": "helv",
        "LiberationSans": "helv",
    }
    return aliases.get(stripped)


def strip_subset_tag(name: str) -> str:
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


# ---------------------------------------------------------------------------
# CMap (/ToUnicode and embedded encodings)
# ---------------------------------------------------------------------------


@dataclass
class CMap:
    # code -> Unicode text
    unicode: Dict[int, str] = field(default_factory=dict)
    # (low, high, base UTF-16 bytes) ranges, expanded lazily
    ranges: List[Tuple[int, int, bytes]] = field(default_factory=list)
    # (low, high, cid_low)
    cid_ranges: List[Tuple[int, int, int]] = field(default_factory=list)
    cid_chars: Dict[int, int] = field(default_factory=dict)
    code_lengths: List[int] = field(default_factory=list)
    #: 1 for a vertical CMap: the pen advances down, not right.
    wmode: int = 0

    def text_of(self, code: int) -> Optional[str]:
        if code in self.unicode:
            return self.unicode[code]
        for low, high, base in self.ranges:
            if low <= code <= high:
                return _utf16_text(_increment(base, code - low))
        return None

    def cid_of(self, code: int) -> int:
        if code in self.cid_chars:
            return self.cid_chars[code]
        for low, high, cid_low in self.cid_ranges:
            if low <= code <= high:
                return cid_low + (code - low)
        return code


def _utf16_text(data: bytes) -> str:
    if len(data) % 2:
        data += b"\x00"
    try:
        return data.decode("utf-16-be", errors="replace")
    except Exception:
        return ""


def _increment(base: bytes, delta: int) -> bytes:
    if not base:
        return base
    value = int.from_bytes(base, "big") + delta
    try:
        return value.to_bytes(len(base), "big")
    except OverflowError:
        # A malformed bfrange whose last entry does not fit the base width;
        # clamp instead of aborting the whole CMap read.
        return b"\x00" * len(base)


_HEX = re.compile(rb"<([0-9A-Fa-f\s]*)>")


def parse_cmap(data: bytes) -> CMap:
    cmap = CMap()
    text = data
    wmode = re.search(rb"/WMode\s+(\d+)", text)
    if wmode:
        cmap.wmode = int(wmode.group(1))
    # codespacerange: <lo> <hi>
    for block in re.findall(rb"begincodespacerange(.*?)endcodespacerange", text, re.S):
        for lo, hi in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            cmap.code_lengths.append(len(lo) // 2)
    # bfchar: <src> <dst>
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            try:
                cmap.unicode[int(src, 16)] = _utf16_text(bytes.fromhex(dst.decode()))
            except ValueError:
                continue
    # bfrange: <lo> <hi> <dst>  or  <lo> <hi> [<d1> <d2> ...]
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for match in re.finditer(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])", block
        ):
            lo = int(match.group(1), 16)
            hi = int(match.group(2), 16)
            target = match.group(3)
            if target.startswith(b"["):
                for offset, hex_value in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", target)):
                    try:
                        cmap.unicode[lo + offset] = _utf16_text(bytes.fromhex(hex_value.decode()))
                    except ValueError:
                        pass
            else:
                hex_value = target[1:-1]
                try:
                    base = bytes.fromhex(hex_value.decode())
                except ValueError:
                    continue
                cmap.ranges.append((lo, hi, base))
    # cidchar / cidrange (embedded CID CMaps)
    for block in re.findall(rb"begincidchar(.*?)endcidchar", text, re.S):
        for src, cid in re.findall(rb"<([0-9A-Fa-f]+)>\s*(\d+)", block):
            cmap.cid_chars[int(src, 16)] = int(cid)
    for block in re.findall(rb"begincidrange(.*?)endcidrange", text, re.S):
        for lo, hi, cid in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\d+)", block):
            cmap.cid_ranges.append((int(lo, 16), int(hi, 16), int(cid)))
    return cmap


# ---------------------------------------------------------------------------
# FontMetrics
# ---------------------------------------------------------------------------


@dataclass
class FontMetrics:
    xref: int
    base_font: str = ""
    subtype: str = ""
    composite: bool = False
    code_len: int = 1
    scale: float = 0.001
    first: int = 0
    widths: List[float] = field(default_factory=list)
    default_width: float = 0.0
    cid_widths: Dict[int, float] = field(default_factory=dict)
    cid_ranges: List[Tuple[int, int, float]] = field(default_factory=list)
    cid_map: Optional[CMap] = None
    unicode: Optional[CMap] = None
    #: True when the font writes vertically (``/WMode 1``); the layout does not
    #: support it yet, so a caller must refuse rather than guess.
    vertical: bool = False
    encoding: Dict[int, str] = field(default_factory=dict)
    # Code chosen by /Differences: it beats the base encoding when writing.
    explicit: Dict[str, int] = field(default_factory=dict)
    ascent: float = 0.8
    descent: float = -0.2
    bold: bool = False
    italic: bool = False
    std14: Optional[fitz.Font] = None
    subset: bool = False
    _reverse: Dict[str, int] = field(default_factory=dict)
    _font: Optional[fitz.Font] = None
    _reverse_built: bool = False

    # -- construction -----------------------------------------------------

    @classmethod
    def from_doc(cls, doc: fitz.Document, xref: int) -> "FontMetrics":
        metrics = cls(xref=xref)
        metrics.subtype = str(get_key(doc, xref, "Subtype") or "")
        metrics.base_font = str(get_key(doc, xref, "BaseFont") or "")
        metrics.subset = bool(re.match(r"^[A-Z]{6}\+", metrics.base_font))
        plain_name = strip_subset_tag(metrics.base_font)
        metrics.bold = "Bold" in plain_name or "Black" in plain_name or "Semibold" in plain_name
        metrics.italic = "Italic" in plain_name or "Oblique" in plain_name

        if metrics.subtype == "Type0":
            metrics.composite = True
            metrics.code_len = 2
            metrics.scale = 0.001
            metrics._read_composite(doc)
        else:
            metrics.composite = False
            metrics.code_len = 1
            metrics._read_simple(doc)
            if metrics.subtype == "Type3":
                matrix = get_key(doc, xref, "FontMatrix")
                if isinstance(matrix, list) and matrix:
                    try:
                        metrics.scale = float(matrix[0])
                    except (TypeError, ValueError):
                        metrics.scale = 0.001

        metrics._read_descriptor(doc)
        metrics._load_program(doc)
        if not metrics.widths and not metrics.cid_widths and not metrics.cid_ranges:
            metrics.std14 = _std14_name(metrics.base_font)
            if metrics.std14:
                try:
                    metrics._font = fitz.Font(metrics.std14)
                except Exception:
                    metrics._font = None
        return metrics

    def _read_simple(self, doc: fitz.Document) -> None:
        first = get_key(doc, self.xref, "FirstChar")
        self.first = int(first) if isinstance(first, (int, float)) else 0
        widths = object_source(doc, get_key(doc, self.xref, "Widths"))
        if isinstance(widths, list):
            for value in widths:
                if isinstance(value, (int, float)):
                    self.widths.append(float(value) * 0.001)
        missing = get_key(doc, self.xref, "FontDescriptor")
        if isinstance(missing, Ref):
            mw = get_key(doc, missing.num, "MissingWidth")
            if isinstance(mw, (int, float)):
                self.default_width = float(mw) * 0.001

        # Encoding: a base encoding name, or a dict with /BaseEncoding and /Differences.
        encoding = get_key(doc, self.xref, "Encoding")
        base_name = "WinAnsiEncoding"
        differences = None
        if isinstance(encoding, Name):
            base_name = str(encoding)
        elif isinstance(encoding, dict):
            base = encoding.get("BaseEncoding")
            if isinstance(base, Name):
                base_name = str(base)
            differences = encoding.get("Differences")
        self.encoding = _simple_encoding_table(base_name)
        if isinstance(differences, list):
            code = 0
            for item in differences:
                if isinstance(item, (int, float)):
                    code = int(item)
                elif isinstance(item, Name):
                    char = glyph_unicode(str(item))
                    if char is not None:
                        self.encoding[code] = char
                        self.explicit[char] = code
                    code += 1

        to_unicode = get_key(doc, self.xref, "ToUnicode")
        if to_unicode is not None:
            self.unicode = parse_cmap(stream_bytes(doc, to_unicode))

    def _read_composite(self, doc: fitz.Document) -> None:
        descendants = object_source(doc, get_key(doc, self.xref, "DescendantFonts"))
        descendant_xref = None
        if isinstance(descendants, list) and descendants:
            descendant_xref = _ref_num(descendants[0])
        if descendant_xref is None:
            return
        dw = get_key(doc, descendant_xref, "DW")
        self.default_width = (float(dw) * 0.001) if isinstance(dw, (int, float)) else 1.0
        w_array = object_source(doc, get_key(doc, descendant_xref, "W"))
        self._read_w_array(w_array)

        encoding = get_key(doc, self.xref, "Encoding")
        if isinstance(encoding, Ref):
            cmap = parse_cmap(stream_bytes(doc, encoding))
            self.cid_map = cmap
            self.vertical = cmap.wmode == 1
            if cmap.code_lengths:
                self.code_len = max(1, max(cmap.code_lengths))
        elif isinstance(encoding, Name) and str(encoding).endswith("-V"):
            self.code_len = 2
            self.vertical = True

        to_unicode = get_key(doc, self.xref, "ToUnicode")
        if to_unicode is not None:
            self.unicode = parse_cmap(stream_bytes(doc, to_unicode))

    def _read_w_array(self, w_array) -> None:
        if not isinstance(w_array, list):
            return
        i = 0
        while i < len(w_array):
            first = w_array[i]
            if not isinstance(first, (int, float)):
                i += 1
                continue
            first = int(first)
            nxt = w_array[i + 1] if i + 1 < len(w_array) else None
            if isinstance(nxt, list):
                for offset, width in enumerate(nxt):
                    if isinstance(width, (int, float)):
                        self.cid_widths[first + offset] = float(width) * 0.001
                i += 2
            elif isinstance(nxt, (int, float)) and i + 2 < len(w_array):
                last = int(nxt)
                width = w_array[i + 2]
                if isinstance(width, (int, float)):
                    self.cid_ranges.append((first, last, float(width) * 0.001))
                i += 3
            else:
                i += 1

    def _read_descriptor(self, doc: fitz.Document) -> None:
        descriptor = get_key(doc, self.xref, "FontDescriptor")
        if self.composite:
            descendants = object_source(doc, get_key(doc, self.xref, "DescendantFonts"))
            if isinstance(descendants, list) and descendants:
                descendant_xref = _ref_num(descendants[0])
                if descendant_xref is not None:
                    descriptor = get_key(doc, descendant_xref, "FontDescriptor")
        if isinstance(descriptor, Ref):
            ascent = get_key(doc, descriptor.num, "Ascent")
            descent = get_key(doc, descriptor.num, "Descent")
            if isinstance(ascent, (int, float)) and ascent:
                self.ascent = float(ascent) / 1000.0
            if isinstance(descent, (int, float)) and descent:
                self.descent = float(descent) / 1000.0
            flags = get_key(doc, descriptor.num, "Flags")
            if isinstance(flags, (int, float)):
                self.italic = self.italic or bool(int(flags) & (1 << 6))
                self.bold = self.bold or bool(int(flags) & (1 << 18))
            weight = get_key(doc, descriptor.num, "FontWeight")
            if isinstance(weight, (int, float)) and float(weight) >= 600:
                self.bold = True

    def _load_program(self, doc: fitz.Document) -> None:
        try:
            info = doc.extract_font(self.xref)
        except Exception:
            return
        buffer = info[3] if len(info) > 3 else None
        if not buffer:
            return
        try:
            self._font = fitz.Font(fontbuffer=buffer)
            if self.ascent == 0.8:
                self.ascent = self._font.ascender
            if self.descent == -0.2:
                self.descent = self._font.descender
        except Exception:
            self._font = None

    # -- queries ----------------------------------------------------------

    def codes(self, data: bytes) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        i = 0
        length = max(1, self.code_len)
        while i < len(data):
            chunk = data[i : i + length]
            out.append((int.from_bytes(chunk, "big"), len(chunk)))
            i += len(chunk)
        return out

    def text_of(self, code: int) -> Optional[str]:
        if self.unicode is not None:
            text = self.unicode.text_of(code)
            if text is not None:
                return text
        if not self.composite:
            return self.encoding.get(code)
        return None

    def decode(self, data: bytes) -> str:
        return "".join(self.text_of(code) or "" for code, _ in self.codes(data))

    def cid(self, code: int) -> int:
        if self.cid_map is not None:
            return self.cid_map.cid_of(code)
        return code

    def width(self, code: int) -> float:
        if self.composite:
            cid = self.cid(code)
            if cid in self.cid_widths:
                return self.cid_widths[cid]
            for low, high, width in self.cid_ranges:
                if low <= cid <= high:
                    return width
            return self.default_width
        index = code - self.first
        if 0 <= index < len(self.widths):
            return self.widths[index]
        if self._font is not None:
            char = self.text_of(code)
            if char and len(char) == 1:
                try:
                    return float(self._font.glyph_advance(ord(char)))
                except Exception:
                    pass
        return self.default_width

    def is_space(self, code: int, length: int) -> bool:
        return code == 32 and length == 1

    def has_glyph(self, code: int) -> bool:
        if self.composite:
            cid = self.cid(code)
            if cid == 0:
                return False
            if cid in self.cid_widths:
                return True
            if any(low <= cid <= high for low, high, _w in self.cid_ranges):
                return True
            return not self.subset
        index = code - self.first
        if 0 <= index < len(self.widths):
            return self.widths[index] > 0 or code == 32
        return not self.widths and not self.subset

    def advance(self, text: str, size: float, char_spacing: float = 0.0, word_spacing: float = 0.0) -> float:
        """Advance of ``text`` in text space at ``size``."""
        encoded = self.encode(text)
        if encoded is not None:
            total = 0.0
            for code, length in self.codes(encoded):
                total += self.width(code) * size + char_spacing
                if self.is_space(code, length):
                    total += word_spacing
            return total
        # A font whose subset lacks a space: measure words and add the space.
        total = 0.0
        space_width = self.space_width()
        for index, word in enumerate(text.split(" ")):
            if index:
                total += space_width * size + char_spacing + word_spacing
            if word:
                encoded = self.encode(word)
                if encoded is None:
                    continue
                for code, _length in self.codes(encoded):
                    total += self.width(code) * size + char_spacing
        return total

    def space_width(self) -> float:
        encoded = self.encode(" ")
        if encoded is not None:
            codes = self.codes(encoded)
            if codes:
                return sum(self.width(code) for code, _ in codes)
        return 0.33

    # -- encoding ---------------------------------------------------------

    def _build_reverse(self) -> None:
        if self._reverse_built:
            return
        self._reverse_built = True
        for char, code in self.explicit.items():
            self._reverse[char] = code
        if self.unicode is not None:
            for code, text in self.unicode.unicode.items():
                if len(text) == 1:
                    self._reverse.setdefault(text, code)
            for low, high, base in self.unicode.ranges:
                span = high - low
                if span > 65536:
                    continue
                for offset in range(span + 1):
                    text = _utf16_text(_increment(base, offset))
                    if len(text) == 1:
                        self._reverse.setdefault(text, low + offset)
        for code, text in self.encoding.items():
            if len(text) == 1:
                self._reverse.setdefault(text, code)

    def encode(self, text: str) -> Optional[bytes]:
        """The bytes that show ``text`` in this font, or ``None`` if it can't."""
        self._build_reverse()
        out = bytearray()
        for char in text:
            code = self._reverse.get(char)
            if code is None or not self.has_glyph(code):
                return None
            out.extend(code.to_bytes(self.code_len, "big"))
        return bytes(out)


def win_ansi_bytes(text: str) -> Optional[bytes]:
    """Encode ``text`` for a WinAnsi base-14 font, or ``None`` if impossible."""
    try:
        return text.encode("cp1252")
    except UnicodeEncodeError:
        return None
