"""F10-3k: the pen's colour and thickness.

F10-3j could draw, but with one fixed ink.  These are the pickers: a colour
menu beside the tool (the palette plus a dialog for anything else) and a
thickness combo, both mirrored from the active tab's view so each document
keeps the pen it was given.

What is worth pinning down is the boundary: picking a style is viewer state,
not a document edit — it must not dirty the file or add a history step — and
the choices only become PDF content when a stroke is committed.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QMouseEvent
from PyQt6.QtWidgets import QApplication

from conftest import build_sample_pdf
from pdfar.annotations import INK_COLOR, INK_COLORS, INK_WIDTH
from pdfar.geometry import map_point

DRAG = ((30.0, 20.0), (60.0, 60.0), (90.0, 25.0))
BLUE = dict(INK_COLORS)["Blue"]
RED = dict(INK_COLORS)["Red"]


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


def choose_draw(win):
    win.act_draw.trigger()
    QApplication.processEvents()


def color_action(win, name):
    return next(a for a in win._pen_color_actions if a.text() == name)


def widget_pos_for(view, page, x, y):
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom - view.verticalScrollBar().value()))


def mouse(view, kind, spot, held):
    vp = view.viewport()
    press = (Qt.MouseButton.LeftButton
             if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton)
    event = QMouseEvent(kind, QPointF(spot), QPointF(vp.mapToGlobal(spot)), press,
                        Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, event)


def drag(view, page, points):
    spots = [widget_pos_for(view, page, x, y) for x, y in points]
    mouse(view, QEvent.Type.MouseButtonPress, spots[0], True)
    for spot in spots[1:]:
        mouse(view, QEvent.Type.MouseMove, spot, True)
    mouse(view, QEvent.Type.MouseButtonRelease, spots[-1], False)
    QApplication.processEvents()


def ink_on(doc, page=0):
    """The stroke's colour and width, read while its page is still alive."""
    sheet = doc.load_page(page)
    for annot in sheet.annots():
        if annot.type[0] == fitz.PDF_ANNOT_INK:
            return {"stroke": [round(c * 255) for c in annot.colors["stroke"]],
                    "width": annot.border["width"]}
    return None


# ------------------------------------------------------------- where they live
def test_the_pickers_show_only_with_the_pen(qapp, win, view):
    assert win.pen_options.isVisible() is False      # the text tool is active
    choose_draw(win)
    assert win.pen_options.isVisible()
    win.act_sel_text.trigger()
    qapp.processEvents()
    assert win.pen_options.isVisible() is False


def test_closing_the_document_takes_the_pickers_with_it(qapp, win, view):
    choose_draw(win)
    assert win.pen_options.isVisible()

    win._close_all_tabs(confirm=False)
    qapp.processEvents()

    assert win.pen_options.isVisible() is False


def test_the_palette_is_the_ink_palette(win, view):
    entries = [a for a in win.pen_color_menu.actions() if not a.isSeparator()]
    assert [a.text() for a in entries] == [name for name, _ in INK_COLORS] + [
        "Custom…"]


# --------------------------------------------------------------- picking a style
def test_choosing_a_colour_sets_the_pen(qapp, win, view):
    choose_draw(win)
    action = color_action(win, "Blue")

    action.trigger()
    qapp.processEvents()

    assert view.ink_color == BLUE
    assert action.isChecked()
    assert not any(a.isChecked() for a in win._pen_color_actions
                   if a is not action)
    assert BLUE in win.pen_color_button.toolTip()


def test_a_custom_colour_comes_from_the_dialog(qapp, win, view, monkeypatch):
    monkeypatch.setattr(
        "pdfar.main.QColorDialog.getColor",
        staticmethod(lambda *args, **kwargs: QColor("#123456")))

    win.pen_custom_color.trigger()
    qapp.processEvents()

    assert view.ink_color == "#123456"
    assert not any(a.isChecked() for a in win._pen_color_actions)
    assert "#123456" in win.pen_color_button.toolTip()


def test_cancelling_the_dialog_keeps_the_pen(qapp, win, view, monkeypatch):
    monkeypatch.setattr(
        "pdfar.main.QColorDialog.getColor",
        staticmethod(lambda *args, **kwargs: QColor()))

    win.pen_custom_color.trigger()
    qapp.processEvents()

    assert view.ink_color == INK_COLOR


def test_the_width_combo_sets_the_pen(qapp, win, view):
    assert win.pen_width_combo.currentData() == INK_WIDTH
    assert view.ink_width == INK_WIDTH

    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    qapp.processEvents()

    assert view.ink_width == 4.0


# ------------------------------------------------------- the style reaches the ink
def test_the_chosen_style_reaches_the_drawing(qapp, win, view):
    choose_draw(win)
    color_action(win, "Blue").trigger()
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    qapp.processEvents()

    drag(view, 0, DRAG)

    annot = ink_on(view.annotation_manager.doc)
    assert annot is not None
    assert annot["stroke"] == [int(BLUE[i:i + 2], 16) for i in (1, 3, 5)]
    assert annot["width"] == pytest.approx(4.0)


def test_the_stroke_in_progress_previews_the_chosen_colour(qapp, win, view):
    """The preview reads the view's ink, so a choice shows up before release."""
    choose_draw(win)
    color_action(win, "Red").trigger()
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    qapp.processEvents()

    spots = [widget_pos_for(view, 0, x, y) for x, y in DRAG]
    mouse(view, QEvent.Type.MouseButtonPress, spots[0], True)
    for spot in spots[1:]:
        mouse(view, QEvent.Type.MouseMove, spot, True)
    qapp.processEvents()

    image = view.viewport().grab().toImage()
    reds = sum(1 for y in range(image.height())
               for x in range(image.width())
               if image.pixelColor(x, y).red() > 150
               and image.pixelColor(x, y).green() < 110
               and image.pixelColor(x, y).blue() < 110)

    mouse(view, QEvent.Type.MouseButtonRelease, spots[-1], False)
    qapp.processEvents()

    assert reds > 10, "the preview is not painted in the chosen colour"


# -------------------------------------------------------------------- boundaries
def test_picking_a_style_is_not_a_document_edit(qapp, win, view):
    session = view.annotation_manager.session
    before = (session.version, session.dirty, len(session.history))

    choose_draw(win)
    color_action(win, "Green").trigger()
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(8.0))
    qapp.processEvents()

    assert (session.version, session.dirty, len(session.history)) == before


def test_the_highlight_colour_stays_its_own(qapp, win, view):
    view.set_highlight_color("#90EE90")

    win._set_ink_color(RED)

    assert view.ink_color == RED
    assert view.get_highlight_color() == "#90EE90"


def test_each_tab_keeps_its_own_pen(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    assert win.open_document(second)
    qapp.processEvents()
    assert win.tabs.count() == 2

    choose_draw(win)                      # the second document is active
    color_action(win, "Red").trigger()
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(8.0))
    qapp.processEvents()
    assert win.view.ink_color == RED

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view.ink_color == INK_COLOR
    assert win.view.ink_width == INK_WIDTH
    assert color_action(win, "Black").isChecked()
    assert win.pen_width_combo.currentData() == INK_WIDTH

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.view.ink_color == RED
    assert win.view.ink_width == 8.0
    assert color_action(win, "Red").isChecked()
    assert win.pen_width_combo.currentData() == 8.0
