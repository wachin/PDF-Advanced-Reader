"""F10-4ba: a blank sheet into the gap the reader names.

The fourth page-tree ask.  Rotate, delete, move and reverse all rearrange sheets
that exist; this one invents a sheet — the first edit whose :class:`Renumbering`
carries a ``-1``, and the first whose *size* has to be decided by PDFAR rather
than carried from another page.

Measured against MuPDF first (the numbers below are from that pass):

* ``doc.new_page(pno=at, ...)`` splices the sheet into the ``/Kids`` of **the
  node the slot belongs to** and re-derives ``/Count`` up the chain — insert at
  2 of a split tree went before the old page 2 inside its own ancestor, and the
  rest of the tree was not touched.  PdfCraft's ``rebuild``
  (``crates/organize/src/lib.rs:168``) does the opposite: a settle-and-flatten of
  the whole book for any insert.  PDFAR keeps the producer's tree, because MuPDF
  lets it.
* the new page states ``/Rotate 0`` and gets an empty ``/Resources`` — so a book
  whose neighbours' geometry lives on an ancestor would arrive *unturned* beside
  turned sheets.  The edit copies what the sheet below the gap states for
  ``/MediaBox``, ``/CropBox`` and ``/Rotate`` — and where that sheet states
  nothing, states nothing itself, into the same ``/Parent``, which is the one
  arrangement where "asks the same questions, gets the same answers" is a
  theorem rather than a hope: the blank resolves through the chain its neighbour
  resolves through.
* MuPDF re-aims ``/PageLabels`` under an insert by itself — a range that starts
  at the pushed-down sheet follows it (measured: start 2 became 3, and a roman
  run continued over the blank) — and outline *destinations* are page objects,
  so a row comes back with the sheet it names.  Neither is PDFAR's to repair;
  both are pinned here because a future library must not be allowed to disagree
  silently.
* ``pno == count`` inserts at the end; beyond it MuPDF raises, so the edit
  refuses a position outside ``0..count`` before asking.

The reading position needs no choreography the move did not already have: the
map is exact for every sheet that survives, and the viewer, the strip and the
bookmarks spend it the way ``F10-4p`` taught them.  And the command carries
``requires_assembly`` like every page-tree edit: Table 22's bit 11 is the right
to *insert* pages in the standard's own sentence for it.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_settle import nested_pdf, own
from PyQt6.QtWidgets import QMessageBox

from pdfar.edits import ASSEMBLY_NOTE, EditError, Renumbering, Scope
from pdfar.main import MainWindow
from pdfar.pages import InsertBlankPage
from pdfar.save import save_document

PAGES = 4
READER = "reader"
OWNER = "someone-elses-password"
SEALED = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY

#: The sample book's sheets, said by their first line; "" names a blank.
SHEETS = ["Hello PDFAR world"] + [f"Page {n} content" for n in range(2, PAGES + 1)]


def protected_pdf(path, permissions, *, user=READER, owner=OWNER, pages=PAGES):
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    staged = path + ".enc"
    doc.save(staged, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner,
              user_pw=user, permissions=permissions)
    doc.close()
    os.replace(staged, path)
    return path


def first_lines(doc):
    """The first printed line of every sheet — "" for one that says nothing."""
    out = []
    for index in range(doc.page_count):
        lines = [line for line in doc[index].get_text().splitlines() if line.strip()]
        out.append(lines[0] if lines else "")
    return out


def says_nothing(page):
    """No text, no drawing: blank is a claim about both."""
    if page.get_text().strip():
        return False
    pix = page.get_pixmap()
    for y in range(pix.height):
        for x in range(pix.width):
            if pix.pixel(x, y) != (255, 255, 255):
                return False
    return True


@pytest.fixture
def book(tmp_path):
    path = str(tmp_path / "book.pdf")
    build_sample_pdf(path, pages=PAGES)
    return path


@pytest.fixture
def session(book):
    handle = make_session(book)
    try:
        yield handle
    finally:
        handle.close()


# ------------------------------------------------------------------ the sheet itself


def test_the_blank_lands_on_the_number_the_gap_names(session):
    """Insert at 1: the blank becomes page 2, and every sheet above it moves up."""
    session.apply(InsertBlankPage(at=1))
    assert first_lines(session.doc) == [SHEETS[0], "", SHEETS[1], SHEETS[2],
                                        SHEETS[3]]


def test_both_ends_of_the_book_take_a_page(session):
    session.apply(InsertBlankPage(at=0))
    assert first_lines(session.doc)[0] == ""
    session.apply(InsertBlankPage(at=session.doc.page_count))
    assert first_lines(session.doc)[-1] == ""
    assert session.doc.page_count == PAGES + 2


def test_a_blank_sheet_says_nothing(session):
    session.apply(InsertBlankPage(at=1))
    assert says_nothing(session.doc[1])


def test_a_position_outside_the_book_is_refused_whole(session):
    before = first_lines(session.doc)
    for bad in (-1, PAGES + 1, 4096):
        with pytest.raises(EditError) as caught:
            session.apply(InsertBlankPage(at=bad))
        assert str(PAGES) in str(caught.value)
    assert session.doc.page_count == PAGES
    assert first_lines(session.doc) == before
    assert session.renumbering is None


# ------------------------------------------------------------------- the geometry


def test_it_wears_the_stated_geometry_of_the_sheet_below_the_gap(session):
    """The sample's sheets state their own boxes; the blank states the same."""
    neighbour = session.doc[1].rect
    session.apply(InsertBlankPage(at=1))
    blank = session.doc[1]
    assert blank.rect == neighbour
    assert own(session.doc, 1, "MediaBox") != "null"


