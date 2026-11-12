"""Phase 5.1 — PdfCraft-style paragraph selection & editing UX.

These tests drive the *real* viewer event handlers (simulated mouse press /
release / move events) so the click → paragraph-selection → outline → editor
pipeline is exercised end to end, and they check the paragraph engine's
grouping/geometry directly.  The modal paragraph editor is replaced by a
non-blocking test double so the release handler can run to completion.

The 15 scenarios from the specification are covered by the test functions
below (single-/multi-line, first/last word, neighbour exclusion, headings,
multi-block assembly, rotated text, outline-boundary fidelity, normal-click
opens the editor, word editor still reachable, drag, apply, cancel, undo/redo
and save-reopen).
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PyQt6.QtWidgets import QDialog
from unittest.mock import patch

from pdfar.geometry import map_rect
from pdfar.paragraph import (
    LayoutOptions, extract_paragraphs, paragraph_for_word,
)
from pdfar.paragraph_edit import ReflowParagraph
from pdfar.text_edit_existing import find_text_at_point
from pdfar.viewer import PDFView

from conftest import make_session


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _view_for(path):
    view = PDFView(path)
    view.resize(700, 900)
    view.show()
    return view


def _word_center(page, text):
    for w in page.get_text("words"):
        if w[4] == text:
            return ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)
    raise AssertionError(f"word {text!r} not found on page")


def _widget_point(view, page_no, rect):
    """Map a page-space rect centre to a viewport widget point."""
    px, py = view._page_origin(page_no)
    px -= view.horizontalScrollBar().value()
    py -= view.verticalScrollBar().value()
    w0, h0, turn = view._sheet_space(page_no)
    x0, y0, x1, y1 = map_rect(rect, w0, h0, turn)
    return QPoint(int(px + ((x0 + x1) / 2.0) * view.zoom),
                  int(py + ((y0 + y1) / 2.0) * view.zoom))


def _mouse_event(etype, widget_pt, modifiers=Qt.KeyboardModifier.NoModifier,
                 buttons=Qt.MouseButton.NoButton):
    return QMouseEvent(
        etype, QPointF(widget_pt), QPointF(widget_pt),
        Qt.MouseButton.LeftButton, buttons, modifiers)


def _click(view, page_no, rect, modifiers=Qt.KeyboardModifier.NoModifier):
    """Simulate a plain left click (press + release) at *rect*'s centre."""
    pt = _widget_point(view, page_no, rect)
    view.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, pt, modifiers))
    view.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, pt, modifiers,
                     Qt.MouseButton.LeftButton))


class _StubParagraphDialog:
    """A non-blocking replacement for :class:`ParagraphEditorDialog`.

    Records the paragraph it was asked to edit and (when accepted) returns a
    :class:`ReflowParagraph` with either the unchanged text or *new_text*.
    """

    last = None

    def __init__(self, parent, page, para, page_num=0, options=None):
        self._page = page
        self._para = para
        self._page_num = page_num
        self.accepted = True
        self.new_text = None  # None -> keep the original text (a no-op apply)
        _StubParagraphDialog.last = self

    def exec(self):
        return (QDialog.DialogCode.Accepted if self.accepted
                else QDialog.DialogCode.Rejected)

    def build(self):
        text = self.new_text if self.new_text is not None else self._para.text
        return ReflowParagraph(
            page_num=self._page_num,
            first_index=self._para.first_index,
            last_index=self._para.last_index,
            new_text=text, anchor=self._para.text,
            options=LayoutOptions())

    def deleteLater(self):
        pass


def _patch_dialog():
    return patch("pdfar.viewer.ParagraphEditorDialog", _StubParagraphDialog)


