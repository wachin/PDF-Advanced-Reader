"""Safe embedding of a replacement font into a PDF.

When the document's original font cannot draw the replacement characters,
PDFAR substitutes a system font and embeds it.  Embedding is the risky half of
that feature: a wrong font dictionary produces a file that looks right in one
viewer and extracts as garbage everywhere else.  This module therefore:

* checks the font's ``fsType`` embedding permission **before** touching the
  document and refuses when the licence forbids embedding;
* only handles TrueType outlines (``glyf``), which can be expressed as
  ``CIDFontType2`` + ``FontFile2`` — the most portable PDF font form;
* subsets the program (when the licence allows) so the file stays small;
* writes an Identity-H ``Type0`` font whose CIDs *are* glyph ids, with a
  ``/W`` width array taken from the font's real metrics and a generated
  ``/ToUnicode`` CMap, so copying and text extraction keep working;
* adds the resource to a **private** dictionary (copy-on-write), never to a
  dictionary shared with other pages or Form XObjects.

Anything unexpected raises :class:`FontEmbedError`; the caller turns that into
a refusal, never into a half-written document.
"""

from __future__ import annotations

import io
import logging
import os
import re
import zlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

try:
    from fontTools.ttLib import TTFont as _TTFont
    from fontTools import subset as _subset
except Exception:  # pragma: no cover - fontTools is an optional extra
    _TTFont = None
    _subset = None

from .font_discovery import FontFace, face_metadata

# fontTools warns about odd timestamps in some vendor fonts; that is noise for
# a GUI application.
logging.getLogger("fontTools").setLevel(logging.ERROR)


class FontEmbedError(Exception):
    """Raised when a font cannot be embedded safely."""


# --------------------------------------------------------------------------- #
# Licensing
# --------------------------------------------------------------------------- #
def embedding_permission(face: FontFace) -> Tuple[bool, str]:
    """Return ``(allowed, reason)`` for embedding *face*.

    ``fsType`` values (PDF/OpenType spec):

    * 0 — Installable: embedding allowed.
    * 2 — Restricted: embedding not allowed.
    * 4 — Preview & Print: embedding allowed, but the document must stay
      read-only; we allow it and force a subset.
    * 8 — Editable: embedding allowed.

    Bit 0x0100 forbids subsetting, bit 0x0200 restricts embedding to bitmaps
    (which a text PDF cannot use).
    """
    meta = face_metadata(face.path, face.index)
    fs_type = meta.get("fs_type")
    if fs_type is None:
        return True, ""
    fs = int(fs_type)  # type: ignore[arg-type]
    if fs & 0x0200:
        return False, "the font may only be embedded as a bitmap"
    perm = fs & 0x000F
    if perm == 0x0002:
        return False, "the font's licence forbids embedding"
    if perm not in (0x0000, 0x0004, 0x0008):
        return False, f"unknown embedding permission (fsType 0x{fs:04X})"
    return True, ""


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #
@dataclass
class EmbeddedFont:
    """A font resource added to a PDF, ready to be used by a text operator."""

    resource_name: str
    xref: int                      # Type0 font object
    basefont: str
    gid_for: Dict[str, int]        # unicode char -> glyph id
    advances: Dict[int, float]     # glyph id -> advance in em units
    #: Vertical metrics in 1000-unit glyph space (as used by FontDescriptor).
    ascender: float
    descender: float
    bbox: Tuple[float, float, float, float]
    subsetted: bool = True
    fs_type: Optional[int] = None

    def encode(self, text: str) -> bytes:
        """Encode *text* as Identity-H two-byte glyph ids."""
        out = bytearray()
        for ch in text:
            gid = self.gid_for.get(ch)
            if gid is None:
                raise FontEmbedError(
                    f"Character {ch!r} (U+{ord(ch):04X}) is not present in the "
                    f"embedded font.")
            out += int(gid).to_bytes(2, "big")
        return bytes(out)

    def width(self, text: str) -> float:
        """Advance of *text* in em units (glyph advances only)."""
        total = 0.0
        for ch in text:
            gid = self.gid_for.get(ch)
            if gid is None:
                raise FontEmbedError(f"Character {ch!r} is not embedded.")
            total += self.advances.get(gid, 1.0)
        return total


