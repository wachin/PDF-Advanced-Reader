"""F10-4: Extract pages to a new PDF document."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_settle import nested_pdf, own

from pdfar.edits import EditError
from pdfar.pages import extract_pages


@pytest.fixture
def sample_file(tmp_path):
    path = str(tmp_path / "sample.pdf")
    build_sample_pdf(path, pages=6)
    return path


def test_extract_pages_creates_valid_pdf(sample_file, tmp_path):
    """Extract pages [1, 3] from a 6-page document."""
    doc = fitz.open(sample_file)
    target_path = str(tmp_path / "extracted.pdf")
    try:
        extract_pages(doc, [1, 3], target_path)
    finally:
        doc.close()

    assert os.path.exists(target_path)
    extracted_doc = fitz.open(target_path)
    try:
        assert extracted_doc.page_count == 2
        assert "Page 2 content" in extracted_doc[0].get_text()
        assert "Page 4 content" in extracted_doc[1].get_text()
    finally:
        extracted_doc.close()


def test_extract_pages_scattered_pick(sample_file, tmp_path):
    """Extract non-contiguous pages [0, 2, 4]."""
    doc = fitz.open(sample_file)
    target_path = str(tmp_path / "scattered.pdf")
    try:
        extract_pages(doc, [0, 2, 4], target_path)
    finally:
        doc.close()

    extracted_doc = fitz.open(target_path)
    try:
        assert extracted_doc.page_count == 3
        assert "Hello PDFAR world" in extracted_doc[0].get_text()
        assert "Page 3 content" in extracted_doc[1].get_text()
        assert "Page 5 content" in extracted_doc[2].get_text()
    finally:
        extracted_doc.close()


def test_extract_pages_nested_geometry(tmp_path):
    """Extract from a nested PDF where size/rotation are inherited from ancestors."""
    path = nested_pdf(tmp_path / "nested.pdf")
    doc = fitz.open(path)
    target_path = str(tmp_path / "extracted_nested.pdf")
    try:
        extract_pages(doc, [0, 2], target_path)
    finally:
        doc.close()

    extracted_doc = fitz.open(target_path)
    try:
        assert extracted_doc.page_count == 2
        # Page 0 was 90 degrees turned, page 2 was cropped to (260, 360)
        assert extracted_doc[0].rotation == 90
        assert tuple(round(v) for v in extracted_doc[1].rect) == (0, 0, 260, 360)
    finally:
        extracted_doc.close()


def test_extract_pages_preserves_labels_and_toc(tmp_path):
    """Extract pages from a document with page labels and TOC."""
    path = str(tmp_path / "labeled.pdf")
    doc = fitz.open()
    for i in range(6):
        doc.new_page(width=200, height=100)
    doc.set_page_labels([
        {"startpage": 0, "prefix": "p", "style": "r"},
        {"startpage": 2, "prefix": "x", "style": "a"},
    ])
    doc.set_toc([[1, "Chapter 1", 1], [1, "Chapter 2", 3], [1, "Chapter 3", 5]])
    doc.set_metadata({"title": "Test Extraction"})
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    target_path = str(tmp_path / "extracted_labeled.pdf")
    try:
        extract_pages(doc, [0, 2, 4], target_path)
    finally:
        doc.close()

    extracted = fitz.open(target_path)
    try:
        assert extracted.page_count == 3
        # Labels for pages 0, 2, 4 should be pi, xa, xb
        assert [extracted[i].get_label() for i in range(3)] == ["pi", "xa", "xb"]
        # TOC for chapters on pages 0, 2, 4 (1-based 1, 3, 5) -> new pages 1, 2, 3
        toc = extracted.get_toc()
        assert toc == [[1, "Chapter 1", 1], [1, "Chapter 2", 2], [1, "Chapter 3", 3]]
        assert extracted.metadata["title"] == "Test Extraction"
    finally:
        extracted.close()


def test_extract_pages_refusals(sample_file, tmp_path):
    """Empty list or invalid index raises EditError."""
    doc = fitz.open(sample_file)
    target_path = str(tmp_path / "fail.pdf")
    try:
        with pytest.raises(EditError, match="No pages chosen"):
            extract_pages(doc, [], target_path)

        with pytest.raises(EditError, match="out of bounds|not in this document"):
            extract_pages(doc, [10], target_path)
    finally:
        doc.close()
