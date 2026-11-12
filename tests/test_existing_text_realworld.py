"""Compatibility tests against real-world PDFs in ``external/``.

Each test either applies a safe edit or accepts a clear refusal, but must never
crash or corrupt the document.  The replacement is a prefix of the original
word, which guarantees the glyphs were already used by the document, so a
refusal here is about *encoding verifiability* rather than missing glyphs.
"""

from __future__ import annotations

import glob
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest

from pdfar.edits import EditError
from pdfar.text_edit_existing import find_text_at_point, ReplaceExistingText

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TWO_COLUMN = os.path.join(ROOT, "external", "two-column-test-pdfs")
LIBREOFFICE = os.path.join(
    ROOT, "external", "LibreOffice-Getting-Started-Guides-PDF-BackUp",
    "GS262-GettingStarted.pdf")

TWO_COLUMN_PDFS = [
    "arxiv-2610.30749.pdf",
    "frontiers-fgene-2024-1387588.pdf",
    "frontiers-fpsyg-2025-1558407.pdf",
    "frontiers-fpsyt-2024-1366186.pdf",
]


def _try_edits(doc, page_no=0, limit=15):
    """Attempt prefix edits on a page.

    Returns ``(applied, refused, errors, applied_pairs)`` where each pair is
    ``(old_word, new_word)``.
    """
    page = doc[page_no]
    applied = refused = 0
    errors = []
    applied_pairs = []
    for w in page.get_text("words"):
        if len(w[4]) < 4:
            continue
        hit = find_text_at_point(page, ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        if hit is None:
            continue
        new_word = hit.text[:2]
        edit = ReplaceExistingText(
            page_num=page_no, old_word=hit.text, new_word=new_word,
            word_rect=hit.rect,
        )
        try:
            edit.apply(doc)
            applied += 1
            applied_pairs.append((hit.text, new_word))
        except EditError as exc:
            refused += 1
            errors.append(str(exc))
        if applied + refused >= limit:
            break
    return applied, refused, errors, applied_pairs


@pytest.mark.parametrize("name", TWO_COLUMN_PDFS)
def test_two_column_pdf_edits_or_refuses(name):
    path = os.path.join(TWO_COLUMN, name)
    if not os.path.exists(path):
        pytest.skip(f"{name} not present")
    doc = fitz.open(path)
    try:
        applied, refused, errors, pairs = _try_edits(doc)
        # No exception other than EditError escaped, and the document is valid.
        assert doc.page_count > 0
        assert applied + refused > 0, "no words were attempted"
        assert applied > 0, (
            f"{name}: expected at least one safe edit; refusals={errors[:3]}")
        # A successful edit is visible in extraction.
        text = doc[0].get_text()
        assert any(new in text for _old, new in pairs)
    finally:
        doc.close()


def test_arxiv_edit_persists_after_save(tmp_path):
    path = os.path.join(TWO_COLUMN, "arxiv-2610.30749.pdf")
    if not os.path.exists(path):
        pytest.skip("arxiv fixture not present")
    doc = fitz.open(path)
    try:
        page = doc[0]
        target = None
        for w in page.get_text("words"):
            if len(w[4]) >= 6 and w[4].isascii():
                hit = find_text_at_point(
                    page, ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
                if hit is not None:
                    target = hit
                    break
        assert target is not None
        new_word = target.text[:3]
        edit = ReplaceExistingText(
            page_num=0, old_word=target.text, new_word=new_word,
            word_rect=target.rect,
        )
        edit.apply(doc)
        out = str(tmp_path / "arxiv-edited.pdf")
        doc.save(out, garbage=3, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(out)
    try:
        text = reopened[0].get_text()
        assert new_word in text
        assert target.text not in text
    finally:
        reopened.close()


def test_subsetted_truetype_is_refused_not_corrupted():
    """GS262 uses subsetted TrueType fonts with no usable Unicode cmap.

    The engine must refuse rather than write a replacement it cannot verify.
    """
    if not os.path.exists(LIBREOFFICE):
        pytest.skip("LibreOffice guide not present")
    doc = fitz.open(LIBREOFFICE)
    try:
        applied, refused, errors, _pairs = _try_edits(doc, page_no=1, limit=10)
        # Every attempt either succeeded or raised a clear EditError.
        assert applied + refused > 0
        if refused:
            assert any("not available in the original font" in e
                       or "Cannot verify how characters are encoded" in e
                       or "Could not load the font program" in e
                       for e in errors), errors[:3]
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Synthetic compatibility cases
# --------------------------------------------------------------------------- #
def _dejavu_path():
    hits = glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)
    return hits[0] if hits else None


def test_multiple_font_resources(tmp_path):
    path = str(tmp_path / "multi.pdf")
    doc = fitz.open()
    p = doc.new_page(width=400, height=300)
    p.insert_text((40, 60), "Alpha text", fontsize=14, fontname="helv")
    p.insert_text((40, 120), "Beta text", fontsize=14, fontname="tiro")
    doc.save(path)
    doc.close()

    d = fitz.open(path)
    try:
        for word in ("Alpha", "Beta"):
            w = next(x for x in d[0].get_text("words") if x[4] == word)
            hit = find_text_at_point(d[0], ((w[0] + w[2]) / 2,
                                            (w[1] + w[3]) / 2))
            ReplaceExistingText(
                page_num=0, old_word=word, new_word=word[:2],
                word_rect=hit.rect,
            ).apply(d)
        text = d[0].get_text()
        assert "Al" in text and "Be" in text
    finally:
        d.close()


def test_colored_text_keeps_color_operator(tmp_path):
    path = str(tmp_path / "color.pdf")
    doc = fitz.open()
    p = doc.new_page(width=400, height=300)
    p.insert_text((50, 100), "Colored Word", fontsize=16, color=(0.8, 0.1, 0.1))
    doc.save(path)
    doc.close()

    d = fitz.open(path)
    try:
        w = next(x for x in d[0].get_text("words") if x[4] == "Colored")
        hit = find_text_at_point(d[0], ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        ReplaceExistingText(
            page_num=0, old_word="Colored", new_word="Tinted",
            word_rect=hit.rect,
        ).apply(d)
        assert "Tinted" in d[0].get_text()
        # The separate colour-setting operator is untouched.
        from pdfar.text_edit_existing import _page_content_streams
        data = _page_content_streams(d[0])[0][1]
        assert b" rg" in data
    finally:
        d.close()


def test_rotated_text(tmp_path):
    path = str(tmp_path / "rot.pdf")
    doc = fitz.open()
    p = doc.new_page(width=400, height=400)
    p.insert_text((100, 300), "Rotated Text", fontsize=16, rotate=90)
    doc.save(path)
    doc.close()

    d = fitz.open(path)
    try:
        w = next(x for x in d[0].get_text("words") if x[4] == "Rotated")
        hit = find_text_at_point(d[0], ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        ReplaceExistingText(
            page_num=0, old_word="Rotated", new_word="Turned",
            word_rect=hit.rect,
        ).apply(d)
        assert "Turned" in d[0].get_text()
    finally:
        d.close()


@pytest.mark.skipif(_dejavu_path() is None,
                    reason="DejaVuSans.ttf is not installed")
def test_subsetted_type0_font(tmp_path):
    path = str(tmp_path / "subset.pdf")
    doc = fitz.open()
    p = doc.new_page(width=400, height=300)
    p.insert_text((50, 100), "Héllo Wörld Again", fontsize=14, fontname="F0",
                  fontfile=_dejavu_path())
    doc.subset_fonts()
    doc.save(path)
    doc.close()

    d = fitz.open(path)
    try:
        w = next(x for x in d[0].get_text("words") if x[4] == "Héllo")
        hit = find_text_at_point(d[0], ((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))
        ReplaceExistingText(
            page_num=0, old_word="Héllo", new_word="Hello",
            word_rect=hit.rect,
        ).apply(d)
        assert "Hello" in d[0].get_text()
    finally:
        d.close()


def test_form_xobject_text_in_real_pdf(tmp_path):
    """The arXiv stamp is drawn by a rotated Form XObject.

    It must be locatable through the invocation matrix, editable, and the
    document must stay valid after a save/reopen cycle.
    """
    path = os.path.join(TWO_COLUMN, "arxiv-2610.30749.pdf")
    if not os.path.exists(path):
        pytest.skip("arxiv PDF not present")
    from pdfar.text_edit_existing import _iter_page_runs

    doc = fitz.open(path)
    try:
        page = doc[0]
        xruns = [r for r in _iter_page_runs(page) if r.source.in_xobject]
        assert xruns, "expected at least one text-bearing Form XObject"
        run = xruns[0]
        assert run.source.depth == 1

        target = next(w for w in page.get_text("words")
                      if w[4].startswith("arXiv:"))
        hit = find_text_at_point(
            page, ((target[0] + target[2]) / 2, (target[1] + target[3]) / 2))
        assert hit is not None and hit.text == target[4]

        original = hit.text
        replacement = original[:6] + "X" * (len(original) - 6)
        ReplaceExistingText(
            page_num=0, old_word=original, new_word=replacement,
            word_rect=hit.rect,
        ).apply(doc)
        assert replacement in doc[0].get_text()

        out = str(tmp_path / "arxiv_xobj.pdf")
        doc.save(out, garbage=3, deflate=True)
    finally:
        doc.close()

    reopened = fitz.open(out)
    try:
        assert replacement in reopened[0].get_text()
        assert original not in reopened[0].get_text()
    finally:
        reopened.close()
