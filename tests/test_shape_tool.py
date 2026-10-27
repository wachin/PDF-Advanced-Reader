"""F10-3m: the shape tool — dragging a shape onto the page.

F10-3l made five shapes real annotations that nothing in the interface could
reach.  This is the mouse half: a Shapes button (a menu of the five kinds), a
drag that rubber-bands the geometry, a preview that shows the shape under the
pointer, and one ``AddShape`` edit when the button comes up.

The rules are the pen's (F10-3j): capture in page space, one drag is one
Ctrl+Z, Escape or a tool change cancels, and reflow has no page to draw on.
What is new is that the drag means different geometry per kind — a box for a
rectangle or ellipse, two endpoints for a line or arrow, a traced (and then
simplified) outline for a polygon.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from conftest import build_sample_pdf
from pdfar.annotations import AnnotationType, ShapeAnnotation
from pdfar.geometry import map_point

#: The sample page is 200x100.
BOX = ((20.0, 20.0), (120.0, 80.0))
DIAGONAL = ((20.0, 80.0), (120.0, 20.0))
TRIANGLE = ((20.0, 80.0), (70.0, 20.0), (120.0, 80.0))

#: Per kind: the page-space drag and the subtype the file has to hold.
KINDS = {
    "rect": (BOX, fitz.PDF_ANNOT_SQUARE),
    "ellipse": (BOX, fitz.PDF_ANNOT_CIRCLE),
    "line": (DIAGONAL, fitz.PDF_ANNOT_LINE),
    "arrow": (DIAGONAL, fitz.PDF_ANNOT_LINE),
    "polygon": (TRIANGLE, fitz.PDF_ANNOT_POLYGON),
}


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
    tab = win._active_tab()
    tab.view.set_tool_mode("shape")
    qapp.processEvents()
    return tab.view


def choose(win, kind):
    """Pick *kind* from the Shapes menu, as clicking its entry does."""
    win._shape_actions[kind].trigger()
    QApplication.processEvents()


def widget_pos_for(view, page, x, y):
    """Where a page-space point is on the screen."""
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom - view.verticalScrollBar().value()))


def shapes(doc, page=0):
    """The shape annotations on *page*, as the file holds them."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] not in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE,
                                 fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_POLYGON):
            continue
        rect = annot.rect
        out.append({
            "id": annot.info.get("id"),
            "subtype": annot.type[0],
            "points": [(round(x, 1), round(y, 1)) for x, y in annot.vertices]
                      if annot.vertices else [],
            "line_ends": tuple(annot.line_ends) if annot.line_ends else (0, 0),
            "color": [round(c, 2) for c in (annot.colors.get("stroke") or [])],
            "width": annot.border.get("width"),
            "rect": (rect.x0, rect.y0, rect.x1, rect.y1),
        })
    return out


def mouse(view, kind, spot, buttons):
    """Send one mouse event with the button state the drag really has."""
    vp = view.viewport()
    held = Qt.MouseButton.LeftButton if buttons else Qt.MouseButton.NoButton
    press = (Qt.MouseButton.LeftButton
             if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton)
    event = QMouseEvent(kind, QPointF(spot), QPointF(vp.mapToGlobal(spot)), press,
                        held, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, event)


def press(view, spot):
    mouse(view, QEvent.Type.MouseButtonPress, spot, True)


def move(view, spot):
    mouse(view, QEvent.Type.MouseMove, spot, True)


def release(view, spot):
    mouse(view, QEvent.Type.MouseButtonRelease, spot, False)


def press_move_release(view, spots):
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    release(view, spots[-1])
    QApplication.processEvents()


def drag(view, page, points):
    """Drag through page-space *points*, converting each to the screen."""
    press_move_release(view, [widget_pos_for(view, page, x, y)
                              for x, y in points])


def near(actual, expected, tol=2.0):
    """Every expected point has a captured point within *tol* of it."""
    for want in expected:
        assert any(abs(x - want[0]) <= tol and abs(y - want[1]) <= tol
                   for x, y in actual), (want, actual)
    return True


# -------------------------------------------------------------------- the tool
@pytest.mark.parametrize("kind", KINDS)
def test_each_shape_is_the_same_tool_with_a_kind(qapp, win, view, kind):
    """Choosing from the menu is a mode change plus one attribute, not a widget."""
    choose(win, kind)

    assert view.tool_mode == "shape"
    assert view.shape_kind == kind
    assert win._shape_actions[kind].isChecked()
    assert win.shape_split.defaultAction() is win._shape_actions[kind]
    label = {"rect": "Rectangle", "ellipse": "Ellipse", "line": "Line",
             "arrow": "Arrow", "polygon": "Polygon"}[kind]
    assert win.shape_split.defaultAction().toolTip().startswith(f"{label}: ")


