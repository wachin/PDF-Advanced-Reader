"""Phase 4: safe font embedding.

Embedding is the part that can silently corrupt a document, so these tests
check the generated PDF structure (Type0 + CIDFontType2 + FontFile2 +
ToUnicode), the licence gate, subsetting, copy-on-write resource handling and
that the embedded program really contains the glyphs it claims to.
"""

from __future__ import annotations

import glob
import io
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pymupdf as fitz
import pytest

from fontTools.ttLib import TTFont

from pdfar import font_discovery as fd
from pdfar.font_discovery import FontFace, face_from_file
from pdfar.font_embed import (
    FontEmbedError,
    EmbeddedFont,
    _font_name,
    _tounicode_cmap,
    build_font,
    embed_font,
    embedding_permission,
    existing_font_names,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _find(*names):
    for name in names:
        hits = glob.glob(f"/usr/share/fonts/**/{name}", recursive=True)
        if hits:
            return hits[0]
    return None


DEJAVU = _find("DejaVuSans.ttf")
DEJAVU_BOLD = _find("DejaVuSans-Bold.ttf")

needs_dejavu = pytest.mark.skipif(DEJAVU is None,
                                  reason="DejaVuSans.ttf is not installed")


def _cff_font():
    for path in glob.glob("/usr/share/fonts/**/*.otf", recursive=True):
        try:
            font = TTFont(path, lazy=True)
            try:
                if "CFF " in font.keys():
                    return path
            finally:
                font.close()
        except Exception:
            continue
    return None


def _font_with_fs_type(tmp_path, fs_type, name="licensed.ttf"):
    """A copy of DejaVuSans whose OS/2 fsType is rewritten."""
    font = TTFont(DEJAVU)
    try:
        font["OS/2"].fsType = fs_type
        out = str(tmp_path / name)
        font.save(out)
    finally:
        font.close()
    return out


def _new_doc(pages=1):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=400, height=300)
        page.insert_text((8, 20), ".")
    return doc


def _font_stream(doc, font_xref):
    """Return ``(descriptor_xref, fontfile_xref, bytes)`` for a Type0 font."""
    kind, value = doc.xref_get_key(font_xref, "DescendantFonts")
    assert kind == "array"
    cid = int(value.strip("[] ").split()[0])
    kind, value = doc.xref_get_key(cid, "FontDescriptor")
    desc = int(value.split()[0])
    kind, value = doc.xref_get_key(desc, "FontFile2")
    assert kind == "xref", "the font has no embedded program"
    ff = int(value.split()[0])
    return desc, ff, doc.xref_stream(ff)


@pytest.fixture(autouse=True)
def _fresh_cache():
    fd.clear_cache()
    yield
    fd.clear_cache()


# --------------------------------------------------------------------------- #
# Licensing gate
# --------------------------------------------------------------------------- #
@needs_dejavu
@pytest.mark.parametrize("fs_type,allowed", [
    (0x0000, True),
    (0x0004, True),
    (0x0008, True),
    (0x0100, True),
    (0x0002, False),
    (0x0200, False),
    (0x0202, False),
    (0x0002 | 0x0100, False),
])
def test_embedding_permission_matrix(tmp_path, fs_type, allowed):
    face = face_from_file(_font_with_fs_type(tmp_path, fs_type))
    ok, reason = embedding_permission(face)
    assert ok is allowed
    if not allowed:
        assert reason


@needs_dejavu
def test_preview_and_print_licence_is_allowed(tmp_path):
    face = face_from_file(_font_with_fs_type(tmp_path, 0x0004))
    assert embedding_permission(face) == (True, "")


# --------------------------------------------------------------------------- #
# Building the font objects
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_build_font_returns_identity_h_encoding():
    doc = _new_doc()
    try:
        face = face_from_file(DEJAVU)
        embedded = build_font(doc, face, "Café ñandú")
        assert isinstance(embedded, EmbeddedFont)
        assert embedded.subsetted is True
        assert set(embedded.gid_for) == set("Café ñandú")
        assert embedded.encode("Café") == b"".join(
            embedded.gid_for[ch].to_bytes(2, "big") for ch in "Café")
        assert len(embedded.encode("Café")) == 8
        assert embedded.width("Café") > 0

        obj = doc.xref_object(embedded.xref)
        assert "/Subtype /Type0" in obj
        assert "/Encoding /Identity-H" in obj
        assert "/ToUnicode" in obj
        assert "/DescendantFonts" in obj
        # Nothing attached yet: the page resources are untouched.
        assert "Fsub1" not in existing_font_names(doc, doc[0].xref)
    finally:
        doc.close()


@needs_dejavu
def test_build_font_subsets_the_program():
    doc = _new_doc()
    try:
        embedded = build_font(doc, face_from_file(DEJAVU), "Café ñandú")
        _desc, _ff, data = _font_stream(doc, embedded.xref)
        assert len(data) < os.path.getsize(DEJAVU) // 4
        program = TTFont(io.BytesIO(data))
        try:
            cmap = program.getBestCmap()
            for ch in "Café ñandú":
                assert ord(ch) in cmap, ch
        finally:
            program.close()
    finally:
        doc.close()


