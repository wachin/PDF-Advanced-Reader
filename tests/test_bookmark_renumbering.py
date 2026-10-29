"""F10-4g: the page number a bookmark holds belongs to a sheet, not to a file.

A user bookmark lives in ``QSettings`` under ``bookmarks/<absolute path>`` and in a
row of the sidebar's tree, and it holds a **0-based page number**.  That number is
the one thing a page-tree edit rewrites everywhere except in the document: take
page 2 out of a book of six and every page above the gap now wears the number of
the one below it, so a bookmark that meant the sheet it was made on now means the
neighbour.  Nothing else in the tree is a bookmark, and nothing else is user data
the reader cannot see, which is why a silently re-pointed mark is worse than a
silently re-pointed thumbnail.

The document's own undo cannot reach it, and neither can a snapshot of it.  The
session restores whole documents, and a bookmark the reader made *between* a delete
and its undo is in no document revision: a mechanism that wrote the old list back
on undo would delete that mark.  So the step says what it did to the numbering --
``order[i]`` is where the sheet now at position ``i`` came from -- and a move is
undone by running that statement the other way, which is exact for every bookmark
that now points at a sheet that exists.  A bookmark whose own page went cannot
return to a number that names nothing: it stays on the sheet it was moved onto,
which is the honest answer and the one every assertion here is written against.

Three measured facts about the neighbours decide shapes:

* ``DeletePages.changed`` is ``None`` -- the pages a page-tree edit *names* are not
  the pages a page-number cache has to re-read.  The order tuple is the same
  answer given properly: where every page went, not which pages were asked about.
* The STRUCTURE branch of ``Sidebar.invalidate_thumbnails`` rebuilds the thumbnail
  list and the outline and **not** the bookmark tree, so the tree is the one panel
  a page-tree edit leaves showing numbers the store no longer holds.
* A rotation is STRUCTURE too, and moves no page.  An edit that leaves the
  numbering alone has to *say* so, or the sidebar would replay the last delete's
  move over the bookmarks again.
* Dragging the thumbnail size rebuilds the same strip at ``Scope.FULL`` with no
  version at all.  A structure rebuild is therefore not proof that a step just
  ran, and the map lives as long as the generation it describes: which step has
  been spent is a question only the version the invalidation names can answer.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import make_session
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from pdfar.bookmarks import BookmarkManager
from pdfar.edits import EditError, Renumbering, SetMetadata, Scope
from pdfar.pages import DeletePages, RotatePages, shifted_index

#: How many pages the fixtures are built with; the sample writes its own number.
PAGES = 6

#: What each sheet says in its first line, in document order.  ``marks`` puts one
#: bookmark on each, named after the page it was made on.
SHEETS = ["Hello PDFAR world"] + [f"Page {n} content" for n in range(2, PAGES + 1)]


def identities(doc):
    """The first line of every page: which original sheet sits at which number."""
    out = []
    for index in range(doc.page_count):
        lines = [line for line in doc[index].get_text().splitlines() if line.strip()]
        out.append(lines[0] if lines else "")
    return out


def reaches(manager, doc):
    """Which sheet each bookmark actually lands on now, named by what it says.

    Comparing numbers would let a wrong map and a wrong expectation cancel out;
    this asks the question the reader asks -- *what do I get when I click it*.
    """
    last = doc.page_count - 1
    return {bm.title: identities(doc)[min(bm.page, last)]
            for bm in manager.get_user_bookmarks()}


@pytest.fixture
def book(tmp_path):
    """Six pages, each naming itself in its first line."""
    path = str(tmp_path / "book.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        page = doc.new_page(width=200, height=100)
        page.insert_text(fitz.Point(10, 30), SHEETS[index], fontsize=12)
        page.insert_text(fitz.Point(10, 70), "alpha beta gamma", fontsize=12)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def session(book):
    handle = make_session(book)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def marks(qapp, session):
    """One bookmark on every sheet, named after the page it was made on.

    ``qapp`` is not decoration: it is what points ``QSettings`` at a throwaway
    directory, and a bookmark manager reads and writes that store.
    """
    manager = BookmarkManager(session)
    for index in range(PAGES):
        manager.add_user_bookmark(f"m{index + 1}", index)
    return manager


def stored(manager):
    """``(title, page)`` for each bookmark, in the order the store holds them."""
    return [(bm.title, bm.page) for bm in manager.get_user_bookmarks()]


def reload_stored(path):
    """What the settings file holds for *path*, read by a manager that never met
    the first one -- the only way to tell a moved bookmark from a moved list."""
    handle = make_session(path)
    try:
        return stored(BookmarkManager(handle))
    finally:
        handle.close()


def tree_rows(bar):
    """``(label, page)`` for each bookmark row in the sidebar's tree."""
    root = bar.bookmarks_tree.topLevelItem(0)
    if root is None:
        return []
    return [(root.child(i).text(0).strip(), root.child(i).data(0, Qt.ItemDataRole.UserRole))
            for i in range(root.childCount())]


