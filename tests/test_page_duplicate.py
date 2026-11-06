"""F10-4bb: the picked sheets, copied — and the copy is its own sheet.

The fifth page-tree ask, and the first one that makes the tree hold the *same
words* twice.  Everything about it turns on which object a copy is, so that was
measured before anything was written (the numbers below are from that pass):

* ``copy_page`` is the obvious call and the wrong one. Its own docstring says
  "this will only create another reference of the same page object", and that is
  what it does: the xrefs of a 4-sheet book after ``copy_page(1, to=3)`` are
  ``[4, 10, 14, 10, 18]`` — one object filed in two slots.  Three things go wrong
  with it.  Annotations are shared, so a note written on the copy is on the
  original too.  The tree invariant the settle, the strip and every reorder in
  this file lean on — one page object, one slot — is broken, and on the nested
  fixture the shared object ends up in two nodes' ``/Kids`` while stating a
  single ``/Parent``, displaying like whichever chain MuPDF resolves first.  And
  PdfCraft's own test forbids it (``duplicate_pages_become_independent_copies``,
  "a page object may appear only once in the tree").
* ``fullcopy_page`` is the one PDFAR uses: a new object, its own ``/Contents``
  (an array of two streams on the source comes out as one merged stream, with the
  same words on the page), its own ``/Annots`` deep-copied, and **``/Resources``
  shared** with the source — the same dictionary object, which is exactly what
  PdfCraft promises when it says a duplicate "shares fonts and images with the
  originals", and it costs nothing: the sheets print the same glyphs because they
  point at the same font files.
* ``to`` is "insert before this index", and ``to == page_count`` raises
  ``ValueError: bad page number(s)``; appending is ``to = -1``.  The pick's copies
  land **as a block in the gap below the last sheet picked**, in pick order, which
  is PdfCraft's ``duplicate_pages`` default (``at: None`` → ``last + 1``) and the
  same rule the insert above it shipped with.  The alternative — each copy
  immediately after its own source — is what a reverse-sorted walk gives, and it
  was not taken: it re-numbers the unpicked sheets *between* the picked ones, so a
  reader who picks pages 2 and 4 and gets copies after both has moved page 5 twice
  to ask for two sheets.
* the duplicate is the F10-4o problem again, and the settle is load-bearing here
  rather than in the docstring.  ``fullcopy_page`` files the new object in the node
  that owns the **landing slot** and copies only what the source states, so on the
  nested book an unsettled ``fullcopy_page(0, to=-1)`` lands in the cropped node and
  comes out 260×360 upright while its source is 400×300 turned.  After
  ``SettlePageTree`` every sheet states its own box, crop and turn, and the copy
  carries those stated values to wherever it lands: measured source and copy alike
  at ``(400, 300)`` with ``/Rotate 90`` stated on both.
* MuPDF re-aims the outline by itself — destinations are page objects, so a row
  comes back with its sheet (``front``/``middle`` stayed on 2 and 4 while ``back``
  walked to 8) — and it leaves ``/PageLabels`` counting **positions**, which is the
  reading ``tests/test_page_labels.py`` pins for every reorder: the copies wear the
  labels of the numbers they arrived at, not of the sheets they were made from.

The map is where this edit first stops being a permutation, and it needed reading
rather than assuming: ``order[i]`` is the old number of the sheet at slot *i*, so a
duplicated sheet appears **twice**, and ``where``/``followed`` answer
``order.index(page)`` — the *first* slot, which is the original.  A bookmark made on
page 4 stays on the sheet that was on page 4 before the copy was made of it, and the
copy arrives unmarked, which is the only answer that does not move the reader's mark
onto a sheet they never pointed at.  Undo runs the same tuple backwards, where
``order[page]`` is read instead: a mark made on a copy goes home to its original,
which is the sheet that survives the undo.

Cost, for the record: 0.46 ms per copy inside a live 540-sheet tree (against the
insert's 1.20 ms for the same trick with ``new_page``).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from test_page_settle import nested_pdf, own
from PyQt6.QtWidgets import QMessageBox

from pdfar.edits import ASSEMBLY_NOTE, EditError, Scope
from pdfar.main import MainWindow
from pdfar.pages import DuplicatePages
from pdfar.save import save_document

PAGES = 6
READER = "reader"
OWNER = "someone-elses-password"
SEALED = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY

#: The sample book's sheets, said by their first line.
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
    """The first printed line of every sheet, in the order the tree reads them."""
    out = []
    for index in range(doc.page_count):
        lines = [line for line in doc[index].get_text().splitlines() if line.strip()]
        out.append(lines[0] if lines else "")
    return out


def xrefs(doc):
    """The object each slot holds — the tree's own answer, not MuPDF's cache."""
    return [doc.load_page(index).xref for index in range(doc.page_count)]


def appearance(doc, index):
    """How a sheet is drawn: the box it shows and the turn it is at."""
    page = doc[index]
    return (page.rect, page.rotation)


@pytest.fixture
def book(tmp_path):
    path = str(tmp_path / "book.pdf")
    build_sample_pdf(path, pages=PAGES)
    return path


@pytest.fixture
def noted_book(tmp_path):
    """The same book with one note written on page 2 — on disk, not in the session.

    A session with no edits yet takes its working copy from the *file*, so a note
    added to the live document after ``make_session`` is not in what the edit is
    handed, and the test would be about the framework rather than about ``/Annots``.
    """
    path = str(tmp_path / "noted.pdf")
    build_sample_pdf(path, pages=PAGES)
    doc = fitz.open(path)
    page = doc[1]
    page.add_text_annot(fitz.Point(10, 10), "on the original").update()
    doc.save(path + ".annot")
    doc.close()
    os.replace(path + ".annot", path)
    return path


@pytest.fixture
def session(book):
    handle = make_session(book)
    try:
        yield handle
    finally:
        handle.close()


# --------------------------------------------------------------- the copy is its own


def test_the_copy_is_a_sheet_of_its_own(session):
    """A second slot, a second object — and the same words on it."""
    session.apply(DuplicatePages(pages=(1,)))
    assert session.doc.page_count == PAGES + 1
    assert first_lines(session.doc) == [SHEETS[0], SHEETS[1], SHEETS[1],
                                        SHEETS[2], SHEETS[3], SHEETS[4], SHEETS[5]]
    assert session.doc.load_page(1).xref != session.doc.load_page(2).xref


def test_no_page_object_is_filed_twice(session):
    """PdfCraft's invariant, ported: one object, one slot.

    ``copy_page`` would pass a text test — two slots reading the same words is
    exactly what a reader asked for — and leave the book with a sheet hanging off
    two ``/Kids`` at once, which is the shape every reorder here is written
    against.
    """
    session.apply(DuplicatePages(pages=(0, 2, 3)))
    slots = xrefs(session.doc)
    assert len(slots) == PAGES + 3
    assert len(set(slots)) == len(slots)


def test_a_note_on_the_copy_is_not_a_note_on_the_source(noted_book):
    """``/Annots`` is deep-copied: the two sheets carry their own writing."""
    handle = make_session(noted_book)
    try:
        handle.apply(DuplicatePages(pages=(1,)))
        doc = handle.doc
        original, copy = doc[1], doc[2]
        assert [a.info["content"] for a in original.annots()] == ["on the original"]
        assert [a.info["content"] for a in copy.annots()] == ["on the original"]
        copy.add_text_annot(fitz.Point(20, 20), "on the copy").update()
        assert len(list(original.annots())) == 1
        assert [a.info["content"] for a in copy.annots()] == [
            "on the original", "on the copy"]
        assert ([a.xref for a in original.annots()]
                != [a.xref for a in copy.annots()])
    finally:
        handle.close()


def test_the_copy_shares_its_source_fonts_and_images(session):
    """``/Resources`` is the same object, ``/Contents`` is not.

    The shared half is PdfCraft's promise — a duplicate adds no copy of the fonts
    and pictures to the file — and the private half is what makes the two sheets
    editable apart.
    """
    session.apply(DuplicatePages(pages=(1,)))
    doc = session.doc
    source, copy = doc.load_page(1).xref, doc.load_page(2).xref
    assert doc.xref_get_key(copy, "Resources") == doc.xref_get_key(source, "Resources")
    assert doc.xref_get_key(copy, "Contents") != doc.xref_get_key(source, "Contents")


# ------------------------------------------------------------------- which slots


def test_the_pick_copies_as_a_block_below_the_last_one(session):
    """Pick pages 2 and 4: both copies go under page 4, in the order picked."""
    session.apply(DuplicatePages(pages=(1, 3)))
    assert first_lines(session.doc) == [SHEETS[0], SHEETS[1], SHEETS[2], SHEETS[3],
                                        SHEETS[1], SHEETS[3], SHEETS[4], SHEETS[5]]


def test_a_sheet_named_twice_is_copied_once(session):
    session.apply(DuplicatePages(pages=(2, 2)))
    assert session.doc.page_count == PAGES + 1
    assert first_lines(session.doc)[2:4] == [SHEETS[2], SHEETS[2]]


def test_the_end_of_the_book_appends_in_pick_order(session):
    """The gap below the last sheet is the end, and the end is ``to = -1``:
    ``fullcopy_page`` refuses ``to == page_count`` outright."""
    session.apply(DuplicatePages(pages=(4, 5)))
    assert first_lines(session.doc) == SHEETS + [SHEETS[4], SHEETS[5]]


def test_the_whole_book_copies_after_itself(session):
    session.apply(DuplicatePages(pages=tuple(range(PAGES))))
    assert first_lines(session.doc) == SHEETS + SHEETS
    assert len(set(xrefs(session.doc))) == 2 * PAGES


# ---------------------------------------------------------------------- refusals


def test_nothing_is_chosen_and_nothing_is_copied(session):
    with pytest.raises(EditError) as caught:
        session.apply(DuplicatePages(pages=()))
    assert str(caught.value) == "No page was chosen to duplicate"
    assert session.doc.page_count == PAGES


@pytest.mark.parametrize("bad", [-1, PAGES, PAGES + 3, 4096])
def test_a_page_outside_the_book_is_refused_whole(session, bad):
    """``-1`` is the one worth naming: Python reads it as the last sheet and
    ``fullcopy_page`` agrees, while a pick from the strip means no page at all."""
    before = first_lines(session.doc)
    with pytest.raises(EditError) as caught:
        session.apply(DuplicatePages(pages=(1, bad)))
    assert str(PAGES) in str(caught.value)
    assert first_lines(session.doc) == before
    assert session.doc.page_count == PAGES
    assert session.renumbering is None


# ------------------------------------------------------------------- the geometry


def test_a_copy_lands_wearing_its_own_source(tmp_path):
    """The nested book: geometry lives on two ancestors, and the copies land in the
    node at the end of the tree — the arrangement that a bare ``fullcopy_page`` gets
    wrong, and the reason the settle comes first here as it does for every reorder."""
    path = nested_pdf(tmp_path / "nested.pdf")
    handle = make_session(path)
    try:
        wanted = {index: appearance(handle.doc, index) for index in range(3)}
        handle.apply(DuplicatePages(pages=(0, 2)))
        doc = handle.doc
        assert appearance(doc, 3) == wanted[0]
        assert appearance(doc, 4) == wanted[2]
        # Stated, not inherited: the copy asks the same questions as its source did.
        assert own(doc, 3, "Rotate") == "90"
        assert own(doc, 4, "CropBox") == "[20 20 280 380]"
    finally:
        handle.close()


def test_without_the_push_down_the_copy_wears_the_node_it_lands_in(tmp_path):
    """The picture the settle prevents, measured rather than asserted about."""
    path = nested_pdf(tmp_path / "nested.pdf")
    doc = fitz.open(path)
    try:
        source = appearance(doc, 0)
        assert source == (fitz.Rect(0, 0, 400, 300), 90)
        doc.fullcopy_page(0, to=-1)
        assert appearance(doc, 3) != source
        assert own(doc, 3, "Rotate") == "null"
    finally:
        doc.close()


# ---------------------------------------------------------------- the read-back


def test_a_library_that_files_nothing_is_refused(monkeypatch, book):
    """The walk is read back, and the read-back is attached.

    Nothing here is expected to fail — every pick on every book in this file lands
    where the rule says — so the only way to see the guard work is to hand the edit a
    MuPDF that accepted the ask and wrote no sheet, which is what a change in
    ``fullcopy_page``'s semantics would look like from here.
    """
    monkeypatch.setattr(fitz.Document, "fullcopy_page",
                        lambda self, pno, to=0: None)
    doc = fitz.open(book)
    try:
        with pytest.raises(EditError, match="does not read the copies"):
            DuplicatePages(pages=(1,)).apply(doc)
    finally:
        doc.close()


def test_one_object_filed_twice_is_refused(monkeypatch, book):
    """The other half of the guard: two slots, one object, which is the shape the
    edit exists to avoid and the one ``copy_page`` makes."""
    monkeypatch.setattr(fitz.Document, "fullcopy_page", fitz.Document.copy_page)
    doc = fitz.open(book)
    try:
        with pytest.raises(EditError, match="does not read the copies"):
            DuplicatePages(pages=(1,)).apply(doc)
    finally:
        doc.close()


# ------------------------------------------------------------------------ the map


def test_the_published_order_holds_a_sheet_twice():
    """The first edit whose map is not a permutation: a copied number appears twice."""
    order = DuplicatePages(pages=(1, 3)).page_order(PAGES)
    assert order == (0, 1, 2, 3, 1, 3, 4, 5)
    assert len(order) == PAGES + 2
    assert order.count(1) == 2
    assert DuplicatePages(pages=(4, 5)).page_order(PAGES) == (0, 1, 2, 3, 4, 5, 4, 5)
    assert DuplicatePages(pages=(2, 2)).page_order(PAGES) == (0, 1, 2, 2, 3, 4, 5)


def test_a_mark_stays_on_the_sheet_it_was_made_on(session):
    """``where`` answers the first slot, which is the original — the copy arrives
    unmarked, because nothing outside the document ever pointed at it."""
    session.apply(DuplicatePages(pages=(1, 3)))
    move = session.renumbering
    assert move is not None
    assert move.where(1) == 1
    assert move.where(3) == 3
    assert move.where(4) == 6          # the sheet above the block, pushed down twice
    assert move.followed(1) == 1
    assert [move.where(index) for index in range(PAGES)] == [0, 1, 2, 3, 6, 7]


def test_undo_takes_the_copies_back_out(session):
    before = first_lines(session.doc)
    session.apply(DuplicatePages(pages=(1, 3)))
    session.undo()
    assert session.doc.page_count == PAGES
    assert first_lines(session.doc) == before
    back = session.renumbering
    assert back is not None and back.backward
    assert back.where(5) == 3          # the pushed-down sheet goes home
    assert back.where(4) == 1          # a mark on a copy lands on the surviving sheet
    assert [back.where(index) for index in range(8)] == [0, 1, 2, 3, 1, 3, 4, 5]


def test_the_edit_answers_like_a_page_tree_edit():
    edit = DuplicatePages(pages=(1, 3))
    assert edit.requires_assembly is True
    assert edit.scope is Scope.STRUCTURE
    assert edit.changed is None        # every number below the block moves
    assert edit.pages == (1, 3)
    assert edit.label == "Duplicate 2 Pages"
    assert DuplicatePages(pages=(2,)).label == "Duplicate Page"
    assert DuplicatePages(pages=(2, 2)).label == "Duplicate Page"


# ------------------------------------------------- what follows by itself


def test_the_numbering_counts_positions_not_sheets(tmp_path):
    """The copies wear the labels of the numbers they arrived at.

    Pinned live, on disk and reopened, because it is MuPDF's decision and not
    PDFAR's: ``/PageLabels`` numbers positions for every reorder in this file
    (``tests/test_page_labels.py``), and a library that started following the
    sheets instead would undo what a reverse is for.
    """
    path = str(tmp_path / "numbered.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        page = doc.new_page(width=200, height=100)
        page.insert_text((10, 30), f"Page {index + 1} content")
    doc.set_page_labels([
        {"startpage": 0, "prefix": "p", "style": "r"},
        {"startpage": 2, "prefix": "x", "style": "a"}])
    doc.save(path)
    doc.close()

    handle = make_session(path)
    try:
        assert [handle.doc[i].get_label() for i in range(PAGES)] == \
            ["pi", "pii", "xa", "xb", "xc", "xd"]
        handle.apply(DuplicatePages(pages=(1, 3)))
        assert [handle.doc[i].get_label() for i in range(PAGES + 2)] == \
            ["pi", "pii", "xa", "xb", "xc", "xd", "xe", "xf"]
        save_document(handle)
    finally:
        handle.close()
    again = fitz.open(path)
    try:
        assert [again[i].get_label() for i in range(PAGES + 2)] == \
            ["pi", "pii", "xa", "xb", "xc", "xd", "xe", "xf"]
    finally:
        again.close()


def test_the_outline_rows_follow_their_own_sheets(tmp_path):
    """Two rows stay, one walks: destinations are page objects, and the copies are
    objects nobody had named."""
    path = str(tmp_path / "booked.pdf")
    doc = fitz.open()
    for index in range(PAGES):
        doc.new_page(width=200, height=100)
    doc.set_toc([[1, "front", 2], [1, "middle", 4], [1, "back", 6]])
    doc.save(path)
    doc.close()
    handle = make_session(path)
    try:
        handle.apply(DuplicatePages(pages=(1, 3)))
        assert handle.doc.get_toc() == [[1, "front", 2], [1, "middle", 4],
                                        [1, "back", 8]]
    finally:
        handle.close()


# ------------------------------------------------------------------ the gate


def test_a_sealed_book_refuses_the_copies_and_a_lifted_one_makes_them(tmp_path):
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    handle = make_session(path, READER)
    try:
        with pytest.raises(EditError) as caught:
            handle.apply(DuplicatePages(pages=(1,)))
        assert str(caught.value) == ASSEMBLY_NOTE
        assert handle.doc.page_count == PAGES
        assert handle.unlock_permissions(OWNER)
        handle.apply(DuplicatePages(pages=(1,)))
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


def test_the_command_copies_the_page_under_the_eyes(window, book):
    handle = open_book(window, book)
    window.view.goto_page(1)
    window.act_page_duplicate.trigger()
    assert first_lines(handle.doc) == [SHEETS[0], SHEETS[1], SHEETS[1], SHEETS[2],
                                       SHEETS[3], SHEETS[4], SHEETS[5]]
    # The reader's own sheet did not move, and the reader did not either.
    assert window.view.current_page == 1
    assert handle.doc.page_count == PAGES + 1


def test_the_command_leaves_a_pick_it_never_went_through(window, book):
    """The Tools route asks for one sheet and moves no selection to match.

    The strip's pick is the reader's own gathering; a command they reached for in the
    menu has no claim on it, and re-aiming it at a copy would silently change what the
    next Rotate or Delete applies to.
    """
    handle = open_book(window, book)
    window.sidebar.select_pages((0, 2))
    window.view.goto_page(4)
    window.act_page_duplicate.trigger()
    assert first_lines(handle.doc) == [SHEETS[0], SHEETS[1], SHEETS[2], SHEETS[3],
                                       SHEETS[4], SHEETS[4], SHEETS[5]]
    assert window.sidebar.selected_pages() == (0, 2)


def test_the_command_greys_with_its_group_on_a_sealed_book(window, tmp_path):
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    open_book(window, path, password=READER)
    for name in ("act_page_rotl", "act_page_rotr", "act_page_delete",
                 "act_page_insert", "act_page_duplicate"):
        action = getattr(window, name)
        assert not action.isEnabled(), name
        assert action.toolTip() == ASSEMBLY_NOTE, name
    window._close_all_tabs(confirm=False)
    plain = str(tmp_path / "plain.pdf")
    build_sample_pdf(plain)
    open_book(window, plain)
    for name in ("act_page_rotl", "act_page_rotr", "act_page_delete",
                 "act_page_insert", "act_page_duplicate"):
        action = getattr(window, name)
        assert action.isEnabled(), name
        # Restored, not merely cleared: the gate writes its reason over the sentence the
        # item was built with, and F10-4z keeps that sentence on the action so the next
        # document can put it back.  With nothing kept, ``QAction.toolTip`` echoes the
        # label — an item that repeats its own name explains nothing.
        assert action.toolTip(), name
        assert action.toolTip() != action.text(), name
        assert ASSEMBLY_NOTE not in action.toolTip(), name


def test_the_strip_asks_for_the_copies_after_the_last_page_picked(window, book):
    open_book(window, book)
    sidebar = window.sidebar
    menu = sidebar._page_commands_menu((1, 3))
    item = next(action for action in menu.actions()
                if action.text() == "Duplicate 2 Pages")
    assert item.isEnabled()
    item.trigger()
    # Pick (1, 3): the last sheet picked is page 4, and both copies follow it.
    assert first_lines(window.view.session.doc) == [SHEETS[0], SHEETS[1], SHEETS[2],
                                                   SHEETS[3], SHEETS[1], SHEETS[3],
                                                   SHEETS[4], SHEETS[5]]


def test_the_strip_greys_the_copies_it_would_otherwise_make(window, tmp_path):
    """The item is there, greyed, with the reason — and it keeps the count its name
    carries, because a sealed book still knows how many sheets were picked."""
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    open_book(window, path, password=READER)
    menu = window.sidebar._page_commands_menu((1, 3))
    item = next(action for action in menu.actions()
                if action.text() == "Duplicate 2 Pages")
    assert not item.isEnabled()
    assert item.toolTip() == ASSEMBLY_NOTE


def test_the_pick_moves_onto_the_copies(window, book):
    """The strip's pick follows the work: a reader who duplicated two sheets means
    to do something to *them* next, and PdfCraft re-selects the block for the same
    reason (``crates/ui-egui/src/editing.rs:146-151``)."""
    open_book(window, book)
    sidebar = window.sidebar
    sidebar.select_pages((1, 3))
    item = next(action for action in sidebar._page_commands_menu((1, 3)).actions()
                if action.text() == "Duplicate 2 Pages")
    item.trigger()
    assert sidebar.selected_pages() == (4, 5)


def test_duplicate_and_insert_sit_side_by_side_in_the_strip(window, book):
    open_book(window, book)
    labels = [action.text() for action in
              window.sidebar._page_commands_menu((1, 3)).actions()
              if not action.isSeparator()]
    assert labels[:9] == ["Rotate 2 Pages Left", "Rotate 2 Pages Right",
                          "Reverse 2 Pages", "Insert Blank Page",
                          "Insert PDF File…", "Duplicate 2 Pages", "Extract 2 Pages…",
                          "Rotate All Pages Left", "Rotate All Pages Right"]
