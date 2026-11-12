"""Phase 5 — paragraph model and layout engine (spec §2, §4, §5, §7).

These tests exercise :mod:`pdfar.paragraph` directly: extraction, the paragraph
model, line breaking, alignment, indentation, overflow / collision detection,
rotated paragraphs and font-substitution planning.  They do **not** touch the
document (planning is read-only) and therefore cannot damage a PDF.
"""

from __future__ import annotations

import fitz
import pytest

from pdfar.paragraph import (
    LayoutOptions,
    ParagraphUnsupported,
    extract_paragraphs,
    extract_range,
    plan_reflow,
)


def _para(session, index=0):
    return extract_paragraphs(session.doc[0])[index]


def _space_text(para):
    return " ".join(line.text for line in para.lines)


# --------------------------------------------------------------------------- #
# Extraction / model
# --------------------------------------------------------------------------- #
def test_extract_groups_one_paragraph(paragraph_session):
    paras = extract_paragraphs(paragraph_session.doc[0])
    assert len(paras) == 1
    para = paras[0]
    assert para.line_count >= 3
    assert para.font == "Helvetica"
    assert para.font_size == 12
    assert para.alignment in ("left", "center", "right", "justify")
    assert para.confidence > 0
    assert para.problems == ()


def test_extract_range_matches_extract(paragraph_session):
    para = _para(paragraph_session)
    again = extract_range(paragraph_session.doc[0],
                          para.first_index, para.last_index)
    assert again.line_count == para.line_count
    assert again.text == para.text


def test_extract_range_out_of_bounds(paragraph_session):
    with pytest.raises(ParagraphUnsupported):
        extract_range(paragraph_session.doc[0], 0, 999)


# --------------------------------------------------------------------------- #
# No-op reflow preserves the layout (spec §1, §7)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alignment", ["left", "justify"])
def test_noop_reflow_preserves_line_count(paragraph_session, alignment):
    para = _para(paragraph_session)
    plan = plan_reflow(
        paragraph_session.doc[0], para, _space_text(para),
        LayoutOptions(alignment=alignment), region_width=400)
    assert plan.feasible
    assert plan.line_count_delta == 0
    assert plan.line_count == para.line_count


# --------------------------------------------------------------------------- #
# Shorter / longer replacements change the line count (spec §4)
# --------------------------------------------------------------------------- #
def test_shorter_text_loses_lines(paragraph_session):
    para = _para(paragraph_session)
    shorter = "A single short line of text."
    plan = plan_reflow(paragraph_session.doc[0], para, shorter, None,
                       region_width=400)
    assert plan.feasible
    assert plan.line_count_delta < 0
    assert plan.line_count == 1


def test_longer_text_gains_lines(paragraph_session):
    para = _para(paragraph_session)
    longer = _space_text(para) + (" The paragraph grows with more words added "
                                  "by the editor so it must wrap onto extra "
                                  "lines inside the same region. ") * 4
    plan = plan_reflow(paragraph_session.doc[0], para, longer, None,
                       region_width=400)
    assert plan.feasible
    assert plan.line_count_delta > 0


# --------------------------------------------------------------------------- #
# Explicit newlines are hard breaks (spec §4)
# --------------------------------------------------------------------------- #
def test_explicit_newline_is_hard_break(paragraph_session):
    para = _para(paragraph_session)
    plan = plan_reflow(paragraph_session.doc[0], para,
                       "First forced line.\nSecond forced line.",
                       None, region_width=400)
    assert plan.feasible
    assert any(line.hard_break for line in plan.lines)


# --------------------------------------------------------------------------- #
# Alignment (spec §4)
# --------------------------------------------------------------------------- #
def test_alignment_left_right_center(paragraph_session):
    para = _para(paragraph_session)
    text = _space_text(para)
    for alignment in ("left", "right", "center"):
        plan = plan_reflow(paragraph_session.doc[0], para, text,
                           LayoutOptions(alignment=alignment), region_width=400)
        assert plan.alignment == alignment
        assert plan.feasible


