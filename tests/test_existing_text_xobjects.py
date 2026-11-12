"""Phase 3: text inside Form XObjects.

Text drawn by a Form XObject lives in a different stream with its own
``/Resources`` and a ``/Matrix`` that positions it on the page.  Editing it
must resolve the right font, map coordinates through the invocation matrix,
and never silently modify a *shared* object: a copy-on-write clone is created
for the page being edited, or the edit is refused.
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
    ReplaceExistingText,
    _font_infos_for,
    _iter_page_runs,
    find_text_at_point,
)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _new_doc(pages: int = 1, width: int = 600, height: int = 800):
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((8, 20), ".")
        page.insert_font(fontname="helv")
    return doc


def _font_xref(doc, name: str = "helv") -> int:
    for page_no in range(len(doc)):
        for f in doc.get_page_fonts(page_no):
            if f[4] == name:
                return int(f[0])
    raise AssertionError(f"font {name!r} not found")


def _resources_xref(doc, page_no: int) -> int:
    page = doc[page_no]
    kind, value = doc.xref_get_key(page.xref, "Resources")
    assert kind == "xref"
    return int(value.split()[0])


def _add_xobject(doc, page_no: int, stream: bytes, font_xref: int,
                 name: str = "Fm1", matrix: str | None = None,
                 resources: str | None = None, invocations: int = 1) -> int:
    """Create a Form XObject and invoke it from page *page_no*."""
    page = doc[page_no]
    xref = doc.get_new_xref()
    src = "<</Type/XObject/Subtype/Form/BBox[0 0 400 200]"
    if matrix:
        src += f"/Matrix[{matrix}]"
    if resources is None:
        resources = "<</Font<</helv %d 0 R>>>>" % font_xref
    src += f"/Resources{resources}>>"
    doc.update_object(xref, src)
    doc.update_stream(xref, stream)

    res_xref = _resources_xref(doc, page_no)
    doc.xref_set_key(res_xref, f"XObject/{name}", f"{xref} 0 R")
    contents = page.get_contents()
    cx = contents[0] if isinstance(contents, list) else contents
    extra = b"".join(
        b"q 1 0 0 1 100 %d cm /%s Do Q\n" % (400 - 200 * i, name.encode())
        for i in range(invocations))
    doc.update_stream(cx, doc.xref_stream(cx) + b"\n" + extra)
    return xref


def _click(page, word: str):
    w = next(w for w in page.get_text("words") if w[4] == word)
    return find_text_at_point(page, ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0))


def _edit(page, old: str, new: str) -> None:
    hit = _click(page, old)
    assert hit is not None
    ReplaceExistingText(
        page_num=page.number, old_word=old, new_word=new,
        word_rect=hit.rect).apply(page.parent)


# --------------------------------------------------------------------------- #
# Basic support
# --------------------------------------------------------------------------- #
def test_xobject_text_is_located_and_edited():
    doc = _new_doc()
    font = _font_xref(doc)
    xref = _add_xobject(
        doc, 0,
        b"q\nBT /helv 14 Tf 1 0 0 1 40 60 Tm (inside xobject) Tj ET\nQ\n",
        font)
    try:
        page = doc[0]
        assert "inside xobject" in page.get_text()
        runs = [r for r in _iter_page_runs(page) if r.source.in_xobject]
        assert len(runs) == 1
        assert runs[0].source.xref == xref
        assert runs[0].source.resource_name == "Fm1"
        assert runs[0].source.depth == 1

        _edit(page, "xobject", "object")
        assert "inside object" in page.get_text()
        assert "xobject" not in page.get_text()
        # The page's own text is untouched.
        assert page.get_text().startswith(".")
    finally:
        doc.close()


def test_xobject_matrix_positions_text():
    doc = _new_doc()
    font = _font_xref(doc)
    _add_xobject(
        doc, 0,
        b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (scaled text) Tj ET",
        font, matrix="2 0 0 2 0 0")
    try:
        page = doc[0]
        runs = [r for r in _iter_page_runs(page) if r.source.in_xobject]
        assert len(runs) == 1
        x, y = runs[0].point_at(0)
        # (40,60) * 2 + (100,400) = (180,520) in PDF space -> y=280 from top.
        assert x == pytest.approx(180.0, abs=0.01)
        assert y == pytest.approx(280.0, abs=0.01)
        word = next(w for w in page.get_text("words") if w[4] == "scaled")
        assert word[0] == pytest.approx(180.0, abs=2.0)
        # The word box starts at the glyph top; the baseline (the run origin)
        # sits inside it.
        assert word[1] < 280.0 <= word[3] + 0.5

        before = (word[0], word[1])
        _edit(page, "scaled", "bigger")
        after = next(w for w in page.get_text("words") if w[4] == "bigger")
        assert after[0] == pytest.approx(before[0], abs=1.0)
        assert after[1] == pytest.approx(before[1], abs=1.0)
    finally:
        doc.close()


def test_xobject_font_resources_are_scoped():
    doc = _new_doc()
    helv = _font_xref(doc, "helv")

    # A second font (Times) that exists only inside the XObject, using the
    # same resource name the page uses for Helvetica.
    scratch = doc.new_page(width=100, height=100)
    scratch.insert_text((5, 5), "t", fontname="tiro")
    times = next(f[0] for f in doc.get_page_fonts(1) if f[3].startswith("Times"))
    doc.delete_page(1)

    xref = _add_xobject(
        doc, 0, b"BT /F1 12 Tf 1 0 0 1 40 60 Tm (serif body) Tj ET",
        times, resources="<</Font<</F1 %d 0 R>>>>" % times)
    try:
        page = doc[0]
        # The page's own /F1 (Helvetica) must not leak into the XObject.
        doc.xref_set_key(_resources_xref(doc, 0), "Font/F1",
                         f"{helv} 0 R")
        page_infos = _font_infos_for(page, page.xref)
        xobj_infos = _font_infos_for(page, xref)
        assert page_infos["F1"].basefont == "Helvetica"
        assert xobj_infos["F1"].basefont == "Times-Roman"

        _edit(page, "serif", "roman")
        assert "roman body" in page.get_text()
    finally:
        doc.close()


def test_xobject_edit_preserves_other_operators():
    doc = _new_doc()
    font = _font_xref(doc)
    stream = (
        b"0 0 1 rg 10 10 100 20 re f\n"
        b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (keep me) Tj ET\n"
        b"0.5 0 0 RG 2 w 10 100 m 200 100 l S\n"
    )
    xref = _add_xobject(doc, 0, stream, font)
    try:
        page = doc[0]
        _edit(page, "keep", "hold")
        data = doc.xref_stream(xref)
        assert b"0 0 1 rg" in data
        assert b"10 10 100 20 re f" in data
        assert b"0.5 0 0 RG 2 w" in data
        assert b"10 100 m 200 100 l S" in data
        assert "hold me" in page.get_text()
    finally:
        doc.close()


def test_xobject_edit_survives_save_and_reopen(tmp_path):
    doc = _new_doc()
    font = _font_xref(doc)
    _add_xobject(doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (before) Tj ET",
                 font)
    page = doc[0]
    _edit(page, "before", "after")
    out = str(tmp_path / "xobj_saved.pdf")
    doc.save(out, garbage=3, deflate=True)
    doc.close()

    reopened = fitz.open(out)
    try:
        assert "after" in reopened[0].get_text()
        assert "before" not in reopened[0].get_text()
        assert reopened[0].get_xobjects()
    finally:
        reopened.close()


# --------------------------------------------------------------------------- #
# Shared objects
# --------------------------------------------------------------------------- #
def test_shared_xobject_across_pages_is_copied_on_write():
    doc = _new_doc(pages=2)
    font = _font_xref(doc)
    xref = _add_xobject(
        doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (shared text) Tj ET", font)
    # Point page 2 at the same XObject.
    doc.xref_set_key(_resources_xref(doc, 1), "XObject/Fm1",
                     f"{xref} 0 R")
    page2 = doc[1]
    contents = page2.get_contents()
    cx = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(cx, doc.xref_stream(cx) + b"\nq 1 0 0 1 100 400 cm /Fm1 Do Q\n")

    try:
        page1 = doc[0]
        assert "shared text" in page1.get_text()
        assert "shared text" in doc[1].get_text()

        _edit(page1, "shared", "private")
        assert "private text" in doc[0].get_text()
        # The other page keeps the original content.
        assert "shared text" in doc[1].get_text()
        assert "private" not in doc[1].get_text()
        # The original object itself was not rewritten.
        assert b"(shared text)" in doc.xref_stream(xref)
        # Page 1 now points at a different XObject.
        assert doc[0].get_xobjects()[0][0] != xref
        assert doc[1].get_xobjects()[0][0] == xref
    finally:
        doc.close()


def test_xobject_invoked_twice_on_one_page_is_refused():
    doc = _new_doc()
    font = _font_xref(doc)
    _add_xobject(
        doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (twice drawn) Tj ET",
        font, invocations=2)
    try:
        page = doc[0]
        assert page.get_text().count("twice") == 2
        hit = _click(page, "twice")
        with pytest.raises(EditError, match="used 2 times"):
            ReplaceExistingText(
                page_num=0, old_word="twice", new_word="once",
                word_rect=hit.rect).apply(doc)
        # Nothing changed.
        assert page.get_text().count("twice") == 2
        assert "once" not in page.get_text()
    finally:
        doc.close()


def test_shared_resources_dictionary_is_split():
    doc = _new_doc(pages=2)
    font = _font_xref(doc)
    xref = _add_xobject(
        doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (shared res) Tj ET", font)
    shared_res = _resources_xref(doc, 0)
    # Page 2 shares page 1's resource dictionary (common in generated PDFs).
    doc.xref_set_key(doc[1].xref, "Resources", f"{shared_res} 0 R")
    page2 = doc[1]
    contents = page2.get_contents()
    cx = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(cx, doc.xref_stream(cx) + b"\nq 1 0 0 1 100 400 cm /Fm1 Do Q\n")
    assert "shared res" in doc[1].get_text()

    try:
        _edit(doc[0], "shared", "own")
        assert "own res" in doc[0].get_text()
        assert "shared res" in doc[1].get_text()
        assert _resources_xref(doc, 0) != shared_res
        assert _resources_xref(doc, 1) == shared_res
    finally:
        doc.close()


def test_inline_page_resources_are_supported():
    """Some producers write /Resources as an inline dict on the page object."""
    doc = _new_doc()
    font = _font_xref(doc)
    xref = _add_xobject(
        doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (inline res) Tj ET", font)
    page = doc[0]
    doc.xref_set_key(
        page.xref, "Resources",
        "<</Font<</helv %d 0 R>>/XObject<</Fm1 %d 0 R>>>>" % (font, xref))
    try:
        page = doc[0]
        assert "inline res" in page.get_text()
        _edit(page, "inline", "private")
        assert "private res" in doc[0].get_text()
        assert doc.xref_get_key(doc[0].xref, "Resources")[0] == "dict"
    finally:
        doc.close()


def test_indirect_xobject_dictionary_is_supported():
    """A /XObject sub-dictionary stored as its own indirect object."""
    doc = _new_doc()
    font = _font_xref(doc)
    xref = _add_xobject(
        doc, 0, b"BT /helv 12 Tf 1 0 0 1 40 60 Tm (indirect xdict) Tj ET",
        font)
    page = doc[0]
    xdict = doc.get_new_xref()
    doc.update_object(xdict, "<</Fm1 %d 0 R>>" % xref)
    res = doc.get_new_xref()
    doc.update_object(res, "<</Font<</helv %d 0 R>>/XObject %d 0 R>>"
                      % (font, xdict))
    doc.xref_set_key(page.xref, "Resources", "%d 0 R" % res)
    try:
        page = doc[0]
        assert "indirect xdict" in page.get_text()
        _edit(page, "indirect", "copied")
        assert "copied xdict" in doc[0].get_text()
        # The shared /XObject dictionary was not edited in place.
        assert doc.xref_object(xdict).count(str(xref)) == 1
    finally:
        doc.close()


def test_nested_xobject_is_refused():
    doc = _new_doc()
    font = _font_xref(doc)

    inner = doc.get_new_xref()
    doc.update_object(
        inner,
        "<</Type/XObject/Subtype/Form/BBox[0 0 400 200]"
        "/Resources<</Font<</helv %d 0 R>>>>>>" % font)
    doc.update_stream(
        inner, b"BT /helv 12 Tf 1 0 0 1 10 10 Tm (nested word) Tj ET")

    outer = doc.get_new_xref()
    doc.update_object(
        outer,
        "<</Type/XObject/Subtype/Form/BBox[0 0 400 200]"
        "/Resources<</XObject<</In %d 0 R>>>>>>" % inner)
    doc.update_stream(outer, b"/In Do")

    page = doc[0]
    doc.xref_set_key(_resources_xref(doc, 0), "XObject/Out", f"{outer} 0 R")
    contents = page.get_contents()
    cx = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(cx, doc.xref_stream(cx) + b"\nq 1 0 0 1 100 400 cm /Out Do Q\n")

    try:
        page = doc[0]
        assert "nested word" in page.get_text()
        depth = [r.source.depth for r in _iter_page_runs(page)
                 if r.source.in_xobject]
        assert depth == [2]
        hit = _click(page, "nested")
        with pytest.raises(EditError, match="nested"):
            ReplaceExistingText(
                page_num=0, old_word="nested", new_word="inner",
                word_rect=hit.rect).apply(doc)
        assert "nested word" in page.get_text()
    finally:
        doc.close()


def test_xobject_cycle_does_not_hang():
    # An XObject that invokes itself must be detected, not recursed forever.
    doc = _new_doc()
    font = _font_xref(doc)
    xref = doc.get_new_xref()
    doc.update_object(
        xref,
        "<</Type/XObject/Subtype/Form/BBox[0 0 400 200]"
        "/Resources<</Font<</helv %d 0 R>>/XObject<</Self %d 0 R>>>>>>"
        % (font, xref))
    doc.update_stream(
        xref, b"BT /helv 12 Tf 1 0 0 1 10 10 Tm (loop) Tj ET /Self Do")
    page = doc[0]
    doc.xref_set_key(_resources_xref(doc, 0), "XObject/Fm1", f"{xref} 0 R")
    contents = page.get_contents()
    cx = contents[0] if isinstance(contents, list) else contents
    doc.update_stream(cx, doc.xref_stream(cx) + b"\nq 1 0 0 1 100 400 cm /Fm1 Do Q\n")
    try:
        runs = [r for r in _iter_page_runs(doc[0]) if r.source.in_xobject]
        assert len(runs) == 1  # the cycle is cut, not repeated
    finally:
        doc.close()
