"""Tests for P1-5 reflow mode (single-column reading).

The feature must never block the GUI: extraction and layout run in a
``ReflowWorker`` thread with its own PyMuPDF document, and the viewer only
paints cached layouts.
"""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtWidgets import QMessageBox

from pdfar.main import MainWindow
from pdfar.reflow import (
    REFLOW_MAX_WIDTH, REFLOW_MIN_WIDTH, Line, extract_paragraphs,
    layout_paragraphs, reflow_text_width,
)
from pdfar.viewer import PDFView


def wait_until(app, predicate, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def build_text_pdf(path, pages=3):
    """A text-based PDF with a bold heading and wrapped body paragraphs."""
    doc = fitz.open()
    for p in range(pages):
        page = doc.new_page(width=400, height=600)
        page.insert_text((50, 60), f"Chapter {p + 1}", fontsize=20, fontname="hebo")
        body = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
            "eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        ) * 10
        page.insert_textbox(fitz.Rect(50, 90, 350, 560), body, fontsize=10)
    doc.save(str(path))
    doc.close()
    return str(path)


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))


def _enable_reflow(view, app):
    ready = []
    view.reflow_ready.connect(lambda has_text, empty: ready.append((has_text, empty)))
    view.set_reflow_mode(True)
    assert wait_until(app, lambda: view._reflow_worker is None), "reflow worker never finished"
    app.processEvents()
    return ready


# ------------------------------------------------------------------ unit
def test_reflow_text_width_is_bounded():
    assert reflow_text_width(4000) == REFLOW_MAX_WIDTH
    assert reflow_text_width(800) == 800 - 2 * 48
    assert reflow_text_width(100) == REFLOW_MIN_WIDTH


def test_extract_paragraphs_preserves_text_and_style(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=1)
    doc = fitz.open(path)
    try:
        paragraphs = extract_paragraphs(doc[0], 0)
    finally:
        doc.close()

    text = " ".join(run.text for para in paragraphs for run in para.runs)
    assert "Chapter" in text
    assert "Lorem" in text
    assert any(run.bold for para in paragraphs for run in para.runs)
    assert [p.page for p in paragraphs] == [0] * len(paragraphs)


def test_layout_wraps_lines_within_width(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=1)
    doc = fitz.open(path)
    try:
        paragraphs = extract_paragraphs(doc[0], 0)
    finally:
        doc.close()

    width = 300
    layout = layout_paragraphs(paragraphs, width, 0)
    assert layout.has_text
    assert len(layout.lines) > 5
    assert layout.height > 0

    from pdfar.reflow import measure_text
    for line in layout.lines:
        line_w = sum(
            measure_text(text, family, size, bold, italic)
            for text, family, size, bold, italic in line.runs
        )
        assert line_w <= width + 1, f"line overflows: {line_w} > {width}"


def test_layout_marks_image_only_page_as_empty(qapp, tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(10, 10, 190, 190), color=(0, 0, 0), fill=(0.2, 0.2, 0.2))
    path = str(tmp_path / "scanned.pdf")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        paragraphs = extract_paragraphs(doc[0], 0)
    finally:
        doc.close()
    layout = layout_paragraphs(paragraphs, 400, 0)
    assert not layout.has_text
    assert layout.lines == []
    assert layout.height > 0