def test_a_blank_beside_inherited_geometry_inherits_the_same_way(tmp_path):
    """mid1 turns its sheets 90° from the ancestor; none of them states it.

    Inserting at the head puts the blank in ``mid1`` before the first sheet,
    stating nothing — so it resolves its turn and its box through the very chain
    its neighbour does, and the two display alike.
    """
    path = nested_pdf(tmp_path / "nested.pdf")
    handle = make_session(path)
    try:
        sheet0 = handle.doc[0].rect, handle.doc[0].rotation
        handle.apply(InsertBlankPage(at=0))
        assert own(handle.doc, 0, "Rotate") == "null"
        assert own(handle.doc, 0, "MediaBox") == "null"
        assert (handle.doc[0].rect, handle.doc[0].rotation) == sheet0
    finally:
        handle.close()


def test_a_page_that_states_its_own_turn_hands_it_over(tmp_path):
    """The nested fixture's second sheet states ``/Rotate 180``; a blank below it
    (gap at 1) must arrive turned like it — by stating the same thing."""
    path = nested_pdf(tmp_path / "nested.pdf")
    handle = make_session(path)
    try:
        handle.apply(InsertBlankPage(at=1))
        assert own(handle.doc, 1, "Rotate") == "180"
        assert handle.doc[1].rotation == 180
        assert handle.doc[1].rect == handle.doc[2].rect
    finally:
        handle.close()


def test_an_inherited_crop_reaches_the_blank_too(tmp_path):
    """The third sheet is cropped by an ancestor and states nothing itself; the
    blank appended at the end lands in its node and wears the same crop."""
    path = nested_pdf(tmp_path / "nested.pdf")
    handle = make_session(path)
    try:
        cropped = handle.doc[2].rect
        assert own(handle.doc, 2, "CropBox") == "null"
        handle.apply(InsertBlankPage(at=handle.doc.page_count))
        assert own(handle.doc, 3, "CropBox") == "null"
        assert handle.doc[3].rect == cropped
    finally:
        handle.close()


# -------------------------------------------------------------------- the map


def test_the_step_publishes_the_sheet_it_invented():
    """``-1`` marks the invented sheet: everything else keeps an exact number."""
    assert InsertBlankPage(at=1).page_order(3) == (0, -1, 1, 2)
    assert InsertBlankPage(at=0).page_order(3) == (-1, 0, 1, 2)
    assert InsertBlankPage(at=3).page_order(3) == (0, 1, 2, -1)


def test_the_map_carries_every_number_over_the_gap(session):
    session.apply(InsertBlankPage(at=2))
    move = session.renumbering
    assert move.where(0) == 0
    assert move.where(2) == 3          # the pushed-down sheet
    assert move.where(3) == 4
    assert move.followed(2) == 3
    back = Renumbering(move.order, backward=True)
    assert [back.where(index) for index in (0, 1, 2, 3, 4)] == [0, 1, 2, 2, 3]


def test_the_edit_answers_like_a_page_tree_edit():
    edit = InsertBlankPage(at=1)
    assert edit.requires_assembly is True
    assert edit.scope is Scope.STRUCTURE
    assert edit.changed is None        # every number at or above the gap moves
    assert edit.label == "Insert Blank Page"


# ------------------------------------------------------- what follows by itself


