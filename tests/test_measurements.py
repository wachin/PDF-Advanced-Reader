"""F10-3p: the measure tool — a distance in units the user chooses.

A measurement is not a new PDF object: it is a ``/Line`` annotation with
``/IT /LineDimension`` and a rectilinear ``/Measure`` dictionary, so a reader
that understands dimensions shows the same number PDFAR does.  MuPDF draws
such a line bare, so PDFAR writes the ticks and the reading into ``/AP/N``
itself, the way a custom stamp carries its caption.

This file covers both halves: the document is the store (the dictionary, the
reading, the appearance, undo, save and reopen) and the tool is a drag that
captures its two ends in page space, like the pen and the shapes.
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    INK_COLORS,
    MEASURE_COLOR,
    MEASURE_DEFAULT_SCALE,
    MEASURE_FONT,
    MEASURE_SCALES,
    AddMeasurement,
    AnnotationManager,
    AnnotationType,
    MeasurementAnnotation,
    MeasurementScale,
    ShapeAnnotation,
    checked_measure_scale,
    measure_area,
    measure_length,
    measurement_reading,
    open_polygon,
)
from pdfar.edits import EditError, Scope
from pdfar.geometry import map_point
from pdfar.save import save_document

MM = MEASURE_SCALES[0][1]
CM = MEASURE_SCALES[1][1]
INCHES = MEASURE_SCALES[2][1]
POINTS = MEASURE_SCALES[3][1]

HORIZONTAL = ((20.0, 50.0), (120.0, 50.0))
RECTANGLE = ((20.0, 20.0), (120.0, 20.0), (120.0, 70.0), (20.0, 70.0))


def same_scale(actual, expected):
    """The file keeps the factor to six decimals, so compare it as a number."""
    assert actual.unit == expected.unit
    assert actual.precision == expected.precision
    assert actual.units_per_point == pytest.approx(
        expected.units_per_point, abs=1e-6)


def near_points(actual, expected, tol=2.0):
    assert len(actual) == len(expected)
    for (x, y), (want_x, want_y) in zip(actual, expected):
        assert x == pytest.approx(want_x, abs=tol)
        assert y == pytest.approx(want_y, abs=tol)


def sole_line(doc, page_idx=0):
    """The page and its only annotation, kept alive by the caller's locals."""
    page = doc.load_page(page_idx)
    annots = list(page.annots())
    assert len(annots) == 1
    return page, annots[0]


def sole_annot(doc, subtype, page_idx=0):
    """The page and its only annotation of *subtype*, kept alive by the caller."""
    page = doc.load_page(page_idx)
    annots = [a for a in page.annots() if a.type[0] == subtype]
    assert len(annots) == 1
    return page, annots[0]


def line_annots(doc, page=0):
    """The ``/Line`` annotations on *page*, as the file holds them."""
    out = []
    for annot in doc.load_page(page).annots():
        if annot.type[0] != fitz.PDF_ANNOT_LINE:
            continue
        out.append({
            "id": annot.info.get("id"),
            "subtype": annot.type[0],
            "points": [(round(x, 2), round(y, 2))
                       for x, y in (annot.vertices or [])],
            "measure": doc.xref_get_key(annot.xref, "Measure"),
            "it": doc.xref_get_key(annot.xref, "IT"),
            "ap": doc.xref_get_key(annot.xref, "AP"),
            "content": annot.info.get("content"),
            "rect": (annot.rect.x0, annot.rect.y0,
                     annot.rect.x1, annot.rect.y1),
        })
    return out


def changed_pixels(bare, painted):
    return sum(1 for y in range(painted.height) for x in range(painted.width)
               if bare.pixel(x, y)[:3] != painted.pixel(x, y)[:3])


def is_red(pixel):
    red, green, blue = pixel[:3]
    return red > 150 and green < 110 and blue < 110


def red_pixels(pixmap):
    return sum(1 for y in range(pixmap.height) for x in range(pixmap.width)
               if is_red(pixmap.pixel(x, y)))


def paint(page, annots):
    clip = fitz.Rect(0.0, 0.0, 200.0, 100.0)
    return page.get_pixmap(matrix=fitz.Matrix(2, 2), annots=annots, clip=clip)


@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