# --------------------------------------------------------------------------- #
# Resource plumbing (copy-on-write)
# --------------------------------------------------------------------------- #
def _clone_object(doc: fitz.Document, xref: int) -> int:
    new_xref = doc.get_new_xref()
    doc.update_object(new_xref, "<<>>")
    doc.xref_copy(xref, new_xref)
    if doc.xref_is_stream(xref):
        doc.update_stream(new_xref, doc.xref_stream(xref))
    return new_xref


def _subdict(doc: fitz.Document, kind: str, value: str, key: str):
    if kind == "xref":
        try:
            xref = int(value.split()[0])
            k, v = doc.xref_get_key(xref, key)
        except Exception:
            return None, None
        return (k, v) if k in ("xref", "dict", "array") else (None, None)
    m = re.search(r"/%s\s*(<<|\d+\s+0\s+R)" % re.escape(key), value or "")
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


def _resources_of(doc: fitz.Document, owner_xref: int):
    """Effective ``/Resources`` for *owner_xref*, following ``/Parent``."""
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


def _font_dict_target(doc: fitz.Document,
                      owner_xref: int) -> Tuple[int, str]:
    """Return ``(xref, key_prefix)`` for a writable ``/Font`` dictionary.

    The dictionary is made private to *owner_xref* first: a resources object
    (or a ``/Font`` sub-dictionary) shared with other pages is cloned rather
    than edited, so embedding never leaks into unrelated content.
    """
    try:
        kind, value = doc.xref_get_key(owner_xref, "Resources")
    except Exception:
        kind, value = "null", "null"

    if kind == "xref":
        res_xref = _clone_object(doc, int(value.split()[0]))
        doc.xref_set_key(owner_xref, "Resources", f"{res_xref} 0 R")
        fk, fv = doc.xref_get_key(res_xref, "Font")
        if fk == "xref":
            font_xref = _clone_object(doc, int(fv.split()[0]))
            doc.xref_set_key(res_xref, "Font", f"{font_xref} 0 R")
            return font_xref, ""
        return res_xref, "Font/"

    if kind == "dict":
        fk, fv = _subdict(doc, "dict", value, "Font")
        if fk == "xref":
            font_xref = _clone_object(doc, int(fv.split()[0]))
            doc.xref_set_key(owner_xref, "Resources/Font",
                             f"{font_xref} 0 R")
            return font_xref, ""
        return owner_xref, "Resources/Font/"

    # The owner has no resources of its own: copy the inherited ones (if any)
    # into a new object so the font does not appear page-wide by accident.
    eff_kind, eff_value = _resources_of(doc, owner_xref)
    if eff_kind == "xref":
        new_res = _clone_object(doc, int(eff_value.split()[0]))
    elif eff_kind == "dict":
        new_res = doc.get_new_xref()
        doc.update_object(new_res, eff_value)
    else:
        new_res = doc.get_new_xref()
        doc.update_object(new_res, "<<>>")
    doc.xref_set_key(owner_xref, "Resources", f"{new_res} 0 R")
    return new_res, "Font/"


def existing_font_names(doc: fitz.Document, owner_xref: int) -> set:
    """Names already used in the owner's ``/Font`` dictionary."""
    try:
        kind, value = doc.xref_get_key(owner_xref, "Resources")
    except Exception:
        return set()
    if kind == "xref":
        try:
            res_xref = int(value.split()[0])
            fk, fv = doc.xref_get_key(res_xref, "Font")
        except Exception:
            return set()
        if fk == "xref":
            try:
                return set(doc.xref_get_keys(int(fv.split()[0])))
            except Exception:
                return set()
        if fk == "dict":
            return set(re.findall(r"/([^\s/<>\[\](){}%]+)\s+\d+\s+0\s+R", fv))
        return set()
    if kind == "dict":
        fk, fv = _subdict(doc, "dict", value, "Font")
        if fk == "xref":
            try:
                return set(doc.xref_get_keys(int(fv.split()[0])))
            except Exception:
                return set()
        if fk == "dict":
            return set(re.findall(r"/([^\s/<>\[\](){}%]+)\s+\d+\s+0\s+R", fv))
    return set()


def _unique_name(doc: fitz.Document, owner_xref: int, base: str = "Fsub") -> str:
    used = existing_font_names(doc, owner_xref)
    n = 1
    while f"{base}{n}" in used:
        n += 1
    return f"{base}{n}"


