"""Tests for P4-2 Notes on Highlights."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.annotations import (
    AnnotationManager,
    HighlightAnnotation,
    NoteAnnotation,
    AnnotationType,
)
from PyQt6.QtWidgets import QMessageBox, QInputDialog


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox and QInputDialog would block the offscreen test run forever."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
        )
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *a, **k: ("Test note content", True)),
    )


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_highlight_annotation_note_methods(tmp_path):
    """Test HighlightAnnotation note getter/setter."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    hl = mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    ann_id = hl.id

    # Initially no note
    assert not hl.has_note()
    assert hl.get_note() == ""

    # Set note
    hl.set_note("This is a test note")
    assert hl.has_note()
    assert hl.get_note() == "This is a test note"

    # Update note
    hl.set_note("Updated note")
    assert hl.get_note() == "Updated note"

    # Clear note
    hl.set_note("")
    assert not hl.has_note()

    doc.close()


def test_annotation_manager_add_note_to_highlight(tmp_path):
    """Test adding notes to highlights via AnnotationManager."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    hl = mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    ann_id = hl.id

    # Add note via manager
    result = mgr.add_note_to_highlight(ann_id, "Note from manager")
    assert result is not None
    assert result.id == ann_id
    assert result.get_note() == "Note from manager"

    # Get note
    note = mgr.get_note_for_highlight(ann_id)
    assert note == "Note from manager"

    # Remove note
    assert mgr.remove_note_from_highlight(ann_id) is True
    assert mgr.get_note_for_highlight(ann_id) == ""

    doc.close()


def test_note_persistence_in_pdf(tmp_path):
    """Test that notes saved to PDF are loaded back."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    hl = mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    hl.set_note("Persistent note")

    # Save to PDF
    assert mgr.save_to_pdf() is True
    doc.close()

    # Reload
    doc2 = fitz.open(p)
    mgr2 = AnnotationManager(p, doc2)

    hls = mgr2.get_highlights_for_page(0)
    assert len(hls) == 1
    assert hls[0].has_note()
    assert hls[0].get_note() == "Persistent note"

    doc2.close()


def test_sidebar_shows_notes(win, tmp_path):
    """Test that sidebar annotations tree shows note indicators."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    sidebar = win.sidebar
    assert sidebar is not None

    # Create a highlight
    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")

    # Add a note via the annotation manager
    hls = win.view.annotation_manager.get_highlights_for_page(0)
    assert len(hls) >= 1
    hl = hls[0]
    win.view.annotation_manager.add_note_to_highlight(hl.id, "Sidebar test note")

    # Check tree shows note indicator
    # The tree should have an item with the note indicator
    assert sidebar.annotations_tree.topLevelItemCount() >= 1


def test_main_window_edit_note_via_sidebar(win, tmp_path, monkeypatch):
    """Test editing a note through the sidebar context menu."""
    # Mock the input dialog to return a specific note
    monkeypatch.setattr(
        QInputDialog,
        "getMultiLineText",
        staticmethod(lambda *a, **k: ("Edited via sidebar", True)),
    )

    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    # Create a highlight
    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")
    hls = win.view.annotation_manager.get_highlights_for_page(0)
    assert len(hls) >= 1
    hl = hls[0]

    # Initially no note
    assert not hl.has_note()

    # Edit note via sidebar (simulate context menu -> Edit Note)
    win.sidebar._edit_note(hl.id)

    # Check note was added
    assert hl.has_note()
    assert hl.get_note() == "Edited via sidebar"


def test_remove_note_via_sidebar(win, tmp_path):
    """Test removing a note via sidebar context menu."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    # Create a highlight with a note
    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")
    hls = win.view.annotation_manager.get_highlights_for_page(0)
    hl = hls[0]
    win.view.annotation_manager.add_note_to_highlight(hl.id, "Note to remove")
    assert hl.has_note()

    # Remove note via sidebar
    win.sidebar._remove_note(hl.id)

    # Check note was removed
    assert not hl.has_note()


def test_note_in_annotations_tree(win, tmp_path):
    """Test that notes appear in the annotations tree with indicator."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")
    hls = win.view.annotation_manager.get_highlights_for_page(0)
    hl = hls[0]
    win.view.annotation_manager.add_note_to_highlight(hl.id, "Tree visible note")

    sidebar = win.sidebar

    # Find the annotation item in the tree
    def find_ann_item(tree, target_id):
        for i in range(tree.topLevelItemCount()):
            page_item = tree.topLevelItem(i)
            for j in range(page_item.childCount()):
                child = page_item.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == target_id:
                    return child
        return None

    from PyQt6.QtCore import Qt
    item = find_ann_item(sidebar.annotations_tree, hl.id)
    assert item is not None
    # Check that the note indicator (💬) is in the text
    assert "💬" in item.text(0)
    assert "Tree visible note" in item.text(0)


def test_multiple_highlights_different_notes(win, tmp_path):
    """Test that different highlights can have different notes."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    # Create highlights on different pages
    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")
    hls_p0 = win.view.annotation_manager.get_highlights_for_page(0)
    assert len(hls_p0) >= 1

    win._goto(1)
    win.view.select_all()
    win.view.create_highlight_from_selection("#90EE90")
    hls_p1 = win.view.annotation_manager.get_highlights_for_page(1)
    assert len(hls_p1) >= 1

    # Add different notes
    hl0 = hls_p0[0]
    hl1 = hls_p1[0]

    win.view.annotation_manager.add_note_to_highlight(hl0.id, "Note on page 1")
    win.view.annotation_manager.add_note_to_highlight(hl1.id, "Note on page 2")

    assert hl0.get_note() == "Note on page 1"
    assert hl1.get_note() == "Note on page 2"


def test_note_saved_with_highlight_color(win, tmp_path):
    """Test that note is saved along with highlight color."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    win.view.select_all()
    win.view.create_highlight_from_selection("#FF6B6B")  # Red
    hls = win.view.annotation_manager.get_highlights_for_page(0)
    hl = hls[0]
    win.view.annotation_manager.add_note_to_highlight(hl.id, "Red highlight note")

    # Save
    win.view.save_annotations()
    win._close_active_tab()

    # Reopen
    win.open_document(p)
    hls2 = win.view.annotation_manager.get_highlights_for_page(0)
    assert len(hls2) >= 1
    hl2 = hls2[0]
    # Color may be stored as lowercase in PDF
    assert hl2.color.lower() == "#ff6b6b"
    assert hl2.has_note()
    assert hl2.get_note() == "Red highlight note"