# ---------------------------------------------------------------- what an edit says
def test_a_delete_says_where_each_page_came_from():
    """The order is the survivors, in the order they keep."""
    assert DeletePages(pages=(1,)).page_order(6) == (0, 2, 3, 4, 5)
    assert DeletePages(pages=(0, 1, 2)).page_order(6) == (3, 4, 5)
    assert DeletePages(pages=(5,)).page_order(6) == (0, 1, 2, 3, 4)
    assert DeletePages(pages=(1, 3)).page_order(6) == (0, 2, 4, 5)


def test_an_edit_that_leaves_the_page_tree_alone_says_nothing():
    """A turn rewrites a page's /Rotate and moves no sheet.

    Saying nothing is not an optimisation: an edit that answers ``None`` is how
    the sidebar knows not to move the bookmarks, and a rotation is a
    ``STRUCTURE`` edit, so anything keyed to the scope alone would replay the
    previous delete's move on top of it.
    """
    assert RotatePages(pages=(2,), delta=90).page_order(PAGES) is None
    assert SetMetadata(entries=(("title", "x"),)).page_order(PAGES) is None


# ---------------------------------------------------------------------- the map
def test_the_forward_map_names_the_new_number_of_a_sheet_that_stayed():
    move = Renumbering((0, 2, 3, 4, 5))
    assert move.where(0) == 0
    assert move.where(2) == 1
    assert move.where(5) == 4


def test_a_number_whose_page_went_lands_on_the_sheet_that_took_its_place():
    """The rule the reader's own page follows (F10-4f), applied to a bookmark."""
    move = Renumbering(DeletePages(pages=(1, 2)).page_order(6))
    assert move.where(1) == 1     # old page 3 wears the number 1 now
    assert move.where(2) == 1     # and nothing arrives at 2 either
    assert move.where(5) == 3     # a survivor, counted exactly


def test_a_number_past_the_last_survivor_lands_on_the_last_page():
    assert Renumbering((0, 1, 2, 3, 4)).where(5) == 4
    assert Renumbering(()).where(3) == 0


def test_the_map_and_the_rule_the_reader_lands_on_are_one_rule():
    """``shifted_index`` (F10-4f) and the step's map must not drift apart.

    Two ways to answer "where did my page go" is one too many: the window aims the
    reader and their pick with the first, the bookmarks with the second.
    """
    for deleted in ((1,), (0,), (5,), (1, 3), (2, 3, 4), (0, 1, 2, 3, 4)):
        order = DeletePages(pages=deleted).page_order(PAGES)
        move = Renumbering(order)
        for page in range(PAGES):
            assert move.where(page) == shifted_index(page, deleted, PAGES)


def test_the_way_back_sends_each_sheet_to_the_number_it_came_from():
    back = Renumbering((0, 2, 3, 4, 5), backward=True)
    assert [back.where(index) for index in range(5)] == [0, 2, 3, 4, 5]


def test_a_bookmark_moved_onto_a_survivor_stays_on_it_when_the_delete_is_undone():
    """The composition is stable, not identity, and that is the honest answer.

    A bookmark on a page that went has nowhere to come back to; the way back is
    the number of the sheet it was moved onto, so the mark follows that sheet home
    rather than inventing a page.
    """
    move = Renumbering((0, 2, 3, 4, 5))
    back = Renumbering((0, 2, 3, 4, 5), backward=True)
    landed = move.where(1)
    assert back.where(landed) == 2          # the sheet it landed on, at home
    assert back.where(move.where(2)) == 2   # and a survivor is exactly restored


def test_a_sheet_with_no_past_keeps_the_number_it_is_on():
    """A page an edit *invents* has nowhere to go back to; leave it where it is."""
    back = Renumbering((-1, 0, 1, 2), backward=True)
    assert back.where(0) == 0
    assert [back.where(index) for index in (1, 2, 3)] == [0, 1, 2]


# ------------------------------------------------------------------ the session
def test_a_session_says_nothing_about_numbering_until_an_edit_makes_it_say(session):
    assert session.renumbering is None
    session.apply(RotatePages(pages=(1,)))
    assert session.renumbering is None
    session.apply(DeletePages(pages=(2,)))
    assert session.renumbering is not None
    assert session.renumbering.where(3) == 2


