"""F10-6: Text editing tests."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from conftest import make_session

from pdfar.edits import EditError
from pdfar.text_editing import (
    TextBoxSettings, HeaderFooterSettings, WatermarkSettings, BatesNumberSettings,
    apply_text_boxes, apply_header_footer, apply_watermark, apply_bates_numbering,
    AddTextBox,
)


@pytest.fixture
def doc_blank(tmp_path):
    """Create a blank document for text editing tests."""
    path = str(tmp_path / "blank.pdf")
    doc = fitz.open()
    for i in range(3):
        p = doc.new_page(width=600, height=800)
        p.insert_text((100, 100), f"Original Page {i + 1}")
    doc.save(path)
    doc.close()
    return path


def test_apply_text_box(doc_blank):
    """Apply a simple text box to a page."""
    doc = fitz.open(doc_blank)
    try:
        settings = TextBoxSettings(
            text="Test Text",
            font_family="Helvetica",
            font_style="Regular",
            font_size=14.0,
            font_color="#FF0000",
            alignment="center",
            box_width=200.0,
            box_height=50.0,
            position="center",
        )
        count = apply_text_boxes(doc, settings, page_range=(0, 0))
        assert count == 1
        
        # Verify text was added
        page = doc[0]
        text = page.get_text()
        assert "Test Text" in text
    finally:
        doc.close()


def test_apply_text_box_multiple_pages(doc_blank):
    """Apply text box to multiple pages."""
    doc = fitz.open(doc_blank)
    try:
        settings = TextBoxSettings(
            text="Multi-page",
            font_family="Helvetica",
            font_size=12.0,
            position="top-left",
        )
        count = apply_text_boxes(doc, settings, page_range=(0, 2))
        assert count == 3
        
        for i in range(3):
            assert "Multi-page" in doc[i].get_text()
    finally:
        doc.close()


def test_apply_text_box_word_wrap(doc_blank):
    """Test word wrapping in text box."""
    doc = fitz.open(doc_blank)
    try:
        settings = TextBoxSettings(
            text="This is a long text that should wrap to multiple lines within the box",
            font_family="Helvetica",
            font_size=10.0,
            box_width=100.0,  # Narrow box to force wrapping
            box_height=100.0,
            word_wrap=True,
        )
        count = apply_text_boxes(doc, settings, page_range=(0, 0))
        assert count == 1
        
        page = doc[0]
        text = page.get_text()
        # Text should be present (wrapped)
        assert "long text" in text
    finally:
        doc.close()


def test_apply_header_footer(doc_blank):
    """Apply header and footer to pages."""
    doc = fitz.open(doc_blank)
    try:
        settings = HeaderFooterSettings(
            header_text="My Header",
            footer_text="Page {number}",
            font_family="Helvetica",
            font_size=10.0,
            include_page_number=True,
            start_number=1,
        )
        count = apply_header_footer(doc, settings, page_range=(0, 2))
        assert count == 3
        
        for i in range(3):
            text = doc[i].get_text()
            assert "My Header" in text
            assert str(i + 1) in text  # Page number
    finally:
        doc.close()


def test_apply_header_footer_different_first(doc_blank):
    """Test different first page header/footer."""
    doc = fitz.open(doc_blank)
    try:
        settings = HeaderFooterSettings(
            header_text="Regular Header",
            footer_text="Regular Footer",
            different_first_page=True,
            first_page_header="First Page Header",
            first_page_footer="First Page Footer",
        )
        apply_header_footer(doc, settings, page_range=(0, 2))
        
        # First page should have different header/footer
        text0 = doc[0].get_text()
        assert "First Page Header" in text0
        assert "First Page Footer" in text0
        
        # Other pages should have regular
        text1 = doc[1].get_text()
        assert "Regular Header" in text1
        assert "Regular Footer" in text1
    finally:
        doc.close()


def test_apply_watermark(doc_blank):
    """Apply a watermark to pages."""
    doc = fitz.open(doc_blank)
    try:
        settings = WatermarkSettings(
            text="CONFIDENTIAL",
            font_family="Helvetica",
            font_style="Bold",
            font_size=60.0,
            font_color="#CCCCCC",
            opacity=0.3,
            rotation=45.0,
        )
        count = apply_watermark(doc, settings, page_range=(0, 1))
        assert count == 2
        
        # Watermark is added as overlay text
        text0 = doc[0].get_text()
        assert "CONFIDENTIAL" in text0
    finally:
        doc.close()


def test_apply_watermark_repeat(doc_blank):
    """Apply tiled watermark."""
    doc = fitz.open(doc_blank)
    try:
        settings = WatermarkSettings(
            text="DRAFT",
            font_size=30.0,
            repeat=True,
            horizontal_spacing=150.0,
            vertical_spacing=100.0,
        )
        count = apply_watermark(doc, settings, page_range=(0, 0))
        assert count == 1
        
        text0 = doc[0].get_text()
        assert "DRAFT" in text0
    finally:
        doc.close()


def test_apply_bates_numbering(doc_blank):
    """Apply Bates numbering."""
    doc = fitz.open(doc_blank)
    try:
        settings = BatesNumberSettings(
            prefix="DOC-",
            suffix="-END",
            start_number=100,
            number_format="{number:04d}",
            font_size=10.0,
            position="bottom-center",
        )
        count = apply_bates_numbering(doc, settings, page_range=(0, 2))
        assert count == 3
        
        assert "DOC-0100-END" in doc[0].get_text()
        assert "DOC-0101-END" in doc[1].get_text()
        assert "DOC-0102-END" in doc[2].get_text()
    finally:
        doc.close()


def test_bates_numbering_skip_pages(doc_blank):
    """Test Bates numbering with skipped pages."""
    doc = fitz.open(doc_blank)
    try:
        settings = BatesNumberSettings(
            start_number=1,
            pages_to_skip=1,  # Skip first page
        )
        apply_bates_numbering(doc, settings, page_range=(0, 2))
        
        assert "000001" not in doc[0].get_text()  # Skipped
        assert "000001" in doc[1].get_text()  # First numbered
        assert "000002" in doc[2].get_text()
    finally:
        doc.close()


def test_add_text_box_edit(doc_blank):
    """Test AddTextBox edit through session."""
    session = make_session(doc_blank)
    try:
        settings = TextBoxSettings(
            text="Edited Text",
            font_size=14.0,
            position="top-left",
        )
        edit = AddTextBox(settings=settings, page_range=(0, 1))
        session.apply(edit)
        
        assert "Edited Text" in session.doc[0].get_text()
        assert "Edited Text" in session.doc[1].get_text()
        assert "Edited Text" not in session.doc[2].get_text()
    finally:
        session.close()


def test_text_box_edit_undo(doc_blank):
    """Test AddTextBox edit undo."""
    session = make_session(doc_blank)
    try:
        settings = TextBoxSettings(text="To Undo")
        edit = AddTextBox(settings=settings, page_range=(0, 0))
        session.apply(edit)
        
        assert "To Undo" in session.doc[0].get_text()
        
        session.undo()
        
        assert "To Undo" not in session.doc[0].get_text()
    finally:
        session.close()


def test_text_box_positions(doc_blank):
    """Test different text box positions."""
    doc = fitz.open(doc_blank)
    try:
        positions = ["top-left", "top-center", "top-right",
                     "middle-left", "center", "middle-right",
                     "bottom-left", "bottom-center", "bottom-right"]
        
        for pos in positions:
            settings = TextBoxSettings(
                text=f"Pos: {pos}",
                font_size=8.0,
                position=pos,
                box_width=100,
                box_height=20,
            )
            count = apply_text_boxes(doc, settings, page_range=(0, 0))
            assert count == 1
        
        # All positions should have their text
        text = doc[0].get_text()
        for pos in positions:
            assert f"Pos: {pos}" in text
    finally:
        doc.close()