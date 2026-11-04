"""F10-4t: the strip's menu gives the reverse its caller.

``ReversePages`` (``tests/test_page_reverse.py``) is the rule and F10-4r shipped it with
nothing that asked for it: a reader who has a batch scanned back to front has one command
to want, and the strip is the one place in PDFAR that already knows which sheets they
gathered.  So this is the smallest of the surface steps — no new MuPDF call, no new
arithmetic, and the widget's only job is to decide **whether to offer the command at all**.

That decision is the whole design, and it has two halves.

* **A reverse of one sheet is nothing to do.**  The edit knows it (``changes_nothing``,
  and ``apply`` returns before the settle), and the menu says it *before* the reader asks:
  the item is there, greyed, with the reason in its tooltip.  PdfCraft answers the same
  gesture differently — ``page.reverse`` (``crates/ui-egui/src/commands.rs:654-658``)
  falls back to the whole document whenever the pick is smaller than two — which works in
  a command palette with no page count in the label and does not work here, where the item
  a reader clicks would read *Reverse Page* while turning over five hundred sheets.  Both
  of the reader's asks get their own item instead: the pick's reverse, and **Reverse All
  Pages**, exactly as the rotation menu already splits *Rotate 2 Pages* from *Rotate All
  Pages*.
* **The gate is the edit's predicate, and it is on both items.**  Every enable is asked of
  ``ReversePages.changes_nothing`` — the same question the strip's drop asks (F10-4s) for
  the same reason: the widget that decides whether to act and the edit that would carry the
  action out must not be able to disagree about what "nothing" means.  On a reverse that
  reduces, for any pick this strip can send, to ``len(pages) < 2`` — the two are the same
  function over the deduplicated domain, which the sweep below pins and the mutation pass
  confirmed by failing to tell them apart.  What is *not* forced by the shape of the rule is
  putting the gate on the book's item as well as the pick's: a one-sheet document has a
  *Reverse All Pages* that writes nothing, and the session would record it and hand the
  reader a Ctrl+Z that undoes a step that changed no page.
"""

from __future__ import annotations

import itertools
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QListWidget, QMenu, QMessageBox

from pdfar.pages import ReversePages
from pdfar.render import RenderPool
from pdfar.sidebar import Sidebar

#: Six sheets, one row each — enough for a scattered pick to have a middle that stays.
PAGES = 6

#: Tall enough to show the whole book, so a pick of every sheet is a real gesture.
STRIP_WIDTH = 320
STRIP_HEIGHT = 1600

WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 1700

PICK = "Reverse {} Pages"
ALL = "Reverse All Pages"


def subsets(count: int):
    """Every non-empty pick of a *count*-sheet book, in page order."""
    return [tuple(pick)
            for size in range(1, count + 1)
            for pick in itertools.combinations(range(count), size)]


# ---------------------------------------------------------------- the widget's harness


def _centre(lst: QListWidget, index: int) -> QPoint:
    """The point a reader's cursor sits on to touch thumbnail *index*."""
    rect = lst.visualItemRect(lst.item(index))
    assert rect.isValid(), "the strip was never laid out"
    return QPoint(rect.center().x(), rect.center().y())


def _pick(lst: QListWidget, *indices: int) -> None:
    """The pick a reader ends up with after clicking those pages."""
    lst.clearSelection()
    for index in indices:
        lst.item(index).setSelected(True)


def _ask_for_menu(bar: Sidebar, pos: QPoint, app) -> None:
    """Ask for the context menu the way the window system does.

    The offscreen platform turns a right click into a selection change but never into a
    ``QContextMenuEvent``, so the event that opens the menu goes to the strip's viewport —
    the same receiver a real right click reaches.
    """
    from PyQt6.QtWidgets import QApplication

    QApplication.sendEvent(bar.thumbs.viewport(),
                           QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos))
    app.processEvents()


def labels(menu: QMenu) -> list:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def act(menu: QMenu, label: str):
    for action in menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"{label!r} is not in {labels(menu)}")


def order(bar: Sidebar) -> tuple:
    """Which page each row of the strip names, top to bottom."""
    return tuple(bar.thumbs.item(index).data(Qt.ItemDataRole.UserRole)
                 for index in range(bar.thumbs.count()))


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


