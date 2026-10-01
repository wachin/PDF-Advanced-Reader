"""Smoke tests for the main window wiring (headless)."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from pdfar.main import MainWindow


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen test run forever."""
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def wait_until(app, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_open_document_wires_everything(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        assert win.view is not None
        assert win.view.page_count == 3
        assert win.page_label.text() == "/ 3"
        assert win.page_spin.maximum() == 3
        assert win.sidebar is not None
        assert win.sidebar.thumbs.count() == 3

        # pages really render through the whole MainWindow path
        assert wait_until(qapp, lambda: win.view.cache.get(0, win.view.zoom, win.view.rotation) is not None)
        assert win.sidebar.thumbs.count() == 3

        # navigation updates the page spinner
        win._goto(2)
        qapp.processEvents()
        assert win.page_spin.value() == 3
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_search_through_the_dock(qapp, sample_pdf):
    """1.x search never ran at all (the worker was never started)."""
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        win.query_edit.setText("alpha beta")
        win.mode.setCurrentText("AND")
        win.start_search()

        assert wait_until(qapp, lambda: win.search_thread is not None and not win.search_thread.isRunning())
        assert wait_until(qapp, lambda: win.results.count() > 0)
        assert win.results.count() == 2          # pages 2 and 3 of the sample
        assert win.view._hit_flat, "search highlights should be set on the view"
        assert win.hit_label.text().startswith("Hit")
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_zoom_preset_and_rotation_actions(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(sample_pdf)
        win.zoom_combo.setCurrentText("150%")
        qapp.processEvents()
        assert abs(win.view.zoom - 1.5) < 1e-6
        assert win.zoom_label.text() == "150%"

        win._rotate(90)
        qapp.processEvents()
        assert win.view.rotation == 90
        win._rotate(90)
        qapp.processEvents()
        assert win.view.rotation == 180
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_recent_files_menu(qapp, sample_pdf):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(sample_pdf) is True
        actions = win.menu_recent.actions()
        assert any(a.data() == sample_pdf for a in actions), "opened file should be in recent list"
        act = next(a for a in actions if a.data() == sample_pdf)
        act.trigger()
        qapp.processEvents()
        assert win.doc_path == sample_pdf
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_open_missing_file_is_rejected(qapp, tmp_path):
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.open_document(str(tmp_path / "does-not-exist.pdf")) is False
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()