@needs_dejavu
def test_build_font_writes_descriptor_and_metrics():
    doc = _new_doc()
    try:
        embedded = build_font(doc, face_from_file(DEJAVU), "Hola")
        desc, _ff, _data = _font_stream(doc, embedded.xref)
        obj = doc.xref_object(desc)
        for key in ("/FontName", "/Flags", "/FontBBox", "/ItalicAngle",
                    "/Ascent", "/Descent", "/CapHeight", "/StemV"):
            assert key in obj, key
        assert embedded.ascender > 0
        assert embedded.descender < 0
        # The CID font maps CIDs to the same glyph ids.
        kind, value = doc.xref_get_key(embedded.xref, "DescendantFonts")
        cid = int(value.strip("[] ").split()[0])
        cid_obj = doc.xref_object(cid)
        assert "/Subtype /CIDFontType2" in cid_obj
        assert "/CIDToGIDMap /Identity" in cid_obj
        assert "/W [" in cid_obj
    finally:
        doc.close()


@needs_dejavu
def test_build_font_writes_tounicode_for_extraction():
    doc = _new_doc()
    try:
        embedded = build_font(doc, face_from_file(DEJAVU), "ñandú")
        kind, value = doc.xref_get_key(embedded.xref, "ToUnicode")
        assert kind == "xref"
        cmap = doc.xref_stream(int(value.split()[0])).decode("latin-1")
        assert "begincmap" in cmap and "beginbfchar" in cmap
        for ch in "ñandú":
            assert ch.encode("utf-16-be").hex().upper() in cmap
    finally:
        doc.close()


@needs_dejavu
def test_build_font_keeps_whole_program_when_subsetting_forbidden(tmp_path):
    face = face_from_file(_font_with_fs_type(tmp_path, 0x0100))
    assert face.subset_allowed is False
    doc = _new_doc()
    try:
        embedded = build_font(doc, face, "Hola")
        assert embedded.subsetted is False
        assert "+" not in embedded.basefont
        _desc, _ff, data = _font_stream(doc, embedded.xref)
        # Whole font, not a tiny subset.
        assert len(data) > 100_000
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_build_font_refuses_missing_glyph():
    doc = _new_doc()
    try:
        with pytest.raises(FontEmbedError, match="cannot draw"):
            build_font(doc, face_from_file(DEJAVU), "漢")
        # A failed build leaves no attached resource.
        assert "Fsub1" not in existing_font_names(doc, doc[0].xref)
    finally:
        doc.close()


def test_build_font_refuses_cff_outlines():
    path = _cff_font()
    if path is None:
        pytest.skip("no CFF/OpenType font installed")
    doc = _new_doc()
    try:
        with pytest.raises(FontEmbedError, match="CFF/PostScript"):
            build_font(doc, face_from_file(path), "Hola")
    finally:
        doc.close()


@needs_dejavu
def test_build_font_refuses_restricted_licence(tmp_path):
    face = face_from_file(_font_with_fs_type(tmp_path, 0x0002))
    doc = _new_doc()
    try:
        with pytest.raises(FontEmbedError, match="licence forbids embedding"):
            build_font(doc, face, "Hola")
    finally:
        doc.close()


@needs_dejavu
def test_build_font_requires_fonttools(monkeypatch):
    import pdfar.font_embed as fe

    monkeypatch.setattr(fe, "_TTFont", None)
    doc = _new_doc()
    try:
        with pytest.raises(FontEmbedError, match="fonttools"):
            build_font(doc, face_from_file(DEJAVU), "Hola")
    finally:
        doc.close()


@needs_dejavu
def test_build_font_refuses_empty_text():
    doc = _new_doc()
    try:
        with pytest.raises(FontEmbedError, match="no text"):
            build_font(doc, face_from_file(DEJAVU), "")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Resource attachment
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_attach_font_adds_unique_names():
    doc = _new_doc()
    try:
        face = face_from_file(DEJAVU)
        first = embed_font(doc, doc[0].xref, face, "Hola")
        second = embed_font(doc, doc[0].xref, face, "Adiós")
        assert first.resource_name == "Fsub1"
        assert second.resource_name == "Fsub2"
        names = existing_font_names(doc, doc[0].xref)
        assert {"Fsub1", "Fsub2"} <= names
        # Both resolve to the right font object.
        res = int(doc.xref_get_key(doc[0].xref, "Resources")[1].split()[0])
        kind, value = doc.xref_get_key(res, "Font/Fsub1")
        assert kind == "xref" and int(value.split()[0]) == first.xref
    finally:
        doc.close()


