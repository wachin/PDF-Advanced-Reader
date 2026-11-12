"""Pixel-level verification of existing-text edits.

Renders the page before and after an edit (through a save/reopen round-trip)
and compares the affected region against unrelated regions.  The goal is to
prove that the edit changes the target glyphs while leaving the rest of the
page visually intact.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest

from pdfar.text_edit_existing import find_text_at_point, ReplaceExistingText


def _build_pdf(tmp_path):
    path = str(tmp_path / "visual.pdf")
    doc = fitz.open()
    p = doc.new_page(width=300, height=200)
    p.insert_text((40, 60), "Hello World", fontsize=16)
    p.insert_text((40, 160), "Unrelated footer", fontsize=12)
    p.draw_rect(fitz.Rect(200, 20, 260, 60),
                color=(0.2, 0.4, 0.8), fill=(0.2, 0.4, 0.8))
    doc.save(path)
    doc.close()
    return path


def _samples(doc, page_no, clip, dpi=144):
    pix = doc[page_no].get_pixmap(clip=clip, dpi=dpi, alpha=False)
    return pix.samples


def _mean_abs(a, b):
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(abs(a[i] - b[i]) for i in range(n)) / n


def _word_rect(page, text):
    w = next(w for w in page.get_text("words") if w[4] == text)
    return (w[0], w[1], w[2], w[3])


def test_edit_changes_target_region_only(tmp_path):
    path = _build_pdf(tmp_path)
    doc = fitz.open(path)
    page = doc[0]
    x0, y0, x1, y1 = _word_rect(page, "World")
    hit = find_text_at_point(page, ((x0 + x1) / 2, (y0 + y1) / 2))
    assert hit is not None

    target_clip = fitz.Rect(x0 - 4, y0 - 4, x1 + 48, y1 + 4)
    footer_clip = fitz.Rect(30, 145, 250, 175)
    graphic_clip = fitz.Rect(195, 15, 265, 65)

    before_target = _samples(doc, 0, target_clip)
    before_footer = _samples(doc, 0, footer_clip)
    before_graphic = _samples(doc, 0, graphic_clip)

    edit = ReplaceExistingText(
        page_num=0, old_word="World", new_word="Earth", word_rect=hit.rect,
    )
    edit.apply(doc)
    out = str(tmp_path / "out.pdf")
    doc.save(out)
    doc.close()

    reopened = fitz.open(out)
    try:
        after_target = _samples(reopened, 0, target_clip)
        after_footer = _samples(reopened, 0, footer_clip)
        after_graphic = _samples(reopened, 0, graphic_clip)

        assert "Earth" in reopened[0].get_text()
        # The edited region visibly changed.
        assert _mean_abs(before_target, after_target) > 3.0
        # Unrelated content is visually unchanged (allow tiny render noise).
        assert _mean_abs(before_footer, after_footer) < 1.0
        assert _mean_abs(before_graphic, after_graphic) < 1.0
    finally:
        reopened.close()


def test_long_replacement_overflows_original_box(tmp_path):
    """An over-wide replacement changes pixels beyond the original word box."""
    path = _build_pdf(tmp_path)
    doc = fitz.open(path)
    page = doc[0]
    x0, y0, x1, y1 = _word_rect(page, "World")
    hit = find_text_at_point(page, ((x0 + x1) / 2, (y0 + y1) / 2))

    # A strip to the right of the original word, where overflow would land.
    overflow_clip = fitz.Rect(x1 + 2, y0 - 4, x1 + 150, y1 + 4)
    before = _samples(doc, 0, overflow_clip)

    edit = ReplaceExistingText(
        page_num=0, old_word="World",
        new_word="WonderfulWorldOfText", word_rect=hit.rect,
    )
    edit.apply(doc)
    out = str(tmp_path / "overflow.pdf")
    doc.save(out)
    doc.close()

    reopened = fitz.open(out)
    try:
        after = _samples(reopened, 0, overflow_clip)
        assert _mean_abs(before, after) > 2.0
    finally:
        reopened.close()


def test_edit_preserves_page_structure(tmp_path):
    path = _build_pdf(tmp_path)
    doc = fitz.open(path)
    page = doc[0]
    x0, y0, x1, y1 = _word_rect(page, "World")
    hit = find_text_at_point(page, ((x0 + x1) / 2, (y0 + y1) / 2))
    pages_before = doc.page_count
    other_before = "Unrelated footer" in page.get_text()

    edit = ReplaceExistingText(
        page_num=0, old_word="World", new_word="Earth", word_rect=hit.rect,
    )
    edit.apply(doc)

    assert doc.page_count == pages_before
    assert "Unrelated footer" in doc[0].get_text()
    assert other_before
    doc.close()