def test_the_numbering_continues_over_the_blank(tmp_path):
    """MuPDF re-aims the ranges under the insert; pin the file's answer, live and
    reopened, so a library that stops doing it cannot pass silently."""
    path = str(tmp_path / "numbered.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        doc.new_page(width=200, height=100)
    doc.set_page_labels([
        {"startpage": 0, "prefix": "p", "style": "r"},
        {"startpage": 2, "prefix": "x", "style": "a"}])
    doc.save(path, garbage=3)
    doc.close()

    handle = make_session(path)
    try:
        assert [handle.doc[i].get_label() for i in range(PAGES)] == \
            ["pi", "pii", "xa", "xb"]
        handle.apply(InsertBlankPage(at=1))
        assert [handle.doc[i].get_label() for i in range(PAGES + 1)] == \
            ["pi", "pii", "piii", "xa", "xb"]
        save_document(handle)
    finally:
        handle.close()
    again = fitz.open(path)
    try:
        assert [again[i].get_label() for i in range(PAGES + 1)] == \
            ["pi", "pii", "piii", "xa", "xb"]
    finally:
        again.close()


def test_the_outline_rows_follow_their_own_sheets(tmp_path):
    """Destinations are page objects, not numbers: a row keeps its sheet.

    The outline is written to the file before the session opens it — an
    in-memory ``set_toc`` is not what the working copy is made of, and the edit
    framework rightly drops it.
    """
    path = str(tmp_path / "booked.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        doc.new_page(width=200, height=100)
    doc.set_toc([[1, "front", 1], [1, "middle", 3], [1, "back", 4]])
    doc.save(path)
    doc.close()
    handle = make_session(path)
    try:
        assert handle.doc.get_toc() == [[1, "front", 1], [1, "middle", 3],
                                       [1, "back", 4]]
        handle.apply(InsertBlankPage(at=1))
        assert handle.doc.get_toc() == [[1, "front", 1], [1, "middle", 4],
                                        [1, "back", 5]]
    finally:
        handle.close()


# ------------------------------------------------------------------ undo & gate


def test_undo_takes_the_blank_back_out(session):
    before = first_lines(session.doc)
    session.apply(InsertBlankPage(at=2))
    session.undo()
    assert session.doc.page_count == PAGES
    assert first_lines(session.doc) == before
    back = session.renumbering
    assert back is not None and back.backward
    assert back.where(3) == 2          # the sheet that was pushed down goes home
    # A mark the reader made *on the blank* keeps its number: the sheet that comes
    # back into that slot is the one the number names now (F10-4g's rule, asked of
    # a page with no past rather than a page that went).
    assert back.where(2) == 2


def test_a_sealed_book_refuses_the_gap_and_a_lifted_one_opens_it(tmp_path):
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    handle = make_session(path, READER)
    try:
        with pytest.raises(EditError) as caught:
            handle.apply(InsertBlankPage(at=1))
        assert str(caught.value) == ASSEMBLY_NOTE
        assert handle.doc.page_count == PAGES
        assert handle.unlock_permissions(OWNER)
        handle.apply(InsertBlankPage(at=1))
        assert handle.doc.page_count == PAGES + 1
    finally:
        handle.close()


# ---------------------------------------------------------------------- surfaces


@pytest.fixture
def window(qapp, monkeypatch):
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))
    win = MainWindow()
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


def open_book(win, path, password=""):
    assert win.open_document(path, password=password)
    return win.view.session


def test_the_command_inserts_after_the_page_under_the_eyes(window, book):
    handle = open_book(window, book)
    window.view.goto_page(1)
    window.act_page_insert.trigger()
    assert first_lines(window.view.session.doc) == [SHEETS[0], SHEETS[1], "",
                                                    SHEETS[2], SHEETS[3]]
    # The reader's own sheet did not move: the gap opened below it.
    assert window.view.current_page == 1
    assert handle.doc.page_count == PAGES + 1


def test_the_item_greys_with_its_group_on_a_sealed_book(window, tmp_path):
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    open_book(window, path, password=READER)
    for name in ("act_page_rotl", "act_page_rotr", "act_page_delete",
                 "act_page_insert"):
        action = getattr(window, name)
        assert not action.isEnabled(), name
        assert action.toolTip() == ASSEMBLY_NOTE, name
    window._close_all_tabs(confirm=False)
    plain = str(tmp_path / "plain.pdf")
    build_sample_pdf(plain)
    open_book(window, plain)
    assert window.act_page_insert.isEnabled()
    assert ASSEMBLY_NOTE not in window.act_page_insert.toolTip()


def test_the_strip_asks_for_the_gap_after_the_last_page_picked(window, book):
    open_book(window, book)
    sidebar = window.sidebar
    menu = sidebar._page_commands_menu((1, 3))
    item = next(action for action in menu.actions()
                if action.text() == "Insert Blank Page")
    assert item.isEnabled()
    item.trigger()
    # Pick (1, 3): the last sheet picked is page 4; the blank follows it.
    got = [page.get_text().splitlines()[0] if page.get_text().strip() else ""
           for page in window.view.session.doc]
    assert got[:5] == [SHEETS[0], SHEETS[1], SHEETS[2], SHEETS[3], ""]