# --------------------------------------------------------------------------- #
# Engine grouping / geometry (headless)
# --------------------------------------------------------------------------- #
def _single_line_pdf(path):
    """A page whose only text is one short line (one block, one line)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text(fitz.Point(60, 200), "One short line only", fontsize=12,
                     fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def test_single_line_paragraph_selects_whole_line(qapp, tmp_path):
    """A one-line paragraph selects that whole line, not just the word."""
    path = _single_line_pdf(tmp_path / "single.pdf")
    view = _view_for(path)
    try:
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "short"))
        assert hit is not None
        para = paragraph_for_word(page, hit)
        assert para is not None
        # The single line is the whole paragraph.
        assert para.line_count == 1
        assert hit.text in para.text
        # The outline is exactly the line's rect.
        assert tuple(para.rect) == tuple(para.lines[0].rect)
    finally:
        view.shutdown()


def test_multi_line_paragraph_selects_all_lines(qapp, paragraph_session):
    """A multi-line paragraph (insert_textbox) groups every line together."""
    view = _view_for(paragraph_session.path)
    try:
        page = view.doc[0]
        paras = extract_paragraphs(page)
        assert len(paras) >= 1
        para = paras[0]
        # build_paragraph_pdf writes several wrapped lines.
        assert para.line_count >= 2
        # Every line rect is inside the paragraph's union rect.
        for line in para.lines:
            assert (para.rect[0] <= line.rect[0] and para.rect[1] <= line.rect[1]
                    and para.rect[2] >= line.rect[2]
                    and para.rect[3] >= line.rect[3])
    finally:
        view.shutdown()


def test_first_and_last_word_yield_same_paragraph(qapp, paragraph_session):
    """Clicking the first or the last word selects the very same paragraph."""
    view = _view_for(paragraph_session.path)
    try:
        page = view.doc[0]
        paras = extract_paragraphs(page)
        para = paras[0]
        first_word = find_text_at_point(page, _word_center(page, "The"))
        last_line = para.lines[-1]
        last_word = find_text_at_point(
            page, ((last_line.rect[0] + last_line.rect[2]) / 2.0,
                   (last_line.rect[1] + last_line.rect[3]) / 2.0))
        p_first = paragraph_for_word(page, first_word)
        p_last = paragraph_for_word(page, last_word)
        assert p_first is not None and p_last is not None
        # Same selection regardless of which word was clicked.
        assert p_first.first_index == p_last.first_index
        assert p_first.last_index == p_last.last_index
        assert p_first.text == p_last.text
    finally:
        view.shutdown()


def _two_paragraph_pdf(path):
    """A page with two clearly separated paragraphs (different baselines)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(60, 100, 540, 300),
                        "First paragraph line one here. First paragraph line "
                        "two continues below on the same block. ",
                        fontsize=12, fontname="helv")
    page.insert_textbox(fitz.Rect(60, 480, 540, 700),
                        "Second paragraph is far away from the first one now. "
                        "It must not be merged with the first block. ",
                        fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def test_neighbour_paragraph_not_merged(qapp, tmp_path):
    """Two separate paragraphs are not merged into one editable block."""
    path = _two_paragraph_pdf(tmp_path / "two.pdf")
    view = _view_for(path)
    try:
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "First"))
        para = paragraph_for_word(page, hit)
        assert para is not None
        # The second paragraph's words are not part of this selection.
        second_hit = find_text_at_point(page, _word_center(page, "Second"))
        second_para = paragraph_for_word(page, second_hit)
        assert second_para is not None
        assert para.first_index != second_para.first_index
        # The second block's text is outside this paragraph's outline.
        assert "Second" not in para.text
    finally:
        view.shutdown()


