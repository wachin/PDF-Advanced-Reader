"""F10-4s: the strip takes the drop, and the document does the moving.

``gap_for_drop`` (``tests/test_page_drop.py``) is the arithmetic; this is the surface it
was written for.  A `QListWidget` already knows how to drag its own rows and put them
back in a different order — ``InternalMove`` — and that is precisely the thing that must
not happen here: the rows are not the document.  A strip that rearranges itself answers
the reader's gesture twice, once by Qt and once by the page tree, and the two are not the
same operation — Qt moves *rows*, the edit moves *sheets*, and only the edit knows what a
sheet carries (its annotations, the outline row that names it, the reader's place in it).
So the strip is draggable and droppable, the drop indicator is Qt's own, and
``dropEvent`` is **not** passed to the base class: the widget asks for ``MovePages`` and
waits for the document to tell it what order its rows are in.

Three rules the widget owns, each of them tested:

* **A line through the block is not an action.**  PdfCraft makes the same call —
  ``if contiguous && to == first { return }`` before the edit is ever built
  (``crates/ui-egui/src/panels.rs:1667-1670``) — and for the same reason: an ``Edit`` that
  writes nothing still takes a slot on the undo stack, so one Ctrl+Z would appear to do
  nothing and the reader would have to press it twice to undo anything at all.  The
  predicate is the edit's (``changes_nothing``), the decision is the strip's.
* **Not every drop is the reader's drag.**  A file hauled in from a file manager arrives
  at the same widget.  What the strip will act on is a drop carrying the mime type its own
  model declares; anything else is left alone.
* **The payload never says which pages move.**  The block is the selection, the gap comes
  from the pointer and the pick — a strip that read the dragged rows out of the mime data
  would be taking the reader's document from a serialised byte stream.

Three things about PyQt6 had to be measured before any of them could be asserted, and the
harness below is shaped by all three.  ``QCoreApplication.sendEvent`` delivers a synthetic
``QDropEvent`` to nobody — not to a ``QListWidget``, not to its viewport, not even to a bare
``QWidget`` with ``setAcceptDrops(True)`` — while ``QWidget.event`` reaches a plain widget's
``dropEvent`` and not a scroll area's, so the drop is handed to ``dropEvent`` outright and
the settings test is what proves Qt would ever call it.  ``QAbstractItemView``'s readable
half of ``setDropIndicatorShown`` is ``showDropIndicator`` (the direct getter is protected
and unwrapped), and it reads back what was set rather than being forced by the mode.  And a
``QListWidget`` has blank space only *below* its last row: ``itemAt`` answers with a row for
any point in layout coordinates, however far outside the viewport, so a scrolled strip has no
margin to drop into at all and the fixture shows the whole book.
"""

from __future__ import annotations

import itertools
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QMimeData, QPointF, Qt, QUrl
from PyQt6.QtGui import QDropEvent
from PyQt6.QtWidgets import QAbstractItemView, QListWidget, QMessageBox

from pdfar.edits import EditError
from pdfar.pages import MovePages, ReversePages, gap_for_drop
from pdfar.render import RenderPool
from pdfar.sidebar import Sidebar

#: Six sheets, one row each.
PAGES = 6

#: A panel that shows all of them at once, plus the margin under the last row.
#: Six rows of thumbnails measure about 1360 px, and the fixture checks the fit rather
#: than trusting these numbers (see :func:`whole_book_shown`).
STRIP_WIDTH = 320
STRIP_HEIGHT = 1600

#: Big enough that the sidebar dock shows the whole book too.
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 1700


def subsets(count: int):
    """Every non-empty pick of a *count*-sheet book, in page order.

    A pick arrives at the edit already sorted and deduplicated — that is what the strip's
    :meth:`~pdfar.sidebar.ThumbnailList.picked_pages` guarantees — so the enumeration is
    over combinations, not over the reader's clicking order.
    """
    return [tuple(pick)
            for size in range(1, count + 1)
            for pick in itertools.combinations(range(count), size)]


@pytest.fixture
def book(tmp_path):
    return build_sample_pdf(str(tmp_path / "book.pdf"), pages=PAGES)


@pytest.fixture
def strip(qapp, book):
    """A sidebar over the six-sheet book, shown so the strip has real geometry.

    Tall enough on purpose that the whole book fits: a drop into the margin below the last
    row is one of the two ways to name the end of the document, and a scrolling strip has
    no such margin to drop into.
    """
    session = make_session(book)
    pool = RenderPool(session, workers=1)
    bar = Sidebar(session, pool)
    bar.resize(STRIP_WIDTH, STRIP_HEIGHT)
    bar.show()
    qapp.processEvents()
    try:
        yield bar
    finally:
        pool.shutdown()
        bar.close()
        bar.deleteLater()
        session.close()
        qapp.processEvents()


