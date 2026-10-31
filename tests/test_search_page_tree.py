"""F10-4n: the search's matches move with the sheets they were found on.

A row of the results dock is a page number and a sentence about it: *"page 4 holds
this word, here."*  The number is 0-based and belongs to the page tree as it stood
when the search ran, and until now nothing re-aimed it — a delete of page 2 left
the dock offering "Page 4" for a book in which the sheet that was 4 now wears 3.
Worse, clicking such a row asked the view for a hit it no longer had (its own list
*is* dropped by ``Scope.STRUCTURE``) and fell back to ``goto_page``, so the reader
landed on a neighbour that does not contain the term at all, next to a row that
promised it did.  This is the third place PDFAR holds a page number outside the
document: the bookmarks were re-aimed in F10-4g, the strip's pick in F10-4h, the
saved reading position in F10-4l.

The step already says what it did to the numbers, so the fix is that map asked of
each hit.  What decides the shape is *which* steps may be answered that way:

* A step that only re-counted the sheets — a delete, a move, the undo of either —
  did not touch a word of any survivor.  Its matches are still exactly where they
  were, on a sheet that now wears another number, so the search survives the edit
  and every row moves with its sheet.  Clearing it instead would make the reader
  search again because a page somewhere else left the book.
* A hit whose own sheet went is dropped rather than re-pointed.  ``where`` sends a
  number to the sheet that took it, which is right for a reader (they cannot be
  nowhere) and wrong for a row: the neighbour may not contain the term, and a row
  that quotes a sentence its page does not hold is a lie with a page number on it.
  A match is allowed to disappear, so ``followed`` is the rule here.
* A turn is STRUCTURE too and moves no numbers at all, so there is nothing to
  re-aim and the highlights go the way they always did.  The rows keep the numbers
  they show, which are still the sheets they named.

One map, spent once by its identity, because a structure rebuild is not proof that
a step just ran; and the view is where it is spent — the dock's rows are a display
of the view's hit list rather than a second copy of the truth, so the view
renumbers, says so, and the rows are rebuilt from what it now holds.  The limit
that comes with spending a step rather than deriving from the history is that an
undo cannot bring back the matches that went out with a deleted sheet: the pages
come back, the search does not re-find them, and a test pins that rather than
letting it surprise anyone.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem, QMessageBox

from pdfar.edits import Scope
from pdfar.main import HIGHLIGHT_ROLE, MainWindow
from pdfar.pages import DeletePages, RotatePages
from pdfar.search import SearchHit, SearchParams, SearchWorker
from pdfar.viewer import PDFView

#: The word every sheet above the first one carries.
TERM = "alpha"

#: How many sheets the fixture book has, and which original sheet each number
#: holds: ``SHEETS[n]`` is the first line of the sheet that was page ``n``.
PAGES = 6
SHEETS = ["Hello PDFAR world"] + [f"Page {n} content" for n in range(2, PAGES + 1)]


def wait_until(app, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """A modal box would block the offscreen run, and none of this needs one."""
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def book(tmp_path):
    """Six self-naming sheets, each with the searched word on it but the first."""
    return build_sample_pdf(tmp_path / "book.pdf", pages=PAGES, width=200, height=300)


def _search(path, params):
    """Run the worker over *path* the way the dock's thread does, and wait."""
    hits = []
    session = make_session(path)
    try:
        worker = SearchWorker(session, params)
        worker.done.connect(hits.extend)
        worker.run()
    finally:
        session.close()
    return hits


def sheet_at(view, page):
    """Which original sheet the number *page* names in the tree as it stands."""
    return view.session.doc[page].get_text().splitlines()[0]


def held_pages(view):
    """The pages the view actually paints a match on."""
    return sorted({page for page, _rect in view._hit_flat})


def rows(win):
    """What the dock offers: the page each row names and the text it shows."""
    return [(win.results.item(i).data(int(Qt.ItemDataRole.UserRole)),
             win.results.item(i).text())
            for i in range(win.results.count())]


def marked(win):
    """The words each row highlights, by the spans the delegate paints."""
    out = []
    for i in range(win.results.count()):
        item = win.results.item(i)
        spans = item.data(HIGHLIGHT_ROLE) or []
        out.append([item.text()[start:end] for start, end in spans])
    return out


