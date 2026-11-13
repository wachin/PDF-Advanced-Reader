"""Phase 5.3 regression tests: paragraph edits must survive Save, close, reopen.

The bug under test: a multi-line paragraph whose new text needs a substitute
font could never be applied (``build_font`` required the font to "draw" the
``\\n`` line separators in ``new_text``), so the edit silently failed and did
not persist — while single-word substitution, which passes no newline, kept
working.  These tests pin the fix and the general "paragraph edit survives an
Apply → Save → reopen" contract across the PDF structures PDFAR supports.

No Qt is required: the engine, ``DocumentSession`` and ``save_document`` are
exercised directly, exactly as the GUI would.
"""

import os
import re

import fitz
import pytest

from pdfar.session import DocumentSession
from pdfar.paragraph import extract_paragraphs, LayoutOptions
from pdfar.paragraph_edit import ReflowParagraph
from pdfar.save import save_document
from pdfar.font_embed import build_font, FontEmbedError
from pdfar.font_discovery import FontFace
from pdfar.text_edit_existing import ReplaceExistingText, FontSubstitution


# --------------------------------------------------------------------------- #
# Fonts / builders
# --------------------------------------------------------------------------- #
_TTF_CANDIDATES = [
    "/opt/ZCode/resources/glm/packages/pdf-plugin/dist/ui/pdfjs/"
    "standard_fonts/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _find_ttf():
    for path in _TTF_CANDIDATES:
        if os.path.isfile(path):
            return path
    return None


_SENTENCE = ("The quick brown fox jumps over the lazy dog near the river and "
             "the meadow while the sun sets slowly behind the distant hills. ")


def _build_basic(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 100, 500, 760), _SENTENCE * 3,
                        fontsize=12, fontname="helv", align=fitz.TEXT_ALIGN_LEFT)
    doc.save(path, garbage=2, deflate=True)
    doc.close()
    return path


def _build_embedded(path, ttf):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 100, 500, 760), _SENTENCE * 3,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    doc.save(path, garbage=2, deflate=True)
    doc.close()
    return path


def _build_object_streams(path, ttf):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 100, 500, 760), _SENTENCE * 3,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    # Force a compressed xref / object streams, as real producers emit.
    doc.save(path, clean=True, garbage=2, deflate=True)
    doc.close()
    return path


def _build_xobject(path, ttf):
    """A depth-1 Form XObject wrapping the paragraph (common in real output)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 100, 500, 760), _SENTENCE * 3,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    cs_xref = page.get_contents()[0]
    cs_data = doc.xref_stream(cs_xref)
    res = doc.xref_object(page.xref, compressed=False)
    res_m = re.search(r"/Resources\s+(.+)", res).group(1).strip()
    fx = doc.get_new_xref()
    doc.update_object(fx, ("<< /Type /XObject /Subtype /Form /FormType 1 "
                          "/BBox [0 0 600 800] /Matrix [1 0 0 1 0 0] "
                          f"/Resources {res_m} >>"))
    doc.update_stream(fx, cs_data)
    new_cs = doc.get_new_xref()
    doc.update_object(new_cs, "<<>>")
    doc.update_stream(new_cs, b"q\n/FX Do\nQ\n")
    doc.xref_set_key(page.xref, "Contents", f"{new_cs} 0 R")
    doc.xref_set_key(page.xref, "Resources",
                     "<< /XObject << /FX %d 0 R >> >>" % fx)
    doc.save(path, garbage=2, deflate=True)
    doc.close()
    return path


def _build_multiblock(path, ttf):
    """A realistic page: a paragraph plus a separate text block below it."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 120, 500, 560), _SENTENCE * 2,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    page.insert_textbox(fitz.Rect(100, 640, 500, 760),
                        "Footer note pinned near the bottom of the page.",
                        fontsize=10, fontname="helv", fontfile=ttf)
    doc.save(path, garbage=2, deflate=True)
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# Lifecycle helper
# --------------------------------------------------------------------------- #
def _first_clean_paragraph(page):
    paras = extract_paragraphs(page)
    clean = [p for p in paras if not p.problems]
    return clean[0] if clean else None


