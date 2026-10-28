"""F10-3n: stamps — the fourteen standard rubber stamps, placed on a page.

A stamp is PDF's /Stamp annotation with one of the names the specification
defines (``/Draft``, ``/Approved``, ...).  The appearance is the one MuPDF
writes, so every other reader shows the same rubber stamp; PDFAR stores the
name, the box and its own /NM, and nothing else.  What the tests pin down is
that the name survives a save, that the box stays inside the paper, that a
stamp is one edit and one Ctrl+Z, and that the tool is a click, not a drag.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    STAMP_LABELS,
    AddStamp,
    AnnotationManager,
    AnnotationType,
    StampAnnotation,
    stamp_box,
)
from pdfar.edits import EditError, Scope
from pdfar.save import save_document

RED = (1.0, 0.0, 0.0)
NAMES = tuple(STAMP_LABELS)


def stamps_on(doc, page=0):
    """The stamps on *page*, as the file holds them."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_STAMP:
            continue
        rect = annot.rect
        out.append({
            "id": annot.info.get("id"),
            "name": annot.info.get("name"),
            "content": annot.info.get("content"),
            "rect": (round(rect.x0, 2), round(rect.y0, 2),
                     round(rect.x1, 2), round(rect.y1, 2)),
            "color": [round(c, 3) for c in (annot.colors.get("stroke") or [])],
        })
    return out


@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


def place(manager, name="Draft", page=0, center=(100.0, 50.0)):
    return manager.add_stamp(page, name, center)


def widget_pos_for(view, page, x, y):
    """Where a page-space point is on the screen."""
    from pdfar.geometry import map_point
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom - view.verticalScrollBar().value()))


# ------------------------------------------------ the document is the store
def test_a_stamp_is_a_real_stamp_annotation(manager):
    made = place(manager, "Draft")

    assert isinstance(made, StampAnnotation)
    assert made.type is AnnotationType.STAMP
    assert made.name == "Draft"
    assert made.rects and len(made.rects) == 1
    assert made.content == ""
    held = stamps_on(manager.doc)
    assert len(held) == 1
    assert held[0]["name"] == "Draft"
    assert held[0]["id"] == made.id
    assert manager.session.history.undo_label == "Stamp"


@pytest.mark.parametrize("name", NAMES)
def test_every_standard_stamp_keeps_its_name(manager, name):
    """The menu label may have spaces; the file keeps the specification's key."""
    made = place(manager, name)

    assert made is not None
    assert made.name == name
    assert stamps_on(manager.doc)[0]["name"] == name
    assert STAMP_LABELS[name]


def test_a_stamp_lands_where_it_was_clicked(manager):
    """The click is the stamp's centre, and the box stays inside the paper."""
    made = place(manager, center=(100.0, 50.0))

    x0, y0, x1, y1 = made.rects[0]
    want = stamp_box(200.0, 100.0, (100.0, 50.0))
    assert (x0, y0, x1, y1) == pytest.approx(want, abs=0.5)
    assert (x0 + x1) / 2 == pytest.approx(100.0, abs=0.5)
    assert (y0 + y1) / 2 == pytest.approx(50.0, abs=0.5)
    assert (x1 - x0) / (y1 - y0) == pytest.approx(3.8, abs=0.05)


@pytest.mark.parametrize("center", [(2.0, 2.0), (198.0, 98.0), (198.0, 2.0)])
def test_a_stamp_at_the_edge_is_pulled_inside(manager, center):
    """A stamp hanging off the page is one nobody can select; clamp it."""
    made = place(manager, center=center)

    x0, y0, x1, y1 = made.rects[0]
    assert 0.0 <= x0 < x1 <= 200.0, (x0, x1)
    assert 0.0 <= y0 < y1 <= 100.0, (y0, y1)


def test_a_stamp_has_its_appearance(manager):
    place(manager, "Approved", center=(100.0, 50.0))

    page = manager.doc.load_page(0)
    clip = fitz.Rect(30.0, 30.0, 170.0, 70.0)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    painted = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    changed = sum(1 for y in range(painted.height) for x in range(painted.width)
                  if painted.pixel(x, y)[:3] != bare.pixel(x, y)[:3])
    assert changed > 100, "MuPDF's stamp appearance is not on the page"

    assert stamps_on(manager.doc)[0]["color"] == list(RED)


def test_an_unknown_stamp_is_refused(manager):
    """A name PDF does not define would make an annotation no viewer can name."""
    version = manager.session.version
    dirt = manager.session.dirty

    made = manager.add_stamp(0, "Friday", (100.0, 50.0))

    assert made is None
    assert stamps_on(manager.doc) == []
    assert (manager.session.version, manager.session.dirty) == (version, dirt)
    assert len(manager.session.history) == 0


def test_add_stamp_refuses_on_its_own():
    doc = fitz.open()
    doc.new_page(width=200, height=100)
    edit = AddStamp(ann_id="x", page=0, name="Friday", center=(50.0, 50.0))

    with pytest.raises(EditError):
        edit.apply(doc)
    assert edit.scope == Scope.COMMENTS
    doc.close()


def test_one_undo_removes_the_stamp(manager):
    made = place(manager, "Final")

    manager.session.undo()
    manager.reload_from_document()

    assert stamps_on(manager.doc) == []
    manager.session.redo()
    manager.reload_from_document()
    back = manager.get_annotation(made.id)
    assert isinstance(back, StampAnnotation)
    assert back.name == "Final"