def test_the_menu_offers_all_five_kinds(win, view):
    entries = [a.text() for a in win.shape_split.menu().actions()]
    assert entries == ["Rectangle", "Ellipse", "Line", "Arrow", "Polygon"]


def test_a_drag_with_another_tool_draws_no_shape(view):
    view.set_tool_mode("text")

    drag(view, 0, BOX)

    assert shapes(view.annotation_manager.doc) == []


# ---------------------------------------------------------- one drag, one shape
@pytest.mark.parametrize("kind", KINDS)
def test_a_drag_draws_the_chosen_shape(qapp, win, view, kind):
    points, subtype = KINDS[kind]
    choose(win, kind)

    drag(view, 0, points)

    held = shapes(view.annotation_manager.doc)
    assert len(held) == 1
    assert held[0]["subtype"] == subtype
    listed = view.annotation_manager.get_annotations(
        page=0, ann_type=AnnotationType.SHAPE)
    assert len(listed) == 1
    assert isinstance(listed[0], ShapeAnnotation)
    assert listed[0].kind == kind
    assert len(view.annotation_manager.session.history) == 1


def test_the_shape_is_not_written_before_release(qapp, view):
    spots = [widget_pos_for(view, 0, x, y) for x, y in BOX]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    QApplication.processEvents()

    assert shapes(view.annotation_manager.doc) == [], \
        "a shape was committed before the button came up"
    assert len(view.annotation_manager.session.history) == 0

    release(view, spots[-1])
    QApplication.processEvents()

    assert len(shapes(view.annotation_manager.doc)) == 1


def test_the_box_is_the_two_corners_that_were_dragged(view):
    drag(view, 0, BOX)

    x0, y0, x1, y1 = shapes(view.annotation_manager.doc)[0]["rect"]
    assert (x0, y0) == pytest.approx((20.0, 20.0), abs=1.0)
    assert (x1, y1) == pytest.approx((120.0, 80.0), abs=1.0)


def test_a_line_keeps_the_two_ends_that_were_dragged(qapp, win, view):
    choose(win, "line")

    drag(view, 0, DIAGONAL)

    held = shapes(view.annotation_manager.doc)[0]
    assert near(held["points"], DIAGONAL)
    assert held["line_ends"] == (0, 0)


def test_an_arrow_is_a_line_with_a_head(qapp, win, view):
    choose(win, "arrow")

    drag(view, 0, DIAGONAL)

    held = shapes(view.annotation_manager.doc)[0]
    assert held["line_ends"] == (0, 5), held["line_ends"]


def test_a_polygon_keeps_the_corners_and_drops_the_jitter(qapp, win, view):
    """The hand wobbles; the annotation should hold the triangle it meant."""
    view.set_zoom(1.0)
    choose(win, "polygon")

    def edge(a, b, steps):
        return [(a[0] + (b[0] - a[0]) * i / steps,
                 a[1] + (b[1] - a[1]) * i / steps)
                for i in range(steps)]

    jitter = [(0.7, -0.6), (-0.8, 0.5), (0.6, 0.7)]
    trace = [TRIANGLE[0]]
    corners = TRIANGLE + (TRIANGLE[0],)
    for index in range(len(corners) - 1):
        run = edge(corners[index], corners[index + 1], 8)
        for i, point in enumerate(run[1:-1]):
            dx, dy = jitter[i % len(jitter)]
            trace.append((round(point[0] + dx, 3), round(point[1] + dy, 3)))
        trace.append(corners[index + 1])
    assert len(trace) > 12

    drag(view, 0, trace)

    points = shapes(view.annotation_manager.doc)[0]["points"]
    assert len(points) == 3, points
    near(points, TRIANGLE, tol=2.5)


def test_one_undo_removes_the_shape(qapp, win, view):
    choose(win, "ellipse")
    drag(view, 0, BOX)

    assert view.annotation_manager.session.history.undo_label == "Ellipse"
    view.undo_edit()
    QApplication.processEvents()

    assert shapes(view.annotation_manager.doc) == []


# --------------------------------------------------------------- the geometry
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_line_lands_where_the_drag_went(qapp, win, view, rotation):
    """The capture is page space, whatever the view is turned to."""
    choose(win, "line")
    view.set_rotation(rotation)
    qapp.processEvents()

    drag(view, 0, DIAGONAL)

    held = shapes(view.annotation_manager.doc)
    assert len(held) == 1
    near(held[0]["points"], DIAGONAL, tol=2.5)


