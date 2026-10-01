"""Tests for search matching and the background search worker."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from pdfar.search import (
    SearchHit, SearchParams, SearchWorker, make_snippet, normalize_space, page_matches,
)


@pytest.mark.parametrize("text,params,expected", [
    ("Hello PDFAR world", SearchParams("pdfar"), True),
    ("Hello PDFAR world", SearchParams("pdfar", case_sensitive=True), False),
    ("Hello PDFAR world", SearchParams("PDFAR", case_sensitive=True), True),
    ("Hello PDFAR world", SearchParams("pdfar", whole_words=True), True),
    ("Hello PDFARs world", SearchParams("pdfar", whole_words=True), False),
    ("Hello PDFARs world", SearchParams("pdfar", whole_words=False), True),
])
def test_phrase_matching(text, params, expected):
    assert page_matches(text, params, [params.query]) is expected


def test_and_or_modes():
    text = "alpha beta gamma"
    assert page_matches(text, SearchParams("alpha beta", mode="AND"), ["alpha", "beta"]) is True
    assert page_matches(text, SearchParams("alpha delta", mode="AND"), ["alpha", "delta"]) is False
    assert page_matches(text, SearchParams("alpha delta", mode="OR"), ["alpha", "delta"]) is True
    assert page_matches(text, SearchParams("zeta omega", mode="OR"), ["zeta", "omega"]) is False


def test_whole_words_with_regex_characters():
    text = "value (x) here"
    assert page_matches(text, SearchParams("(x)", whole_words=True), ["(x)"]) is True


def test_normalize_space():
    assert normalize_space("  a \n b\t c  ") == "a b c"
    assert normalize_space(None) == ""


def test_make_snippet_centers_on_match():
    text = "word " * 40 + "NEEDLE " + "word " * 40
    snippet = make_snippet(text, ["NEEDLE"], False)
    assert "NEEDLE" in snippet
    assert len(snippet) < 250


def _run_worker(sample_pdf, params):
    hits = []
    worker = SearchWorker(sample_pdf, params)
    worker.done.connect(hits.extend)
    worker.run()
    return hits


def test_worker_finds_pages(sample_pdf):
    hits = _run_worker(sample_pdf, SearchParams("alpha beta", mode="AND"))
    # "alpha beta gamma" is on pages 2..3 of the sample
    assert [h.page for h in hits] == [1, 2]
    assert all(isinstance(h, SearchHit) for h in hits)
    assert all(h.rects for h in hits), "every hit should carry highlight rects"


def test_worker_phrase_mode(sample_pdf):
    hits = _run_worker(sample_pdf, SearchParams("BOTTOMRIGHT", mode="PHRASE"))
    assert [h.page for h in hits] == [0]
    assert hits[0].rects


def test_worker_no_match(sample_pdf):
    assert _run_worker(sample_pdf, SearchParams("zzz-not-there")) == []


def test_worker_rects_are_page_bounded(sample_pdf):
    import pymupdf as fitz
    doc = fitz.open(sample_pdf)
    size = doc.load_page(0).rect
    doc.close()
    hits = _run_worker(sample_pdf, SearchParams("Hello"))
    for x0, y0, x1, y1 in hits[0].rects:
        assert 0 <= x0 <= x1 <= size.width + 1
        assert 0 <= y0 <= y1 <= size.height + 1
