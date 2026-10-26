"""Tests for P1-5 reflow mode (single-column reading).

The feature must never block the GUI: extraction and layout run in a
``ReflowWorker`` thread with its own PyMuPDF document, and the viewer only
paints cached layouts.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QMessageBox

from pdfar.main import MainWindow, _format_zoom
from pdfar.reflow import (
    REFLOW_FONT_SCALE_MAX, REFLOW_FONT_SCALE_MIN, REFLOW_MAX_WIDTH,
    REFLOW_MIN_WIDTH, REFLOW_UI_MAX_FONT, REFLOW_UI_MIN_FONT, Line,
    _detect_gutter, clamp_font_scale, extract_page, extract_paragraphs,
    layout_paragraphs, measure_text, reflow_text_width, scaled_font_size,
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


def _save_image(path, color, width=80, height=60):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    image.save(str(path))
    return str(path)


def build_image_pdf(path):
    """Text above and below a red raster figure."""
    image_path = _save_image(path.parent / "figure.png", "#dc1e1e")
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((40, 60), "Before the figure.", fontsize=12)
    page.insert_image(fitz.Rect(40, 90, 240, 240), filename=image_path)
    page.insert_text((40, 340), "After the figure.", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def build_table_pdf(path):
    """A ruled 3x2 table between two paragraphs."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((40, 60), "Before the table.", fontsize=12)
    for y in (100, 160, 220, 280):
        page.draw_line(fitz.Point(40, y), fitz.Point(360, y), color=(0, 0, 0))
    for x in (40, 200, 360):
        page.draw_line(fitz.Point(x, 100), fitz.Point(x, 280), color=(0, 0, 0))
    for row, y in enumerate((135, 195, 255)):
        page.insert_text((50, y), f"Left {row}", fontsize=11)
        page.insert_text((210, y), f"Right {row}", fontsize=11)
    page.insert_text((40, 330), "After the table.", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def _text_of(paragraphs) -> str:
    return " ".join(run.text for para in paragraphs for run in para.runs)


def _pixel_count(image, predicate) -> int:
    return sum(
        1
        for y in range(0, image.height(), 4)
        for x in range(0, image.width(), 4)
        if predicate(QColor(image.pixel(x, y)))
    )


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


def test_font_scale_is_clamped(qapp):
    assert clamp_font_scale(0.01) == REFLOW_FONT_SCALE_MIN
    assert clamp_font_scale(99) == REFLOW_FONT_SCALE_MAX
    assert clamp_font_scale("bad") == 1.0
    assert scaled_font_size(34, 99) == REFLOW_UI_MAX_FONT
    assert scaled_font_size(10, 99) == 30
    assert scaled_font_size(10, 0.01) == REFLOW_UI_MIN_FONT


def test_layout_font_scale_grows_text_and_keeps_width(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=1)
    doc = fitz.open(path)
    try:
        paragraphs = extract_paragraphs(doc[0], 0)
    finally:
        doc.close()

    width = 300
    small = layout_paragraphs(paragraphs, width, 0, size_scale=0.7)
    normal = layout_paragraphs(paragraphs, width, 0)
    big = layout_paragraphs(paragraphs, width, 0, size_scale=1.6)

    assert small.height < normal.height < big.height
    assert small.lines[0].runs[0][2] < normal.lines[0].runs[0][2]
    assert normal.lines[0].runs[0][2] < big.lines[0].runs[0][2]

    for layout in (small, normal, big):
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


def test_extract_page_renders_images_inline(qapp, tmp_path):
    path = build_image_pdf(tmp_path / "image.pdf")
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=320)
    finally:
        doc.close()

    assert "Before the figure." in _text_of(paragraphs)
    assert "After the figure." in _text_of(paragraphs)
    assert len(media) == 1
    figure = media[0]
    assert figure.kind == "image"
    assert figure.data.startswith(b"\x89PNG")
    assert 0 < figure.width <= 320
    assert figure.height > 0

    layout = layout_paragraphs(paragraphs, 320, 0, media=media)
    assert layout.has_text
    assert len(layout.media) == 1
    placed = layout.media[0]
    assert placed.y > 0
    assert any(line.y < placed.y for line in layout.lines)
    assert any(line.y >= placed.y + placed.height for line in layout.lines)
    assert layout.height >= placed.y + placed.height


