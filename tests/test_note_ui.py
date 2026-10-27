"""F10-3e: placing and opening a note from the page.

F10-3d made a note a real ``/Text`` annotation; nothing but a test could make
one.  Here the view gains the missing half: a right-click on a page asks for a
note at the point under the cursor, and a right-click on a note's icon opens or
deletes that note.  The sidebar, which already lists notes, offers the same
actions on its rows.

The geometry is the part worth pinning down: a note's position is a PDF point,
while a click is a widget coordinate, and the two are separated by scroll bars,
zoom, view rotation and the page layout.  These tests walk the same conversion
the page-number preview uses, in both directions.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from conftest import build_sample_pdf
from pdfar.annotations import AnnotationType
from pdfar.geometry import map_point

#: The sample page is 200x100; this keeps the whole icon inside it.
PDF_POINT = (60.0, 40.0)


@pytest.fixture
def win(qapp):
    from pdfar.main import MainWindow
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def open_tab(qapp, win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2, width=200, height=100))
    assert win.open_document(path)
    qapp.processEvents()
    return win._active_tab()


@pytest.fixture
def view(qapp, open_tab):
    v = open_tab.view
    qapp.processEvents()
    return v


@pytest.fixture
def answer(qapp, monkeypatch):
    """Answer the note dialog without showing it; ``None`` stands for Cancel."""
    asked = {"text": ""}

    def get_multi_line_text(parent, title, label, current, *args, **kwargs):
        asked["title"] = title
        asked["label"] = label
        asked["current"] = current
        qapp.processEvents()
        return (None, False) if asked["reply"] is None else (asked["reply"], True)

    monkeypatch.setattr(QInputDialog, "getMultiLineText",
                        staticmethod(get_multi_line_text))

    def reply(text):
        asked["reply"] = text
        return asked

    return reply


@pytest.fixture
def dialogs(monkeypatch):
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))


def widget_pos_for(view, page, x, y):
    """Where a PDF point is on the screen, as the view would have to be clicked."""
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom
                        - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom
                        - view.verticalScrollBar().value()))


def text_notes(doc, page=0):
    """The ``/Text`` annotations on *page*, as ``(x0, y0, content)`` triples.

    A PyMuPDF ``Annot`` is only valid while its ``Page`` object lives, so
    everything worth reporting is read here, before the page goes away.
    """
    p = doc.load_page(page)
    return [(a.rect.x0, a.rect.y0, a.info.get("content"))
            for a in p.annots() if a.type[0] == fitz.PDF_ANNOT_TEXT]


def menu_texts(menu):
    return [a.text() for a in menu.actions() if not a.isSeparator()]


# ------------------------------------------------------------------- the click lands
def test_a_point_on_the_page_becomes_a_pdf_point(view):
    """The conversion a note is placed with, at zoom and under rotation."""
    for zoom, rotation in ((1.0, 0), (2.5, 0), (1.3, 90), (2.0, 180), (0.8, 270)):
        view.set_zoom(zoom)
        view.set_rotation(rotation)
        pos = widget_pos_for(view, 0, *PDF_POINT)
        hit = view._pdf_point_at(pos)
        assert hit is not None and hit == pytest.approx((0, *PDF_POINT), abs=1.0), \
            f"zoom {zoom} rotation {rotation}"
    view.set_rotation(0)


def test_a_point_off_the_page_is_on_no_page(view):
    """The margin below the last page is not a page, and gets no note."""
    view.set_zoom(1.0)
    view.set_rotation(0)

    assert view._pdf_point_at(QPoint(0, 100000)) is None


def test_a_note_is_placed_where_the_user_clicked(view, answer, qapp):
    answer("check the figures")
    session = view.session

    assert view.add_note_at(0, *PDF_POINT) is True
    qapp.processEvents()

    held = text_notes(session.doc)
    assert len(held) == 1
    assert held[0][:2] == pytest.approx(PDF_POINT)
    assert held[0][2] == "check the figures"
    assert session.dirty is True
    assert session.history.undo_label == "Add note"
    listed = view.annotation_manager.get_annotations(ann_type=AnnotationType.NOTE)
    assert [n.content for n in listed] == ["check the figures"]


def test_a_note_is_placed_on_the_page_under_the_cursor(view, answer, qapp):
    """Two pages down, the second one gets it — not the first, not the gap."""
    answer("page two")
    pos = widget_pos_for(view, 1, 40.0, 30.0)
    page, x, y = view._pdf_point_at(pos)

    assert view.add_note_at(page, x, y) is True
    qapp.processEvents()

    assert text_notes(view.session.doc, 0) == []
    assert len(text_notes(view.session.doc, 1)) == 1


def test_cancelling_the_dialog_places_nothing(view, answer, qapp):
    answer(None)
    session = view.session
    version = session.version

    assert view.add_note_at(0, *PDF_POINT) is False
    qapp.processEvents()

    assert session.version == version
    assert session.dirty is False
    assert text_notes(session.doc) == []


def test_an_empty_answer_still_places_the_icon(view, answer, qapp):
    """The user asked for a note, so OK means OK even with no text typed.

    MuPDF gives the annotation an appearance stream anyway, so the icon is there
    to click again.
    """
    answer("   ")

    assert view.add_note_at(0, *PDF_POINT) is True
    qapp.processEvents()

    assert len(text_notes(view.session.doc)) == 1
    assert view.annotation_manager.get_notes_for_page(0)[0].content == ""


def test_the_dialog_says_which_page_the_note_goes_on(view, answer, qapp):
    asked = answer("on the second page")
    view.add_note_at(1, *PDF_POINT)

    assert asked["title"] == "Add Note"
    assert "page 2" in asked["label"]


# ---------------------------------------------------------------- opening one again
def test_the_icon_is_a_target_and_the_page_around_it_is_not(view, answer, qapp):
    answer("here")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]

    inside = view._note_at(0, PDF_POINT[0] + 4.0, PDF_POINT[1] + 4.0)
    outside = view._note_at(0, PDF_POINT[0] + 80.0, PDF_POINT[1] + 40.0)

    assert inside is not None and inside.id == note.id
    assert outside is None
    # A click is rounded to a widget pixel, so the icon's edge is a hair
    # wherever the user aims; a point off the corner is still the same icon.
    assert view._note_at(0, PDF_POINT[0] - 1.0, PDF_POINT[1] - 1.0) is not None


def test_the_right_click_menu_does_what_it_offers(view, answer, qapp):
    answer("first")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]
    hit = (0, *PDF_POINT)

    assert menu_texts(view._note_menu(hit, note)) == ["Edit Note…", "Delete Note"]
    assert menu_texts(view._note_menu(hit)) == ["Add note here…"]

    # Each menu carries the point and the annotation it was built for, not
    # whatever the cursor happens to be over when the action fires.
    answer("second")
    view._note_menu(hit).actions()[0].trigger()
    qapp.processEvents()
    contents = [n.content for n in view.annotation_manager.get_notes_for_page(0)]
    assert contents == ["first", "second"]

    view._note_menu(hit, note).actions()[1].trigger()
    qapp.processEvents()
    contents = [n.content for n in view.annotation_manager.get_notes_for_page(0)]
    assert contents == ["second"]


def test_editing_a_note_from_the_page_is_undoable(view, answer, qapp):
    answer("written once")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]
    session = view.session

    answer("written twice")
    assert view.edit_note(note.id) is True
    qapp.processEvents()

    assert text_notes(session.doc)[0][2] == "written twice"
    assert session.history.undo_label == "Edit note"
    session.undo()
    qapp.processEvents()
    assert text_notes(session.doc)[0][2] == "written once"


def test_editing_a_note_offers_its_current_text(view, answer, qapp):
    answer("what it says")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]
    asked = answer("edited")

    view.edit_note(note.id)

    assert asked["title"] == "Edit Note"
    assert asked["current"] == "what it says"


def test_deleting_a_note_takes_the_icon_with_it(view, answer, qapp):
    answer("to remove")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]

    assert view.delete_note(note.id) is True
    qapp.processEvents()

    assert text_notes(view.session.doc) == []
    assert view.session.history.undo_label == "Delete annotation"


def test_a_note_that_is_gone_says_why(view, qapp, dialogs):
    """A silent no is not an answer: the row can be stale after an undo."""
    seen = []
    view.transient_message.connect(seen.append)

    assert view.edit_note("never-here") is False
    qapp.processEvents()

    assert seen and "not in this document" in seen[-1]


# ------------------------------------------------------------- the sidebar agrees
def test_a_note_row_offers_the_note_actions_and_not_a_color(view, open_tab,
                                                            answer, qapp):
    answer("sidebar note")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]

    texts = menu_texts(open_tab.sidebar._annotation_menu(note.id, note.type.value))

    assert "Delete Annotation" in texts
    assert "Edit Note…" in texts
    assert "Change Color" not in texts


def test_a_highlight_row_still_offers_its_color(view, open_tab, qapp):
    """The menu refactor must not cost the highlight the thing it is for."""
    highlight = view.annotation_manager.add_highlight(0, [(20.0, 20.0, 60.0, 30.0)])
    qapp.processEvents()

    assert "Change Color" in menu_texts(
        open_tab.sidebar._annotation_menu(highlight.id, highlight.type.value))


def test_a_note_row_offers_to_add_text_to_an_empty_note(view, open_tab,
                                                        answer, qapp):
    answer("   ")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    note = view.annotation_manager.get_notes_for_page(0)[0]

    assert "Add Note…" in menu_texts(
        open_tab.sidebar._annotation_menu(note.id, note.type.value))


def test_a_note_row_shows_its_text(view, open_tab, answer, qapp):
    answer("a row label")
    view.add_note_at(0, *PDF_POINT)
    qapp.processEvents()
    tree = open_tab.sidebar.annotations_tree

    rows = [tree.topLevelItem(i).child(j).text(0)
            for i in range(tree.topLevelItemCount())
            for j in range(tree.topLevelItem(i).childCount())]

    assert any("Note" in r and "a row label" in r for r in rows), rows
