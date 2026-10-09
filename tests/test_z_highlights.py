"""Tests for P4-1 Highlight Annotations."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.annotations import (
    AnnotationManager,
    AnnotationType,
    HIGHLIGHT_COLORS,
)
from PyQt6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen test run forever."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(
            QMessageBox,
            name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok),
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


def test_annotation_manager_add_get_highlight(tmp_path):
    """Test AnnotationManager creating and querying highlights."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    assert mgr.get_annotations() == []

    hl = mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    assert hl.page == 0
    assert hl.type == AnnotationType.HIGHLIGHT
    assert hl.color == "#FFFF00"
    assert len(hl.rects) == 1

    # query
    page0_hls = mgr.get_highlights_for_page(0)
    assert len(page0_hls) == 1
    assert page0_hls[0].id == hl.id

    page1_hls = mgr.get_highlights_for_page(1)
    assert len(page1_hls) == 0

    doc.close()


def test_annotation_manager_update_and_remove(tmp_path):
    """Test updating and removing annotations."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    hl = mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    ann_id = hl.id

    # update color
    updated = mgr.update_annotation(ann_id, color="#90EE90")
    assert updated is not None
    assert updated.color == "#90EE90"
    assert mgr.get_highlights_for_page(0)[0].color == "#90EE90"

    # remove
    assert mgr.remove_annotation(ann_id) is True
    assert len(mgr.get_annotations()) == 0
    assert mgr.remove_annotation("non-existent") is False

    doc.close()


def test_save_and_reload_highlights_from_pdf(tmp_path):
    """Test that highlights saved to PDF can be loaded back."""
    import pymupdf as fitz

    p = build_sample_pdf(tmp_path / "test.pdf", pages=3)
    doc = fitz.open(p)
    mgr = AnnotationManager(p, doc)

    mgr.add_highlight(0, [(10.0, 10.0, 50.0, 20.0)], color="#FFFF00")
    mgr.add_highlight(1, [(20.0, 30.0, 80.0, 45.0)], color="#FF6B6B")

    # save to PDF
    assert mgr.save_to_pdf() is True
    doc.close()

    # reopen with a new doc and manager
    doc2 = fitz.open(p)
    mgr2 = AnnotationManager(p, doc2)

    hls_p0 = mgr2.get_highlights_for_page(0)
    hls_p1 = mgr2.get_highlights_for_page(1)

    assert len(hls_p0) == 1
    assert len(hls_p1) == 1
    assert len(mgr2.get_annotations()) == 2

    doc2.close()


def test_create_highlight_from_selection(win, tmp_path):
    """Test creating a highlight directly from text selection in the view."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    view = win.view
    assert view is not None

    # select all text
    view.select_all()
    assert view.has_selection()

    # create highlight via view method
    created = view.create_highlight_from_selection("#87CEEB")
    assert created is True
    # selection should be cleared after highlighting
    assert not view.has_selection()

    # verify highlight in manager
    hls = view.annotation_manager.get_highlights_for_page(0)
    assert len(hls) >= 1
    assert hls[0].color == "#87CEEB"


def test_cycle_highlight_colors(win, tmp_path):
    """Test cycling through highlight color palette."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)
    view = win.view

    default_col = view.get_highlight_color()
    assert default_col == HIGHLIGHT_COLORS[0][1]

    next_col = view.next_highlight_color()
    assert next_col == HIGHLIGHT_COLORS[1][1]

    view.set_highlight_color(HIGHLIGHT_COLORS[3][1])
    assert view.get_highlight_color() == HIGHLIGHT_COLORS[3][1]


def test_sidebar_annotations_tab_and_tree(win, tmp_path):
    """Test that sidebar shows the annotations tab and lists created highlights."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    sidebar = win.sidebar
    assert sidebar is not None

    # Check tab exists
    tab_names = [sidebar.tabs.tabText(i) for i in range(sidebar.tabs.count())]
    assert "Annotations" in tab_names

    # Add a highlight
    win.view.select_all()
    win.view.create_highlight_from_selection("#FFFF00")

    # The sidebar annotations tree should now have items
    assert sidebar.annotations_tree.topLevelItemCount() >= 1


def test_main_window_highlight_action(win, tmp_path):
    """Test invoking highlight creation and save via MainWindow actions."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(p)

    win.view.select_all()
    # Trigger action
    win.act_highlight.trigger()

    hls = win.view.annotation_manager.get_annotations()
    assert len(hls) >= 1

    # Trigger save action
    win.act_save_annotations.trigger()
    assert "saved" in win.status_left.text().lower()
