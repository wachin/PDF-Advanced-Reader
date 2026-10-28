"""F10-3o: a stamp PDFAR draws itself — a typed caption or an image file.

The standard fourteen (F10-3n) get their appearance from ``/Name``.  A custom
stamp cannot: the caption is the user's text and the picture is the user's
file, so the edit writes the appearance itself as a form XObject in the
annotation's ``/AP/N`` and names the mark ``/Custom``.  Everything else — the
box, the click, undo, the comments panel — is the code F10-3n already has.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QFileDialog, QInputDialog, QMessageBox

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    AddCustomStamp,
    AnnotationManager,
    AnnotationType,
    StampAnnotation,
)
from pdfar.edits import EditError, Scope
from pdfar.save import save_document

RED = (255, 0, 0)


def make_image(path, width=16, height=8):
    """An RGBA test image: the left half red, the right half transparent."""
    raw = bytearray()
    for _y in range(height):
        for x in range(width):
            raw += (bytes((255, 0, 0, 255)) if x < width // 2
                    else bytes((0, 0, 0, 0)))
    fitz.Pixmap(fitz.csRGB, width, height, bytes(raw), 1).save(str(path))
    return str(path)


def place(manager, page=0, center=(100.0, 50.0), **definition):
    return manager.add_custom_stamp(page, center, **definition)


def custom_stamps(doc, page=0):
    """Every stamp on *page* with the keys the file holds for it."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_STAMP:
            continue
        rect = annot.rect
        out.append({
            "id": annot.info.get("id"),
            "name": annot.info.get("name"),
            "content": annot.info.get("content"),
            "ap": doc.xref_get_key(annot.xref, "AP"),
            "rect": (round(rect.x0, 2), round(rect.y0, 2),
                     round(rect.x1, 2), round(rect.y1, 2)),
        })
    return out


def changed_pixels(bare, painted):
    return sum(1 for y in range(painted.height) for x in range(painted.width)
               if bare.pixel(x, y)[:3] != painted.pixel(x, y)[:3])


@pytest.fixture
def image_path(tmp_path):
    return make_image(tmp_path / "mark.png")


@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


# ------------------------------------------------------- the document is the store
def test_a_text_stamp_is_a_real_stamp_with_its_own_appearance(manager):
    made = place(manager, caption="PAID 2026-10-27")

    assert isinstance(made, StampAnnotation)
    assert made.type is AnnotationType.STAMP
    assert made.name == "Custom"
    assert made.content == "PAID 2026-10-27"
    held = custom_stamps(manager.doc)
    assert len(held) == 1
    assert held[0]["name"] == "Custom"
    assert held[0]["content"] == "PAID 2026-10-27"
    assert held[0]["id"] == made.id
    kind, value = held[0]["ap"]
    assert kind == "dict" and "/N" in value
    assert manager.session.history.undo_label == "Custom stamp"


def test_the_caption_is_drawn(manager):
    place(manager, caption="PAID")

    page = manager.doc.load_page(0)
    clip = fitz.Rect(10.0, 10.0, 190.0, 90.0)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    painted = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    assert changed_pixels(bare, painted) > 200


def test_a_text_stamp_lands_where_it_was_clicked(manager):
    made = place(manager, caption="DRAFT", center=(100.0, 50.0))

    x0, y0, x1, y1 = made.rects[0]
    assert (x0 + x1) / 2 == pytest.approx(100.0, abs=1.0)
    assert (y0 + y1) / 2 == pytest.approx(50.0, abs=1.0)
    assert 0.0 <= x0 < x1 <= 200.0, (x0, x1)
    assert 0.0 <= y0 < y1 <= 100.0, (y0, y1)


@pytest.mark.parametrize("center", [(2.0, 2.0), (198.0, 98.0), (198.0, 2.0)])
def test_a_text_stamp_at_the_edge_is_pulled_inside(manager, center):
    made = place(manager, caption="DRAFT", center=center)

    x0, y0, x1, y1 = made.rects[0]
    assert 0.0 <= x0 < x1 <= 200.0, (x0, x1)
    assert 0.0 <= y0 < y1 <= 100.0, (y0, y1)


def test_a_long_caption_shrinks_instead_of_hanging_off(manager):
    made = place(manager, caption="CONFIDENTIAL - REVIEW")

    x0, y0, x1, y1 = made.rects[0]
    assert 0.0 <= x0 and x1 <= 200.0, (x0, x1)
    assert x1 - x0 <= 160.5, x1 - x0


def test_an_image_stamp_keeps_its_own_aspect(manager, image_path):
    made = place(manager, page=1, image=image_path)

    x0, y0, x1, y1 = made.rects[0]
    assert (x1 - x0) / (y1 - y0) == pytest.approx(2.0, abs=0.05)
    assert x1 - x0 <= 120.5, x1 - x0
    held = custom_stamps(manager.doc, page=1)
    assert held[0]["name"] == "Custom"
    assert held[0]["content"] == ""