# ------------------------------------------------------- the document is the store
def test_a_measurement_is_a_real_line_dimension(manager):
    made = manager.add_measurement(0, HORIZONTAL)

    assert isinstance(made, MeasurementAnnotation)
    assert made.type is AnnotationType.MEASUREMENT
    assert made.color == MEASURE_COLOR
    held = line_annots(manager.doc)
    assert len(held) == 1
    assert held[0]["id"] == made.id
    assert held[0]["subtype"] == fitz.PDF_ANNOT_LINE
    assert held[0]["it"] == ("name", "/LineDimension")
    assert held[0]["points"] == [(20.0, 50.0), (120.0, 50.0)]
    _page, annot = sole_line(manager.doc)
    assert manager.doc.xref_get_key(annot.xref, "Subj") == (
        "string", "Distance")
    assert held[0]["measure"][0] == "dict"
    assert "/Subtype/RL" in held[0]["measure"][1]
    assert "(1 pt = 0.352778 mm)" in held[0]["measure"][1]
    assert held[0]["ap"][0] == "dict" and "/N" in held[0]["ap"][1]
    assert manager.session.history.undo_label == "Measurement"


def test_the_reading_comes_from_the_distance_and_the_scale(manager):
    made = manager.add_measurement(0, HORIZONTAL)

    assert made.reading == "35.3 mm"
    assert made.content == "35.3 mm"
    assert made.points == [(20.0, 50.0), (120.0, 50.0)]
    same_scale(made.scale, MM)


@pytest.mark.parametrize("scale,reading", [
    (MM, "35.3 mm"),
    (CM, "3.53 cm"),
    (INCHES, "1.39 in"),
    (POINTS, "100.0 pt"),
])
def test_each_scale_reads_in_its_own_units(manager, scale, reading):
    made = manager.add_measurement(0, HORIZONTAL, scale=scale)

    assert made.reading == reading
    same_scale(made.scale, scale)
    assert line_annots(manager.doc)[0]["content"] == reading


def test_the_reading_is_drawn_on_the_page(manager):
    manager.add_measurement(0, HORIZONTAL)

    page = manager.doc.load_page(0)
    bare = paint(page, False)
    painted = paint(page, True)
    assert changed_pixels(bare, painted) > 100
    assert red_pixels(painted) > 100


def test_a_diagonal_measurement_reads_its_true_length(manager):
    made = manager.add_measurement(0, ((20.0, 20.0), (120.0, 80.0)))

    assert math.dist(made.points[0], made.points[1]) == pytest.approx(
        116.619, abs=0.01)
    assert made.reading == "41.1 mm"


def test_the_box_makes_room_for_the_reading_above_the_line(manager):
    made = manager.add_measurement(0, HORIZONTAL)

    x0, y0, x1, y1 = made.rects[0]
    assert x0 < 20.0 and x1 > 120.0, (x0, x1)
    assert y0 < 50.0 < y1, (y0, y1)
    assert y1 - y0 >= MEASURE_FONT, (y0, y1)


def test_at_the_top_of_the_page_the_reading_goes_below(manager):
    made = manager.add_measurement(0, ((20.0, 2.0), (120.0, 2.0)))

    x0, y0, x1, y1 = made.rects[0]
    assert y0 < 2.0 < y1, (y0, y1)
    assert 0.0 <= y0 and y1 <= 100.0, (y0, y1)


@pytest.mark.parametrize("points", [
    ((2.0, 50.0), (30.0, 50.0)),
    ((170.0, 50.0), (198.0, 50.0)),
    ((2.0, 2.0), (30.0, 2.0)),
    ((2.0, 98.0), (30.0, 98.0)),
])
def test_the_box_never_leaves_the_page(manager, points):
    made = manager.add_measurement(0, points)

    x0, y0, x1, y1 = made.rects[0]
    assert 0.0 <= x0 < x1 <= 200.0, (x0, x1)
    assert 0.0 <= y0 < y1 <= 100.0, (y0, y1)


def test_a_measurement_takes_the_pen_colour_and_width(manager):
    made = manager.add_measurement(
        0, HORIZONTAL, color="#1565C0", width=2.5)

    assert made.color == "#1565C0"
    assert made.width == pytest.approx(2.5)
    _page, annot = sole_line(manager.doc)
    assert annot.border["width"] == pytest.approx(2.5)


def test_the_list_holds_it_as_a_measurement(manager):
    made = manager.add_measurement(2, ((10.0, 10.0), (60.0, 60.0)))

    listed = manager.get_annotations(page=2,
                                     ann_type=AnnotationType.MEASUREMENT)
    assert listed == [made]
    assert manager.get_annotations(page=0) == []


def test_one_undo_removes_and_redo_restores(manager):
    made = manager.add_measurement(0, HORIZONTAL)

    manager.session.undo()
    manager.reload_from_document()
    assert line_annots(manager.doc) == []
    assert manager.get_annotation(made.id) is None

    manager.session.redo()
    manager.reload_from_document()
    back = manager.get_annotation(made.id)
    assert isinstance(back, MeasurementAnnotation)
    assert back.reading == "35.3 mm"