# ------------------------------------------------------------- the strip's geometry


def rows(bar) -> tuple:
    """Which page each row of the strip names, top to bottom."""
    return tuple(bar.thumbs.item(index).data(Qt.ItemDataRole.UserRole)
                 for index in range(bar.thumbs.count()))


def labels(bar) -> tuple:
    """The number printed under each thumbnail."""
    return tuple(bar.thumbs.item(index).data(Qt.ItemDataRole.DisplayRole)
                 for index in range(bar.thumbs.count()))


def rect_of(bar, row: int):
    rect = bar.thumbs.visualItemRect(bar.thumbs.item(row))
    assert rect.isValid(), "the strip was never laid out"
    return rect


def upper_half(bar, row: int) -> QPointF:
    """The line *above* row *row*: a pointer resting on its upper half."""
    rect = rect_of(bar, row)
    return QPointF(rect.center().x(), rect.top() + 4)


def lower_half(bar, row: int) -> QPointF:
    """The line *below* row *row*."""
    rect = rect_of(bar, row)
    return QPointF(rect.center().x(), rect.bottom() - 4)


def above_the_book(bar) -> QPointF:
    """A hand's breadth over the first row — the frame, or a pointer that left the strip.

    There is no blank space *above* a list: rows are laid out from ``y = 0`` and scrolling
    moves them, it does not open a margin.  This is the one way a drop reaches the widget
    with a negative ordinate, and the answer it gets has to be the front of the book rather
    than a nonsense gap.
    """
    rect = rect_of(bar, 0)
    return QPointF(rect.center().x(), rect.top() - 40)


def below_the_book(bar) -> QPointF:
    """Blank margin past the last row — which only exists with the whole book on screen."""
    rect = rect_of(bar, PAGES - 1)
    return QPointF(rect.center().x(), rect.bottom() + 40)


def whole_book_shown(bar) -> None:
    """Insist the strip is showing every sheet, so the margin below it is real.

    A scrolled strip has no space past its last row — ``itemAt`` answers with the row the
    point lands on, however far below the viewport it is, because visual rects are layout
    coordinates and not clipped ones.  Two of the tests below want the drop that names no
    row at all, so they ask for this first rather than measure a blank that is not blank.
    """
    viewport = bar.thumbs.viewport().rect()
    last = rect_of(bar, PAGES - 1)
    assert last.bottom() < viewport.bottom(), \
        "the strip is scrolling, so a drop past its rows lands on a row"
    assert bar.thumbs.itemAt(below_the_book(bar).toPoint()) is None


# ---------------------------------------------------------------------- the drag data


def own_drag(bar, dragged_rows) -> QMimeData:
    """The mime data the strip's own drag carries for these rows."""
    model = bar.thumbs.model()
    return model.mimeData([model.index(row, 0) for row in dragged_rows])


def file_drag(url="file:/home/reader/scan.pdf") -> QMimeData:
    """A drop from outside: what a file manager hands over."""
    mime = QMimeData()
    mime.setUrls([QUrl(url)])
    return mime


def list_drag(rows_=("x", "y", "z")) -> QMimeData:
    """Another widget's list, whose data the strip's model would also decode."""
    other = QListWidget()
    other.addItems(list(rows_))
    model = other.model()
    return model.mimeData([model.index(index, 0)
                           for index in range(other.count())])


