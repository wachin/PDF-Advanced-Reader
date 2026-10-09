"""Tests for P1-4 Resume Reading."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from PyQt6.QtWidgets import QMessageBox


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen test run forever."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_resume_reading_saves_page_position(win, tmp_path):
    """Test that page position is saved and restored."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=10)
    win.open_document(p)
    QApplication.processEvents()

    # Navigate to page 5
    win._goto(4)
    QApplication.processEvents()
    QApplication.processEvents()  # Allow scroll to complete
    assert win.view.current_page == 4

    # Close and reopen
    win._close_active_tab()
    assert win.tabs.count() == 0

    win.open_document(p)
    QApplication.processEvents()

    # Should be restored to page 5
    assert win.view.current_page == 4


def test_resume_reading_saves_zoom(win, tmp_path):
    """Test that zoom level is saved and restored."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)
    QApplication.processEvents()

    # Change zoom
    win.view.set_zoom(2.5)
    QApplication.processEvents()
    assert win.view.zoom == 2.5

    # Close and reopen
    win._close_active_tab()
    win.open_document(p)
    QApplication.processEvents()

    # Zoom should be restored
    assert win.view.zoom == 2.5


def test_resume_reading_saves_rotation(win, tmp_path):
    """Test that rotation is saved and restored."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)

    # Rotate
    win.view.set_rotation(90)
    QApplication.processEvents()
    assert win.view.rotation == 90

    # Close and reopen
    win._close_active_tab()
    win.open_document(p)
    QApplication.processEvents()

    # Rotation should be restored
    assert win.view.rotation == 90


def test_resume_reading_saves_fit_mode(win, tmp_path):
    """Test that fit mode is saved and restored."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)

    # Set fit mode
    win.view.set_fit_mode("page")
    QApplication.processEvents()
    assert win.view.fit_mode == "page"

    # Close and reopen
    win._close_active_tab()
    win.open_document(p)
    QApplication.processEvents()

    # Fit mode should be restored
    assert win.view.fit_mode == "page"


def test_resume_reading_independent_per_document(win, tmp_path):
    """Test that state is saved independently per document."""
    p1 = build_sample_pdf(tmp_path / "a.pdf", pages=5)
    p2 = build_sample_pdf(tmp_path / "b.pdf", pages=5)

    # Open first doc, go to page 3, zoom 2.0
    win.open_document(p1)
    QApplication.processEvents()
    win._goto(2)
    QApplication.processEvents()
    win.view.set_zoom(2.0)
    QApplication.processEvents()

    # Open second doc, go to page 1, zoom 1.5
    win.open_document(p2)
    QApplication.processEvents()
    win._goto(0)
    QApplication.processEvents()
    win.view.set_zoom(1.5)
    QApplication.processEvents()

    # Switch back to first doc
    win.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    QApplication.processEvents()  # Allow timer for state restoration to fire
    assert win.view.current_page == 2
    assert win.view.zoom == 2.0

    # Switch to second doc
    win.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    QApplication.processEvents()
    assert win.view.current_page == 0
    assert win.view.zoom == 1.5

    # Close and reopen both
    win._close_all_tabs()
    win.open_document(p1)
    win.open_document(p2)

    # First doc should have its state
    win.tabs.setCurrentIndex(0)
    QApplication.processEvents()
    QApplication.processEvents()
    assert win.view.current_page == 2
    assert win.view.zoom == 2.0

    # Second doc should have its state
    win.tabs.setCurrentIndex(1)
    QApplication.processEvents()
    QApplication.processEvents()
    assert win.view.current_page == 0
    assert win.view.zoom == 1.5


def test_resume_reading_scroll_position(win, tmp_path):
    """Test that scroll position is saved and restored."""
    import time
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=10)
    win.open_document(p)

    # Wait for document to load and layout to complete
    def layout_ready():
        return win.view.verticalScrollBar().maximum() > 0
    
    deadline = time.time() + 5.0
    while time.time() < deadline:
        QApplication.processEvents()
        if layout_ready():
            break
        time.sleep(0.05)

    # Scroll down
    win.view.verticalScrollBar().setValue(500)
    QApplication.processEvents()
    vscroll = win.view.verticalScrollBar().value()
    assert vscroll > 0

    # Close and reopen
    win._close_active_tab()
    win.open_document(p)
    QApplication.processEvents()

    # Scroll position should be restored (approximately)
    restored_vscroll = win.view.verticalScrollBar().value()
    # Allow some tolerance due to layout timing
    assert abs(restored_vscroll - vscroll) < 50


def test_resume_reading_on_window_close(win, tmp_path):
    """Test that state is saved when closing the main window."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)
    QApplication.processEvents()

    # Navigate to page 3
    win._goto(2)
    QApplication.processEvents()
    # Allow time for scroll to complete
    QApplication.processEvents()
    assert win.view.current_page == 2

    # Close window (which saves state)
    win.close()
    QApplication.processEvents()

    # Reopen in new window
    win2 = MainWindow()
    win2.show()
    QApplication.processEvents()
    win2.open_document(p)
    QApplication.processEvents()

    # State should be restored
    assert win2.view.current_page == 2

    win2._close_all_tabs()
    win2.close()
    win2.deleteLater()
    QApplication.processEvents()


def test_resume_reading_with_fit_width(win, tmp_path):
    """Test that fit width mode is correctly saved and restored."""
    p = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    win.open_document(p)
    QApplication.processEvents()

    # Set fit width
    win.view.set_fit_mode("width")
    QApplication.processEvents()
    QApplication.processEvents()
    assert win.view.fit_mode == "width"

    # Close and reopen
    win._close_active_tab()
    win.open_document(p)
    QApplication.processEvents()
    QApplication.processEvents()

    # Fit width should be restored
    assert win.view.fit_mode == "width"


from PyQt6.QtWidgets import QApplication