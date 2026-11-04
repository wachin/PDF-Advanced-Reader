"""F10-4z: a sealed book does not offer the commands its own rights forbid.

F10-4x (`tests/test_page_permission.py`) put the assemble permission in front of the
page tree and answers a reader who asks anyway.  PdfCraft does not answer that reader —
it never offers them the command: the organize panel builds its buttons through
`command_enabled` (`crates/ui-egui/src/organize_panel.rs:46`), the command palette
refuses to select a greyed entry (`crates/ui-egui/src/panels.rs:344`), and the canvas
greys the same thing again where it draws it (`canvas.rs:2901` and `:2933`).  The
predicate behind all three is `Document::allows_assembly`
(`crates/engine/src/document.rs:353-354`) — the very one F10-4x gates on — so the offer
and the refusal are the same question asked twice, once before the click and once after.

That is the whole of this step: no new MuPDF call, no new arithmetic, one boolean that
already exists read by two widgets that did not ask it.  What is worth pinning is where
the answer has to arrive.

* **every page command, not one of them** — the strip's rotate, reverse and delete, the
  pick's and the book's, and the window's toolbar items and their shortcuts, which are
  the same commands reached by a reader who never opens a menu;
* **greyed, not missing** — the command exists and this document cannot use it, which is
  a fact the reader can only learn from an item that is there;
* **with the reason said** — `QAction.toolTip()` echoes the action's own text when
  nothing was set, so a search for "permission" in it would pass with no tooltip at all,
  and the sentence is the one the refusal uses rather than a second wording of it;
* **and only for as long as the document is open** — the window's actions outlive the
  tab, so a plain book opened next to a sealed one has to get its item, and its tooltip,
  back.

What is *not* greyed is the strip's drop.  A drop is not an item, and the move it names
goes to the same `MovePages` F10-4x already refuses, so the last case here pins the
other half: the sheets do not move, and the reason lands in the status bar.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QApplication, QListWidget, QMenu, QMessageBox

from pdfar.edits import ASSEMBLY_NOTE
from pdfar.main import MainWindow
from pdfar.render import RenderPool
from pdfar.sidebar import Sidebar

PAGES = 6
READER = "reader"
OWNER = "someone-elses-password"
#: Print, copy — and nothing about the sheets.
SEALED = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
GRANTED = SEALED | fitz.PDF_PERM_ASSEMBLE

STRIP_WIDTH = 320
STRIP_HEIGHT = 1600
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 1700

#: Everything ``_page_commands_menu`` puts up for a two-sheet pick of a six-sheet book.
PICK_ITEMS = ["Rotate 2 Pages Left", "Rotate 2 Pages Right", "Reverse 2 Pages",
              "Insert Blank Page",
              "Rotate All Pages Left", "Rotate All Pages Right", "Reverse All Pages"]

PAGE_ACTIONS = ("act_page_rotl", "act_page_rotr", "act_page_delete",
                "act_page_insert")


def protected_pdf(path, permissions, *, user=READER, owner=OWNER, pages=PAGES):
    """The book, encrypted so that typing *user* grants exactly *permissions*."""
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    staged = path + ".enc"
    doc.save(staged, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner,
             user_pw=user, permissions=permissions)
    doc.close()
    os.replace(staged, path)
    return path


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


def _pick(lst: QListWidget, *indices: int) -> None:
    lst.clearSelection()
    for index in indices:
        lst.item(index).setSelected(True)


def _centre(lst: QListWidget, index: int) -> QPoint:
    rect = lst.visualItemRect(lst.item(index))
    assert rect.isValid(), "the strip was never laid out"
    return QPoint(rect.center().x(), rect.center().y())


def _ask_for_menu(bar: Sidebar, pos: QPoint, app) -> None:
    """Ask for the context menu the way the window system does.

    The offscreen platform turns a right click into a selection change but never into a
    ``QContextMenuEvent``, so the event goes to the strip's viewport.
    """
    QApplication.sendEvent(bar.thumbs.viewport(),
                           QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos))
    app.processEvents()


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
    """A sidebar over a book opened past *password*, torn down afterwards."""
    built: list = []

    def build(path: str, password: str = "") -> Sidebar:
        session = make_session(path, password)
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
def sealed_strip(make_strip, tmp_path):
    return make_strip(protected_pdf(str(tmp_path / "sealed.pdf"), SEALED), READER)


@pytest.fixture
def plain_strip(make_strip, tmp_path):
    return make_strip(build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES))


# ------------------------------------------------------------------ the strip's menu


def test_a_sealed_book_offers_every_page_command_and_asks_none_of_it(sealed_strip):
    """There, greyed, and saying why — the pick's sheets and the book's alike.

    The item is not hidden: a reader who cannot find *Delete 2 Pages* has no way to learn
    that the file is the reason, and a producer who seals a report expects the reader to
    see the commands they may not use.
    """
    menu = sealed_strip._page_commands_menu((1, 3))
    assert labels(menu) == PICK_ITEMS
    for name in PICK_ITEMS:
        action = act(menu, name)
        assert not action.isEnabled(), name
        assert action.toolTip() == ASSEMBLY_NOTE, name


def test_the_delete_is_greyed_with_the_rest(sealed_strip, menus, qapp):
    """The destructive item is added by the handler, after the menu is built.

    It is the command a reader is most likely to reach for on a page they no longer want,
    and it sits outside ``_page_commands_menu``, so a gate written inside that method
    alone leaves the sealed book offering exactly the one item no reader can undo.
    """
    _pick(sealed_strip.thumbs, 2)
    _ask_for_menu(sealed_strip, _centre(sealed_strip.thumbs, 2), qapp)
    menu = menus[-1]
    delete = act(menu, "Delete Page")
    assert not delete.isEnabled()
    assert delete.toolTip() == ASSEMBLY_NOTE
    assert not any(action.isEnabled() for action in menu.actions()
                   if not action.isSeparator()), \
        "every page command in a sealed book's menu is greyed"


def test_a_plain_book_is_greyed_for_nothing_but_its_own_rules(plain_strip):
    """The gate adds a reason and takes no command away.

    A book with no encryption at all is the case ``DocumentSession.allows_assembly``
    answers without consulting a mask, and the reverse of one sheet is still greyed for
    the reason F10-4t gave it — which is what makes this a regression pin rather than a
    tautology: the permission gate must not overwrite the one item this menu already
    disabled, and must not disable the rest.
    """
    menu = plain_strip._page_commands_menu((3,))
    single = act(menu, "Reverse Page")
    assert not single.isEnabled()
    assert single.toolTip() != ASSEMBLY_NOTE, "the reason is still 'nothing to reverse'"
    for name in ("Rotate Page Left", "Rotate Page Right", "Reverse All Pages",
                 "Rotate All Pages Left"):
        assert act(menu, name).isEnabled(), name


def test_a_book_that_grants_assembly_is_not_greyed(make_strip, tmp_path):
    """Bit 11 set, commands live: the gate reads the mask, not the fact of encryption.

    The two fixtures differ by one bit and by nothing else, so this is the same widget on
    the same protected file answering the other way.
    """
    path = protected_pdf(str(tmp_path / "allowed.pdf"), GRANTED)
    bar = make_strip(path, READER)
    menu = bar._page_commands_menu((1, 3))
    for name in PICK_ITEMS:
        assert act(menu, name).isEnabled(), name


def test_an_owner_only_book_is_greyed_though_nobody_was_asked(make_strip, tmp_path):
    """The trap file from F10-4x, on the surface: nothing asks, everything is greyed.

    An owner-password-only document reports ``needs_pass == 0``, so no dialog ever
    appears and no password is ever typed — and it still states the restricted ``/P``.  A
    widget that greyed on "the reader typed something" would offer every command on the
    most commonly sealed kind of file there is.
    """
    path = protected_pdf(str(tmp_path / "owner-only.pdf"), SEALED, user="")
    bar = make_strip(path)
    assert bar.session.encryption is not None
    menu = bar._page_commands_menu((0, 1))
    for name in PICK_ITEMS:
        assert not act(menu, name).isEnabled(), name


def test_the_permission_is_the_outer_reason(sealed_strip):
    """A sealed book's one-sheet reverse says *permission*, not *nothing to reverse*.

    Two gates can fall on the same item — F10-4t's shape rule and this file's rights rule —
    and the reader needs the one that does not go away when they pick a second sheet.  A
    gate that left an already-greyed item alone would keep the inner sentence here, so the
    assertion is on the wording and not on the flag.
    """
    single = act(sealed_strip._page_commands_menu((3,)), "Reverse Page")
    assert not single.isEnabled()
    assert single.toolTip() == ASSEMBLY_NOTE


def test_a_greyed_command_sends_nothing_to_the_session(sealed_strip):
    """The point of greying is that no edit reaches the document.

    Qt refuses to activate a disabled action, and what is asserted is the signal the strip
    would have emitted rather than the flag that stopped it.
    """
    seen: list = []
    sealed_strip.page_rotation_requested.connect(lambda *a: seen.append(a))
    sealed_strip.page_reverse_requested.connect(seen.append)
    sealed_strip.page_deletion_requested.connect(seen.append)
    menu = sealed_strip._page_commands_menu((1, 3))
    for name in PICK_ITEMS:
        act(menu, name).trigger()
    assert seen == []


def test_the_sentence_is_the_one_the_refusal_uses(sealed_strip, qapp, tmp_path,
                                                 answers):
    """One wording, two surfaces.

    The tooltip and the ``EditError`` a refused edit raises are the same string, so a
    reader who is greyed out and a reader who is refused read the same explanation, and a
    rewording cannot leave one of the two behind.
    """
    assert act(sealed_strip._page_commands_menu((1, 3)),
               "Reverse 2 Pages").toolTip() == ASSEMBLY_NOTE

    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    try:
        path = protected_pdf(str(tmp_path / "refused.pdf"), SEALED)
        assert win.open_document(path, password=READER)
        qapp.processEvents()
        from pdfar.pages import RotatePages
        said = win.view.apply_edit(RotatePages(pages=(0,), delta=90))
        assert said is False
        assert ASSEMBLY_NOTE in win.statusBar().currentMessage()
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------------------- the window


@pytest.fixture
def window(qapp, tmp_path, answers):
    """The real main window, with documents opened through the password path."""
    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_the_window_greys_its_page_actions_on_a_sealed_document(window, qapp, tmp_path):
    """Toolbar and menu from the same actions, for the reader who never opens a menu.

    These three are also ``Ctrl+Shift+Left``, ``Ctrl+Shift+Right`` and
    ``Ctrl+Shift+Backspace``, and a disabled ``QAction`` does not fire on its shortcut, so
    the keyboard route closes with the item — which is why the trigger below is asked of
    the action rather than of a key press.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    assert window.open_document(path, password=READER)
    qapp.processEvents()
    for name in PAGE_ACTIONS:
        action = getattr(window, name)
        assert not action.isEnabled(), name
        assert action.toolTip() == ASSEMBLY_NOTE, name

    depth = window.view.session.history.depth
    for name in PAGE_ACTIONS:
        getattr(window, name).trigger()
    qapp.processEvents()
    assert window.view.session.history.depth == depth
    assert not window.view.session.dirty


