"""F10-4l: the saved position names a sheet the *file* has.

``viewState/<absolute path>`` outlives the session, but until now it was written
from the live document: read page 4 of six, delete page 2, close without saving —
and the number stored is 2, which belongs to the five-sheet tree only this
session ever had.  The file on disk still holds six sheets, so reopening lands on
``PAGE 3`` instead of the sheet the reader was on.

The history already carries what each step did to the numbers (F10-4h), so the
way from a live page to the file's page is a walk along those steps: backward
through the ones applied since the save, forward through the ones an undo put
back on the shelf.  A walk is only honest when every step it needs is still
there, so two things end it with no answer at all — a sheet the file never had,
and a step dropped from a bounded stack — and no answer is written rather than a
number for another tree.

The two write sites keep different masters on purpose.  Closing a tab writes the
file's numbering, because that is who reads the key next.  Switching tabs keeps
the live view on the tab itself: the reader coming back to a tab in this session
belongs on the sheet they left, not on where that sheet sits in the file.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from pdfar.edits import EditHistory
from pdfar.main import MainWindow, view_state_key
from pdfar.pages import DeletePages, RotatePages

#: How many sheets the fixture book has; each one says which it is.
PAGES = 6

SHEETS = ["Hello PDFAR world"] + [f"Page {n} content" for n in range(2, PAGES + 1)]


def identities(doc):
    """The first line of every page: which original sheet sits at which number."""
    out = []
    for index in range(doc.page_count):
        lines = [line for line in doc[index].get_text().splitlines() if line.strip()]
        out.append(lines[0] if lines else "")
    return out


@pytest.fixture
def book(tmp_path):
    """Six self-naming sheets, never written to by the session tests.

    Tall enough that the window below has a real scroll range: a position saved
    as a row is only worth testing when rows exist.
    """
    return build_sample_pdf(tmp_path / "book.pdf", pages=PAGES,
                            width=200, height=300)


@pytest.fixture
def live(book):
    """A session over the book as it sits on disk."""
    session = make_session(book)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """QMessageBox would block the offscreen run forever; a close means discard."""
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def win(qapp, book):
    """The real main window on the book, short enough to have somewhere to scroll."""
    window = MainWindow()
    window.resize(500, 300)
    window.show()
    qapp.processEvents()
    assert window.open_document(book)
    qapp.processEvents()
    try:
        yield window
    finally:
        window._close_all_tabs(confirm=False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def delete(win, pages):
    """Move the page tree the way the document does, with no command in the way."""
    assert win.view.apply_edit(DeletePages(pages=tuple(sorted(pages))))


def stored(book):
    """What ``viewState/<path>`` holds for *book* right now."""
    return QSettings("PDFAR", "PDFAR").value(view_state_key(book))


def top_of_view(view):
    """The page the viewport is actually showing, by its own reckoning."""
    return view._page_at_y(view.verticalScrollBar().value() + 2)


# ------------------------------------------------------- the history's numbering
def test_the_history_counts_the_steps_since_the_document_was_opened(book):
    """``depth`` is where the live document sits, which is what a page is relative to."""
    session = make_session(book)
    try:
        assert session.history.depth == 0
        session.apply(DeletePages(pages=(1,)))
        assert session.history.depth == 1
        session.undo()
        assert session.history.depth == 0
        session.redo()
        assert session.history.depth == 1
    finally:
        session.close()


def test_a_clean_session_names_the_page_it_is_on(live):
    """Nothing to walk: the live tree *is* the file's tree."""
    assert live.page_in_file(3) == 3


def test_a_delete_maps_the_live_page_back_to_the_sheet_the_file_has(live, book):
    """The reader moved one number down; the file has not moved at all."""
    live.apply(DeletePages(pages=(1,)))
    assert identities(live.doc)[2] == SHEETS[3]
    assert live.page_in_file(2) == 3
    with fitz.open(book) as on_disk:
        assert identities(on_disk)[live.page_in_file(2)] == SHEETS[3]


def test_a_turn_moves_no_numbers_so_the_page_comes_back_unchanged(live):
    live.apply(RotatePages(pages=(1,), delta=90))
    assert live.page_in_file(2) == 2


def test_a_cut_below_the_reading_position_leaves_the_page_alone(live):
    live.apply(DeletePages(pages=(4,)))
    assert live.page_in_file(2) == 2


