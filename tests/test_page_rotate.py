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
