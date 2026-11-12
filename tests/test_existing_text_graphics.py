"""Phase 3: graphics-state replay and repeated-word disambiguation.

The engine locates a clicked word by replaying the page's content stream.  A
correct replay must honour nested ``q``/``Q``, composed ``cm`` matrices,
rotated/scaled text and the text state (``Tc``/``Tw``/``Tz``/``TL``), and it
must pick the *occurrence* the user actually clicked when the same word is
drawn several times.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fitz
import pytest

from pdfar.edits import EditError
from pdfar.content_stream import ContentStreamParser
from pdfar.text_edit_existing import (
    ReplaceExistingText,
    _iter_page_runs,
    _rect_distance,
    find_text_at_point,
    locate_operator_for_word,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _pdf_from_stream(tmp_path, name: str, stream: bytes,
                     font: str = "helv", width: int = 600,
                     height: int = 800) -> str:
    path = str(tmp_path / name)
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_text((5, 5), ".")
    page.insert_font(fontname=font)
    contents = page.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(xref, stream.replace(b"/F1", b"/" + font.encode()))
    doc.save(path)
    doc.close()
    return path


def _word(page, text: str, n: int = 0):
    return [w for w in page.get_text("words") if w[4] == text][n]


def _click(page, text: str, n: int = 0):
    w = _word(page, text, n)
    return find_text_at_point(page, ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0))


def _origin_error(page, text: str) -> float:
    """Smallest distance between a run origin and *text*'s rendered box."""
    rect = tuple(_word(page, text)[:4])
    best = None
    for run in _iter_page_runs(page):
        if not run.text.startswith(text):
            continue
        d = _rect_distance(run.point_at(0), rect)
        best = d if best is None else min(best, d)
    assert best is not None, f"no run starts with {text!r}"
    return best


def _span(page, text: str):
    """The text span whose text contains *text* (spans survive char spacing)."""
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if text in span.get("text", ""):
                    return span
    raise AssertionError(f"no span contains {text!r}")


def _run(page, text: str):
    runs = [r for r in _iter_page_runs(page) if text in r.text]
    assert runs, f"no run contains {text!r}"
    return runs[0]


def _shown_text(data: bytes) -> str:
    """Decode the text shown by a stream's simple Tj operators."""
    out = []
    for op in ContentStreamParser.parse(data):
        if op.operator == "Tj" and op.operands:
            value = op.operands[0]
            out.append(value.decode("latin-1") if isinstance(value, bytes)
                       else str(value))
    return "".join(out)


def _edit(page, old: str, new: str, n: int = 0) -> None:
    hit = _click(page, old, n)
    assert hit is not None
    ReplaceExistingText(
        page_num=page.number, old_word=old, new_word=new,
        word_rect=hit.rect).apply(page.parent)


# --------------------------------------------------------------------------- #
# Priority A — graphics state
# --------------------------------------------------------------------------- #
def test_nested_q_q_restores_graphics_state(tmp_path):
    stream = (
        b"q\n"
        b"1 0 0 1 120 100 cm\n"
        b"q\n"
        b"2 0 0 2 0 0 cm\n"
        b"BT /F1 10 Tf 1 0 0 1 0 200 Tm (scaled) Tj ET\n"
        b"Q\n"
        b"BT /F1 10 Tf 1 0 0 1 0 400 Tm (unscaled) Tj ET\n"
        b"Q\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "nested.pdf", stream))
    try:
        page = doc[0]
        assert _origin_error(page, "scaled") < 2.0
        assert _origin_error(page, "unscaled") < 2.0
        # The inner scale must not leak: the second word is 10pt, not 20pt.
        assert _word(page, "unscaled")[2] - _word(page, "unscaled")[0] < \
            _word(page, "scaled")[2] - _word(page, "scaled")[0]
    finally:
        doc.close()


def test_nested_cm_matrices_compose(tmp_path):
    stream = (
        b"q\n"
        b"1 0 0 1 50 100 cm\n"
        b"q\n"
        b"1.5 0 0 1.5 10 20 cm\n"
        b"BT /F1 10 Tf 1 0 0 1 4 6 Tm (composed) Tj ET\n"
        b"Q\n"
        b"Q\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "compose.pdf", stream))
    try:
        page = doc[0]
        # Content position: 50 + 10 + 1.5*4 = 66, 100 + 20 + 1.5*6 = 129.
        # point_at() reports top-left page space, so y is 800 - 129.
        runs = [r for r in _iter_page_runs(page) if r.text.startswith("composed")]
        assert len(runs) == 1
        x, y = runs[0].point_at(0)
        assert x == pytest.approx(66.0, abs=0.01)
        assert y == pytest.approx(671.0, abs=0.01)
        assert _origin_error(page, "composed") < 2.0
    finally:
        doc.close()


def test_rotated_text_matrix(tmp_path):
    stream = (
        b"q\n"
        b"BT /F1 12 Tf 0 1 -1 0 200 100 Tm (rotated) Tj ET\n"
        b"Q\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rot_tm.pdf", stream))
    try:
        page = doc[0]
        assert _origin_error(page, "rotated") < 2.0
        before = _run(page, "rotated").point_at(0)
        _edit(page, "rotated", "turned")
        assert "turned" in page.get_text()
        assert "rotated" not in page.get_text()
        # The replacement starts at the same rotated origin.
        after = _run(page, "turned").point_at(0)
        assert after[0] == pytest.approx(before[0], abs=0.5)
        assert after[1] == pytest.approx(before[1], abs=0.5)
        # Baseline is at page y=700 and the text runs upward from it.
        assert _word(page, "turned")[1] < 700.0
    finally:
        doc.close()


def test_rotated_cm_matrix(tmp_path):
    stream = (
        b"q\n"
        b"0 1 -1 0 300 100 cm\n"
        b"BT /F1 12 Tf 1 0 0 1 0 0 Tm (turned) Tj ET\n"
        b"Q\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rot_cm.pdf", stream))
    try:
        page = doc[0]
        assert _origin_error(page, "turned") < 2.0
        _edit(page, "turned", "spun")
        assert "spun" in page.get_text()
    finally:
        doc.close()