@pytest.fixture
def menus(monkeypatch):
    """Catch the menu a context handler would put on the screen: ``exec`` blocks."""
    shown: list = []

    def record(self, *args):
        shown.append(self)
        return None

    monkeypatch.setattr(QMenu, "exec", record)
    return shown


@pytest.fixture
def answers(monkeypatch):
    """Answer the boxes a dirty document raises, because a modal would block the run."""
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
def make_strip(qapp, tmp_path):
    """Build a sidebar over a book of *n* sheets; everything is torn down afterwards."""
    built: list = []

    def build(pages: int, name: str = "book") -> Sidebar:
        path = build_sample_pdf(str(tmp_path / f"{name}-{pages}.pdf"), pages=pages)
        session = make_session(path)
        pool = RenderPool(session, workers=1)
        bar = Sidebar(session, pool)
        bar.resize(STRIP_WIDTH, STRIP_HEIGHT)
        bar.show()
        qapp.processEvents()
        built.append((bar, pool, session))
        return bar

    yield build
    for bar, pool, session in reversed(built):
        pool.shutdown()
        bar.close()
        bar.deleteLater()
        session.close()
    qapp.processEvents()


@pytest.fixture
def strip(make_strip):
    return make_strip(PAGES)


# ------------------------------------------------------------------- the menu's shape


def test_the_menu_offers_the_pick_and_the_book_in_that_order(strip, menus, qapp):
    """What the reader sees, in the order they see it.

    The pick's commands first and the book's after a separator, which is the shape the
    rotation menu already had; the reverse of each sits under the turn of the same pages,
    because a reader looking for the one has learned where the other lives.  The blank F10-4ba
    added goes with the pick, whose last sheet is the gap it opens, and F10-4bb's copies went
    in beside it on the same rule — the gap is the one thing a pick that was never dragged can
    name.  Delete stays last, behind a separator of its own — the destructive one of the group.
    """
    _pick(strip.thumbs, 0, 2)
    _ask_for_menu(strip, _centre(strip.thumbs, 2), qapp)
    assert menus, "the right click opened no menu"
    assert labels(menus[-1]) == [
        "Rotate 2 Pages Left", "Rotate 2 Pages Right", PICK.format(2),
        "Insert Blank Page", "Duplicate 2 Pages",
        "Rotate All Pages Left", "Rotate All Pages Right", ALL,
        "Delete 2 Pages",
    ]
    assert [action.isSeparator() for action in menus[-1].actions()] == [
        False, False, False, False, False, True, False, False, False, True, False]


def test_the_reverse_is_named_with_the_pages_it_moves(strip):
    """One sheet is *Reverse Page*, three are *Reverse 3 Pages*.

    A reorder written into the file is not free — the whole book is re-read afterwards —
    and the label is the only place the reader is told how many sheets it costs.
    """
    assert labels(strip._page_commands_menu((1,))) == [
        "Rotate Page Left", "Rotate Page Right", "Reverse Page", "Insert Blank Page",
        "Duplicate Page",
        "Rotate All Pages Left", "Rotate All Pages Right", ALL]
    assert labels(strip._page_commands_menu((0, 2, 4)))[2] == PICK.format(3)


# ------------------------------------------------------- the gate: nothing to reverse


def test_a_reverse_of_one_sheet_is_offered_and_explains_why_it_is_not_asked_for(strip):
    """Greyed, not missing: the command exists, and this pick cannot use it.

    A reader who right-clicked the only thumbnail they meant to move learns the rule from
    the menu rather than from a step that undoes nothing.  The reason has to be *said*, so
    the assertion is the sentence and not a word from the label: ``QAction.toolTip()``
    echoes the action's own text when nothing was set, which is measured here because a
    search for "revers" in it passes with no tooltip at all.
    """
    menu = strip._page_commands_menu((3,))
    action = act(menu, "Reverse Page")
    assert not action.isEnabled()
    assert action.toolTip() != action.text()
    assert "two sheets" in action.toolTip()
    assert act(menu, ALL).isEnabled(), "the book has six sheets to turn around"


def test_a_one_sheet_book_offers_no_reverse_at_all(qapp, make_strip):
    """The second item needs the gate too, and this is the book that shows it.

    A one-sheet document has a **Reverse All Pages** that writes nothing, so a menu that
    greyed only the pick's item would offer it, the session would record it, and the
    reader's Ctrl+Z would undo a step that changed no page.  Both items are gated on the
    same question asked of the edit's own predicate, so they cannot answer apart — and
    *that*, rather than the predicate against a count of names, is what this case pins: the
    two formulations agree on every pick this strip can send, which the sweep below records
    on the way to failing each half of the gate separately.
    """
    bar = make_strip(1)
    menu = bar._page_commands_menu((0,))
    assert not act(menu, "Reverse Page").isEnabled()
    assert not act(menu, ALL).isEnabled()


