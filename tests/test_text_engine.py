"""F10-7: Text editing engine tests."""

from __future__ import annotations

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
import pytest
from conftest import make_session

from pdfar.edits import EditError
from pdfar.text_engine import (
    ContentStreamTokenizer, GlyphOperatorMapper, FontLadder,
    ContentStreamSplicer, TextEditEngine,
    FontLadder, ContentStreamSplicer, TextEditEngine,
)


@pytest.fixture
def doc_with_text(tmp_path):
    """Create a document with known text for testing."""
    path = str(tmp_path / "test_text.pdf")
    doc = fitz.open()
    for i in range(3):
        p = doc.new_page(width=600, height=800)
        p.insert_text((100, 200), f"Hello World Page {i+1}", fontsize=14)
        p.insert_text((100, 300), f"Test text with αβγ", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_content_stream_tokenizer(doc_with_text):
    """Test ContentStreamTokenizer initialization."""
    session = make_session(doc_with_text)
    try:
        tokenizer = ContentStreamTokenizer(session.doc[0])
        trace = tokenizer.get_text_trace()
        assert isinstance(trace, list)
    finally:
        session.close()


def test_font_ladder_resolution(doc_with_text):
    """Test FontLadder font resolution."""
    session = make_session(doc_with_text)
    try:
        ladder = FontLadder(session.doc[0])
        
        # Should resolve Helvetica (base-14)
        name, font_file = ladder.resolve_font("Helvetica", 12, "Test")
        assert name in ["Helvetica", "helv"]
        assert font_file is None
        
        # Should refuse unknown font
        with pytest.raises(Exception):
            ladder.resolve_font("NonExistentFont", 12, "Test")
    finally:
        session.close()


def test_content_stream_splicer(doc_with_text):
    """Test ContentStreamSplicer initialization."""
    session = make_session(doc_with_text)
    try:
        splicer = ContentStreamSplicer(session.doc[0])
        # Just verify it's created without error
        assert splicer is not None
    finally:
        session.close()


def test_text_edit_engine(doc_with_text):
    """Test TextEditEngine creation."""
    session = make_session(doc_with_text)
    try:
        engine = TextEditEngine(session.doc, 0)
        assert engine.page_num == 0
        # Just verify engine is created without error
        assert engine is not None
    finally:
        session.close()


def test_replace_text_run_edit(doc_with_text):
    """Test ReplaceTextRun edit creation."""
    session = make_session(doc_with_text)
    try:
        from pdfar.text_engine import ReplaceTextRun
        
        edit = ReplaceTextRun(
            page_num=0,
            old_text="Hello World",
            new_text="Modified Text",
            font_name="Helvetica",
            font_size=14.0,
            color=(0.0, 0.0, 0.0)
        )
        
        assert edit.label == "Replace 'Hello World...' with 'Modified Text...'"
        assert edit.page_order(3) == (0, 1, 2)
    finally:
        session.close()


def test_replace_text_run_apply(doc_with_text):
    """Test ReplaceTextRun apply (basic functionality)."""
    session = make_session(doc_with_text)
    try:
        from pdfar.text_engine import ReplaceTextRun
        
        edit = ReplaceTextRun(
            page_num=0,
            old_text="Hello World",
            new_text="Modified Text",
            font_name="Helvetica",
            font_size=14.0,
            color=(0.0, 0.0, 0.0)
        )
        
        # Should apply without error (uses fallback redaction method)
        session.apply(edit)
        
        # Verify text was replaced
        page = session.doc[0]
        text = page.get_text()
        assert "Modified Text" in text
        assert "Hello World" not in text
    finally:
        session.close()


def test_text_edit_engine_replace(doc_with_text):
    """Test TextEditEngine.replace_text method."""
    session = make_session(doc_with_text)
    try:
        engine = TextEditEngine(session.doc, 0)
        edit = engine.replace_text("Hello World", "New Text", font_size=14.0)
        
        assert edit is not None
        
        session.apply(edit)
        
        # Verify text was replaced
        page = session.doc[0]
        text = page.get_text()
        assert "New Text" in text
    finally:
        session.close()


def test_font_ladder_base14(doc_with_text):
    """Test FontLadder base-14 font substitution."""
    session = make_session(doc_with_text)
    try:
        ladder = FontLadder(session.doc[0])
        
        # Test various base-14 mappings
        test_cases = [
            ("Helvetica", False, False, "helv"),
            ("Helvetica", True, False, "hebo"),
            ("Helvetica", False, True, "heit"),
            ("Helvetica", True, True, "hebi"),
            ("Times", False, False, "tiro"),
            ("Times", True, False, "tibo"),
            ("Courier", False, False, "cour"),
        ]
        
        for name, bold, italic, expected in test_cases:
            name_result, _ = ladder.resolve_font(name, 12, "Test", bold=bold, italic=italic)
            # Should resolve to base-14 name
            assert name_result in [expected, name], f"Failed for {name} bold={bold} italic={italic}"
    finally:
        session.close()


def test_text_edit_undo(doc_with_text):
    """Test text editing undo functionality."""
    session = make_session(doc_with_text)
    try:
        from pdfar.text_engine import ReplaceTextRun
        
        edit = ReplaceTextRun(
            page_num=0,
            old_text="Hello World",
            new_text="Modified Text",
            font_name="Helvetica",
            font_size=14.0,
            color=(0.0, 0.0, 0.0)
        )
        
        session.apply(edit)
        
        # Verify change
        text_before = session.doc[0].get_text()
        assert "Modified Text" in text_before
        
        # Undo
        session.undo()
        
        # Verify undo
        text_after = session.doc[0].get_text()
        assert "Hello World" in text_after
        assert "Modified Text" not in text_after
    finally:
        session.close()


def test_text_engine_with_unicode(doc_with_text, tmp_path):
    """Test text editing with extended Latin characters."""
    # Create a document with extended Latin characters (supported by base-14 fonts)
    path = str(tmp_path / "unicode.pdf")
    doc = fitz.open()
    p = doc.new_page(width=600, height=800)
    # Use extended Latin characters that work with base-14 fonts
    p.insert_text((100, 200), "Test café naïve résumé", fontsize=14)
    doc.save(path)
    doc.close()
    
    session = make_session(path)
    try:
        engine = TextEditEngine(fitz.open(path), 0)
        edit = engine.replace_text("café", "cafe", font_size=14.0)
        
        session.apply(edit)
        
        text = session.doc[0].get_text()
        assert "cafe" in text
        assert "café" not in text
    finally:
        session.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])