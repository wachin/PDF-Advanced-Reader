"""Phase 6B: the read-only adapter over the ported PdfCraft engine.

The adapter is the only application-facing entry point to
:mod:`pdfar.pdfcraft`.  These tests hold it to the contract the phase defines:

* it never modifies the document or the file it came from;
* it reports lines, paragraphs and operator provenance for the structures real
  producers write (shared BT/ET, one BT/ET per row, several content streams,
  two columns, turned text, embedded subsets);
* it refuses what the engine cannot rebuild, with a reason a user can act on;
* its identity and staleness rules match ``DocumentSession``.

The comparison tests at the end put the two engines side by side.  They assert
where the engines agree and *document* the one place where they deliberately do
not: list markers start a new paragraph in the port (PdfCraft #2564) while the
existing geometric grouper keeps the list as one block.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pymupdf as fitz
import pytest

import pdfcraft_fixtures as fx
from pdfar.edits import SetMetadata
from pdfar.paragraph import extract_paragraphs
from pdfar.pdfcraft import strip_subset_tag
from pdfar.pdfcraft_adapter import (
    AnalysisError,
    ParagraphKey,
    analyze_page,
    analyze_session_page,
)
from pdfar.session import DocumentSession

TEXT_SHOWING = {"Tj", "TJ", "'", '"'}


def _norm(text: str) -> str:
    return " ".join(text.split())


def _session(path) -> DocumentSession:
    doc = fitz.open(str(path))
    return DocumentSession(doc, str(path))


@pytest.fixture(scope="module")
def pdfs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("pdfcraft-adapter")
    builders = {
        "simple": fx.simple_paragraph,
        "shared_btet": fx.shared_btet,
        "multi_stream": fx.multi_stream,
        "two_column": fx.two_column,
        "form_xobject": fx.form_xobject,
        "rotated": fx.rotated,
        "mixed_styles": fx.mixed_styles,
        "row_per_bt": fx.row_per_bt,
        "list_items": fx.list_items,
        "undecodable": fx.undecodable_text,
        "quote_surplus": fx.quote_and_surplus,
        "missing_font": fx.missing_font,
        "vertical": fx.vertical_text,
        "empty": fx.empty_page,
    }
    out = {}
    for name, build in builders.items():
        path = directory / f"{name}.pdf"
        build(path)
        out[name] = str(path)
    if fx.embedded_font_path():
        path = directory / "embedded_subset.pdf"
        fx.embedded_subset(path)
        out["embedded_subset"] = str(path)
    return out


def _analysis(pdfs, name):
    doc = fitz.open(pdfs[name])
    try:
        return analyze_page(doc, 0)
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Read-only guarantees
# --------------------------------------------------------------------------- #
def test_analysis_leaves_the_file_untouched(pdfs):
    path = pdfs["shared_btet"]
    with open(path, "rb") as handle:
        before = handle.read()

    doc = fitz.open(path)
    try:
        assert doc.is_dirty is False
        analyze_page(doc, 0)
        assert doc.is_dirty is False
    finally:
        doc.close()

    with open(path, "rb") as handle:
        assert handle.read() == before


def test_analysis_leaves_the_open_document_untouched(pdfs):
    doc = fitz.open(pdfs["multi_stream"])
    try:
        page = doc[0]
        xrefs = list(page.get_contents())
        streams = [doc.xref_stream(xref) for xref in xrefs]
        objects = doc.xref_length()

        analyze_page(doc, 0)

        assert doc.xref_length() == objects
        assert list(doc[0].get_contents()) == xrefs
        assert [doc.xref_stream(xref) for xref in xrefs] == streams
    finally:
        doc.close()


def test_analysis_does_not_need_a_writable_document(pdfs):
    """The read path is pure MuPDF reading: a stream-opened document works too."""
    with open(pdfs["simple"], "rb") as handle:
        data = handle.read()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        analysis = analyze_page(doc, 0)
        assert analysis.paragraphs
        assert doc.is_dirty is False
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Structures
# --------------------------------------------------------------------------- #
def test_shared_btet_is_two_paragraphs(pdfs):
    analysis = _analysis(pdfs, "shared_btet")
    assert [p.row_count for p in analysis.paragraphs] == [2, 2]
    assert analysis.paragraphs[0].text.startswith("Alpha paragraph")
    assert analysis.paragraphs[1].text.startswith("Beta paragraph")
    assert all(p.editable for p in analysis.paragraphs)
    assert {p.streams for p in analysis.paragraphs} == {(0,)}


def test_one_bt_per_row_is_one_paragraph(pdfs):
    analysis = _analysis(pdfs, "row_per_bt")
    assert [p.row_count for p in analysis.paragraphs] == [2, 1]
    first = analysis.paragraphs[0]
    assert first.text.startswith("First row of the paragraph")
    assert first.text.endswith("continues")
    assert first.editable
    # Each row is its own text object, so each line has exactly one operator.
    assert [len(line.operators) for line in first.lines] == [1, 1]
    assert {span.keyword for line in first.lines for span in line.operators} == {"Tj"}


def test_multiple_content_streams_keep_their_own_provenance(pdfs):
    analysis = _analysis(pdfs, "multi_stream")
    assert [p.streams for p in analysis.paragraphs] == [(0,), (1,)]
    assert all(p.editable for p in analysis.paragraphs)
    assert not any(line.from_form for line in analysis.lines)
    # A paragraph in the second stream is still readable and typed correctly.
    second = analysis.paragraphs[1]
    assert second.text.startswith("Second stream paragraph")
    assert second.row_count == 3


def test_two_columns_stay_two_paragraphs(pdfs):
    analysis = _analysis(pdfs, "two_column")
    assert len(analysis.paragraphs) == 2
    left, right = analysis.paragraphs
    assert left.text.startswith("Left column first row")
    assert right.text.startswith("Right column first row")
    # Rows never join across the gutter: every row's box ends left of the
    # right column's start.
    assert max(line.rect[2] for line in left.lines) < min(
        line.rect[0] for line in right.lines)


def test_rotated_text_keeps_its_frame(pdfs):
    analysis = _analysis(pdfs, "rotated")
    turned = next(p for p in analysis.paragraphs if p.text.startswith("Rotated line"))
    assert abs(turned.rotation - 90.0) < 0.1
    line = turned.lines[0]
    # The upright box of turned text is about one em wide; the frame along the
    # baseline is the real row width, which a reflow must wrap to.
    assert line.frame[2] - line.frame[0] > line.rect[2] - line.rect[0] + 20
    upright = next(p for p in analysis.paragraphs if p.text.startswith("Upright sibling"))
    assert abs(upright.rotation) < 0.1


def test_mixed_font_row_is_refused_with_reasons(pdfs):
    analysis = _analysis(pdfs, "mixed_styles")
    mixed = next(p for p in analysis.paragraphs if "Bold lead-in" in p.text)
    assert mixed.editable is False
    reasons = " ".join(mixed.problems)
    assert "font resources" in reasons
    assert "font sizes" in reasons
    assert "bold/italic" in reasons
    # The plain row below it is a separate, editable paragraph.
    plain = next(p for p in analysis.paragraphs if "Second row" in p.text)
    assert plain.editable is True


@pytest.mark.skipif(fx.embedded_font_path() is None,
                    reason="no TrueType font is installed to embed")
def test_embedded_subset_font_is_read(pdfs):
    analysis = _analysis(pdfs, "embedded_subset")
    assert len(analysis.paragraphs) == 1
    paragraph = analysis.paragraphs[0]
    assert paragraph.editable
    assert paragraph.font_resource == "F0"
    assert "DejaVu" in strip_subset_tag(paragraph.base_font)
    assert paragraph.row_count >= 3


def test_form_xobject_text_is_read_but_refused(pdfs):
    analysis = _analysis(pdfs, "form_xobject")
    form = next(p for p in analysis.paragraphs if "form XObject" in p.text)
    assert form.editable is False
    assert "Form XObject" in " ".join(form.problems)
    assert all(line.from_form for line in form.lines)
    # A form's operator offsets are local to the form stream, so no page
    # provenance is claimed for it.
    assert form.operators == ()

    page_text = next(p for p in analysis.paragraphs if "Page text" in p.text)
    assert page_text.editable is True
    assert not any(line.from_form for line in page_text.lines)


def test_list_items_are_separate_paragraphs(pdfs):
    analysis = _analysis(pdfs, "list_items")
    assert len(analysis.paragraphs) == 5
    assert [p.row_count for p in analysis.paragraphs] == [1] * 5


def test_undecodable_text_is_refused(pdfs):
    """A code with no Unicode mapping must not look like a safe paragraph.

    The row still shows on the page, but the engine can only read part of it.
    Reporting the paragraph as editable would let a rewrite drop the glyph it
    could not decode, so the adapter refuses it and says why.
    """
    analysis = _analysis(pdfs, "undecodable")
    paragraph = next(p for p in analysis.paragraphs if "Hello" in p.text)
    assert paragraph.editable is False
    assert "no Unicode mapping" in " ".join(paragraph.problems)
    assert [line.decodable for line in paragraph.lines] == [False, True]


def test_quote_operator_and_surplus_tj_are_read(pdfs):
    """``"`` carries its string last, and a TJ takes the array before it."""
    analysis = _analysis(pdfs, "quote_surplus")
    texts = [p.text for p in analysis.paragraphs]
    assert "Quoted operator row" in texts
    assert "array row" in texts
    assert "Ignored" not in " ".join(texts)


