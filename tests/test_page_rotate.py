"""F10-4b: turning a page **for keeps**.

F10-4a made the view able to show a page that arrives turned.  This is the other
direction — a command that writes the turn into the file — and everything that has
to stay true while it does: the pages beside the one asked for keep their turn, the
marks on it keep the boxes the file states, and the document that comes back from
disk is the one the user was looking at.

Two measured facts carry the design:

* ``page.rotation`` is the turn a page is *displayed* at, including a ``/Rotate``
  inherited from an ancestor ``/Pages`` node, while ``set_rotation`` writes the
  page's own node.  Adding the delta to the first and storing it on the second is
  what turns one page of a shared tree without moving its neighbours — and it is
  why an inherited turn is carried into the new value rather than counted twice.
* PyMuPDF folds an angle that is not a multiple of 90 to *no turn at all*
  (45→0, 100→0), so the edit refuses one instead of reporting a change the file
  does not hold.

The page objects are bound to a name before their annotations are read: PyMuPDF
frees the underlying page when the wrapper goes away, and an annotation that
outlives it takes the process down with it.
"""

from __future__ import annotations

import os
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QContextMenuEvent
from PyQt6.QtWidgets import QListWidget

from pdfar.annotations import AnnotationManager
from pdfar.edits import EditError, Scope
from pdfar.geometry import map_rect
from pdfar.pages import RotatePages
from pdfar.save import save_document
from pdfar.viewer import PDFView

#: The sheet every fixture is built on: landscape, so a turn changes its shape.
SHEET = (200.0, 100.0)

#: A box on that sheet, clear of the sample page's square and its text.
MARK = (30.0, 12.0, 130.0, 42.0)


def wait_until(app, predicate, timeout=20.0):
    """Pump the event loop until *predicate* holds; reflow runs on a worker."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """Closing a turned page asks whether to keep the turn; a test says no.

    ``QMessageBox`` would block the offscreen run forever, and the answer does not
    matter here — the document under test is a fixture, not the user's work.
    """
    from PyQt6.QtWidgets import QMessageBox

    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard))


# ------------------------------------------------------------------- the file, read back
def rotations(doc):
    """The turn each page is displayed at, inheritance included."""
    return [doc[index].rotation for index in range(doc.page_count)]


def stored_rotate(doc, index):
    """The ``/Rotate`` the page's *own* node carries; ``'null'`` if it inherits."""
    page = doc[index]
    return doc.xref_get_key(page.xref, "Rotate")[1]


def pages_root(doc):
    return int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])


def root_rotate(doc):
    return doc.xref_get_key(pages_root(doc), "Rotate")[1]


def inherited_pdf(path, turn=90, pages=2):
    """A file whose only ``/Rotate`` sits on the ``/Pages`` node.

    That is how a producer that turns a whole document writes it, and PyMuPDF's
    own ``new_page`` stamps ``/Rotate 0`` on every page it makes, which would
    override it — so the key is stripped from the page objects afterwards.
    """
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=SHEET[0], height=SHEET[1])
        page.insert_text(fitz.Point(10, 62), "Hello PDFAR world", fontsize=12)
    root = pages_root(doc)
    kids = [int(found) for found in
            re.findall(r"(\d+)\s+\d+\s+R", doc.xref_get_key(root, "Kids")[1])]
    doc.xref_set_key(root, "Rotate", str(turn))
    for kid in kids:
        doc.update_object(kid,
                          re.sub(r"/Rotate\s+\S+\s*", "", doc.xref_object(kid)))
    doc.save(str(path))
    doc.close()
    return str(path)


