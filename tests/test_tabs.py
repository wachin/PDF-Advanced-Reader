"""Tests for P1-1 multi-document tabs."""

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
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w.close()


def test_open_three_pdfs_creates_three_tabs(win, tmp_path):
    for i in range(3):
        p = build_sample_pdf(tmp_path / f"doc{i}.pdf")
        assert win.open_document(p)
    assert win.tabs.count() == 3
    assert win.view is not None
    assert win.doc_path == str(tmp_path / "doc2.pdf")  # last opened is active


def test_tab_titles_show_basename(win, tmp_path):
    p = build_sample_pdf(tmp_path / "mydoc.pdf")
    win.open_document(p)
    assert win.tabs.tabText(0) == "mydoc.pdf"


def test_switch_tabs_preserves_independent_state(win, tmp_path):
    p1 = build_sample_pdf(tmp_path / "a.pdf", pages=5)
    p2 = build_sample_pdf(tmp_path / "b.pdf", pages=7)
    win.open_document(p1)
    win.open_document(p2)

    # navigate to page 3 in tab 0
    win.tabs.setCurrentIndex(0)
    win._goto(2)
    assert win.view.current_page == 2

    # switch to tab 1 and back — page must be preserved
    win.tabs.setCurrentIndex(1)
    assert win.view.current_page == 0
    win.tabs.setCurrentIndex(0)
    assert win.view.current_page == 2

    # page spinbox reflects the active tab's page count
    win.tabs.setCurrentIndex(1)
    assert win.page_spin.maximum() == 7


def test_zoom_is_independent_per_tab(win, tmp_path):
    p1 = build_sample_pdf(tmp_path / "a.pdf")
    p2 = build_sample_pdf(tmp_path / "b.pdf")
    win.open_document(p1)
    win.open_document(p2)

    win.tabs.setCurrentIndex(0)
    win._set_zoom(2.0)
    assert win.view.zoom == 2.0
    win.tabs.setCurrentIndex(1)
    assert win.view.zoom != 2.0


def test_close_tab_shuts_down_workers(win, tmp_path):
    p1 = build_sample_pdf(tmp_path / "a.pdf")
    p2 = build_sample_pdf(tmp_path / "b.pdf")
    win.open_document(p1)
    win.open_document(p2)

    tab_view = win.tabs.widget(0).view
    win.tabs.tabCloseRequested.emit(0)
    assert win.tabs.count() == 1
    assert not tab_view.pool.is_running()
    # active tab switched to the remaining one
    assert win.doc_path == p2


def test_reopen_same_file_switches_to_existing_tab(win, tmp_path):
    p = build_sample_pdf(tmp_path / "a.pdf")
    win.open_document(p)
    win.open_document(build_sample_pdf(tmp_path / "b.pdf"))
    win.open_document(p)  # same path again
    assert win.tabs.count() == 2
    assert win.tabs.currentIndex() == 0


def test_close_all_tabs_returns_to_empty_state(win, tmp_path):
    p = build_sample_pdf(tmp_path / "a.pdf")
    win.open_document(p)
    win.tabs.tabCloseRequested.emit(0)
    assert win.tabs.count() == 0
    assert win.view is None
    assert win.page_spin.maximum() == 1
    assert "No document" in win.status_left.text()


def test_close_window_shuts_down_all_tabs(win, tmp_path):
    pools = []
    for i in range(3):
        p = build_sample_pdf(tmp_path / f"d{i}.pdf")
        win.open_document(p)
        pools.append(win.tabs.widget(i).view.pool)
    win.close()
    for pool in pools:
        assert not pool.is_running()


def test_invalid_pdf_does_not_create_tab(win, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a pdf")
    assert not win.open_document(str(bad))
    assert win.tabs.count() == 0