def test_unreadable_text_is_reported_not_hidden(pdfs):
    """A run whose font cannot be read is absent, and the page says so."""
    analysis = _analysis(pdfs, "missing_font")
    assert "Readable row" in [p.text for p in analysis.paragraphs]
    assert not any("no font metrics" in line.text for line in analysis.lines)
    assert any("could not be read" in note for note in analysis.diagnostics)


def test_vertical_writing_is_refused(pdfs):
    """``/WMode 1`` text must not be reported as a horizontal paragraph."""
    analysis = _analysis(pdfs, "vertical")
    turned = [p for p in analysis.paragraphs if any(line.vertical for line in p.lines)]
    assert turned
    assert turned[0].editable is False
    assert "vertical writing" in " ".join(turned[0].problems)


def test_empty_page_has_no_paragraphs(pdfs):
    analysis = _analysis(pdfs, "empty")
    assert analysis.paragraphs == ()
    assert analysis.lines == ()
    assert "No text was found" in " ".join(analysis.diagnostics)


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_operator_spans_point_at_real_text_operators(pdfs):
    doc = fitz.open(pdfs["shared_btet"])
    try:
        streams = [doc.xref_stream(xref) for xref in doc[0].get_contents()]
        analysis = analyze_page(doc, 0)
    finally:
        doc.close()

    for paragraph in analysis.paragraphs:
        keys = [(span.stream, span.op_index) for span in paragraph.operators]
        assert len(keys) == len(set(keys))
        for span in paragraph.operators:
            data = streams[span.stream]
            assert 0 <= span.start < span.end <= len(data)
            assert span.keyword in TEXT_SHOWING
            assert data[span.start:span.end].rstrip().endswith(
                span.keyword.encode("latin-1"))
        # One row is drawn by one TJ in this fixture.
        assert [len(line.operators) for line in paragraph.lines] == [1, 1]