# --------------------------------------------------------------------------- #
# Overflow is refused until the user approves shrinking (spec §5)
# --------------------------------------------------------------------------- #
def test_overflow_refused_without_shrink(tmp_path):
    from tests.conftest import build_paragraph_pdf
    # A short page so even a moderately longer paragraph overflows.
    path = build_paragraph_pdf(tmp_path / "short.pdf", height=240,
                               region=(100, 100, 500, 220))
    doc = fitz.open(path)
    try:
        para = extract_paragraphs(doc[0])[0]
        huge = ("An extremely long paragraph that needs far more vertical room "
                "than the page provides, " * 6)
        # Planning reports (does not raise) an infeasible plan.
        plan = plan_reflow(doc[0], para, huge, None, region_width=400)
        assert not plan.feasible
        assert plan.overflow_bottom > 0
    finally:
        doc.close()


def test_overflow_allowed_with_shrink(tmp_path):
    from tests.conftest import build_paragraph_pdf
    path = build_paragraph_pdf(tmp_path / "short.pdf", height=240,
                               region=(100, 100, 500, 220))
    doc = fitz.open(path)
    try:
        para = extract_paragraphs(doc[0])[0]
        huge = ("An extremely long paragraph that needs far more vertical room "
                "than the page provides, " * 6)
        plan = plan_reflow(
            doc[0], para, huge,
            LayoutOptions(alignment="left", allow_shrink=True),
            region_width=400)
        # With explicit approval the text fits by shrinking.
        assert plan.feasible
        assert plan.font_scale < 1.0
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Collision with a neighbour is reported (spec §5, §7)
# --------------------------------------------------------------------------- #
def test_collision_with_footer_reported(tmp_path):
    from tests.conftest import build_paragraph_pdf
    # The paragraph starts near the top; a footer sits just below where extra
    # wrapped lines will land, so a longer reflow must report a collision.
    path = build_paragraph_pdf(tmp_path / "p.pdf", lines=3,
                               region=(100, 60, 500, 760))
    sess = fitz.open(path)
    try:
        para = extract_paragraphs(sess[0])[0]
        # Footer just below the original paragraph's bottom (~y 150).
        sess[0].insert_text((100, 168), "Footer note here", fontsize=12)
        longer = (" ".join(line.text for line in para.lines)
                  + " Extra words that push the text down into the footer. " * 3)
        plan = plan_reflow(sess[0], para, longer, None, region_width=400)
        assert plan.collisions, "expected a collision with the footer"
        assert any(c.kind in ("text", "drawing") for c in plan.collisions)
    finally:
        sess.close()


# --------------------------------------------------------------------------- #
# Rotated paragraphs (spec §4)
# --------------------------------------------------------------------------- #
def test_rotated_paragraph_reflows():
    # Built in memory: PyMuPDF's save collapses the rotation into the glyph
    # transforms, so the rotation must be inspected before any save/reopen.
    # A single rotated line keeps the rotation in its text matrix, which is the
    # case the reflow writer reproduces exactly.
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((300, 680), "Rotated single line of text here.",
                     fontsize=12, fontname="helv", rotate=90)
    paras = extract_paragraphs(page)
    rotated = [p for p in paras if p.rotated]
    assert rotated, "expected at least one rotated paragraph"
    para = rotated[0]
    text = _space_text(para)
    plan = plan_reflow(page, para, text, None)
    assert plan.feasible
    # The planned line sits on the rotated baseline, not at the origin.
    assert plan.lines[0].baseline != (0.0, 0.0)


# --------------------------------------------------------------------------- #
# Font substitution planning (spec §4, §9)
# --------------------------------------------------------------------------- #
def test_substitution_needed_reported(paragraph_session):
    para = _para(paragraph_session)
    # A character the embedded Helvetica cannot draw (no glyph for it).
    plan = plan_reflow(paragraph_session.doc[0], para,
                       "Hello 😀 world", None, region_width=400)
    assert plan.needs_substitution
    assert plan.feasible is False
    assert plan.missing
