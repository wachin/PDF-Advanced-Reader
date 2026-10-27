"""F10-1: the dirty mark, the Undo/Redo menu and refusing to lose work.

Everything here is about the promise the user sees: a document with changes
that are not in any file says so, and no way out of the application — tab
close, window close — can discard it silently.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from test_edits import AddText


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    # Teardown closes without asking: the dialogs are what the tests below
    # decide, and a modal here would block the offscreen run forever.
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def open_tab(win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2))
    assert win.open_document(path)
    return win._active_tab()


def _answer(monkeypatch, button):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: button))


def _silent(monkeypatch):
    """Swallow the message boxes a modal test run must never really show."""
    seen = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


# ------------------------------------------------------------------ indicators
def test_a_clean_document_shows_no_mark(win, open_tab):
    assert open_tab.dirty is False
    assert win.tabs.tabText(0) == open_tab.title
    assert win.isWindowModified() is False


def test_an_edit_marks_the_tab_and_the_window(qapp, win, open_tab):
    assert open_tab.view.apply_edit(AddText(0, "VISIBLE"))
    qapp.processEvents()

    assert open_tab.dirty is True
    assert win.tabs.tabText(0).startswith("•")
    assert open_tab.title in win.tabs.tabText(0)
    assert win.isWindowModified() is True


def test_the_mark_follows_the_active_tab(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "one.pdf", pages=1))
    second = str(build_sample_pdf(tmp_path / "two.pdf", pages=1))
    assert win.open_document(first) and win.open_document(second)
    tab_two = win._active_tab()
    assert win.tabs.count() == 2

    tab_two.view.apply_edit(AddText(0, "ONLY ON TWO"))
    qapp.processEvents()
    assert win.isWindowModified() is True

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.isWindowModified() is False        # the other document is clean
    assert win.tabs.tabText(1).startswith("•")

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.isWindowModified() is True


# ----------------------------------------------------------------- undo / redo
def test_the_menu_names_the_step_it_would_undo(qapp, win, open_tab):
    assert win.act_undo.isEnabled() is False
    assert win.act_undo.text() == "&Undo"

    open_tab.view.apply_edit(AddText(0, "STEP"))
    qapp.processEvents()
    assert win.act_undo.isEnabled() is True
    assert win.act_undo.text() == "&Undo Add text “STEP”"
    assert win.act_redo.isEnabled() is False

    win.act_undo.trigger()
    qapp.processEvents()
    assert "STEP" not in open_tab.view.doc.load_page(0).get_text()
    assert win.act_redo.text() == "&Redo Add text “STEP”"

    win.act_redo.trigger()
    qapp.processEvents()
    assert "STEP" in open_tab.view.doc.load_page(0).get_text()


def test_an_ampersand_in_a_label_stays_text(qapp, win, open_tab):
    open_tab.view.apply_edit(AddText(0, "A & B"))
    qapp.processEvents()
    assert win.act_undo.text() == '&Undo Add text “A && B”'


# ------------------------------------------------------------- closing documents
def test_closing_a_clean_document_does_not_ask(qapp, win, open_tab, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a clean document must not be questioned")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(boom))
    assert win._close_tab(0) is True
    qapp.processEvents()
    assert win.tabs.count() == 0


def test_cancel_keeps_a_dirty_document_open(qapp, win, open_tab, monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    open_tab.view.apply_edit(AddText(0, "KEEP ME"))
    qapp.processEvents()

    assert win._close_tab(0) is False
    assert win.tabs.count() == 1
    assert win.isWindowModified() is True


def test_discarding_closes_and_the_work_is_gone(qapp, win, open_tab, monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.Discard)
    open_tab.view.apply_edit(AddText(0, "DROP ME"))
    qapp.processEvents()

    assert win._close_tab(0) is True
    assert win.tabs.count() == 0
    # Discarding the session cannot touch the file: the edit was never written.
    with fitz.open(open_tab.doc_path) as on_disk:
        assert "DROP ME" not in on_disk.load_page(0).get_text()


def test_the_dialog_saves_in_place_and_a_save_clears_the_mark(qapp, win, open_tab,
                                                             monkeypatch):
    """Since F10-2 the dialog's Save means Save: the file it names is the answer."""
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _silent(monkeypatch)
    path = open_tab.doc_path
    open_tab.view.apply_edit(AddText(0, "SAVED"))
    qapp.processEvents()

    assert win._close_tab(0) is True               # saving succeeded, so close
    assert win.tabs.count() == 0
    with fitz.open(path) as saved:
        assert "SAVED" in saved.load_page(0).get_text()


def test_a_refused_save_does_not_close(qapp, win, open_tab, monkeypatch):
    """A write that fails must keep the tab open with its work and its mark."""
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    seen = _silent(monkeypatch)
    open_tab.view.apply_edit(AddText(0, "UNSAVED"))
    qapp.processEvents()
    os.chmod(open_tab.doc_path, 0o444)
    try:
        assert win._close_tab(0) is False
    finally:
        os.chmod(open_tab.doc_path, 0o644)

    assert win.tabs.count() == 1
    assert open_tab.dirty is True
    assert seen, "the user must be told why nothing was saved"


def test_saving_through_the_dialog_clears_the_mark(qapp, win, open_tab, monkeypatch):
    """The dialog's write retargets nothing: the document keeps its own file."""
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    _silent(monkeypatch)
    original = open_tab.doc_path
    open_tab.view.apply_edit(AddText(0, "IN THE FILE"))
    qapp.processEvents()
    assert open_tab.dirty is True

    assert win._confirm_discard_all() is True
    assert open_tab.dirty is False
    assert win.isWindowModified() is False
    assert win.tabs.tabText(0) == open_tab.title
    assert open_tab.doc_path == original
    with fitz.open(original) as doc:
        assert "IN THE FILE" in doc.load_page(0).get_text()


def test_the_window_refuses_to_close_on_cancel(qapp, win, open_tab, monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.Cancel)
    open_tab.view.apply_edit(AddText(0, "STILL HERE"))
    qapp.processEvents()

    win.close()
    qapp.processEvents()
    assert win.isVisible() is True
    assert win.tabs.count() == 1