@needs_dejavu
def test_attach_font_copies_shared_resources():
    doc = _new_doc(pages=2)
    try:
        p1, p2 = doc[0], doc[1]
        shared = int(doc.xref_get_key(p1.xref, "Resources")[1].split()[0])
        doc.xref_set_key(p2.xref, "Resources", f"{shared} 0 R")
        assert doc.xref_get_key(p2.xref, "Resources")[1] == f"{shared} 0 R"

        embedded = embed_font(doc, p1.xref, face_from_file(DEJAVU), "Hola")

        # Page 1 got a private copy; page 2 still points at the old object.
        p1_res = int(doc.xref_get_key(p1.xref, "Resources")[1].split()[0])
        assert p1_res != shared
        p2_res = int(doc.xref_get_key(p2.xref, "Resources")[1].split()[0])
        assert p2_res == shared
        assert "Fsub1" in existing_font_names(doc, p1.xref)
        assert "Fsub1" not in existing_font_names(doc, p2.xref)
        assert embedded.resource_name == "Fsub1"
    finally:
        doc.close()


@needs_dejavu
def test_attach_font_handles_inline_font_dictionary():
    """A page whose /Resources/Font is a direct dictionary, not an xref."""
    doc = _new_doc()
    try:
        page = doc[0]
        res = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
        # Rewrite the resources object with an inline (direct) Font dict.
        doc.update_object(res, "<< /Font << >> >>")
        embedded = embed_font(doc, page.xref, face_from_file(DEJAVU), "Hola")
        assert embedded.resource_name == "Fsub1"
        assert "Fsub1" in existing_font_names(doc, page.xref)
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# End-to-end extraction and persistence
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_embedded_font_extracts_text_through_tounicode():
    doc = _new_doc()
    try:
        page = doc[0]
        embedded = embed_font(doc, page.xref, face_from_file(DEJAVU),
                              "Café ñandú")
        content = (b"BT /" + embedded.resource_name.encode() + b" 14 Tf"
                   b" 1 0 0 1 40 200 Tm <"
                   + embedded.encode("Café ñandú").hex().encode()
                   + b"> Tj ET")
        xref = page.get_contents()[0]
        doc.update_stream(xref, content)
        text = page.get_text()
        assert "Café ñandú" in text
        assert "\ufffd" not in text
    finally:
        doc.close()


@needs_dejavu
def test_embedded_font_survives_save_and_reopen(tmp_path):
    doc = _new_doc()
    try:
        page = doc[0]
        embedded = embed_font(doc, page.xref, face_from_file(DEJAVU), "ñandú")
        doc.update_stream(
            page.get_contents()[0],
            b"BT /" + embedded.resource_name.encode() + b" 14 Tf"
            b" 1 0 0 1 40 200 Tm <"
            + embedded.encode("ñandú").hex().encode() + b"> Tj ET")
        out = str(tmp_path / "embedded.pdf")
        doc.save(out)
    finally:
        doc.close()

    reopened = fitz.open(out)
    try:
        assert "ñandú" in reopened[0].get_text()
        fonts = reopened[0].get_fonts(full=True)
        assert any(f[1] == "ttf" for f in fonts)
        font_xref = next(f[0] for f in fonts if f[1] == "ttf")
        name, ext, _type, content = reopened.extract_font(font_xref)
        assert ext == "ttf"
        assert name
        # The program is inside the file: no system font needed to draw it.
        program = TTFont(io.BytesIO(content))
        try:
            cmap = program.getBestCmap()
            assert ord("ñ") in cmap and ord("ú") in cmap
        finally:
            program.close()
    finally:
        reopened.close()


@needs_dejavu
def test_embedded_font_renders_pixels():
    doc = _new_doc()
    try:
        page = doc[0]
        embedded = embed_font(doc, page.xref, face_from_file(DEJAVU), "ñandú")
        doc.update_stream(
            page.get_contents()[0],
            b"BT /" + embedded.resource_name.encode() + b" 24 Tf"
            b" 1 0 0 1 40 200 Tm <"
            + embedded.encode("ñandú").hex().encode() + b"> Tj ET")
        pix = page.get_pixmap(dpi=96)
        # Ink was drawn on the page.
        assert min(pix.samples) < 100
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def test_font_name_uses_subset_tag():
    face = FontFace(path="/x.ttf", family="DejaVu Sans")
    subset = _font_name(face, "Hola", subsetted=True)
    assert len(subset.split("+")[0]) == 6
    assert subset.split("+")[1] == "DejaVuSans"
    assert _font_name(face, "Hola", subsetted=False) == "DejaVuSans"


def test_tounicode_cmap_chunks_large_sets():
    gid_for = {chr(0x100 + i): i for i in range(150)}
    cmap = _tounicode_cmap(gid_for)
    assert cmap.count("beginbfchar") == 2  # 100 + 50, CMap max chunk
    assert "<0000> <0100>" in cmap


def test_encode_unknown_character_raises():
    font = EmbeddedFont(resource_name="F1", xref=1, basefont="X",
                        gid_for={"a": 3}, advances={3: 0.5},
                        ascender=800, descender=-200,
                        bbox=(0, -200, 1000, 800))
    assert font.encode("a") == b"\x00\x03"
    assert font.width("a") == 0.5
    with pytest.raises(FontEmbedError):
        font.encode("b")
    with pytest.raises(FontEmbedError):
        font.width("b")