def drop(bar, mime, where: QPointF) -> bool:
    """Let go on the strip at *where*; True when the strip took the event.

    ``bar.thumbs.dropEvent`` is called outright, because nothing else in PyQt6 delivers a
    synthetic drop.  Measured on this Qt: ``QCoreApplication.sendEvent`` returns False and
    calls nothing for a ``QDropEvent`` aimed at either the viewport or the view — and not
    only for item views, since a bare ``QWidget`` with ``setAcceptDrops(True)`` behaves the
    same way — while ``widget.event(drop_event)`` does reach a plain widget's ``dropEvent``
    but not a ``QAbstractScrollArea``'s.  ``QTest`` has no drop helper at all, and
    ``QDropEvent.setSource`` is not wrapped, so the gesture has to be handed to the one
    method Qt itself calls when it arrives.  What the settings test below checks is that
    the widget is configured for Qt to call it.
    """
    event = QDropEvent(where, Qt.DropAction.MoveAction, mime,
                       Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    bar.thumbs.dropEvent(event)
    return event.isAccepted()


class Asks:
    """Every reorder request the sidebar made, in the order it made them."""

    def __init__(self, bar) -> None:
        self.calls = []
        bar.page_move_requested.connect(self.record)

    def record(self, pages, gap) -> None:
        self.calls.append((tuple(pages), gap))

    @property
    def last(self):
        assert self.calls, "the strip asked for nothing"
        return self.calls[-1]


# --------------------------------------------------------------- what the strip is


def test_the_strip_can_be_dragged_from_and_dropped_onto(strip):
    assert strip.thumbs.dragEnabled(), "a pick cannot be lifted off the strip"
    assert strip.thumbs.acceptDrops(), "there is nowhere to let go"
    assert strip.thumbs.viewport().acceptDrops(), \
        "the pointer is over the viewport, and that is the widget Qt asks"
    assert strip.thumbs.showDropIndicator(), \
        "the reader aims at a boundary they cannot see"


def test_the_strip_never_rearranges_its_own_rows_by_layout(strip):
    """``InternalMove`` is the drag the reader needs; keeping it is not the drop's job.

    The mode is what gives the boundary line, the internal mime type and a drag that
    starts from the pick rather than from the row under the button.  What keeps the rows
    where they are is ``dropEvent`` — tested below — and ``Static`` movement, so no
    gesture rearranges the strip behind the document's back.
    """
    assert strip.thumbs.movement() == QListWidget.Movement.Static
    assert strip.thumbs.dragDropMode() == QAbstractItemView.DragDropMode.InternalMove


# ------------------------------------------------------------------- a drop asks


def test_a_drop_asks_for_the_block_at_the_gap_the_line_names(strip):
    asks = Asks(strip)
    strip.select_pages((1, 2))

    assert drop(strip, own_drag(strip, (1, 2)), lower_half(strip, 4))

    gap = gap_for_drop(PAGES, (1, 2), 4, True)
    assert gap == 3
    assert asks.last == ((1, 2), gap)


@pytest.mark.parametrize("row", range(PAGES))
def test_the_two_halves_of_a_row_are_the_two_lines_it_touches(strip, row):
    """Both boundaries of every row, on a single-sheet pick nothing else could explain.

    Half the lines a lone sheet is dragged across are lines it is already sitting on, and
    those are asked for nothing at all — so the assertion is the conditional: *if* the line
    names a reorder, it names this one.  Checking the other half would mean checking that
    the strip asked, which the tests below do.
    """
    asks = Asks(strip)
    pick = (0,)
    strip.select_pages(pick)

    for after in (False, True):
        asks.calls.clear()
        where = lower_half(strip, row) if after else upper_half(strip, row)
        gap = gap_for_drop(PAGES, pick, row, after)
        silent = MovePages(pick, gap).changes_nothing(PAGES)

        took = drop(strip, own_drag(strip, pick), where)

        if silent:
            assert asks.calls == [], \
                f"row {row} {'lower' if after else 'upper'} half is gap {gap}, which the pick fills"
            assert not took
        else:
            assert took, f"row {row} {'lower' if after else 'upper'} half names gap {gap}"
            assert asks.last == (pick, gap)


def test_blank_space_below_the_book_asks_for_the_end(strip):
    whole_book_shown(strip)
    asks = Asks(strip)
    strip.select_pages((0, 1))

    assert drop(strip, own_drag(strip, (0, 1)), below_the_book(strip))

    assert asks.last == ((0, 1), gap_for_drop(PAGES, (0, 1), None, True))
    assert asks.last[1] == PAGES - 2


def test_blank_space_above_the_book_asks_for_the_front(strip):
    asks = Asks(strip)
    strip.select_pages((4, 5))

    assert drop(strip, own_drag(strip, (4, 5)), above_the_book(strip))

    assert asks.last == ((4, 5), 0)


def test_the_strip_asks_for_the_pick_and_not_for_the_row_under_the_pointer(strip):
    """The pointer is over row 3; the reader's block is rows 0 and 5."""
    asks = Asks(strip)
    strip.select_pages((0, 5))

    drop(strip, own_drag(strip, (0, 5)), upper_half(strip, 3))

    assert asks.last == ((0, 5), gap_for_drop(PAGES, (0, 5), 3, False))


def test_a_drop_with_nothing_gathered_asks_for_nothing(strip):
    """No block, no request: the pointer alone names no sheets to move.

    The payload is a real one — rows this strip once held — so the answer comes from the
    pick and not from what the drag happens to carry.  A strip with nothing selected
    cannot start a drag at all, but a drop can still reach it from a drag begun before the
    selection was lost.

    This is the ``changes_nothing`` guard doing the work, not a check for an empty pick:
    a block of no sheets fills every gap, so the reorder is already the book the reader
    has.  A mutant that deletes a separate empty-pick check survives this test, which is
    how the redundancy was found and the second branch removed.
    """
    asks = Asks(strip)
    mime = own_drag(strip, (2, 3))
    strip.thumbs.clearSelection()

    took = drop(strip, mime, lower_half(strip, 2))

    assert strip.thumbs.picked_pages() == ()
    assert asks.calls == []
    assert not took


def test_a_drop_from_outside_the_strip_asks_for_nothing(strip):
    """A file dragged in from a file manager is not a reorder of the pick."""
    asks = Asks(strip)
    strip.select_pages((1, 3))

    took = drop(strip, file_drag(), lower_half(strip, 2))

    assert asks.calls == []
    assert not took, "the strip accepted a drop it has no business acting on"


def test_the_data_a_drop_carries_never_says_which_pages_move(strip):
    """Another list's payload, decoded as ours: the block is still this strip's pick.

    A drag from a foreign ``QListWidget`` carries the very same internal format, so no
    format test tells it apart — and none needs to.  The pages that move are the ones the
    reader gathered on *this* strip, and the gap is where *this* pointer let go; reading
    either out of the mime data would take the document from a byte stream.
    """
    asks = Asks(strip)
    strip.select_pages((1,))

    drop(strip, list_drag(), lower_half(strip, 3))

    assert asks.last[0] == (1,)
    assert asks.last[1] == gap_for_drop(PAGES, (1,), 3, True)


# --------------------------------------------------- the rows stay the document's


def test_the_strip_leaves_its_rows_exactly_where_they_were(strip):
    """The whole point: a drop is a *request*, and Qt is not allowed to answer it."""
    strip.select_pages((1, 2))
    before, named = rows(strip), labels(strip)

    drop(strip, own_drag(strip, (1, 2)), lower_half(strip, 4))

    assert rows(strip) == before == tuple(range(PAGES))
    assert labels(strip) == named
    assert strip.thumbs.count() == PAGES


# --------------------------------------------------------------- a silent drop


@pytest.mark.parametrize("pick, row, after", [
    ((1, 2, 3), 2, False),
    ((1, 2, 3), 2, True),
    ((0, 1), 1, False),
    ((4, 5), 5, True),
    ((2,), 2, False),
    ((2,), 2, True),
])
def test_a_line_through_the_block_the_reader_is_holding_asks_for_nothing(
        strip, pick, row, after):
    """The drop that changes nothing is not an action, so it is not recorded either."""
    asks = Asks(strip)
    strip.select_pages(pick)
    where = lower_half(strip, row) if after else upper_half(strip, row)

    took = drop(strip, own_drag(strip, pick), where)

    assert asks.calls == []
    assert not took, "the strip accepted a drop it is going to do nothing about"
    gap = gap_for_drop(PAGES, pick, row, after)
    assert MovePages(pick, gap).changes_nothing(PAGES)


def test_a_scattered_pick_dropped_at_its_own_front_is_a_move_and_is_asked_for(strip):
    """The other half of the rule: a pick with holes is never already in place.

    ``0 2 4`` gathered and let go on the line above row 0 names gap 0, which is where its
    first sheet already is — and the book still changes, because the sheets between them
    move up.  A silent-drop test written only on contiguous picks would not tell the two
    cases apart, which is the mistake PdfCraft's ``contiguous &&`` half of the guard is
    there to stop.
    """
    asks = Asks(strip)
    pick = (0, 2, 4)
    strip.select_pages(pick)

    drop(strip, own_drag(strip, pick), upper_half(strip, 0))

    assert asks.last == (pick, 0)
    assert not MovePages(pick, 0).changes_nothing(PAGES)


# ----------------------------------------------------------- the edit's own answer


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6, 8])
def test_changes_nothing_agrees_with_the_order_the_move_writes(count):
    """The predicate the strip guards on, read against the order the edit writes.

    Every non-empty pick into every gap, so the guard cannot be quietly re-derived from
    the shape of one pick — the contiguous case, the scattered case and the case of a pick
    that is the whole book all answer differently, and only the written order settles it.
    """
    for pick in subsets(count):
        for target in range(count + 1):
            edit = MovePages(pick, target)
            assert edit.changes_nothing(count) == (
                edit._intended(count) == list(range(count)))


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_only_a_reverse_of_one_sheet_changes_nothing(count):
    """The other guard the strip will need when *Reverse* reaches the context menu.

    ``(2, 2)`` is in the sweep although no strip can send it — selection is a set of rows
    — because it is the only pick where "one sheet named" and "one name in the pick" come
    apart, and the predicate is about the slots.
    """
    picks = subsets(count) + ([(2, 2)] if count > 2 else [])
    for pick in picks:
        edit = ReversePages(pick)
        assert edit.changes_nothing(count) == (len(set(pick)) == 1)
        assert edit.changes_nothing(count) == (
            edit._intended(count) == list(range(count)))