def test_an_image_stamp_shows_the_image_and_keeps_its_transparency(
        manager, image_path):
    made = place(manager, page=1, image=image_path)

    page = manager.doc.load_page(1)
    clip = fitz.Rect(0.0, 0.0, 200.0, 100.0)
    bare = page.get_pixmap(matrix=fitz.Matrix(4, 4), annots=False, clip=clip)
    shown = page.get_pixmap(matrix=fitz.Matrix(4, 4), annots=True, clip=clip)
    x0, y0, x1, y1 = made.rects[0]
    left = (round((x0 + (x1 - x0) * 0.25) * 4),
            round((y0 + (y1 - y0) * 0.5) * 4))
    right = (round((x0 + (x1 - x0) * 0.75) * 4),
             round((y0 + (y1 - y0) * 0.5) * 4))
    assert shown.pixel(*left)[:3] == RED
    # The right half of the file is clear, so the page shows through it.
    assert shown.pixel(*right) == bare.pixel(*right)


def test_an_image_stamp_at_the_edge_is_pulled_inside(manager, image_path):
    made = place(manager, page=1, center=(2.0, 2.0), image=image_path)

    x0, y0, x1, y1 = made.rects[0]
    assert 0.0 <= x0 < x1 <= 200.0, (x0, x1)
    assert 0.0 <= y0 < y1 <= 100.0, (y0, y1)


def test_an_empty_definition_is_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert place(manager, caption="   ") is None
    assert manager.session.version == 0
    assert manager.session.dirty is False
    assert len(manager.session.history) == 0
    assert failures and "caption or an image" in failures[0]


def test_add_custom_stamp_refuses_an_empty_definition_directly(manager):
    edit = AddCustomStamp(ann_id="x", page=0)

    with pytest.raises(EditError):
        edit.apply(manager.doc)
    assert edit.scope == Scope.COMMENTS
    assert edit.label == "Custom stamp"


def test_an_unreadable_image_is_refused(tmp_path, manager):
    bad = tmp_path / "not-an-image.txt"
    bad.write_text("this is not a picture")
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert place(manager, image=str(bad)) is None
    assert manager.session.version == 0
    assert failures and "not an image" in failures[0]


def test_a_missing_image_is_refused(tmp_path, manager):
    assert place(manager, image=str(tmp_path / "gone.png")) is None
    assert manager.session.version == 0


def test_a_caption_the_font_cannot_draw_is_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert place(manager, caption="发票") is None
    assert manager.session.version == 0
    assert failures and "cannot draw" in failures[0]


def test_a_caption_too_long_for_the_page_is_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert place(manager, caption="W" * 300) is None
    assert manager.session.version == 0
    assert failures and "too long" in failures[0]


def test_one_undo_removes_the_custom_stamp(manager):
    made = place(manager, caption="PAID")

    manager.session.undo()
    manager.reload_from_document()
    assert custom_stamps(manager.doc) == []

    manager.session.redo()
    manager.reload_from_document()
    back = manager.get_annotation(made.id)
    assert isinstance(back, StampAnnotation)
    assert back.content == "PAID"


def test_a_custom_stamp_survives_save_and_reopen(tmp_path, manager, image_path):
    text = place(manager, caption="PAID")
    picture = place(manager, page=1, image=image_path)
    path = str(tmp_path / "custom.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        back = listed.get_annotation(text.id)
        assert isinstance(back, StampAnnotation)
        assert back.name == "Custom"
        assert back.content == "PAID"
        assert back.rects[0] == pytest.approx(text.rects[0], abs=0.1)
        held = custom_stamps(session.doc, page=1)
        assert held[0]["id"] == picture.id
        page = session.doc.load_page(1)
        clip = fitz.Rect(0.0, 0.0, 200.0, 100.0)
        bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False,
                               clip=clip)
        shown = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True,
                                clip=clip)
        assert changed_pixels(bare, shown) > 1000
    finally:
        session.close()


def test_a_custom_text_stamp_lists_what_it_says(manager):
    made = place(manager, caption="PAID 2026-10-27")

    listed = manager.get_annotations(page=0, ann_type=AnnotationType.STAMP)
    assert listed == [made]
    assert listed[0].has_note()
    assert listed[0].content == "PAID 2026-10-27"


def test_a_custom_stamp_takes_a_note_and_keeps_its_appearance(
        manager, image_path):
    made = place(manager, page=1, image=image_path)

    noted = manager.set_note(made.id, "checked by hand")
    assert noted is not None and noted.content == "checked by hand"
    page = manager.doc.load_page(1)
    clip = fitz.Rect(0.0, 0.0, 200.0, 100.0)
    bare = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=False, clip=clip)
    shown = page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=True, clip=clip)
    assert changed_pixels(bare, shown) > 1000
    assert manager.remove_annotation(made.id) is True


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


def widget_pos_for(view, page, x, y):
    from pdfar.geometry import map_point
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom
                        - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom
                        - view.verticalScrollBar().value()))


