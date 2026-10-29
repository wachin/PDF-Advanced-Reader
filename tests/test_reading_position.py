"""F10-4h: the reader's own place in the book, and their pick, follow the sheets.

A page-tree edit rewrites every number above the cut, and two pieces of the
reader's state are numbers: the page they are on (`PDFView.current_page`, which the
window also writes to `viewState/<absolute path>` on a tab change and on close) and
the pages they have picked in the strip.  F10-4f made the *delete command* re-aim
both, with `shifted_index` worked out in `MainWindow._delete_pages`.  That leaves
every other way a page tree can move -- undo, redo, and the insert, extract and
reorder steps still to come -- moving the sheets out from under a reader who stays
exactly where the number said.

Four measured facts carry this, and a fifth found on the way.

* `_step_edit` (undo and redo) does **nothing** for the reader: `session.undo()`
  adopts an older document, `invalidate` re-measures the pages, and
  `_measure_pages` clamps `current_page` into the new count silently -- no
  `page_changed`, so the number box, the strip's current row and the sheet on
  screen can all disagree.
* After an undo of a delete the reader is left on the *number* they had, which now
  names the sheet below the one they were reading: delete page 2 while reading page
  4 lands on page 3 (right), and undo puts all six sheets back while the reader is
  still on 3 -- which is `PAGE 4` in the old numbering but `PAGE 3` in the new one.
* The STRUCTURE branch of `Sidebar.invalidate_thumbnails` rebuilds the list from
  scratch, and only `select_pages` restores a pick.  Its one caller is the delete
  command, so an undo loses the pick entirely rather than moving it -- and a turn,
  which moves no sheets at all, loses it just the same.
* A pick and a reading position are not the same kind of answer.  A reader on a
  page that went has to land *somewhere* -- the sheet that took the number, which
  is where F10-4f put them -- while a picked page that went is simply not picked
  any more.  So the map answers two questions: `where` for the one, `followed` for
  the other, and they agree wherever the sheet still exists.
* Marking a row and picking a row are **one channel**: `setCurrentItem(item)`
  selects as it makes current, so the strip's "where the reader is" mark edits a
  pick the reader gathered -- measured, a pick of (1, 3) with the view landing on
  page 5 comes out as (1, 4).  The mark cannot simply stop selecting, because the
  delegate paints only `State_Selected` and an unselected current row is invisible;
  so a pick whose pages have all gone out reads as a one-row pick, and the test
  that would have said "no pick" says what the strip can tell.  Splitting the
  channel is F10-4k.

The rule therefore moves off the command and onto the step: the view and the strip
read `DocumentSession.renumbering`, the same map the bookmarks follow, and each
spends it once.  `MainWindow._delete_pages` keeps its own arithmetic for now and
must land in the same place -- a test says so, and the step that deletes it is next.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import make_session
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QMessageBox

from pdfar.edits import Renumbering, Scope
from pdfar.pages import DeletePages, RotatePages

#: How many pages the fixtures are built with; each sheet names itself.
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
    """Six self-naming sheets."""
    path = str(tmp_path / "book.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        page = doc.new_page(width=200, height=100)
        page.insert_text(fitz.Point(10, 30), SHEETS[index], fontsize=12)
        page.insert_text(fitz.Point(10, 70), "alpha beta gamma", fontsize=12)
    doc.save(path)
    doc.close()
    return path


# ------------------------------------------------------------------ followed()
def test_follow_names_the_number_of_a_sheet_that_stayed():
    move = Renumbering((0, 2, 3, 4, 5))
    assert move.followed(0) == 0
    assert move.followed(2) == 1
    assert move.followed(5) == 4


def test_a_sheet_that_went_follows_nothing():
    """A picked page that was deleted is not picked any more.

    ``where`` answers the same question with the sheet that took the number, which
    is right for a reader, who has to be somewhere, and wrong for a selection,
    which is allowed to shrink.
    """
    move = Renumbering((0, 2, 3, 4, 5))
    assert move.followed(1) is None


def test_the_way_back_sends_a_sheet_to_the_number_it_came_from():
    back = Renumbering((0, 2, 3, 4, 5), backward=True)
    assert back.followed(0) == 0
    assert back.followed(1) == 2
    assert back.followed(4) == 5


def test_a_page_the_step_invented_follows_nothing_on_the_way_back():
    """Undoing an insert takes a sheet out; anything picked on it goes with it."""
    back = Renumbering((-1, 0, 1, 2), backward=True)
    assert back.followed(0) is None
    assert [back.followed(index) for index in (1, 2, 3)] == [0, 1, 2]


def test_a_number_beyond_the_document_the_step_came_out_of_follows_nothing():
    """The domain boundary of ``followed``, which is not the one ``where`` uses.

    A pick cannot name a page the document it was read from does not have, so the
    UI never reaches here; ``where`` still answers with the number unchanged,
    because a reader has to be somewhere even when the question is out of range.
    """
    assert Renumbering((0, 2, 3, 4, 5), backward=True).followed(5) is None
    assert Renumbering(()).followed(0) is None


def test_where_and_follow_agree_wherever_there_is_a_sheet():
    """The two answers are one rule, asked of a sheet that exists or not."""
    for order in ((0, 2, 3, 4, 5), (0, 1, 2, 3, 4), (3, 4, 5), ()):
        for direction in (False, True):
            move = Renumbering(order, backward=direction)
            for page in range(PAGES):
                followed = move.followed(page)
                if followed is not None:
                    assert followed == move.where(page)


# ---------------------------------------------------------------- the window
class _Answers:
    def __init__(self):
        self.asked = []


@pytest.fixture(autouse=True)
def answers(monkeypatch):
    """Stand in for every box this feature raises: a modal would block the run."""
    box = _Answers()

    def fake_question(_parent, title, text, buttons, *rest):
        box.asked.append((title, text))
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    return box


@pytest.fixture
def win(qapp, book):
    """The real main window on the six-page book."""
    from pdfar.main import MainWindow

    window = MainWindow()
    window.show()
    qapp.processEvents()
    window.open_document(book)
    qapp.processEvents()
    try:
        yield window
    finally:
        window._close_document()
        window.close()
        window.deleteLater()
        qapp.processEvents()


def reading(win):
    """The sheet the reader is on, named by what it says rather than its number."""
    doc = win.view.session.doc
    return identities(doc)[min(win.view.current_page, doc.page_count - 1)]


def delete(win, pages):
    """Take pages out the way the *document* does, with no command in the way.

    ``MainWindow._delete_pages`` re-aims the reader with its own arithmetic; this
    goes through the view alone, so every assertion below is about the step's map.
    """
    assert win.view.apply_edit(DeletePages(pages=tuple(sorted(pages))))
    return win


# ----------------------------------------------------------- the reader's page
def test_a_delete_moves_the_reader_onto_the_sheet_that_took_their_number(win):
    """Page 2 goes; a reader on page 4 is on a sheet whose number changed."""
    win.view.goto_page(3)
    assert reading(win) == SHEETS[3]
    delete(win, (1,))
    assert win.view.current_page == 2, "one number lower, not two and not none"
    assert reading(win) == SHEETS[3], "and still the same sheet"


def test_a_delete_of_the_readers_own_page_lands_on_the_sheet_below(win):
    win.view.goto_page(1)
    delete(win, (1,))
    assert win.view.current_page == 1
    assert reading(win) == SHEETS[2], "the sheet that took the number"


def test_a_delete_below_the_reading_position_leaves_the_reader_alone(win):
    win.view.goto_page(1)
    delete(win, (4,))
    assert win.view.current_page == 1
    assert reading(win) == SHEETS[1]


def test_a_delete_that_takes_the_end_with_it_moves_the_reader_by_the_map(win):
    """The clamp that keeps a page number inside the document is not the rule.

    Reading page 5 of six while page 1 and page 6 go out: clamping first would ask
    the map about the number the clamp landed on, and the reader would be carried
    one sheet short of the one they were on.
    """
    win.view.goto_page(4)
    delete(win, (0, 5))
    assert win.view.current_page == 3
    assert reading(win) == SHEETS[4], "the sheet, not the clamped number"


def test_an_undo_puts_the_reader_back_on_the_sheet_they_were_reading(win):
    """The gap F10-4f left: the number survives the undo, the sheet does not.

    Landing on page 3 of five is ``PAGE 4``; undo returns six sheets and page 3 is
    ``PAGE 3`` -- the reader is on another sheet than the one they were reading,
    with nothing on screen to say so.
    """
    win.view.goto_page(3)
    home = reading(win)
    delete(win, (1,))
    assert win.view.current_page == 2
    win.view.undo_edit()
    assert win.view.current_page == 3, "back to the number the sheet wears again"
    assert reading(win) == home


def test_a_redo_moves_the_reader_out_again(win):
    win.view.goto_page(3)
    home = reading(win)
    delete(win, (1,))
    win.view.undo_edit()
    win.view.redo_edit()
    assert win.view.current_page == 2
    assert reading(win) == home


def test_the_reader_is_moved_once_by_a_step_and_not_twice(win):
    """A map spent twice would put a reader two pages further on than their sheet.

    The identity key is what makes the second spend impossible; a value comparison
    would do too, and would be wrong the day two steps describe the same move.
    """
    win.view.goto_page(4)
    delete(win, (0,))
    assert win.view.current_page == 3
    assert reading(win) == SHEETS[4]
    # Whatever else asks for a structure rebuild at this generation, the reader
    # stays on that sheet: the step has been spent.
    win.view.invalidate()
    assert win.view.current_page == 3
    assert reading(win) == SHEETS[4]


def test_a_turn_leaves_the_reader_exactly_where_they_were(win):
    """A STRUCTURE edit that moves no sheet must not move anybody.

    The turn rebuilds the geometry and the strip, which is the same scope a delete
    arrives under; only the map tells the two apart.
    """
    win.view.goto_page(3)
    before = win.view.current_page
    assert win.view.apply_edit(RotatePages(pages=(3,), delta=90))
    assert win.view.current_page == before
    assert reading(win) == SHEETS[3]


def test_two_deletes_undone_in_sequence_bring_the_reader_home_to_their_sheet(win):
    """Each step's own map, in its own direction -- not one composed answer."""
    win.view.goto_page(5)
    home = reading(win)
    delete(win, (1,))
    assert win.view.current_page == 4
    delete(win, (3,))
    assert win.view.current_page == 3
    assert reading(win) == home
    win.view.undo_edit()
    assert reading(win) == home
    win.view.undo_edit()
    assert win.view.current_page == 5
    assert reading(win) == home


