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
    _validate_replacement,
    combine_hits,
    words_between,
    words_in_line,
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


def test_refuses_when_get_fonts_raises(session, monkeypatch):
    """An inspection failure is a refusal, never an implicit approval."""
    page = session.doc[0]
    target = next(w for w in page.get_text("words") if w[4] == "fox")
    hit = find_text_at_point(page, _click_center(target))

    def boom(self, *args, **kwargs):
        raise RuntimeError("font introspection failed")

    monkeypatch.setattr(fitz.Page, "get_fonts", boom)
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="wolf", word_rect=hit.rect,
    )
    with pytest.raises(EditError, match="Could not inspect the page fonts"):
        edit.apply(session.doc)


def test_refuses_when_font_program_cannot_be_loaded(session, monkeypatch):
    page = session.doc[0]
    target = next(w for w in page.get_text("words") if w[4] == "fox")
    hit = find_text_at_point(page, _click_center(target))

    def boom(*args, **kwargs):
        raise RuntimeError("font program unavailable")

    # Patch the module the engine actually uses (pymupdf), not the fitz shim.
    monkeypatch.setattr("pdfar.text_edit_existing.fitz.Font", boom)
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="wolf", word_rect=hit.rect,
    )
    with pytest.raises(EditError, match="Could not load the font program"):
        edit.apply(session.doc)


# --------------------------------------------------------------------------- #
# Encoding-aware replacement (Type0 / embedded fonts)
# --------------------------------------------------------------------------- #
def _dejavu_path():
    import glob
    hits = glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)
    return hits[0] if hits else None


def _make_type0_pdf(tmp_path, text="Héllo Wörld"):
    """A PDF using an embedded DejaVuSans Type0/Identity-H font."""
    path = str(tmp_path / "type0.pdf")
    doc = fitz.open()
    p = doc.new_page(width=400, height=300)
    p.insert_text((50, 100), text, fontsize=14, fontname="F0",
                  fontfile=_dejavu_path())
    doc.save(path)
    doc.close()
    return path


@pytest.mark.skipif(_dejavu_path() is None,
                    reason="DejaVuSans.ttf is not installed")
def test_type0_identity_h_replacement(tmp_path):
    """Type0/Identity-H text is decoded and re-encoded via ToUnicode."""
    path = _make_type0_pdf(tmp_path)
    s = fitz.open(path)
    try:
        page = s[0]
        target = next(w for w in page.get_text("words") if w[4] == "Wörld")
        hit = find_text_at_point(page, _click_center(target))
        edit = ReplaceExistingText(
            page_num=0, old_word="Wörld", new_word="Earth", word_rect=hit.rect,
        )
        edit.apply(s)
        text = s[0].get_text()
        assert "Earth" in text
        assert "Wörld" not in text
        # The other word is untouched.
        assert "Héllo" in text
    finally:
        s.close()


@pytest.mark.skipif(_dejavu_path() is None,
                    reason="DejaVuSans.ttf is not installed")
def test_type0_replacement_with_accent(tmp_path):
    """A non-ASCII replacement is encoded through the Type0 ToUnicode map."""
    path = _make_type0_pdf(tmp_path)
    s = fitz.open(path)
    try:
        page = s[0]
        target = next(w for w in page.get_text("words") if w[4] == "Héllo")
        hit = find_text_at_point(page, _click_center(target))
        edit = ReplaceExistingText(
            page_num=0, old_word="Héllo", new_word="Café", word_rect=hit.rect,
        )
        edit.apply(s)
        assert "Café" in s[0].get_text()
    finally:
        s.close()


@pytest.mark.skipif(_dejavu_path() is None,
                    reason="DejaVuSans.ttf is not installed")
def test_type0_without_tounicode_is_refused(tmp_path):
    """Without a ToUnicode map a Type0 font cannot be reverse-mapped safely."""
    path = _make_type0_pdf(tmp_path)
    doc = fitz.open(path)
    try:
        fx = doc[0].get_fonts()[0][0]
        doc.xref_set_key(fx, "ToUnicode", "null")
        ok, reason, codec = _validate_replacement(doc, doc[0], "F0", "Hello")
        assert ok is False
        assert codec is None
        assert "Cannot verify how characters are encoded" in reason
    finally:
        doc.close()


def test_simple_font_replacement_with_accent(session):
    """WinAnsi simple fonts encode accented replacements via cp1252."""
    page = session.doc[0]
    target = next(w for w in page.get_text("words") if w[4] == "fox")
    hit = find_text_at_point(page, _click_center(target))
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="fóx", word_rect=hit.rect,
    )
    edit.apply(session.doc)
    assert "fóx" in session.doc[0].get_text()


