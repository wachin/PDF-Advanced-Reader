"""Phase 4: intelligent font substitution for existing-text edits.

A document whose font cannot draw the replacement must not be corrupted or
silently reflowed.  These tests cover the whole path: diagnosis, ranked
alternatives, explicit user approval, embedding, width correction, save/reopen,
text extraction and visual locality — plus every refusal.
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
from fontTools import subset
from fontTools.ttLib import TTFont

from pdfar.edits import EditError
from pdfar.font_discovery import face_from_file
from pdfar.session import DocumentSession
from pdfar.text_edit_existing import (
    FontSubstitution,
    FontSubstitutionRequired,
    ReplaceExistingText,
    diagnose_replacement,
    find_font_alternatives,
    find_text_at_point,
)


# --------------------------------------------------------------------------- #
# Fonts
# --------------------------------------------------------------------------- #
def _find(*names):
    for name in names:
        hits = glob.glob(f"/usr/share/fonts/**/{name}", recursive=True)
        if hits:
            return hits[0]
    return None


DEJAVU = _find("DejaVuSans.ttf")
DEJAVU_BOLD = _find("DejaVuSans-Bold.ttf")
DEJAVU_OBLIQUE = _find("DejaVuSans-Oblique.ttf")

needs_dejavu = pytest.mark.skipif(DEJAVU is None,
                                  reason="DejaVuSans.ttf is not installed")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _subset_font_bytes(data: bytes, text: str):
    """Subset *data* to *text*, keeping glyph ids stable (retain_gids)."""
    font = TTFont(io.BytesIO(data))
    options = subset.Options()
    options.retain_gids = True
    options.notdef_outline = True
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.name_languages = ["*"]
    options.drop_tables += ["FFTM", "DSIG"]
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(unicodes=[ord(ch) for ch in dict.fromkeys(text)])
    subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    return buf.getvalue()


def _tounicode(gid_for) -> bytes:
    pairs = sorted((gid, ch) for ch, gid in gid_for.items())
    lines = [
        "/CIDInit /ProcSet findresource begin", "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS)"
        " /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def", "/CMapType 2 def",
        "1 begincodespacerange", "<0000> <FFFF>", "endcodespacerange",
        f"{len(pairs)} beginbfchar",
    ]
    lines += [f"<{gid:04X}> <{ch.encode('utf-16-be').hex().upper()}>"
              for gid, ch in pairs]
    lines += ["endbfchar", "endcmap",
              "CMapName currentdict /CMap defineresource pop", "end", "end"]
    return ("\n".join(lines) + "\n").encode("latin-1")


def _restricted_pdf(tmp_path, text="Hola mundo", fontfile=DEJAVU,
                    name="restricted.pdf", subset_tag=False):
    """A PDF whose embedded Type0 font only knows *text*.

    Built with PyMuPDF, then the font program and ToUnicode map are rewritten
    so that characters outside *text* are genuinely unavailable — which is what
    a real subsetted PDF looks like.
    """
    path = str(tmp_path / name)
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.draw_rect(fitz.Rect(300, 20, 360, 60), color=(0.8, 0.2, 0.2),
                   fill=(0.8, 0.2, 0.2))
    page.insert_text((50, 150), text, fontsize=14, fontname="F0",
                     fontfile=fontfile)
    font_xref = page.get_fonts()[0][0]
    cid = int(doc.xref_get_key(font_xref, "DescendantFonts")[1]
              .strip("[] ").split()[0])
    desc = int(doc.xref_get_key(cid, "FontDescriptor")[1].split()[0])
    ff = int(doc.xref_get_key(desc, "FontFile2")[1].split()[0])
    tu = int(doc.xref_get_key(font_xref, "ToUnicode")[1].split()[0])

    data = _subset_font_bytes(doc.xref_stream(ff), text)
    doc.update_stream(ff, data)
    doc.xref_set_key(ff, "Length1", str(len(data)))

    final = TTFont(io.BytesIO(data))
    try:
        cmap = final.getBestCmap()
        gid_for = {ch: final.getGlyphID(cmap[ord(ch)]) for ch in text}
    finally:
        final.close()
    doc.update_stream(tu, _tounicode(gid_for))

    if subset_tag:
        for owner in (font_xref, desc):
            key = "BaseFont" if owner == font_xref else "FontName"
            kind, value = doc.xref_get_key(owner, key)
            if kind == "name":
                doc.xref_set_key(owner, key, "/ABCDEF+DejaVuSans")
    doc.save(path)
    doc.close()
    return path, gid_for


def _tj_pdf(tmp_path, name="tj.pdf"):
    """A simple-font page drawn with a TJ array (non-embedded Helvetica)."""
    path = str(tmp_path / name)
    doc = fitz.open()
    page = doc.new_page(width=500, height=250)
    page.insert_text((10, 20), ".")
    page.insert_font(fontname="helv")
    stream = (b"q\nBT\n/helv 14 Tf\n1 0 0 1 60 140 Tm\n"
              b"[(Hello) -300 ( mundo tail)] TJ\nET\nQ\n")
    doc.update_stream(page.get_contents()[0], stream)
    doc.save(path)
    doc.close()
    return path


def _xobject_pdf(tmp_path, text="Hola mundo"):
    """The restricted font used from inside a Form XObject."""
    path, gid_for = _restricted_pdf(tmp_path, text=text, name="xo-src.pdf")
    doc = fitz.open(path)
    page = doc[0]
    font_xref = page.get_fonts()[0][0]
    res = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
    xref = doc.get_new_xref()
    doc.update_object(
        xref,
        "<</Type/XObject/Subtype/Form/BBox[0 0 400 200]"
        f"/Resources<</Font<</F0 {font_xref} 0 R>>>>>>")
    hexstr = "".join(f"{gid_for[ch]:04X}" for ch in text)
    doc.update_stream(
        xref, f"BT /F0 14 Tf 1 0 0 1 50 100 Tm <{hexstr}> Tj ET".encode())
    doc.xref_set_key(res, "XObject/Fm1", f"{xref} 0 R")
    # Replace every page stream (the drawing and the original text) so the
    # only visible text is the one drawn by the XObject.
    streams = page.get_contents()
    for stream in streams:
        doc.update_stream(stream, b"")
    doc.update_stream(streams[-1], b"q 1 0 0 1 50 50 cm /Fm1 Do Q")
    out = str(tmp_path / "xobject.pdf")
    doc.save(out)
    doc.close()
    return out


def _word_rect(page, text):
    w = next(w for w in page.get_text("words") if w[4] == text)
    return (w[0], w[1], w[2], w[3])


def _hit(page, text):
    x0, y0, x1, y1 = _word_rect(page, text)
    hit = find_text_at_point(page, ((x0 + x1) / 2, (y0 + y1) / 2))
    assert hit is not None and hit.text == text
    return hit


def _edit(page, old, new, substitute=None):
    hit = _hit(page, old)
    edit = ReplaceExistingText(
        page_num=page.number, old_word=old, new_word=new, word_rect=hit.rect,
        substitute=substitute)
    edit.apply(page.parent)
    return edit


def _best_face(doc, page, old, new, hit=None):
    hit = hit or _hit(page, old)
    matches = find_font_alternatives(doc, page, old, new, hit.rect)
    assert matches
    return matches[0]


def _embedded_programs(doc):
    """All FontFile2 streams in the document."""
    out = []
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Type")[1] == "/FontDescriptor":
            kind, value = doc.xref_get_key(xref, "FontFile2")
            if kind == "xref":
                out.append(doc.xref_stream(int(value.split()[0])))
    return out


# --------------------------------------------------------------------------- #
# Diagnosis
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_diagnose_returns_none_when_font_covers_text(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        assert diagnose_replacement(doc, page, "mundo", "mund", hit.rect) is None
    finally:
        doc.close()


@needs_dejavu
def test_diagnose_reports_missing_characters_and_font(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        issue = diagnose_replacement(doc, page, "mundo", "mundiño", hit.rect)
        assert issue is not None
        assert issue.missing == ("i", "ñ")
        assert issue.font_name == "DejaVu Sans Book"
        assert issue.font_type == "Type0"
        assert issue.embedded is True
        assert "no glyph" in issue.reason
        assert issue.text == "mundiño"
    finally:
        doc.close()


@needs_dejavu
def test_diagnose_flags_subsetted_base_font(tmp_path):
    path, _gid = _restricted_pdf(tmp_path, subset_tag=True)
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        issue = diagnose_replacement(doc, page, "mundo", "mundiño", hit.rect)
        assert issue is not None and issue.subsetted is True
        assert issue.font_name.startswith("ABCDEF+")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Refusal without approval
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_edit_raises_substitution_required_and_leaves_document_untouched(
        tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        before = page.get_text()
        fonts_before = len(page.get_fonts())
        hit = _hit(page, "mundo")
        edit = ReplaceExistingText(
            page_num=0, old_word="mundo", new_word="mundiño",
            word_rect=hit.rect)
        with pytest.raises(FontSubstitutionRequired) as excinfo:
            edit.apply(doc)
        assert excinfo.value.issue.missing == ("i", "ñ")
        assert page.get_text() == before
        assert len(page.get_fonts()) == fonts_before
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Alternatives
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_find_alternatives_ranks_usable_fonts(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        matches = find_font_alternatives(doc, page, "mundo", "mundiño",
                                         hit.rect, limit=6)
        assert matches
        assert all(m.usable for m in matches)
        assert all(not m.missing for m in matches)
        assert matches[0].face.family.lower().startswith("dejavu sans")
        # ñ is wider than o, so the replacement is wider than the original.
        assert matches[0].width_ratio > 1.0
        scores = [m.score for m in matches]
        assert scores == sorted(scores, reverse=True)
    finally:
        doc.close()


@needs_dejavu
@pytest.mark.skipif(DEJAVU_BOLD is None, reason="DejaVu Sans Bold missing")
def test_bold_original_gets_bold_alternative(tmp_path):
    path, _gid = _restricted_pdf(tmp_path, fontfile=DEJAVU_BOLD,
                                 name="bold.pdf")
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        matches = find_font_alternatives(doc, page, "mundo", "mundiño",
                                         hit.rect, limit=4)
        assert matches
        assert matches[0].face.bold is True
    finally:
        doc.close()


@needs_dejavu
@pytest.mark.skipif(DEJAVU_OBLIQUE is None,
                    reason="DejaVu Sans Oblique missing")
def test_italic_original_gets_italic_alternative(tmp_path):
    path, _gid = _restricted_pdf(tmp_path, fontfile=DEJAVU_OBLIQUE,
                                 name="italic.pdf")
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "mundo")
        matches = find_font_alternatives(doc, page, "mundo", "mundiño",
                                         hit.rect, limit=4)
        assert matches
        assert matches[0].face.italic is True
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Substitution and persistence
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_substitution_embeds_font_and_updates_text(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))

        assert "Hola mundiño" in page.get_text()
        fonts = page.get_fonts(full=True)
        assert len(fonts) == 2
        new_font = next(f for f in fonts if f[3] != "DejaVu Sans Book")
        assert new_font[1] == "ttf"
        # Subset tag + sanitized family name.
        assert "+" in new_font[3]
        assert "DejaVuSans" in new_font[3]
    finally:
        doc.close()


@needs_dejavu
def test_substitution_survives_save_and_reopen(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))
        out = str(tmp_path / "substituted.pdf")
        doc.save(out)
    finally:
        doc.close()

    reopened = fitz.open(out)
    try:
        text = reopened[0].get_text()
        assert "Hola mundiño" in text
        assert "\ufffd" not in text
        fonts = reopened[0].get_fonts(full=True)
        assert any(f[1] == "ttf" for f in fonts)
    finally:
        reopened.close()


@needs_dejavu
def test_substituted_glyphs_are_inside_the_pdf(tmp_path):
    """The output must draw correctly where the system font is absent."""
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))
        out = str(tmp_path / "portable.pdf")
        doc.save(out)
    finally:
        doc.close()

    reopened = fitz.open(out)
    try:
        fonts = reopened[0].get_fonts(full=True)
        # The substituted font is the one with a subset tag.
        xref = next(f[0] for f in fonts if "+" in f[3])
        content = reopened.extract_font(xref)[3]
        program = TTFont(io.BytesIO(content))
        try:
            cmap = program.getBestCmap()
            assert ord("ñ") in cmap
            assert ord("i") in cmap
        finally:
            program.close()
        # And it renders.
        pix = reopened[0].get_pixmap(dpi=96)
        assert min(pix.samples) < 100
    finally:
        reopened.close()


@needs_dejavu
def test_substitution_keeps_following_text_in_place(tmp_path):
    path, _gid = _restricted_pdf(tmp_path, text="Hola mundo tail")
    doc = fitz.open(path)
    try:
        page = doc[0]
        tail_before = _word_rect(page, "tail")[0]
        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))
        text = page.get_text()
        assert "Hola mundiño" in text
        assert "tail" in text
        tail_after = _word_rect(page, "tail")[0]
        assert abs(tail_after - tail_before) < 1.0
    finally:
        doc.close()


@needs_dejavu
def test_substitution_changes_only_the_target_region(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        target = fitz.Rect(*_word_rect(page, "mundo"))
        target = fitz.Rect(target.x0 - 6, target.y0 - 6,
                           target.x1 + 10, target.y1 + 6)
        blank = fitz.Rect(10, 230, 200, 290)
        before_target = page.get_pixmap(clip=target, dpi=144).samples
        before_blank = page.get_pixmap(clip=blank, dpi=144).samples

        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))

        after_target = page.get_pixmap(clip=target, dpi=144).samples
        after_blank = page.get_pixmap(clip=blank, dpi=144).samples
        changed = sum(1 for a, b in zip(before_target, after_target) if a != b)
        assert changed > 0
        assert after_blank == before_blank
    finally:
        doc.close()


@needs_dejavu
def test_substitution_preserves_unrelated_drawing_operators(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        streams = page.get_contents()
        assert isinstance(streams, list) and len(streams) == 2
        drawing_before = doc.xref_stream(streams[0])
        text_before = doc.xref_stream(streams[1])

        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))

        assert doc.xref_stream(streams[0]) == drawing_before
        assert doc.xref_stream(streams[1]) != text_before
        assert b"300 240 60 40 re" in doc.xref_stream(streams[0])
    finally:
        doc.close()


@needs_dejavu
@pytest.mark.parametrize("replacement", [
    "año",
    "ñandú",
    "áéíóú ü ñ",
    "ÁÉÍÓÚ Ü Ñ",
    "Łódź Škoda čć",
])
def test_spanish_and_latin_extended_substitutions(tmp_path, replacement):
    path, _gid = _restricted_pdf(tmp_path, name="es.pdf")
    doc = fitz.open(path)
    try:
        page = doc[0]
        face = _best_face(doc, page, "mundo", replacement).face
        _edit(page, "mundo", replacement, FontSubstitution(face=face))
        text = page.get_text()
        assert replacement in text
        assert "\ufffd" not in text
    finally:
        doc.close()


@needs_dejavu
def test_substitution_through_tj_array(tmp_path):
    path = _tj_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        hit = _hit(page, "Hello")
        issue = diagnose_replacement(doc, page, "Hello", "Ωmega", hit.rect)
        assert issue is not None
        assert issue.embedded is False
        assert issue.missing == ("Ω",)
        assert ("not available" in issue.reason
                or "cannot be encoded" in issue.reason)
        face = _best_face(doc, page, "Hello", "Ωmega", hit).face
        _edit(page, "Hello", "Ωmega", FontSubstitution(face=face))
        text = page.get_text()
        assert "Ωmega" in text
        assert "mundo tail" in text
        assert len(page.get_fonts()) == 2
    finally:
        doc.close()


@needs_dejavu
def test_substitution_inside_form_xobject(tmp_path):
    path = _xobject_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        assert "Hola mundo" in page.get_text()
        face = _best_face(doc, page, "mundo", "mundiño").face
        _edit(page, "mundo", "mundiño", FontSubstitution(face=face))
        assert "Hola mundiño" in page.get_text()

        # The font was added to the XObject's own resources, not the page's.
        page_res = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
        kind, value = doc.xref_get_key(page_res, "XObject/Fm1")
        xobj = int(value.split()[0])
        kind, value = doc.xref_get_key(xobj, "Resources/Font")
        assert "Fsub1" in (value or "")
        kind, value = doc.xref_get_key(page_res, "Font")
        assert "Fsub1" not in (value or "")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Refusals with an approved font
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_substitution_refuses_restricted_licence(tmp_path):
    source = str(tmp_path / "restricted-licence.ttf")
    font = TTFont(DEJAVU)
    try:
        font["OS/2"].fsType = 0x0002
        font.save(source)
    finally:
        font.close()
    face = face_from_file(source)
    assert face.embeddable is False

    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        with pytest.raises(EditError, match="cannot be embedded"):
            _edit(page, "mundo", "mundiño", FontSubstitution(face=face))
        assert "Hola mundo" in page.get_text()
    finally:
        doc.close()


@needs_dejavu
def test_substitution_refuses_face_without_glyphs(tmp_path):
    face = face_from_file(DEJAVU)
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        with pytest.raises(EditError, match="cannot draw"):
            _edit(page, "mundo", "漢", FontSubstitution(face=face))
        assert "Hola mundo" in page.get_text()
    finally:
        doc.close()


@needs_dejavu
def test_substitution_refuses_unsupported_show_operator(tmp_path):
    path = str(tmp_path / "quote.pdf")
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((10, 20), ".")
    page.insert_font(fontname="helv")
    doc.update_stream(page.get_contents()[0],
                      b"BT /helv 14 Tf 1 0 0 1 60 150 Tm (Hello) ' ET")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        page = doc[0]
        face = face_from_file(DEJAVU)
        with pytest.raises(EditError, match="cannot be split"):
            _edit(page, "Hello", "Ωmega", FontSubstitution(face=face))
        assert "Hello" in page.get_text()
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Session integration (undo / redo / save)
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_session_undo_redo_with_substitution(tmp_path):
    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    session = DocumentSession(doc, path, "", 0)
    try:
        page = session.doc[0]
        face = _best_face(session.doc, page, "mundo", "mundiño").face
        hit = _hit(page, "mundo")
        edit = ReplaceExistingText(
            page_num=0, old_word="mundo", new_word="mundiño",
            word_rect=hit.rect, substitute=FontSubstitution(face=face))
        session.apply(edit)
        assert "Hola mundiño" in session.doc[0].get_text()
        assert len(session.doc[0].get_fonts()) == 2

        session.undo()
        assert "Hola mundo" in session.doc[0].get_text()
        assert len(session.doc[0].get_fonts()) == 1

        session.redo()
        assert "Hola mundiño" in session.doc[0].get_text()
        assert len(session.doc[0].get_fonts()) == 2
    finally:
        session.close()


@needs_dejavu
def test_substitution_is_a_content_scope_edit(tmp_path):
    from pdfar.edits import Scope

    path, _gid = _restricted_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        page = doc[0]
        face = _best_face(doc, page, "mundo", "mundiño").face
        hit = _hit(page, "mundo")
        edit = ReplaceExistingText(
            page_num=0, old_word="mundo", new_word="mundiño",
            word_rect=hit.rect, substitute=FontSubstitution(face=face))
        assert edit.scope == Scope.CONTENT
        assert edit.pages is None
        assert edit.label.startswith("Edit text:")
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Viewer integration: the dialog is offered, then the edit is retried
# --------------------------------------------------------------------------- #
@needs_dejavu
@pytest.mark.parametrize("accept", [True, False])
def test_viewer_offers_substitution_dialog(tmp_path, monkeypatch, qapp,
                                           accept):
    from PyQt6.QtWidgets import QDialog

    from pdfar import viewer as viewer_mod
    from pdfar.viewer import PDFView

    path, _gid = _restricted_pdf(tmp_path)
    seen = {}

    class StubDialog:
        def __init__(self, parent, issue, text, matches):
            seen["issue"] = issue
            seen["text"] = text
            seen["matches"] = matches
            self._face = matches[0].face if matches else None

        def exec(self):
            return (QDialog.DialogCode.Accepted if accept
                    else QDialog.DialogCode.Rejected)

        def chosen_face(self):
            return self._face

        def deleteLater(self):
            pass

    monkeypatch.setattr(viewer_mod, "FontSubstitutionDialog", StubDialog)
    view = PDFView(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = _hit(page, "mundo")
        view._open_text_editor(0, hit)
        view._commit_text_edit("mundiño")

        assert seen["text"] == "mundiño"
        assert seen["issue"].missing == ("i", "ñ")
        assert seen["matches"]
        if accept:
            assert "Hola mundiño" in view.doc[0].get_text()
        else:
            assert "Hola mundo" in view.doc[0].get_text()
            assert len(view.doc[0].get_fonts()) == 1
    finally:
        view.shutdown()