def test_extract_page_skips_background_image_under_text(qapp, tmp_path):
    image_path = _save_image(tmp_path / "background.png", "#445566", 120, 160)
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_image(fitz.Rect(0, 0, 300, 400), filename=image_path)
    page.insert_text((40, 60), "Text over a background image.", fontsize=12)
    path = str(tmp_path / "background.pdf")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=300)
    finally:
        doc.close()
    assert "Text over a background image." in _text_of(paragraphs)
    assert media == [], "page background must not be reflowed as a figure"


def test_extract_page_renders_ruled_tables_once(qapp, tmp_path):
    path = build_table_pdf(tmp_path / "table.pdf")
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=320)
    finally:
        doc.close()

    text = _text_of(paragraphs)
    assert "Before the table." in text
    assert "After the table." in text
    # Cell text belongs to the rendered table, not to the reflowed column.
    assert "Left 0" not in text and "Right 2" not in text

    tables = [item for item in media if item.kind == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.data.startswith(b"\x89PNG")
    assert table.width == pytest.approx(320)
    assert table.height > 0

    layout = layout_paragraphs(paragraphs, 320, 0, media=media)
    assert layout.media
    assert any(line.y < layout.media[0].y for line in layout.lines)
    assert any(line.y >= layout.media[0].y for line in layout.lines)


def _column_page(path, *, image=False, band=False, page_number=False):
    """Two columns (x=40 and x=270) on a 500x640 page."""
    doc = fitz.open()
    page = doc.new_page(width=500, height=640)
    page.insert_text((40, 46), "A page-wide title spanning both columns here",
                     fontsize=15, fontname="hebo")

    def para(x, y, text):
        page.insert_textbox(fitz.Rect(x, y, x + 215, y + 50), text,
                            fontsize=9.5)

    para(40, 70, "Left one. The first paragraph of the left column.")
    para(40, 140, "Left two. The second paragraph of the left column.")
    para(270, 70, "Right one. The first paragraph of the right column.")
    para(270, 140, "Right two. The second paragraph of the right column.")
    if image:
        image_path = _save_image(path.parent / "column.png", "#2457a6", 80, 50)
        page.insert_image(fitz.Rect(40, 220, 240, 300), filename=image_path)
        para(40, 320, "Left three. The paragraph after the column figure.")
        para(270, 220, "Right three. More text in the right column.")
    if band:
        image_path = _save_image(path.parent / "band.png", "#a65724", 160, 60)
        page.insert_image(fitz.Rect(40, 340, 460, 440), filename=image_path)
        page.insert_textbox(
            fitz.Rect(40, 448, 460, 470),
            "Figure 1. A page-wide caption under the full-width figure, long "
            "enough to reach across the gutter.",
            fontsize=9)
        para(40, 490, "Left four. The paragraph below the full-width figure.")
        para(270, 515, "Right four. The paragraph below the full-width figure.")
    if page_number:
        # Centred on the gutter between x=255 (left edge) and x=270.
        page.insert_text((260, 600), "7", fontsize=8)
    doc.save(str(path))
    doc.close()
    return str(path)


def _ordered_items(paragraphs, media) -> list:
    items = [(p.order, " ".join(r.text for r in p.runs)) for p in paragraphs]
    items += [(m.order, m.kind) for m in media]
    return [label for _order, label in sorted(items)]


def test_two_column_text_reads_left_column_first(qapp, tmp_path):
    path = _column_page(tmp_path / "columns.pdf")
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=700)
        gutter = _detect_gutter(paragraphs + media)
    finally:
        doc.close()

    assert media == []
    assert gutter is not None and 240 < gutter < 270
    text = [p for p in paragraphs if p.runs]
    labels = [" ".join(r.text for r in p.runs) for p in text]
    assert labels[0].startswith("A page-wide title")
    joined = " | ".join(labels)
    assert joined.index("Left one") < joined.index("Left two")
    assert joined.index("Left two") < joined.index("Right one")
    assert joined.index("Right one") < joined.index("Right two")