def test_several_steps_are_walked_one_after_the_other(live):
    """Two cuts: the number has to survive both, in order, not just the last."""
    live.apply(DeletePages(pages=(4,)))       # [0 1 2 3 5]
    live.apply(DeletePages(pages=(1,)))       # [0 2 3 5]
    assert identities(live.doc)[1] == SHEETS[2]
    assert live.page_in_file(1) == 2
    with fitz.open(live.path) as on_disk:
        assert identities(on_disk)[2] == SHEETS[2], "the file never took either cut"


def test_the_walk_back_takes_the_newest_step_first(live):
    """Composing the two cuts the other way round lands one sheet off.

    A cut at page 2, then a cut below it: read back from the newest, the sheet on
    live page 2 is number 2 of the five-sheet tree and number 3 of the six the
    file has.  Read in the order the cuts happened and the first map is asked
    about a number only the second tree has — ``PAGE 5``, one sheet past the one
    the reader is on.
    """
    live.apply(DeletePages(pages=(1,)))       # [0 2 3 4 5]
    live.apply(DeletePages(pages=(3,)))       # [0 2 3 5]
    assert identities(live.doc)[2] == SHEETS[3]
    assert live.page_in_file(2) == 3


def test_the_walk_forward_takes_the_oldest_step_first(live):
    """The way back out is the same chain read the other way, in order.

    Both cuts are in the file now and the reader undid past them: ``PAGE 5`` is
    not in the four-sheet tree at all, and a forward walk that spends the second
    cut before the first finds a number for it anyway.
    """
    live.apply(DeletePages(pages=(1,)))
    live.apply(DeletePages(pages=(3,)))
    live.mark_saved()
    live.undo()
    live.undo()
    assert identities(live.doc)[4] == SHEETS[4]
    assert live.page_in_file(2) == 1
    assert live.page_in_file(4) is None, "the sheet is not in the file"
    assert live.page_in_file(5) == 3


def test_a_sheet_the_step_invented_has_no_number_in_the_file():
    """``-1`` is a sheet with no past, so the way back ends rather than guesses."""
    history = EditHistory()
    history.record(DeletePages(pages=(1,)), b"one", (0, 1, -1))
    assert history.depth == 1
    assert history.map_page(2, 0) is None
    assert history.map_page(1, 0) == 1


def test_undoing_past_the_save_re_aims_the_page_through_the_redo_step(live):
    """The file can be *ahead* of the reader: an undo left the saved step behind."""
    live.apply(DeletePages(pages=(1,)))
    # mark_saved is the promise that these bytes are on disk; save.py is what
    # performs the write, and is not what this walk is about.
    live.mark_saved()
    in_file = identities(live.doc)
    live.undo()
    assert identities(live.doc)[3] == SHEETS[3]
    assert live.page_in_file(3) == 2
    assert in_file[2] == SHEETS[3]


def test_a_branch_that_replaced_the_saved_steps_cannot_say_where_the_page_is(live):
    """Edit again after an undo and the saved tree is gone from the history.

    The step counts line up — one applied, one saved — but the applied one is not
    the saved one, so a number read off the live tree would be carried by an
    identity that never held.  The epoch is what tells the two apart.
    """
    live.apply(DeletePages(pages=(1,)))
    live.mark_saved()
    live.undo()
    live.apply(RotatePages(pages=(1,), delta=90))
    assert live.history.depth == 1
    assert live.page_in_file(2) is None


def test_a_revert_puts_the_numbering_back_where_the_file_is(live):
    """Revert throws the edits away *and* their steps; the file is the whole story."""
    live.apply(DeletePages(pages=(1,)))
    live.revert()
    assert live.history.depth == 0
    assert live.page_in_file(2) == 2


def test_a_step_dropped_from_the_history_is_a_hole_not_a_guess():
    """A bounded stack loses its oldest step; the walk stops rather than hopping over."""
    history = EditHistory(max_steps=1)
    history.record(DeletePages(pages=(1,)), b"one", (0, 2, 3))
    history.record(DeletePages(pages=(0,)), b"two", (1, 2))
    assert history.depth == 2
    assert len(history) == 1, "the first step is gone"
    assert history.map_page(1, 1) == 2, "the step still here maps exactly"
    assert history.map_page(0, 0) is None


# ---------------------------------------------------------------- closing a tab
def test_closing_a_clean_tab_stores_the_page_it_is_on(win, book):
    win.view.goto_page(3)
    assert win._close_tab(0) is True
    assert stored(book)["page"] == 3