def test_the_number_box_says_the_page_the_reader_is_on(win):
    """The announcement is the point: a silent clamp is what F10-4f measured."""
    win.view.goto_page(3)
    delete(win, (1,))
    assert win.page_spin.value() == win.view.current_page + 1
    win.view.undo_edit()
    assert win.page_spin.value() == win.view.current_page + 1


def test_the_command_and_the_view_land_the_reader_in_the_same_place(win):
    """``_delete_pages`` still works its own arithmetic; the map must agree.

    The step that deletes that arithmetic comes next, and until it does, a reader
    who uses the menu and a reader whose document moved another way must not end up
    on different sheets.
    """
    win.view.goto_page(3)
    win._delete_pages((1,))
    by_command = (win.view.current_page, reading(win))

    win._undo_edit()
    win.view.goto_page(3)
    delete(win, (1,))
    assert (win.view.current_page, reading(win)) == by_command


# ------------------------------------------------------------------ the pick
def picked(bar):
    return bar.selected_pages()


def test_a_pick_moves_with_the_sheets_it_named(win):
    win.sidebar.select_pages((0, 2))
    delete(win, (1,))
    assert picked(win.sidebar) == (0, 1)


def test_an_undo_puts_the_pick_back_on_the_sheets_it_named(win):
    """The pick F10-4f leaves lost: nothing re-selects after a rebuild on the way back."""
    win.sidebar.select_pages((0, 2))
    delete(win, (1,))
    assert picked(win.sidebar) == (0, 1)
    win.view.undo_edit()
    assert picked(win.sidebar) == (0, 2)