def test_two_column_media_stays_in_its_column(qapp, tmp_path):
    path = _column_page(tmp_path / "columns.pdf", image=True)
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=700)
    finally:
        doc.close()

    assert len(media) == 1 and media[0].kind == "image"
    labels = _ordered_items(paragraphs, media)
    assert labels.index("image") > labels.index(
        "Left two. The second paragraph of the left column.")
    # The left column is complete before the right column starts.
    assert labels.index("image") < labels.index(
        "Right one. The first paragraph of the right column.")
    assert labels.index("Left three. The paragraph after the column figure.") \
        < labels.index("Right one. The first paragraph of the right column.")


def test_full_width_band_splits_the_columns(qapp, tmp_path):
    path = _column_page(tmp_path / "columns.pdf", band=True)
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=700)
    finally:
        doc.close()

    assert len(media) == 1
    labels = _ordered_items(paragraphs, media)
    band_index = labels.index("image")
    assert labels.index("Left two. The second paragraph of the left column.") < band_index
    assert labels.index("Right two. The second paragraph of the right column.") < band_index
    assert band_index < labels.index(
        "Left four. The paragraph below the full-width figure.")
    # The caption is page-wide and follows the figure.
    caption = next(i for i, label in enumerate(labels) if "Figure 1." in label)
    assert caption == band_index + 1


def test_centered_page_number_is_placed_last(qapp, tmp_path):
    path = _column_page(tmp_path / "columns.pdf", page_number=True)
    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=700)
    finally:
        doc.close()

    numbers = [p for p in paragraphs
               if " ".join(r.text for r in p.runs).strip() == "7"]
    assert len(numbers) == 1
    assert numbers[0].order == max(p.order for p in paragraphs)


def test_single_column_has_no_gutter(qapp, tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=500)
    for index in range(4):
        page.insert_textbox(fitz.Rect(40, 50 + index * 90, 360, 130 + index * 90),
                            f"Paragraph {index} of a single wide column.",
                            fontsize=11)
    path = str(tmp_path / "single.pdf")
    doc.save(path)
    doc.close()

    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=600)
        assert _detect_gutter(paragraphs + media) is None
    finally:
        doc.close()
    assert [p.top for p in paragraphs] == sorted(p.top for p in paragraphs)


def test_sidebar_beside_wide_body_is_not_two_columns(qapp, tmp_path):
    """A narrow metadata sidebar next to one wide body column has no gutter."""
    doc = fitz.open()
    page = doc.new_page(width=520, height=640)
    for index in range(5):
        page.insert_textbox(fitz.Rect(40, 80 + index * 45, 140, 120 + index * 45),
                            f"Metadata line {index} in the sidebar.", fontsize=8)
    page.insert_textbox(fitz.Rect(220, 60, 500, 80), "1 Introduction", fontsize=11)
    for index in range(5):
        page.insert_textbox(
            fitz.Rect(220, 90 + index * 90, 500, 170 + index * 90),
            "A wide body paragraph that fills most of the page width; it is "
            "the main column, not one half of a two-column layout.",
            fontsize=10)
    page.insert_textbox(fitz.Rect(220, 560, 500, 585), "2 Methods", fontsize=11)
    path = tmp_path / "sidebar.pdf"
    doc.save(str(path))
    doc.close()

    doc = fitz.open(path)
    try:
        paragraphs, media = extract_page(doc[0], 0, width=700)
        assert _detect_gutter(paragraphs + media) is None
    finally:
        doc.close()


def _two_column_fixtures():
    folder = (Path(__file__).resolve().parents[1]
              / "external" / "two-column-test-pdfs")
    return sorted(folder.glob("*.pdf"))


