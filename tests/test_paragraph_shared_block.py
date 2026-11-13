"""Phase 5.4 — paragraph editing inside a BT/ET block shared with siblings.

The bug this file guards against: real content streams very often place
several paragraphs inside a *single* ``BT … ET`` block (one text region per
block is the norm for many PDF producers).  The earlier implementation refused
to edit any paragraph that shared its block with other text, and the viewer
swallowed that refusal as a transient message — so the Apply silently did
nothing and the user's edit was never saved.

These tests build PDFs where two paragraphs live in one ``BT … ET`` block and
exercise the real :class:`ParagraphEditorDialog` / :meth:`PDFView._open_paragraph_editor`
pipeline, plus the session Apply / save / reopen cycle, to prove the edit
commits, preserves the sibling paragraph, and survives close-and-reopen.
"""

import re

import fitz
import pytest
from PyQt6.QtCore import QTimer

from pdfar.paragraph import extract_paragraphs, LayoutOptions
from pdfar.paragraph_dialog import ParagraphEditorDialog
from pdfar.paragraph_edit import ReflowParagraph
from pdfar.save import save_document
from pdfar.session import DocumentSession
from pdfar.text_edit_existing import find_text_at_point
from pdfar.viewer import PDFView

TTF = ("/opt/ZCode/resources/glm/packages/pdf-plugin/dist/ui/pdfjs/"
       "standard_fonts/LiberationSans-Regular.ttf")


# --------------------------------------------------------------------------- #
# Fixture PDFs
# --------------------------------------------------------------------------- #
def _merge_two_boxes_into_one_block(doc, page, text_a, text_b):
    """Put two paragraphs into ONE BT/ET block (the sibling-layout case)."""
    page.insert_textbox(fitz.Rect(60, 100, 400, 230), text_a, fontsize=12.0,
                        fontname="helv", fontfile=TTF)
    page.insert_textbox(fitz.Rect(60, 330, 400, 470), text_b, fontsize=12.0,
                        fontname="helv", fontfile=TTF)
    res = doc.xref_get_key(page.xref, "Contents")
    xr = res[1] if isinstance(res, (tuple, list)) else res
    cxs = [int(t) for t in re.findall(r"(\d+) 0 R", xr)]
    s_a = doc.xref_stream(int(cxs[0]))
    s_b = doc.xref_stream(int(cxs[1]))
    bt = s_b.rfind(b"BT")
    et = s_b.rfind(b"ET")
    inner_b = s_b[bt + 2:et]
    a_et = s_a.rfind(b"ET")
    merged = s_a[:a_et] + b"\n" + inner_b + b"\n" + s_a[a_et:]
    doc.update_stream(int(cxs[0]), merged)
    doc.xref_set_key(page.xref, "Contents", f"{cxs[0]} 0 R")


def _shared_block_pdf(path):
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    a = ("Alpha paragraph first line of the first block that keeps on going. "
         "Alpha second line continues the same paragraph thought here.")
    b = ("Beta paragraph first line of the second block below the first one. "
         "Beta second line finishes the second paragraph block now.")
    _merge_two_boxes_into_one_block(doc, page, a, b)
    doc.save(str(path), garbage=2, deflate=True)
    doc.close()