def test_a_pick_whose_pages_all_went_is_not_carried_onto_their_neighbours(win):
    """A selection is allowed to shrink; a reader is not allowed to be nowhere.

    Nothing is picked after a cut that took every picked sheet, and what the strip
    shows instead is the row the reader is on: the marker for "here" and the pick
    are the same channel in this widget, so an empty pick reads as that one row
    rather than as nothing.  Splitting the two is a roadmap line of its own; what
    this guards is that the sheets *beside* the cut are not quietly picked instead,
    which is what answering with ``where`` would have done.
    """
    win.sidebar.select_pages((1, 2, 3))
    delete(win, (1, 2, 3))
    assert picked(win.sidebar) == (0,), "the reader's own row, and nothing else"
    win.view.undo_edit()
    # The sheets came back and the pick is still gone: what went, went.
    assert picked(win.sidebar) == (0,)


def test_a_pick_partly_deleted_keeps_the_sheets_that_stayed(win):
    win.sidebar.select_pages((0, 1, 4))
    delete(win, (1,))
    assert picked(win.sidebar) == (0, 3)


def test_a_turn_leaves_the_pick_alone(win):
    win.sidebar.select_pages((1, 3))
    assert win.view.apply_edit(RotatePages(pages=(1,), delta=90))
    assert picked(win.sidebar) == (1, 3)


def test_a_comment_leaves_the_pick_alone(win):
    """Only a step that moved sheets moves a pick, and only once."""
    win.sidebar.select_pages((1, 3))
    delete(win, (0,))
    assert picked(win.sidebar) == (0, 2)
    win.sidebar.invalidate_thumbnails(win.view.session.version + 1,
                                      int(Scope.COMMENTS))
    assert picked(win.sidebar) == (0, 2)


# ------------------------------------------------------- the saved position
def test_the_position_written_after_a_delete_names_the_sheet_the_reader_is_on(
        win, book):
    """The store is a copy of the view, so fixing the view fixes it.

    ``viewState/<path>`` is written on a tab change and on close and never by an
    edit; the number it holds is only honest if ``current_page`` was on the sheet
    the reader meant at the moment it was copied.
    """
    from pdfar.main import view_state_key

    win.view.goto_page(3)
    delete(win, (1,))
    tab = win._active_tab()
    win._save_tab_state(tab)
    saved = QSettings("PDFAR", "PDFAR").value(view_state_key(book))
    assert saved["page"] == win.view.current_page == 2
    doc = win.view.session.doc
    assert identities(doc)[saved["page"]] == SHEETS[3], "the sheet, not the number"


def test_a_session_can_be_asked_where_its_pages_went(book):
    """The map is published for the transition, which is what a consumer reads."""
    session = make_session(book)
    try:
        session.apply(DeletePages(pages=(1,)))
        assert session.renumbering is not None
        assert session.renumbering.followed(2) == 1
        session.undo()
        assert session.renumbering.backward is True
        assert session.renumbering.followed(1) == 2
    finally:
        session.close()