def test_text_state_is_saved_and_restored(tmp_path):
    # Tc/Tw/Tz are set inside q ... Q; the second word must use the defaults.
    stream = (
        b"q\n"
        b"1 0 0 1 0 0 Tc 2 Tw 130 Tz\n"
        b"BT /F1 10 Tf 1 0 0 1 50 500 Tm (spaced) Tj ET\n"
        b"Q\n"
        b"BT /F1 10 Tf 1 0 0 1 50 300 Tm (plain) Tj ET\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "state.pdf", stream))
    try:
        page = doc[0]
        # Spacing may split the word for hit testing, so compare spans.
        spaced = _span(page, "spaced")
        plain = _span(page, "plain")
        assert _rect_distance(_run(page, "spaced").point_at(0),
                              tuple(spaced["bbox"])) < 2.0
        assert _rect_distance(_run(page, "plain").point_at(0),
                              tuple(plain["bbox"])) < 2.0
        spaced_w = spaced["bbox"][2] - spaced["bbox"][0]
        plain_w = plain["bbox"][2] - plain["bbox"][0]
        assert spaced_w > plain_w * 1.3  # Tc/Tz applied inside the q block
        # And the spacing did not leak past Q: the plain word is narrow.
        assert plain_w < 40.0
    finally:
        doc.close()


def test_bt_resets_text_matrix(tmp_path):
    stream = (
        b"q\n"
        b"BT /F1 10 Tf 1 0 0 1 100 600 Tm (first) Tj ET\n"
        b"BT /F1 10 Tf 1 0 0 1 100 400 Tm (second) Tj ET\n"
        b"Q\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "bt.pdf", stream))
    try:
        page = doc[0]
        assert _origin_error(page, "first") < 2.0
        assert _origin_error(page, "second") < 2.0
    finally:
        doc.close()


def test_edit_keeps_transformed_position(tmp_path):
    stream = (
        b"q\n"
        b"1 0 0 1 120 300 cm\n"
        b"BT /F1 12 Tf 1 0 0 1 0 0 Tm (anchor) Tj ET\n"
        b"Q\n"
    )
    path = _pdf_from_stream(tmp_path, "keep.pdf", stream)
    doc = fitz.open(path)
    try:
        page = doc[0]
        before = _word(page, "anchor")[:2]
        _edit(page, "anchor", "marker")
        after = _word(page, "marker")[:2]
        assert after[0] == pytest.approx(before[0], abs=1.0)
        assert after[1] == pytest.approx(before[1], abs=1.0)
        # The graphics operator itself is untouched.
        xref, data = [(x, doc.xref_stream(x)) for x in page.get_contents()][0]
        assert b"1 0 0 1 120 300 cm" in data
    finally:
        doc.close()


def test_cropbox_offset_is_accounted_for(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((5, 5), ".")
    page.insert_font(fontname="helv")
    page.set_cropbox(fitz.Rect(50, 40, 250, 160))
    contents = page.get_contents()
    xref = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(
        xref,
        b"BT /helv 10 Tf 1 0 0 1 60 300 Tm (cropped) Tj ET")
    path = str(tmp_path / "crop.pdf")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        page = doc[0]
        assert _origin_error(page, "cropped") < 2.0
        _edit(page, "cropped", "visible")
        assert "visible" in page.get_text()
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Priority B — repeated words
# --------------------------------------------------------------------------- #
def test_repeated_word_in_one_operator_edits_only_clicked(tmp_path):
    stream = (
        b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm\n"
        b"(the cat and the dog) Tj\nET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rep_tj.pdf", stream))
    try:
        page = doc[0]
        _edit(page, "the", "one", n=1)  # the second "the"
        text = page.get_text().replace("\n", " ")
        assert "the cat and one dog" in text
        assert "one cat" not in text
        xref, data = [(x, doc.xref_stream(x)) for x in page.get_contents()][0]
        assert _shown_text(data) == "the cat and one dog"
    finally:
        doc.close()


def test_repeated_word_across_two_operators(tmp_path):
    stream = (
        b"q\nBT /F1 14 Tf\n"
        b"1 0 0 1 60 600 Tm (same word here) Tj\n"
        b"1 0 0 1 60 400 Tm (same word there) Tj\n"
        b"ET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rep_ops.pdf", stream))
    try:
        page = doc[0]
        _edit(page, "word", "term", n=1)  # second line
        lines = [ln for ln in page.get_text().splitlines() if ln.strip()]
        assert "same term there" in lines
        assert "same word here" in lines
    finally:
        doc.close()


def test_repeated_word_split_across_tj_segments(tmp_path):
    stream = (
        b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm\n"
        b"[(dup) -20 (licate ) 30 (dup) -20 (licate)] TJ\nET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rep_tj.pdf", stream))
    try:
        page = doc[0]
        _edit(page, "duplicate", "copy", n=1)
        assert "duplicate copy" in page.get_text().replace("\n", " ")
    finally:
        doc.close()


