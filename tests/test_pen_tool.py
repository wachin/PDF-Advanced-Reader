"""F10-3j: the pen tool — dragging a stroke into the page.

F10-3i made a freehand drawing a real ``/Ink`` annotation that nothing in the
interface could reach.  This is the mouse half: a tool mode that captures a
drag, turns it into page-space points and hands them to ``AddInk`` when the
button comes up.

The parts worth pinning down are the ones a drawing has and a click does not:
the stroke is *live* while the button is held, it is one annotation per drag,
and every point of it has to survive the walk from widget coordinates through
scroll bars, zoom and view rotation into the page space the annotation is
stored in — the same road a highlight learned the hard way (F10-3g).
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
from pdfar.annotations import AnnotationType, InkAnnotation
from pdfar.geometry import map_point

#: The sample page is 200x100.
DRAG = ((30.0, 20.0), (60.0, 60.0), (90.0, 25.0), (120.0, 70.0))


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
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=2, width=200, height=100))
    assert win.open_document(path)
    qapp.processEvents()
    tab = win._active_tab()
    tab.view.set_tool_mode("draw")
    qapp.processEvents()
    return tab.view


def widget_pos_for(view, page, x, y):
    """Where a page-space point is on the screen."""
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom - view.verticalScrollBar().value()))


def inks(doc, page=0):
    """The ``/Ink`` annotations on *page*, as the file holds them."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_INK:
            continue
        out.append({"id": annot.info.get("id"),
                    "paths": [[(round(x, 1), round(y, 1)) for x, y in line]
                              for line in annot.vertices],
                    "color": [round(c, 2) for c in (annot.colors.get("stroke") or [])],
                    "width": annot.border.get("width")})
    return out


def mouse(view, kind, spot, buttons):
    """Send one mouse event with the button state the drag really has.

    ``QTest.mouseMove`` takes no button argument here, and a move it sends says
    ``NoButton`` — which the view reads as "the pen was lifted".
    """
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
    """Drag the mouse through *spots* (widget points), button held."""
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    release(view, spots[-1])
    QApplication.processEvents()


def drag(view, page, points):
    """Drag through page-space *points*, converting each to the screen."""
    press_move_release(view, [widget_pos_for(view, page, x, y) for x, y in points])


def near(actual, expected, tol=2.0):
    """Every expected point has a captured point within *tol* of it."""
    flat = [(x, y) for line in actual for x, y in line]
    for want in expected:
        assert any(abs(x - want[0]) <= tol and abs(y - want[1]) <= tol
                   for x, y in flat), (want, flat)
    return True


# -------------------------------------------------------------------- the tool
def test_the_pen_is_a_mouse_tool(qapp, win, view):
    """Selecting it is a mode change, not a second widget."""
    assert view.tool_mode == "draw"
    view.set_tool_mode("text")
    assert view.tool_mode == "text"
    win.act_draw.trigger()
    qapp.processEvents()
    assert view.tool_mode == "draw"
    assert win.act_draw.isChecked()


def test_drawing_needs_the_pen(view):
    """The same drag with the text tool selects; it does not draw."""
    view.set_tool_mode("text")

    drag(view, 0, DRAG)

    assert inks(view.annotation_manager.doc) == []


# ---------------------------------------------------------- a drag becomes a stroke
def test_a_drag_draws_one_stroke_on_release(view):
    """Nothing is written while the button is down: the stroke is one edit."""
    spots = [widget_pos_for(view, 0, x, y) for x, y in DRAG]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    QApplication.processEvents()

    doc = view.annotation_manager.doc
    assert inks(doc) == [], "a stroke was committed before the button came up"
    assert len(view.annotation_manager.session.history) == 0

    release(view, spots[-1])
    QApplication.processEvents()

    # The edit adopted a new document; the one read before it is closed.
    held = inks(view.annotation_manager.doc)
    assert len(held) == 1
    assert near(held[0]["paths"], DRAG)
    assert view.annotation_manager.session.history.undo_label == "Freehand"


def test_the_pen_hands_the_drawing_to_the_document(view):
    """The tool adds no second way to change a PDF: it calls the manager."""
    drag(view, 0, DRAG)

    listed = view.annotation_manager.get_annotations(page=0,
                                                    ann_type=AnnotationType.FREEHAND)
    assert len(listed) == 1
    assert isinstance(listed[0], InkAnnotation)
    assert len(view.annotation_manager.session.history) == 1