@pytest.mark.parametrize("path", _two_column_fixtures(), ids=lambda path: path.name)
def test_real_world_two_column_fixtures(qapp, path):
    """CC BY research papers: most pages expose a gutter near the page middle.

    Title pages mix a sidebar with a wide body and a full-width abstract, so
    they legitimately have no gutter; the body pages must.
    """
    doc = fitz.open(path)
    try:
        page_width = doc[0].rect.width
        detected = 0
        for index in range(doc.page_count):
            paragraphs, media = extract_page(doc[index], index, width=760)
            gutter = _detect_gutter(paragraphs + media)
            if gutter is None:
                continue
            detected += 1
            assert page_width / 3 <= gutter <= page_width * 2 / 3, (
                f"{path.name} page {index + 1}: gutter {gutter:.1f} off-centre")
        assert detected >= max(3, doc.page_count // 2), (
            f"{path.name}: only {detected}/{doc.page_count} pages two-column")
    finally:
        doc.close()


def test_committed_two_column_demo_reading_order(qapp):
    demo = Path(__file__).resolve().parents[1] / "demo-two-columns.pdf"
    if not demo.exists():
        pytest.skip("demo-two-columns.pdf is not present")
    doc = fitz.open(demo)
    try:
        assert doc.page_count == 4
        orders = []
        for index in range(doc.page_count):
            paragraphs, media = extract_page(doc[index], index, width=760)
            assert _detect_gutter(paragraphs + media) is not None
            orders.append(_ordered_items(paragraphs, media))
    finally:
        doc.close()

    # Page 3: the column figure and the table sit in different columns, and
    # the left column is emitted before the right one.
    page3 = " | ".join(orders[2])
    assert page3.index("Figure 2.") < page3.index("Table 1.")
    # The lower column figure belongs to the left column, so it is read
    # before the table at the top of the right column.
    assert page3.index("Figure 3.") < page3.index("Table 1.")
    assert page3.index("Table 1.") < page3.index("3.4 Implementation Notes")
    # Page 4: the full-width table is followed by both body columns, then the
    # references heading, then the reference columns.
    page4 = " | ".join(orders[3])
    assert page4.index("Table 2.") < page4.index("4. Evaluation")
    assert page4.index("References") < page4.index("Breuel")
    assert page4.index("References") < page4.index("Nagy")
    # Page-wide figure caption sits between the figure and the lower columns.
    page2 = " | ".join(orders[1])
    assert page2.index("Figure 1.") < page2.index("3. Method")


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


def test_reflow_text_size_controls_preserve_position(qapp, tmp_path):
    path = build_text_pdf(tmp_path / "text.pdf", pages=8)
    view = PDFView(path)
    view.resize(800, 600)
    view.show()
    try:
        _enable_reflow(view, qapp)
        view.goto_page(4, center=False)
        qapp.processEvents()
        base_height = view._reflow_pages[4].height
        base_size = view._reflow_pages[4].lines[0].runs[0][2]
        emitted = []
        view.reflow_font_scale_changed.connect(emitted.append)

        view.reflow_text_bigger()
        assert wait_until(qapp, lambda: view._reflow_worker is None)
        qapp.processEvents()
        assert view.reflow_font_scale > 1.0
        assert emitted and emitted[-1] == view.reflow_font_scale
        assert view.current_page == 4
        assert view._reflow_pages[4].height > base_height
        assert view._reflow_pages[4].lines[0].runs[0][2] > base_size
        assert 0 <= view._reflow_tops[4] - view.verticalScrollBar().value() < 2

        view.reflow_text_smaller()
        assert wait_until(qapp, lambda: view._reflow_worker is None)
        qapp.processEvents()
        assert view.reflow_font_scale == pytest.approx(1.0)
        assert view.current_page == 4

        view.reflow_text_smaller()
        assert wait_until(qapp, lambda: view._reflow_worker is None)
        qapp.processEvents()
        assert view.reflow_font_scale < 1.0
        assert view._reflow_pages[4].height < base_height

        view.set_reflow_font_scale(1.0)
        assert wait_until(qapp, lambda: view._reflow_worker is None)
        qapp.processEvents()
        assert view.reflow_font_scale == pytest.approx(1.0)
        assert view._reflow_pages[4].height == pytest.approx(base_height)
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