def test_the_map_is_read_while_the_document_still_holds_the_old_count(session):
    """``len(order)`` is the page count *after* the step, and nothing else says it.

    The session asks the edit for its order before the working copy is adopted, so
    ``before_count`` is the count the sheets wore going in.  Asked afterwards the
    tuple would be one longer, and the last page would answer with a number that
    belongs to a sheet this document never had.
    """
    session.apply(DeletePages(pages=(1,)))
    assert session.doc.page_count == PAGES - 1
    assert session.renumbering.order == (0, 2, 3, 4, 5)
    assert session.renumbering.where(5) == 4, "the last sheet, counted exactly"


def test_a_turn_clears_the_last_move_rather_than_repeating_it(session):
    """The trap the ``None`` in ``page_order`` exists to spring.

    ``renumbering`` describes the step that produced this generation, so a step
    that moved nothing has to clear it; leaving the delete's map published would
    shift the bookmarks a second time on the next structure rebuild.
    """
    session.apply(DeletePages(pages=(1,)))
    assert session.renumbering is not None
    session.apply(RotatePages(pages=(0,)))
    assert session.renumbering is None


def test_a_refused_delete_leaves_the_session_saying_nothing(session):
    with pytest.raises(EditError):
        session.apply(DeletePages(pages=tuple(range(PAGES))))
    assert session.renumbering is None


def test_undo_publishes_the_way_back_and_redo_the_way_forward(session):
    session.apply(DeletePages(pages=(1,)))
    assert session.renumbering.where(2) == 1

    session.undo()
    assert session.renumbering.backward is True
    assert session.renumbering.where(1) == 2

    session.redo()
    assert session.renumbering.backward is False
    assert session.renumbering.where(2) == 1


def test_a_step_parked_for_redo_still_knows_what_it_did(session):
    """The order belongs to the step, so it travels with it between the stacks."""
    session.apply(DeletePages(pages=(1,)))
    session.undo()
    session.redo()
    assert session.renumbering.where(2) == 1
    session.undo()
    assert session.renumbering.backward is True
    assert session.renumbering.where(1) == 2


# ------------------------------------------------------------------- the bookmarks
def test_a_delete_moves_the_reader_s_bookmarks_with_their_sheets(marks, session):
    marks.renumber(Renumbering((0, 2, 3, 4, 5)).where)
    assert stored(marks) == [("m1", 0), ("m2", 1), ("m3", 1), ("m4", 2),
                             ("m5", 3), ("m6", 4)]
    assert len(marks.get_user_bookmarks()) == PAGES


def test_a_bookmark_on_a_page_that_went_is_not_lost(marks):
    """It lands on the sheet that took the number, sharing it with that sheet's own."""
    marks.renumber(Renumbering((0, 2, 3, 4, 5)).where)
    pages = dict(stored(marks))
    assert pages["m2"] == pages["m3"] == 1
    assert pages["m6"] == 4


def test_a_bookmark_below_the_gap_does_not_move(marks):
    marks.renumber(Renumbering((0, 2, 3, 4, 5)).where)
    assert dict(stored(marks))["m1"] == 0


def test_the_bookmarks_that_moved_are_written_back_to_the_store(book, marks):
    marks.renumber(Renumbering((0, 2, 3, 4, 5)).where)
    assert reload_stored(book) == stored(marks)


def test_a_move_that_changes_nothing_writes_nothing(marks):
    """One write when the marks move, none when they do not.

    The store is rewritten on every structure rebuild that moves a page, so a
    ``QSettings`` write per bookmark per rebuild -- or per rebuild that changed
    nothing at all -- is the cost this guards.
    """
    writes = []
    marks._save_user_bookmarks = lambda: writes.append(1)

    marks.renumber(Renumbering(tuple(range(PAGES))).where)
    assert writes == []
    assert stored(marks) == [(f"m{n}", n - 1) for n in range(1, PAGES + 1)]

    marks.renumber(Renumbering((0, 2, 3, 4, 5)).where)
    assert writes == [1], "one write for the move, not one per mark"


def test_the_renumbering_keeps_the_list_in_page_order(marks):
    """``add_user_bookmark`` builds the list in page order and the tree shows it."""
    marks.renumber(Renumbering((5, 4, 3, 2, 1, 0)).where)
    pages = [page for _title, page in stored(marks)]
    assert pages == sorted(pages)