# --------------------------------------------------------------------------- #
# Font program preparation
# --------------------------------------------------------------------------- #
def _load_ttfont(face: FontFace):
    if _TTFont is None:
        raise FontEmbedError(
            "The 'fonttools' Python package is required to embed a replacement "
            "font. Install it (pip install fonttools) and try again.")
    try:
        return _TTFont(face.path, fontNumber=face.index, lazy=False)
    except Exception as exc:
        raise FontEmbedError(
            f"Could not read the font file '{os.path.basename(face.path)}': "
            f"{exc}") from exc


def _subset_font(font, text: str) -> Tuple[bytes, bool]:
    """Subset *font* to the characters in *text*; return ``(bytes, subsetted)``."""
    if _subset is None:
        raise FontEmbedError("fontTools.subset is not available.")
    options = _subset.Options()
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.recalc_bounds = True
    options.layout_features = ["*"]
    options.drop_tables += ["FFTM", "GDEF", "GPOS", "GSUB", "DSIG"]
    subsetter = _subset.Subsetter(options=options)
    subsetter.populate(unicodes=[ord(ch) for ch in dict.fromkeys(text)])
    subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue(), True


def _font_name(face: FontFace, text: str, subsetted: bool) -> str:
    base = re.sub(r"[^A-Za-z0-9]", "", face.family) or "Substitute"
    if not subsetted:
        return base[:40]
    tag = "%06X" % (zlib.crc32(text.encode("utf-8")) & 0xFFFFFF)
    return f"{tag}+{base[:34]}"


# --------------------------------------------------------------------------- #
# Embedding
# --------------------------------------------------------------------------- #
def build_font(doc: fitz.Document, face: FontFace, text: str) -> EmbeddedFont:
    """Create the PDF font objects for *face* covering *text*.

    Nothing is added to any resource dictionary yet, so a later failure leaves
    the document's visible structure untouched (the orphan objects are removed
    by a garbage-collecting save).
    """
    allowed, reason = embedding_permission(face)
    if not allowed:
        raise FontEmbedError(
            f"The font '{face.display_name}' cannot be embedded because "
            f"{reason}.")

    # Newlines and carriage returns are line/paragraph separators in the text
    # being laid out, never glyphs to be drawn or embedded.  A multi-line
    # paragraph's *new_text* carries them between rows, so a font could never
    # "draw" one — which used to refuse every multi-line paragraph that needed
    # font substitution (single-word substitution, which passes no newline,
    # kept working, so paragraph edits silently failed to apply and so did not
    # survive Save).  Drop line separators before measuring font coverage or
    # building the subset; they are not written to the content stream as glyphs.
    text = text.replace("\n", "").replace("\r", "")

    chars = "".join(dict.fromkeys(text))
    if not chars:
        raise FontEmbedError("There is no text to embed a font for.")

    font = _load_ttfont(face)
    try:
        tables = set(font.keys())
        if "glyf" not in tables:
            raise FontEmbedError(
                f"The font '{face.display_name}' uses CFF/PostScript outlines, "
                f"which this version cannot embed safely. Choose a TrueType "
                f"(.ttf) font instead.")
        cmap = font.getBestCmap() or {}
        missing = [ch for ch in chars if ord(ch) not in cmap]
        if missing:
            raise FontEmbedError(
                "The font cannot draw: " + ", ".join(repr(c) for c in missing))

        subsetted = False
        if face.subset_allowed:
            try:
                data, subsetted = _subset_font(font, chars)
            except Exception:
                # Subsetting is an optimisation; a failed subset must not
                # prevent a correct (larger) embedding.
                subsetted = False
        if not subsetted:
            buf = io.BytesIO()
            font.save(buf)
            data = buf.getvalue()

        # Re-read the final program: subsetting renumbers glyph ids, so the
        # mapping must come from the bytes that are actually embedded.
        final = _TTFont(io.BytesIO(data))
        try:
            cmap = final.getBestCmap() or {}
            hmtx = final["hmtx"]
            upem = int(final["head"].unitsPerEm)
            gid_for: Dict[str, int] = {}
            advances: Dict[int, float] = {}
            for ch in chars:
                name = cmap.get(ord(ch))
                if name is None:
                    raise FontEmbedError(
                        f"Character {ch!r} was lost while subsetting the font.")
                gid = final.getGlyphID(name)
                gid_for[ch] = gid
                advances[gid] = hmtx[name][0] / upem
            head = final["head"]
            hhea = final["hhea"]
            os2 = final["OS/2"] if "OS/2" in final else None
            ascender = hhea.ascent * 1000.0 / upem
            descender = hhea.descent * 1000.0 / upem
            bbox = (head.xMin * 1000.0 / upem, head.yMin * 1000.0 / upem,
                    head.xMax * 1000.0 / upem, head.yMax * 1000.0 / upem)
            cap_height = 700.0
            if os2 is not None and getattr(os2, "sCapHeight", 0):
                cap_height = os2.sCapHeight * 1000.0 / upem
        finally:
            final.close()
    finally:
        font.close()

    basefont = _font_name(face, chars, subsetted)

    # 1) Font program stream.
    ff_xref = doc.get_new_xref()
    doc.update_object(ff_xref, "<<>>")
    doc.update_stream(ff_xref, data)
    doc.xref_set_key(ff_xref, "Length1", str(len(data)))

    # 2) Font descriptor.
    flags = 32  # nonsymbolic
    if face.monospaced:
        flags |= 1
    if face.serif:
        flags |= 2
    if face.italic:
        flags |= 64
    desc_xref = doc.get_new_xref()
    doc.update_object(desc_xref, (
        "<< /Type /FontDescriptor /FontName /%s /Flags %d"
        " /FontBBox [%.0f %.0f %.0f %.0f] /ItalicAngle %d"
        " /Ascent %.0f /Descent %.0f /CapHeight %.0f /StemV 80"
        " /FontFile2 %d 0 R >>"
        % (basefont, flags, bbox[0], bbox[1], bbox[2], bbox[3],
           -12 if face.italic else 0, ascender, descender, cap_height,
           ff_xref)))

    # 3) CIDFont: CID == GID (CIDToGIDMap /Identity).
    widths = " ".join("%d [%d]" % (gid, round(w * 1000.0))
                      for gid, w in sorted(advances.items()))
    cid_xref = doc.get_new_xref()
    doc.update_object(cid_xref, (
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s"
        " /CIDSystemInfo << /Registry (Adobe) /Ordering (Identity)"
        " /Supplement 0 >> /FontDescriptor %d 0 R"
        " /CIDToGIDMap /Identity /DW 1000 /W [%s] >>"
        % (basefont, desc_xref, widths)))

    # 4) ToUnicode CMap, so extraction and copy keep the real characters.
    tu_xref = doc.get_new_xref()
    doc.update_object(tu_xref, "<<>>")
    doc.update_stream(tu_xref, _tounicode_cmap(gid_for).encode("latin-1"))

    # 5) Type0 parent.
    t0_xref = doc.get_new_xref()
    doc.update_object(t0_xref, (
        "<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H"
        " /DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>"
        % (basefont, cid_xref, tu_xref)))

    return EmbeddedFont(
        resource_name="",
        xref=t0_xref,
        basefont=basefont,
        gid_for=gid_for,
        advances=advances,
        ascender=ascender,
        descender=descender,
        bbox=bbox,
        subsetted=subsetted,
        fs_type=face.fs_type,
    )