def test_reflow_paints_images_and_caches_them(qapp, tmp_path):
    path = build_image_pdf(tmp_path / "image.pdf")
    view = PDFView(path)
    view.resize(800, 600)
    view.show()
    try:
        _enable_reflow(view, qapp)
        assert view.reflow_has_media is True
        figure = view._reflow_pages[0].media[0]
        image = view.grab().toImage()
        red = _pixel_count(
            image, lambda c: c.red() > 170 and c.green() < 100 and c.blue() < 100)
        assert red > 50, "the inline figure was not painted"

        first = view._reflow_media_image(figure)
        assert not first.isNull()
        assert view._reflow_media_image(figure) is first
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


def test_reflow_scanned_pages_are_shown_as_images(qapp, tmp_path):
    image_path = _save_image(tmp_path / "scan.png", "#d21e1e", 120, 160)
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_image(fitz.Rect(0, 0, 300, 400), filename=image_path)
    path = str(tmp_path / "scan.pdf")
    doc.save(path)
    doc.close()

    view = PDFView(path)
    view.resize(700, 500)
    view.show()
    try:
        ready = _enable_reflow(view, qapp)
        assert view._reflow_has_text is False
        assert view.reflow_has_media is True
        assert view._reflow_empty_pages == 0
        assert ready and ready[-1] == (False, 0)
        image = view.grab().toImage()
        red = _pixel_count(
            image, lambda c: c.red() > 170 and c.green() < 100 and c.blue() < 100)
        assert red > 50, "scanned page image was not painted"
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
def test_main_window_zoom_actions_switch_with_reflow(qapp, tmp_path):
    win = MainWindow()
    win.show()
    try:
        win.open_document(build_text_pdf(tmp_path / "a.pdf", pages=3))
        assert win.act_zin.text() == "Zoom In"
        assert not win.act_zin.icon().isNull()
        assert not win.act_zout.icon().isNull()
        assert not win.act_reflow.icon().isNull()
        assert (win.toolbar.widgetForAction(win.act_zin).toolButtonStyle()
                == Qt.ToolButtonStyle.ToolButtonIconOnly)
        assert (win.toolbar.widgetForAction(win.act_reflow).toolButtonStyle()
                == Qt.ToolButtonStyle.ToolButtonIconOnly)
        assert win.act_reflow.toolTip() == "Reflow (Ctrl+R)"

        win._toggle_reflow()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
        assert win.act_zin.text() == "Increase Text Size"
        assert win.act_zout.text() == "Decrease Text Size"
        assert not win.act_zin.icon().isNull()
        assert not win.act_zout.icon().isNull()
        assert win.act_zin.toolTip() == "Increase Text Size (Ctrl++)"
        assert win.act_zout.toolTip() == "Decrease Text Size (Ctrl+-)"
        assert win.zoom_label.text() == "100%"

        win._zoom_in()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
        assert win.view.reflow_font_scale == pytest.approx(1.15)
        assert win.zoom_label.text() == "115%"
        assert "text 115%" in win.status_right.text()
        # zoom itself must not change while reflow is active
        assert win.view.zoom != pytest.approx(1.25)

        win._zoom_out()
        assert wait_until(qapp, lambda: win.view._reflow_worker is None)
        assert win.view.reflow_font_scale == pytest.approx(1.0)
        assert win.zoom_label.text() == "100%"

        win._toggle_reflow()
        assert win.act_zin.text() == "Zoom In"
        assert not win.act_zin.icon().isNull()
        assert not win.act_zout.icon().isNull()
        assert win.act_zin.toolTip() == "Zoom in (Ctrl++)"
        assert win.act_zout.toolTip() == "Zoom out (Ctrl+-)"
        assert (win.toolbar.widgetForAction(win.act_zin).toolButtonStyle()
                == Qt.ToolButtonStyle.ToolButtonIconOnly)
        assert win.zoom_label.text() == _format_zoom(win.view.zoom)
    finally:
        win.close()


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