# ------------------------------------------------- the move, undone without snapshots
def test_undoing_a_delete_takes_every_bookmark_home(strip_with_marks):
    """Each mark that was on a surviving sheet names it again after the undo."""
    bar, book = strip_with_marks
    manager, session = bar.bookmark_manager, bar.session
    home = dict(reaches(manager, session.doc))

    session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    session.undo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))

    back = reaches(manager, session.doc)
    for title in ("m1", "m3", "m4", "m5", "m6"):
        assert back[title] == home[title]
    assert back["m2"] == SHEETS[2], "the sheet it was moved onto, not the one that went"
    assert reload_stored(book) == stored(manager)


def test_a_bookmark_made_between_a_delete_and_its_undo_keeps_its_page(
        strip_with_marks):
    """The reason this is a map and not a snapshot.

    Undo restoring the list as it was before the delete would take out a mark the
    reader made since, because that mark is in no document revision.  Moving each
    bookmark with its sheet leaves the new one on the sheet it was made on.
    """
    bar, _book = strip_with_marks
    manager, session = bar.bookmark_manager, bar.session

    session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    manager.add_user_bookmark("made after", 3)

    session.undo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    pages = dict(stored(manager))
    assert pages["made after"] == 4, "its number moved with its sheet"
    assert reaches(manager, session.doc)["made after"] == SHEETS[4]
    assert len(pages) == PAGES + 1, "and none of the marks that were there before went"


def test_redo_moves_the_bookmarks_out_again(strip_with_marks):
    bar, _book = strip_with_marks
    manager, session = bar.bookmark_manager, bar.session
    session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    after = stored(manager)
    session.undo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    session.redo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    assert stored(manager) == after


def test_two_deletes_undone_name_their_own_sheets_again(strip_with_marks):
    """The second delete takes out the page the first one had parked a mark on.

    ``m2`` was moved onto old page 3 and ``m5`` was deleted outright, so those two
    are the ones allowed to have moved; the rest have to be back on their sheets
    after two undos, which is the only promise a map can keep.
    """
    bar, _book = strip_with_marks
    manager, session = bar.bookmark_manager, bar.session
    home = dict(reaches(manager, session.doc))

    session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    session.apply(DeletePages(pages=(3,)))
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    assert len(manager.get_user_bookmarks()) == PAGES

    session.undo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))
    session.undo()
    bar.invalidate_thumbnails(session.version, int(Scope.STRUCTURE))

    back = reaches(manager, session.doc)
    for title in ("m1", "m3", "m4", "m6"):
        assert back[title] == home[title]
    assert back["m2"] == SHEETS[2]
    assert back["m5"] == SHEETS[5], "its own page went, so it wears the number above"


# ------------------------------------------------------------------ the sidebar
def test_the_bookmark_rows_move_with_the_bookmarks(strip_with_marks):
    """The tree is a copy of the store, and a STRUCTURE edit rebuilt everything
    *except* it."""
    bar, _book = strip_with_marks
    bar.session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))
    assert tree_rows(bar) == [
        ("m1 (p. 1)", 0), ("m2 (p. 2)", 1), ("m3 (p. 2)", 1), ("m4 (p. 3)", 2),
        ("m5 (p. 4)", 3), ("m6 (p. 5)", 4)]


def test_a_followed_bookmark_reaches_the_sheet_it_was_made_on(strip_with_marks):
    """What the reader pays for the move: clicking the row must land on the page."""
    bar, _book = strip_with_marks
    reached = []
    bar.goto_page_requested.connect(reached.append)
    bar.session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))

    root = bar.bookmarks_tree.topLevelItem(0)
    row = next(root.child(i) for i in range(root.childCount())
               if root.child(i).text(0).strip().startswith("m4"))
    bar._on_bookmark_activated(row, 0)
    assert reached == [2]
    assert identities(bar.session.doc)[reached[0]] == SHEETS[3]


def test_a_turn_leaves_the_bookmark_rows_alone(strip_with_marks):
    bar, _book = strip_with_marks
    before = tree_rows(bar)
    bar.session.apply(RotatePages(pages=(1,)))
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))
    assert tree_rows(bar) == before


def test_a_comment_leaves_the_bookmark_rows_alone(strip_with_marks):
    """Neither the scope nor the version alone is enough to move a mark.

    The delete's map is still the session's answer when a comment lands, and a
    comment is a new version: what keeps the marks where they are is that the page
    tree did not move.
    """
    bar, _book = strip_with_marks
    bar.session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))
    moved = stored(bar.bookmark_manager)
    rows = tree_rows(bar)
    assert moved[1] == ("m2", 1)

    bar.invalidate_thumbnails(bar.session.version + 1, int(Scope.COMMENTS))
    assert stored(bar.bookmark_manager) == moved
    assert tree_rows(bar) == rows