def test_an_empty_pick_writes_the_order_the_book_is_already_in(book):
    """The one pick the guards refuse before the predicate is ever asked.

    ``changes_nothing`` answers True because the book is untouched, while the "one sheet"
    rule above says no such pick exists: nothing was named to reverse.  ``apply`` refuses
    it outright, and the strip never sends it, so the two answers never meet.
    """
    assert ReversePages(()).changes_nothing(PAGES)
    with fitz.open(book) as doc:
        with pytest.raises(EditError):
            ReversePages(()).apply(doc)


# ------------------------------------------------------------- through the window


@pytest.fixture
def answers(monkeypatch):
    """Answer the boxes a dirty document raises, because a modal would block the run.

    A move makes the document unsaved, and the window's teardown closes the tab it
    opened — which asks whether to keep the work.  Offscreen, that question never
    returns, so the fixture answers "discard" to anything offering to save (a test that
    wrote the answer back would leave a modified fixture on disk) and "OK" to the rest.
    """
    def question(_parent, _title, _text, buttons, *rest):
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


@pytest.fixture
def window(qapp, book, answers):
    """The real main window on the six-sheet book."""
    from pdfar.main import MainWindow

    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    win.open_document(book)
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def sheet_numbers(doc) -> list:
    """The book as its sheets name themselves."""
    out = []
    for index in range(doc.page_count):
        line = " ".join(doc.load_page(index).get_text().split())
        if line.startswith("SHEET"):
            out.append(int(line.split()[1]))
        elif line.startswith("Hello"):
            out.append(0)
        else:
            out.append(int(line.removeprefix("Page ").split()[0]) - 1)
    return out