def test_two_drags_are_two_drawings_and_two_undo_steps(view):
    """Each press-to-release is its own drawing, so Ctrl+Z lifts one at a time."""
    drag(view, 0, DRAG)
    drag(view, 0, ((150.0, 20.0), (170.0, 80.0)))

    held = inks(view.annotation_manager.doc)
    assert len(held) == 2
    assert len(view.annotation_manager.session.history) == 2

    view.undo_edit()
    assert [len(h["paths"][0]) for h in inks(view.annotation_manager.doc)] == [4]


def test_a_tap_makes_a_dot(view):
    """A press and release in one place is a stroke of a single point.

    MuPDF accepts it, and a user who taps with a pen expects a mark, not
    nothing at all.
    """
    drag(view, 0, ((40.0, 40.0),))

    held = inks(view.annotation_manager.doc)
    assert len(held) == 1
    assert len(held[0]["paths"][0]) >= 1


def test_the_pen_keeps_its_colour_and_thickness(view):
    drag(view, 0, DRAG)

    held = inks(view.annotation_manager.doc)[0]
    assert held["color"] == [round(int(view.ink_color[i:i + 2], 16) / 255, 2)
                             for i in (1, 3, 5)]
    assert held["width"] == pytest.approx(view.ink_width)


# --------------------------------------------------------------- the geometry
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_stroke_lands_where_the_pen_went(qapp, view, rotation):
    """The capture is in page space, whatever the view is turned to.

    At 90° a drag that runs *down* the screen runs *along* the page, and the
    annotation has to say so — the mistake F10-3g found in the highlight tool.
    """
    view.set_rotation(rotation)
    qapp.processEvents()

    drag(view, 0, DRAG)

    held = inks(view.annotation_manager.doc)
    assert len(held) == 1
    near(held[0]["paths"], DRAG, tol=2.5)


@pytest.mark.parametrize("zoom", [0.8, 1.0, 2.5])
def test_the_stroke_follows_the_zoom(qapp, view, zoom):
    """Points are stored in page space, so the magnification cannot stretch them."""
    view.set_zoom(zoom)
    qapp.processEvents()

    drag(view, 0, DRAG)

    near(inks(view.annotation_manager.doc)[0]["paths"], DRAG, tol=2.5)


def test_a_drag_that_leaves_the_page_keeps_the_part_on_it(qapp, view):
    """Off-page points are dropped, not clamped: nothing is drawn in the gutter."""
    off = widget_pos_for(view, 0, 60.0, 60.0)
    away = QPoint(off.x(), off.y() + 400)      # past the bottom of the page
    press(view, widget_pos_for(view, 0, 30.0, 20.0))
    move(view, off)
    move(view, away)
    release(view, away)
    qapp.processEvents()

    held = inks(view.annotation_manager.doc)
    assert len(held) == 1
    assert all(y <= 100.0 for _x, y in held[0]["paths"][0]), held[0]["paths"]


def test_a_press_off_the_page_draws_nothing(qapp, view):
    """The gutter is not paper."""
    origin_x, origin_y = view._page_origin(0)
    away = QPoint(max(0, round(origin_x) - 40), round(origin_y + 40))

    press_move_release(view, [away, QPoint(away.x() + 20, away.y() + 20)])

    assert inks(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0


# ------------------------------------------------------------------ the preview
def test_the_stroke_is_shown_while_it_is_being_drawn(qapp, view):
    """A pen that paints nothing until you let go feels broken."""
    blank = view.viewport().grab().toImage()
    spots = [widget_pos_for(view, 0, x, y) for x, y in DRAG]
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

    assert changed > 10, "the stroke in progress is not being painted"


# --------------------------------------------------------------- getting out of it
def test_escape_gives_up_the_stroke(qapp, view):
    """The user lifted nothing, so nothing should be in the document."""
    spots = [widget_pos_for(view, 0, x, y) for x, y in DRAG]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    qapp.processEvents()

    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)
    release(view, spots[-1])
    qapp.processEvents()

    assert inks(view.annotation_manager.doc) == []
    assert len(view.annotation_manager.session.history) == 0


def test_another_tool_gives_up_the_stroke(qapp, view):
    spots = [widget_pos_for(view, 0, x, y) for x, y in DRAG]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    qapp.processEvents()

    view.set_tool_mode("text")
    release(view, spots[-1])
    qapp.processEvents()

    assert inks(view.annotation_manager.doc) == []
    assert view._stroke is None


# ------------------------------------------------------------------ reflow guard
def test_the_pen_is_off_in_reflow(qapp, win, view):
    """Reflow shows reflowed text with no page geometry, so there is nowhere to draw."""
    win.act_reflow.trigger()
    qapp.processEvents()

    assert win.act_draw.isEnabled() is False
    assert view.tool_mode != "draw"