def test_the_menu_enables_exactly_the_reverses_that_would_write_something(qapp,
                                                                        make_strip):
    """Every pick of every small book, both items, against the edit's answer.

    The enable is not a rule the menu invents: it is ``ReversePages.changes_nothing``
    turned off, so the item and the step cannot drift apart.  The picks are the ones the
    strip can send — sorted and deduplicated by ``picked_pages`` — and over that domain the
    predicate is exactly "fewer than two sheets", which is why a mutant swapping one for the
    other is equivalent rather than wrong.  What the sweep does pin is that both items are
    gated: the book's as many times as the pick's.
    """
    for count in (1, 2, 3, 4):
        bar = make_strip(count, name="sweep")
        whole = tuple(range(count))
        for pick in subsets(count):
            menu = bar._page_commands_menu(pick)
            named = "Reverse Page" if len(pick) == 1 else PICK.format(len(pick))
            assert act(menu, named).isEnabled() is \
                not ReversePages(pick).changes_nothing(count), (count, pick)
            assert act(menu, ALL).isEnabled() is \
                not ReversePages(whole).changes_nothing(count), count


def test_a_greyed_reverse_asks_for_nothing(strip, menus, qapp):
    """The reader can still click at the item, and nothing is sent.

    Qt will not activate a disabled action, and the tooltip is not a promise the widget
    has to enforce — but the point of gating at all is that no edit reaches the session,
    so that is what is asserted here rather than the flag.
    """
    seen: list = []
    strip.page_reverse_requested.connect(seen.append)
    _pick(strip.thumbs, 4)
    _ask_for_menu(strip, _centre(strip.thumbs, 4), qapp)
    action = act(menus[-1], "Reverse Page")
    assert not action.isEnabled()
    action.trigger()
    qapp.processEvents()
    assert seen == []


def test_the_menu_asks_and_leaves_the_reorder_to_the_window(strip):
    """The strip has a session it could edit, and must not use it.

    Undo, the re-render, the carried pick and the "is this document saved" answer all
    live behind the view; a sidebar that reordered the tree itself would leave the screen
    showing the book as it was, with no step to undo.
    """
    seen: list = []
    strip.page_reverse_requested.connect(seen.append)

    menu = strip._page_commands_menu((0, 2, 4))
    act(menu, PICK.format(3)).trigger()
    act(menu, ALL).trigger()
    assert seen == [(0, 2, 4), (0, 1, 2, 3, 4, 5)]
    assert order(strip) == tuple(range(PAGES)), "nothing moved until the window said so"


# --------------------------------------------------------------- through the window


@pytest.fixture
def window(qapp, tmp_path, answers):
    """The real main window over the six-sheet book."""
    from pdfar.main import MainWindow

    path = build_sample_pdf(str(tmp_path / "window.pdf"), pages=PAGES)
    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    win.open_document(path)
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_the_menu_reverses_the_picked_sheets_in_the_slots_they_fill(window, qapp, menus):
    """A pick, a right click and one menu item: the tree holds the other order.

    The scattered pick is the case the rule was written for: sheets 1 and 4 change slots
    and sheet 3, the middle of the pick, does not move at all — while every unpicked sheet
    keeps the number it had.
    """
    sidebar = window.sidebar
    session = window.view.session
    _pick(sidebar.thumbs, 1, 3, 4)
    depth = session.history.depth
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 3), qapp)
    act(menus[-1], PICK.format(3)).trigger()
    qapp.processEvents()

    assert sheet_numbers(session.doc) == [0, 4, 2, 3, 1, 5]
    assert order(sidebar) == tuple(range(PAGES)), \
        "the rebuilt strip names the pages in their new order"
    assert depth + 1 == session.history.depth, "one request is one edit"
    assert session.dirty

    window._undo_edit()
    qapp.processEvents()
    # Read back through the session: an accepted edit replaces the document object.
    assert sheet_numbers(session.doc) == [0, 1, 2, 3, 4, 5]