def test_a_drop_in_the_window_moves_the_pages(window, qapp):
    """The gesture end to end: rows gathered, let go on a line, book re-ordered."""
    sidebar = window.sidebar
    sidebar.select_pages((0, 1))
    depth = window.view.session.history.depth

    drop(sidebar, own_drag(sidebar, (0, 1)), lower_half(sidebar, 3))
    qapp.processEvents()

    assert sheet_numbers(window.view.session.doc) == [2, 3, 0, 1, 4, 5]
    assert window.view.session.history.depth == depth + 1
    assert rows(sidebar) == tuple(range(PAGES)), \
        "the rebuilt strip names the pages in their new order"
    assert sidebar.selected_pages() == (2, 3), "the pick follows the block it moved"


def test_a_silent_drop_in_the_window_leaves_nothing_to_undo(window, qapp):
    """One Ctrl+Z has to undo one thing the reader did."""
    sidebar = window.sidebar
    sidebar.select_pages((1, 2, 3))
    depth = window.view.session.history.depth
    before = sheet_numbers(window.view.session.doc)

    drop(sidebar, own_drag(sidebar, (1, 2, 3)), upper_half(sidebar, 2))
    qapp.processEvents()

    assert sheet_numbers(window.view.session.doc) == before
    assert window.view.session.history.depth == depth
    assert not window.view.session.doc.is_dirty


def test_the_reader_follows_the_sheet_they_were_on(window, qapp):
    """Moved the page under their own eyes: the reader stays on the sheet, not the slot."""
    sidebar = window.sidebar
    window.view.goto_page(0)
    qapp.processEvents()
    sidebar.select_pages((0, 1))

    drop(sidebar, own_drag(sidebar, (0, 1)), lower_half(sidebar, 3))
    qapp.processEvents()

    assert window.view.current_page == 2
    assert sheet_numbers(window.view.session.doc)[window.view.current_page] == 0


def test_undo_puts_the_book_back_and_the_strip_with_it(window, qapp):
    sidebar = window.sidebar
    sidebar.select_pages((4, 5))
    drop(sidebar, own_drag(sidebar, (4, 5)), upper_half(sidebar, 0))
    qapp.processEvents()
    assert sheet_numbers(window.view.session.doc) == [4, 5, 0, 1, 2, 3]

    assert window.view.undo_edit()
    qapp.processEvents()

    assert sheet_numbers(window.view.session.doc) == [0, 1, 2, 3, 4, 5]
    assert rows(sidebar) == tuple(range(PAGES))