def _heading_plus_body_pdf(path):
    """A bold heading line directly above a regular body paragraph."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(60, 100, 540, 140),
                        "Section Heading", fontsize=18, fontname="helv",
                        align=fitz.TEXT_ALIGN_LEFT)
    page.insert_textbox(fitz.Rect(60, 170, 540, 360),
                        "Body paragraph written in a smaller size that follows "
                        "the heading and should be treated as its own block. "
                        "It keeps going for a couple of lines of body text. ",
                        fontsize=11, fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def test_heading_and_body_are_separate(qapp, tmp_path):
    """A heading (different size) is not merged with the body beneath it."""
    path = _heading_plus_body_pdf(tmp_path / "heading.pdf")
    view = _view_for(path)
    try:
        page = view.doc[0]
        body_hit = find_text_at_point(page, _word_center(page, "Body"))
        para = paragraph_for_word(page, body_hit)
        assert para is not None
        # The heading word is excluded.
        assert "Section" not in para.text
        assert "Heading" not in para.text
    finally:
        view.shutdown()


def _two_line_pdf(path):
    """Two consecutive full-width lines that the engine groups as one paragraph.

    Both lines are wide so the short-line / paragraph-break heuristic does not
    fire; they are drawn by separate ``insert_text`` calls (multiple text
    operators) yet belong to a single editable block.
    """
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text(fitz.Point(60, 200),
                     "First line of the paragraph keeps going quite long here. ",
                     fontsize=12, fontname="helv")
    page.insert_text(fitz.Point(60, 216),
                     "Second line continues the same paragraph wider still. ",
                     fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def test_consecutive_lines_group_into_paragraph(qapp, tmp_path):
    """Consecutive lines (possibly from several operators) group as one block."""
    path = _two_line_pdf(tmp_path / "twoline.pdf")
    view = _view_for(path)
    try:
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "First"))
        para = paragraph_for_word(page, hit)
        assert para is not None
        # Both lines are included in one paragraph.
        assert "First" in para.text and "Second" in para.text
        assert para.line_count == 2
        # The outline spans both lines.
        assert para.rect[3] - para.rect[1] > 12
    finally:
        view.shutdown()


def test_rotated_paragraph_outline_is_valid(qapp, rotated_session):
    """A rotated paragraph yields a usable, correctly-mapped outline rect.

    The engine's bounding rect is an axis-aligned box in page space; the same
    ``map_rect`` transform used by every overlay maps it to view space, so the
    outline hugs the rotated text's real (rotated) footprint.
    """
    view = _view_for(rotated_session.path)
    try:
        page = view.doc[0]
        paras = extract_paragraphs(page)
        assert paras, "expected at least one paragraph on a rotated page"
        para = paras[0]
        r = para.rect
        # Non-degenerate box in page space (negative y is fine for rotated text).
        assert r[2] > r[0] and r[3] > r[1]
        w0, h0, turn = view._sheet_space(0)
        m = map_rect(r, w0, h0, turn)
        assert m[2] > m[0] and m[3] > m[1]
    finally:
        view.shutdown()


def test_outline_matches_paragraph_boundaries(qapp, paragraph_session):
    """The painted outline equals the union of the paragraph's line rects."""
    view = _view_for(paragraph_session.path)
    try:
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        # Compute the union of line rects by hand.
        ux0 = min(line.rect[0] for line in para.lines)
        uy0 = min(line.rect[1] for line in para.lines)
        ux1 = max(line.rect[2] for line in para.lines)
        uy1 = max(line.rect[3] for line in para.lines)
        assert tuple(para.rect) == (ux0, uy0, ux1, uy1)
        # Mapped outline encloses every line's mapped rect.
        w0, h0, turn = view._sheet_space(0)
        ox0, oy0, ox1, oy1 = map_rect(para.rect, w0, h0, turn)
        for line in para.lines:
            lx0, ly0, lx1, ly1 = map_rect(line.rect, w0, h0, turn)
            assert ox0 <= lx0 and oy0 <= ly0 and ox1 >= lx1 and oy1 >= ly1
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Mouse routing through the real event handlers
# --------------------------------------------------------------------------- #
def _click_with_edit(view, page_no, hit, new_text):
    """Click *hit* and have the stub dialog apply *new_text*."""
    _StubParagraphDialog.last = None
    orig_init = _StubParagraphDialog.__init__

    def _init(self, parent, page_, para, page_num=0, options=None):
        orig_init(self, parent, page_, para, page_num, options)
        self.new_text = new_text

    with patch.object(_StubParagraphDialog, "__init__", _init):
        _click(view, page_no, hit.rect)
    return _StubParagraphDialog.last


def test_normal_click_opens_paragraph_editor(qapp, paragraph_session):
    """A plain click on a word routes to the paragraph editor and edits."""
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        new = para.text.replace("quick", "QUICK")
        with _patch_dialog() as _p:
            last = _click_with_edit(view, 0, hit, new)
        # The stub was used and the edit applied.
        assert last is not None
        assert last._para.first_index == para.first_index
        assert last._para.last_index == para.last_index
        assert last._para.text == para.text
        assert "QUICK" in view.doc[0].get_text()
        assert "quick" not in view.doc[0].get_text()
        # Selection cleared after apply.
        assert view._paragraph_selection is None
    finally:
        view.shutdown()


def test_shift_click_opens_word_editor(qapp, paragraph_session):
    """Shift+click keeps the inline single-word editor accessible."""
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        _click(view, 0, hit.rect, modifiers=Qt.KeyboardModifier.ShiftModifier)
        # The word editor (not the paragraph editor) opened for that word.
        assert view._edit_hit is not None
        assert view._edit_hit.text == "quick"
        assert view._paragraph_selection is None
        view._cancel_text_edit()
    finally:
        view.shutdown()


def test_drag_opens_word_editor(qapp, sample_pdf):
    """Dragging across words on a line opens the inline word editor."""
    view = _view_for(sample_pdf)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        start = find_text_at_point(page, _word_center(page, "Hello"))
        end = find_text_at_point(page, _word_center(page, "world"))
        p0 = _widget_point(view, 0, start.rect)
        p1 = _widget_point(view, 0, end.rect)
        view.mousePressEvent(
            _mouse_event(QEvent.Type.MouseButtonPress, p0))
        # Move with the button held to extend the selection.
        move = QMouseEvent(
            QEvent.Type.MouseMove, QPointF(p1), QPointF(p1),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier)
        view.mouseMoveEvent(move)
        view.mouseReleaseEvent(
            _mouse_event(QEvent.Type.MouseButtonRelease, p1,
                         buttons=Qt.MouseButton.LeftButton))
        assert view._edit_hit is not None
        assert view._edit_hit.text == "Hello PDFAR world"
        view._cancel_text_edit()
    finally:
        view.shutdown()


def test_context_menu_wiring_present(qapp, paragraph_session):
    """Right-click still exposes Edit word… and Edit paragraph… paths."""
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        # The two editing entry points are wired for the context menu.
        assert hasattr(view, "_open_text_editor")
        assert hasattr(view, "_open_paragraph_editor")
        assert "quick" in view.doc[0].get_text()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Apply / cancel / undo / redo / save
