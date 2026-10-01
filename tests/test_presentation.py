"""Tests for P1-2 presentation mode."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtTest import QTest
from PyQt6.QtCore import Qt

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


def _doc(win, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "pres.pdf", pages=5))


def test_enter_presentation_hides_chrome(win, qapp, tmp_path):
    _doc(win, tmp_path)
    win.toggle_presentation()
    qapp.processEvents()
    try:
        assert win._presentation is True
        assert win.isFullScreen()
        assert not win.toolbar.isVisible()
        assert not win.statusBar().isVisible()
        assert not win.search_dock.isVisible()
        assert not win.side_dock.isVisible()
        assert not win.tabs.tabBar().isVisible()
    finally:
        win.toggle_presentation()


def test_exit_presentation_restores_chrome(win, qapp, tmp_path):
    _doc(win, tmp_path)
    toolbar_before = win.toolbar.isVisible()
    dock_before = win.side_dock.isVisible()
    win.toggle_presentation()
    win.toggle_presentation()
    qapp.processEvents()
    assert win._presentation is False
    assert not win.isFullScreen()
    assert win.toolbar.isVisible() == toolbar_before
    assert win.side_dock.isVisible() == dock_before


def test_presentation_sets_fit_page_and_restores(win, qapp, tmp_path):
    _doc(win, tmp_path)
    win._set_fit("width")
    win.toggle_presentation()
    try:
        assert win.view.fit_mode == "page"
    finally:
        win.toggle_presentation()
    assert win.view.fit_mode == "width"


def test_presentation_page_navigation(win, qapp, tmp_path):
    _doc(win, tmp_path)
    win.toggle_presentation()
    try:
        win._goto(1)
        qapp.processEvents()
        assert win.view.current_page == 1
        win._goto(0)
        qapp.processEvents()
        assert win.view.current_page == 0
    finally:
        win.toggle_presentation()


def test_escape_key_exits_presentation(win, qapp, tmp_path):
    _doc(win, tmp_path)
    win.toggle_presentation()
    QTest.keyClick(win, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert win._presentation is False


def test_escape_without_presentation_is_noop(win):
    QTest.keyClick(win, Qt.Key.Key_Escape)
    assert win._presentation is False


def test_presentation_close_window_shuts_down(win, qapp, tmp_path):
    _doc(win, tmp_path)
    pool = win.view.pool
    win.toggle_presentation()
    win.close()
    assert not pool.is_running()


def test_presentation_requires_document(win):
    win.toggle_presentation()
    assert win._presentation is False