def test_supports_word_split_across_kerning(tmp_path):
    """A word split across TJ string segments is reconstructed, not refused."""
    path = str(tmp_path / "kern.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 700), "x")
    contents = p.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    # "World" split into "Wor" and "ld" by a kerning number, with text after.
    stream = (
        b"q\nBT\n/helv 14 Tf\n1 0 0 1 100 700 Tm\n"
        b"[ (Wor) -40 (ld) ( again) ] TJ\nET\nQ\n"
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
        edit = ReplaceExistingText(
            page_num=0, old_word="World", new_word="Planet", word_rect=hit.rect,
        )
        edit.apply(s)
        text = s[0].get_text()
        assert "Planet" in text
        assert "World" not in text
        # Text after the replaced word is preserved.
        assert "again" in text
    finally:
        s.close()


def test_kerning_outside_word_is_preserved(tmp_path):
    """Kerning numbers outside the matched word must survive the edit."""
    path = str(tmp_path / "kern2.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    p.insert_text((100, 700), "x")
    contents = p.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    stream = (
        b"q\nBT\n/helv 14 Tf\n1 0 0 1 100 700 Tm\n"
        b"[ (AB ) -25 (World) -15 ( CD) ] TJ\nET\nQ\n"
    )
    p.insert_font(fontname="helv")
    p.parent.update_stream(xref, stream)
    doc.save(path)
    doc.close()

    s = fitz.open(path)
    try:
        page = s[0]
        target = next(w for w in page.get_text("words") if w[4] == "World")
        hit = find_text_at_point(page, _click_center(target))
        edit = ReplaceExistingText(
            page_num=0, old_word="World", new_word="Earth", word_rect=hit.rect,
        )
        edit.apply(s)
        xref2, data = _page_content_streams(s[0])[0]
        assert b"<4561727468>" in data  # "Earth" re-encoded as a hex string
        assert b"-25" in data  # kerning before the word kept
        assert b"-15" in data  # kerning after the word kept
        assert "Earth" in s[0].get_text()
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
    new_ops, status = _replace_in_operands(op, "World", "Earth")
    assert status == "ok"
    # Replacement operands are bytes so the exact encoding is controlled.
    assert new_ops == [b"Hello Earth"]


def test_replace_in_operands_tj_bytes():
    op = PDFOperator("TJ", [[b"Hello World"]], 0, 15)
    new_ops, status = _replace_in_operands(op, "World", "Earth")
    assert status == "ok"
    val = new_ops[0][0]
    if isinstance(val, bytes):
        val = val.decode("latin-1", "ignore")
    assert val == "Hello Earth"


def test_replace_in_operands_tj_multisegment():
    # "World" split across two string segments -> reconstructed.
    op = PDFOperator("TJ", [["Wor", -40, "ld"]], 0, 20)
    new_ops, status = _replace_in_operands(op, "World", "Planet")
    assert status == "ok"
    assert new_ops == [[b"Planet"]]


def test_replace_in_operands_tj_multisegment_ambiguous():
    # The word appears twice -> refuse rather than guess.
    op = PDFOperator("TJ", [["World World"]], 0, 20)
    new_ops, status = _replace_in_operands(op, "World", "Planet")
    assert status == "ambiguous"


def test_replace_in_operands_tj_not_found():
    op = PDFOperator("TJ", [["Hello"]], 0, 10)
    new_ops, status = _replace_in_operands(op, "World", "Planet")
    assert status == "not_found"


# --------------------------------------------------------------------------- #
# Multi-word spans and whole-line editing
# --------------------------------------------------------------------------- #
def _word_hit(page, text):
    target = next(w for w in page.get_text("words") if w[4] == text)
    return find_text_at_point(page, _click_center(target))


def test_words_in_line_and_combine(session):
    page = session.doc[0]
    hit = _word_hit(page, "quick")
    line = words_in_line(page, hit)
    assert [h.text for h in line] == ["the", "quick", "brown", "fox"]
    combined = combine_hits(line)
    assert combined is not None
    assert combined.text == "the quick brown fox"
    # The combined box spans the whole line.
    assert combined.rect[0] <= line[0].rect[0]
    assert combined.rect[2] >= line[-1].rect[2]


def test_words_between_selects_span(session):
    page = session.doc[0]
    start = _word_hit(page, "quick")
    end = _word_hit(page, "brown")
    span = words_between(page, start, end)
    assert [h.text for h in span] == ["quick", "brown"]
    # Dragging in reverse selects the same span.
    assert words_between(page, end, start) == span


def test_replace_multi_word_span(session):
    page = session.doc[0]
    span = combine_hits(words_between(page, _word_hit(page, "quick"),
                                      _word_hit(page, "brown")))
    edit = ReplaceExistingText(
        page_num=0, old_word=span.text, new_word="slow red", word_rect=span.rect,
    )
    edit.apply(session.doc)
    text = session.doc[0].get_text()
    assert "slow red" in text
    assert "quick brown" not in text
    # Neighbouring words are untouched.
    assert "the" in text and "fox" in text


def test_replace_whole_line(session):
    page = session.doc[0]
    line = combine_hits(words_in_line(page, _word_hit(page, "quick")))
    edit = ReplaceExistingText(
        page_num=0, old_word=line.text, new_word="a new line",
        word_rect=line.rect,
    )
    edit.apply(session.doc)
    text = session.doc[0].get_text()
    assert "a new line" in text
    assert "the quick brown fox" not in text
    # The other line is a separate operator and stays intact.
    assert "jumps over the lazy dog" in text


def test_span_across_operators_is_refused(session):
    """A visual span that crosses text operators cannot be replaced safely."""
    page = session.doc[0]
    fox = _word_hit(page, "fox")
    edit = ReplaceExistingText(
        page_num=0, old_word="fox jumps over", new_word="x", word_rect=fox.rect,
    )
    with pytest.raises(EditError, match="Could not locate"):
        edit.apply(session.doc)


def test_delete_word(session):
    """An empty replacement removes the word without touching neighbours."""
    page = session.doc[0]
    hit = _word_hit(page, "fox")
    edit = ReplaceExistingText(
        page_num=0, old_word="fox", new_word="", word_rect=hit.rect,
    )
    edit.apply(session.doc)
    text = session.doc[0].get_text()
    assert "fox" not in text
    assert "the quick brown" in text
