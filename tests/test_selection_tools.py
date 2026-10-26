"""Area/table selection helpers and mouse tools."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMenu

from pdfar.geometry import map_rect
from pdfar.selection_tools import (
    find_dividers, group_lines, table_size, table_text, words_in_rect,
)
from pdfar.viewer import PDFView


def word(x0, y0, x1, y1, text):
    return (x0, y0, x1, y1, text)


def test_words_in_rect_excludes_outside_boxes():
    words = [
        word(10, 10, 30, 20, "inside"),
        word(100, 10, 130, 20, "outside"),
        word(10, 100, 30, 110, "below"),
    ]

    assert [w[4] for w in words_in_rect(words, (0, 0, 60, 60))] == ["inside"]


def test_group_lines_orders_words_left_to_right():
    words = [
        word(60, 40, 80, 52, "second"),
        word(10, 10, 30, 22, "Hello"),
        word(35, 10, 55, 22, "world"),
        word(10, 40, 40, 52, "line"),
    ]

    assert group_lines(words) == ["Hello world", "line second"]


def test_find_dividers_two_columns():
    words = [
        word(10, 10, 20, 20, "A1"), word(100, 10, 110, 20, "B1"),
        word(10, 40, 20, 50, "A2"), word(100, 40, 110, 50, "B2"),
    ]

    assert find_dividers(words, "x") == [60.0]
    assert find_dividers(words, "y") == [30.0]


def test_table_tsv_two_by_two():
    words = [
        word(10, 10, 20, 20, "A1"), word(100, 10, 110, 20, "B1"),
        word(10, 40, 20, 50, "A2"), word(100, 40, 110, 50, "B2"),
    ]

    text = table_text(words, (0, 0, 120, 60))

    assert text == "A1\tB1\nA2\tB2"
    assert table_size(text) == (2, 2)


def test_table_tsv_keeps_interior_empty_cell():
    words = [
        word(10, 10, 20, 20, "A1"),
        word(100, 10, 110, 20, "B1"),
        word(200, 10, 210, 20, "C1"),
        word(10, 40, 20, 50, "A2"),
        word(200, 40, 210, 50, "C2"),
    ]

    text = table_text(words, (0, 0, 220, 60))

    assert text == "A1\tB1\tC1\nA2\t\tC2"
    assert table_size(text) == (2, 3)


def test_table_text_without_words_is_empty():
    assert table_text([], (0, 0, 100, 100)) == ""
    assert table_size("") == (0, 0)


# ----------------------------------------------------- viewer interaction
def _view_for(qapp, sample_pdf, size=(600, 400)):
    view = PDFView(sample_pdf)
    view.resize(*size)
    view.show()
    qapp.processEvents()
    return view


def _union(words):
    return (min(w[0] for w in words), min(w[1] for w in words),
            max(w[2] for w in words), max(w[3] for w in words))


def _content_view_rect(view, page, page_rect, margin=4.0):
    """Content-space rect (scrollbars applied later) for a page-space rect."""
    x0, y0, x1, y1 = map_rect(
        page_rect, *view.page_sizes[page], view.rotation)
    px, py = view._page_origin(page)
    return QRect(
        int(round(px + (x0 - margin / view.zoom) * view.zoom)),
        int(round(py + (y0 - margin / view.zoom) * view.zoom)),
        max(1, int(round((x1 - x0) * view.zoom + 2 * margin))),
        max(1, int(round((y1 - y0) * view.zoom + 2 * margin))),
    )


def _viewport_rect(view, content_rect):
    return content_rect.translated(
        -view.horizontalScrollBar().value(),
        -view.verticalScrollBar().value())


def _send_mouse(view, kind, local, button, buttons):
    event = QMouseEvent(
        kind, QPointF(local), QPointF(local), button, buttons,
        Qt.KeyboardModifier.NoModifier)
    from PyQt6.QtWidgets import QApplication
    QApplication.sendEvent(view.viewport(), event)


def _drag(view, start, end):
    _send_mouse(view, QEvent.Type.MouseButtonPress, start,
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    _send_mouse(view, QEvent.Type.MouseMove, end,
                Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    _send_mouse(view, QEvent.Type.MouseButtonRelease, end,
                Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)


def _drag_over_words(view, words):
    content = _content_view_rect(view, 0, _union(words))
    rect = _viewport_rect(view, content)
    _drag(view, rect.topLeft(), rect.bottomRight())
    return rect


def test_view_defaults_to_text_tool(qapp, sample_pdf):
    view = _view_for(qapp, sample_pdf)
    try:
        assert view.tool_mode == "text"
        assert (view.viewport().cursor().shape()
                == Qt.CursorShape.IBeamCursor)
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_browse_drag_pans_scrollbars(qapp, sample_pdf):
    view = _view_for(qapp, sample_pdf, size=(300, 120))
    try:
        bar = view.verticalScrollBar()
        assert bar.maximum() > 60
        bar.setValue(0)
        view.set_tool_mode("browse")
        _drag(view, QPoint(150, 100), QPoint(150, 40))
        assert bar.value() == 60
        assert view.tool_mode == "browse"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_area_text_and_menu(qapp, sample_pdf, monkeypatch):
    view = _view_for(qapp, sample_pdf)
    try:
        view.set_tool_mode("area")
        words = [w for w in view._page_words(0)
                 if w[4] in ("Hello", "PDFAR", "world")]
        rect = _viewport_rect(
            view, _content_view_rect(view, 0, _union(words)))
        assert view.area_text(rect) == "Hello PDFAR world"

        image = view.area_image(rect)
        assert not image.isNull()
        assert (image.width(), image.height()) == (rect.width(), rect.height())

        menu = view._build_area_menu(rect)
        labels = [action.text() for action in menu.actions()]
        assert labels[0].startswith("Copy Text (17 characters)")
        assert labels[1].startswith("Copy Image")

        menu.actions()[0].trigger()
        assert qapp.clipboard().text() == "Hello PDFAR world"
        menu.actions()[1].trigger()
        grabbed = qapp.clipboard().pixmap()
        assert not grabbed.isNull()
        menu.deleteLater()
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_area_release_opens_menu(qapp, sample_pdf, monkeypatch):
    shown = []
    monkeypatch.setattr(QMenu, "exec",
                        lambda self, *a, **k: shown.append(self) or None)
    view = _view_for(qapp, sample_pdf)
    try:
        view.set_tool_mode("area")
        words = [w for w in view._page_words(0)
                 if w[4] in ("Hello", "PDFAR", "world")]
        _drag_over_words(view, words)
        assert len(shown) == 1
        assert [a.text().split(" (")[0] for a in shown[0].actions()] == [
            "Copy Text", "Copy Image"]
        assert view._marquee is None
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_area_text_survives_all_rotations(qapp, sample_pdf):
    view = _view_for(qapp, sample_pdf)
    try:
        view.set_tool_mode("area")
        words = [w for w in view._page_words(0)
                 if w[4] in ("Hello", "PDFAR", "world")]
        for rotation in (0, 90, 180, 270):
            view.set_rotation(rotation)
            qapp.processEvents()
            content = _content_view_rect(view, 0, _union(words))
            assert view.area_text(_viewport_rect(view, content)) == \
                "Hello PDFAR world"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def _build_table_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    for text, point in (("A1", (30, 40)), ("B1", (150, 40)),
                        ("A2", (30, 100)), ("B2", (150, 100))):
        page.insert_text(fitz.Point(*point), text, fontsize=12)
    doc.save(path)
    doc.close()
    return str(path)


def test_table_drag_autocopies_tsv_and_status(qapp, tmp_path):
    view = _view_for(qapp, _build_table_pdf(tmp_path / "table.pdf"))
    messages = []
    view.transient_message.connect(messages.append)
    try:
        view.set_tool_mode("table")
        words = list(view._page_words(0))
        _drag_over_words(view, words)
        assert qapp.clipboard().text() == "A1\tB1\nA2\tB2"
        assert messages[-1] == "Table copied (2 × 2)"
        assert view.table_text(
            _viewport_rect(view, _content_view_rect(view, 0, _union(words)))
        ) == "A1\tB1\nA2\tB2"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_tiny_drag_does_not_touch_clipboard(qapp, sample_pdf):
    view = _view_for(qapp, sample_pdf)
    messages = []
    view.transient_message.connect(messages.append)
    try:
        view.set_tool_mode("table")
        qapp.clipboard().setText("sentinel")
        point = QPoint(100, 100)
        _drag(view, point, point + QPoint(2, 2))
        assert qapp.clipboard().text() == "sentinel"
        assert messages == []
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_escape_cancels_marquee(qapp, sample_pdf):
    view = _view_for(qapp, sample_pdf)
    try:
        view.set_tool_mode("area")
        _send_mouse(view, QEvent.Type.MouseButtonPress, QPoint(50, 50),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
        _send_mouse(view, QEvent.Type.MouseMove, QPoint(150, 150),
                    Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
        assert view._marquee is not None
        QTest.keyClick(view, Qt.Key.Key_Escape)
        assert view._marquee is None
        # the release after Escape must not open a menu
        _send_mouse(view, QEvent.Type.MouseButtonRelease, QPoint(150, 150),
                    Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()