def test_spans_in_a_later_stream_use_that_streams_offsets(pdfs):
    doc = fitz.open(pdfs["multi_stream"])
    try:
        streams = [doc.xref_stream(xref) for xref in doc[0].get_contents()]
        analysis = analyze_page(doc, 0)
    finally:
        doc.close()

    second = analysis.paragraphs[1]
    assert second.streams == (1,)
    for span in second.operators:
        assert span.stream == 1
        assert 0 <= span.start < span.end <= len(streams[1])


def test_fingerprint_identifies_the_read_content(pdfs):
    doc = fitz.open(pdfs["simple"])
    try:
        first = analyze_page(doc, 0)
        second = analyze_page(doc, 0)
    finally:
        doc.close()
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != _analysis(pdfs, "shared_btet").fingerprint


# --------------------------------------------------------------------------- #
# Identity, lookup and diagnostics
# --------------------------------------------------------------------------- #
def test_paragraph_key_finds_the_same_paragraph(pdfs):
    analysis = _analysis(pdfs, "shared_btet")
    target = analysis.paragraphs[1]

    assert analysis.find(target.key) is target
    assert analysis.find(target.key, hint=1) is target
    assert analysis.find(target.key, hint=0) is target

    moved = ParagraphKey(target.text, (target.rect[0] + 1.0, *target.rect[1:]))
    assert analysis.find(moved) is None
    renamed = ParagraphKey(target.text + "!", target.rect)
    assert analysis.find(renamed) is None


def test_paragraph_lookup_by_point_and_box(pdfs):
    analysis = _analysis(pdfs, "shared_btet")
    first, second = analysis.paragraphs

    centre = ((first.rect[0] + first.rect[2]) / 2,
              (first.rect[1] + first.rect[3]) / 2)
    assert analysis.paragraph_at(*centre) is first
    assert analysis.paragraph_at(-50.0, -50.0) is None

    assert analysis.paragraph_overlapping(first.rect) is first
    assert analysis.paragraph_overlapping(second.rect) is second
    # A box that covers the first paragraph and clips the top of the second
    # still resolves to the first, which holds most of the overlap.
    mixed = (first.rect[0], second.rect[1] + 10.0, second.rect[2], first.rect[3])
    assert analysis.paragraph_overlapping(mixed) is first