def test_the_pick_survives_the_reverse_it_was_made_from(window, qapp, menus):
    """The gather the reader clicked stays the gather they have — same sheets, same rows.

    A move carries the pick somewhere else, because the block leaves its slots.  A reverse
    permutes the picked sheets *among the slots they already fill*, so the pick as a set of
    rows is unchanged and the reader looks at the same three highlighted thumbnails, now
    holding the three sheets in the other order.  It is the property that makes
    ``ReversePages.changed`` the pick rather than the whole book, said back from the widget.
    """
    sidebar = window.sidebar
    _pick(sidebar.thumbs, 1, 3, 4)
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 3), qapp)
    act(menus[-1], PICK.format(3)).trigger()
    qapp.processEvents()

    assert sidebar.selected_pages() == (1, 3, 4)
    doc = window.view.session.doc
    assert [sheet_numbers(doc)[page] for page in sidebar.selected_pages()] == [4, 3, 1]


def test_the_reader_follows_the_sheet_they_were_on(window, qapp, menus):
    """Reversed the page under their own eyes: they stay with the sheet, not the slot."""
    sidebar = window.sidebar
    window.view.goto_page(1)
    qapp.processEvents()
    _pick(sidebar.thumbs, 1, 3, 4)
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 1), qapp)
    act(menus[-1], PICK.format(3)).trigger()
    qapp.processEvents()

    assert window.view.current_page == 4
    assert sheet_numbers(window.view.session.doc)[window.view.current_page] == 1


def test_reverse_all_pages_turns_a_book_that_arrived_backwards(window, qapp, menus):
    """The ask the whole step exists for: five hundred sheets scanned end to front.

    One item, every sheet, one undo — and the numbers printed under the thumbnails do not
    move, because a reorder changes which sheet wears which number and not the numbering.
    """
    sidebar = window.sidebar
    session = window.view.session
    _pick(sidebar.thumbs, 0)
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 0), qapp)
    act(menus[-1], ALL).trigger()
    qapp.processEvents()

    assert sheet_numbers(session.doc) == [5, 4, 3, 2, 1, 0]
    assert [sidebar.thumbs.item(index).data(Qt.ItemDataRole.DisplayRole)
            for index in range(PAGES)] == ["1", "2", "3", "4", "5", "6"]

    window._undo_edit()
    qapp.processEvents()
    assert sheet_numbers(session.doc) == [0, 1, 2, 3, 4, 5]


def test_a_reverse_asked_twice_is_the_book_that_was(window, qapp, menus):
    """The involution, through the menu: two real steps, and the reader is where they were.

    Two entries on the undo stack, not one that cancels — the second is a reorder of its
    own, and Ctrl+Z twice has to put the book back rather than undo both at once.
    """
    sidebar = window.sidebar
    session = window.view.session
    _pick(sidebar.thumbs, *range(PAGES))
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 0), qapp)
    act(menus[-1], ALL).trigger()
    qapp.processEvents()
    assert sheet_numbers(session.doc) == [5, 4, 3, 2, 1, 0]
    _ask_for_menu(sidebar, _centre(sidebar.thumbs, 0), qapp)
    act(menus[-1], ALL).trigger()
    qapp.processEvents()

    assert sheet_numbers(session.doc) == [0, 1, 2, 3, 4, 5]
    window._undo_edit()
    qapp.processEvents()
    assert sheet_numbers(session.doc) == [5, 4, 3, 2, 1, 0]


def test_a_one_sheet_book_gives_the_reader_no_reorder_to_undo(qapp, tmp_path, answers,
                                                             menus):
    """The greyed item, end to end: nothing recorded, nothing dirtied, nothing to undo."""
    from pdfar.main import MainWindow

    path = build_sample_pdf(str(tmp_path / "single.pdf"), pages=1)
    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    win.open_document(path)
    qapp.processEvents()
    try:
        sidebar = win.sidebar
        depth = win.view.session.history.depth
        _ask_for_menu(sidebar, _centre(sidebar.thumbs, 0), qapp)
        menu = menus[-1]
        assert not act(menu, "Reverse Page").isEnabled()
        assert not act(menu, ALL).isEnabled()
        act(menu, "Reverse Page").trigger()
        act(menu, ALL).trigger()
        qapp.processEvents()

        assert win.view.session.history.depth == depth
        assert not win.view.session.dirty
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()