def test_changing_the_thumbnail_width_does_not_move_the_marks_again(
        strip_with_marks):
    """A structure rebuild that is not an edit must not spend the map a second time.

    Dragging the thumbnail size rebuilds the strip at ``Scope.FULL`` with no
    version, and the map the last page-tree step published is still the session's
    answer then.  Rebuilds of that kind are what a per-generation cache lives
    through, and a bookmark is exactly such a cache.
    """
    from pdfar.sidebar import ThumbnailSettings

    bar, _book = strip_with_marks
    bar.session.apply(DeletePages(pages=(1,)))
    bar.invalidate_thumbnails(bar.session.version, int(Scope.STRUCTURE))
    after = stored(bar.bookmark_manager)

    bar.apply_thumbnail_settings(
        ThumbnailSettings(width=bar._thumb_settings.width + 20))
    assert stored(bar.bookmark_manager) == after
    assert [label for label, _page in tree_rows(bar)] == [
        f"{title} (p. {page + 1})" for title, page in after]


def test_a_revert_leaves_no_move_to_spend(session):
    """The history went with the edits, and the map they described with it."""
    session.apply(DeletePages(pages=(1,)))
    assert session.renumbering is not None
    session.revert()
    assert session.renumbering is None


@pytest.fixture
def strip_with_marks(qapp, book):
    """The real sidebar over the six-page book, with a bookmark on every sheet."""
    from pdfar.render import RenderPool
    from pdfar.sidebar import Sidebar

    session = make_session(book)
    pool = RenderPool(session, workers=1)
    bar = Sidebar(session, pool)
    manager = bar.bookmark_manager
    for index in range(PAGES):
        manager.add_user_bookmark(f"m{index + 1}", index)
    bar._build_bookmarks()
    qapp.processEvents()
    try:
        yield bar, book
    finally:
        pool.shutdown()
        bar.deleteLater()
        session.close()
        qapp.processEvents()


# ------------------------------------------------------------------ through the window
class _Answers:
    def __init__(self):
        self.asked = []
        self.reply = QMessageBox.StandardButton.Yes


@pytest.fixture(autouse=True)
def answers(monkeypatch):
    """Stand in for every box this feature raises: a modal would block the run.

    Anything offering to *save* is answered "don't", because teardown closes a
    document the test made dirty.
    """
    box = _Answers()

    def fake_question(_parent, title, text, buttons, *rest):
        box.asked.append((title, text))
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return box.reply

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    return box


@pytest.fixture
def window(qapp, book):
    """The real main window on the six-page book, with a mark on every sheet.

    ``MainWindow`` connects the view's ``content_changed`` to the sidebar, so the
    bookmarks move because the document did: nothing in the delete command
    mentions them.
    """
    from pdfar.main import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.open_document(book)
    qapp.processEvents()
    manager = win.sidebar.bookmark_manager
    for index in range(PAGES):
        manager.add_user_bookmark(f"m{index + 1}", index)
    try:
        yield win
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_the_window_moves_the_marks_after_its_own_delete(window):
    manager = window.sidebar.bookmark_manager
    home = dict(reaches(manager, window.view.session.doc))

    window._delete_pages((1,))
    after = reaches(manager, window.view.session.doc)
    assert after["m4"] == home["m4"], "the mark followed its own sheet"
    assert after["m1"] == home["m1"]

    window._undo_edit()
    back = reaches(manager, window.view.session.doc)
    assert back["m4"] == home["m4"]
    assert back["m2"] == SHEETS[2]


def test_the_moved_marks_are_what_a_reopened_document_reads(window, book, qapp):
    """The point of re-writing the store rather than the widget.

    A bookmark that moved only in the tree would come back at its old number the
    next time the book is opened -- which is the corruption outliving the session
    that noticed it.  The file is saved first because that is the case which
    matters: the marks and the page tree they count have to agree afterwards.
    """
    from pdfar.main import MainWindow

    manager = window.sidebar.bookmark_manager
    window._delete_pages((1,))
    written = stored(manager)
    assert window._save_tab(window._active_tab()) is True, \
        "the reopened book has to be the one with the page out"
    assert reload_stored(book) == written

    window._close_document()
    qapp.processEvents()
    again = MainWindow()
    again.show()
    qapp.processEvents()
    try:
        again.open_document(book)
        qapp.processEvents()
        assert stored(again.sidebar.bookmark_manager) == written
        assert reaches(again.sidebar.bookmark_manager,
                       again.view.session.doc)["m4"] == SHEETS[3]
    finally:
        again._close_document()
        again.close()
        again.deleteLater()
        qapp.processEvents()