# ---------------------------------------------------------- integration
def test_reflow_line_paint_uses_section_coordinates(qapp, tmp_path):
    """A line's baseline is page-section relative; painting must not add y twice."""
    from PyQt6.QtGui import QColor, QImage, QPainter

    path = build_text_pdf(tmp_path / "text.pdf", pages=1)
    view = PDFView(path)
    view.resize(600, 400)
    view.show()
    try:
        line = Line(page=0, y=100.0, height=20.0, baseline=115.0,
                    runs=[("Hello", "Sans Serif", 14, False, False)])
        img = QImage(200, 200, QImage.Format.Format_ARGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        view._paint_reflow_line(painter, line, 10.0, 0.0, QColor("black"))
        painter.end()

        ink_rows = [y for y in range(img.height())
                    if any(QColor(img.pixel(x, y)).lightness() < 128
                           for x in range(img.width()))]
        assert ink_rows, "line was not painted"
        assert min(ink_rows) >= 95, f"ink too high: {min(ink_rows)}"
        assert max(ink_rows) <= 125, f"ink too low: {max(ink_rows)}"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_anchors_requested_page_after_preparation(qapp, tmp_path):
    """Estimated heights change while preparing; the requested page must stay put."""
    path = build_text_pdf(tmp_path / "text.pdf", pages=12)
    view = PDFView(path)
    view.resize(600, 400)
    view.show()
    try:
        view.goto_page(8)
        assert view.current_page == 8
        _enable_reflow(view, qapp)
        assert view.current_page == 8
        scroll = view.verticalScrollBar().value()
        assert 0 <= view._reflow_tops[8] - scroll < 2, "page top not aligned"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_prepares_and_paints(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=3)
    view = PDFView(path)
    view.resize(900, 700)
    view.show()
    try:
        ready = _enable_reflow(view, qapp)
        assert view.reflow_mode is True
        assert view._reflow_has_text is True
        assert len(view._reflow_pages) == 3
        assert view._reflow_total == 3
        assert view._content_h > 0
        assert ready and ready[-1] == (True, 0)

        # painting must produce text, not just the background
        img = view.grab().toImage()
        colors = {img.pixel(x, y)
                  for y in range(0, img.height(), 8)
                  for x in range(0, img.width(), 8)}
        assert len(colors) > 3, "reflow view looks blank"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_does_not_render_pages(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=2)
    view = PDFView(path)
    view.resize(800, 600)
    view.show()
    try:
        _enable_reflow(view, qapp)
        view.cache.clear()
        seen = []
        orig = view.pool.request

        def spy(page, zoom, priority=5, tag="view", alpha=False,
                rotation=0, clip=None):
            seen.append(tag)
            return orig(page, zoom, priority=priority, tag=tag,
                        alpha=alpha, rotation=rotation, clip=clip)

        view.pool.request = spy
        try:
            view._request_visible()
        finally:
            view.pool.request = orig
        assert seen == [], "reflow mode must not queue page renders"
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_navigation_updates_current_page(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=10)
    view = PDFView(path)
    view.resize(700, 500)
    view.show()
    try:
        _enable_reflow(view, qapp)
        view.goto_page(5)
        qapp.processEvents()
        assert view.current_page == 5

        vbar = view.verticalScrollBar()
        assert vbar.maximum() > 0, "reflow content should be scrollable"
        vbar.setValue(vbar.maximum())
        qapp.processEvents()
        # The page at the top of the viewport can still be the previous one
        # when the last section is shorter than the viewport.
        assert view.current_page >= 8
        view.goto_page(9)
        qapp.processEvents()
        assert view.current_page == 9
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_scanned_pdf_reports_no_text(qapp, tmp_path):
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page(width=300, height=400)
        page.draw_rect(fitz.Rect(20, 20, 280, 380), color=(0, 0, 0),
                       fill=(0.3, 0.3, 0.3))
    path = str(tmp_path / "scanned.pdf")
    doc.save(path)
    doc.close()

    view = PDFView(path)
    view.resize(700, 500)
    view.show()
    try:
        ready = _enable_reflow(view, qapp)
        assert view._reflow_has_text is False
        assert view._reflow_empty_pages == 2
        assert ready and ready[-1] == (False, 2)
        assert view.grab().toImage().width() > 0
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_off_restores_normal_layout(qapp, sample_pdf):
    view = PDFView(sample_pdf)
    view.resize(700, 500)
    view.show()
    try:
        normal_height = view._content_h
        _enable_reflow(view, qapp)
        view.set_reflow_mode(False)
        qapp.processEvents()
        assert view.reflow_mode is False
        assert view._reflow_pages == {}
        assert view._content_h == normal_height
        assert view.verticalScrollBarPolicy() == view._vbar_policy_before_reflow
        assert len(view._disp_sizes) == view.page_count
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_reflow_shutdown_while_preparing_is_clean(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "big.pdf", pages=200)
    view = PDFView(path)
    view.resize(800, 600)
    view.show()
    try:
        view.set_reflow_mode(True)      # worker starts; do not wait
        view.shutdown()                 # must interrupt and join it
        assert view._reflow_worker is None
    finally:
        view.deleteLater()
        qapp.processEvents()


# ---------------------------------------------------------- main window
def test_main_window_reflow_is_per_tab(qapp, tmp_path):
    win = MainWindow()
    win.show()
    try:
        win.open_document(build_text_pdf(tmp_path / "a.pdf", pages=2))
        win.open_document(build_text_pdf(tmp_path / "b.pdf", pages=2))
        assert win.tabs.count() == 2

        win.tabs.setCurrentIndex(0)
        win._toggle_reflow()
        assert win.view.reflow_mode is True
        assert win.act_reflow.isChecked() is True

        # reflow belongs to the document, not to the window
        win.tabs.setCurrentIndex(1)
        assert win.view.reflow_mode is False
        assert win.act_reflow.isChecked() is False

        win.tabs.setCurrentIndex(0)
        assert win.act_reflow.isChecked() is True
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
    finally:
        win.close()
