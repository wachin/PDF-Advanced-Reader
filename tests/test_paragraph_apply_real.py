"""Phase 5.2 — real paragraph-dialog Apply/commit tests.

These drive the *real* :class:`ParagraphEditorDialog` (no stub) through the
real viewer event handler :meth:`PDFView._open_paragraph_editor`, exercising
the genuine Apply callback.  They catch the class of failure the Phase 5.1
stubbed tests could not: an Apply that silently does nothing (spec §1) and an
infeasible plan that must be explained rather than swallowed.
"""

import os

import fitz
import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from pdfar.font_discovery import FontFace
from pdfar.paragraph import (
    extract_paragraphs, paragraph_rejection_reason)
from pdfar.paragraph_dialog import ParagraphEditorDialog
from pdfar.text_edit_existing import FontSubstitution, find_text_at_point
from pdfar.viewer import PDFView


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


def _single_paragraph_pdf(path):
    """One editable paragraph (single content stream) with room to reflow."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    body = ("The quick brown fox jumps over the lazy dog near the river and "
            "the meadow while the sun sets slowly behind the distant hills "
            "where the birds return each evening to rest among the reeds and "
            "the tall grass sways gently in the cool night breeze that carries "
            "the scent of pine and damp earth through the quiet valley below "
            "where a small stream murmurs over smooth rounded stones.")
    page.insert_textbox(fitz.Rect(60, 100, 540, 360), body, fontsize=11.0,
                        fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def _multi_stream_pdf(path):
    """A paragraph whose lines live in separate content streams.

    ``insert_text`` per line appends a new content stream each time, which is
    exactly the case the engine refuses ("more than one content stream").
    """
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    lines = [
        "First line of a paragraph that is split across streams.",
        "Second line continues the same thought here.",
        "Third line ends the paragraph at this point.",
    ]
    for i, line in enumerate(lines):
        page.insert_text(fitz.Point(60, 120.0 + i * 16.0), line, fontsize=12.0,
                         fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def _bottom_paragraph_pdf(path):
    """A paragraph anchored near the page bottom, so a longer edit overflows.

    The body fits, but editing it into many more lines runs past the page
    bottom — the genuine overflow case the planner refuses.
    """
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    body = ("The quick brown fox jumps over the lazy dog near the river and "
            "the meadow while the sun sets slowly behind the distant hills.")
    page.insert_textbox(fitz.Rect(60, 700, 540, 790), body, fontsize=11.0,
                        fontname="helv")
    doc.save(path)
    doc.close()
    return str(path)


def _open_and_drive(view, page_no, hit, configure):
    """Open the real paragraph editor and run *configure(dlg)* while modal.

    ``configure`` performs the user actions (edit text, click a button).  The
    dialog is guaranteed to close afterwards: if *configure* neglects to, or an
    assertion inside it fails, the ``finally`` rejects it so the blocking
    :meth:`_open_paragraph_editor` can return (and the suite cannot hang).
    """
    captured = {}

    def interact():
        dlg = view.findChild(ParagraphEditorDialog)
        if dlg is not None:
            captured["dlg"] = dlg
            try:
                configure(dlg)
            finally:
                if dlg.isVisible():
                    dlg.reject()

    QTimer.singleShot(60, interact)
    view._open_paragraph_editor(page_no, hit)  # blocks until dialog closes
    return captured.get("dlg")


# --------------------------------------------------------------------------- #
# Feasible commit (the happy path the bug report is about)
# --------------------------------------------------------------------------- #
def test_real_dialog_apply_writes_text(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = extract_paragraphs(page)[0]
        new = " ".join(line.text for line in para.lines).replace("fox", "cat", 1)
        before = view.session.version
        dlg = _open_and_drive(view, 0, hit, lambda d: d._editor.setPlainText(new)
                              or d._apply_btn.click())
        assert dlg is not None
        # The edit actually reached the document.
        assert "cat" in view.session.doc[0].get_text()
        assert "fox" not in view.session.doc[0].get_text()
        assert view.session.version == before + 1
    finally:
        view.shutdown()


def test_real_dialog_apply_refreshes_viewer(qapp, tmp_path):
    """A successful Apply bumps the content version the viewer reads."""
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = extract_paragraphs(page)[0]
        new = " ".join(line.text for line in para.lines).replace("fox", "cat", 1)
        version_before = view.session.version

        def configure(dlg):
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        # The invalidated version the viewer shows matches the new document.
        assert view.session.version > version_before
        # The page text reflects the committed change (rendering reads this).
        assert "cat" in view.session.doc[0].get_text()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Infeasible plan — must explain, never silently do nothing (spec §1)
# --------------------------------------------------------------------------- #
def test_real_dialog_infeasible_explains_and_does_not_commit(qapp, tmp_path):
    path = _bottom_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        before = view.session.doc[0].get_text()
        version_before = view.session.version

        # Long enough that the reflow runs past the page bottom -> infeasible.
        long = " ".join(
            ["The quick brown fox jumps over the lazy dog near the river"] * 30)

        seen = {}

        def configure(dlg):
            dlg._editor.setPlainText(long)
            dlg._replan()  # make the plan (and the button) reflect infeasibility
            seen["feasible"] = dlg._plan.feasible if dlg._plan else None
            # Click Apply: the dialog must NOT accept and must NOT commit, but
            # must leave a clear explanation instead of doing nothing.
            dlg._apply_btn.click()
            seen["accepted"] = dlg.result() == int(QDialog.DialogCode.Accepted)
            seen["status"] = dlg._status.text()

        _open_and_drive(view, 0, hit, configure)
        assert seen["feasible"] is False
        assert seen["accepted"] is False
        assert seen["status"]  # a non-empty explanation was shown
        # Document is untouched: no commit, no version bump.
        assert view.session.doc[0].get_text() == before
        assert view.session.version == version_before
    finally:
        view.shutdown()


def test_real_dialog_infeasible_keeps_dialog_open(qapp, tmp_path):
    """Clicking Apply on an infeasible plan leaves the dialog up with reason."""
    path = _bottom_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))

        long = " ".join(
            ["The quick brown fox jumps over the lazy dog near the river"] * 30)

        seen = {}

        def configure(dlg):
            dlg._editor.setPlainText(long)
            dlg._replan()
            dlg._apply_btn.click()
            seen["accepted"] = dlg.result() == int(QDialog.DialogCode.Accepted)
            seen["status"] = dlg._status.text()

        _open_and_drive(view, 0, hit, configure)
        assert seen["accepted"] is False
        assert seen["status"]  # non-empty explanation present
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Cancel leaves the document exactly as it was
# --------------------------------------------------------------------------- #
def test_real_dialog_cancel_keeps_text(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        before = view.session.doc[0].get_text()

        def configure(dlg):
            dlg._editor.setPlainText("completely different text here")
            dlg.reject()

        _open_and_drive(view, 0, hit, configure)
        assert view.session.doc[0].get_text() == before
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Undo / redo through the real viewer after a real Apply
# --------------------------------------------------------------------------- #
def test_real_dialog_undo_redo(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = extract_paragraphs(page)[0]
        new = " ".join(line.text for line in para.lines).replace("fox", "cat", 1)

        def configure(dlg):
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        assert "cat" in view.session.doc[0].get_text()
        view.undo_edit()
        assert "cat" not in view.session.doc[0].get_text()
        assert "fox" in view.session.doc[0].get_text()
        view.redo_edit()
        assert "cat" in view.session.doc[0].get_text()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Save / reopen preserves the committed paragraph
# --------------------------------------------------------------------------- #
def test_real_dialog_save_and_reopen(qapp, tmp_path):
    from pdfar.save import save_document

    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = extract_paragraphs(page)[0]
        new = " ".join(line.text for line in para.lines).replace("fox", "cat")

        def configure(dlg):
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        assert "cat" in view.session.doc[0].get_text()
        saved = tmp_path / "saved.pdf"
        assert save_document(view.session, str(saved)) is not None

        reopened = fitz.open(str(saved))
        try:
            assert "cat" in reopened[0].get_text()
            assert "fox" not in reopened[0].get_text()
        finally:
            reopened.close()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Editor usability (spec §3): text wraps, no horizontal scrollbar
# --------------------------------------------------------------------------- #
def test_editor_wraps_to_width(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    doc = fitz.open(str(path))
    page = doc[0]
    para = extract_paragraphs(page)[0]
    dlg = ParagraphEditorDialog(None, page, para, page_num=0)
    try:
        assert (dlg._editor.lineWrapMode()
            == QPlainTextEdit.LineWrapMode.WidgetWidth)
        # A long, unwrapped line must not produce a horizontal scrollbar.
        dlg._editor.setPlainText(
            "A single very long line with no breaks that would overflow the "
            "editor horizontally if wrapping were disabled; with wrapping it "
            "must fold and only the vertical scrollbar (if any) appears.")
        dlg._editor.show()
        QApplication.processEvents()
        assert not dlg._editor.horizontalScrollBar().isVisible()
    finally:
        dlg.deleteLater()
        doc.close()


def test_editor_grows_for_long_paragraph(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    doc = fitz.open(str(path))
    page = doc[0]
    para = extract_paragraphs(page)[0]
    dlg = ParagraphEditorDialog(None, page, para, page_num=0)
    try:
        short = dlg._editor.minimumHeight()
        dlg._editor.setPlainText(
            " ".join(["A fairly long sentence keeps repeating over and over "
                      "again to make the paragraph several lines tall so the "
                      "editor should grow vertically to keep it readable."] * 6))
        QApplication.processEvents()
        grown = dlg._editor.minimumHeight()
        assert grown >= short
        # But it is clamped, not unbounded.
        assert dlg._editor.maximumHeight() == 320
    finally:
        dlg.deleteLater()
        doc.close()


# --------------------------------------------------------------------------- #
# Font substitution through the real Apply path (spec §1 test list)
# --------------------------------------------------------------------------- #
def test_real_dialog_apply_with_substitute_font(qapp, tmp_path):
    ttf = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    if not os.path.exists(ttf):
        pytest.skip("DejaVuSans.ttf not available")
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "quick"))
        para = extract_paragraphs(page)[0]
        new = " ".join(line.text for line in para.lines).replace("fox", "cat", 1)

        def configure(dlg):
            dlg._substitute = FontSubstitution(
                face=FontFace(path=ttf, index=0))
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        assert "cat" in view.session.doc[0].get_text()
    finally:
        view.shutdown()


# --------------------------------------------------------------------------- #
# Detection diagnostics (spec §2): why a word falls back to word editing
# --------------------------------------------------------------------------- #
def test_rejection_reason_for_multi_stream_paragraph(qapp, tmp_path):
    path = _multi_stream_pdf(tmp_path / "multi.pdf")
    doc = fitz.open(str(path))
    page = doc[0]
    hit = find_text_at_point(page, _word_center(page, "split"))
    reason = paragraph_rejection_reason(page, hit)
    assert reason is not None
    assert "content stream" in reason
    doc.close()


def test_rejection_reason_none_for_editable_paragraph(qapp, tmp_path):
    path = _single_paragraph_pdf(tmp_path / "p.pdf")
    doc = fitz.open(str(path))
    page = doc[0]
    hit = find_text_at_point(page, _word_center(page, "quick"))
    assert paragraph_rejection_reason(page, hit) is None
    doc.close()