def _three_line_shared_pdf(path):
    """One BT/ET with a 3-line paragraph followed by a 2-line paragraph."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    a = ("Gamma line one of the earlier paragraph that is fairly long so it "
         "wraps onto another line and then a third line for good measure. "
         "Gamma line three ends this opening paragraph block.")
    b = ("Delta line one of the later paragraph lower on the page. "
         "Delta line two closes the document out.")
    _merge_two_boxes_into_one_block(doc, page, a, b)
    doc.save(str(path), garbage=2, deflate=True)
    doc.close()


# --------------------------------------------------------------------------- #
# Non-GUI pipeline helpers
# --------------------------------------------------------------------------- #
def _apply_edit(session, para, new_text):
    edit = ReflowParagraph(
        page_num=0, first_index=para.first_index, last_index=para.last_index,
        new_text=new_text, anchor=para.text,
        options=LayoutOptions(allow_shrink=True, allow_expand=True))
    return session.apply(edit)


def _open_and_drive(view, page_no, hit, configure):
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


def _view_for(path):
    view = PDFView(str(path))
    view.resize(700, 900)
    view.show()
    return view


# --------------------------------------------------------------------------- #
# Pipeline (non-GUI) — the exact failure class, fixed
# --------------------------------------------------------------------------- #
def test_shared_block_edit_first_paragraph_persists(tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("Alpha", "Alfa"))
    assert "Alfa" in session.doc[0].get_text()
    assert "Alpha" not in session.doc[0].get_text()
    # The sibling paragraph is untouched.
    assert "Beta" in session.doc[0].get_text()


def test_shared_block_edit_second_paragraph_preserves_first(tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    b = [p for p in paras if p.first_index >= 2][0]
    _apply_edit(session, b, b.text.replace("Beta", "Beto"))
    assert "Beto" in session.doc[0].get_text()
    assert "Beta" not in session.doc[0].get_text()
    # The first paragraph is untouched.
    assert "Alpha" in session.doc[0].get_text()


def test_shared_block_save_and_reopen(tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("Alpha", "Alfa"))
    assert save_document(session, str(path)) is not None
    session.close()
    reopened = fitz.open(str(path))
    try:
        assert "Alfa" in reopened[0].get_text()
        assert "Beta" in reopened[0].get_text()
        assert "Alpha" not in reopened[0].get_text()
    finally:
        reopened.close()


def test_shared_block_three_line_paragraph(tmp_path):
    path = tmp_path / "three.pdf"
    _three_line_shared_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 3][0]
    assert len(a.lines) >= 3
    _apply_edit(session, a, a.text.replace("Gamma", "Gama"))
    assert "Gama" in session.doc[0].get_text()
    assert "Delta" in session.doc[0].get_text()
    assert "Gamma" not in session.doc[0].get_text()


def test_shared_block_save_as_leaves_original_unchanged(tmp_path):
    src = tmp_path / "shared.pdf"
    _shared_block_pdf(src)
    dst = tmp_path / "copy.pdf"
    session = DocumentSession(fitz.open(str(src)), str(src), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("Alpha", "Alfa"))
    assert save_document(session, str(dst)) is not None
    session.close()
    original = fitz.open(str(src))
    try:
        assert "Alfa" not in original[0].get_text()
        assert "Alpha" in original[0].get_text()
    finally:
        original.close()
    saved = fitz.open(str(dst))
    try:
        assert "Alfa" in saved[0].get_text()
    finally:
        saved.close()


def test_shared_block_reselect_after_save_and_edit_again(tmp_path):
    """The precise user report: edit, save, reopen, edit the same paragraph
    again, save.  Both edits must survive."""
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)

    # Edit 1: change a word.
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("Alpha", "Alfa"))
    assert save_document(session, str(path)) is not None
    session.close()

    # Reopen and edit the same paragraph again (now a sibling-sharing block).
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("paragraph", "section", 1))
    assert save_document(session, str(path)) is not None
    session.close()

    reopened = fitz.open(str(path))
    try:
        assert "Alfa" in reopened[0].get_text()
        assert "section" in reopened[0].get_text()
        assert "Beta" in reopened[0].get_text()
    finally:
        reopened.close()


def test_shared_block_undo_redo(tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    _apply_edit(session, a, a.text.replace("Alpha", "Alfa"))
    assert "Alfa" in session.doc[0].get_text()
    session.undo()
    assert "Alfa" not in session.doc[0].get_text()
    assert "Alpha" in session.doc[0].get_text()
    session.redo()
    assert "Alfa" in session.doc[0].get_text()
    session.close()


def test_shared_block_failed_apply_does_not_modify(tmp_path):
    """An infeasible reflow must not mark the document modified."""
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    session = DocumentSession(fitz.open(str(path)), str(path), "", 0)
    paras = extract_paragraphs(session.doc[0])
    a = [p for p in paras if p.first_index < 2][0]
    # Far too long to fit -> infeasible.
    long_text = "Alpha " * 400
    edit = ReflowParagraph(
        page_num=0, first_index=a.first_index, last_index=a.last_index,
        new_text=long_text, anchor=a.text,
        options=LayoutOptions(allow_shrink=False, allow_expand=False))
    version_before = session.version
    with pytest.raises(Exception):
        session.apply(edit)
    assert session.version == version_before
    assert "Alfa" not in session.doc[0].get_text()
    session.close()


# --------------------------------------------------------------------------- #
# Real GUI — drive the genuine dialog + viewer handler
# --------------------------------------------------------------------------- #
def test_real_dialog_shared_block_apply(qapp, tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "Alpha"))
        paras = extract_paragraphs(page)
        a = [p for p in paras if p.first_index < 2][0]
        new = a.text.replace("Alpha", "Alfa")
        dlg = _open_and_drive(view, 0, hit, lambda d: d._editor.setPlainText(new)
                              or d._apply_btn.click())
        assert dlg is not None
        assert "Alfa" in view.session.doc[0].get_text()
        assert "Alpha" not in view.session.doc[0].get_text()
        # Sibling preserved through the real pipeline.
        assert "Beta" in view.session.doc[0].get_text()
        assert view.session.version == 1
    finally:
        view.shutdown()


def test_real_dialog_shared_block_save_and_reopen(qapp, tmp_path):
    from pdfar.save import save_document
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "Alpha"))
        paras = extract_paragraphs(page)
        a = [p for p in paras if p.first_index < 2][0]
        new = a.text.replace("Alpha", "Alfa")

        def configure(dlg):
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        assert "Alfa" in view.session.doc[0].get_text()
        saved = tmp_path / "saved.pdf"
        assert save_document(view.session, str(saved)) is not None
        reopened = fitz.open(str(saved))
        try:
            assert "Alfa" in reopened[0].get_text()
            assert "Beta" in reopened[0].get_text()
        finally:
            reopened.close()
    finally:
        view.shutdown()


def test_real_dialog_shared_block_second_paragraph(qapp, tmp_path):
    path = tmp_path / "shared.pdf"
    _shared_block_pdf(path)
    view = _view_for(path)
    try:
        view.set_tool_mode("edit_text")
        page = view.session.doc[0]
        hit = find_text_at_point(page, _word_center(page, "Beta"))
        paras = extract_paragraphs(page)
        b = [p for p in paras if p.first_index >= 2][0]
        new = b.text.replace("Beta", "Beto")

        def configure(dlg):
            dlg._editor.setPlainText(new)
            dlg._apply_btn.click()

        _open_and_drive(view, 0, hit, configure)
        assert "Beto" in view.session.doc[0].get_text()
        assert "Beta" not in view.session.doc[0].get_text()
        assert "Alpha" in view.session.doc[0].get_text()
    finally:
        view.shutdown()


def _word_center(page, text):
    for w in page.get_text("words"):
        if w[4] == text:
            return ((w[0] + w[2]) / 2.0, (w[1] + w[3]) / 2.0)
    raise AssertionError(f"word {text!r} not found on page")