def test_a_window_with_no_document_claims_no_restriction(window, qapp):
    """Greyed because there is nothing to act on, and the tooltip says so.

    The gate has two reasons and they are not interchangeable: with no document open the
    commands are disabled by the absence, and a window that wrote the permission sentence
    into that state would be telling a reader about a file it has not opened.  This is also
    the state the next case reads its baseline from, so it is pinned rather than assumed.
    """
    for name in PAGE_ACTIONS:
        action = getattr(window, name)
        assert not action.isEnabled(), name
        assert action.toolTip() != ASSEMBLY_NOTE, name
        assert action.toolTip(), name


def test_a_plain_book_in_the_next_tab_gets_the_commands_and_the_tooltips_back(
        window, qapp, tmp_path):
    """The actions outlive the document, so the gate has to be undone as well as done.

    ``QAction.toolTip()`` echoes the label when nothing was set and keeps whatever was set
    last otherwise: a window that wrote the permission sentence into the tooltip and never
    restored it would leave the next tab's *Delete Page* explaining a restriction that
    document does not have.  The baseline is read with the plain book **open**, not at
    startup, so it is the sentence the action was built with rather than the one some
    earlier refresh left behind.
    """
    plain = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    sealed = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    assert window.open_document(plain)
    qapp.processEvents()
    before = {name: getattr(window, name).toolTip() for name in PAGE_ACTIONS}
    assert all(tooltip != ASSEMBLY_NOTE for tooltip in before.values())

    assert window.open_document(sealed, password=READER)
    qapp.processEvents()
    assert not window.act_page_delete.isEnabled()

    window.tabs.setCurrentIndex(0)
    qapp.processEvents()
    for name in PAGE_ACTIONS:
        action = getattr(window, name)
        assert action.isEnabled(), name
        assert action.toolTip() == before[name], name