# ------------------------------------------------------------------ the view
@pytest.fixture
def hit_view(qapp, book):
    """A real view over the book with the search already run on it."""
    view = PDFView(book)
    view.resize(600, 400)
    view.show()
    qapp.processEvents()
    view.set_search_hits(_search(book, SearchParams(TERM)))
    try:
        yield view
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_the_search_found_every_sheet_that_says_the_word(hit_view):
    """The starting point: one match per sheet but the first, in page order."""
    assert held_pages(hit_view) == [1, 2, 3, 4, 5]
    assert hit_view.search_hit_pages() == [1, 2, 3, 4, 5]


def test_a_step_that_recounts_leaves_each_match_on_its_own_sheet(hit_view):
    """The rectangle is the match; the number is only what its sheet wears.

    Deleting a page above a hit moved that hit down without changing a word of
    the sheet it sits on, so the highlight has to come along rather than go.
    """
    before = {page: list(rects) for page, rects in hit_view._hit_rects.items()}
    assert hit_view.apply_edit(DeletePages(pages=(1,)))

    # The sheet that was page 2 is page 1 now, and its match did not move on it.
    assert sheet_at(hit_view, 1) == SHEETS[2]
    assert hit_view._hit_rects[1] == before[2]
    assert held_pages(hit_view) == [1, 2, 3, 4]


def test_a_match_on_a_deleted_sheet_is_not_handed_to_its_neighbour(hit_view):
    """``followed``, not ``where``: a row about a page that went says nothing.

    Landing the deleted sheet's match on the sheet that took its number would
    offer the reader a page that does not contain the word, in a list whose every
    other row is a quotation.
    """
    assert hit_view.apply_edit(DeletePages(pages=(1,)))

    for page in held_pages(hit_view):
        assert TERM in hit_view.session.doc[page].get_text()
    assert len(hit_view._hit_flat) == PAGES - 2


def test_the_view_holds_one_page_for_each_row_in_the_order_they_were_built(hit_view):
    """The dock's rows and this list are the same list of matches.

    Row ``k`` was built from hit ``k``, so the answer a re-aim gives has to be
    per hit and in that order — with a hole where a row's own sheet went out.
    """
    assert hit_view.apply_edit(DeletePages(pages=(1,)))
    assert hit_view.search_hit_pages() == [None, 1, 2, 3, 4]


def test_the_current_match_follows_its_sheet_and_counts_the_survivors(hit_view):
    """The reader stays on the match they were looking at, not on its old index.

    Dropping an earlier sheet's match shifts the list under the current one, so
    the count that comes out of the re-aim is the position the reader is at now
    and the total that is left.
    """
    emitted = []
    hit_view.hit_changed.connect(lambda current, total: emitted.append((current, total)))
    index = hit_view.first_hit_index(3)
    assert hit_view.goto_hit_index(index) is True
    rect = hit_view._current_hit_rect()[1]

    assert hit_view.apply_edit(DeletePages(pages=(1,)))

    assert hit_view._current_hit_rect() == (2, rect)
    assert emitted[-1] == (hit_view._hit_cur + 1, len(hit_view._hit_flat))
    assert hit_view._hit_cur == 1, "the first surviving sheet's match went with it"


def test_a_rebuild_that_is_not_a_step_does_not_move_the_matches_again(hit_view):
    """Spent once, by the identity of the map: a second spend is a silent drift.

    A structure rebuild is not proof that a page-tree step just ran, and the map
    the last one published is still the session's answer when one does not.  The
    rebuild cannot say what moved, so the highlights end the way they always did —
    which is the conservative half of the promise, and the reason the search
    survives a delete rather than every edit that follows one.
    """
    assert hit_view.apply_edit(DeletePages(pages=(1,)))
    assert held_pages(hit_view) == [1, 2, 3, 4]

    hit_view.invalidate(Scope.STRUCTURE)

    assert held_pages(hit_view) == []
    assert hit_view.search_hit_pages() == []


def test_a_turn_moves_no_numbers_so_the_highlights_go_as_they_always_did(hit_view):
    """Nothing to re-aim, and nothing to say: a turn says nothing about numbers.

    A turned sheet's match is no longer at the rectangle the search found, and the
    step does not publish a map at all — so the highlights end, while the rows that
    survive keep the numbers they show, which are still the sheets they named.
    """
    assert hit_view.apply_edit(RotatePages(pages=(2,), delta=90))

    assert hit_view._hit_flat == []
    assert hit_view.search_hit_pages() == []