def test_closing_over_a_discarded_delete_stores_the_files_number(win, book):
    """The bug named: the number stored must be the one the file's tree gives."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win.view.current_page == 2, "the reader moved with the sheets"
    assert win._close_tab(0) is True
    assert stored(book)["page"] == 3
    with fitz.open(book) as on_disk:
        assert on_disk.page_count == PAGES, "the delete never reached the file"
        assert identities(on_disk)[stored(book)["page"]] == SHEETS[3]


def test_reopening_the_book_puts_the_reader_back_on_the_sheet_they_left(
        win, book, qapp):
    """The whole point of the key: the next session opens the same sheet."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win._close_tab(0) is True

    assert win.open_document(book)
    qapp.processEvents()
    assert win.view.current_page == 3
    assert identities(win.view.session.doc)[3] == SHEETS[3]


def test_a_re_aimed_position_drops_the_row_it_came_with(win, book):
    """Only the number carried over; the scroll offset belonged to the other tree."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win._close_tab(0) is True
    state = stored(book)
    assert state["page"] == 3
    assert "vscroll" not in state, "a row of a tree the file does not have"


def test_a_position_that_did_not_move_keeps_its_row(win, book):
    win.view.goto_page(3)
    assert win._close_tab(0) is True
    state = stored(book)
    assert state["page"] == 3
    assert state["vscroll"] > 0, "the reader was not at the top of the document"


def test_reopening_scrolls_the_named_sheet_to_the_top_of_the_view(win, book, qapp):
    """The number box and the screen have to say the same sheet."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win._close_tab(0) is True

    assert win.open_document(book)
    qapp.processEvents()
    qapp.processEvents()
    origin = win.view._page_origin(3)[1]
    value = win.view.verticalScrollBar().value()
    assert origin - 60 <= value <= origin, "the sheet is where the number says"


def test_closing_the_window_stores_the_files_number(win, book, qapp):
    """``closeEvent`` reaches the same rule as the tab's own close button."""
    win.view.goto_page(3)
    delete(win, (1,))
    win.close()
    qapp.processEvents()
    assert stored(book)["page"] == 3


def test_a_saved_edit_makes_the_live_number_the_files_number(win, book):
    """Save, and the tree the reader is on is the tree the file has."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win._save_tab(win._active_tab()) is True
    assert win._close_tab(0) is True
    assert stored(book)["page"] == 2


def test_a_close_that_cannot_name_the_sheet_leaves_the_last_position_alone(
        win, book):
    """No answer is written rather than a number for a tree the file does not have."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win._save_tab(win._active_tab()) is True
    win._save_tab_state(win._active_tab(), for_file=True)
    assert stored(book)["page"] == 2

    win.view.undo_edit()                     # six sheets again, the file has five
    win.view.apply_edit(RotatePages(pages=(1,), delta=90))
    win._save_tab_state(win._active_tab(), for_file=True)
    assert stored(book)["page"] == 2, "the honest number survives the one that is not"


# --------------------------------------------------------------- switching tabs
def test_switching_away_stores_the_file_number(win, book, tmp_path, qapp):
    """The path key is written for whoever opens the file next."""
    other = build_sample_pdf(tmp_path / "other.pdf", pages=2)
    assert win.open_document(other)
    qapp.processEvents()

    win.tabs.setCurrentIndex(0)
    win.view.goto_page(3)
    delete(win, (1,))
    win.tabs.setCurrentIndex(1)
    assert stored(book)["page"] == 3


def test_the_tab_comes_back_to_the_sheet_the_reader_left(win, book, tmp_path, qapp):
    """A tab switched away from and back is the same session: live, not file."""
    other = build_sample_pdf(tmp_path / "other.pdf", pages=2)
    assert win.open_document(other)
    qapp.processEvents()

    win.tabs.setCurrentIndex(0)
    win.view.goto_page(3)
    delete(win, (1,))
    win.tabs.setCurrentIndex(1)
    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view.current_page == 2, "the reader comes back to the same sheet"
    assert identities(win.view.session.doc)[2] == SHEETS[3]


def test_a_clean_tab_still_comes_back_where_it_was_left(win, book, tmp_path, qapp):
    """With no unsaved steps the two answers coincide; switching loses nothing."""
    other = build_sample_pdf(tmp_path / "other.pdf", pages=2)
    assert win.open_document(other)
    qapp.processEvents()

    win.tabs.setCurrentIndex(0)
    win.view.goto_page(4)
    win.tabs.setCurrentIndex(1)
    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view.current_page == 4