def attach_font(doc: fitz.Document, owner_xref: int,
                embedded: EmbeddedFont) -> str:
    """Add *embedded* to *owner_xref*'s (private) ``/Font`` dictionary.

    *owner_xref* is the object whose ``/Resources`` are in force for the text
    being edited — the page, or the Form XObject that owns the operator.  The
    dictionary is made private first, so a dictionary shared with other pages
    or XObjects is cloned rather than edited.
    """
    target, prefix = _font_dict_target(doc, owner_xref)
    name = _unique_name(doc, owner_xref)
    doc.xref_set_key(target, f"{prefix}{name}", f"{embedded.xref} 0 R")
    embedded.resource_name = name
    return name


def embed_font(doc: fitz.Document, owner_xref: int, face: FontFace,
               text: str) -> EmbeddedFont:
    """Build and attach a font in one step (build + :func:`attach_font`)."""
    embedded = build_font(doc, face, text)
    attach_font(doc, owner_xref, embedded)
    return embedded


def _tounicode_cmap(gid_for: Dict[str, int]) -> str:
    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS)"
        " /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    pairs = sorted(((gid, ch) for ch, gid in gid_for.items()))
    for start in range(0, len(pairs), 100):
        chunk = pairs[start:start + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for gid, ch in chunk:
            utf16 = ch.encode("utf-16-be").hex().upper()
            lines.append(f"<{gid:04X}> <{utf16}>")
        lines.append("endbfchar")
    lines += [
        "endcmap",
        "CMapName currentdict /CMap defineresource pop",
        "end",
        "end",
    ]
    return "\n".join(lines) + "\n"