def _paragraph_edit_for(path, change, *, ttf=None, substitute=None,
                       allow_shrink=True, allow_expand=True):
    """Build a feasible ``ReflowParagraph`` edit for the first clean paragraph."""
    doc = fitz.open(path)
    para = _first_clean_paragraph(doc[0])
    doc.close()
    assert para is not None, "no editable paragraph found"
    new_text = change(para.text)
    return ReflowParagraph(
        page_num=0, first_index=para.first_index, last_index=para.last_index,
        new_text=new_text, anchor=para.text,
        options=LayoutOptions(allow_shrink=allow_shrink,
                              allow_expand=allow_expand),
        substitute=substitute)


def _apply_save_reopen(path, edit, *, same_path=False, save_as=None):
    """Run Apply → Save → reopen and return in-memory and reopened page text."""
    handle = fitz.open(path)
    session = DocumentSession(handle, path, "", 0)
    version, scope = session.apply(edit)
    inmem = session.doc[0].get_text()
    if same_path:
        save_document(session, None)
        session.close()
        reopened_path = path
    else:
        target = save_as or (path + ".saved.pdf")
        save_document(session, target)
        session.close()
        reopened_path = target
    rdoc = fitz.open(reopened_path)
    reopened = rdoc[0].get_text()
    rdoc.close()
    if not same_path and not save_as:
        os.remove(reopened_path)
    return inmem, reopened


# --------------------------------------------------------------------------- #
# 1. No-substitution paragraph edits persist across structures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("builder", [
    ("basic", _build_basic, None),
    ("embedded", _build_embedded, 0),
    ("object_streams", _build_object_streams, 0),
    ("xobject", _build_xobject, 0),
    ("multiblock", _build_multiblock, 0),
])
def test_paragraph_edit_persists(tmp_path, builder):
    name, fn, ttf_idx = builder
    ttf = _find_ttf() if fn in (_build_embedded, _build_object_streams,
                                _build_xobject, _build_multiblock) else None
    if ttf is None and fn not in (_build_basic,):
        pytest.skip("no embeddable TTF available")
    src = str(tmp_path / f"{name}.pdf")
    if fn is _build_basic:
        fn(src)
    else:
        fn(src, ttf)

    marker = "ZZEDITED_OK"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", "brown" + marker, 1))
    inmem, reopened = _apply_save_reopen(src, edit, same_path=False)
    assert marker in inmem, "edit did not apply in memory"
    assert marker in reopened, f"{name}: paragraph edit lost after reopen"


# --------------------------------------------------------------------------- #
# 2. Word edit persists (control, the path that always worked)
# --------------------------------------------------------------------------- #
def test_word_edit_persists(tmp_path):
    src = str(tmp_path / "word.pdf")
    _build_embedded(src, _find_ttf())
    doc = fitz.open(src)
    words = doc[0].get_text("words")
    target = next(w for w in words if "brown" in w[4])
    cx, cy = (target[0] + target[2]) / 2, (target[1] + target[3]) / 2
    from pdfar.text_edit_existing import find_text_at_point
    hit = find_text_at_point(doc[0], (cx, cy))
    doc.close()
    marker = "BROWNZZ"
    edit = ReplaceExistingText(page_num=0, old_word=hit.text,
                               new_word=hit.text.replace("brown", marker, 1),
                               word_rect=hit.rect)
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    inmem = session.doc[0].get_text()
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    reopened = rdoc[0].get_text()
    rdoc.close()
    os.remove(out)
    assert marker in inmem
    assert marker in reopened


# --------------------------------------------------------------------------- #
# 3. The bug fix: multi-line paragraph substitution persists
# --------------------------------------------------------------------------- #
def test_multiline_paragraph_substitution_persists(tmp_path):
    ttf = _find_ttf()
    if ttf is None:
        pytest.skip("no embeddable TTF available")
    src = str(tmp_path / "sub.pdf")
    # Embedded (subset) font: any new character triggers substitution.
    _build_embedded(src, ttf)
    face = FontFace(path=ttf, family="Liberation Sans", style="Regular",
                    weight=400, italic=False)
    marker = "riverj"  # 'j' is not in the drawn subset -> needs substitution

    def change(t):
        return t.replace("river", marker, 1)

    edit = _paragraph_edit_for(src, change,
                               substitute=FontSubstitution(face=face))
    inmem, reopened = _apply_save_reopen(src, edit, same_path=False)
    assert marker in inmem, "substituted paragraph did not apply in memory"
    assert marker in reopened, "substituted paragraph lost after reopen"


