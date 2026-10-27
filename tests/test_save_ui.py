"""F10-2: the Save / Save As / Revert commands the user can actually reach.

The disk-facing logic is ``tests/test_save.py``; this file is about the window:
that the commands are offered only when they can do something, that a refusal
reaches the user instead of a silent ``False``, that Save As carries the
per-document state with the file, and that nothing is thrown away without asking.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from conftest import build_sample_pdf
from pdfar.bookmarks import settings_key_for
from pdfar.main import MainWindow, view_state_key
from test_edits import AddText
from test_save import build_encrypted_pdf


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def open_tab(win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2))
    assert win.open_document(path)
    return win._active_tab()


@pytest.fixture
def messages(monkeypatch):
    """Capture the dialogs a modal test run must never actually show."""
    seen = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


def _answer(monkeypatch, button):
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: button))


def _save_to(monkeypatch, path):
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "PDF Files (*.pdf)")))


def disk_text(path, page=0):
    with fitz.open(str(path)) as doc:
        return doc.load_page(page).get_text()


# ---------------------------------------------------------------- Save
def test_save_needs_a_document_but_not_a_dirty_one(win, open_tab):
    """Save is not gated on the dirty mark.

    A highlight the user just made lives in the annotation manager and only
    reaches the document when Save syncs it, so the session cannot report
    "there is something to save" until F10-3 routes annotations through it.
    Gating here would switch Ctrl+S off on exactly the document the user is
    working on; the command asks the writer and says "Nothing to save" instead.
    """
    assert open_tab.dirty is False
    assert win.act_save.isEnabled() is True
    assert win.act_save_as.isEnabled() is True

    win._close_tab(0, confirm=False)
    assert win.act_save.isEnabled() is False
    assert win.act_save_as.isEnabled() is False


def test_saving_a_document_that_never_changed_says_so(qapp, win, open_tab):
    assert win._save_tab(open_tab) is True
    assert win.status_left.text() == "Nothing to save"


def test_save_writes_the_document_and_clears_the_mark(qapp, win, open_tab):
    open_tab.view.apply_edit(AddText(0, "ON DISK"))
    qapp.processEvents()

    assert win._save_tab(open_tab) is True
    qapp.processEvents()

    assert "ON DISK" in disk_text(open_tab.doc_path)
    assert open_tab.dirty is False
    assert win.tabs.tabText(0) == open_tab.title
    assert win.isWindowModified() is False


def test_a_refused_save_reaches_the_user_and_keeps_the_mark(qapp, win, open_tab,
                                                           messages):
    open_tab.view.apply_edit(AddText(0, "NOWHERE"))
    qapp.processEvents()
    os.chmod(open_tab.doc_path, 0o444)
    try:
        assert win._save_tab(open_tab) is False
    finally:
        os.chmod(open_tab.doc_path, 0o644)

    assert messages, "a failed save must not be silent"
    assert open_tab.dirty is True
    assert "NOWHERE" not in disk_text(open_tab.doc_path)


def test_save_still_writes_the_highlights_the_user_made(qapp, win, open_tab):
    """Ctrl+S used to mean "save my highlights"; it must not stop meaning that."""
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [(10.0, 55.0, 120.0, 66.0)], color="#FFFF00")

    assert win._save_tab(open_tab) is True

    with fitz.open(open_tab.doc_path) as doc:
        kinds = [a.type[0] for a in doc.load_page(0).annots()]
    assert fitz.PDF_ANNOT_HIGHLIGHT in kinds


def test_the_close_dialog_saves_in_place_once_saving_is_safe(qapp, win, open_tab,
                                                            monkeypatch):
    """F10-1 could only offer Save As; F10-2 makes the plain answer correct."""
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    open_tab.view.apply_edit(AddText(0, "KEEP"))
    qapp.processEvents()

    assert win._close_tab(0) is True
    assert win.tabs.count() == 0
    assert "KEEP" in disk_text(open_tab.doc_path)


# ------------------------------------------------------------- Save As
def test_save_as_writes_a_copy_and_switches_the_document_to_it(qapp, win, open_tab,
                                                              monkeypatch, tmp_path):
    target = str(tmp_path / "copy.pdf")
    _save_to(monkeypatch, target)
    original = open_tab.doc_path
    open_tab.view.apply_edit(AddText(0, "COPY ONLY"))

    assert win._save_tab_as(open_tab) is True
    qapp.processEvents()

    assert open_tab.doc_path == target
    assert "COPY ONLY" in disk_text(target)
    assert "COPY ONLY" not in disk_text(original)
    assert win.tabs.tabText(0) == "copy.pdf"
    assert open_tab.dirty is False


def test_save_as_carries_the_per_document_state_with_the_file(qapp, win, open_tab,
                                                             monkeypatch, tmp_path):
    """Both stores are keyed by path; a rename must not silently orphan them."""
    settings = QSettings("PDFAR", "PDFAR")
    old = open_tab.doc_path
    settings.setValue(view_state_key(old), {"page": 2, "zoom": 1.5})
    settings.setValue(settings_key_for(old), [{"title": "pinned", "page": 1}])
    settings.sync()

    target = str(tmp_path / "moved.pdf")
    _save_to(monkeypatch, target)
    assert win._save_tab_as(open_tab) is True
    qapp.processEvents()

    assert settings.value(view_state_key(target)) == {"page": 2, "zoom": 1.5}
    assert settings.value(settings_key_for(target)) == [{"title": "pinned", "page": 1}]
    assert settings.value(view_state_key(old)) is None
    assert settings.value(settings_key_for(old)) is None


def test_a_cancelled_save_as_keeps_the_document_where_it_was(qapp, win, open_tab,
                                                            monkeypatch):
    _save_to(monkeypatch, "")
    open_tab.view.apply_edit(AddText(0, "HOME"))

    assert win._save_tab_as(open_tab) is False
    assert open_tab.doc_path.endswith("doc.pdf")
    assert open_tab.dirty is True


def test_backing_out_of_save_as_is_not_a_failure(qapp, win, open_tab, monkeypatch,
                                                messages):
    _save_to(monkeypatch, "")
    assert win._save_tab_as(open_tab) is False
    assert messages == []


def test_save_as_of_a_document_pdfar_cannot_reprotect_still_honours_the_target(
        qapp, win, monkeypatch, tmp_path):
    """The retry after a protection refusal goes where the user asked, not home.

    An owner-only file guards changes with a password PDFAR never saw, so a full
    rewrite has to refuse first; the copy the user then approves must land on the
    name they chose, and the original must not gain the edit.
    """
    source = build_encrypted_pdf(tmp_path / "locked.pdf", "pw-owner", owner=True)
    assert win.open_document(source)
    tab = win._active_tab()
    assert tab is not None
    _save_to(monkeypatch, tmp_path / "copy.pdf")
    _answer(monkeypatch, QMessageBox.StandardButton.Save)
    tab.view.apply_edit(AddText(0, "WEAK COPY"))
    qapp.processEvents()

    assert win._save_tab_as(tab) is True
    qapp.processEvents()

    assert tab.doc_path == str(tmp_path / "copy.pdf")
    assert "WEAK COPY" in disk_text(tab.doc_path)
    assert "WEAK COPY" not in disk_text(source)
    with fitz.open(tab.doc_path) as doc:
        assert (doc.metadata or {}).get("encryption"), "the copy lost its password"
    win._close_tab(0, confirm=False)


def test_saving_twice_writes_once(qapp, win, open_tab):
    """The second Ctrl+S must not rewrite a document that already matches."""
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [(10.0, 55.0, 120.0, 66.0)])
    assert win._save_tab(open_tab) is True
    written = os.stat(open_tab.doc_path).st_mtime_ns

    assert win._save_tab(open_tab) is True
    qapp.processEvents()

    assert win.status_left.text() == "Nothing to save"
    assert os.stat(open_tab.doc_path).st_mtime_ns == written


def test_a_new_document_object_forces_the_highlights_back_in(open_tab):
    """Undo hands back a different Document, so "already written" must expire."""
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [(10.0, 55.0, 120.0, 66.0)])
    assert manager.sync_to_document() is True
    assert manager.sync_to_document() is False

    open_tab.view.apply_edit(AddText(0, "NEW OBJECT"))

    assert manager.sync_to_document() is True


def test_the_sidebars_save_button_uses_the_windows_write(qapp, win, open_tab,
                                                        messages):
    """The Highlights tab must not keep its own idea of what saving means."""
    open_tab.view.annotation_manager.add_highlight(
        0, [(10.0, 55.0, 120.0, 66.0)])

    open_tab.sidebar.save_requested.emit()
    qapp.processEvents()

    with fitz.open(open_tab.doc_path) as doc:
        kinds = [a.type[0] for a in doc.load_page(0).annots()]
    assert fitz.PDF_ANNOT_HIGHLIGHT in kinds
    assert messages == [], "a write that worked needs no dialog"


# ---------------------------------------------------------------- Revert
def test_revert_is_offered_only_when_there_is_work_to_lose(qapp, win, open_tab):
    assert win.act_revert.isEnabled() is False
    open_tab.view.apply_edit(AddText(0, "DRAFT"))
    qapp.processEvents()
    assert win.act_revert.isEnabled() is True


def test_revert_restores_the_file_and_clears_the_mark(qapp, win, open_tab,
                                                     monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    open_tab.view.apply_edit(AddText(0, "DRAFT"))
    qapp.processEvents()

    assert win._revert_tab(open_tab) is True
    qapp.processEvents()

    assert "DRAFT" not in disk_text(open_tab.doc_path)
    assert "DRAFT" not in open_tab.view.doc.load_page(0).get_text()
    assert open_tab.dirty is False
    assert win.tabs.tabText(0) == open_tab.title
    assert win.act_undo.isEnabled() is False


def test_revert_can_be_refused_and_the_work_survives(qapp, win, open_tab, monkeypatch):
    _answer(monkeypatch, QMessageBox.StandardButton.No)
    open_tab.view.apply_edit(AddText(0, "STILL DRAFTING"))

    assert win._revert_tab(open_tab) is False
    assert "STILL DRAFTING" in open_tab.view.doc.load_page(0).get_text()
    assert open_tab.dirty is True


def test_revert_keeps_what_is_in_the_file_and_drops_what_is_not(
        qapp, win, open_tab, monkeypatch):
    """Revert means "the file is the truth again", sidebar list included."""
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [(10.0, 55.0, 120.0, 66.0)])
    assert win._save_tab(open_tab) is True
    manager.add_highlight(1, [(10.0, 55.0, 120.0, 66.0)])
    open_tab.view.apply_edit(AddText(0, "DRAFT"))
    qapp.processEvents()

    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    assert win._revert_tab(open_tab) is True
    qapp.processEvents()

    assert [a.page for a in manager.get_annotations()] == [0]
    assert open_tab.dirty is False


def test_a_refused_revert_leaves_the_document_exactly_as_it_was(qapp, win, open_tab,
                                                               monkeypatch, messages):
    """A broken file on disk must not cost the user the in-memory one."""
    _answer(monkeypatch, QMessageBox.StandardButton.Yes)
    open_tab.view.apply_edit(AddText(0, "PRECIOUS"))
    qapp.processEvents()
    os.remove(open_tab.doc_path)

    assert win._revert_tab(open_tab) is False
    assert messages, "the failure must be explained"
    assert "PRECIOUS" in open_tab.view.doc.load_page(0).get_text()
    assert open_tab.dirty is True
