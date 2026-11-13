"""Tests for the isolated PdfCraft-Python prototype.

Each test exercises one of the capabilities the feasibility study must show:
reading operators with source spans, lines and paragraphs, changed-row
detection, byte-preserving rewrites, font substitution, save/reopen, and
rendering.  Nothing here imports ``pdfar``: the prototype is deliberately
independent of the failing paragraph-editing implementation.
"""

from __future__ import annotations

import pymupdf as fitz
import pytest

from pdfar.pdfcraft import (
    TextSession,
    UnsupportedError,
    clear_block,
    read_page,
    render_page,
    replace_line,
    rewrite_block,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def contents(doc: fitz.Document) -> bytes:
    """The page's content stream(s), concatenated."""
    page = doc[0]
    key = doc.xref_get_key(page.xref, "Contents")
    if key[0] == "xref":
        return doc.xref_stream(int(key[1].split()[0])) or b""
    if key[0] == "array":
        numbers = [int(part.split()[0]) for part in key[1].strip("[]").split("R") if part.strip()]
        return b"".join(doc.xref_stream(number) or b"" for number in numbers)
    raise AssertionError(key)


def row_bytes(doc: fitz.Document, pm, row) -> list[bytes]:
    """The exact source bytes of every show operator in one row."""
    data = pm.streams[0]
    return [data[pm.ops[i].start:pm.ops[i].end] for index in row for i in pm.lines[index].ops]


def trace(doc: fitz.Document):
    return doc[0].get_texttrace()


def char_origin(doc: fitz.Document, needle: str):
    """Where ``needle`` starts, from the text trace (one span per text object)."""
    for span in trace(doc):
        text = "".join(chr(char[0]) for char in span["chars"])
        at = text.find(needle)
        if at >= 0:
            return span["chars"][at][2]
    raise AssertionError(f"{needle!r} is not in the page's text trace")


def diff_bbox(before: fitz.Pixmap, after: fitz.Pixmap):
    """Bounding box of differing pixels, or None."""
    assert before.width == after.width and before.height == after.height
    a, b = before.samples, after.samples
    n = before.n
    x0 = y0 = 10**9
    x1 = y1 = -1
    for y in range(before.height):
        row = y * before.stride
        for x in range(before.width):
            offset = row + x * n
            if a[offset:offset + n] != b[offset:offset + n]:
                x0 = min(x0, x)
                x1 = max(x1, x)
                y0 = min(y0, y)
                y1 = max(y1, y)
    return None if x1 < 0 else (x0, y0, x1 + 1, y1 + 1)


# ---------------------------------------------------------------------------
# 1. Operators with source positions
# ---------------------------------------------------------------------------


def test_operators_keep_their_source_spans(pdfs):
    doc = fitz.open(pdfs["simple"])
    pm = read_page(doc, 0)
    data = pm.streams[0]
    assert len(pm.ops) > 10
    for op in pm.ops:
        assert 0 <= op.start <= op.kw_start < op.end <= len(data)
        keyword = data[op.kw_start:op.end].split()[-1]
        assert keyword == op.op.encode("latin-1")
    shows = [op for op in pm.ops if op.op in ("Tj", "TJ")]
    assert shows and all(op.string(0) is not None or op.array(0) is not None for op in shows)
    # A show's span contains the exact source bytes it paints (hex or literal).
    first = shows[0]
    first_string = first.string(0) or first.array(0)[0]
    assert first_string.raw in data[first.start:first.end]
    doc.close()


# ---------------------------------------------------------------------------
# 2. Lines and paragraphs
# ---------------------------------------------------------------------------


def test_lines_and_paragraphs(pdfs):
    doc = fitz.open(pdfs["simple"])
    pm = read_page(doc, 0)
    assert len(pm.lines) == 4
    assert len(pm.blocks) == 1
    assert len(pm.blocks[0].rows) == 4
    assert pm.blocks[0].text.startswith("The quick brown fox")
    assert pm.blocks[0].text.endswith("through the meadow.")
    doc.close()


def test_two_paragraphs_in_one_bt_et(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    pm = read_page(doc, 0)
    assert len(pm.streams) == 1
    assert len(pm.lines) == 4
    assert len(pm.blocks) == 2
    assert [len(block.rows) for block in pm.blocks] == [2, 2]
    assert pm.blocks[0].text.startswith("Alpha paragraph")
    assert pm.blocks[1].text.startswith("Beta paragraph")
    doc.close()


def test_two_paragraphs_in_two_streams(pdfs):
    doc = fitz.open(pdfs["multi_stream"])
    pm = read_page(doc, 0)
    assert len(pm.streams) == 2
    assert len(pm.blocks) == 2
    assert [len(block.rows) for block in pm.blocks] == [3, 3]
    assert pm.blocks[0].lines[0] in range(len(pm.lines))
    doc.close()


def test_form_xobject_text_is_read(pdfs):
    doc = fitz.open(pdfs["form_xobject"])
    pm = read_page(doc, 0)
    assert any("form XObject" in line.text for line in pm.lines)
    form_line = next(line for line in pm.lines if "form XObject" in line.text)
    assert form_line.stream != 0
    doc.close()


def test_list_items_are_separate_paragraphs(pdfs):
    doc = fitz.open(pdfs["list_items"])
    pm = read_page(doc, 0)
    assert len(pm.blocks) == 5
    assert [block.rows[0][0] for block in pm.blocks] == [0, 1, 2, 3, 4]
    doc.close()


# ---------------------------------------------------------------------------
# 3. Changed rows and untouched operators
# ---------------------------------------------------------------------------


def test_changed_row_is_one_operator(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    pm = read_page(doc, 0)
    assert pm.lines[0].ops == [4]  # one TJ shows the whole row
    doc.close()


def test_replace_line_leaves_the_sibling_byte_for_byte(pdfs):
    doc = fitz.open(pdfs["tj_kerning"])
    pm = read_page(doc, 0)
    before = row_bytes(doc, pm, [1])  # the untouched second line
    replace_line(doc, 0, 0, "Kerning is fun indeed")
    after = contents(doc)
    assert all(chunk in after for chunk in before)
    assert b"(Second line stays exactly here) Tj" in after
    assert b"Kerning is fun indeed" in after
    doc.close()


def test_replace_line_keeps_its_position(pdfs, tmp_path):
    doc = fitz.open(pdfs["shared_btet"])
    origin = char_origin(doc, "Alpha")
    replace_line(doc, 0, 0, "Short replacement")
    moved = char_origin(doc, "Short replacement")
    assert abs(moved[0] - origin[0]) < 0.5
    assert abs(moved[1] - origin[1]) < 0.5
    path = tmp_path / "position.pdf"
    doc.save(path)
    doc.close()
    reopened = fitz.open(path)
    assert "Short replacement" in reopened[0].get_text()
    assert "Beta paragraph" in reopened[0].get_text()
    reopened.close()


# ---------------------------------------------------------------------------
# 4. Paragraph reflow
# ---------------------------------------------------------------------------


def test_rewrite_block_wraps_and_saves(pdfs, tmp_path):
    doc = fitz.open(pdfs["simple"])
    pm = read_page(doc, 0)
    text = pm.blocks[0].text + " Plus a few more words appended at the end."
    edit = rewrite_block(doc, 0, 0, text)
    assert edit.substituted is None
    path = tmp_path / "simple.pdf"
    doc.save(path)
    doc.close()

    reopened = fitz.open(path)
    got = reopened[0].get_text()
    assert "Plus a few more words appended at the end." in got
    assert got.startswith("The quick brown fox")
    assert len(read_page(reopened, 0).blocks[0].rows) >= 5
    pix = render_page(reopened, 0)
    assert pix.width > 0 and len(set(pix.samples)) > 1
    reopened.close()


def test_rewrite_block_keeps_the_other_paragraph_bytes(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    pm = read_page(doc, 0)
    beta = row_bytes(doc, pm, [2, 3])
    rewrite_block(doc, 0, 0, pm.blocks[0].text + " An extra sentence that forces the row to wrap.")
    after = contents(doc)
    assert all(chunk in after for chunk in beta)
    doc.close()


def test_rewrite_multi_stream_paragraph(pdfs):
    doc = fitz.open(pdfs["multi_stream"])
    pm = read_page(doc, 0)
    first = pm.blocks[0].text
    second = pm.blocks[1].text
    rewrite_block(doc, 0, 0, first + " Tail added to the first paragraph.")
    reopened_model = read_page(doc, 0)
    assert reopened_model.blocks[1].text == second
    doc.close()


def test_rewrite_in_the_second_stream(pdfs):
    """A block in a later piece must splice at its own offsets, not the joined ones."""
    doc = fitz.open(pdfs["multi_stream"])
    pm = read_page(doc, 0)
    second = pm.blocks[1].text
    rewrite_block(doc, 0, 1, second + " Tail added to the second paragraph.")
    reopened_model = read_page(doc, 0)
    assert "Tail added to the second paragraph." in reopened_model.blocks[1].text
    assert reopened_model.blocks[0].text == pm.blocks[0].text
    doc.close()


def test_rewrite_a_row_per_bt_paragraph(pdfs):
    doc = fitz.open(pdfs["row_per_bt"])
    pm = read_page(doc, 0)
    assert len(pm.blocks) == 2
    assert len(pm.blocks[0].rows) == 2
    other = row_bytes(doc, pm, [2])
    rewrite_block(doc, 0, 0, pm.blocks[0].text + " An appended sentence that wraps.")
    after = contents(doc)
    assert all(chunk in after for chunk in other)
    assert "An appended sentence that wraps." in doc[0].get_text()
    doc.close()


def test_rewrite_refuses_a_form_xobject(pdfs):
    doc = fitz.open(pdfs["form_xobject"])
    pm = read_page(doc, 0)
    index = next(i for i, block in enumerate(pm.blocks) if "form XObject" in block.text)
    with pytest.raises(UnsupportedError):
        rewrite_block(doc, 0, index, "new text")
    doc.close()


def test_replace_line_keeps_rotation(pdfs):
    doc = fitz.open(pdfs["rotated"])
    pm = read_page(doc, 0)
    assert abs(pm.lines[0].rotation - 90.0) < 0.1
    before = next(s["dir"] for s in trace(doc) if "Rotated line one here" in "".join(chr(c[0]) for c in s["chars"]))
    replace_line(doc, 0, 0, "Rotated line one edited")
    span = next(s for s in trace(doc) if "Rotated line one edited" in "".join(chr(c[0]) for c in s["chars"]))
    assert abs(span["dir"][0] - before[0]) < 0.01 and abs(span["dir"][1] - before[1]) < 0.01
    doc.close()


def test_reflow_of_rotated_text_uses_the_baseline_width(pdfs):
    doc = fitz.open(pdfs["rotated"])
    pm = read_page(doc, 0)
    block = pm.blocks[0]
    rewrite_block(doc, 0, 0, block.text + " appended words that have to wrap")
    after = read_page(doc, 0)
    paragraph = after.blocks[0]
    # The upright bounding box of turned text is ~12 pt wide; wrapping to it
    # would put every word on its own row.
    assert 2 <= len(paragraph.rows) <= 4
    assert abs(paragraph.rotation - 90.0) < 0.1
    doc.close()


# ---------------------------------------------------------------------------
# 5. Fonts
# ---------------------------------------------------------------------------


def test_substitute_font_is_added_and_saved(pdfs, tmp_path):
    doc = fitz.open(pdfs["embedded_subset"])
    new_text = "A completely new sentence in a substitute font placed where the paragraph started."
    edit = rewrite_block(doc, 0, 0, new_text)
    assert edit.substituted == "Helvetica"
    path = tmp_path / "subset.pdf"
    doc.save(path)
    doc.close()

    reopened = fitz.open(path)
    names = [entry[4] for entry in reopened.get_page_fonts(0)]
    assert "PCEdHelvetica" in names
    assert "A completely new sentence" in reopened[0].get_text()
    reopened.close()


# ---------------------------------------------------------------------------
# 6. Preview
# ---------------------------------------------------------------------------


def test_clear_block_hides_only_that_paragraph(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    beta_origin = char_origin(doc, "Beta")
    clear_block(doc, 0, 0)
    text = doc[0].get_text()
    assert "Alpha paragraph" not in text
    assert "Beta paragraph" in text
    moved = char_origin(doc, "Beta")
    assert abs(moved[0] - beta_origin[0]) < 0.01
    assert abs(moved[1] - beta_origin[1]) < 0.01
    doc.close()


# ---------------------------------------------------------------------------
# 7. Session
# ---------------------------------------------------------------------------


def test_session_refuses_a_stale_document(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    session = TextSession(doc, 0, 0)
    assert session.is_current(doc)
    layout = session.layout(session.source.text + " draft")
    assert layout.rows
    replace_line(doc, 0, 2, "Beta edited elsewhere")
    assert not session.is_current(doc)
    with pytest.raises(UnsupportedError):
        session.commit(doc, "something else")
    doc.close()


def test_session_commit_matches_layout(pdfs):
    doc = fitz.open(pdfs["simple"])
    session = TextSession(doc, 0, 0)
    text = session.source.text + " Draft tail."
    layout = session.layout(text)
    session.commit(doc, text)
    assert "Draft tail." in doc[0].get_text()
    assert layout.rows[-1].startswith("Draft tail")
    doc.close()


# ---------------------------------------------------------------------------
# 8. Rendering: untouched pixels stay untouched
# ---------------------------------------------------------------------------


def test_replace_line_changes_only_its_own_area(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    before = render_page(doc, 0, dpi=72)
    pm = read_page(doc, 0)
    rect = pm.lines[0].rect
    replace_line(doc, 0, 0, "Short replacement")
    after = render_page(doc, 0, dpi=72)
    box = diff_bbox(before, after)
    assert box is not None
    # 72 dpi: one pixel per point, with the pixmap's y axis pointing down.
    height = doc[0].rect.height
    x0, y0, x1, y1 = box
    assert x0 >= rect[0] - 2 and x1 <= rect[2] + 2
    assert y0 >= height - rect[3] - 2 and y1 <= height - rect[1] + 2
    doc.close()