def test_repeated_word_keeps_surrounding_kerning(tmp_path):
    stream = (
        b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm\n"
        b"[(A ) -120 (repeat ) -80 (B ) -200 (repeat ) -60 (C)] TJ\nET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rep_kern.pdf", stream))
    try:
        page = doc[0]
        _edit(page, "repeat", "twice", n=1)
        xref, data = [(x, doc.xref_stream(x)) for x in page.get_contents()][0]
        assert data.count(b"-120") == 1
        assert data.count(b"-80") == 1
        assert data.count(b"-200") == 1
        assert data.count(b"-60") == 1
        tj = [o for o in ContentStreamParser.parse(data) if o.operator == "TJ"][0]
        shown = "".join(
            v.decode("latin-1") if isinstance(v, bytes) else str(v)
            for v in tj.operands[0] if not isinstance(v, (int, float)))
        assert shown == "A repeat B twice C"
        assert page.get_text().count("repeat") == 1
    finally:
        doc.close()


def test_locate_reports_occurrence_index(tmp_path):
    stream = (
        b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm\n"
        b"(alpha beta alpha gamma) Tj\nET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "occ.pdf", stream))
    try:
        page = doc[0]
        hit = _click(page, "alpha", 1)
        loc = locate_operator_for_word(page, "alpha", hit.rect)
        assert loc is not None
        assert loc.occurrence == 1
        hit0 = _click(page, "alpha", 0)
        loc0 = locate_operator_for_word(page, "alpha", hit0.rect)
        assert loc0 is not None
        assert loc0.occurrence == 0
    finally:
        doc.close()


def test_word_moved_far_away_is_not_matched(tmp_path):
    # A word drawn at (60,600) must not be located for a box at (500,100).
    stream = (
        b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm (unique) Tj ET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "far.pdf", stream))
    try:
        page = doc[0]
        loc = locate_operator_for_word(page, "unique", (500.0, 90.0, 560.0, 110.0))
        assert loc is None
    finally:
        doc.close()


def test_edit_refuses_when_word_is_not_on_page(tmp_path):
    stream = b"q\nBT /F1 14 Tf 1 0 0 1 60 600 Tm (hello) Tj ET\nQ\n"
    doc = fitz.open(_pdf_from_stream(tmp_path, "missing.pdf", stream))
    try:
        page = doc[0]
        hit = _click(page, "hello")
        with pytest.raises(EditError):
            ReplaceExistingText(
                page_num=0, old_word="absent", new_word="x",
                word_rect=hit.rect).apply(doc)
    finally:
        doc.close()


def test_text_rise_offsets_origin(tmp_path):
    stream = (
        b"q\nBT /F1 12 Tf 1 0 0 1 60 600 Tm\n"
        b"8 Ts (raised) Tj\n"
        b"0 Ts 0 -40 Td (flat) Tj\n"
        b"ET\nQ\n"
    )
    doc = fitz.open(_pdf_from_stream(tmp_path, "rise.pdf", stream))
    try:
        page = doc[0]
        raised = _run(page, "raised").point_at(0)
        flat = _run(page, "flat").point_at(0)
        # Tm (60,600) is page y=200; 8 Ts lifts the baseline to 192, and the
        # following Td 0 -40 moves the next line to page y=240.
        assert raised[0] == pytest.approx(60.0, abs=0.5)
        assert raised[1] == pytest.approx(192.0, abs=0.5)
        assert flat[0] == pytest.approx(60.0, abs=0.5)
        assert flat[1] == pytest.approx(240.0, abs=0.5)
    finally:
        doc.close()