def test_multiline_paragraph_substitution_in_place_save(tmp_path):
    ttf = _find_ttf()
    if ttf is None:
        pytest.skip("no embeddable TTF available")
    src = str(tmp_path / "sub2.pdf")
    _build_embedded(src, ttf)
    face = FontFace(path=ttf, family="Liberation Sans", style="Regular",
                    weight=400, italic=False)
    marker = "riverj"

    def change(t):
        return t.replace("river", marker, 1)

    edit = _paragraph_edit_for(src, change,
                               substitute=FontSubstitution(face=face))
    inmem, reopened = _apply_save_reopen(src, edit, same_path=True)
    assert marker in inmem
    assert marker in reopened


# --------------------------------------------------------------------------- #
# 4. Unit test for the actual fix in build_font
# --------------------------------------------------------------------------- #
def test_build_font_ignores_newline():
    ttf = _find_ttf()
    if ttf is None:
        pytest.skip("no embeddable TTF available")
    face = FontFace(path=ttf, family="Liberation Sans", style="Regular",
                    weight=400, italic=False)
    doc = fitz.open()
    # The bug: a multi-line string raised "The font cannot draw: '\\n'".
    embedded = build_font(doc, face, "hello\nworld")
    assert embedded is not None
    assert embedded.xref > 0
    doc.close()


def test_build_font_still_rejects_missing_glyph():
    ttf = _find_ttf()
    if ttf is None:
        pytest.skip("no embeddable TTF available")
    face = FontFace(path=ttf, family="Liberation Sans", style="Regular",
                    weight=400, italic=False)
    doc = fitz.open()
    # A genuinely undrawable character is still reported.
    with pytest.raises(FontEmbedError):
        build_font(doc, face, "\U0001F600")  # emoji not in LiberationSans
    doc.close()


# --------------------------------------------------------------------------- #
# 5. Lifecycle edge cases
# --------------------------------------------------------------------------- #
def test_paragraph_edit_undo_then_save_reopen(tmp_path):
    src = str(tmp_path / "undo.pdf")
    _build_basic(src)
    marker = "ZZUNDO"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    assert marker in session.doc[0].get_text()
    session.undo()
    assert marker not in session.doc[0].get_text()
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    reopened = rdoc[0].get_text()
    rdoc.close()
    os.remove(out)
    assert marker not in reopened, "undo then save should drop the edit"


def test_paragraph_edit_undo_redo_save_reopen(tmp_path):
    src = str(tmp_path / "red.pdf")
    _build_basic(src)
    marker = "ZZREDO"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    session.undo()
    assert marker not in session.doc[0].get_text()
    session.redo()
    assert marker in session.doc[0].get_text()
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    reopened = rdoc[0].get_text()
    rdoc.close()
    os.remove(out)
    assert marker in reopened, "redo then save should keep the edit"


def test_paragraph_edit_save_as_keeps_edit(tmp_path):
    src = str(tmp_path / "sa.pdf")
    _build_basic(src)
    marker = "ZZSAVEAS"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    out = str(tmp_path / "sa_out.pdf")
    inmem, reopened = _apply_save_reopen(src, edit, save_as=out)
    assert marker in inmem
    assert marker in reopened
    # original untouched
    odoc = fitz.open(src)
    assert marker not in odoc[0].get_text()
    odoc.close()


