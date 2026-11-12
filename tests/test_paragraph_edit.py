"""Phase 5 — the ReflowParagraph edit (spec §6, §8, §9).

Exercises :class:`pdfar.paragraph_edit.ReflowParagraph` through a
:class:`DocumentSession` so undo/redo, save/reopen and rendering invalidation are
covered exactly as a user-triggered edit would be.  The planner used here is the
same one the preview dialog uses, so preview and commit cannot disagree.
"""

from __future__ import annotations

import fitz
import pytest

from pdfar.paragraph import (
    extract_paragraphs,
    extract_range,
)
from pdfar.paragraph_edit import ReflowParagraph
from pdfar.edits import EditError, Scope
from pdfar.font_discovery import FontQuery, find_alternatives, supports
from pdfar.text_edit_existing import FontSubstitution


def _para(session, index=0):
    return extract_paragraphs(session.doc[0])[index]


def _space_text(para):
    return " ".join(line.text for line in para.lines)


def _build_edit(session, text=None, **kw):
    para = _para(session)
    new_text = text if text is not None else _space_text(para)
    return ReflowParagraph(
        page_num=0,
        first_index=para.first_index,
        last_index=para.last_index,
        new_text=new_text,
        anchor=para.text,
        **kw,
    )


# --------------------------------------------------------------------------- #
# No-op commit reproduces the same paragraph (spec §7)
# --------------------------------------------------------------------------- #
def test_noop_reflow_roundtrip(paragraph_session):
    para = _para(paragraph_session)
    before = _space_text(para)
    edit = _build_edit(paragraph_session)
    version, scope = paragraph_session.apply(edit)
    assert scope == Scope.CONTENT
    after = extract_paragraphs(paragraph_session.doc[0])[0]
    assert after.line_count == para.line_count
    # The extracted text is unchanged for a no-op (whitespace-insensitive).
    assert before.split() == after.text.replace("\n", " ").split()


# --------------------------------------------------------------------------- #
# Shorter / longer replacements change the page (spec §4, §9)
# --------------------------------------------------------------------------- #
def test_shorter_replacement_commits(paragraph_session):
    edit = _build_edit(paragraph_session, "One short line.")
    paragraph_session.apply(edit)
    out = extract_paragraphs(paragraph_session.doc[0])[0]
    assert out.line_count == 1
    assert "One short line." in out.text


def test_longer_replacement_gains_lines(paragraph_session):
    para = _para(paragraph_session)
    longer = _space_text(para) + (" Extra words added to force the text onto "
                                  "more wrapped lines. ") * 4
    edit = _build_edit(paragraph_session, longer)
    paragraph_session.apply(edit)
    out = extract_paragraphs(paragraph_session.doc[0])[0]
    assert out.line_count > para.line_count


# --------------------------------------------------------------------------- #
# Undo / redo keep a clean document (spec §8)
# --------------------------------------------------------------------------- #
def test_undo_redo_paragraph(paragraph_session):
    para = _para(paragraph_session)
    original = extract_range(paragraph_session.doc[0],
                            para.first_index, para.last_index)
    edit = _build_edit(paragraph_session, "Undoable paragraph text here.")
    paragraph_session.apply(edit)
    assert extract_paragraphs(paragraph_session.doc[0])[0].text != original.text
    paragraph_session.undo()
    restored = extract_range(paragraph_session.doc[0],
                            para.first_index, para.last_index)
    assert _norm(restored.text) == _norm(original.text)
    paragraph_session.redo()
    assert "Undoable paragraph text here." in (
        extract_paragraphs(paragraph_session.doc[0])[0].text)


def _norm(text):
    return "".join(text.split())


def test_dirty_flag_after_edit(paragraph_session):
    assert paragraph_session.dirty is False
    paragraph_session.apply(_build_edit(paragraph_session, "Short."))
    assert paragraph_session.dirty is True


# --------------------------------------------------------------------------- #
# Save / reopen survives (spec §9)
# --------------------------------------------------------------------------- #
def test_save_reopen_preserves_reflow(paragraph_session, tmp_path):
    edit = _build_edit(paragraph_session,
                       "Survives save and reopen correctly.")
    paragraph_session.apply(edit)
    out_path = str(tmp_path / "out.pdf")
    paragraph_session.doc.save(out_path)
    reopened = fitz.open(out_path)
    try:
        out = extract_paragraphs(reopened[0])[0]
        assert "Survives save and reopen correctly." in out.text
        assert out.line_count == 1
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Substitution path: original font cannot draw the text (spec §9)
# --------------------------------------------------------------------------- #
def test_substitution_edit_applies(paragraph_session, tmp_path):
    # A character the embedded Helvetica cannot draw, so a substitute must be
    # embedded.  Pick a substitute that actually covers the missing glyph.
    new_text = "Hello 😀 world from the editor here."
    query = FontQuery(basefont="Helvetica", bold=False, italic=False,
                      text=new_text)
    matches = [m for m in find_alternatives(query) if supports(m.face, new_text)]
    assert matches, "expected an installed font that covers the emoji"
    edit = _build_edit(
        paragraph_session, new_text,
        substitute=FontSubstitution(face=matches[0].face))
    version, scope = paragraph_session.apply(edit)
    assert scope == Scope.CONTENT
    out = extract_paragraphs(paragraph_session.doc[0])[0]
    assert "😀" in out.text
    # Survives save/reopen with the embedded substitute font.
    out_path = str(tmp_path / "sub.pdf")
    paragraph_session.doc.save(out_path)
    reopened = fitz.open(out_path)
    try:
        assert "😀" in extract_paragraphs(reopened[0])[0].text
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Refusal cases (spec §6)
# --------------------------------------------------------------------------- #
def test_changed_page_refuses(paragraph_session):
    para = _para(paragraph_session)
    edit = ReflowParagraph(
        page_num=0, first_index=para.first_index, last_index=para.last_index,
        new_text=_space_text(para) + " and something new",
        anchor="definitely not the current text")
    with pytest.raises(EditError):
        paragraph_session.apply(edit)


def test_overflow_edit_refused_without_shrink(tmp_path):
    from tests.conftest import build_paragraph_pdf, make_session
    from pdfar.paragraph import extract_paragraphs, ParagraphOverflow

    path = build_paragraph_pdf(tmp_path / "short.pdf", height=240,
                               region=(100, 100, 500, 220))
    sess = make_session(path)
    try:
        page = sess.doc[0]
        para = extract_paragraphs(page)[0]
        huge = ("An extremely long paragraph that needs far more vertical room "
                "than the page provides, " * 6)
        edit = ReflowParagraph(
            page_num=0, first_index=para.first_index, last_index=para.last_index,
            new_text=huge, anchor=para.text)
        with pytest.raises(ParagraphOverflow):
            sess.apply(edit)
    finally:
        sess.close()