@pytest.mark.parametrize("zoom", [0.8, 1.0, 2.5])
def test_the_shape_follows_the_zoom(qapp, view, zoom):
    view.set_zoom(zoom)
    qapp.processEvents()

    drag(view, 0, BOX)

    # MuPDF stores the box padded by half the stroke width, and a rounded
    # widget pixel is worth more page points at low zoom.
    x0, y0, x1, y1 = shapes(view.annotation_manager.doc)[0]["rect"]
    assert (x0, y0, x1, y1) == pytest.approx((20.0, 20.0, 120.0, 80.0),
                                            abs=2.5)


def test_a_drag_that_leaves_the_page_ends_at_the_paper(qapp, view):
    """The shape stops where the page does instead of reaching into the gutter."""
    start = widget_pos_for(view, 0, 30.0, 20.0)
    inside = widget_pos_for(view, 0, 60.0, 60.0)
    away = QPoint(inside.x(), inside.y() + 400)      # past the bottom of the page
    press(view, start)
    move(view, inside)
    move(view, away)
    release(view, away)
    qapp.processEvents()

    held = shapes(view.annotation_manager.doc)
    assert len(held) == 1
    assert held[0]["rect"][3] <= 100.5, held[0]["rect"]


def test_a_press_off_the_page_draws_nothing(qapp, view):
    origin_x, origin_y = view._page_origin(0)
    away = QPoint(max(0, round(origin_x) - 40), round(origin_y + 40))

    press_move_release(view, [away, QPoint(away.x() + 20, away.y() + 20)])

    assert shapes(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0


def test_a_click_is_not_a_shape(view):
    """A click has no extent, and a degenerate /Square is not an annotation."""
    spot = widget_pos_for(view, 0, 60.0, 50.0)

    press_move_release(view, [spot, spot])

    assert shapes(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0


# ------------------------------------------------------------------ the preview
def test_the_shape_is_shown_while_it_is_dragged(qapp, view):
    blank = view.viewport().grab().toImage()
    spots = [widget_pos_for(view, 0, x, y) for x, y in BOX]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    qapp.processEvents()

    live = view.viewport().grab().toImage()
    changed = sum(1 for y in range(0, live.height(), 3)
                  for x in range(0, live.width(), 3)
                  if live.pixelColor(x, y) != blank.pixelColor(x, y))
    release(view, spots[-1])
    qapp.processEvents()

    assert changed > 10, "the shape in progress is not being painted"


# --------------------------------------------------------------- getting out of it
def test_escape_gives_up_the_shape(qapp, view):
    spots = [widget_pos_for(view, 0, x, y) for x, y in BOX]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    qapp.processEvents()

    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)
    release(view, spots[-1])
    qapp.processEvents()

    assert shapes(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0
    assert view._shape_drag is None


def test_another_tool_gives_up_the_shape(qapp, view):
    spots = [widget_pos_for(view, 0, x, y) for x, y in BOX]
    press(view, spots[0])
    move(view, spots[1])
    qapp.processEvents()

    view.set_tool_mode("text")
    release(view, spots[1])
    qapp.processEvents()

    assert shapes(view.annotation_manager.doc) == []
    assert view._shape_drag is None


# ------------------------------------------------------------------ reflow guard
def test_the_shape_tool_is_off_in_reflow(qapp, win, view):
    win.act_reflow.trigger()
    qapp.processEvents()

    assert win.act_shape_rect.isEnabled() is False
    assert view.tool_mode != "shape"


# --------------------------------------------------------------------- styling
def test_the_drawing_colour_and_width_style_the_shape(qapp, win, view):
    from pdfar.annotations import INK_COLORS
    blue = dict(INK_COLORS)["Blue"]
    win._set_ink_color(blue)
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    qapp.processEvents()

    drag(view, 0, BOX)

    held = shapes(view.annotation_manager.doc)[0]
    assert held["color"] == [round(int(blue[i:i + 2], 16) / 255, 2)
                             for i in (1, 3, 5)]
    assert held["width"] == pytest.approx(4.0)


def test_the_style_pickers_appear_with_the_shape_tool(qapp, win, view):
    choose(win, "rect")
    assert win.pen_options.isVisible() is True
    win.act_sel_text.trigger()
    qapp.processEvents()
    assert win.pen_options.isVisible() is False


# ---------------------------------------------------------------- per-document
def test_each_tab_keeps_its_own_shape_kind(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    choose(win, "arrow")
    assert win.open_document(second)
    qapp.processEvents()

    choose(win, "ellipse")
    assert win.view.shape_kind == "ellipse"

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view.shape_kind == "arrow"
    assert win.act_shape_arrow.isChecked()
    assert win.shape_split.defaultAction() is win.act_shape_arrow