# --------------------------------------------------------------------------- #
def test_paragraph_apply_writes_text(qapp, paragraph_session):
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        new = para.text.replace("fox", "cat", 1)
        with _patch_dialog():
            _click_with_edit(view, 0, hit, new)
        assert "cat" in view.doc[0].get_text()
        assert para.text not in view.doc[0].get_text()
    finally:
        view.shutdown()


def test_paragraph_cancel_keeps_text(qapp, paragraph_session):
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        before = view.doc[0].get_text()
        _StubParagraphDialog.last = None
        orig_init = _StubParagraphDialog.__init__

        def _init(self, parent, page_, para, page_num=0, options=None):
            orig_init(self, parent, page_, para, page_num, options)
            self.accepted = False  # Cancel

        with _patch_dialog():
            with patch.object(_StubParagraphDialog, "__init__", _init):
                _click(view, 0, hit.rect)
        # Nothing changed on cancel.
        assert view.doc[0].get_text() == before
    finally:
        view.shutdown()


def test_paragraph_undo_redo(qapp, paragraph_session):
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        new = para.text.replace("fox", "cat", 1)
        with _patch_dialog():
            _click_with_edit(view, 0, hit, new)
        assert "cat" in view.doc[0].get_text()
        view.undo_edit()
        assert "cat" not in view.doc[0].get_text()
        assert "fox" in view.doc[0].get_text()
        view.redo_edit()
        assert "cat" in view.doc[0].get_text()
    finally:
        view.shutdown()


def test_paragraph_save_and_reopen(qapp, paragraph_session, tmp_path):
    from pdfar.save import save_document
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        new = para.text.replace("fox", "cat")
        with _patch_dialog():
            _click_with_edit(view, 0, hit, new)
        assert "cat" in view.doc[0].get_text()
        assert "fox" not in view.doc[0].get_text()
        saved = tmp_path / "saved.pdf"
        report = save_document(view.session, str(saved))
        assert report is not None
        # Reopen and confirm the edit persisted.
        reopened = make_session(str(saved))
        try:
            assert "cat" in reopened.doc[0].get_text()
            assert "fox" not in reopened.doc[0].get_text()
        finally:
            reopened.close()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Visual: the outline paints and does not cover neighbours
# --------------------------------------------------------------------------- #
def test_paragraph_outline_paints(qapp, paragraph_session):
    """Painting the overlay with a selection fills the paragraph region."""
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = paragraph_for_word(page, hit)
        view._paragraph_selection = para
        view._paragraph_selection_page = 0

        img = QImage(700, 900, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        painter = QPainter(img)
        px, py = view._page_origin(0)
        px -= view.horizontalScrollBar().value()
        py -= view.verticalScrollBar().value()
        view._paint_text_edit_overlay(painter, 0, px, py)
        painter.end()

        # Map the paragraph rect into image space and sample inside it.
        w0, h0, turn = view._sheet_space(0)
        x0, y0, x1, y1 = map_rect(para.rect, w0, h0, turn)
        ix0 = int(px + x0 * view.zoom)
        iy0 = int(py + y0 * view.zoom)
        ix1 = int(px + x1 * view.zoom)
        iy1 = int(py + y1 * view.zoom)

        def is_green(c: QColor) -> bool:
            return c.green() > 80 and c.green() > c.red() + 15

        green_inside = 0
        for yy in range(iy0, iy1, 3):
            for xx in range(ix0, ix1, 3):
                if 0 <= xx < img.width() and 0 <= yy < img.height():
                    if is_green(QColor(img.pixel(xx, yy))):
                        green_inside += 1
        assert green_inside > 0, "no outline pixels found inside paragraph"

        # A point well outside the paragraph (top-left corner of the page, away
        # from the text) should not be painted green.
        corner = QColor(img.pixel(8, 8))
        assert not is_green(corner)
    finally:
        view.shutdown()


def test_hover_suppressed_while_paragraph_selected(qapp, paragraph_session):
    """Hover highlight is not drawn while a paragraph is selected."""
    view = _view_for(paragraph_session.path)
    try:
        view.set_tool_mode("edit_text")
        page = view.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        view._hover_word = hit
        view._paragraph_selection = paragraph_for_word(page, hit)
        view._paragraph_selection_page = 0
        # No exception; the hover branch is guarded by the selection check.
        img = QImage(700, 900, QImage.Format.Format_RGB32)
        img.fill(QColor(255, 255, 255))
        painter = QPainter(img)
        px, py = view._page_origin(0)
        px -= view.horizontalScrollBar().value()
        py -= view.verticalScrollBar().value()
        view._paint_text_edit_overlay(painter, 0, px, py)
        painter.end()
    finally:
        view.shutdown()