def test_one_page_turns_and_its_neighbours_keep_theirs(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        _version, scope = session.apply(RotatePages(pages=(1,)))
        assert scope is Scope.STRUCTURE
        assert rotations(session.doc) == [0, 90, 0]
        saved = save_document(session).path
    finally:
        session.close()
    with fitz.open(saved) as back:
        assert rotations(back) == [0, 90, 0]


def test_the_turn_is_written_on_the_page_and_not_on_the_tree(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = make_session(path)
    try:
        session.apply(RotatePages(pages=(0, 1), delta=270))
        assert stored_rotate(session.doc, 0) == "270"
        assert stored_rotate(session.doc, 1) == "270"
        # An ancestor /Rotate would apply to every page below it, including any
        # that a later operation adds to the document.
        assert root_rotate(session.doc) == "null"
    finally:
        session.close()


def test_a_turned_page_is_tall_the_way_a_viewer_measures_it(tmp_path):
    """``page.rect`` follows the turn, which is what the layout is built from."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        assert tuple(session.doc[0].rect) == (0.0, 0.0, 200.0, 100.0)
        session.apply(RotatePages(pages=(0,)))
        assert tuple(session.doc[0].rect) == (0.0, 0.0, 100.0, 200.0)
    finally:
        session.close()


def test_the_page_s_words_are_untouched_by_the_turn(tmp_path):
    """Rotation hangs the sheet differently; it does not rewrite what is on it."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        before = session.doc[0].get_text().split()
        session.apply(RotatePages(pages=(0,), delta=180))
        assert session.doc[0].get_text().split() == before
    finally:
        session.close()


# ------------------------------------------------------------------------- inheritance
def test_an_inherited_turn_is_carried_into_the_page_not_double_counted(tmp_path):
    """A page under ``/Pages /Rotate 90`` turned again is 180, not 90 or 270."""
    path = inherited_pdf(tmp_path / "inherited.pdf", turn=90)
    session = make_session(path)
    try:
        assert rotations(session.doc) == [90, 90]
        assert stored_rotate(session.doc, 0) == "null"
        session.apply(RotatePages(pages=(0,)))
        assert stored_rotate(session.doc, 0) == "180"
        assert root_rotate(session.doc) == "90"
    finally:
        session.close()


def test_turning_one_page_of_a_shared_tree_leaves_the_other_inherited(tmp_path):
    path = inherited_pdf(tmp_path / "inherited.pdf", turn=90)
    session = make_session(path)
    try:
        session.apply(RotatePages(pages=(0,), delta=-90))
        assert rotations(session.doc) == [0, 90]
        assert stored_rotate(session.doc, 1) == "null"
        saved = save_document(session).path
    finally:
        session.close()
    with fitz.open(saved) as back:
        assert rotations(back) == [0, 90]
        assert tuple(back[1].rect) == (0.0, 0.0, 100.0, 200.0)


# -------------------------------------------------------------------------------- angles
@pytest.mark.parametrize("delta, want", [
    (90, 90), (180, 180), (270, 270), (-90, 270), (360, 0), (450, 90),
])
def test_a_quarter_turn_adds_to_whatever_the_page_had(tmp_path, delta, want):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        session.apply(RotatePages(pages=(0,), delta=delta))
        assert rotations(session.doc) == [want]
    finally:
        session.close()


def test_four_turns_come_back_to_where_they_started(tmp_path):
    """Each step is its own undoable edit, and the file ends where it began."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        seen = []
        for _ in range(4):
            session.apply(RotatePages(pages=(0,)))
            seen.append(rotations(session.doc)[0])
        assert seen == [90, 180, 270, 0]
        assert len(session.history) == 4
    finally:
        session.close()


def test_an_angle_that_is_not_a_quarter_turn_is_refused(tmp_path):
    """MuPDF would write 0 and the user would see a command that did nothing."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        version = session.version
        with pytest.raises(EditError, match="multiple of 90"):
            session.apply(RotatePages(pages=(0,), delta=45))
        assert rotations(session.doc) == [0]
        assert session.version == version
    finally:
        session.close()


def test_a_page_named_twice_is_turned_once(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = make_session(path)
    try:
        session.apply(RotatePages(pages=(0, 0, 0), delta=90))
        assert rotations(session.doc) == [90, 0]
    finally:
        session.close()


# ---------------------------------------------------------------------------- refusals
def test_an_empty_list_is_refused(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    try:
        version = session.version
        with pytest.raises(EditError, match="No page was chosen"):
            session.apply(RotatePages(pages=()))
        assert session.version == version
        assert len(session.history) == 0
    finally:
        session.close()


@pytest.mark.parametrize("index", [3, 99, -1])
def test_a_page_outside_the_document_is_refused(tmp_path, index):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        with pytest.raises(EditError, match="not in this document"):
            session.apply(RotatePages(pages=(index,)))
        assert rotations(session.doc) == [0, 0, 0]
    finally:
        session.close()


def test_a_list_with_one_bad_page_turns_nothing(tmp_path):
    """The valid half of a bad request is not applied on the way out."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        version = session.version
        with pytest.raises(EditError):
            session.apply(RotatePages(pages=(0, 1, 7)))
        assert rotations(session.doc) == [0, 0, 0]
        assert session.version == version
    finally:
        session.close()


# -------------------------------------------------------------------- undo, redo, scope
def test_undo_returns_the_turn_and_redo_puts_it_back(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = make_session(path)
    try:
        session.apply(RotatePages(pages=(0, 1), delta=90))
        assert rotations(session.doc) == [90, 90]
        _version, scope = session.undo()
        assert scope is Scope.STRUCTURE
        assert rotations(session.doc) == [0, 0]
        session.redo()
        assert rotations(session.doc) == [90, 90]
    finally:
        session.close()


def test_the_edit_names_the_pages_it_touched(tmp_path):
    """A reader that caches per page re-reads exactly these and no others."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    session = make_session(path)
    try:
        edit = RotatePages(pages=(1, 3))
        assert edit.pages == (1, 3)
        assert edit.scope is Scope.STRUCTURE
        assert edit.label == "Rotate 2 Pages"
        session.apply(edit)
        assert session.changed_pages == (1, 3)
        assert RotatePages(pages=(2,)).label == "Rotate Page"
    finally:
        session.close()


# ------------------------------------------------------------- the marks and the window
def test_a_mark_keeps_the_box_the_file_states_after_the_page_turns(tmp_path):
    """Turning the sheet does not move what was drawn on it.

    The turn is applied by whoever paints the page, so an annotation's ``/Rect``
    survives a rotation unchanged — which is what lets a mark made before the turn
    still be the same mark after it.
    """
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    manager = AnnotationManager(session)
    try:
        mark = manager.add_shape(0, "rect", rect=MARK)
        assert mark is not None
        session.apply(RotatePages(pages=(0,)))
        assert manager.get_annotation(mark.id).rects == [MARK]
        saved = save_document(session).path
    finally:
        session.close()
    with fitz.open(saved) as back:
        page = back[0]
        assert page.rotation == 90
        held = tuple(next(page.annots()).rect)
        assert held == pytest.approx(MARK, abs=0.6), held


@pytest.fixture
def view(qapp, sample_pdf):
    handle = PDFView(sample_pdf)
    handle.resize(500, 400)
    handle.show()
    qapp.processEvents()
    try:
        yield handle
    finally:
        handle.shutdown()
        handle.deleteLater()
        qapp.processEvents()


def mark_ink(page):
    """Where a page's annotations painted, in points, at its own turn."""
    marked = page.get_pixmap(matrix=fitz.Matrix(1, 1), annots=True)
    bare = page.get_pixmap(matrix=fitz.Matrix(1, 1), annots=False)
    found = [(x, y) for y in range(marked.height) for x in range(marked.width)
             if marked.pixel(x, y)[:3] != bare.pixel(x, y)[:3]]
    assert found, "nothing was painted, so the measurement says nothing"
    xs = [point[0] for point in found]
    ys = [point[1] for point in found]
    return (float(min(xs)), float(min(ys)), float(max(xs) + 1), float(max(ys) + 1))


def test_the_view_re_measures_the_page_after_a_permanent_turn(view, qapp):
    """A ``Scope.STRUCTURE`` edit is what tells the view the sheet changed."""
    assert view.page_sizes[0] == SHEET
    assert view._sheet_turns[0] == 0
    assert view.apply_edit(RotatePages(pages=(0,))) is True
    qapp.processEvents()
    assert view._sheet_turns[0] == 90
    assert view.page_sizes[0] == (SHEET[1], SHEET[0])
    assert view._sheet_space(0) == (SHEET[0], SHEET[1], 90)


def test_a_mark_still_sits_under_its_ring_after_the_page_is_turned(view, qapp):
    """The point of doing F10-4a first: rotating does not orphan the user's marks.

    The ring is drawn from the box in the file, mapped by the sheet size and the
    page's new turn; the ink is whatever MuPDF paints.  They are the same place
    before the turn and the same place after it.
    """
    assert view.annotation_manager.add_shape(0, "rect", rect=MARK) is not None
    qapp.processEvents()
    doc = view.session.open_copy(view.session.read_version())
    try:
        before = mark_ink(doc.load_page(0))
    finally:
        doc.close()
    assert before == pytest.approx(map_rect(MARK, 200.0, 100.0, 0), abs=2.0)

    assert view.apply_edit(RotatePages(pages=(0,))) is True
    qapp.processEvents()

    doc = view.session.open_copy(view.session.read_version())
    try:
        ink = mark_ink(doc.load_page(0))
    finally:
        doc.close()
    want = map_rect(MARK, *view._sheet_space(0))
    # A quarter turn of the 200x100 sheet puts the box's y range across the
    # narrow way and flips its x: (100-42, 30, 100-12, 130).
    assert want == pytest.approx((58.0, 30.0, 88.0, 130.0), abs=1.0)
    assert ink == pytest.approx(want, abs=2.0), (ink, want)


def test_a_turned_page_still_opens_in_the_view_that_saved_it(tmp_path, qapp):
    """Save, reopen, and the marks and the turn arrive together."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    session = make_session(path)
    manager = AnnotationManager(session)
    try:
        manager.add_shape(0, "rect", rect=MARK)
        session.apply(RotatePages(pages=(0,)))
        saved = save_document(session).path
    finally:
        session.close()

    handle = PDFView(saved)
    handle.resize(500, 400)
    handle.show()
    qapp.processEvents()
    try:
        assert handle._sheet_turns == [90]
        assert handle.page_sizes[0] == (SHEET[1], SHEET[0])
        marks = handle.annotation_manager.get_annotations_for_page(0)
        assert [mark.rects[0] for mark in marks] == [MARK]
    finally:
        handle.shutdown()
        handle.deleteLater()
        qapp.processEvents()


# ------------------------------------------------------------------------ the sidebar
def _painted_icon():
    """An icon standing in for a thumbnail the render pool already drew."""
    from PyQt6.QtGui import QIcon, QPixmap

    pix = QPixmap(1, 1)
    pix.fill()
    return QIcon(pix)


def test_the_thumbnail_is_measured_against_the_page_as_it_lies(tmp_path, qapp):
    """The one piece of page geometry the sidebar keeps: how big to draw a sheet.

    ``_thumb_zoom`` is ``icon width / painted width``, so a page that turns from
    200 wide to 100 wide has to be rendered twice as large to fill the same icon.
    A ``STRUCTURE`` edit is what tells the sidebar the tree moved; a smaller scope
    must not pay for that rebuild — a comment clears the pixels but keeps the list,
    and a title change is below even the scope that touches thumbnails.
    """
    from pdfar.render import RenderPool
    from pdfar.sidebar import Sidebar

    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = make_session(path)
    pool = RenderPool(session, workers=1)
    bar = Sidebar(session, pool)
    try:
        icon = bar._thumb_settings.width
        assert session.doc[0].rect.width == pytest.approx(SHEET[0])
        assert bar._thumb_zoom[0] == pytest.approx(icon / SHEET[0])

        session.apply(RotatePages(pages=(0,)))
        bar.invalidate_thumbnails(2, int(Scope.STRUCTURE))
        assert bar._thumb_zoom[0] == pytest.approx(icon / SHEET[1])
        assert bar.thumbs.count() == session.doc.page_count

        # The list survives a scope that did not move the tree.  A comment is the
        # closest neighbour of a structure edit: it can stale the pixels but not the
        # page count, so rebuilding 540 items is waste.
        before = bar._thumb_zoom[0]
        item = bar.thumbs.item(0)
        item.setText("sentinel")
        bar.invalidate_thumbnails(3, int(Scope.COMMENTS))
        assert bar.thumbs.item(0) is item
        assert bar.thumbs.item(0).text() == "sentinel"

        # A metadata edit is graded below even that: a title change moves no pixel,
        # so a thumbnail that is already painted is left painted.
        item.setIcon(_painted_icon())
        bar.invalidate_thumbnails(4, int(Scope.METADATA))
        assert bar._thumb_zoom[0] == before
        assert bar.thumbs.item(0).text() == "sentinel"
        assert not bar.thumbs.item(0).icon().isNull()
    finally:
        pool.shutdown()
        bar.deleteLater()
        session.close()
        qapp.processEvents()


# --------------------------------------------------------------------------- the command
@pytest.fixture
def window(qapp, sample_pdf):
    """The real main window with the sample document open."""
    from pdfar.main import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    win.open_document(sample_pdf)
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_the_command_turns_the_page_the_user_is_on(window, qapp):
    """Tools ▸ Rotate Page works on the current page, and on no other."""
    win = window
    win.view.goto_page(1)
    qapp.processEvents()
    assert win.view.current_page == 1
    assert rotations(win.view.session.doc) == [0, 0, 0]

    win.act_page_rotl.trigger()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [0, 270, 0]
    # A turn of the page is not a turn of the view: what was upright stays upright.
    assert win.view.rotation == 0


def test_the_page_command_and_the_view_command_stay_apart(window, qapp):
    """Ctrl+Shift+Left writes the file; Ctrl+Left only turns what is on the screen."""
    win = window
    win._rotate(90)
    qapp.processEvents()
    assert win.view.rotation == 90
    assert rotations(win.view.session.doc) == [0, 0, 0]
    assert win.view.session.dirty is False

    win.act_page_rotr.trigger()
    qapp.processEvents()
    assert win.view.rotation == 90
    assert rotations(win.view.session.doc) == [90, 0, 0]


def test_one_command_is_one_undo_step(window, qapp):
    win = window
    win.act_page_rotr.trigger()
    win.act_page_rotr.trigger()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [180, 0, 0]
    assert win.act_undo.isEnabled()

    win._undo_edit()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [90, 0, 0]
    win._redo_edit()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [180, 0, 0]


def test_a_turned_page_leaves_the_document_dirty(window, qapp):
    """The turn is in the session, not yet in the file — the title says so."""
    win = window
    win.act_page_rotr.trigger()
    qapp.processEvents()
    assert win.view.session.dirty is True
    assert win._active_tab().dirty is True


def test_the_page_commands_need_a_document(qapp, tmp_path):
    from pdfar.main import MainWindow

    path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        assert win.act_page_rotl.isEnabled() is False
        assert win.act_page_rotr.isEnabled() is False
        win.open_document(path)
        qapp.processEvents()
        assert win.act_page_rotl.isEnabled() is True
        assert win.act_page_rotr.isEnabled() is True
    finally:
        win._close_document()
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_a_turned_page_still_reads_in_reflow(window, qapp):
    """Reflow is another rendering of the same tree, so the turn reaches it.

    The command is not one of the tools reflow has to give up: it changes the
    page, and the rebuilt reading is taken from the page that comes back.
    """
    win = window
    win._toggle_reflow()
    assert wait_until(qapp, lambda: win.view._reflow_worker is None)
    assert win.view.reflow_mode
    assert win.act_page_rotr.isEnabled() is True

    win.act_page_rotr.trigger()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [90, 0, 0]
    assert win.view.reflow_mode


def test_the_commands_sit_beside_the_view_rotation(qapp, sample_pdf):
    """Menu placement is documentation: the two kinds of turn, next to each other."""
    from PyQt6.QtWidgets import QMenu

    from pdfar.main import MainWindow

    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        menus = {menu.title(): menu for menu in win.menuBar().findChildren(QMenu)}
        tools = menus["&Tools"].actions()
        order = [a for a in tools if a in (win.act_rotate_left, win.act_rotate_right,
                                           win.act_page_rotl, win.act_page_rotr)]
        assert order == [win.act_rotate_left, win.act_rotate_right,
                         win.act_page_rotl, win.act_page_rotr]
        assert win.act_page_rotl.shortcut().toString() == "Ctrl+Shift+Left"
        assert win.act_page_rotr.shortcut().toString() == "Ctrl+Shift+Right"
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


# ------------------------------------------------------- the pages themselves (F10-4d)
def _centre(lst, index):
    """The point a reader's cursor sits on to touch thumbnail *index*."""
    rect = lst.visualItemRect(lst.item(index))
    return QPoint(rect.center().x(), rect.center().y())


def _blank_below(lst):
    """A point inside the strip that is on no page: below the last thumbnail."""
    rect = lst.visualItemRect(lst.item(lst.count() - 1))
    return QPoint(rect.center().x(), rect.bottom() + 20)


def _click(lst, pos, *, ctrl=False, right=False):
    from PyQt6.QtTest import QTest

    QTest.mouseClick(lst.viewport(),
                     Qt.MouseButton.RightButton if right else Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ControlModifier if ctrl
                     else Qt.KeyboardModifier.NoModifier,
                     pos)


def _press(lst, index, **kwargs):
    _click(lst, _centre(lst, index), **kwargs)


def _pick(lst, *indices):
    """The pick a reader ends up with after clicking those pages."""
    lst.clearSelection()
    for index in indices:
        lst.item(index).setSelected(True)


def _ask_for_menu(bar, pos, app):
    """Ask for the context menu the way the window system does.

    The offscreen platform turns a right click into a selection change but never
    into a ``QContextMenuEvent``, so the event that opens the menu is handed to the
    strip's viewport — the same receiver a real right click reaches.
    """
    from PyQt6.QtWidgets import QApplication

    QApplication.sendEvent(bar.thumbs.viewport(),
                           QContextMenuEvent(QContextMenuEvent.Reason.Mouse, pos))
    app.processEvents()


def _labels(menu):
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def _act(menu, label):
    for action in menu.actions():
        if action.text() == label:
            return action
    raise AssertionError(f"{label!r} is not in {_labels(menu)}")


@pytest.fixture
def menus(monkeypatch):
    """Catch the menu a context handler would put on the screen: ``exec`` blocks."""
    from PyQt6.QtWidgets import QMenu

    shown: list = []

    def record(self, *args):
        shown.append(self)
        return None

    monkeypatch.setattr(QMenu, "exec", record)
    return shown


@pytest.fixture
def strip(qapp, tmp_path):
    """The thumbnail strip of a three-page document, shown so it has real geometry."""
    from pdfar.render import RenderPool
    from pdfar.sidebar import Sidebar

    path = build_sample_pdf(tmp_path / "book.pdf", pages=3)
    session = make_session(path)
    pool = RenderPool(session, workers=1)
    bar = Sidebar(session, pool)
    bar.resize(320, 900)
    bar.show()
    qapp.processEvents()
    try:
        yield bar
    finally:
        pool.shutdown()
        bar.deleteLater()
        session.close()
        qapp.processEvents()


def test_a_second_page_can_be_picked_in_the_strip(qapp, strip):
    """The strip is a place to *choose* pages, not only to jump to one.

    Single selection could not express "these three pages of the scan", and turning
    one page of a pick through a menu that acts on all of it needs a set to name.
    Extended selection costs the reader nothing beside that: a plain click still
    selects one page, and clicking a picked page again drops it.
    """
    lst = strip.thumbs
    assert lst.selectionMode() == QListWidget.SelectionMode.ExtendedSelection

    _press(lst, 0)
    qapp.processEvents()
    assert [item.data(Qt.ItemDataRole.UserRole) for item in lst.selectedItems()] == [0]

    _press(lst, 2, ctrl=True)
    qapp.processEvents()
    assert [item.data(Qt.ItemDataRole.UserRole) for item in lst.selectedItems()] == [0, 2]

    _press(lst, 2, ctrl=True)
    qapp.processEvents()
    assert [item.data(Qt.ItemDataRole.UserRole) for item in lst.selectedItems()] == [0]


def test_picking_pages_does_not_take_the_jump_away(qapp, strip):
    """The strip's first job is still a jump: double-clicking a page asks for it.

    Extended selection changes what a click does *to the pick*, and a double click is
    the other gesture the strip answers to.  It is the behaviour this change was most
    able to break quietly, so it is sent the whole way: ``QTest.mouseDClick`` never
    reaches ``itemDoubleClicked`` under offscreen Qt — the platform does not turn its
    synthetic pair into a double click — so the events go over one by one.
    """
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtWidgets import QApplication

    seen: list = []
    strip.goto_page_requested.connect(seen.append)
    lst = strip.thumbs
    pos = _centre(lst, 2)
    local = QPointF(pos)
    global_ = QPointF(lst.viewport().mapToGlobal(pos))

    def make(event_type, button, buttons):
        return QMouseEvent(event_type, local, global_, button, buttons,
                           Qt.KeyboardModifier.NoModifier)

    for event in (make(QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton),
                  make(QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton,
                       Qt.MouseButton.NoButton),
                  make(QEvent.Type.MouseButtonDblClick, Qt.MouseButton.LeftButton,
                       Qt.MouseButton.LeftButton),
                  make(QEvent.Type.MouseButtonRelease, Qt.MouseButton.LeftButton,
                       Qt.MouseButton.NoButton)):
        QApplication.sendEvent(lst.viewport(), event)
    qapp.processEvents()
    assert seen == [2]


def test_the_menu_acts_on_the_pick_and_on_the_page_under_the_cursor(strip):
    """Whichever pages the menu names, it is the pages the reader pointed at.

    Picked pages come out in page order whatever the order they were clicked in;
    with no pick the menu takes the page the request arrived on; and a request on
    blank space names nothing, because there is no page to turn.
    """
    _pick(strip.thumbs, 2, 0)
    assert strip._picked_pages(_centre(strip.thumbs, 1)) == (0, 2)

    strip.thumbs.clearSelection()
    assert strip._picked_pages(_centre(strip.thumbs, 1)) == (1,)
    assert strip._picked_pages(_blank_below(strip.thumbs)) == ()


def test_the_menu_names_every_page_it_is_about_to_turn(strip):
    """The label counts the pages: a turn written into the file is not free."""
    assert _labels(strip._page_rotation_menu((1,))) == [
        "Rotate Page Left", "Rotate Page Right",
        "Rotate All Pages Left", "Rotate All Pages Right"]
    assert _labels(strip._page_rotation_menu((0, 2))) == [
        "Rotate 2 Pages Left", "Rotate 2 Pages Right",
        "Rotate All Pages Left", "Rotate All Pages Right"]
    assert [a.isSeparator() for a in strip._page_rotation_menu((0,)).actions()] == [
        False, False, True, False, False]


def test_the_menu_asks_and_leaves_the_turn_to_the_window(strip):
    """The strip has a session it could edit, and must not use it.

    Undo, the re-render and the "is this document saved" answer all live behind the
    view; a sidebar that wrote the angle itself would turn the page and leave the
    screen showing the old one, with no step to undo.
    """
    seen: list = []
    strip.page_rotation_requested.connect(
        lambda pages, delta: seen.append((tuple(pages), delta)))

    menu = strip._page_rotation_menu((0, 2))
    _act(menu, "Rotate 2 Pages Left").trigger()
    _act(menu, "Rotate All Pages Right").trigger()
    assert seen == [((0, 2), -90), ((0, 1, 2), 90)]
    assert rotations(strip.doc) == [0, 0, 0]


def test_a_right_click_on_a_page_offers_to_turn_it(qapp, strip, menus):
    """The whole path, from the mouse to a menu on the screen."""
    _press(strip.thumbs, 2, right=True)
    qapp.processEvents()
    assert [item.data(Qt.ItemDataRole.UserRole) for item in strip.thumbs.selectedItems()] == [2]

    _ask_for_menu(strip, _centre(strip.thumbs, 2), qapp)
    assert menus, "the right click opened no menu"
    assert _labels(menus[-1])[:2] == ["Rotate Page Left", "Rotate Page Right"]


def test_the_blank_space_below_the_pages_offers_nothing(qapp, strip, menus):
    """No page, no menu: a request on the strip's empty tail turns no page."""
    _pick(strip.thumbs)
    _click(strip.thumbs, _blank_below(strip.thumbs), right=True)
    qapp.processEvents()
    _ask_for_menu(strip, _blank_below(strip.thumbs), qapp)
    assert menus == []


def test_the_pages_a_reader_picked_are_the_pages_that_turn(qapp, window, menus):
    """A pick, a right click and one menu item: the file holds the turn."""
    win = window
    _pick(win.sidebar.thumbs, 1, 2)
    _ask_for_menu(win.sidebar, _centre(win.sidebar.thumbs, 2), qapp)
    _act(menus[-1], "Rotate 2 Pages Right").trigger()
    qapp.processEvents()

    assert rotations(win.view.session.doc) == [0, 90, 90]
    # Not a turn of the view: what was upright on the screen stays upright.
    assert win.view.rotation == 0
    # One request is one edit, so one undo returns both pages at once.
    win._undo_edit()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [0, 0, 0]


def test_rotate_all_pages_turns_a_book_that_arrived_sideways(qapp, window, menus):
    """The scanned book that came in upside down: one item, every page, one undo."""
    win = window
    _pick(win.sidebar.thumbs, 0)
    _ask_for_menu(win.sidebar, _centre(win.sidebar.thumbs, 0), qapp)
    _act(menus[-1], "Rotate All Pages Left").trigger()
    qapp.processEvents()

    assert rotations(win.view.session.doc) == [270, 270, 270]
    win._undo_edit()
    qapp.processEvents()
    assert rotations(win.view.session.doc) == [0, 0, 0]


def test_only_the_tab_that_asked_answers_the_request(qapp, window, tmp_path):
    """A sidebar belongs to its own document.

    The handler turns the pages through the view that is on screen, which is why the
    connection has to come apart with the tab: a stale one would let a hidden
    document's thumbnail strip edit the file the reader is looking at.
    """
    win = window
    first = win.sidebar
    assert win.open_document(build_sample_pdf(tmp_path / "second.pdf", pages=2))
    qapp.processEvents()
    assert win.tabs.count() == 2
    assert first is not win.sidebar
    try:
        first.page_rotation_requested.emit((0,), 90)
        qapp.processEvents()
        assert rotations(win.view.session.doc) == [0, 0]

        win.tabs.setCurrentIndex(0)
        qapp.processEvents()
        first.page_rotation_requested.emit((0,), 90)
        qapp.processEvents()
        assert rotations(win.view.session.doc) == [90, 0, 0]
    finally:
        while win.tabs.count():
            win._close_tab(win.tabs.count() - 1, confirm=False)
            qapp.processEvents()
