"""Headless integration of existing-text editing through the viewer + session.

These do not simulate real mouse events (fragile offscreen); they drive the
viewer's editing methods directly to prove the engine, the Edit, the session's
undo/redo and the paint overlay all hang together.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtCore import QPoint
from PyQt6.QtGui import QColor, QImage, QPainter

from pdfar.text_edit_existing import find_text_at_point
from pdfar.viewer import PDFView

from conftest import make_session


def _view_for(path):
    view = PDFView(path)
    view.resize(600, 800)
    view.show()
    return view


def _word_center(page, text):
    for w in page.get_text("words"):
        if w[4] == text:
            return ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)
    raise AssertionError(f"word {text!r} not found")


def test_edit_text_commit_and_undo(qapp, sample_pdf):
    view = _view_for(sample_pdf)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        cx, cy = _word_center(page, "PDFAR")
        hit = find_text_at_point(page, (cx, cy))
        assert hit is not None and hit.text == "PDFAR"

        # Open + commit via the viewer's own pipeline (applies through session).
        view._open_text_editor(0, hit)
        view._commit_text_edit("ACME")

        assert "ACME" in view.doc[0].get_text()
        assert "PDFAR" not in view.doc[0].get_text()

        # Undo restores the original.
        view.undo_edit()
        assert "PDFAR" in view.doc[0].get_text()
        assert "ACME" not in view.doc[0].get_text()

        # Redo re-applies.
        view.redo_edit()
        assert "ACME" in view.doc[0].get_text()
    finally:
        view.shutdown()


def test_edit_text_noop_when_unchanged(qapp, sample_pdf):
    view = _view_for(sample_pdf)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        cx, cy = _word_center(page, "PDFAR")
        hit = find_text_at_point(page, (cx, cy))
        view._open_text_editor(0, hit)
        # Commit the same text -> no edit should be created.
        before = view.doc[0].get_text()
        view._commit_text_edit("PDFAR")
        after = view.doc[0].get_text()
        assert before == after
    finally:
        view.shutdown()


def test_hover_overlay_paints_without_error(qapp, sample_pdf):
    view = _view_for(sample_pdf)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        cx, cy = _word_center(page, "PDFAR")
        hit = find_text_at_point(page, (cx, cy))
        view._hover_word = hit
        view._edit_hit = hit
        view._edit_preview = "PREVIEW"

        # Paint the overlay onto an offscreen image for page 0.
        img = QImage(600, 800, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        painter = QPainter(img)
        # Replicate the coordinates _paint_text_edit_overlay expects.
        px, py = view._page_origin(0)
        px -= view.horizontalScrollBar().value()
        py -= view.verticalScrollBar().value()
        view._paint_text_edit_overlay(painter, 0, px, py)
        painter.end()
        # No exception == success; the overlay ran.
    finally:
        view.shutdown()
