"""Phase 4: the font-substitution dialog.

Headless (offscreen) checks that the dialog explains the problem, lists the
ranked alternatives, previews the chosen face, warns about width changes and
never accepts a font it cannot embed or that lacks glyphs.
"""

from __future__ import annotations

import glob
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from PyQt6.QtWidgets import QDialog

from fontTools.ttLib import TTFont

from pdfar import font_discovery as fd
from pdfar import font_picker
from pdfar.font_discovery import FontMatch, face_from_file
from pdfar.font_picker import FontSubstitutionDialog
from pdfar.text_edit_existing import FontIssue


def _find(*names):
    for name in names:
        hits = glob.glob(f"/usr/share/fonts/**/{name}", recursive=True)
        if hits:
            return hits[0]
    return None


DEJAVU = _find("DejaVuSans.ttf")
DEJAVU_BOLD = _find("DejaVuSans-Bold.ttf")

needs_dejavu = pytest.mark.skipif(DEJAVU is None,
                                  reason="DejaVuSans.ttf is not installed")


def _cff_font():
    for path in glob.glob("/usr/share/fonts/**/*.otf", recursive=True):
        try:
            font = TTFont(path, lazy=True)
            try:
                if "CFF " in font.keys():
                    return path
            finally:
                font.close()
        except Exception:
            continue
    return None


@pytest.fixture(autouse=True)
def _fresh_cache():
    fd.clear_cache()
    yield
    fd.clear_cache()


def _issue(**kwargs):
    defaults = dict(
        reason="Character 'ñ' has no glyph in the embedded font.",
        text="Café ñandú", font_name="ABCDEF+DejaVu Sans",
        font_type="Type0", embedded=True, subsetted=True, missing=("ñ",))
    defaults.update(kwargs)
    return FontIssue(**defaults)


def _matches():
    regular = FontMatch(face=face_from_file(DEJAVU), score=100.0,
                        width_ratio=1.25, notes=())
    bold = FontMatch(
        face=face_from_file(DEJAVU_BOLD), score=90.0, width_ratio=0.95,
        notes=("licence forbids subsetting; the whole font is embedded",))
    return [regular, bold]


@needs_dejavu
def test_dialog_lists_matches_and_preselects_best(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", _matches())
    try:
        assert dialog.table.rowCount() == 2
        assert dialog.table.item(0, 0).text() == "DejaVu Sans"
        assert dialog.table.currentRow() == 0
        match = dialog.selected_match()
        assert match is not None and match.score == 100.0
        assert dialog.use_button.isEnabled()
        assert dialog.chosen_face() is None  # nothing accepted yet
    finally:
        dialog.close()


@needs_dejavu
def test_dialog_shows_problem_and_font_state(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", _matches())
    try:
        texts = [label.text() for label in dialog.findChildren(type(dialog.preview))]
        joined = " ".join(texts)
        assert "ABCDEF+DejaVu Sans" in joined
        assert "embedded" in joined and "subset" in joined
        assert "U+00F1" in joined  # ñ
        assert "no glyph" in joined
    finally:
        dialog.close()


@needs_dejavu
def test_dialog_accept_returns_selected_face(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", _matches())
    try:
        dialog.table.selectRow(1)
        dialog._accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        face = dialog.chosen_face()
        assert face is not None
        assert face.bold is True
    finally:
        dialog.close()


@needs_dejavu
def test_dialog_cancel_returns_no_face(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", _matches())
    try:
        dialog.reject()
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert dialog.chosen_face() is None
    finally:
        dialog.close()


def test_dialog_without_matches_disables_use(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "ñ", [])
    try:
        assert dialog.table.rowCount() == 0
        assert dialog.selected_match() is None
        assert dialog.use_button.isEnabled() is False
    finally:
        dialog.close()


def test_width_label_formats_ratio():
    make = FontSubstitutionDialog._width_label
    assert make(FontMatch(face=None, score=1, width_ratio=1.0)) == "—"
    assert make(FontMatch(face=None, score=1, width_ratio=1.25)) == "+25%"
    assert make(FontMatch(face=None, score=1, width_ratio=0.80)) == "-20%"


@needs_dejavu
def test_preview_updates_with_selection_and_warns_about_width(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", _matches())
    try:
        dialog.table.selectRow(0)
        assert "wider" in dialog.width_note.text()
        dialog.table.selectRow(1)
        # 0.95 is 5% narrower: the width warning and the licence note appear.
        assert "narrower" in dialog.width_note.text()
        assert "whole font" in dialog.width_note.text()
    finally:
        dialog.close()


@needs_dejavu
def test_browse_accepts_a_covering_font(qapp, monkeypatch):
    dialog = FontSubstitutionDialog(None, _issue(), "Café ñandú", [])
    try:
        monkeypatch.setattr(
            font_picker.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (DEJAVU, "")))
        dialog._browse()
        assert dialog.result() == QDialog.DialogCode.Accepted
        face = dialog.chosen_face()
        assert face is not None and face.path == DEJAVU
        assert face.source == "user"
    finally:
        dialog.close()


@needs_dejavu
def test_browse_rejects_a_font_without_glyphs(qapp, monkeypatch):
    dialog = FontSubstitutionDialog(None, _issue(text="漢"), "漢", [])
    try:
        monkeypatch.setattr(
            font_picker.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (DEJAVU, "")))
        dialog._browse()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "cannot draw" in dialog.width_note.text()
        assert dialog.chosen_face() is None
    finally:
        dialog.close()


@needs_dejavu
def test_browse_rejects_a_restricted_licence(qapp, monkeypatch, tmp_path):
    source = str(tmp_path / "restricted.ttf")
    font = TTFont(DEJAVU)
    try:
        font["OS/2"].fsType = 0x0002
        font.save(source)
    finally:
        font.close()
    dialog = FontSubstitutionDialog(None, _issue(), "Café", [])
    try:
        monkeypatch.setattr(
            font_picker.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (source, "")))
        dialog._browse()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "does not allow embedding" in dialog.width_note.text()
    finally:
        dialog.close()


def test_browse_rejects_cff_outlines(qapp, monkeypatch):
    path = _cff_font()
    if path is None:
        pytest.skip("no CFF/OpenType font installed")
    dialog = FontSubstitutionDialog(None, _issue(), "Café", [])
    try:
        monkeypatch.setattr(
            font_picker.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: (path, "")))
        dialog._browse()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert "TrueType" in dialog.width_note.text()
    finally:
        dialog.close()


@needs_dejavu
def test_browse_cancel_keeps_dialog_open(qapp, monkeypatch):
    dialog = FontSubstitutionDialog(None, _issue(), "Café", _matches())
    try:
        monkeypatch.setattr(
            font_picker.QFileDialog, "getOpenFileName",
            staticmethod(lambda *a, **k: ("", "")))
        dialog._browse()
        assert dialog.result() == 0  # still open
        assert dialog.chosen_face() is None
    finally:
        dialog.close()


@needs_dejavu
def test_accept_without_selection_does_nothing(qapp):
    dialog = FontSubstitutionDialog(None, _issue(), "Café", [])
    try:
        dialog._accept()
        assert dialog.result() == 0
        assert dialog.chosen_face() is None
    finally:
        dialog.close()
