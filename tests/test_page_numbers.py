"""Page numbering: formats, layout, stamping, preview, and export flow."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pymupdf as fitz
from PyQt6.QtCore import (
    QEvent, QPoint, QPointF, QRect, QSettings, QSize, Qt,
)
from PyQt6.QtGui import QColor, QImage, QMouseEvent
from PyQt6.QtWidgets import QColorDialog, QFileDialog, QMessageBox

from conftest import build_sample_pdf
from pdfar import APP_NAME
from pdfar.geometry import map_point
from pdfar.main import MainWindow
from pdfar.page_numbers import (
    MASK_COLORS, EraseSettings, PageNumbersPanel, PageNumberSettings,
    ScreenColorPicker, box_to_settings, erase_box, erase_copy,
    erased_copy_path, layout_number, number_box, number_text, page_range,
    points_to_unit, sample_screen_color, stamp_copy, unit_to_points,
)


def _settings(**overrides) -> PageNumberSettings:
    values = dict(position="bottom-center", format="number/total",
                  font_family="helv", font_size=12, margin_x=20,
                  margin_y=20, start_page=1, end_page=0, start_number=1,
                  mask_left=4, mask_right=4, mask_top=4, mask_bottom=4,
                  mask_color="#ffffff")
    values.update(overrides)
    return PageNumberSettings(**values)


def _erase_settings(**overrides) -> EraseSettings:
    values = dict(position="top-left", width=80, height=20, margin_x=10,
                  margin_y=10, start_page=1, end_page=0, unit="pt",
                  color="#ffffff")
    values.update(overrides)
    return EraseSettings(**values)


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


def _white_drawings(page):
    masks = []
    for drawing in page.get_drawings():
        fill = drawing.get("fill")
        if fill and all(abs(channel - 1.0) < 0.01 for channel in fill):
            masks.append(fitz.Rect(drawing["rect"]))
    return masks


def test_stamp_draws_mask_behind_the_number(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=1,
                                width=200, height=300)
    with fitz.open(src_path) as src:
        stamped = tmp_path / "masked.pdf"
        stamp_copy(src, str(stamped), _settings(format="number"))

    with fitz.open(stamped) as out:
        page = out[0]
        span = next(s for s in _spans(page) if s["text"] == "1")
        bbox = fitz.Rect(span["bbox"])
        masks = _white_drawings(page)
    assert masks
    assert any(mask.contains(bbox) for mask in masks)
    assert masks[0].width > bbox.width
    assert masks[0].height > bbox.height

    with fitz.open(src_path) as src:
        plain = tmp_path / "unmasked.pdf"
        stamp_copy(src, str(plain), _settings(format="number", mask=False))
    with fitz.open(plain) as out:
        assert _white_drawings(out[0]) == []


def test_mask_margins_expand_each_side():
    settings = _settings(mask=True, mask_left=2, mask_right=6,
                         mask_top=10, mask_bottom=1)
    box = number_box(settings, 200, 300, 1, 1)
    x0, y0, x1, y1 = box.mask_rect
    assert x0 == pytest.approx(box.left - 2)
    assert x1 == pytest.approx(box.right + 6)
    assert y0 == pytest.approx(box.top - 10)
    assert y1 == pytest.approx(box.bottom + 1)

    plain = number_box(_settings(mask=False, mask_left=30), 200, 300, 1, 1)
    assert plain.mask_rect == (plain.left, plain.top,
                               plain.right, plain.bottom)


def test_stamp_uses_per_side_mask_margins(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=1,
                                width=200, height=300)
    settings = _settings(format="number", margin_x=40, margin_y=40,
                         mask_left=1, mask_right=9, mask_top=3,
                         mask_bottom=15)
    with fitz.open(src_path) as src:
        stamped = tmp_path / "masked-sides.pdf"
        stamp_copy(src, str(stamped), settings)

    with fitz.open(stamped) as out:
        masks = _white_drawings(out[0])
    assert len(masks) == 1
    expected = fitz.Rect(number_box(settings, 200, 300, 1, 1).mask_rect)
    assert masks[0].x0 == pytest.approx(expected.x0, abs=0.02)
    assert masks[0].y0 == pytest.approx(expected.y0, abs=0.02)
    assert masks[0].x1 == pytest.approx(expected.x1, abs=0.02)
    assert masks[0].y1 == pytest.approx(expected.y1, abs=0.02)


def test_stamp_uses_the_chosen_mask_colour(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=1,
                                width=200, height=300)
    settings = _settings(format="number", margin_x=40, margin_y=40,
                         mask_color="#00ff80")
    with fitz.open(src_path) as src:
        stamped = tmp_path / "masked-colour.pdf"
        stamp_copy(src, str(stamped), settings)

    with fitz.open(stamped) as out:
        fills = [drawing["fill"] for drawing in out[0].get_drawings()
                 if drawing.get("fill")]
    rounded = [tuple(round(channel, 2) for channel in fill) for fill in fills]
    assert (0.0, 1.0, 0.5) in rounded


def test_erase_box_places_a_blank_rect_inside_the_page():
    box = erase_box(_erase_settings(position="top-left", width=80, height=20,
                                   margin_x=10, margin_y=12), 200, 300)
    assert (box.left, box.top, box.right, box.bottom) == (10, 12, 90, 32)
    assert box.mask_rect == (10, 12, 90, 32)
    assert box.text == ""

    top_right = erase_box(_erase_settings(
        position="top-right", margin_x=10, margin_y=12), 200, 300)
    assert (top_right.left, top_right.right) == (110, 190)

    centred = erase_box(_erase_settings(
        position="top-center", margin_x=99), 200, 300)
    assert centred.left == pytest.approx(60)

    bottom = erase_box(_erase_settings(
        position="bottom-left", margin_y=12), 200, 300)
    assert (bottom.top, bottom.bottom) == (268, 288)

    clamped = erase_box(_erase_settings(
        width=500, height=500, margin_x=50, margin_y=50), 200, 300)
    assert (clamped.left, clamped.top, clamped.right,
            clamped.bottom) == (0, 0, 200, 300)

    edge = erase_box(_erase_settings(margin_x=500, margin_y=500), 200, 300)
    assert (edge.right, edge.bottom) == (200, 300)


def test_erased_copy_path_sits_next_to_the_source():
    assert erased_copy_path("/tmp/Book.PDF") == "/tmp/Book-erased.PDF"
    assert erased_copy_path("/tmp/Book") == "/tmp/Book-erased.pdf"


def test_erase_copy_covers_only_the_selected_pages(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=4,
                                width=200, height=300)
    before = open(src_path, "rb").read()
    out_path = tmp_path / "erased.pdf"
    settings = _erase_settings(width=90, height=30, margin_x=12,
                               margin_y=9, start_page=2, end_page=3,
                               color="#00ff40")

    with fitz.open(src_path) as src:
        assert erase_copy(src, str(out_path), settings) == 2

    assert open(src_path, "rb").read() == before
    green = (0.0, 1.0, 0.25)
    with fitz.open(out_path) as out:
        assert out.page_count == 4
        fills = [
            [tuple(round(channel, 2) for channel in drawing["fill"])
             for drawing in page.get_drawings() if drawing.get("fill")]
            for page in out
        ]
        expected = fitz.Rect(erase_box(settings, 200, 300).mask_rect)
        covered = [fitz.Rect(drawing["rect"])
                   for drawing in out[1].get_drawings()
                   if drawing.get("fill")]
    assert green not in fills[0]
    assert green in fills[1] and green in fills[2]
    assert green not in fills[3]
    assert any(abs(rect.x0 - expected.x0) < 0.02
               and abs(rect.y1 - expected.y1) < 0.02
               for rect in covered)


def test_erase_copy_needs_a_non_zero_mask(tmp_path):
    src_path = build_sample_pdf(tmp_path / "doc.pdf", pages=1)
    with fitz.open(src_path) as src:
        with pytest.raises(ValueError):
            erase_copy(src, str(tmp_path / "x.pdf"),
                       _erase_settings(height=0))


def test_box_to_settings_moves_and_reanchors():
    settings = _settings(position="bottom-center", margin_x=20, margin_y=20)
    box = number_box(settings, 200, 300, 1, 1)

    moved = box_to_settings(settings, 200, 300, box, 12, 0)
    assert moved.position == "bottom-right"
    assert moved.margin_y == pytest.approx(settings.margin_y)
    assert moved.margin_x > settings.margin_x

    clamped = box_to_settings(settings, 200, 300, box, 10000, 10000)
    moved_box = number_box(clamped, 200, 300, 1, 1)
    assert moved_box.left >= -0.01
    assert moved_box.right <= 200.01
    assert moved_box.bottom <= 300.01


def test_dragging_across_a_tall_page_keeps_large_offsets(qapp):
    settings = _settings(position="bottom-left", margin_x=20, margin_y=20,
                         mask=True)
    box = number_box(settings, 612, 792, 1, 1)
    moved = box_to_settings(settings, 612, 792, box, 0, -700)
    assert moved.margin_y > 300

    panel = PageNumbersPanel(1, moved)
    assert panel.settings().margin_y == pytest.approx(moved.margin_y)
    assert panel.settings().margin_x == pytest.approx(moved.margin_x)


def test_panel_emits_settings_and_keeps_range_ordered(qapp):
    panel = PageNumbersPanel(5, _settings(start_page=2, end_page=4))
    emitted = []
    panel.settings_changed.connect(emitted.append)

    panel.position_combo.setCurrentIndex(
        panel.position_combo.findData("top-right"))
    panel.size_spin.setValue(18)
    panel.margin_x_spin.setValue(48)
    panel.first_page_spin.setValue(3)

    settings = panel.settings()
    assert settings.position == "top-right"
    assert settings.font_size == 18
    assert settings.margin_x == 48
    assert settings.start_page == 3
    assert emitted and emitted[-1] == settings
    assert "3–4" in panel.range_label.text()

    # Moving the last page before the first keeps the range valid.
    panel.last_page_spin.setValue(2)
    assert panel.settings().start_page == 2
    assert panel.settings().end_page == 2


def test_panel_unit_switch_converts_offsets(qapp):
    panel = PageNumbersPanel(
        1, _settings(margin_x=72, margin_y=36, mask_left=6, mask_right=12,
                     mask_top=18, mask_bottom=24))
    assert panel.margin_x_spin.value() == pytest.approx(72)
    assert panel.margin_x_spin.suffix().strip() == "pt"
    assert panel.mask_left_spin.value() == pytest.approx(6)

    panel.unit_combo.setCurrentIndex(panel.unit_combo.findData("cm"))

    assert panel.margin_x_spin.suffix().strip() == "cm"
    assert panel.margin_x_spin.value() == pytest.approx(2.54, abs=0.01)
    assert panel.mask_bottom_spin.suffix().strip() == "cm"
    assert panel.mask_bottom_spin.value() == pytest.approx(0.85, abs=0.01)
    assert panel.erase_width_spin.suffix().strip() == "cm"
    assert panel.erase_width_spin.value() == pytest.approx(
        points_to_unit(150, "cm"), abs=0.01)
    settings = panel.settings()
    assert settings.unit == "cm"
    assert settings.margin_x == pytest.approx(72)
    assert settings.margin_y == pytest.approx(36)
    assert settings.mask_left == pytest.approx(6)
    assert settings.mask_right == pytest.approx(12)
    assert settings.mask_top == pytest.approx(18)
    assert settings.mask_bottom == pytest.approx(24)
    assert panel.erase_settings().width == pytest.approx(150)
    assert panel.erase_settings().height == pytest.approx(24)


def test_panel_mask_toggle_and_page_count(qapp):
    panel = PageNumbersPanel(0, _settings(mask=True))
    assert not panel.save_btn.isEnabled()

    panel.mask_check.setChecked(False)
    for widget in (panel.mask_left_spin, panel.mask_right_spin,
                   panel.mask_top_spin, panel.mask_bottom_spin,
                   panel.mask_color_btn, panel.pick_color_btn,
                   *panel.swatch_buttons):
        assert not widget.isEnabled()
    assert panel.settings().mask is False

    panel.set_page_count(7)
    assert panel.save_btn.isEnabled()
    assert panel.last_page_spin.maximum() == 7
    panel.set_page_count(0)
    assert not panel.save_btn.isEnabled()


def test_panel_palette_and_custom_mask_colour(qapp, monkeypatch):
    panel = PageNumbersPanel(1, _settings())
    emitted = []
    panel.settings_changed.connect(emitted.append)

    panel.swatch_buttons[2].click()
    expected = MASK_COLORS[2][1]
    assert panel.settings().mask_color == expected
    assert expected in panel.mask_color_btn.text()
    assert emitted[-1].mask_color == expected
    assert "2px solid #0078d7" in panel.swatch_buttons[2].styleSheet()
    assert "2px solid #0078d7" not in panel.swatch_buttons[0].styleSheet()

    monkeypatch.setattr(
        QColorDialog, "getColor",
        staticmethod(lambda *args, **kwargs: QColor("#123456")))
    panel.mask_color_btn.click()
    assert panel.settings().mask_color == "#123456"
    assert emitted[-1].mask_color == "#123456"


def test_panel_has_position_mask_and_erase_sub_tabs(qapp):
    panel = PageNumbersPanel(3, _settings())
    tabs = panel.sub_tabs

    assert tabs.count() == 3
    assert [tabs.tabText(i) for i in range(3)] == [
        "Position", "Mask", "Erase"]
    assert tabs.currentIndex() == 0
    assert tabs.widget(0) is panel.position_page
    assert tabs.widget(1) is panel.mask_page
    assert tabs.widget(2) is panel.erase_page
    assert panel.position_page.isAncestorOf(panel.position_combo)
    assert panel.mask_page.isAncestorOf(panel.mask_left_spin)
    assert panel.mask_page.isAncestorOf(panel.mask_color_btn)
    assert panel.erase_page.isAncestorOf(panel.erase_width_spin)
    assert panel.erase_page.isAncestorOf(panel.erase_color_btn)
    assert not panel.position_page.isAncestorOf(panel.mask_left_spin)


def test_erase_tab_shares_the_range_and_relabels(qapp):
    panel = PageNumbersPanel(5, _settings(start_page=2, end_page=4))
    emitted = []
    panel.settings_changed.connect(emitted.append)

    panel.sub_tabs.setCurrentIndex(2)
    assert panel.erase_active()
    assert panel.save_btn.text() == "Save Erased Copy…"
    assert panel.numbering_group.title() == "Erase range"
    assert not panel._numbering_form.isRowVisible(0)
    assert not panel._numbering_form.isRowVisible(3)
    assert emitted and isinstance(emitted[-1], EraseSettings)
    assert (emitted[-1].start_page, emitted[-1].end_page) == (2, 4)
    assert "covered" in panel.range_label.text()

    panel.first_page_spin.setValue(3)
    assert panel.erase_settings().start_page == 3
    assert panel.settings().start_page == 3
    assert "3–4" in panel.range_label.text()

    panel.sub_tabs.setCurrentIndex(0)
    assert not panel.erase_active()
    assert panel.numbering_group.title() == "Numbering"
    assert panel._numbering_form.isRowVisible(0)
    assert panel._numbering_form.isRowVisible(3)
    assert panel.save_btn.text() == "Save Copy…"
    assert isinstance(emitted[-1], PageNumberSettings)


def test_erase_panel_edits_keep_their_own_colour(qapp):
    panel = PageNumbersPanel(
        3, _settings(), _erase_settings(width=90, height=30, margin_x=12,
                                        margin_y=14, color="#ffffff"))
    emitted = []
    panel.settings_changed.connect(emitted.append)
    panel.sub_tabs.setCurrentIndex(2)

    panel.erase_position_combo.setCurrentIndex(
        panel.erase_position_combo.findData("bottom-right"))
    panel.erase_width_spin.setValue(120)
    panel.erase_height_spin.setValue(40)
    panel.erase_margin_x_spin.setValue(18)
    panel.erase_margin_y_spin.setValue(24)
    settings = panel.erase_settings()
    assert settings.position == "bottom-right"
    assert (settings.width, settings.height) == (120, 40)
    assert (settings.margin_x, settings.margin_y) == (18, 24)
    assert emitted[-1] == settings

    panel.erase_swatch_buttons[1].click()
    expected = MASK_COLORS[1][1]
    assert panel.erase_settings().color == expected
    assert expected in panel.erase_color_btn.text()
    assert panel.settings().mask_color == "#ffffff"
    assert emitted[-1].color == expected


def test_sample_screen_color_maps_scaled_points(qapp):
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("#ffffff"))
    image.setPixelColor(2, 3, QColor("#336699"))

    assert sample_screen_color(
        image, QPoint(5, 8), QSize(10, 10)) == QColor("#336699")
    assert not sample_screen_color(
        image, QPoint(-1, 0), QSize(10, 10)).isValid()


def test_screen_picker_emits_the_sampled_pixel(qapp):
    picker = ScreenColorPicker()
    picker.resize(10, 10)
    image = QImage(4, 4, QImage.Format.Format_RGB32)
    image.fill(QColor("#123456"))
    picker._image = image
    picked = []
    picker.color_picked.connect(picked.append)

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier)
    picker.mousePressEvent(event)

    assert picked == ["#123456"]
    picker.deleteLater()


def test_pick_button_opens_the_screen_picker(qapp, monkeypatch):
    started = []
    monkeypatch.setattr(ScreenColorPicker, "start",
                        lambda self: started.append(self))
    panel = PageNumbersPanel(1, _settings())
    panel.pick_color_btn.click()
    assert len(started) == 1


def test_panel_end_page_follows_the_document_until_edited(qapp):
    panel = PageNumbersPanel(0, PageNumberSettings())
    assert panel.settings().end_page == 0

    panel.set_page_count(3)
    assert panel.last_page_spin.value() == 3
    assert panel.settings().end_page == 0
    assert page_range(panel.settings(), 3) == (0, 2)

    panel.last_page_spin.setValue(2)
    assert panel.settings().end_page == 2
    panel.set_page_count(5)
    assert panel.last_page_spin.value() == 2
    assert panel.settings().end_page == 2

    panel.apply_settings(PageNumberSettings(start_page=2))
    assert panel.last_page_spin.value() == 5
    assert panel.settings().end_page == 0


def test_unit_conversions():
    assert unit_to_points(25.4, "mm") == pytest.approx(72)
    assert unit_to_points(2.54, "cm") == pytest.approx(72)
    assert points_to_unit(72, "in") == pytest.approx(1)
    for unit in ("pt", "mm", "cm", "in"):
        assert points_to_unit(unit_to_points(3.5, unit), unit) == \
            pytest.approx(3.5)


def test_settings_roundtrip(qapp):
    saved = PageNumberSettings(
        position="top-left", format="page-number/total",
        font_family="tiro", font_style="Bold Italic", font_size=17,
        margin_x=12, margin_y=34,
        start_page=2, end_page=7, start_number=3, unit="cm",
        mask=False, mask_left=9, mask_right=1, mask_top=2,
        mask_bottom=5, mask_color="#c0ffee")
    saved.save()
    assert PageNumberSettings.load() == saved


def test_erase_settings_roundtrip(qapp):
    saved = EraseSettings(position="bottom-right", width=120.5, height=18,
                          margin_x=14, margin_y=22, start_page=3,
                          end_page=8, unit="mm", color="#123456")
    saved.save()
    assert EraseSettings.load() == saved


def test_legacy_mask_padding_is_used_for_every_side(qapp):
    settings = QSettings(APP_NAME, APP_NAME)
    settings.remove("pageNumbers")
    settings.setValue("pageNumbers/maskPadding", 7.5)
    try:
        loaded = PageNumberSettings.load()
        assert (loaded.mask_left, loaded.mask_right, loaded.mask_top,
                loaded.mask_bottom) == (7.5, 7.5, 7.5, 7.5)
        assert loaded.mask_color == "#ffffff"
    finally:
        settings.remove("pageNumbers")


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    yield w
    w._close_all_tabs()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_right_dock_tabs_match_sidebar_tab_height(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=2))
    qapp.processEvents()
    assert win.sidebar is not None
    assert (win.right_tabs.tabBar().height()
            == win.sidebar.tabs.tabBar().height())


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


def _green_pixels(image) -> int:
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.green() > 200 and color.red() < 100 and color.blue() < 100:
                count += 1
    return count


def test_preview_uses_the_chosen_mask_colour(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=2))
    view = win.view
    qapp.processEvents()
    assert _green_pixels(_grab_first_page(view)) == 0

    view.set_numbering_preview(_settings(
        font_size=30, margin_x=10, margin_y=10, format="number",
        mask_left=10, mask_right=10, mask_top=10, mask_bottom=10,
        mask_color="#00ff00"))
    qapp.processEvents()
    assert _green_pixels(_grab_first_page(view)) > 10

    view.clear_numbering_preview()


def test_erase_preview_paints_the_mask_colour(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=2,
                                       width=200, height=100))
    view = win.view
    qapp.processEvents()
    assert _green_pixels(_grab_first_page(view)) == 0

    view.set_numbering_preview(_erase_settings(
        width=120, height=40, margin_x=10, margin_y=10, color="#00ff00"))
    qapp.processEvents()
    assert view._numbering_font is None
    assert _green_pixels(_grab_first_page(view)) > 100

    view.clear_numbering_preview()
    qapp.processEvents()
    assert _green_pixels(_grab_first_page(view)) == 0


def test_right_dock_has_two_icon_only_tabs(win):
    tabs = win.right_tabs
    assert tabs.count() == 2
    assert [tabs.tabText(i) for i in range(2)] == ["", ""]
    assert tabs.tabToolTip(0) == "Search"
    assert tabs.tabToolTip(1) == "Page Numbers"
    assert not tabs.tabIcon(0).isNull()
    assert not tabs.tabIcon(1).isNull()
    assert tabs.iconSize() == QSize(18, 18)
    assert tabs.widget(0) is win.search_panel
    assert tabs.widget(1) is win.page_numbers_panel
    assert win.search_dock.windowTitle() == "Tools"


def test_tools_dock_can_shrink_for_more_document_space(win, qapp):
    win.resize(1200, 800)
    qapp.processEvents()
    dock = win.search_dock
    assert dock.minimumWidth() == 280

    win.resizeDocks([dock], [280], Qt.Orientation.Horizontal)
    for _ in range(4):
        qapp.processEvents()
    assert dock.width() == 280
    assert win.tabs.width() > 0

    # The page-number forms wrap instead of forcing a wider panel.
    panel = win.page_numbers_panel
    assert panel.minimumSizeHint().width() <= 280
    for page in (panel.position_page, panel.mask_page, panel.erase_page):
        assert page.minimumSizeHint().width() <= 260

    # The requested minimum is a floor, not a fixed width.
    win.resizeDocks([dock], [200], Qt.Orientation.Horizontal)
    qapp.processEvents()
    assert dock.width() == 280

    win.resizeDocks([dock], [420], Qt.Orientation.Horizontal)
    qapp.processEvents()
    assert dock.width() == 420


def _icon_ink(icon) -> QColor:
    image = icon.pixmap(QSize(18, 18)).toImage()
    answer = QColor(0, 0, 0, 0)
    for y in range(image.height()):
        for x in range(image.width()):
            color = QColor.fromRgba(image.pixel(x, y))
            if color.alpha() > answer.alpha():
                answer = color
    return answer


def test_right_dock_icons_follow_the_visible_theme(win):
    """A saved dark-mode flag must not tint the icons white on a light UI."""
    saved = win.settings.value("dark_mode", False, type=bool)
    try:
        win.settings.setValue("dark_mode", True)
        win._restore_state()
        assert _icon_ink(win.right_tabs.tabIcon(0)).lightness() < 100
        assert _icon_ink(win.right_tabs.tabIcon(1)).lightness() < 100

        win._apply_dark_mode(True)
        assert _icon_ink(win.right_tabs.tabIcon(0)).lightness() > 150
    finally:
        win._apply_dark_mode(False)
        win.settings.setValue("dark_mode", saved)


def test_page_numbers_tab_drives_the_live_preview(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    qapp.processEvents()
    assert win.view._numbering_preview is None

    win.act_page_numbers.trigger()
    qapp.processEvents()
    assert win.right_tabs.currentWidget() is win.page_numbers_panel
    assert win.view._numbering_preview is not None
    assert win.page_numbers_panel._page_count == 3

    win._focus_search()
    assert win.right_tabs.currentWidget() is win.search_panel
    assert win.view._numbering_preview is None


def test_page_numbers_tab_leaves_and_restores_reflow(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    qapp.processEvents()
    win.view.set_reflow_mode(True)
    win.act_reflow.setChecked(True)

    win.right_tabs.setCurrentWidget(win.page_numbers_panel)
    assert not win.view.reflow_mode
    assert win.view._numbering_preview is not None

    win.right_tabs.setCurrentWidget(win.search_panel)
    assert win.view.reflow_mode
    assert win.view._numbering_preview is None


def test_page_numbers_tab_saves_numbered_copy(
        win, qapp, tmp_path, monkeypatch):
    src = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    win.open_document(src)
    qapp.processEvents()
    before = open(src, "rb").read()
    out = tmp_path / "doc-numbered.pdf"
    opened = []

    win.act_page_numbers.trigger()
    qapp.processEvents()
    panel = win.page_numbers_panel
    panel.position_combo.setCurrentIndex(
        panel.position_combo.findData("bottom-center"))
    panel.format_combo.setCurrentIndex(
        panel.format_combo.findData("number/total"))
    panel.size_spin.setValue(14)
    panel.first_page_spin.setValue(1)
    panel.last_page_spin.setValue(3)
    panel.first_number_spin.setValue(1)

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: (str(out), "")))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs: QMessageBox.StandardButton.No))
    monkeypatch.setattr(
        win, "open_document",
        lambda path, password="": opened.append(path) or True)

    panel.save_btn.click()
    qapp.processEvents()

    assert out.is_file()
    assert open(src, "rb").read() == before
    with fitz.open(out) as doc:
        texts = [page.get_text() for page in doc]
    assert "1/3" in texts[0] and "2/3" in texts[1] and "3/3" in texts[2]
    assert opened == []
    assert win.view._numbering_preview is not None
    assert PageNumberSettings.load().font_size == 14


def test_page_numbers_tab_refuses_to_overwrite_the_open_document(
        win, qapp, tmp_path, monkeypatch):
    src = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    win.open_document(src)
    qapp.processEvents()
    before = open(src, "rb").read()
    warnings = []
    answers = iter([(src, ""), ("", "")])

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName",
        staticmethod(lambda *args, **kwargs: next(answers)))
    monkeypatch.setattr(
        QMessageBox, "warning",
        staticmethod(lambda *args, **kwargs: warnings.append(args[2])))

    win.act_page_numbers.trigger()
    qapp.processEvents()
    win.page_numbers_panel.save_btn.click()
    qapp.processEvents()

    assert warnings and "never overwrites" in warnings[0]
    assert open(src, "rb").read() == before
    assert win.view._numbering_preview is not None


def _mouse(view, kind, pos, button, buttons):
    event = QMouseEvent(kind, pos, pos, button, buttons,
                        Qt.KeyboardModifier.NoModifier)
    if kind == QEvent.Type.MouseButtonPress:
        view.mousePressEvent(event)
    elif kind == QEvent.Type.MouseMove:
        view.mouseMoveEvent(event)
    else:
        view.mouseReleaseEvent(event)


def test_dragging_the_preview_moves_the_number_and_updates_the_panel(
        win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    qapp.processEvents()
    win.act_page_numbers.trigger()
    qapp.processEvents()

    view = win.view
    settings = win.page_numbers_panel.settings()
    w0, h0 = view.page_sizes[0]
    box = number_box(settings, w0, h0, settings.start_number, 3,
                     font=view._numbering_font)
    px, py = view._page_origin(0)
    centre_x = box.left + box.width / 2.0
    centre_y = box.top + box.height / 2.0
    u, v = map_point(centre_x, centre_y, w0, h0, view.rotation)
    start = QPointF(
        px + u * view.zoom - view.horizontalScrollBar().value(),
        py + v * view.zoom - view.verticalScrollBar().value())

    changes = []
    view.numbering_changed.connect(changes.append)

    _mouse(view, QEvent.Type.MouseButtonPress, start,
           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert view._numbering_drag is not None
    moved = QPointF(start.x() + 8, start.y())
    _mouse(view, QEvent.Type.MouseMove, moved,
           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    _mouse(view, QEvent.Type.MouseButtonRelease, moved,
           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)

    assert view._numbering_drag is None
    assert changes
    assert changes[-1].position == "bottom-right"
    assert changes[-1].margin_x > settings.margin_x
    panel_settings = win.page_numbers_panel.settings()
    assert panel_settings.position == "bottom-right"
    assert panel_settings.margin_x == pytest.approx(changes[-1].margin_x, abs=1)
    assert view._numbering_preview == changes[-1]


def test_dragging_the_erase_mask_updates_the_panel(win, qapp, tmp_path):
    win.open_document(build_sample_pdf(tmp_path / "doc.pdf", pages=3,
                                       width=200, height=100))
    qapp.processEvents()
    win.act_page_numbers.trigger()
    qapp.processEvents()
    panel = win.page_numbers_panel
    panel.sub_tabs.setCurrentIndex(2)
    qapp.processEvents()

    view = win.view
    settings = panel.erase_settings()
    w0, h0 = view.page_sizes[0]
    box = erase_box(settings, w0, h0)
    px, py = view._page_origin(0)
    centre_x = box.left + box.width / 2.0
    centre_y = box.top + box.height / 2.0
    u, v = map_point(centre_x, centre_y, w0, h0, view.rotation)
    start = QPointF(
        px + u * view.zoom - view.horizontalScrollBar().value(),
        py + v * view.zoom - view.verticalScrollBar().value())

    changes = []
    view.numbering_changed.connect(changes.append)

    _mouse(view, QEvent.Type.MouseButtonPress, start,
           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
    assert view._numbering_drag is not None
    moved = QPointF(start.x() + 8, start.y() + 6)
    _mouse(view, QEvent.Type.MouseMove, moved,
           Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton)
    _mouse(view, QEvent.Type.MouseButtonRelease, moved,
           Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton)

    assert view._numbering_drag is None
    assert changes and isinstance(changes[-1], EraseSettings)
    assert changes[-1].margin_x > settings.margin_x
    assert changes[-1].margin_y > settings.margin_y
    panel_settings = panel.erase_settings()
    assert panel_settings.margin_x == pytest.approx(
        changes[-1].margin_x, abs=1)
    assert panel_settings.margin_y == pytest.approx(
        changes[-1].margin_y, abs=1)
    assert isinstance(view._numbering_preview, EraseSettings)


def test_erase_tab_saves_an_erased_copy(win, qapp, tmp_path, monkeypatch):
    src = build_sample_pdf(tmp_path / "doc.pdf", pages=3,
                           width=200, height=100)
    win.open_document(src)
    qapp.processEvents()
    before = open(src, "rb").read()
    out = tmp_path / "doc-erased.pdf"
    opened, asked, defaults = [], [], []

    win.act_page_numbers.trigger()
    qapp.processEvents()
    panel = win.page_numbers_panel
    panel.sub_tabs.setCurrentIndex(2)
    panel.erase_width_spin.setValue(120)
    panel.erase_height_spin.setValue(40)
    panel.erase_margin_x_spin.setValue(10)
    panel.erase_margin_y_spin.setValue(10)
    panel.first_page_spin.setValue(2)
    panel.last_page_spin.setValue(3)

    def _save_name(*args, **kwargs):
        defaults.append(args[2])
        return str(out), ""

    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", staticmethod(_save_name))
    monkeypatch.setattr(
        QMessageBox, "question",
        staticmethod(lambda *args, **kwargs:
                     asked.append(args[2]) or QMessageBox.StandardButton.No))
    monkeypatch.setattr(
        win, "open_document",
        lambda path, password="": opened.append(path) or True)

    panel.save_btn.click()
    qapp.processEvents()

    assert out.is_file()
    assert open(src, "rb").read() == before
    assert defaults and defaults[0].endswith("doc-erased.pdf")
    with fitz.open(out) as doc:
        covered = [_white_drawings(page) for page in doc]
    assert covered[0] == []
    assert covered[1] and covered[2]
    assert opened == []
    assert asked and "Covered 2 page(s)" in asked[0]
    assert EraseSettings.load().width == pytest.approx(120)