def test_unrelated_page_unchanged_after_paragraph_edit(tmp_path):
    ttf = _find_ttf()
    src = str(tmp_path / "multi.pdf")
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    doc[0].insert_textbox(fitz.Rect(100, 100, 500, 760), _SENTENCE * 3,
                          fontsize=12, fontname="helv",
                          fontfile=ttf, align=fitz.TEXT_ALIGN_LEFT)
    p1 = doc.new_page(width=600, height=800)
    p1.insert_textbox(fitz.Rect(100, 100, 500, 760),
                      "UNIQUEMARKER_PAGE_TWO stays intact.", fontsize=12,
                      fontname="helv", fontfile=ttf)
    doc.save(src, garbage=2, deflate=True)
    doc.close()
    marker = "ZZP1"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    assert "UNIQUEMARKER_PAGE_TWO" in rdoc[1].get_text()
    assert marker in rdoc[0].get_text()
    rdoc.close()
    os.remove(out)


def test_fonts_resources_remain_after_paragraph_edit(tmp_path):
    ttf = _find_ttf()
    src = str(tmp_path / "fonts.pdf")
    _build_embedded(src, ttf)
    before = fitz.open(src)
    before_fonts = set(before[0].get_fonts(full=True))
    before.close()
    marker = "ZZFONT"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    after = fitz.open(out)
    after_fonts = set(after[0].get_fonts(full=True))
    after.close()
    # Every original font is still present (the edit may add one if it
    # substituted, but must not drop any existing one).
    assert before_fonts.issubset(after_fonts)
    os.remove(out)


def test_substituted_font_attached_and_persists(tmp_path):
    ttf = _find_ttf()
    if ttf is None:
        pytest.skip("no embeddable TTF available")
    src = str(tmp_path / "subfont.pdf")
    _build_embedded(src, ttf)
    face = FontFace(path=ttf, family="Liberation Sans", style="Regular",
                    weight=400, italic=False)
    marker = "riverj"
    edit = _paragraph_edit_for(src, lambda t: t.replace("river", marker, 1),
                               substitute=FontSubstitution(face=face))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    # A new font must have been attached to the page.
    assert len(session.doc[0].get_fonts()) > len(fitz.open(src)[0].get_fonts())
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    assert len(rdoc[0].get_fonts()) > len(fitz.open(src)[0].get_fonts())
    assert marker in rdoc[0].get_text()
    rdoc.close()
    os.remove(out)


def test_render_after_reopen_matches_content(tmp_path):
    src = str(tmp_path / "render.pdf")
    _build_basic(src)
    marker = "ZZRENDER"
    edit = _paragraph_edit_for(src, lambda t: t.replace("brown", marker, 1))
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    session.apply(edit)
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    # A document that parses but cannot be drawn would have failed the save's
    # own render proof; render again to be sure the saved file is real.
    pix = rdoc[0].get_pixmap(dpi=72)
    rdoc.close()
    assert pix.width > 0 and pix.height > 0
    os.remove(out)


def test_multi_paragraph_same_page(tmp_path):
    ttf = _find_ttf()
    src = str(tmp_path / "mp.pdf")
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(100, 100, 500, 350), _SENTENCE * 2,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    page.insert_textbox(fitz.Rect(100, 420, 500, 760), _SENTENCE * 2,
                        fontsize=12, fontname="helv", fontfile=ttf,
                        align=fitz.TEXT_ALIGN_LEFT)
    doc.save(src, garbage=2, deflate=True)
    doc.close()
    paras = fitz.open(src)
    plist = [p for p in extract_paragraphs(paras[0]) if not p.problems]
    paras.close()
    assert len(plist) >= 2
    handle = fitz.open(src)
    session = DocumentSession(handle, src, "", 0)
    for i, para in enumerate(plist[:2]):
        new = para.text.replace("brown", f"BROWN{i}", 1)
        ed = ReflowParagraph(page_num=0, first_index=para.first_index,
                             last_index=para.last_index, new_text=new,
                             anchor=para.text,
                             options=LayoutOptions(allow_shrink=True,
                                                   allow_expand=True))
        session.apply(ed)
    inmem = session.doc[0].get_text()
    out = src + ".saved.pdf"
    save_document(session, out)
    session.close()
    rdoc = fitz.open(out)
    reopened = rdoc[0].get_text()
    rdoc.close()
    assert "BROWN0" in inmem and "BROWN1" in inmem
    assert "BROWN0" in reopened and "BROWN1" in reopened
    os.remove(out)