def test_a_cut_at_the_end_takes_its_match_and_moves_no_other(hit_view):
    """A step can vanish a sheet without moving a single number.

    Deleting the last page leaves the map as the identity, which is the shape a
    "nothing re-counted, skip it" rule mistakes for a turn — and the match that was
    on that page has nowhere to be while the four above it have not moved.
    """
    assert hit_view.apply_edit(DeletePages(pages=(PAGES - 1,)))

    assert hit_view.search_hit_pages() == [1, 2, 3, 4, None]
    assert held_pages(hit_view) == [1, 2, 3, 4]


def test_undoing_a_delete_sends_the_survivors_home_without_the_ones_that_went(hit_view):
    """The walk back is the same map run the other way.

    The pages come back and the matches on the deleted sheet do not: a step is
    spent here, not derived from the history, and this is the limit that comes
    with it — pinned so it is a known edge rather than a surprise.
    """
    assert hit_view.apply_edit(DeletePages(pages=(1,)))
    assert hit_view.search_hit_pages() == [None, 1, 2, 3, 4]

    assert hit_view.undo_edit()

    assert hit_view.search_hit_pages() == [2, 3, 4, 5]
    assert held_pages(hit_view) == [2, 3, 4, 5]
    assert sheet_at(hit_view, 2) == SHEETS[2]


def test_redo_moves_the_matches_out_again(hit_view):
    """Four rows, not five: the list is the matches this search still has."""
    assert hit_view.apply_edit(DeletePages(pages=(1,)))
    assert hit_view.undo_edit()
    assert hit_view.redo_edit()
    assert hit_view.search_hit_pages() == [1, 2, 3, 4]
    assert held_pages(hit_view) == [1, 2, 3, 4]


def test_two_steps_move_the_matches_by_their_own_maps_each(hit_view):
    """Each step spends its own map, so the numbering is never applied twice."""
    assert hit_view.apply_edit(DeletePages(pages=(1,)))
    assert hit_view.search_hit_pages() == [None, 1, 2, 3, 4]
    assert hit_view.apply_edit(DeletePages(pages=(3,)))

    pages = [page for page in hit_view.search_hit_pages() if page is not None]
    assert pages == held_pages(hit_view)
    for page in pages:
        assert TERM in hit_view.session.doc[page].get_text()


def test_a_delete_that_takes_every_matched_sheet_leaves_nothing_to_offer(hit_view):
    assert hit_view.apply_edit(DeletePages(pages=(1, 2, 3, 4, 5)))
    assert hit_view._hit_flat == []
    assert all(page is None for page in hit_view.search_hit_pages())


def test_a_step_that_also_rewrote_the_pages_ends_the_highlights(hit_view):
    """A re-count the search may follow, and a rewrite it may not.

    Only the numbering is evidence here: a step that also changed what is on the
    sheets can have moved the words the rectangles point at, and a highlight on the
    wrong sentence is worse than no highlight.  No edit does both today — a delete
    is STRUCTURE alone — so the map and the scope are handed over directly.
    """
    from pdfar.edits import Renumbering

    hit_view.session.renumbering = Renumbering((0, 2, 3, 4, 5))
    hit_view.invalidate(Scope.STRUCTURE | Scope.CONTENT)

    assert held_pages(hit_view) == []
    assert hit_view.search_hit_pages() == []


def test_a_view_that_never_searched_moves_nothing_and_says_nothing(qapp, book):
    """The re-aim is spent on a hit list that does not exist, so no signal."""
    view = PDFView(book)
    view.resize(600, 400)
    seen = []
    view.hits_renumbered.connect(lambda: seen.append(1))
    try:
        assert view.apply_edit(DeletePages(pages=(1,)))
        assert seen == []
        assert view.search_hit_pages() == []
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


