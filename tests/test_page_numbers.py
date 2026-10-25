"""Page numbering: formats, layout, stamping, preview, and export flow."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pymupdf as fitz
from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from conftest import build_sample_pdf
from pdfar.main import MainWindow
from pdfar.page_numbers import (
    PageNumbersDialog, PageNumberSettings, layout_number, number_text,
    page_range, stamp_copy,
)


def _settings(**overrides) -> PageNumberSettings:
    values = dict(position="bottom-center", format="number/total",
                  font_family="helv", font_size=12, margin_x=20,
                  margin_y=20, start_page=1, end_page=0, start_number=1)
    values.update(overrides)
    return PageNumberSettings(**values)


def _spans(page):
    result = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                result.append(span)
    return result


def test_total_counts_only_numbered_pages():
    settings = _settings(start_page=2, end_page=4)
    assert page_range(settings, 5) == (1, 3)
    assert number_text(settings, 1, 3) == "1/3"
    assert number_text(settings, 3, 3) == "3/3"

    # Skipping the cover gives 1/4 … 4/4 on a five-page document.
    settings = _settings(start_page=2, end_page=5)
    assert page_range(settings, 5) == (1, 4)
    assert [number_text(settings, n, 4) for n in range(1, 5)] == [
        "1/4", "2/4", "3/4", "4/4"]


def test_page_range_is_clamped_and_empty_documents_are_safe():
    assert page_range(_settings(start_page=9, end_page=9), 3) == (2, 2)
    assert page_range(_settings(start_page=1, end_page=99), 3) == (0, 2)
    assert page_range(_settings(), 0) == (0, -1)


@pytest.mark.parametrize("fmt,number,total,expected", [
    ("number", 3, 9, "3"),
    ("number/total", 3, 9, "3/9"),
    ("page-number", 3, 9, "Page 3"),
    ("page-number/total", 3, 9, "Page 3 of 9"),
    ("dash-number", 3, 9, "- 3 -"),
])
def test_common_formats(fmt, number, total, expected):
    settings = _settings(format=fmt)
    assert number_text(settings, number, total) == expected
    text, _x, _y, _align = layout_number(settings, 200, 400, number, total)
    assert text == expected


def test_layout_anchor_offsets_and_glyph_edges():
    font = fitz.Font("helv")
    size = 12.0

    text, x, baseline, align = layout_number(
        _settings(position="top-left", margin_x=30, margin_y=25),
        300, 500, 2, 4, font=font)
    assert text == "2/4"
    assert (x, align) == (30, "left")
    assert baseline == pytest.approx(25 + font.ascender * size)

    _text, x, _y, align = layout_number(
        _settings(position="top-center"), 300, 500, 2, 4, font=font)
    assert (x, align) == (150, "center")

    _text, x, _y, align = layout_number(
        _settings(position="top-right", margin_x=30), 300, 500, 2, 4,
        font=font)
    assert (x, align) == (270, "right")

    # Bottom placement keeps the descender above the requested edge.
    _text, _x, baseline, align = layout_number(
        _settings(position="bottom-right", margin_y=25),
        300, 500, 2, 4, font=font)
    assert align == "right"
    assert baseline == pytest.approx(500 - 25 + font.descender * size)


def test_stamp_copy_numbers_a_copy_and_leaves_source_untouched(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=5, width=200,
                                height=300)
    before = open(src_path, "rb").read()
    out_path = tmp_path / "numbered.pdf"
    settings = _settings(start_page=2, end_page=5)

    with fitz.open(src_path) as src:
        assert stamp_copy(src, str(out_path), settings) == 4

    assert open(src_path, "rb").read() == before
    with fitz.open(out_path) as out:
        assert out.page_count == 5
        pages = [page.get_text() for page in out]
    assert "1/4" not in pages[0]
    for number, text in enumerate(pages[1:], start=1):
        assert f"{number}/4" in text


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_stamp_respects_pdf_rotation(tmp_path, rotation):
    src_path = tmp_path / "rotated.pdf"
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((10, 20), "content", fontsize=10)
    page.set_rotation(rotation)
    doc.save(src_path)
    doc.close()

    out_path = tmp_path / f"rotated-{rotation}.pdf"
    settings = _settings(position="bottom-center", margin_x=20, margin_y=20,
                         format="number")
    with fitz.open(src_path) as src:
        stamp_copy(src, str(out_path), settings)

    with fitz.open(out_path) as out:
        page = out[0]
        span = next(s for s in _spans(page) if s["text"] == "1")
        # Convert the text bbox from unrotated page space to the displayed
        # (rotated) page space and check the visual bottom-centre position.
        bbox = fitz.Rect(span["bbox"]) * page.rotation_matrix
        display_w, display_h = page.rect.width, page.rect.height
    assert bbox.x0 < display_w / 2 < bbox.x1
    assert bbox.y1 == pytest.approx(display_h - 20, abs=1.0)


def test_stamp_respects_cropbox(tmp_path):
    src_path = tmp_path / "cropped.pdf"
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.set_cropbox(fitz.Rect(50, 60, 350, 540))
    doc.save(src_path)
    doc.close()

    out_path = tmp_path / "cropped-numbered.pdf"
    settings = _settings(position="bottom-center", margin_x=20, margin_y=20,
                         format="number")
    with fitz.open(src_path) as src:
        stamp_copy(src, str(out_path), settings)

    with fitz.open(out_path) as out:
        page = out[0]
        span = next(s for s in _spans(page) if s["text"] == "1")
        bbox = fitz.Rect(span["bbox"]) * page.rotation_matrix
        assert page.rect.width == 300 and page.rect.height == 480
    assert bbox.x0 < 150 < bbox.x1
    assert bbox.y1 == pytest.approx(460, abs=1.0)


def test_numbered_copy_of_encrypted_pdf_is_decrypted(tmp_path):
    src_path = tmp_path / "secret.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "secret", fontsize=12)
    doc.save(src_path, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()

    out_path = tmp_path / "secret-numbered.pdf"
    with fitz.open(src_path) as src:
        assert src.authenticate("user")
        stamp_copy(src, str(out_path), _settings(format="number",
                                                 margin_x=10, margin_y=10))

    with fitz.open(out_path) as out:
        assert not out.needs_pass
        text = out[0].get_text()
    assert "secret" in text and "1" in text


def test_dialog_emits_settings_and_keeps_range_ordered(qapp):
    dialog = PageNumbersDialog(5, _settings(start_page=2, end_page=4))
    emitted = []
    dialog.settings_changed.connect(emitted.append)

    dialog.position_combo.setCurrentIndex(
        dialog.position_combo.findData("top-right"))
    dialog.size_spin.setValue(18)
    dialog.margin_x_spin.setValue(48)
    dialog.first_page_spin.setValue(3)

    settings = dialog.settings()
    assert settings.position == "top-right"
    assert settings.font_size == 18
    assert settings.margin_x == 48
    assert settings.start_page == 3
    assert emitted and emitted[-1] == settings
    assert "3–4" in dialog.range_label.text()

    # Moving the last page before the first keeps the range valid.
    dialog.last_page_spin.setValue(2)
    assert dialog.settings().start_page == 2
    assert dialog.settings().end_page == 2


def test_settings_roundtrip(qapp):
    saved = PageNumberSettings(
        position="top-left", format="page-number/total",
        font_family="tiro", font_size=17, margin_x=12, margin_y=34,
        start_page=2, end_page=7, start_number=3)
    saved.save()
    assert PageNumberSettings.load() == saved


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def _red_pixels(image) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() > 140 and color.green() < 110 and color.blue() < 110:
                count += 1
    return count


def _grab_first_page(view):
    x, y = view._page_origin(0)
    width, height = view._disp_sizes[0]
    rect = QRect(int(round(x)), int(round(y)), int(round(width)),
                 int(round(height)))
    return view.viewport().grab(rect).toImage()


def test_red_preview_is_painted_and_cleared(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=2))
    view = win.view
    qapp.processEvents()

    view.set_numbering_preview(_settings(font_size=30, margin_x=10,
                                         margin_y=10, format="number"))
    qapp.processEvents()
    assert _red_pixels(_grab_first_page(view)) > 10

    view.clear_numbering_preview()
    qapp.processEvents()
    assert _red_pixels(_grab_first_page(view)) == 0
    assert view._numbering_preview is None


def test_tools_action_saves_numbered_copy(win, qapp, tmp_path, monkeypatch):
    src = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(src)
    qapp.processEvents()
    before = open(src, "rb").read()
    out = tmp_path / "doc-numbered.pdf"
    opened = []

    def fake_exec(dialog):
        dialog.position_combo.setCurrentIndex(
            dialog.position_combo.findData("bottom-center"))
        dialog.format_combo.setCurrentIndex(
            dialog.format_combo.findData("number/total"))
        dialog.size_spin.setValue(14)
        dialog.first_page_spin.setValue(1)
        dialog.last_page_spin.setValue(3)
        dialog.first_number_spin.setValue(1)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(PageNumbersDialog, "exec", fake_exec)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(out), "")))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    monkeypatch.setattr(
        win, "open_document",
        lambda path, password="": opened.append(path) or True)

    win.act_page_numbers.trigger()
    qapp.processEvents()

    assert out.is_file()
    assert open(src, "rb").read() == before
    with fitz.open(out) as doc:
        texts = [page.get_text() for page in doc]
    assert "1/3" in texts[0] and "2/3" in texts[1] and "3/3" in texts[2]
    assert opened == []
    assert win.view._numbering_preview is None
    assert PageNumberSettings.load().font_size == 14


def test_tools_action_refuses_to_overwrite_the_open_document(
        win, qapp, tmp_path, monkeypatch):
    src = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    win.open_document(src)
    qapp.processEvents()
    before = open(src, "rb").read()
    warnings = []
    answers = iter([(src, ""), ("", "")])

    monkeypatch.setattr(
        PageNumbersDialog, "exec",
        lambda dialog: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: next(answers)))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args[2])))

    win.act_page_numbers.trigger()
    qapp.processEvents()

    assert warnings and "never overwrites" in warnings[0]
    assert open(src, "rb").read() == before
    assert win.view._numbering_preview is None
