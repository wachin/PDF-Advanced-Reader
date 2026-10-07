"""Tests for P2-1 bookmarks."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.bookmarks import Bookmark, BookmarkManager
from pdfar.sidebar import Sidebar
from PyQt6.QtWidgets import QMessageBox, QInputDialog


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen test run forever."""
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def mock_input_dialog(monkeypatch):
    """Mock QInputDialog.getText to return a test bookmark title."""
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("Test Bookmark", True)))


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_bookmark_manager_creates_user_bookmark(tmp_path):
    """Test BookmarkManager basic operations."""
    pdf_path = build_sample_pdf(tmp_path / "test.pdf", pages=5)
    import pymupdf as fitz
    doc = fitz.open(pdf_path)

    mgr = BookmarkManager(pdf_path, doc)

    # Initially no user bookmarks
    assert mgr.get_user_bookmarks() == []

    # Add a bookmark
    bm = mgr.add_user_bookmark("Chapter 1", 0)
    assert bm.title == "Chapter 1"
    assert bm.page == 0
    assert bm.is_user is True
    assert bm.level == 1

    user_bms = mgr.get_user_bookmarks()
    assert len(user_bms) == 1
    assert user_bms[0].title == "Chapter 1"

    # Add another bookmark
    mgr.add_user_bookmark("Chapter 2", 2)
    user_bms = mgr.get_user_bookmarks()
    assert len(user_bms) == 2

    # Test persistence - create new manager with same path
    doc.close()
    doc2 = fitz.open(pdf_path)
    mgr2 = BookmarkManager(pdf_path, doc2)
    user_bms2 = mgr2.get_user_bookmarks()
    assert len(user_bms2) == 2
    assert user_bms2[0].title == "Chapter 1"
    assert user_bms2[1].title == "Chapter 2"

    doc2.close()


def test_bookmark_manager_outline_bookmarks(tmp_path):
    """Test that PDF outline bookmarks are loaded."""
    pdf_path = build_sample_pdf(tmp_path / "test.pdf", pages=5)
    import pymupdf as fitz
    doc = fitz.open(pdf_path)

    # Add a TOC to the PDF
    doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 3]])

    mgr = BookmarkManager(pdf_path, doc)
    outline = mgr.get_outline_bookmarks()

    assert len(outline) == 3
    assert outline[0].title == "Chapter 1"
    assert outline[0].page == 0
    assert outline[0].level == 1
    assert outline[1].title == "Section 1.1"
    assert outline[1].page == 1
    assert outline[1].level == 2
    assert outline[2].title == "Chapter 2"
    assert outline[2].page == 2
    assert outline[2].level == 1

    doc.close()


def test_bookmark_manager_all_bookmarks(tmp_path):
    """Test get_all_bookmarks returns user + outline."""
    pdf_path = build_sample_pdf(tmp_path / "test.pdf", pages=5)
    import pymupdf as fitz
    doc = fitz.open(pdf_path)
    doc.set_toc([[1, "Outline Chapter", 2]])

    mgr = BookmarkManager(pdf_path, doc)
    mgr.add_user_bookmark("My Bookmark", 0)

    all_bms = mgr.get_all_bookmarks()
    assert len(all_bms) == 2
    # User bookmarks come first
    assert all_bms[0].is_user is True
    assert all_bms[0].title == "My Bookmark"
    assert all_bms[1].is_user is False
    assert all_bms[1].title == "Outline Chapter"

    doc.close()


def test_bookmark_manager_rename_delete(tmp_path):
    """Test renaming and deleting user bookmarks."""
    pdf_path = build_sample_pdf(tmp_path / "test.pdf", pages=5)
    import pymupdf as fitz
    doc = fitz.open(pdf_path)

    mgr = BookmarkManager(pdf_path, doc)
    mgr.add_user_bookmark("Old Title", 1)
    mgr.add_user_bookmark("Another", 3)

    # Rename
    assert mgr.rename_user_bookmark(0, "New Title") is True
    assert mgr.get_user_bookmarks()[0].title == "New Title"

    # Delete
    assert mgr.remove_user_bookmark(0) is True
    assert len(mgr.get_user_bookmarks()) == 1
    assert mgr.get_user_bookmarks()[0].title == "Another"

    # Delete out of bounds
    assert mgr.remove_user_bookmark(5) is False

    doc.close()