# ----------------------------------------------------------------- the dock
@pytest.fixture
def win(qapp, book):
    """The real main window over the book, with the search run through the dock."""
    window = MainWindow()
    window.resize(700, 500)
    window.show()
    qapp.processEvents()
    assert window.open_document(book)
    qapp.processEvents()
    window.query_edit.setText(TERM)
    window.start_search()
    assert wait_until(qapp, lambda: window.search_thread is not None
                      and not window.search_thread.isRunning())
    assert wait_until(qapp, lambda: window.results.count() > 0)
    qapp.processEvents()
    try:
        yield window
    finally:
        window._close_all_tabs(confirm=False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def delete(win, pages):
    """Move the page tree the way the document does, with no dialog in the way."""
    assert win.view.apply_edit(DeletePages(pages=tuple(sorted(pages))))
    QApplication.processEvents()


def test_a_row_names_the_page_its_match_sits_on_now(win):
    """Five rows, one per matched sheet; take one out and they move down.

    The snippet keeps quoting the sheet — that is what the reader searched for —
    while the number in front of it follows the tree.
    """
    assert [page for page, _text in rows(win)] == [1, 2, 3, 4, 5]

    delete(win, (1,))

    assert rows(win) == [
        (1, "Page 2: Page 3 content alpha beta gamma"),
        (2, "Page 3: Page 4 content alpha beta gamma"),
        (3, "Page 4: Page 5 content alpha beta gamma"),
        (4, "Page 5: Page 6 content alpha beta gamma"),
    ]


def test_the_row_for_a_deleted_sheet_stops_being_offered(win):
    """The harm in one assertion: nobody is invited to a page that has no match."""
    delete(win, (1,))
    for page, _text in rows(win):
        assert page < win.view.page_count
        assert TERM in win.view.session.doc[page].get_text()


def test_clicking_a_moved_row_lands_on_the_sheet_that_holds_the_word(win):
    """What the reader gets when they take the dock at its word.

    Before this, the row still named page 1 while page 1 was another sheet, and
    the click fell through to ``goto_page`` because the view had no hits left.
    """
    delete(win, (1,))
    win._on_result_clicked(win.results.item(0))
    QApplication.processEvents()

    assert win.view.current_page == 1
    assert TERM in win.view.session.doc[win.view.current_page].get_text()
    assert win.view._current_hit_rect()[0] == 1
    assert win.hit_label.text().startswith("Hit ")


def test_the_status_line_says_what_the_move_cost(win):
    delete(win, (1,))
    text = win.status_left.text()
    assert "4" in text and "1" in text, text
    assert "search" in text.lower()


def test_a_cut_at_the_end_of_the_book_costs_its_own_match_only(win):
    """The count that says what was lost, which is the one the reader needs.

    A cut at the end moves no number and still takes a sheet out, so the row above
    it has nowhere to be: four results name their sheet and one has gone, and that
    is what the line says whatever the numbering did.
    """
    delete(win, (PAGES - 1,))

    assert [page for page, _t in rows(win)] == [1, 2, 3, 4]
    text = win.status_left.text()
    assert text.startswith("1 search result"), text
    assert "4 still name their sheet" in text, text


def test_a_match_that_goes_out_takes_the_notice_with_it(win):
    """The reader was standing on the sheet that left: nothing is the current hit.

    ``Hit 1 / 5`` over four matches that are not the one being looked at is the
    same lie in a smaller font, so the count that comes back from the re-aim says
    there is no current match at all.
    """
    assert win.hit_label.text() == "Hit 1 / 5"
    assert win.view._hit_flat[0][0] == 1, "the first match is the sheet being cut"

    delete(win, (1,))

    assert win.hit_label.text() == ""


def test_a_row_whose_prefix_changed_width_still_marks_the_word(qapp, tmp_path):
    """``Page 10:`` becomes ``Page 9: ``: the highlight offsets move with the text.

    The spans are offsets into the row's whole label, prefix included, so a
    re-aim that writes a narrower prefix without shifting them underlines the
    wrong letters — a one-character drift nobody would notice until the wrong
    word was yellow.
    """
    wide = build_sample_pdf(tmp_path / "wide.pdf", pages=11, width=200, height=300)
    window = MainWindow()
    window.resize(700, 500)
    window.show()
    qapp.processEvents()
    try:
        assert window.open_document(wide)
        qapp.processEvents()
        window.query_edit.setText(TERM)
        window.start_search()
        assert wait_until(qapp, lambda: window.results.count() > 0)
        qapp.processEvents()
        assert [page for page, _t in rows(window)] == list(range(1, 11))

        delete(window, (1,))

        moved = [text for _p, text in rows(window)]
        assert moved[7].startswith("Page 9: Page 10 content"), moved[7]
        for spans in marked(window):
            assert spans == ["alpha"], spans
    finally:
        window._close_all_tabs(confirm=False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_a_turn_keeps_the_rows_naming_the_sheets_they_always_named(win):
    """No number moved, so the dock has nothing to be told and nothing to lose."""
    before = rows(win)
    assert win.view.apply_edit(RotatePages(pages=(1,), delta=90))
    assert rows(win) == before


def test_a_cut_below_every_match_moves_nothing_and_says_nothing(qapp, book):
    """Silence where nothing changed, so the message means something.

    One match on the first sheet and a page taken out below it: the number the row
    names is still its own, and a status line that comments on every page-tree edit
    is a status line the reader learns to ignore.
    """
    window = MainWindow()
    window.resize(700, 500)
    window.show()
    qapp.processEvents()
    try:
        assert window.open_document(book)
        qapp.processEvents()
        window.query_edit.setText("PDFAR")
        window.start_search()
        assert wait_until(qapp, lambda: window.results.count() > 0)
        qapp.processEvents()
        assert [page for page, _t in rows(window)] == [0]
        before = rows(window)
        said = window.status_left.text()

        delete(window, (3,))

        assert rows(window) == before
        assert window.status_left.text() == said
    finally:
        window._close_all_tabs(confirm=False)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_the_dock_and_the_view_never_disagree_after_a_move(win):
    """One truth, two displays: a row exists exactly where the view has a match."""
    delete(win, (1,))
    assert [page for page, _t in rows(win)] == [
        page for page in win.view.search_hit_pages() if page is not None]


def test_two_cuts_in_a_row_keep_the_rows_and_the_hits_in_step(win):
    """The dock prunes its own list with its rows, or the second move mispairs.

    The view drops the holes it is left with before it applies the next map, so
    its list shrinks with every cut; if the dock keeps the matches that already
    went out, the pairing that starts the second move is off by one and each row
    is written with another sheet's number.
    """
    delete(win, (1,))
    delete(win, (1,))

    assert rows(win) == [
        (1, "Page 2: Page 4 content alpha beta gamma"),
        (2, "Page 3: Page 5 content alpha beta gamma"),
        (3, "Page 4: Page 6 content alpha beta gamma"),
    ]
    assert [page for page, _t in rows(win)] == [
        page for page in win.view.search_hit_pages() if page is not None]


def test_a_search_that_found_nothing_is_not_reported_as_moved(win):
    """Silence in the ordinary case: a list of nothing has nothing to move.

    A status line that comments on every page-tree edit would train the reader
    past the one that says their matches went out with a sheet.
    """
    win.query_edit.setText("zebra")
    win.start_search()
    assert wait_until(QApplication.instance(),
                      lambda: win.btn_search.isEnabled() and not win.search_thread.isRunning())
    QApplication.processEvents()
    assert win.results.count() == 0
    said = win.status_left.text()

    delete(win, (1,))

    assert win.results.count() == 0
    assert win.status_left.text() == said


def test_the_row_the_reader_is_on_is_selected_after_the_move(win):
    """The rows are rebuilt *before* the notice, or the selection is lost with them.

    ``hit_changed`` finds the reader's row by the page it names, so a row list
    still wearing the old numbering either selects the wrong row or has its
    selection wiped a moment later by the rebuild.
    """
    win.view.goto_hit_index(2)
    assert win.results.currentRow() == 2

    delete(win, (1,))

    assert win.hit_label.text() == "Hit 2 / 4"
    assert win.results.currentRow() == 1
    assert win.results.item(1).text().startswith("Page 3: Page 4 content")


def test_matches_with_no_rectangles_still_move_their_rows(win):
    """A page that matched without a word box is still a page the reader searched for.

    The notice has nothing to count here, so the hit list alone has to be the
    reason the dock is told — otherwise those rows keep the numbers their sheets
    left behind while every other row moves.
    """
    hits = [SearchHit(page=page, snippet=f"Page {page + 1} content alpha",
                      rects=[]) for page in (2, 3, 4)]
    win.view.set_search_hits(hits)
    win.results.clear()
    win._search_hits = list(hits)
    for hit in hits:
        item = QListWidgetItem()
        win._fill_result_row(item, hit.page, hit.snippet, None)
        win.results.addItem(item)
    assert win.hit_label.text() == ""

    delete(win, (1,))

    assert [page for page, _t in rows(win)] == [1, 2, 3]


def test_a_document_with_no_search_at_all_hears_the_move_in_silence(qapp, book):
    """The view re-aims a hit list it never had, and the dock is not told."""
    window = MainWindow()
    window.resize(700, 500)
    window.show()
    qapp.processEvents()
    try:
        assert window.open_document(book)
        qapp.processEvents()
        assert window.results.count() == 0
        said = window.status_left.text()

        delete(window, (1,))

        assert window.results.count() == 0
        assert window.status_left.text() == said
    finally:
        window._close_all_tabs(confirm=False)
        window.close()
        window.deleteLater()
        qapp.processEvents()