def test_the_reader_can_still_write_on_a_sealed_book(window, qapp, tmp_path):
    """Greyed page commands, live title: the gate is the tree, not a read-only mode.

    F10-4x pinned that a ``SetMetadata`` still applies to a sealed document; this pins the
    same fact where a reader would notice it — Save and the annotation tools stay enabled
    and only the sheet order is refused.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    assert window.open_document(path, password=READER)
    qapp.processEvents()
    assert window.act_save.isEnabled()
    assert window.act_export_xfdf.isEnabled()
    assert not window.act_page_delete.isEnabled()


def test_a_refused_move_leaves_the_strip_where_it_was(window, qapp, tmp_path):
    """The one page command with nothing to grey, and what answers for it.

    A drop is not a ``QAction``: the strip measures a line between rows and emits the move
    it names, so the refusal has to come from the edit.  What this pins is that the reader
    sees the sheets stay where they were and the reason appear under their feet.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    assert window.open_document(path, password=READER)
    qapp.processEvents()
    sidebar = window.sidebar
    before = order(sidebar)
    depth = window.view.session.history.depth

    sidebar.page_move_requested.emit((0, 1), 4)
    qapp.processEvents()

    assert order(sidebar) == before, "a refused move does not repaint the strip"
    assert window.view.session.history.depth == depth
    assert not window.view.session.dirty
    assert ASSEMBLY_NOTE in window.statusBar().currentMessage()
