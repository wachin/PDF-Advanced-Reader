"""Phase 5 — paragraph editor dialog and preview (spec §3, §7).

The dialog is a thin, read-only planning surface over :mod:`pdfar.paragraph`;
these tests confirm it builds the right edit and reflects plan feasibility, line
count and substitution need.  They rely on the ``qapp`` session fixture (offscreen).
"""

from __future__ import annotations


from pdfar.paragraph import extract_paragraphs
from pdfar.paragraph_dialog import ParagraphEditorDialog
from pdfar.paragraph_edit import ReflowParagraph


def _para(session, index=0):
    return extract_paragraphs(session.doc[0])[index]


def _open(session):
    return ParagraphEditorDialog(
        None, session.doc[0], _para(session), page_num=0)


def test_dialog_seeds_space_joined_text(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    text = dlg._editor.toPlainText()
    assert text.strip()
    assert "\n" not in text  # reflowable form, no hard breaks


def test_dialog_initial_plan_feasible(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    assert dlg._plan is not None
    assert dlg._plan.feasible
    assert dlg._apply_btn.isEnabled()


def test_dialog_replans_shorter(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    dlg._editor.setPlainText("A short replacement line.")
    dlg._replan()
    assert dlg._plan.feasible
    assert dlg._plan.line_count_delta < 0


def test_dialog_replans_longer(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    base = dlg._editor.toPlainText()
    dlg._editor.setPlainText(
        base + (" Extra words that force the paragraph to grow onto more "
                "wrapped lines within the same region. ") * 4)
    dlg._replan()
    assert dlg._plan.feasible
    assert dlg._plan.line_count_delta > 0


def test_dialog_disables_apply_on_overflow(qapp, tmp_path):
    from tests.conftest import build_paragraph_pdf, make_session
    path = build_paragraph_pdf(tmp_path / "short.pdf", height=240,
                               region=(100, 100, 500, 220))
    sess = make_session(path)
    try:
        page = sess.doc[0]
        from pdfar.paragraph import extract_paragraphs
        para = extract_paragraphs(page)[0]
        dlg = ParagraphEditorDialog(None, page, para, page_num=0)
        dlg._editor.setPlainText(
            "An extremely long paragraph that needs far more vertical room than "
            "the page provides, " * 6)
        dlg._replan()
        assert not dlg._plan.feasible
        assert not dlg._apply_btn.isEnabled()
    finally:
        sess.close()


def test_dialog_build_returns_edit(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    edit = dlg.build()
    assert isinstance(edit, ReflowParagraph)
    para = _para(paragraph_session)
    assert edit.page_num == 0
    assert edit.first_index == para.first_index
    assert edit.last_index == para.last_index
    assert edit.anchor == para.text
    assert edit.new_text == dlg._editor.toPlainText()


def test_dialog_substitution_button(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    dlg._editor.setPlainText("Hello 😀 world")
    dlg._replan()
    assert dlg._plan.needs_substitution
    assert dlg._font_btn.isEnabled()


def test_dialog_accept_rejected_when_infeasible(qapp, tmp_path):
    from tests.conftest import build_paragraph_pdf, make_session
    path = build_paragraph_pdf(tmp_path / "short.pdf", height=240,
                               region=(100, 100, 500, 220))
    sess = make_session(path)
    try:
        page = sess.doc[0]
        from pdfar.paragraph import extract_paragraphs
        para = extract_paragraphs(page)[0]
        dlg = ParagraphEditorDialog(None, page, para, page_num=0)
        dlg._editor.setPlainText(
            "An extremely long paragraph that needs far more vertical room than "
            "the page provides. " * 8)
        dlg._replan()
        assert not dlg._plan.feasible
        # _on_accept must refuse to accept an infeasible plan.
        dlg._on_accept()
        assert dlg.result() != dlg.DialogCode.Accepted
    finally:
        sess.close()


def test_dialog_range_reextracts(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    para = _para(paragraph_session)
    # Narrow the range to a single line and re-extract.
    dlg._from.setValue(para.first_index + 1)
    dlg._to.setValue(para.first_index + 1)
    dlg._apply_range()
    assert dlg._para.line_count == 1


def test_preview_widget_paints(qapp, paragraph_session):
    dlg = _open(paragraph_session)
    preview = dlg._preview
    preview.set_plan(dlg._para, dlg._plan)
    # Exercise painting without crashing (offscreen).
    preview.resize(300, 200)
    preview.repaint()
