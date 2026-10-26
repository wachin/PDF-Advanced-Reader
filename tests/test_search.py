"""Tests for search matching and the background search worker."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from pdfar.search import (
    SearchHit, SearchParams, SearchWorker, compile_regex, make_snippet,
    normalize_space, page_matches,
)
from PyQt6.QtWidgets import QApplication


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


def test_regex_matching():
    """Test regex matching functionality."""
    text = "The quick brown fox jumps over the lazy dog"
    # Test basic regex matching
    assert page_matches(text, SearchParams(r"quick.*fox", mode="REGEX"), ["quick.*fox"]) is True
    # Case insensitive by default
    assert page_matches(text, SearchParams(r"QUICK.*FOX", mode="REGEX"), ["QUICK.*FOX"]) is True
    # Case sensitive
    assert page_matches(text, SearchParams("quick.*fox", mode="REGEX", case_sensitive=True), ["quick.*fox"]) is True
    assert page_matches(text, SearchParams("QUICK.*FOX", mode="REGEX", case_sensitive=True), ["QUICK.*FOX"]) is False
    # No match
    assert page_matches(text, SearchParams(r"cat.*dog", mode="REGEX"), ["cat.*dog"]) is False


def test_regex_invalid_pattern():
    """Test that invalid regex patterns are handled gracefully."""
    text = "test"
    # Invalid regex should not crash
    assert page_matches(text, SearchParams(r"[invalid", mode="REGEX"), ["[invalid"]) is False


def test_compile_regex_rejects_invalid_pattern():
    with pytest.raises(ValueError, match="Invalid regex"):
        compile_regex(SearchParams(r"[invalid", mode="REGEX"))


def test_regex_worker(sample_pdf):
    """Test regex search worker."""
    hits = []
    params = SearchParams(r"alpha.*gamma", mode="REGEX", case_sensitive=False)
    worker = SearchWorker(sample_pdf, params)
    
    def on_done(hits_list):
        hits.extend(hits_list)
    
    worker.done.connect(on_done)
    worker.run()
    
    # Need to process events to allow worker to complete
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    for _ in range(50):  # Wait up to 5 seconds
        QApplication.instance().processEvents()
        time.sleep(0.1)
        if len(hits) > 0:
            break
    
    # The sample PDF has "alpha beta gamma" on pages 1, 2 (0-indexed pages 1, 2)
    # Page 0 has "Hello PDFAR world" instead
    assert [h.page for h in hits] == [1, 2]
    assert all(isinstance(h, SearchHit) for h in hits)


def test_worker_cancel_discards_results(sample_pdf):
    worker = SearchWorker(sample_pdf, SearchParams("alpha beta", mode="AND"))
    hits, progress = [], []

    def on_progress(done, total):
        progress.append(done)
        worker.cancel()

    worker.progress.connect(on_progress)
    worker.done.connect(hits.extend)
    worker.run()

    assert progress, "the worker should have started scanning pages"
    assert hits == [], "a cancelled search must not publish partial results"


def test_worker_invalid_regex_reports_failure(sample_pdf):
    worker = SearchWorker(sample_pdf, SearchParams(r"[invalid", mode="REGEX"))
    errors, hits = [], []
    worker.failed.connect(errors.append)
    worker.done.connect(hits.extend)
    worker.run()

    assert errors and "Invalid regex" in errors[0]
    assert hits == []


def test_regex_timeout_reports_failure(monkeypatch, sample_pdf):
    ticks = iter(range(0, 1000))
    monkeypatch.setattr("pdfar.search._now", lambda: float(next(ticks)))
    worker = SearchWorker(
        sample_pdf, SearchParams("alpha", mode="REGEX", regex_timeout_ms=1))
    errors, hits = [], []
    worker.failed.connect(errors.append)
    worker.done.connect(hits.extend)
    worker.run()

    assert errors and "timed out" in errors[0].lower()
    assert hits == []