def test_sidebar_bookmarks_tab(win, tmp_path):
    """Test that sidebar has bookmarks tab and shows bookmarks."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    sidebar = win.sidebar
    assert sidebar is not None

    # Check bookmarks tab exists (now 4 tabs: Thumbnails, Index, Bookmarks, Annotations)
    tab_count = sidebar.tabs.count()
    assert tab_count == 4

    # Find bookmarks tab index
    bookmarks_tab_idx = -1
    for i in range(tab_count):
        if sidebar.tabs.tabText(i) == "Bookmarks":
            bookmarks_tab_idx = i
            break
    assert bookmarks_tab_idx >= 0

    # Switch to bookmarks tab
    sidebar.tabs.setCurrentIndex(bookmarks_tab_idx)
    assert sidebar.tabs.currentWidget() is sidebar.bookmarks_tree


def test_add_bookmark_via_shortcut(win, tmp_path, mock_input_dialog):
    """Test adding a bookmark via the main window action."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)

    # Navigate to page 2
    win._goto(2)
    assert win.view.current_page == 2

    # Add bookmark via action
    win._add_bookmark()

    # Check bookmark was added
    sidebar = win.sidebar
    user_bms = sidebar.bookmark_manager.get_user_bookmarks()
    assert len(user_bms) == 1
    assert user_bms[0].page == 2
    assert user_bms[0].title == "Test Bookmark"


def test_bookmark_persistence_across_reopen(win, tmp_path, mock_input_dialog):
    """Test that user bookmarks persist when reopening the same file."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)

    win._goto(1)
    win._add_bookmark()  # Adds "Test Bookmark" at page 1

    win._goto(3)
    win._add_bookmark()  # Adds another at page 3

    user_bms = win.sidebar.bookmark_manager.get_user_bookmarks()
    assert len(user_bms) == 2

    # Close and reopen
    win._close_active_tab()
    assert win.tabs.count() == 0

    win.open_document(p)

    # Bookmarks should be restored
    user_bms = win.sidebar.bookmark_manager.get_user_bookmarks()
    assert len(user_bms) == 2
    assert user_bms[0].page == 1
    assert user_bms[1].page == 3


def test_bookmark_independent_per_document(win, tmp_path, mock_input_dialog):
    """Test that bookmarks are independent per document."""
    p1 = build_sample_pdf(tmp_path / "a.pdf", pages=3)
    p2 = build_sample_pdf(tmp_path / "b.pdf", pages=3)

    win.open_document(p1)
    win._goto(1)
    win._add_bookmark()

    win.open_document(p2)
    win._goto(2)
    win._add_bookmark()

    # Switch back to first doc
    win.tabs.setCurrentIndex(0)
    bms1 = win.sidebar.bookmark_manager.get_user_bookmarks()
    assert len(bms1) == 1
    assert bms1[0].page == 1

    # Switch to second doc
    win.tabs.setCurrentIndex(1)
    bms2 = win.sidebar.bookmark_manager.get_user_bookmarks()
    assert len(bms2) == 1
    assert bms2[0].page == 2


def test_outline_bookmarks_displayed(win, tmp_path):
    """Test that PDF outline bookmarks are displayed in sidebar."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    import pymupdf as fitz
    doc = fitz.open(p)
    doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2], [1, "Chapter 2", 4]])
    doc.save(p, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    doc.close()

    win.open_document(p)

    # Check outline bookmarks are in the bookmarks tree
    sidebar = win.sidebar
    all_bms = sidebar.bookmark_manager.get_all_bookmarks()
    outline_bms = [b for b in all_bms if not b.is_user]
    assert len(outline_bms) == 3
    assert outline_bms[0].title == "Chapter 1"
    assert outline_bms[1].title == "Section 1.1"
    assert outline_bms[2].title == "Chapter 2"