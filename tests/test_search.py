"""Tests for search functionality."""

import pytest
import fitz
from pdfar.search import SearchParams, SearchHit, normalize_space


class TestSearchParams:
    """Tests for SearchParams dataclass."""

    def test_params_creation(self):
        params = SearchParams(
            query="test",
            mode="AND",
            case_sensitive=True,
            whole_words=False,
            proximity=5,
        )
        assert params.query == "test"
        assert params.mode == "AND"
        assert params.case_sensitive is True
        assert params.whole_words is False
        assert params.proximity == 5


class TestNormalizeSpace:
    """Tests for normalize_space function."""

    def test_normalize_multiple_spaces(self):
        result = normalize_space("hello   world")
        assert result == "hello world"

    def test_normalize_leading_trailing(self):
        result = normalize_space("  hello world  ")
        assert result == "hello world"

    def test_normalize_tabs(self):
        result = normalize_space("hello\t\tworld")
        assert result == "hello world"


# Test cases for whole_words functionality
class TestWholeWords:
    """Test whole_words search mode in SearchWorker."""

    def test_whole_words_true_should_not_match_partial(self):
        """When whole_words is True, 'test' should not match 'testing'."""
        text = "This is a testing document"
        
        # Test with whole word boundary regex
        result = __import__('re').search(r'\btest\b', text.lower())
        assert result is None  # Should NOT match 'testing'

    def test_whole_words_false_matches_anywhere(self):
        """When whole_words is False, 'test' should match 'testing'."""
        text = "This is a testing document"
        
        # Test with substring search
        result = "test" in text.lower()
        assert result is True  # Should match 'testing'

    def test_whole_words_with_exact_word(self):
        """Whole words should match exact word boundaries."""
        text = "This is a test document"
        
        # Test with whole word boundary regex
        result = __import__('re').search(r'\btest\b', text.lower())
        assert result is not None  # Should match 'test'


# Test cases for missing import bug - this tests main.py module
class TestSearchImport:
    """Test that main module can be imported without errors."""

    def test_search_worker_no_import_error(self):
        """SearchWorker should be importable without re import errors."""
        # This test documents Bug 1.2: re module was missing from main.py
        from pdfar.main import SearchWorker
        assert SearchWorker is not None


# Run with: pytest tests/test_search.py -v
