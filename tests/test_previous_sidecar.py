"""F10-4m: the export keeps the numbering it is about to replace.

F10-4j made a sidecar honest about itself: the root carries ``pdfar:pages``, the
count of the document its marks number into, so an import of a file written
against a longer book says so instead of landing marks on sheets that moved
(``src/pdfar/xfdf.py:467``, ``xfdf.py:1175``).

That honesty has a shelf life, and the export is what expires it.  Both commands
open on ``name.xfdf`` beside the document (``xfdf.py:1265``), and the write
truncates whatever is there (``main.py:1578``).  So the second export after a
delete replaces the only file that still said what the marks used to mean: the
six-page numbering lives on in nothing but a history that closes with the tab,
and the disagreement F10-4j learned to announce goes silent because the evidence
of it is gone.  The reader who wants the old copy back has nowhere to look — the
bytes were overwritten, not moved.

So the export asks the file that is there one question before writing over it:
does it describe a document of the size being written now?  When the answer is
no — a different count, no count at all, a file that is not XFDF, one that cannot
be read — the copy is kept beside the target under its own name and the write
goes on top of the original.  Not a dialog: Qt's own chooser already asks
"replace?", cannot be influenced, and an answer given to a prompt that cannot
tell ours from somebody else's is not the reader's decision to make.  And not a
silent overwrite either: keeping costs one file in the folder the reader is
already looking at, which is the only place an exchange can be undone by them.

When the copy cannot be made the export refuses.  The two failures share a
cause — a folder that will not take a file will not take the export either — and
refusing is the difference between telling the reader about a numbering that went
missing and adding it to the list of things they will find out later.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QMessageBox

from conftest import build_sample_pdf
from pdfar import xfdf
from pdfar.main import MainWindow
from pdfar.pages import DeletePages
from pdfar.xfdf import (XfdfError, keep_previous_sidecar, pages_written_against,
                        previous_comments_file)
from test_xfdf_import import HEIGHT, envelope
from test_xfdf_ui import _open, _save_as

#: One note, on the page and with the name the file gives it.
NOTE = '<text page="{page}" name="{name}" rect="10,{top},26,{bottom}"/>'


def _note(page: int, name: str = "n") -> str:
    return NOTE.format(page=page, name=name, top=HEIGHT - 26, bottom=HEIGHT - 10)


def _write(tmp_path, name: str, text: str) -> str:
    path = os.path.join(str(tmp_path), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# The window and the dialog recorder are test_xfdf_ui's, but a fixture imported
# from another test module is a name its tests re-declare as parameters, which
# reads as a redefinition; these two are scaffolding, so they are repeated here.
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


@pytest.fixture(autouse=True)
def seen(monkeypatch):
    """Capture the dialogs a modal test run must never actually show."""
    seen = []
    for name in ("critical", "warning", "information", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    return seen


@pytest.fixture
def book_tab(win, tmp_path):
    """A six-page report, open, under the name its sidecar will take."""
    path = str(build_sample_pdf(tmp_path / "report.pdf", pages=6))
    assert win.open_document(path)
    return win._active_tab()


# ------------------------------------------------------------------ the claim
def test_a_file_that_says_how_many_pages_it_was_written_for(tmp_path):
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one"), pages=6))
    assert pages_written_against(path) == 6


def test_a_file_that_says_nothing_has_no_claim(tmp_path):
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one")))
    assert pages_written_against(path) is None


def test_a_file_that_is_not_xfdf_is_refused_not_guessed(tmp_path):
    path = _write(tmp_path, "report.xfdf", "<html><body>not a sidecar</body></html>")
    with pytest.raises(XfdfError):
        pages_written_against(path)


# ------------------------------------------------------------------- the keep
def test_a_target_that_is_not_there_is_nothing_to_keep(tmp_path):
    away = os.path.join(str(tmp_path), "report.xfdf")
    assert keep_previous_sidecar(away, 6) is None
    assert os.listdir(str(tmp_path)) == []


def test_a_sidecar_written_against_this_document_is_ours(tmp_path):
    """No copy for a file whose numbering the write is about to repeat.

    Re-exporting the same book is the ordinary case, and a ``.previous`` file
    every time would train the reader to ignore the one that matters.
    """
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one"), pages=6))
    assert keep_previous_sidecar(path, 6) is None
    assert os.listdir(str(tmp_path)) == ["report.xfdf"]


def test_an_older_numbering_is_copied_aside_before_the_write(tmp_path):
    older = envelope(_note(0, "one"), _note(5, "six"), pages=6)
    path = _write(tmp_path, "report.xfdf", older)

    kept = keep_previous_sidecar(path, 4)

    assert kept is not None
    target, claim = kept
    assert claim == 6
    assert os.path.basename(target) == "report.previous.xfdf"
    assert _read(target) == older


def test_the_kept_copy_still_says_which_book_it_was_written_for(tmp_path):
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one"), pages=6))
    target = keep_previous_sidecar(path, 4)[0]
    assert pages_written_against(target) == 6


@pytest.mark.parametrize("text", [
    envelope(_note(0, "one")),                       # another reader: no claim
    envelope(_note(0, "one"), pages="many"),         # a claim that is no count
    envelope(_note(0, "one"), namespaced=False),     # an envelope, not XFDF's
    "<html><body>not xml at all</body></html>",      # somebody's notes file
    "\u0000\u0001\u0002 not text",                   # neither
])
def test_a_sidecar_we_cannot_vouch_for_is_kept_too(tmp_path, text):
    """The direction of an unknown: keep it.

    A file that never promised a page count may still hold marks whose numbers
    mean something only the book it came from can say, and a file that is not
    XFDF is somebody's work whatever it is.  Absent evidence is not agreement.
    """
    path = _write(tmp_path, "report.xfdf", text)
    kept = keep_previous_sidecar(path, 4)
    assert kept is not None
    assert _read(kept[0]) == text


def test_a_file_too_large_to_read_is_kept_before_it_is_replaced(tmp_path,
                                                                monkeypatch):
    """The bound stops the read, not the keeping.

    A sidecar PDFAR will not parse is a sidecar whose numbering is unknown, and
    the copy costs bytes rather than a parse.
    """
    path = _write(tmp_path, "report.xfdf", envelope(*[_note(p) for p in range(40)],
                                                   pages=40))
    monkeypatch.setattr(xfdf, "MAX_INPUT_CHARS", 64)
    kept = keep_previous_sidecar(path, 4)
    assert kept is not None
    assert kept[1] is None
    assert os.path.getsize(kept[0]) == os.path.getsize(path)


def test_two_keeps_do_not_share_a_name(tmp_path):
    """The first kept copy is not the second one's destination.

    Each export that replaces a foreign numbering leaves a file behind, and the
    one being replaced now is worth exactly as much as the one before it.
    """
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "six"), pages=6))
    first = keep_previous_sidecar(path, 5)[0]

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(envelope(_note(0, "five"), pages=5))
    second = keep_previous_sidecar(path, 4)[0]

    assert os.path.basename(first) == "report.previous.xfdf"
    assert os.path.basename(second) == "report.previous-2.xfdf"
    assert "six" in _read(first) and "five" in _read(second)


def test_the_copy_lands_beside_the_target_not_beside_the_document(tmp_path):
    """Where the reader will look is where the file they pointed at is."""
    folder = os.path.join(str(tmp_path), "exchange")
    os.makedirs(folder)
    target = os.path.join(folder, "notes.xfdf")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(envelope(_note(0, "one"), pages=9))

    assert os.path.dirname(previous_comments_file(target)) == folder
    kept = keep_previous_sidecar(target, 4)[0]
    assert os.path.basename(kept) == "notes.previous.xfdf"
    assert sorted(os.listdir(folder)) == ["notes.previous.xfdf", "notes.xfdf"]


def test_a_copy_that_cannot_be_made_raises(tmp_path, monkeypatch):
    def refuse(source, target):
        raise OSError("no space left on the device")

    monkeypatch.setattr(xfdf.shutil, "copyfile", refuse)
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one"), pages=6))
    with pytest.raises(OSError):
        keep_previous_sidecar(path, 4)


def test_a_copy_that_stops_halfway_leaves_nothing_behind(tmp_path, monkeypatch):
    """A broken keep is not a kept file.

    A truncated copy reads as a numbering that was saved when it was not, and the
    reader would trust the name it has.
    """
    def half_write(source, target):
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("<xfdf>")
        raise OSError("no space left on the device")

    monkeypatch.setattr(xfdf.shutil, "copyfile", half_write)
    path = _write(tmp_path, "report.xfdf", envelope(_note(0, "one"), pages=6))
    with pytest.raises(OSError):
        keep_previous_sidecar(path, 4)
    assert os.listdir(str(tmp_path)) == ["report.xfdf"]


# ------------------------------------------------------------------- the write
def test_the_first_export_keeps_nothing(win, book_tab, monkeypatch, tmp_path):
    out = os.path.join(str(tmp_path), "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(book_tab) is True
    assert sorted(os.listdir(str(tmp_path))) == ["report.pdf", "report.xfdf"]
    assert "previous" not in win.status_left.text()


def test_an_export_over_our_own_sidecar_keeps_nothing(win, book_tab, monkeypatch,
                                                     tmp_path):
    """The same document twice: the numbering did not move, so nothing is lost."""
    out = os.path.join(str(tmp_path), "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(book_tab) is True
    assert win._export_tab_comments(book_tab) is True
    assert sorted(os.listdir(str(tmp_path))) == ["report.pdf", "report.xfdf"]


def test_an_export_over_another_numbering_keeps_it(win, book_tab, monkeypatch,
                                                  tmp_path, seen):
    """The roadmap's case: the second export no longer eats the first.

    Six notes exported, then one page taken out of the same book and the comments
    exported again to the same name.  The file that stands at the end describes
    five pages, and the six-page one it replaced is still there to be read.
    """
    for page in range(6):
        book_tab.view.annotation_manager.add_note(page, (30.0, 30.0),
                                                 f"note {page + 1}")
    out = os.path.join(str(tmp_path), "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(book_tab) is True

    assert book_tab.view.session.apply(DeletePages(pages=(1,)))
    book_tab.view.annotation_manager.reload_from_document()
    assert int(book_tab.view.session.doc.page_count) == 5

    assert win._export_tab_comments(book_tab) is True
    assert pages_written_against(out) == 5

    kept = os.path.join(str(tmp_path), "report.previous.xfdf")
    assert pages_written_against(kept) == 6
    for page in range(6):
        assert f"<contents>note {page + 1}</contents>" in _read(kept), _read(kept)
    assert "report.previous.xfdf" in win.status_left.text(), win.status_left.text()
    assert seen == [], "keeping is not a problem to be reported"


def test_a_foreign_sidecar_is_kept_before_it_is_replaced(win, book_tab, monkeypatch,
                                                         tmp_path):
    """A file with no claim is not a file that agrees.

    Acrobat's export names its pages by number too, and says nothing about how
    many there were; PDFAR cannot tell the reader that its marks meant the pages
    they now mean.  So it is kept, and the reader keeps the answer.
    """
    foreign = envelope(*[_note(p) for p in range(6)])
    out = _write(tmp_path, "report.xfdf", foreign)
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(book_tab) is True
    kept = os.path.join(str(tmp_path), "report.previous.xfdf")
    assert _read(kept) == foreign
    assert pages_written_against(kept) is None
    assert pages_written_against(out) == 6


def test_an_export_that_cannot_keep_the_older_file_writes_nothing(
        win, book_tab, monkeypatch, tmp_path, seen):
    """No overwrite the keep was supposed to make safe.

    A folder that will not take the copy is a folder whose file would have been
    destroyed for nothing, so the export stops and says so instead.
    """
    def refuse(source, target):
        raise OSError("Read-only file system")

    monkeypatch.setattr(xfdf.shutil, "copyfile", refuse)
    older = envelope(_note(0, "six"), pages=9)
    out = _write(tmp_path, "report.xfdf", older)
    _save_as(monkeypatch, out)

    assert win._export_tab_comments(book_tab) is False
    assert seen and "Read-only file system" in seen[0], seen
    assert _read(out) == older, "the file that was there is untouched"


# ------------------------------------------------------- what the keeping buys
def test_the_kept_copy_reads_back_into_the_book_it_was_written_for(
        win, book_tab, monkeypatch, tmp_path, seen):
    """The record survives in the only form that is any use: an importable file.

    The six notes land on the six sheets they named, and the import has nothing
    to complain about, because the numbering kept in the file is the numbering of
    the document in hand.  Before this step the file was gone by the time the
    reader wanted it.
    """
    for page in range(6):
        book_tab.view.annotation_manager.add_note(page, (30.0, 30.0),
                                                 f"note {page + 1}")
    out = os.path.join(str(tmp_path), "report.xfdf")
    _save_as(monkeypatch, out)
    assert win._export_tab_comments(book_tab) is True
    assert book_tab.view.session.apply(DeletePages(pages=(1,)))
    book_tab.view.annotation_manager.reload_from_document()
    assert win._export_tab_comments(book_tab) is True

    kept = os.path.join(str(tmp_path), "report.previous.xfdf")
    win._close_all_tabs(confirm=False)

    assert win.open_document(str(build_sample_pdf(tmp_path / "side.pdf", pages=6)))
    tab = win._active_tab()
    _open(monkeypatch, kept)
    assert win._import_tab_comments(tab) is True
    arrived = tab.view.annotation_manager.get_annotations()
    assert len(arrived) == 6, [mark.page for mark in arrived]
    assert seen == [], seen
