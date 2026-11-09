"""Existing-text editing: hit testing, operator mapping, surgical replacement.

These tests exercise the genuine-content-stream editing engine in
``pdfar.text_edit_existing`` end to end, including save/reopen and undo/redo
through the real ``DocumentSession``.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz
import pytest

from pdfar.edits import EditError
from pdfar.text_edit_existing import (
    find_text_at_point,
    locate_operator_for_word,
    ReplaceExistingText,
    _page_content_streams,
    _replace_in_operands,
)
from pdfar.content_stream import PDFOperator


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def literal_pdf(tmp_path):
    """A PDF whose page content is hand-written literal Tj operators.

    Two lines share the word "the" so we can test disambiguation.
    """
    path = str(tmp_path / "literal.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 700), "placeholder")  # ensure a content stream exists
    contents = p.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    stream = (
        b"q\nBT\n/helv 14 Tf\n"
        b"1 0 0 1 100 700 Tm\n"
        b"(the quick brown fox) Tj\n"
        b"0 -20 Td\n"
        b"(jumps over the lazy dog) Tj\n"
        b"ET\nQ\n"
    )
    p.insert_font(fontname="helv")
    p.parent.update_stream(xref, stream.replace(b"/F1", b"/helv"))
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def session(literal_pdf):
    doc = fitz.open(literal_pdf)
    from pdfar.session import DocumentSession
    s = DocumentSession(doc, literal_pdf, "", 0)
    try:
        yield s
    finally:
        s.close()


def _click_center(word):
    return ((word[0] + word[2]) / 2.0, (word[1] + word[3]) / 2.0)


# --------------------------------------------------------------------------- #
# Hit testing
# --------------------------------------------------------------------------- #
def test_find_text_at_point_inside_box(session):
    page = session.doc[0]
    words = page.get_text("words")
    target = next(w for w in words if w[4] == "quick")
    hit = find_text_at_point(page, _click_center(target))
    assert hit is not None
    assert hit.text == "quick"


def test_find_text_at_point_off_box_uses_threshold(session):
    page = session.doc[0]
    # A point just above the first line's text should still snap to a word.
    hit = find_text_at_point(page, (110.0, 70.0), threshold=20.0)
    assert hit is not None


def test_find_text_at_point_empty_page():
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    assert find_text_at_point(doc[0], (10, 10)) is None
    doc.close()


# --------------------------------------------------------------------------- #
# Operator location + surgical replacement
# --------------------------------------------------------------------------- #
def test_locate_matches_correct_line(session):
    page = session.doc[0]
    words = page.get_text("words")
    # Second "the" (in the lazy dog line).
    target = [w for w in words if w[4] == "the"][1]
    hit = find_text_at_point(page, _click_center(target))
    loc = locate_operator_for_word(page, hit.text, hit.rect)
    assert loc is not None
    assert loc.text == "jumps over the lazy dog"


def test_surgical_replacement_preserves_other_operators(session):
    page = session.doc[0]
    words = page.get_text("words")
    target = next(w for w in words if w[4] == "quick")
    hit = find_text_at_point(page, _click_center(target))
    edit = ReplaceExistingText(
        page_num=0, old_word="quick", new_word="slow", word_rect=hit.rect,
        font=hit.font, font_size=hit.font_size, color=hit.color, flags=hit.flags,
    )
    edit.apply(session.doc)
    text = session.doc[0].get_text()
    assert "slow" in text
    assert "quick" not in text
    # The other line is untouched.
    assert "lazy dog" in text
    # Stream structure outside the operator is byte-identical.
    xref, data = _page_content_streams(session.doc[0])[0]
    decoded = data.decode("latin-1")
    assert "q\nBT" in decoded
    assert "/helv 14 Tf" in decoded
    assert "ET\nQ" in decoded


def test_replacement_in_hex_tj(tmp_path):
    """insert_text emits a TJ with a hex string; replacement must work there."""
    path = str(tmp_path / "hex.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 200), "Hello World Page 1", fontsize=14)
    doc.save(path)
    doc.close()

    s = fitz.open(path)
    try:
        page = s[0]
        words = page.get_text("words")
        target = next(w for w in words if w[4] == "World")
        hit = find_text_at_point(page, _click_center(target))
        edit = ReplaceExistingText(
            page_num=0, old_word="World", new_word="Earth", word_rect=hit.rect,
            font=hit.font, font_size=hit.font_size, color=hit.color, flags=hit.flags,
        )
        edit.apply(s)
        assert "Earth" in s[0].get_text()
        assert "World" not in s[0].get_text()
    finally:
        s.close()


def test_accents_survive_replacement(tmp_path):
    path = str(tmp_path / "acc.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 200), "café naïve résumé", fontsize=14)
    doc.save(path)
    doc.close()
    s = fitz.open(path)
    try:
        page = s[0]
        words = page.get_text("words")
        target = next(w for w in words if w[4] == "café")
        hit = find_text_at_point(page, _click_center(target))
        edit = ReplaceExistingText(
            page_num=0, old_word="café", new_word="coffee", word_rect=hit.rect,
        )
        edit.apply(s)
        assert "coffee" in s[0].get_text()
        assert "café" not in s[0].get_text()
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Font support (Strategy D)
# --------------------------------------------------------------------------- #
def test_refuses_glyphs_not_in_font(session):
    page = session.doc[0]
    words = page.get_text("words")
    target = next(w for w in words if w[4] == "fox")
    hit = find_text_at_point(page, _click_center(target))
    # "中" is not in base-14 Helvetica.
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="中", word_rect=hit.rect,
    )
    with pytest.raises(EditError, match="not available in the original font"):
        edit.apply(session.doc)


def test_refuses_word_split_across_kerning(tmp_path):
    """A word split across TJ string segments must be refused, not corrupted."""
    path = str(tmp_path / "kern.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 700), "x")
    contents = p.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    # "World" split into "Wor" and "ld" by a kerning number.
    stream = (
        b"q\nBT\n/helv 14 Tf\n1 0 0 1 100 700 Tm\n"
        b"[ (Wor) -40 (ld) ] TJ\nET\nQ\n"
    )
    p.insert_font(fontname="helv")
    p.parent.update_stream(xref, stream)
    doc.save(path)
    doc.close()

    s = fitz.open(path)
    try:
        page = s[0]
        words = page.get_text("words")
        target = next(w for w in words if w[4] == "World")
        hit = find_text_at_point(page, _click_center(target))
        loc = locate_operator_for_word(page, hit.text, hit.rect)
        assert loc is not None
        # The operator text joins to "World" so location succeeds...
        edit = ReplaceExistingText(
            page_num=0, old_word="World", new_word="Planet", word_rect=hit.rect,
        )
        with pytest.raises(EditError, match="split across kerning"):
            edit.apply(s)
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Integration: session apply / undo / redo / save / reopen
# --------------------------------------------------------------------------- #
def test_session_apply_undo_redo(session):
    page = session.doc[0]
    words = page.get_text("words")
    target = next(w for w in words if w[4] == "quick")
    hit = find_text_at_point(page, _click_center(target))
    edit = ReplaceExistingText(
        page_num=0, old_word="quick", new_word="slow", word_rect=hit.rect,
    )
    session.apply(edit)
    assert "slow" in session.doc[0].get_text()
    assert "quick" not in session.doc[0].get_text()

    session.undo()
    assert "quick" in session.doc[0].get_text()
    assert "slow" not in session.doc[0].get_text()

    session.redo()
    assert "slow" in session.doc[0].get_text()
    assert "quick" not in session.doc[0].get_text()


def test_save_reopen_preserves_edit(tmp_path, literal_pdf):
    out = str(tmp_path / "out.pdf")
    doc = fitz.open(literal_pdf)
    page = doc[0]
    words = page.get_text("words")
    target = next(w for w in words if w[4] == "fox")
    hit = find_text_at_point(page, _click_center(target))
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="wolf", word_rect=hit.rect,
    )
    edit.apply(doc)
    doc.save(out)
    doc.close()

    reopened = fitz.open(out)
    try:
        assert "wolf" in reopened[0].get_text()
        assert "fox" not in reopened[0].get_text()
        # The untouched line is still intact.
        assert "lazy dog" in reopened[0].get_text()
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Low-level operand replacement
# --------------------------------------------------------------------------- #
def test_replace_in_operands_tj():
    op = PDFOperator("Tj", ["Hello World"], 0, 11)
    new_ops, spanned = _replace_in_operands(op, "World", "Earth")
    assert not spanned
    assert new_ops == ["Hello Earth"]


def test_replace_in_operands_tj_bytes():
    op = PDFOperator("TJ", [[b"Hello World"]], 0, 15)
    new_ops, spanned = _replace_in_operands(op, "World", "Earth")
    assert not spanned
    val = new_ops[0][0]
    if isinstance(val, bytes):
        val = val.decode("latin-1", "ignore")
    assert val == "Hello Earth"


def test_replace_in_operands_tj_spanned():
    # "World" split across two string segments -> spanned flag.
    op = PDFOperator("TJ", [["Wor", -40, "ld"]], 0, 20)
    new_ops, spanned = _replace_in_operands(op, "World", "Planet")
    assert spanned is True