def test_the_stamp_survives_save_and_reopen(tmp_path, manager):
    made = place(manager, "Confidential")
    path = str(tmp_path / "stamped.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        back = listed.get_annotation(made.id)
        assert isinstance(back, StampAnnotation)
        assert back.name == "Confidential"
        assert back.rects[0] == pytest.approx(made.rects[0], abs=0.1)
    finally:
        session.close()


def test_stamps_list_with_the_other_kinds(manager):
    shape = manager.add_shape(0, "rect", rect=(20.0, 20.0, 60.0, 50.0))
    stamp = place(manager, "Draft")

    kinds = {ann.type for ann in manager.get_annotations()}
    assert kinds == {AnnotationType.SHAPE, AnnotationType.STAMP}
    assert manager.get_annotations(ann_type=AnnotationType.STAMP) == [stamp]
    assert manager.get_notes_for_page(0) == []
    assert shape is not None


def test_a_stamp_takes_a_note_and_deletes(manager):
    made = place(manager, "Draft")

    noted = manager.set_note(made.id, "checked by hand")
    assert noted is not None
    assert noted.content == "checked by hand"
    assert manager.remove_annotation(made.id) is True
    assert stamps_on(manager.doc) == []


def test_a_foreign_stamp_is_listed_without_the_file_being_rewritten(manager):
    page = manager.doc.load_page(0)
    foreign = page.add_stamp_annot(fitz.Rect(20, 20, 120, 50), stamp=fitz.STAMP_Sold)
    foreign.update()
    manager.doc.xref_set_key(foreign.xref, "NM", "null")

    listed = AnnotationManager(manager.session)      # read from the top
    stamps = listed.get_annotations(ann_type=AnnotationType.STAMP)

    assert [s.name for s in stamps] == ["Sold"]
    assert stamps[0].id == f"anon:{foreign.xref}"
    assert listed.session.dirty is False
    assert manager.doc.xref_get_key(foreign.xref, "NM") == ("null", "null")


# --------------------------------------------------------------- the tool
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
def view(qapp, win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2,
                                width=200, height=100))
    assert win.open_document(path)
    qapp.processEvents()
    return win._active_tab().view


def choose(win, name):
    win._stamp_actions[name].trigger()
    QApplication.processEvents()


def click(view, spot):
    mouse = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(spot),
        QPointF(view.viewport().mapToGlobal(spot)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), mouse)
    mouse = QMouseEvent(
        QEvent.Type.MouseButtonRelease, QPointF(spot),
        QPointF(view.viewport().mapToGlobal(spot)),
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(view.viewport(), mouse)
    QApplication.processEvents()


def test_the_stamp_is_a_click_tool(qapp, win, view):
    choose(win, "Draft")

    assert view.tool_mode == "stamp"
    assert view.stamp_name == "Draft"
    assert win.act_stamp_draft.isChecked()
    assert win.stamp_split.defaultAction() is win.act_stamp_draft
    assert win.act_stamp_draft.toolTip().startswith("Stamp: ")


def test_the_menu_offers_every_standard_stamp(win, view):
    entries = [a.text() for a in win.stamp_split.menu().actions()]
    assert entries == [STAMP_LABELS[name] for name in NAMES]


def test_a_click_places_the_chosen_stamp(qapp, win, view):
    choose(win, "Confidential")

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    held = stamps_on(view.annotation_manager.doc)
    assert len(held) == 1
    assert held[0]["name"] == "Confidential"
    listed = view.annotation_manager.get_annotations(
        page=0, ann_type=AnnotationType.STAMP)
    assert len(listed) == 1
    assert listed[0].name == "Confidential"
    assert len(view.annotation_manager.session.history) == 1


def test_another_tool_places_no_stamp(qapp, view):
    view.set_tool_mode("text")

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    assert stamps_on(view.annotation_manager.doc) == []


def test_a_click_off_the_page_places_nothing(qapp, view):
    view.set_tool_mode("stamp")
    origin_x, origin_y = view._page_origin(0)
    away = QPoint(max(0, round(origin_x) - 40), round(origin_y + 40))

    click(view, away)

    assert stamps_on(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_stamp_lands_where_the_click_went(qapp, view, rotation):
    """The click is captured in page space, so a turned view does not move it."""
    view.set_tool_mode("stamp")
    view.set_rotation(rotation)
    qapp.processEvents()

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    held = stamps_on(view.annotation_manager.doc)
    assert len(held) == 1
    x0, y0, x1, y1 = held[0]["rect"]
    assert (x0 + x1) / 2 == pytest.approx(100.0, abs=2.5)
    assert (y0 + y1) / 2 == pytest.approx(50.0, abs=2.5)


def test_the_stamp_is_off_in_reflow(qapp, win, view):
    choose(win, "Draft")
    win.act_reflow.trigger()
    qapp.processEvents()

    assert win.act_stamp_draft.isEnabled() is False
    assert view.tool_mode != "stamp"


def test_each_tab_keeps_its_own_stamp(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    choose(win, "Approved")
    assert win.open_document(second)
    qapp.processEvents()

    choose(win, "TopSecret")
    assert win.view.stamp_name == "TopSecret"

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view.stamp_name == "Approved"
    assert win.act_stamp_approved.isChecked()
    assert win.stamp_split.defaultAction() is win.act_stamp_approved

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.view.stamp_name == "TopSecret"
    assert win.stamp_split.defaultAction() is win.act_stamp_topsecret