def test_diagnostics_count_editable_and_unsupported(pdfs):
    analysis = _analysis(pdfs, "form_xobject")
    summary = analysis.diagnostics[0]
    assert "2 paragraph(s)" in summary
    assert "1 editable" in summary
    assert "1 unsupported" in summary
    assert any("Form XObject" in line for line in analysis.diagnostics)


def test_page_outside_the_document_is_refused(pdfs):
    doc = fitz.open(pdfs["simple"])
    try:
        with pytest.raises(AnalysisError, match="outside the document"):
            analyze_page(doc, 3)
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# DocumentSession integration
# --------------------------------------------------------------------------- #
def test_session_analysis_does_not_invalidate_the_live_page(pdfs):
    session = _session(pdfs["simple"])
    try:
        page = session.doc[0]
        objects = session.doc.xref_length()

        analysis = analyze_session_page(session, 0)

        assert analysis.version == session.version == 0
        assert session.doc.xref_length() == objects
        assert page.get_text().strip()          # the handle is still usable
        assert session.snapshot() is None        # nothing was published
    finally:
        session.close()


def test_session_analysis_matches_a_direct_read(pdfs):
    session = _session(pdfs["two_column"])
    try:
        from_session = analyze_session_page(session, 0)
        direct = analyze_page(session.doc, 0)
    finally:
        session.close()
    assert [p.text for p in from_session.paragraphs] == [
        p.text for p in direct.paragraphs]


def test_an_accepted_edit_makes_the_analysis_stale(pdfs):
    session = _session(pdfs["shared_btet"])
    try:
        analysis = analyze_session_page(session, 0)
        assert analysis.is_current(session)

        session.apply(SetMetadata((("title", "Phase 6B"),)))

        assert session.version == 1
        assert not analysis.is_current(session)
        # The old result is still readable — it is a snapshot — but a new read
        # is stamped with the revision it actually saw.
        assert analysis.paragraphs
        fresh = analyze_session_page(session, 0)
        assert fresh.version == 1
        assert fresh.is_current(session)
        assert fresh.fingerprint == analysis.fingerprint   # metadata-only edit
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# Comparison with the existing engine
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", [
    "simple", "shared_btet", "multi_stream", "row_per_bt",
])
def test_grouping_agrees_with_the_existing_engine(pdfs, name):
    doc = fitz.open(pdfs[name])
    try:
        port = analyze_page(doc, 0)
        existing = extract_paragraphs(doc[0])
    finally:
        doc.close()

    assert len(port.paragraphs) == len(existing)
    assert [_norm(p.text) for p in port.paragraphs] == [
        _norm(p.text) for p in existing]


def test_line_extraction_matches_mupdf_text(pdfs):
    doc = fitz.open(pdfs["two_column"])
    try:
        analysis = analyze_page(doc, 0)
        mupdf = {_norm(line) for line in doc[0].get_text().splitlines() if line.strip()}
    finally:
        doc.close()
    port = {_norm(line.text) for line in analysis.lines if line.text.strip()}
    assert port == mupdf


def test_list_markers_are_a_documented_difference(pdfs):
    """The two engines legitimately disagree about a list.

    The existing grouper joins the bulleted items geometrically — same font,
    same left edge, one leading apart — and does the same for the numbered
    items, giving two paragraphs.  The port follows PdfCraft #2564 and starts a
    new paragraph at every marker, which is what a list needs.  Neither is wrong
    for reading; the port's grouping is the one an editor can rebuild item by
    item.
    """
    doc = fitz.open(pdfs["list_items"])
    try:
        port = analyze_page(doc, 0)
        existing = extract_paragraphs(doc[0])
    finally:
        doc.close()

    assert len(port.paragraphs) == 5
    assert len(existing) == 2
    assert _norm(" ".join(p.text for p in existing)) == _norm(
        " ".join(p.text for p in port.paragraphs))


def test_two_columns_are_a_documented_difference(pdfs):
    """The existing grouper measures its body width across the whole page.

    Each column's rows are narrower than that width and start at the body edge,
    so the "short line followed by a full line" rule splits them: the existing
    engine returns one paragraph per line (six here).  The port measures each
    column's own leading and returns the two paragraphs a reader sees.  This is
    a limitation of the existing implementation, not of the document, and the
    adapter is read-only, so the difference is recorded rather than fixed here.
    """
    doc = fitz.open(pdfs["two_column"])
    try:
        port = analyze_page(doc, 0)
        existing = extract_paragraphs(doc[0])
    finally:
        doc.close()

    assert len(port.paragraphs) == 2
    assert len(existing) == 6
    assert _norm(" ".join(p.text for p in existing)) == _norm(
        " ".join(p.text for p in port.paragraphs))