def click(view, spot):
    for kind, buttons in ((QEvent.Type.MouseButtonPress,
                           Qt.MouseButton.LeftButton),
                          (QEvent.Type.MouseButtonRelease,
                           Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, QPointF(spot),
                            QPointF(view.viewport().mapToGlobal(spot)),
                            Qt.MouseButton.LeftButton, buttons,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
    QApplication.processEvents()


def test_custom_text_arms_the_tool(qapp, win, view):
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})
    qapp.processEvents()

    assert view.tool_mode == "stamp"
    assert view.stamp_name == "Custom"
    assert view.custom_stamp == {"kind": "text", "caption": "PAID",
                                 "image": ""}
    assert win.act_stamp_custom_text.isChecked()
    assert win.stamp_split.defaultAction() is win.act_stamp_custom_text
    assert "PAID" in win.act_stamp_custom_text.toolTip()


def test_a_click_places_the_custom_text(qapp, win, view):
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    held = custom_stamps(view.annotation_manager.doc)
    assert len(held) == 1
    assert held[0]["name"] == "Custom"
    assert held[0]["content"] == "PAID"
    assert len(view.annotation_manager.session.history) == 1


def test_a_click_places_the_custom_image(qapp, win, view, image_path):
    win._set_custom_stamp({"kind": "image", "caption": "", "image": image_path})

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    held = custom_stamps(view.annotation_manager.doc)
    assert len(held) == 1
    assert held[0]["name"] == "Custom"
    x0, y0, x1, y1 = held[0]["rect"]
    assert (x1 - x0) / (y1 - y0) == pytest.approx(2.0, abs=0.05)


def test_choosing_a_standard_stamp_clears_the_custom_one(qapp, win, view):
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})
    win._stamp_actions["Approved"].trigger()
    qapp.processEvents()

    assert view.custom_stamp is None
    assert view.stamp_name == "Approved"
    click(view, widget_pos_for(view, 0, 100.0, 50.0))
    assert custom_stamps(view.annotation_manager.doc)[0]["name"] == "Approved"


def test_the_custom_stamp_lands_where_the_click_went(qapp, win, view):
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})
    view.set_rotation(90)
    qapp.processEvents()

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    x0, y0, x1, y1 = custom_stamps(view.annotation_manager.doc)[0]["rect"]
    assert (x0 + x1) / 2 == pytest.approx(100.0, abs=2.5)
    assert (y0 + y1) / 2 == pytest.approx(50.0, abs=2.5)
    view.set_rotation(0)


def test_the_custom_stamp_is_off_in_reflow(qapp, win, view):
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})
    win.act_reflow.trigger()
    qapp.processEvents()

    assert win.act_stamp_custom_text.isEnabled() is False
    assert win.act_stamp_custom_image.isEnabled() is False
    assert view.tool_mode != "stamp"


def test_typing_a_caption_arms_the_tool(qapp, win, view, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("REVIEWED", True)))

    win.act_stamp_custom_text.trigger()
    qapp.processEvents()

    assert win._custom_stamp == {"kind": "text", "caption": "REVIEWED",
                                 "image": ""}
    assert win.act_stamp_custom_text.isChecked()
    click(view, widget_pos_for(view, 0, 100.0, 50.0))
    assert custom_stamps(view.annotation_manager.doc)[0]["content"] == "REVIEWED"


def test_picking_an_image_arms_the_tool(qapp, win, view, monkeypatch,
                                        image_path):
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (image_path, "")))

    win.act_stamp_custom_image.trigger()
    qapp.processEvents()

    assert win._custom_stamp["kind"] == "image"
    assert win._custom_stamp["image"] == image_path
    assert image_path.split(os.sep)[-1] in win.act_stamp_custom_image.toolTip()


def test_cancelling_the_caption_keeps_the_previous_stamp(qapp, win, view,
                                                         monkeypatch):
    win._stamp_actions["Approved"].trigger()
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))

    win.act_stamp_custom_text.trigger()
    qapp.processEvents()

    assert win.act_stamp_approved.isChecked()
    assert win.act_stamp_custom_text.isChecked() is False
    assert win.stamp_split.defaultAction() is win.act_stamp_approved
    click(view, widget_pos_for(view, 0, 100.0, 50.0))
    assert custom_stamps(view.annotation_manager.doc)[0]["name"] == "Approved"


def test_an_unreadable_image_is_not_armed(qapp, win, view, monkeypatch,
                                          tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_text("nope")
    previous = win._stamp_actions["Approved"]
    win._stamp_actions["Approved"].trigger()
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bad), "")))
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

    win.act_stamp_custom_image.trigger()
    qapp.processEvents()

    assert win._custom_stamp is None
    assert win.stamp_split.defaultAction() is previous
    assert previous.isChecked()


def test_each_tab_keeps_its_own_custom_stamp(qapp, win, tmp_path, image_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    win._set_custom_stamp({"kind": "text", "caption": "PAID", "image": ""})
    view_a = win.view

    assert win.open_document(second)
    qapp.processEvents()
    win._set_custom_stamp({"kind": "image", "caption": "", "image": image_path})
    view_b = win.view

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view is view_a
    assert view_a.custom_stamp["caption"] == "PAID"
    assert win.stamp_split.defaultAction() is win.act_stamp_custom_text
    assert "PAID" in win.act_stamp_custom_text.toolTip()

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.view is view_b
    assert view_b.custom_stamp["image"] == image_path
    assert win.stamp_split.defaultAction() is win.act_stamp_custom_image
