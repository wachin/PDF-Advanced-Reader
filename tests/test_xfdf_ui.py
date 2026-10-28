"""F10-3w: the File-menu commands that reach both halves of the exchange.

The writer is judged by the file it produces and the reader by the plan it returns,
each where it lives.  This file is about the window, because a capability nobody can
ask for is not one:

* the two commands are offered exactly when they could do something;
* a chooser the user cancels leaves no file, no mark and no undo step;
* a refusal is said out loud — the reason, not a grey status bar; and
* what the user is told after an import is what the import did, **including** the
  marks it did not take, since a comment that vanishes silently cannot be told
  apart from one that was never sent.

The dialogs are replaced, never shown: an offscreen run that reaches a modal is a
test that hangs.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QFileDialog, QMessageBox

from conftest import build_sample_pdf
from pdfar.main import MainWindow, skipped_report
from pdfar.xfdf import comments_file_for, export_xfdf
from test_xfdf_import import HEIGHT, box_file, envelope


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
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    assert win.open_document(path)
    return win._active_tab()


@pytest.fixture
def second_tab(win, tmp_path):
    """Another document, already open, so a round trip is two real tabs."""
    path = str(build_sample_pdf(tmp_path / "other.pdf", pages=3))
    assert win.open_document(path)
    return win._active_tab()


@pytest.fixture(autouse=True)
def seen(monkeypatch):
    """Capture the dialogs a modal test run must never actually show.

    Autouse because the import reports what it left out in a dialog: a test that
    forgot to ask for one would otherwise be a test that waits forever.
    """
    seen = []
    for name in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


def _save_as(monkeypatch, path):
    """The write chooser hands back *path* (or nothing, if it was cancelled)."""
    asked = []

    def choose(parent, caption, directory, *rest):
        asked.append((caption, directory))
        return (str(path), "XFDF Files (*.xfdf)") if path else ("", "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(choose))
    return asked


def _open(monkeypatch, path):
    """The read chooser hands back *path* (or nothing, if it was cancelled)."""
    asked = []

    def choose(parent, caption, directory, *rest):
        asked.append((caption, directory))
        return (str(path), "XFDF Files (*.xfdf)") if path else ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(choose))
    return asked


def _file(tmp_path, name: str, *marks: str) -> str:
    """A foreign comments file, written the way the other reader would write it."""
    path = tmp_path / name
    path.write_text(envelope(*marks), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ offered
def test_the_commands_need_a_document_but_not_a_dirty_one(win, open_tab):
    """Both are offered on a clean document.

    Export has something to say about a document that matches its file — the marks
    are in it already — and importing is how a document gets marks in the first
    place, so neither waits on the dirty mark the way Revert does.
    """
    assert open_tab.dirty is False
    assert win.act_export_xfdf.isEnabled() is True
    assert win.act_import_xfdf.isEnabled() is True

    win._close_tab(0, confirm=False)
    assert win.act_export_xfdf.isEnabled() is False
    assert win.act_import_xfdf.isEnabled() is False


def test_choosers_open_on_the_documents_own_name(win, open_tab, monkeypatch,
                                                 tmp_path):
    """The suggested file is the document's, with the exchange's extension.

    A marks file that does not say which document it belongs to is a pile of
    numbers, so the name the chooser offers is the one that already does.
    """
    wanted = comments_file_for(open_tab.doc_path)
    assert wanted == str(tmp_path / "doc.xfdf")

    saved = _save_as(monkeypatch, None)
    assert win._export_tab_comments(open_tab) is False
    opened = _open(monkeypatch, None)
    assert win._import_tab_comments(open_tab) is False
    assert [ask[1] for ask in saved + opened] == [wanted, wanted]


# ------------------------------------------------------------------- export
def test_exporting_writes_the_marks_the_document_holds(win, open_tab, monkeypatch,
                                                       tmp_path):
    manager = open_tab.view.annotation_manager
    manager.add_note(0, (20.0, 30.0), "Sent out")
    manager.add_highlight(1, [(10.0, 20.0, 90.0, 28.0)])
    assert open_tab.dirty is True
    target = tmp_path / "review.xfdf"
    _save_as(monkeypatch, target)

    assert win._export_tab_comments(open_tab) is True
    text = target.read_text(encoding="utf-8")
    assert "<text" in text and "<highlight" in text
    assert "Sent out" in text
    assert win.status_left.text().endswith(str(target))
    # An export describes the marks and is not a save, so the document still
    # holds the changes its own file does not.
    assert open_tab.dirty is True


def test_a_name_without_an_extension_still_becomes_a_file_pdfar_can_find(
        win, open_tab, monkeypatch, tmp_path):
    """Typing ``review`` in the chooser writes ``review.xfdf``.

    Every other save in this application adds its own extension; a marks file
    named without one is the same slip, and the alternative is a file a later
    import has to be pointed at by hand.
    """
    target = tmp_path / "review"
    _save_as(monkeypatch, target)
    assert win._export_tab_comments(open_tab) is True
    assert (tmp_path / "review.xfdf").is_file()
    assert not target.exists()


def test_an_empty_document_still_writes_a_file(win, open_tab, monkeypatch,
                                               tmp_path):
    """No marks is an answer, not a no-op.

    The other side of an exchange needs the file to exist to know there was
    nothing in it; a command that quietly refused would leave them waiting.
    """
    target = tmp_path / "none.xfdf"
    _save_as(monkeypatch, target)
    assert win._export_tab_comments(open_tab) is True
    assert "<annots>" in target.read_text(encoding="utf-8")
    assert "<text" not in target.read_text(encoding="utf-8")


def test_a_cancelled_export_writes_nothing(win, open_tab, monkeypatch, tmp_path):
    _save_as(monkeypatch, None)
    assert win._export_tab_comments(open_tab) is False
    assert not list(tmp_path.glob("*.xfdf"))


def test_an_export_that_cannot_write_says_so(win, open_tab, monkeypatch, seen):
    _save_as(monkeypatch, "/no/such/review.xfdf")
    assert win._export_tab_comments(open_tab) is False
    assert seen and "Could not write the comments file" in seen[0]


def test_what_goes_out_comes_back(win, open_tab, second_tab, monkeypatch, tmp_path):
    """The two commands are each other's mirror, through the user's own actions.

    Marks written by PDFAR and read back by PDFAR through the menu arrive as the
    same marks in the same places — which is the claim the whole exchange exists
    to keep, and the one that only survives if both commands use the same
    coordinates.
    """
    source = open_tab.view.annotation_manager
    source.add_note(0, (20.0, 30.0), "Round trip")
    source.add_highlight(2, [(10.0, 20.0, 90.0, 28.0)])
    box = tmp_path / "trip.xfdf"
    _save_as(monkeypatch, box)
    assert win._export_tab_comments(open_tab) is True
    assert export_xfdf(source) == box.read_text(encoding="utf-8")

    _open(monkeypatch, box)
    assert win._import_tab_comments(second_tab) is True
    arrived = second_tab.view.annotation_manager.get_annotations()
    assert sorted(int(mark.page) for mark in arrived) == [0, 2]
    assert win.status_left.text().startswith("Imported 2 marks")
    assert [mark.content for mark in
            second_tab.view.annotation_manager.get_annotations(page=0)] == \
        ["Round trip"]


# ------------------------------------------------------------------- import
def test_importing_takes_the_whole_file_as_one_undo_step(win, open_tab,
                                                         monkeypatch, tmp_path):
    path = _file(tmp_path, "in.xfdf",
                 f'<text page="0" name="a" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>',
                 f'<text page="1" name="b" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>',
                 f'<square page="2" name="c" '
                 f'rect="{box_file((10.0, 10.0, 40.0, 40.0))}"/>')
    _open(monkeypatch, path)

    assert win._import_tab_comments(open_tab) is True
    assert open_tab.dirty is True
    assert win.act_undo.isEnabled() is True
    assert len(open_tab.view.session.history) == 1
    expected = f"Imported 3 marks from {os.path.basename(path)}"
    assert win.status_left.text() == expected

    open_tab.view.undo_edit()
    assert open_tab.view.annotation_manager.get_annotations() == []


def test_a_file_that_is_not_xfdf_is_refused_out_loud(win, open_tab, monkeypatch,
                                                     tmp_path, seen):
    path = tmp_path / "notxfdf.xfdf"
    path.write_text("<html><body>not a comments file</body></html>",
                    encoding="utf-8")
    _open(monkeypatch, str(path))

    assert win._import_tab_comments(open_tab) is False
    assert seen and "not XFDF" in seen[0]
    assert len(open_tab.view.session.history) == 0
    assert open_tab.dirty is False
    assert win.status_left.text() == "Comments not imported"


def test_the_marks_left_out_are_named(win, open_tab, monkeypatch, tmp_path, seen):
    """A partial import says which part it refused.

    Another reader's file is not obliged to hold only the kinds PDFAR has words
    for, so the count alone would not be enough: the user has to be able to tell
    "nothing of yours arrived" from "three of five did".
    """
    path = _file(tmp_path, "mixed.xfdf",
                 f'<text page="0" name="a" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>',
                 f'<freetext page="0" rect="10,{HEIGHT - 26},80,{HEIGHT - 10}"/>')
    _open(monkeypatch, path)

    assert win._import_tab_comments(open_tab) is True
    assert win.status_left.text() == (
        f"Imported 1 mark, 1 not taken from {os.path.basename(path)}")
    assert seen and "freetext on page 1" in seen[0]
    assert "is not a kind of mark PDFAR can draw" in seen[0]


def test_importing_the_same_file_twice_imports_it_once(win, open_tab, monkeypatch,
                                                       tmp_path, seen):
    path = _file(tmp_path, "twice.xfdf",
                 f'<text page="0" name="dup" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>')
    _open(monkeypatch, path)

    assert win._import_tab_comments(open_tab) is True
    assert len(open_tab.view.session.history) == 1
    assert win._import_tab_comments(open_tab) is True
    assert len(open_tab.view.session.history) == 1
    assert win.status_left.text() == (
        f"No comments imported from {os.path.basename(path)}")


def test_a_mark_for_a_page_the_document_lacks_is_left_out_and_named(
        win, open_tab, monkeypatch, tmp_path, seen):
    """A file about a different edition still gives what it can.

    The parser refuses the mark rather than the file, and the window's job is to
    make that visible: the user has to be able to see that page 100 was expected
    and is not here.
    """
    path = _file(tmp_path, "otheredition.xfdf",
                 f'<text page="0" name="here" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>',
                 f'<text page="99" name="there" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>')
    _open(monkeypatch, path)

    assert win._import_tab_comments(open_tab) is True
    assert len(open_tab.view.annotation_manager.get_annotations()) == 1
    assert seen and "text on page 100" in seen[0]
    assert "names a page this document does not have" in seen[0]


def test_an_import_the_manager_refuses_is_not_reported_as_one(win, open_tab,
                                                              monkeypatch,
                                                              tmp_path, seen):
    """``None`` from the manager is its own message, already given.

    A plan that cannot be carried out is an edit failure, and every edit failure
    reports itself through ``transient_message``; the command stays out of it,
    because a second dialog saying the same thing would only make the user read
    it twice.
    """
    path = _file(tmp_path, "refused.xfdf",
                 f'<text page="0" name="a" rect="10,{HEIGHT - 26},26,'
                 f'{HEIGHT - 10}"/>')
    _open(monkeypatch, path)
    monkeypatch.setattr(open_tab.view.annotation_manager, "import_xfdf",
                        lambda _text: None)

    assert win._import_tab_comments(open_tab) is False
    assert seen == []
    assert open_tab.dirty is False
    assert len(open_tab.view.session.history) == 0


def test_a_cancelled_chooser_leaves_the_document_alone(win, open_tab, monkeypatch):
    _open(monkeypatch, None)
    assert win._import_tab_comments(open_tab) is False
    assert open_tab.dirty is False
    assert len(open_tab.view.session.history) == 0


# ------------------------------------------------------------- the skip list
def test_the_left_out_list_stops_before_it_becomes_a_wall_of_text():
    """Names the marks that did not arrive, and counts the rest.

    A file may carry thousands; the point is which kinds were refused, and a
    dialog no one scrolls to the end of says nothing.
    """
    skipped = [(f"text on page {index}", "has no rectangle")
               for index in range(15)]
    lines = skipped_report(skipped).splitlines()
    assert len(lines) == 13
    assert lines[0] == "• text on page 0: has no rectangle"
    assert lines[-1] == "• … and 3 more"
    assert skipped_report([]) == ""
