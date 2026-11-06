"""F10-5: True redaction tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from conftest import make_session

from pdfar.edits import EditError
from pdfar.annotations import ApplyRedactions, AddRedact


@pytest.fixture
def doc_with_content(tmp_path):
    """Create a document with known content for redaction tests."""
    path = str(tmp_path / "redact_test.pdf")
    doc = fitz.open()
    # Page 1: text and drawing
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((100, 200), "Hello World Secret")
    p1.insert_text((100, 300), "This should be redacted")
    p1.draw_rect(fitz.Rect(150, 250, 400, 350), color=(1, 0, 0), fill=(0, 1, 0))
    # Page 2: different content
    p2 = doc.new_page(width=600, height=800)
    p2.insert_text((200, 300), "Page 2 content")
    p2.draw_rect(fitz.Rect(50, 50, 300, 300), color=(0, 0, 1))
    # Page 3: empty page
    p3 = doc.new_page(width=600, height=800)
    doc.save(path)
    doc.close()
    return path


def test_add_redact_mark(doc_with_content):
    """Add a redaction mark to a page."""
    session = make_session(doc_with_content)
    try:
        edit = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 190, 500, 220),
            fill_color="#FF0000",
            overlay_text="REDACTED",
            author="Test User",
        )
        session.apply(edit)

        # Verify the redaction mark was added
        page = session.doc.load_page(0)
        redacts = [a for a in page.annots() if a.type[0] == 12]
        assert len(redacts) == 1
        assert redacts[0].rect == fitz.Rect(100, 190, 500, 220)
        assert redacts[0].info.get("content") == "REDACTED"
        assert redacts[0].info.get("title") == "Test User"
    finally:
        session.close()


def test_apply_redactions_removes_text(doc_with_content):
    """Apply redactions removes text in the marked area."""
    session = make_session(doc_with_content)
    try:
        # Add redaction mark covering "Hello World Secret" only
        edit = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 190, 500, 220),
            fill_color="#000000",
        )
        session.apply(edit)

        # Apply redactions
        edit = ApplyRedactions()
        session.apply(edit)

        # Verify text was removed
        page = session.doc.load_page(0)
        text = page.get_text()
        assert "Secret" not in text
        assert "Hello World" not in text  # Covered by redaction
        # Text outside redaction should remain
        assert "This should be redacted" in text
    finally:
        session.close()


def test_apply_redactions_removes_drawings(doc_with_content):
    """Apply redactions removes vector drawings in the marked area."""
    session = make_session(doc_with_content)
    try:
        # Add redaction mark covering the drawing
        edit = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 190, 500, 360),  # Covers drawing at (150,250,400,350)
            fill_color="#000000",
        )
        session.apply(edit)

        # Apply redactions
        edit = ApplyRedactions()
        session.apply(edit)

        # Verify drawing was removed
        page = session.doc.load_page(0)
        drawings = page.get_drawings()
        # The redaction itself creates a drawing (the fill rect)
        # Original drawing should be gone
        orig_drawing_found = False
        for d in drawings:
            if d['rect'] == fitz.Rect(150, 250, 400, 350):
                orig_drawing_found = True
                break
        assert not orig_drawing_found
    finally:
        session.close()


def test_apply_redactions_removes_images(doc_with_content, tmp_path):
    """Apply redactions removes images in the marked area."""
    from PIL import Image
    import io

    # Add an image to the document first
    doc = fitz.open(doc_with_content)
    img = Image.new('RGB', (100, 100), color='red')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    doc[0].insert_image(fitz.Rect(100, 100, 300, 300), stream=buf.read())
    # Save to a new file since original might be encrypted
    new_path = str(tmp_path / "with_image.pdf")
    doc.save(new_path)
    doc.close()

    session = make_session(new_path)
    try:
        # Add redaction mark covering the image
        edit = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 100, 300, 300),
            fill_color="#000000",
        )
        session.apply(edit)

        # Apply redactions
        edit = ApplyRedactions()
        session.apply(edit)

        # Verify image was removed
        page = session.doc.load_page(0)
        images = page.get_images()
        assert len(images) == 0
    finally:
        session.close()


def test_apply_redactions_no_marks(doc_with_content):
    """ApplyRedactions with no marks raises EditError."""
    session = make_session(doc_with_content)
    try:
        with pytest.raises(EditError, match="No redaction marks found"):
            session.apply(ApplyRedactions())
    finally:
        session.close()


def test_apply_redactions_multiple_pages(doc_with_content):
    """Apply redactions on multiple pages."""
    session = make_session(doc_with_content)
    try:
        # Add redaction marks on pages 0 and 1
        edit1 = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 190, 500, 220),
            fill_color="#000000",
        )
        edit2 = AddRedact(
            ann_id="test-redact-2",
            page=1,
            rect=(50, 50, 400, 350),  # Covers "Page 2 content" at (200, 300)
            fill_color="#000000",
        )
        session.apply(edit1)
        session.apply(edit2)

        # Apply redactions
        edit = ApplyRedactions()
        session.apply(edit)

        # Verify both pages were processed
        page0 = session.doc.load_page(0)
        page1 = session.doc.load_page(1)
        assert "Secret" not in page0.get_text()
        assert "Page 2 content" not in page1.get_text()
    finally:
        session.close()


def test_redact_mark_refusals(doc_with_content):
    """Invalid redaction mark parameters raise EditError."""
    session = make_session(doc_with_content)
    try:
        # Too small rect
        with pytest.raises(EditError, match="too small"):
            session.apply(AddRedact(
                ann_id="test-redact-1",
                page=0,
                rect=(100, 190, 101, 191),
                fill_color="#000000",
            ))

        # Out of range page
        with pytest.raises(EditError, match="page not in document"):
            session.apply(AddRedact(
                ann_id="test-redact-2",
                page=10,
                rect=(100, 190, 500, 220),
                fill_color="#000000",
            ))
    finally:
        session.close()


def test_redact_mark_undo(doc_with_content):
    """Adding and removing redaction marks is undoable."""
    session = make_session(doc_with_content)
    try:
        edit = AddRedact(
            ann_id="test-redact-1",
            page=0,
            rect=(100, 190, 500, 220),
            fill_color="#000000",
        )
        session.apply(edit)

        page = session.doc.load_page(0)
        redacts = [a for a in page.annots() if a.type[0] == 12]
        assert len(redacts) == 1

        # Undo
        session.undo()

        page = session.doc.load_page(0)
        redacts = [a for a in page.annots() if a.type[0] == 12]
        assert len(redacts) == 0
    finally:
        session.close()