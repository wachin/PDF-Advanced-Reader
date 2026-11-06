"""F10-4: CropPages edit tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from conftest import build_sample_pdf, make_session

from pdfar.edits import EditError
from pdfar.pages import CropPages, get_page_content_bbox


@pytest.fixture
def doc_with_content(tmp_path):
    """Create a document with known content for cropping tests."""
    path = str(tmp_path / "crop_test.pdf")
    doc = fitz.open()
    # Page 1: text at (100, 200), drawing at (150, 250, 400, 500)
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((100, 200), "Hello World")
    p1.draw_rect(fitz.Rect(150, 250, 400, 500))
    # Page 2: different content
    p2 = doc.new_page(width=600, height=800)
    p2.insert_text((200, 300), "Page 2 content")
    p2.draw_rect(fitz.Rect(50, 50, 300, 300))
    # Page 3: empty page
    p3 = doc.new_page(width=600, height=800)
    doc.save(path)
    doc.close()
    return path


def test_crop_pages_set_rect(doc_with_content):
    """Set a crop rectangle on specific pages."""
    session = make_session(doc_with_content)
    try:
        assert session.doc.page_count == 3
        # Crop page 0 to a specific rect
        edit = CropPages(pages=(0,), rect=fitz.Rect(50, 50, 300, 400))
        session.apply(edit)

        # Verify crop was applied
        assert session.doc[0].cropbox == fitz.Rect(50, 50, 300, 400)
        # Other pages unchanged
        assert session.doc[1].cropbox == session.doc[1].mediabox
        assert session.doc[2].cropbox == session.doc[2].mediabox

        # Verify undo works
        session.undo()
        assert session.doc[0].cropbox == session.doc[0].mediabox
    finally:
        session.close()


def test_crop_pages_reset(doc_with_content):
    """Reset crop box to MediaBox."""
    session = make_session(doc_with_content)
    try:
        # First set a crop
        session.apply(CropPages(pages=(0,), rect=fitz.Rect(100, 100, 200, 200)))
        assert session.doc[0].cropbox != session.doc[0].mediabox

        # Now reset
        session.apply(CropPages(pages=(0,), rect=None))
        assert session.doc[0].cropbox == session.doc[0].mediabox
    finally:
        session.close()


def test_crop_pages_multiple_pages(doc_with_content):
    """Crop multiple pages at once."""
    session = make_session(doc_with_content)
    try:
        edit = CropPages(pages=(0, 1), rect=fitz.Rect(0, 0, 400, 600))
        session.apply(edit)

        assert session.doc[0].cropbox == fitz.Rect(0, 0, 400, 600)
        assert session.doc[1].cropbox == fitz.Rect(0, 0, 400, 600)
        assert session.doc[2].cropbox == session.doc[2].mediabox
    finally:
        session.close()


def test_crop_pages_refusals(doc_with_content, tmp_path):
    """Invalid page index or empty crop raises EditError."""
    session = make_session(doc_with_content)
    try:
        # Out of range page
        with pytest.raises(EditError, match="not in this document"):
            session.apply(CropPages(pages=(10,), rect=fitz.Rect(0, 0, 100, 100)))

        # Empty crop rect
        with pytest.raises(EditError, match="empty or out of bounds"):
            session.apply(CropPages(pages=(0,), rect=fitz.Rect(0, 0, 0, 0)))

        # Negative crop rect
        with pytest.raises(EditError, match="empty or out of bounds"):
            session.apply(CropPages(pages=(0,), rect=fitz.Rect(-10, -10, -5, -5)))
    finally:
        session.close()


def test_get_page_content_bbox(doc_with_content):
    """Compute content bounding box."""
    doc = fitz.open(doc_with_content)
    try:
        # Page 0 has text at (100,200) and drawing at (150,250,400,500)
        bbox0 = get_page_content_bbox(doc[0])
        assert bbox0.x0 <= 100
        assert bbox0.x1 >= 400
        assert bbox0.y0 <= 200
        assert bbox0.y1 >= 500

        # Page 2 is empty
        bbox2 = get_page_content_bbox(doc[2])
        assert bbox2.is_empty

        # With margin
        bbox0_margin = get_page_content_bbox(doc[0], margin=10)
        assert bbox0_margin.x0 <= bbox0.x0
        assert bbox0_margin.x1 >= bbox0.x1
    finally:
        doc.close()


def test_crop_pages_inherited_geometry(tmp_path):
    """Crop works correctly with inherited /CropBox from parent node."""
    # Create a document with inherited crop box
    path = str(tmp_path / "inherited_crop.pdf")
    doc = fitz.open()
    # Add a /CropBox on the root Pages node
    cat_xref = doc.pdf_catalog()
    pages_xref = int(doc.xref_get_key(cat_xref, "Pages")[1].split()[0])
    doc.xref_set_key(pages_xref, "CropBox", "[0 0 400 600]")

    for i in range(2):
        p = doc.new_page(width=600, height=800)
        p.insert_text((50, 100), f"Page {i + 1}")
    doc.save(path)
    doc.close()

    session = make_session(path)
    try:
        # The pages should inherit the crop box from root - check cropbox
        assert session.doc[0].cropbox == fitz.Rect(0, 200, 400, 800)
        assert session.doc[1].cropbox == fitz.Rect(0, 200, 400, 800)

        # Now crop page 0 to something else
        session.apply(CropPages(pages=(0,), rect=fitz.Rect(50, 50, 200, 300)))
        assert session.doc[0].cropbox == fitz.Rect(50, 50, 200, 300)
        # Page 1 still has inherited crop
        assert session.doc[1].cropbox == fitz.Rect(0, 200, 400, 800)
    finally:
        session.close()