def test_a_measurement_survives_save_and_reopen(tmp_path, manager):
    made = manager.add_measurement(0, HORIZONTAL, scale=INCHES)
    path = str(tmp_path / "measured.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        back = listed.get_annotation(made.id)
        assert isinstance(back, MeasurementAnnotation)
        near_points(back.points, made.points, tol=0.01)
        same_scale(back.scale, INCHES)
        assert back.reading == "1.39 in"
        held = line_annots(session.doc)
        assert held[0]["it"] == ("name", "/LineDimension")
        page = session.doc.load_page(0)
        bare = paint(page, False)
        painted = paint(page, True)
        assert changed_pixels(bare, painted) > 100
        assert red_pixels(painted) > 100
    finally:
        session.close()


def test_a_note_replaces_the_text_but_not_the_drawing(manager):
    made = manager.add_measurement(0, HORIZONTAL)

    noted = manager.set_note(made.id, "checked by hand")
    assert noted is not None
    assert noted.content == "checked by hand"
    assert noted.reading == "35.3 mm"
    page = manager.doc.load_page(0)
    bare = paint(page, False)
    painted = paint(page, True)
    assert changed_pixels(bare, painted) > 100


# ------------------------------------------------------- perimeter and area
def test_a_perimeter_is_a_real_poly_line_dimension(manager):
    made = manager.add_measurement(0, RECTANGLE, kind="perimeter")

    assert isinstance(made, MeasurementAnnotation)
    assert made.kind == "perimeter"
    assert made.points == list(RECTANGLE)
    assert made.reading == "88.2 mm"
    _page, annot = sole_annot(manager.doc, fitz.PDF_ANNOT_POLY_LINE)
    assert manager.doc.xref_get_key(annot.xref, "IT") == (
        "name", "/PolyLineDimension")
    assert manager.doc.xref_get_key(annot.xref, "Subj") == (
        "string", "Perimeter")
    kind, value = manager.doc.xref_get_key(annot.xref, "Measure")
    assert kind == "dict"
    assert "/Subtype/RL" in value
    assert "/A[" in value and "mm^2" in value
    assert manager.doc.xref_get_key(annot.xref, "AP")[0] == "dict"
    assert manager.session.history.undo_label == "Measurement"


def test_an_area_is_a_real_polygon_dimension(manager):
    made = manager.add_measurement(0, RECTANGLE, kind="area")

    assert isinstance(made, MeasurementAnnotation)
    assert made.kind == "area"
    assert made.reading == "622.3 mm^2"
    _page, annot = sole_annot(manager.doc, fitz.PDF_ANNOT_POLYGON)
    assert manager.doc.xref_get_key(annot.xref, "IT") == (
        "name", "/PolygonDimension")
    assert manager.doc.xref_get_key(annot.xref, "Subj") == ("string", "Area")
    kind, value = manager.doc.xref_get_key(annot.xref, "Measure")
    assert kind == "dict"
    assert "mm^2" in value
    assert manager.session.history.undo_label == "Measurement"


@pytest.mark.parametrize("kind,scale,reading", [
    ("perimeter", CM, "8.82 cm"),
    ("area", CM, "6.22 cm^2"),
    ("perimeter", POINTS, "250.0 pt"),
    ("area", POINTS, "5000.0 pt^2"),
])
def test_perimeter_and_area_read_in_the_chosen_units(
        manager, kind, scale, reading):
    made = manager.add_measurement(0, RECTANGLE, scale=scale, kind=kind)

    assert made.reading == reading
    same_scale(made.scale, scale)


def test_an_area_accepts_a_repeated_closing_corner(manager):
    made = manager.add_measurement(
        0, RECTANGLE + (RECTANGLE[0],), kind="area")

    assert made.points == list(RECTANGLE)
    assert made.reading == "622.3 mm^2"


def test_the_area_helpers_drop_the_repeat_and_find_the_space():
    corners = open_polygon(RECTANGLE + (RECTANGLE[0],))
    assert corners == list(RECTANGLE)
    assert measure_area(corners) == pytest.approx(5000.0)
    assert measure_length(corners + [corners[0]]) == pytest.approx(300.0)
    assert measurement_reading("area", RECTANGLE, MM) == "622.3 mm^2"
    assert measurement_reading("perimeter", RECTANGLE, MM) == "88.2 mm"


def test_the_area_reading_is_drawn_on_the_page(manager):
    manager.add_measurement(0, RECTANGLE, kind="area")

    page = manager.doc.load_page(0)
    bare = paint(page, False)
    painted = paint(page, True)
    assert changed_pixels(bare, painted) > 100
    assert red_pixels(painted) > 100


@pytest.mark.parametrize("kind", ["perimeter", "area"])
def test_perimeter_and_area_survive_save_and_reopen(tmp_path, manager, kind):
    made = manager.add_measurement(0, RECTANGLE, kind=kind, scale=INCHES)
    path = str(tmp_path / f"{kind}.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        back = listed.get_annotation(made.id)
        assert isinstance(back, MeasurementAnnotation)
        assert back.kind == kind
        near_points(back.points, made.points, tol=0.01)
        same_scale(back.scale, INCHES)
        assert back.reading == made.reading
    finally:
        session.close()


def test_a_polygon_without_a_measure_is_still_a_shape(tmp_path, manager):
    manager.add_measurement(0, RECTANGLE, kind="area")
    _page, annot = sole_annot(manager.doc, fitz.PDF_ANNOT_POLYGON)
    manager.doc.xref_set_key(annot.xref, "Measure", "null")
    path = str(tmp_path / "area.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        made = listed.get_annotations(page=0)
        assert len(made) == 1
        assert isinstance(made[0], ShapeAnnotation)
        assert made[0].kind == "polygon"
        near_points(made[0].points, RECTANGLE, tol=0.01)
    finally:
        session.close()


@pytest.mark.parametrize("kind,points,message", [
    ("area", ((20.0, 20.0), (120.0, 20.0)), "at least three"),
    ("area", ((20.0, 20.0), (120.0, 20.0), (20.0, 20.0)), "at least three"),
    ("area", ((20.0, 20.0), (120.0, 70.0),
              (120.0, 20.0), (20.0, 70.0)), "cross itself"),
    ("area", ((20.0, 20.0), (70.0, 20.0), (120.0, 20.0)),
     "shape with area"),
    ("perimeter", ((20.0, 20.0),), "at least two"),
    ("perimeter", ((20.0, 20.0), (20.0, 20.0)), "different adjacent"),
    ("volume", ((20.0, 20.0), (120.0, 20.0), (120.0, 70.0)),
     "not a measurement"),
])
def test_geometry_that_does_not_fit_the_kind_is_refused(
        manager, kind, points, message):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert manager.add_measurement(0, points, kind=kind) is None
    assert manager.session.version == 0
    assert manager.session.dirty is False
    assert len(manager.session.history) == 0
    assert failures and message in failures[0]


# --------------------------------------------------------- refused measurements
def test_two_points_that_are_the_same_are_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert manager.add_measurement(
        0, ((50.0, 50.0), (50.0, 50.0))) is None
    assert manager.session.version == 0
    assert manager.session.dirty is False
    assert len(manager.session.history) == 0
    assert failures and "two different points" in failures[0]


def test_a_scale_the_file_cannot_hold_is_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert manager.add_measurement(
        0, HORIZONTAL, scale=MeasurementScale(0.0, "mm")) is None
    assert manager.session.version == 0
    assert failures and "usable number" in failures[0]


def test_a_unit_the_file_cannot_hold_is_refused(manager):
    failures = []
    manager.annotation_edit_failed.connect(failures.append)

    assert manager.add_measurement(
        0, HORIZONTAL, scale=MeasurementScale(1.0, "\u03bcm")) is None
    assert manager.session.version == 0
    assert failures and "unit" in failures[0]


def test_a_reading_too_wide_for_the_page_is_refused(tmp_path):
    path = str(tmp_path / "narrow.pdf")
    doc = fitz.open()
    doc.new_page(width=20, height=100)
    doc.save(path)
    doc.close()
    session = make_session(path)
    manager = AnnotationManager(session)
    try:
        failures = []
        manager.annotation_edit_failed.connect(failures.append)

        assert manager.add_measurement(
            0, ((2.0, 50.0), (17.0, 50.0)),
            scale=MeasurementScale(1e6, "m", 2)) is None
        assert session.version == 0
        assert failures and "too wide" in failures[0]
    finally:
        session.close()


def test_add_measurement_refuses_bad_geometry_directly(manager):
    one_point = AddMeasurement(ann_id="x", page=0, points=((10.0, 10.0),))
    with pytest.raises(EditError):
        one_point.apply(manager.doc)
    assert one_point.scope == Scope.COMMENTS
    assert one_point.label == "Measurement"

    equal = AddMeasurement(ann_id="y", page=0,
                           points=((10.0, 10.0), (10.0, 10.0)))
    with pytest.raises(EditError):
        equal.apply(manager.doc)


def test_checked_scale_trims_the_unit_it_keeps_the_numbers():
    assert checked_measure_scale(MeasurementScale(2.0, "  m  ", 3)) == \
        MeasurementScale(2.0, "m", 3)


@pytest.mark.parametrize("scale", [
    MeasurementScale(0.0, "mm"),
    MeasurementScale(-1.0, "mm"),
    MeasurementScale(float("nan"), "mm"),
    MeasurementScale(float("inf"), "mm"),
    MeasurementScale(1e7, "mm"),
    MeasurementScale(1.0, "mm", 5),
    MeasurementScale(1.0, "mm", -1),
    MeasurementScale(1.0, ""),
    MeasurementScale(1.0, "m" * 17),
    MeasurementScale(1.0, "m\nm"),
    MeasurementScale(1.0, "\u03bcm"),
])
def test_a_scale_the_file_cannot_describe_is_refused(scale):
    with pytest.raises(EditError):
        checked_measure_scale(scale)


# ------------------------------------------------------ reading foreign files
def build_foreign(tmp_path, mutate):
    """A sample page with one line annotation *mutate* edits, as a session."""
    path = str(tmp_path / "foreign.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    annot = page.add_line_annot(fitz.Point(20, 50), fitz.Point(120, 50))
    annot.update()
    mutate(doc, annot)
    doc.save(path)
    doc.close()
    return make_session(path)


def test_a_plain_line_dimension_is_read_as_a_line(tmp_path):
    def no_measure(doc, annot):
        doc.xref_set_key(annot.xref, "IT", "/LineDimension")

    session = build_foreign(tmp_path, no_measure)
    try:
        listed = AnnotationManager(session)
        made = listed.get_annotations(page=0)
        assert len(made) == 1
        assert isinstance(made[0], ShapeAnnotation)
        assert made[0].kind == "line"
    finally:
        session.close()


def test_a_measure_dictionary_of_another_kind_is_read_as_a_line(
        tmp_path, manager):
    manager.add_measurement(0, HORIZONTAL)
    _page, annot = sole_line(manager.doc)
    manager.doc.xref_set_key(annot.xref, "Measure/Subtype", "/XY")
    path = str(tmp_path / "other.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        listed = AnnotationManager(session)
        made = listed.get_annotations(page=0)
        assert len(made) == 1
        assert isinstance(made[0], ShapeAnnotation)
    finally:
        session.close()


def test_a_measure_dictionary_in_an_indirect_object_is_read(
        tmp_path, manager):
    made = manager.add_measurement(0, HORIZONTAL)
    _page, annot = sole_line(manager.doc)
    kind, value = manager.doc.xref_get_key(annot.xref, "Measure")
    assert kind == "dict"
    target = manager.doc.get_new_xref()
    manager.doc.update_object(target, value)
    manager.doc.xref_set_key(annot.xref, "Measure", f"{target} 0 R")
    path = str(tmp_path / "indirect.pdf")
    assert save_document(manager.session, path) is not None

    session = make_session(path)
    try:
        _page, annot = sole_line(session.doc)
        assert session.doc.xref_get_key(annot.xref, "Measure")[0] == "xref"
        listed = AnnotationManager(session)
        back = listed.get_annotation(made.id)
        assert isinstance(back, MeasurementAnnotation)
        same_scale(back.scale, MM)
        assert back.reading == "35.3 mm"
    finally:
        session.close()


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
    w0, h0 = view.page_sizes[page]
    u, v = map_point(x, y, w0, h0, view.rotation)
    origin_x, origin_y = view._page_origin(page)
    return QPoint(round(origin_x + u * view.zoom
                        - view.horizontalScrollBar().value()),
                  round(origin_y + v * view.zoom
                        - view.verticalScrollBar().value()))


def mouse(view, kind, spot, buttons):
    vp = view.viewport()
    held = Qt.MouseButton.LeftButton if buttons else Qt.MouseButton.NoButton
    press = (Qt.MouseButton.LeftButton
             if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton)
    event = QMouseEvent(kind, QPointF(spot), QPointF(vp.mapToGlobal(spot)),
                        press, held, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(vp, event)


def press(view, spot):
    mouse(view, QEvent.Type.MouseButtonPress, spot, True)


def move(view, spot):
    mouse(view, QEvent.Type.MouseMove, spot, True)


def release(view, spot):
    mouse(view, QEvent.Type.MouseButtonRelease, spot, False)
    QApplication.processEvents()


def drag(view, page, points):
    spots = [widget_pos_for(view, page, x, y) for x, y in points]
    press(view, spots[0])
    for spot in spots[1:]:
        move(view, spot)
    release(view, spots[-1])


def click(view, spot):
    press(view, spot)
    release(view, spot)


def made_measurements(view, page=0):
    return view.annotation_manager.get_annotations(
        page=page, ann_type=AnnotationType.MEASUREMENT)


def test_choosing_a_scale_arms_the_measure_tool(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()

    assert win._measure_scale == POINTS
    assert view.tool_mode == "measure"
    assert view.measure_scale == POINTS
    assert win.act_measure.isChecked()
    assert win._measure_scale_actions["Points"].isChecked()
    assert win.measure_split.defaultAction() is win.act_measure
    assert "1 pt = 1 pt" in win.act_measure.toolTip()
    assert "1 pt = 1 pt" in win.measure_split.toolTip()


def test_the_menu_offers_each_kind_scale_and_a_custom_one(win, view):
    actions = win.measure_split.menu().actions()
    entries = [action.text() for action in actions]

    assert entries == ["Distance", "Perimeter", "Area", "",
                       "Millimetres", "Centimetres", "Inches", "Points",
                       "", "Custom Scale…"]
    assert actions[3].isSeparator()
    assert actions[8].isSeparator()
    assert actions[0].isChecked()
    assert actions[4].isChecked() and actions[4].data() == MM


def double_click(view, spot):
    """The first click of a double click, then its event, as Qt delivers them."""
    click(view, spot)
    mouse(view, QEvent.Type.MouseButtonDblClick, spot, False)
    QApplication.processEvents()


def test_choosing_area_arms_the_measure_tool(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()

    assert win._measure_kind == "area"
    assert view.measure_kind == "area"
    assert view.tool_mode == "measure"
    assert win.act_measure.isChecked()
    assert win._measure_kind_actions["area"].isChecked()
    assert not win._measure_kind_actions["distance"].isChecked()
    assert "Measure area" in win.act_measure.toolTip()
    assert "double-click" in win.measure_split.toolTip()


def test_clicking_corners_and_enter_places_an_area(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    win._measure_scale_actions["Millimetres"].trigger()
    qapp.processEvents()

    for corner in RECTANGLE:
        click(view, widget_pos_for(view, 0, *corner))
    assert len(view._measure_drag["points"]) == 4

    QTest.keyClick(view.viewport(), Qt.Key.Key_Return)
    qapp.processEvents()

    made = made_measurements(view)
    assert len(made) == 1
    assert made[0].kind == "area"
    near_points(made[0].points, RECTANGLE)
    assert made[0].reading == made[0].scale.area_reading(
        measure_area(made[0].points))
    assert made[0].reading.endswith(" mm^2")
    assert view._measure_drag is None
    assert len(view.annotation_manager.session.history) == 1


def test_enter_accepts_the_corner_under_the_pointer(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()
    for corner in RECTANGLE[:3]:
        click(view, widget_pos_for(view, 0, *corner))

    move(view, widget_pos_for(view, 0, *RECTANGLE[3]))
    QTest.keyClick(view.viewport(), Qt.Key.Key_Return)
    qapp.processEvents()

    made = made_measurements(view)
    assert len(made) == 1
    near_points(made[0].points, RECTANGLE)


def test_clicking_the_first_corner_closes_an_area(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()
    for corner in RECTANGLE:
        click(view, widget_pos_for(view, 0, *corner))

    click(view, widget_pos_for(view, 0, *RECTANGLE[0]))

    made = made_measurements(view)
    assert len(made) == 1 and made[0].kind == "area"
    near_points(made[0].points, RECTANGLE)
    assert view._measure_drag is None


def test_a_double_click_finishes_a_perimeter(qapp, win, view):
    win._measure_kind_actions["perimeter"].trigger()
    qapp.processEvents()
    for corner in RECTANGLE[:3]:
        click(view, widget_pos_for(view, 0, *corner))

    double_click(view, widget_pos_for(view, 0, *RECTANGLE[3]))

    made = made_measurements(view)
    assert len(made) == 1 and made[0].kind == "perimeter"
    near_points(made[0].points, RECTANGLE)
    assert made[0].reading == made[0].scale.reading(
        measure_length(made[0].points))
    assert made[0].reading.endswith(" mm")


def test_enter_with_too_few_corners_places_nothing(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()
    click(view, widget_pos_for(view, 0, 50.0, 20.0))

    QTest.keyClick(view.viewport(), Qt.Key.Key_Return)

    assert view._measure_drag is None
    assert made_measurements(view) == []
    assert len(view.annotation_manager.session.history) == 0


def test_escape_cancels_an_area_path(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()
    for corner in RECTANGLE[:3]:
        click(view, widget_pos_for(view, 0, *corner))
    assert view._measure_drag is not None

    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)

    assert view._measure_drag is None
    assert made_measurements(view) == []


def test_changing_the_kind_cancels_the_path(qapp, win, view):
    win._measure_kind_actions["perimeter"].trigger()
    qapp.processEvents()
    click(view, widget_pos_for(view, 0, 50.0, 20.0))
    assert view._measure_drag is not None

    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()

    assert view._measure_drag is None
    assert made_measurements(view) == []


def test_the_area_preview_follows_the_pointer_between_clicks(qapp, win, view):
    win._measure_kind_actions["area"].trigger()
    qapp.processEvents()
    click(view, widget_pos_for(view, 0, 50.0, 50.0))
    click(view, widget_pos_for(view, 0, 150.0, 50.0))

    move(view, widget_pos_for(view, 0, 150.0, 90.0))

    assert len(view._measure_drag["points"]) == 2
    assert view._measure_drag["current"] == pytest.approx(
        (150.0, 90.0), abs=1.0)
    assert not view.viewport().grab().isNull()

    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)


def test_each_tab_keeps_its_own_measure_kind(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    win._measure_kind_actions["perimeter"].trigger()
    view_a = win.view

    assert win.open_document(second)
    qapp.processEvents()
    win._measure_kind_actions["area"].trigger()
    view_b = win.view

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view is view_a
    assert view_a.measure_kind == "perimeter"
    assert win._measure_kind == "perimeter"
    assert win._measure_kind_actions["perimeter"].isChecked()

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.view is view_b
    assert view_b.measure_kind == "area"
    assert win._measure_kind == "area"
    assert win._measure_kind_actions["area"].isChecked()


def test_a_perimeter_takes_the_pen_colour_and_width(qapp, win, view):
    blue = dict(INK_COLORS)["Blue"]
    win._set_ink_color(blue)
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    win._measure_kind_actions["perimeter"].trigger()
    qapp.processEvents()

    for corner in RECTANGLE[:2]:
        click(view, widget_pos_for(view, 0, *corner))
    QTest.keyClick(view.viewport(), Qt.Key.Key_Return)
    qapp.processEvents()

    made = made_measurements(view)[0]
    assert made.kind == "perimeter"
    assert made.color == blue
    assert made.width == pytest.approx(4.0)


def test_a_drag_places_a_measurement(qapp, win, view):
    win._measure_scale_actions["Millimetres"].trigger()
    qapp.processEvents()

    drag(view, 0, [(50.0, 50.0), (150.0, 50.0)])

    made = made_measurements(view)
    assert len(made) == 1
    near_points(made[0].points, [(50.0, 50.0), (150.0, 50.0)])
    same_scale(made[0].scale, MM)
    assert made[0].reading == made[0].scale.reading(
        math.dist(*made[0].points))
    assert made[0].reading.endswith(" mm")
    assert len(view.annotation_manager.session.history) == 1
    assert view.annotation_manager.session.history.undo_label == "Measurement"


def test_a_click_places_nothing(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()

    click(view, widget_pos_for(view, 0, 100.0, 50.0))

    assert made_measurements(view) == []
    assert len(view.annotation_manager.session.history) == 0


def test_escape_cancels_the_measurement(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()
    press(view, widget_pos_for(view, 0, 100.0, 50.0))
    move(view, widget_pos_for(view, 0, 150.0, 50.0))
    assert view._measure_drag is not None

    QTest.keyClick(view.viewport(), Qt.Key.Key_Escape)
    assert view._measure_drag is None
    release(view, widget_pos_for(view, 0, 150.0, 50.0))

    assert made_measurements(view) == []


def test_the_preview_follows_the_drag(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()
    press(view, widget_pos_for(view, 0, 100.0, 50.0))
    move(view, widget_pos_for(view, 0, 150.0, 50.0))

    assert view._measure_drag["start"] == pytest.approx(
        (100.0, 50.0), abs=1.0)
    assert view._measure_drag["current"] == pytest.approx(
        (150.0, 50.0), abs=1.0)
    assert not view.viewport().grab().isNull()

    release(view, widget_pos_for(view, 0, 150.0, 50.0))


def test_changing_tools_cancels_the_drag(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()
    press(view, widget_pos_for(view, 0, 100.0, 50.0))
    move(view, widget_pos_for(view, 0, 150.0, 50.0))

    win.act_explore.trigger()
    qapp.processEvents()

    assert view._measure_drag is None
    assert view.tool_mode == "browse"
    release(view, widget_pos_for(view, 0, 150.0, 50.0))
    assert made_measurements(view) == []


def test_the_measure_tool_is_off_in_reflow(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    win.act_reflow.trigger()
    qapp.processEvents()

    assert win.act_measure.isEnabled() is False
    assert view.tool_mode != "measure"


def test_typing_a_custom_scale_arms_the_tool(qapp, win, view, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("0.5 in", True)))

    win.act_measure_custom.trigger()
    qapp.processEvents()

    scale = MeasurementScale(0.5, "in", 2)
    assert win._measure_scale == scale
    assert view.measure_scale == scale
    assert view.tool_mode == "measure"
    assert "1 pt = 0.5 in" in win.act_measure.toolTip()
    drag(view, 0, [(50.0, 50.0), (150.0, 50.0)])
    made = made_measurements(view)
    assert len(made) == 1
    same_scale(made[0].scale, scale)
    assert made[0].reading.endswith(" in")

def test_a_custom_scale_out_of_range_is_refused(qapp, win, view, monkeypatch):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[2])
                                     or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("0 mm", True)))

    win.act_measure_custom.trigger()
    qapp.processEvents()

    assert win._measure_scale == POINTS
    assert view.measure_scale == POINTS
    assert view.tool_mode == "measure"
    assert warnings and "usable number" in warnings[-1]


def test_a_custom_scale_without_a_unit_is_refused(qapp, win, view,
                                                  monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning",
                        staticmethod(lambda *a, **k: warnings.append(a[2])
                                     or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("0.5", True)))

    win.act_measure_custom.trigger()
    qapp.processEvents()

    assert win._measure_scale == MEASURE_DEFAULT_SCALE
    assert warnings and "number and a unit" in warnings[-1]


def test_cancelling_the_custom_scale_keeps_the_old_one(qapp, win, view,
                                                       monkeypatch):
    win._measure_scale_actions["Inches"].trigger()
    qapp.processEvents()
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: ("", False)))

    win.act_measure_custom.trigger()
    qapp.processEvents()

    assert win._measure_scale == INCHES
    assert view.measure_scale == INCHES
    assert view.tool_mode == "measure"


def test_each_tab_keeps_its_own_measure_scale(qapp, win, tmp_path):
    first = str(build_sample_pdf(tmp_path / "first.pdf", pages=1,
                                 width=200, height=100))
    second = str(build_sample_pdf(tmp_path / "second.pdf", pages=1,
                                  width=200, height=100))
    assert win.open_document(first)
    win._measure_scale_actions["Inches"].trigger()
    view_a = win.view

    assert win.open_document(second)
    qapp.processEvents()
    win._measure_scale_actions["Points"].trigger()
    view_b = win.view

    win.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert win.view is view_a
    assert view_a.measure_scale == INCHES
    assert "0.0138889 in" in win.measure_split.toolTip()

    win.tabs.setCurrentIndex(1)
    qapp.processEvents()
    assert win.view is view_b
    assert view_b.measure_scale == POINTS
    assert "1 pt = 1 pt" in win.measure_split.toolTip()


def test_the_pen_controls_appear_with_the_measure_tool(qapp, win, view):
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()
    assert win.pen_options.isVisible() is True

    win.act_sel_text.trigger()
    qapp.processEvents()
    assert win.pen_options.isVisible() is False


def test_the_pen_style_styles_the_measurement(qapp, win, view):
    blue = dict(INK_COLORS)["Blue"]
    win._set_ink_color(blue)
    win.pen_width_combo.setCurrentIndex(win.pen_width_combo.findData(4.0))
    win._measure_scale_actions["Points"].trigger()
    qapp.processEvents()

    drag(view, 0, [(50.0, 50.0), (150.0, 50.0)])

    made = made_measurements(view)[0]
    assert made.color == blue
    assert made.width == pytest.approx(4.0)


def test_a_measurement_lands_under_a_turned_view(qapp, win, view):
    win._measure_scale_actions["Centimetres"].trigger()
    view.set_rotation(90)
    qapp.processEvents()

    drag(view, 0, [(50.0, 50.0), (150.0, 50.0)])

    made = made_measurements(view)
    assert len(made) == 1
    near_points(made[0].points, [(50.0, 50.0), (150.0, 50.0)])
    same_scale(made[0].scale, CM)
    view.set_rotation(0)
