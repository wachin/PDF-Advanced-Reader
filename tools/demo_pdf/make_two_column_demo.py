#!/usr/bin/env python3
"""Build demo-two-columns.pdf, a journal-style two-column demo article.

The fixture is a four-page A4 paper typeset in two columns with page-wide
elements in between:

* page 1: masthead, title block and full-width abstract, then Introduction;
* page 2: body columns, a full-width pipeline figure with its caption, then
  more body columns;
* page 3: a column figure at the top of the left column, a column table at the
  top of the right column, and a second column figure lower down;
* page 4: a full-width results table, body text, and two-column references.

It exercises the column-aware reading order in ``pdfar.reflow``: everything is
drawn with PyMuPDF so the text blocks, raster figures and ruled tables land in
realistic positions without external tooling.

Usage::

    python3 tools/demo_pdf/make_two_column_demo.py [--out PATH]
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "demo-two-columns.pdf"

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN_L = MARGIN_R = 50.0
TOP = 46.0
BOTTOM = 792.0
GUTTER = 20.0
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R
COL_W = (CONTENT_W - GUTTER) / 2.0
RIGHT_X = MARGIN_L + COL_W + GUTTER

BODY_SIZE = 9.3
BODY_LEADING = 12.55
CAPTION_SIZE = 8.0
CAPTION_LEADING = 10.4
INK = (0.13, 0.13, 0.13)
MUTED = (0.42, 0.42, 0.42)
ACCENT = (0.16, 0.34, 0.58)
RULE = (0.70, 0.70, 0.70)

_INLINE_RE = re.compile(r"\*\*(.+?)\*\*|\*(.+?)\*")


class LayoutOverflow(RuntimeError):
    pass


# --------------------------------------------------------------- text helpers
def _segments(text: str):
    """Split *text* into ``(segment, font)`` runs using **bold** / *italic*."""
    parts = []
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            parts.append((text[pos:match.start()], "helv"))
        if match.group(1) is not None:
            parts.append((match.group(1), "hebo"))
        else:
            parts.append((match.group(2), "heit"))
        pos = match.end()
    if pos < len(text):
        parts.append((text[pos:], "helv"))
    return parts


def _words(text: str):
    words = []
    for segment, font in _segments(text):
        words.extend((word, font) for word in segment.split())
    return words


def _measure(text: str, font: str, size: float) -> float:
    return float(fitz.get_text_length(text, fontname=font, fontsize=size))


def _wrap(words, size: float, first_width: float, width: float):
    """Greedy wrap; returns ``[(words, line_width), ...]``."""
    lines = []
    line = []
    line_w = 0.0
    available = first_width
    space = _measure(" ", "helv", size)
    for word, font in words:
        word_w = _measure(word, font, size)
        extra = word_w if not line else space + word_w
        if line and line_w + extra > available + 1.5:
            lines.append((line, line_w))
            line = [(word, font)]
            line_w = word_w
            available = width
        else:
            line.append((word, font))
            line_w += extra
    if line:
        lines.append((line, line_w))
    return lines


class _Frame:
    def __init__(self, rect):
        self.rect = fitz.Rect(rect)
        self.y = self.rect.y0
        self.fresh = True


class Flow:
    """Draw text and figures into an ordered list of frames (columns)."""

    def __init__(self, page, rects):
        self.page = page
        self.frames = [_Frame(rect) for rect in rects]
        self.index = 0

    @property
    def frame(self) -> _Frame:
        return self.frames[self.index]

    @property
    def width(self) -> float:
        return self.frame.rect.width

    def remaining(self) -> float:
        return self.frame.rect.y1 - self.frame.y

    def _advance(self) -> None:
        if self.index + 1 >= len(self.frames):
            raise LayoutOverflow(
                f"content overflows the last frame at y={self.frame.y:.1f}")
        self.index += 1

    def _ensure(self, height: float) -> None:
        while self.frame.y + height > self.frame.rect.y1 + 0.01:
            self._advance()

    def space(self, points: float) -> None:
        if self.frame.fresh or points <= 0:
            return
        self.frame.y = min(self.frame.y + points, self.frame.rect.y1)

    def heading(self, text: str, size: float = 11.0,
                space_before: float = 8.0, space_after: float = 3.0) -> None:
        lines = _wrap(_words(text), size, self.width, self.width)
        total = len(lines) * size * 1.25 + space_before
        self._ensure(total)
        self.space(space_before)
        for line, line_w in lines:
            if self.frame.y + size * 1.25 > self.frame.rect.y1:
                self._advance()
            baseline = self.frame.y + size * 0.78
            self._draw_words(line, line_w, self.frame.rect.x0, baseline,
                             size, self.width, justify=False)
            self.frame.y += size * 1.25
            self.frame.fresh = False
        self.frame.y += space_after

    def paragraph(self, text: str, size: float = BODY_SIZE,
                  leading: float = BODY_LEADING, indent: float = 12.0,
                  space_before: float = 0.0, space_after: float = 4.5,
                  justify: bool = True) -> None:
        words = _words(text)
        if not words:
            return
        self.space(space_before)
        width = self.width
        lines = _wrap(words, size, width - indent, width)
        for index, (line, line_w) in enumerate(lines):
            if self.frame.y + leading > self.frame.rect.y1 + 0.01:
                self._advance()
            x = self.frame.rect.x0 + (indent if index == 0 else 0.0)
            available = self.width - (indent if index == 0 else 0.0)
            baseline = self.frame.y + size * 0.78
            self._draw_words(line, line_w, x, baseline, size, available,
                             justify and index < len(lines) - 1)
            self.frame.y += leading
            self.frame.fresh = False
        self.frame.y += space_after

    def figure(self, data: bytes, height: float, caption: str,
               caption_size: float = CAPTION_SIZE,
               space_before: float = 3.0, space_after: float = 9.0) -> None:
        width = self.width
        caption_leading = caption_size * 1.3
        caption_lines = _wrap(_words(caption), caption_size, width, width)
        total = height + 4.0 + len(caption_lines) * caption_leading
        self._ensure(total)
        self.space(space_before)
        x0 = self.frame.rect.x0
        y0 = self.frame.y
        rect = fitz.Rect(x0, y0, x0 + width, y0 + height)
        self.page.insert_image(rect, stream=data, keep_proportion=True)
        self.frame.y = rect.y1 + 4.0
        self.frame.fresh = False
        last = len(caption_lines) - 1
        for index, (line, line_w) in enumerate(caption_lines):
            baseline = self.frame.y + caption_size * 0.78
            self._draw_words(line, line_w, x0, baseline, caption_size,
                             width, justify=index < last)
            self.frame.y += caption_leading
        self.frame.y += space_after

    def _draw_words(self, words, line_w, x, baseline, size, available,
                    justify) -> None:
        gaps = len(words) - 1
        space = _measure(" ", "helv", size)
        extra = 0.0
        if justify and gaps > 0 and line_w < available:
            extra = (available - line_w) / gaps
        cursor = x
        for index, (word, font) in enumerate(words):
            self.page.insert_text((cursor, baseline), word,
                                  fontname=font, fontsize=size, color=INK)
            cursor += _measure(word, font, size)
            if index < gaps:
                cursor += space + extra


# -------------------------------------------------------------------- tables
def draw_table(page, rect, col_widths, header, rows, size=7.4,
               header_size=7.4, header_fill=(0.90, 0.93, 0.97),
               aligns=None, row_height=None) -> None:
    rect = fitz.Rect(rect)
    n_rows = len(rows) + 1
    row_h = row_height if row_height else rect.height / n_rows
    xs = [rect.x0]
    for width in col_widths:
        xs.append(xs[-1] + width)

    page.draw_rect(fitz.Rect(xs[0], rect.y0, xs[-1], rect.y0 + row_h),
                   color=None, fill=header_fill)

    for row in range(n_rows + 1):
        y = rect.y0 + row * row_h
        page.draw_line(fitz.Point(xs[0], y), fitz.Point(xs[-1], y),
                       color=RULE, width=0.6)
    for x in xs:
        page.draw_line(fitz.Point(x, rect.y0),
                       fitz.Point(x, rect.y0 + n_rows * row_h),
                       color=RULE, width=0.6)

    for row, values in enumerate([header] + rows):
        font = "hebo" if row == 0 else "helv"
        base_size = header_size if row == 0 else size
        for col, value in enumerate(values):
            text = str(value)
            x0 = xs[col] + 5.0
            x1 = xs[col + 1] - 5.0
            cell_w = x1 - x0
            use_size = base_size
            text_w = _measure(text, font, use_size)
            if text_w > cell_w > 0:
                use_size = max(4.5, use_size * cell_w / text_w)
                text_w = _measure(text, font, use_size)
            align = aligns[col] if aligns else ("left" if col == 0 else "center")
            if align == "center":
                x = x0 + (cell_w - text_w) / 2.0
            elif align == "right":
                x = x1 - text_w
            else:
                x = x0
            baseline = rect.y0 + row * row_h + row_h / 2.0 + use_size * 0.36
            page.insert_text((x, baseline), text, fontname=font,
                             fontsize=use_size, color=INK)


# ------------------------------------------------------------------- figures
def _figure_page(width: float, height: float, scale: float = 3.0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    return doc, page, fitz.Matrix(scale, scale)


def _png(doc, page, matrix) -> bytes:
    pixmap = page.get_pixmap(matrix=matrix, alpha=False)
    data = pixmap.tobytes("png")
    doc.close()
    return data


def figure_pipeline() -> bytes:
    """Full-width schematic: extraction -> gutter -> bands -> reflow."""
    width, height = CONTENT_W, 200.0
    doc, page, matrix = _figure_page(width, height)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=(1, 1, 1))

    labels = ["Text and media\nboxes", "Gutter and\nbands", "Column-major\norder",
              "Reflow\ncolumn"]
    box_w, box_h, gap = 102.0, 36.0, 27.0
    total = len(labels) * box_w + (len(labels) - 1) * gap
    x = (width - total) / 2.0
    for index, label in enumerate(labels):
        rect = fitz.Rect(x, 12.0, x + box_w, 12.0 + box_h)
        page.draw_rect(rect, color=ACCENT, fill=(0.93, 0.96, 1.0), width=0.9)
        page.insert_textbox(rect, label, fontname="hebo", fontsize=8.0,
                            color=ACCENT, align=fitz.TEXT_ALIGN_CENTER)
        if index < len(labels) - 1:
            start = fitz.Point(rect.x1 + 3.0, 30.0)
            end = fitz.Point(rect.x1 + gap - 3.0, 30.0)
            page.draw_line(start, end, color=ACCENT, width=1.0)
            page.draw_line(end, fitz.Point(end.x - 4.0, end.y - 3.0),
                           color=ACCENT, width=1.0)
            page.draw_line(end, fitz.Point(end.x - 4.0, end.y + 3.0),
                           color=ACCENT, width=1.0)
        x += box_w + gap

    # Miniature two-column page with a full-width band between the columns.
    px, py, pw, ph = 150.0, 68.0, 195.0, 122.0
    page.draw_rect(fitz.Rect(px, py, px + pw, py + ph),
                   color=(0.45, 0.45, 0.45), width=0.7)

    def band(y0, y1, fill, label):
        page.draw_rect(fitz.Rect(px + 8, y0, px + pw - 8, y1),
                       color=None, fill=fill)
        if label:
            page.insert_text((px + pw / 2.0 - 26.0, (y0 + y1) / 2.0 + 2.2),
                             label, fontname="helv", fontsize=5.6,
                             color=(0.25, 0.25, 0.25))

    band(py + 6, py + 14, (0.16, 0.34, 0.58), "")
    page.insert_text((px + 10, py + 12), "Title (page-wide)", fontname="hebo",
                     fontsize=5.4, color=(1, 1, 1))

    def lines(x0, x1, y0, y1):
        y = y0
        while y < y1:
            page.draw_line(fitz.Point(x0, y), fitz.Point(x1, y), color=(0.65, 0.65, 0.65),
                           width=0.55)
            y += 4.0

    left_x0, left_x1 = px + 8, px + 90
    right_x0, right_x1 = px + 105, px + pw - 8
    lines(left_x0, left_x1, py + 20, py + 42)
    lines(right_x0, right_x1, py + 20, py + 42)
    band(py + 48, py + 64, (0.97, 0.90, 0.78), "full-width figure / table")
    lines(left_x0, left_x1, py + 70, py + 112)
    lines(right_x0, right_x1, py + 70, py + 112)

    gutter_x = (left_x1 + right_x0) / 2.0
    for y in range(int(py + 20), int(py + 113), 6):
        page.draw_line(fitz.Point(gutter_x, y), fitz.Point(gutter_x, y + 3),
                       color=(0.75, 0.25, 0.25), width=1.0)
    page.insert_text((gutter_x - 18, py + 119), "gutter", fontname="helv",
                     fontsize=6.0, color=(0.75, 0.25, 0.25))

    page.insert_text((px - 50, py + 40), "band 1", fontname="helv", fontsize=6.0,
                     color=MUTED)
    page.insert_text((px - 50, py + 80), "band 2", fontname="helv", fontsize=6.0,
                     color=MUTED)
    page.draw_line(fitz.Point(px - 48, py + 43), fitz.Point(px - 2, py + 43),
                   color=MUTED, width=0.5)
    page.draw_line(fitz.Point(px - 48, py + 83), fitz.Point(px - 2, py + 83),
                   color=MUTED, width=0.5)
    return _png(doc, page, matrix)


def figure_accuracy() -> bytes:
    """Column figure: horizontal bars with reading-order accuracy."""
    width, height = COL_W, 142.0
    doc, page, matrix = _figure_page(width, height, scale=4.0)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=(1, 1, 1))

    rows = [
        ("Full-width figures", 100),
        ("Column figures", 98),
        ("Column tables", 97),
        ("Page numbers", 100),
        ("Scanned pages", 99),
    ]
    label_x = 88.0
    bar_x = 92.0
    bar_max = 126.0
    top = 18.0
    step = 19.0
    page.insert_text((8.0, 10.0), "Reading-order accuracy (%)",
                     fontname="hebo", fontsize=7.6, color=INK)
    for index, (label, value) in enumerate(rows):
        y = top + index * step
        page.insert_text((label_x - 4.0 - _measure(label, "helv", 6.8), y + 8.0),
                         label, fontname="helv", fontsize=6.8, color=INK)
        page.draw_rect(fitz.Rect(bar_x, y, bar_x + bar_max, y + 10.0),
                       color=(0.82, 0.82, 0.82), width=0.4)
        page.draw_rect(fitz.Rect(bar_x, y, bar_x + bar_max * value / 100.0, y + 10.0),
                       color=None, fill=ACCENT)
        page.insert_text((bar_x + bar_max + 4.0, y + 8.0), f"{value}",
                         fontname="helv", fontsize=6.8, color=INK)
    baseline_y = top + len(rows) * step + 2.0
    page.draw_line(fitz.Point(bar_x, baseline_y - 4.0),
                   fitz.Point(bar_x, baseline_y + 2.0), color=MUTED, width=0.6)
    for tick in (0, 25, 50, 75, 100):
        x = bar_x + bar_max * tick / 100.0
        page.draw_line(fitz.Point(x, baseline_y - 2.0),
                       fitz.Point(x, baseline_y + 2.0), color=MUTED, width=0.6)
        page.insert_text((x - 3.0, baseline_y + 11.0), str(tick),
                         fontname="helv", fontsize=6.2, color=MUTED)
    return _png(doc, page, matrix)


def figure_cost() -> bytes:
    """Small column figure: cost of each reflow stage."""
    width, height = COL_W, 96.0
    doc, page, matrix = _figure_page(width, height, scale=4.0)
    page.draw_rect(fitz.Rect(0, 0, width, height), color=None, fill=(1, 1, 1))

    rows = [("Text extraction", 31), ("Figures", 78),
            ("Tables", 107), ("Column analysis", 4)]
    label_x = 82.0
    bar_x = 86.0
    bar_max = 124.0
    top = 20.0
    step = 16.5
    page.insert_text((8.0, 11.0), "Reflow cost (ms per page)",
                     fontname="hebo", fontsize=7.4, color=INK)
    for index, (label, value) in enumerate(rows):
        y = top + index * step
        page.insert_text((label_x - 4.0 - _measure(label, "helv", 6.4), y + 7.0),
                         label, fontname="helv", fontsize=6.4, color=INK)
        page.draw_rect(fitz.Rect(bar_x, y, bar_x + bar_max, y + 9.0),
                       color=(0.82, 0.82, 0.82), width=0.4)
        page.draw_rect(
            fitz.Rect(bar_x, y, bar_x + bar_max * min(1.0, value / 110.0), y + 9.0),
            color=None, fill=(0.26, 0.56, 0.42))
        page.insert_text((bar_x + bar_max + 4.0, y + 7.0), f"{value}",
                         fontname="helv", fontsize=6.4, color=INK)
    return _png(doc, page, matrix)


# --------------------------------------------------------------------- pages
def _centered(page, y, text, size, font="helv", color=INK) -> None:
    width = _measure(text, font, size)
    page.insert_text(((PAGE_W - width) / 2.0, y), text, fontname=font,
                     fontsize=size, color=color)


def footer(page, number: int) -> None:
    page.draw_line(fitz.Point(MARGIN_L, 802.0),
                   fitz.Point(PAGE_W - MARGIN_R, 802.0),
                   color=(0.85, 0.85, 0.85), width=0.5)
    text = str(number)
    width = _measure(text, "helv", 8.4)
    page.insert_text(((PAGE_W - width) / 2.0, 817.0), text, fontname="helv",
                     fontsize=8.4, color=MUTED)


ABSTRACT = (
    "**Abstract.** Two-column layouts are the norm in scientific publishing, "
    "but most PDF reflow engines linearize pages strictly by vertical position "
    "and therefore interleave the left and right columns line by line. We "
    "describe a column-aware reflow pipeline for PDF-Advanced-Reader that "
    "detects the gutter between columns, partitions each page into horizontal "
    "bands at page-wide elements such as titles, full-width figures and "
    "tables, and emits the text and media of every band in column-major order. "
    "Raster figures and ruled tables are rendered inline at their reading "
    "position, and scanned pages fall back to fit-to-column page images. On a "
    "small annotated corpus the pipeline preserved the intended reading order "
    "for 97% of the elements, without modifying the document."
)

KEYWORDS = (
    "**Keywords:** PDF reflow; two-column layout; reading order; document "
    "analysis; accessibility"
)

INTRO = [
    "Scientific articles are usually typeset in two columns so that a printed "
    "page can carry more text at a comfortable measure. The same layout "
    "becomes an obstacle on phones, tablets and narrow windows: the reader "
    "must pan horizontally or zoom out until the text is too small to read. "
    "Reflow addresses the problem by discarding the fixed page geometry and "
    "re-wrapping the text into a column that fits the screen.",
    "Removing the geometry is easy; restoring the reading order is not. A "
    "naive implementation sorts every text block by its vertical position and "
    "interleaves the columns: the first line of the left column is followed by "
    "the first line of the right column, then by the second line of each "
    "column, and so on. The result is grammatically valid text that reads like "
    "a conversation between two authors talking at once.",
    "Page-wide elements make the problem harder. Titles, abstracts, section "
    "banners, full-width figures and tables interrupt both columns and must be "
    "read at the exact point where they appear. A figure placed in the middle "
    "of a page belongs between the paragraph above it and the paragraph below "
    "it, but a column figure belongs only to its own column. A single global "
    "sort cannot express either relationship.",
    "This paper presents the column-aware reading order used by "
    "PDF-Advanced-Reader. The pipeline detects the gutter from the left edges "
    "of the text and media boxes, classifies items that cross the gutter as "
    "page-wide, splits the page into vertical bands around them, and "
    "sorts the remaining items by column and then by vertical position. The "
    "algorithm is deliberately conservative: when the evidence for two columns "
    "is weak, it falls back to the familiar top-to-bottom order rather than "
    "guessing.",
    "The rest of the paper is organized as follows. Section 2 reviews related "
    "work. Section 3 describes gutter detection, band ordering and media "
    "packing. Section 4 presents the evaluation, and Section 5 discusses "
    "limitations and concludes.",
]

CONTRIBUTIONS = [
    "This work makes three practical contributions. First, it defines a gutter "
    "detector that needs no machine learning and runs in a few milliseconds "
    "per page. Second, it introduces band segmentation, which keeps page-wide "
    "figures and tables exactly where the author placed them. Third, it packs "
    "text, raster figures and ruled tables into the same reading flow, so a "
    "reflowed page no longer loses its illustrations.",
    "The implementation reuses the background worker that already extracts "
    "text for reflow. No second pass over the document is required, and a "
    "document with hundreds of pages remains scrollable while its layouts are "
    "being prepared.",
]

RELATED = [
    "Reading-order recovery has a long history in document analysis. Classical "
    "systems segment a page into regions, classify each region as text, image "
    "or table, and then order the regions with heuristics derived from the "
    "layout. Rule-based methods work well on clean scans, but they require "
    "thresholds that are difficult to tune across publishers.",
    "Layout-analysis methods based on geometric features, such as the "
    "recursive X-Y cut, split a page along whitespace valleys. The approach "
    "naturally handles two columns, because the gutter is the widest vertical "
    "valley. X-Y cut was extended with statistical models and, more recently, "
    "with learned region detectors. These systems produce accurate page "
    "segmentation, but they usually stop at the region level and do not "
    "describe how text and figures should be interleaved inside a reflow "
    "column.",
    "PDF-specific approaches exploit the drawing operators and the content "
    "stream. The position of every glyph is known, so columns can be recovered "
    "from the distribution of glyph coordinates. A common strategy clusters "
    "text by x position and treats the largest gap between clusters as the "
    "gutter. The strategy fails on centered titles, on tables of contents and "
    "on pages with a single wide column; those cases need explicit checks.",
    "Commercial readers generally do not reflow two-column PDFs; they zoom and "
    "pan instead. Some mobile readers offer a text-extraction view that "
    "concatenates the content stream order. That order reflects the authoring "
    "tool rather than the visual layout, so it is reliable only when the "
    "producer emitted the columns in reading order.",
    "Our pipeline combines start clustering with band segmentation and shares "
    "the extraction pass with media rendering, so a two-column page costs only "
    "a few extra milliseconds.",
]

METHOD_1 = [
    "The detector receives the bounding boxes of every text paragraph, raster "
    "image and detected table on the page. Blocks wider than 55% of the "
    "content area are discarded first: they are titles, abstracts, full-width "
    "figures or captions, and including them would blur the columns.",
    "The remaining boxes are grouped by their left edge. A two-column page "
    "produces two strong start clusters, one per column, while centred "
    "metadata and page numbers produce isolated starts that are ignored, and "
    "the grouping tolerance absorbs first-line indents. The widest separation "
    "between populated clusters becomes the candidate gutter, refined to the "
    "midpoint between the columns' inner edges. It must be at least 8 pt wide, "
    "or 2% of the content width.",
    "Three sanity checks reject false positives. The median widths of the two "
    "sides must be within a factor of two, so a narrow sidebar is not mistaken "
    "for a column. The vertical spans of the two sides must overlap by at "
    "least 20% of the shorter span, so a table of contents with a single body "
    "column is rejected. And the inner edges of the two sides must not "
    "overlap, which filters out ragged single-column pages. If any check "
    "fails, reflow keeps the simple top-to-bottom order.",
]

METHOD_2 = [
    "Once a gutter is known, every item whose box crosses it becomes a band "
    "separator. A margin of 4 pt absorbs rounding errors, so a caption that "
    "ends a couple of points before the gutter is not treated as page-wide. "
    "Band separators are read in their own vertical position: the title first, "
    "then a full-width figure between the text that surrounds it.",
    "Items that stay inside a column are accumulated until the next separator "
    "and then sorted by column, vertical position and horizontal position, in "
    "that order. The left column is therefore emitted completely before the "
    "right column of the same band. Figures and tables carry the source "
    "rectangle they came from, so media inside a column keep their place "
    "relative to the paragraphs around them.",
    "A footer page number is a special but harmless case. It crosses the "
    "gutter, yet it is not page-wide content; because its vertical position is "
    "the largest on the page, it is appended after both columns of the final "
    "band. This matches what a reader does when reaching the bottom of a page.",
    "The algorithm is applied per page and does not depend on the previous "
    "page. That keeps the worker stateless and allows pages to be prepared "
    "independently, in any order, while the user scrolls.",
]

METHOD_3 = [
    "Media extraction runs after the text pass and reuses the same page. "
    "Raster figures are located with the image inventory of the page; an image "
    "that covers at least 85% of a page which also has text is treated as a "
    "background and skipped, so the text remains reflowable. Ruled tables are "
    "found only when the page contains enough long horizontal and vertical "
    "strokes, which avoids the cost of a full table search on ordinary pages.",
    "Each region is rendered to PNG at 1.5 times its display resolution, "
    "bounded by a 2.5-megapixel budget. The worker transports the encoded "
    "bytes; the viewer decodes them lazily, only when the figure becomes "
    "visible, and keeps a bounded cache. Text that belongs to a rendered table "
    "is removed from the paragraph list, so cell contents are not duplicated.",
    "Tables are displayed at no less than 60% of the reflow column width, "
    "which keeps small result tables legible. Figures narrower than the column "
    "keep their natural size and are centred. A page with no text layer, such "
    "as a scanned article, is shown as a single fit-to-column page image "
    "instead of being reported as empty.",
]

METHOD_4 = [
    "Column analysis is deliberately cheap. It runs on the boxes that "
    "extraction already produced, after media regions have been removed from "
    "the text, so it never re-reads the page. The stage is a sort plus a few "
    "group tests, and its result is cached with the page layout. When the "
    "reader changes the text size, only the wrapping is repeated; the gutter "
    "and the reading order are reused.",
    "Two properties make the stage easy to trust. It never drops an item: "
    "every paragraph and every rendered region is assigned exactly one "
    "position in the sequence. And it is idempotent: the same page always "
    "produces the same order, which keeps the scroll position stable when the "
    "viewer re-lays out a page.",
    "The only state carried between pages is the document handle owned by the "
    "worker. Because pages are independent, they can be prepared out of order "
    "and the viewer can show a later page while an earlier one is still being "
    "analyzed.",
]

EVAL = [
    "We evaluated the pipeline on six documents: this four-page demo, three "
    "two-column journal articles, one single-column technical report and one "
    "scanned paper. For each page we annotated the intended reading order of "
    "the text blocks and of the rendered media, then compared it with the "
    "sequence produced by the reflow worker.",
    "Table 2 summarizes the results. The gutter was found on all five pages "
    "with a two-column body, and the reading order was correct for 97% of the "
    "annotated elements. The two errors came from one article whose right "
    "column contains a wide caption that touches the gutter, and from a page "
    "with a sidebar that is as wide as a column. In both cases the fallback "
    "kept the output readable: text was still complete, only the position of "
    "one figure and one paragraph changed.",
    "Column analysis itself is cheap. On an 80-page report the complete "
    "pipeline costs about 168 ms per page, of which text extraction accounts "
    "for 31 ms, figure rendering for 78 ms and table detection for 107 ms; the "
    "projection and band ordering add about 4 ms. Table detection dominates "
    "because it can only run after a page has been rasterized for inspection.",
    "The single-column report never produced a gutter, as intended. The "
    "scanned paper has no text layer, so it was shown as page images and was "
    "not included in the order accuracy figure. These two outcomes are the "
    "documented fallbacks rather than failures of the detector.",
]

CONCLUSION = [
    "Two-column scientific papers are the hardest common case for PDF reflow, "
    "not because the columns are difficult to find but because page-wide "
    "elements break the assumption that a page has a single linear order. "
    "Gutter detection plus band segmentation restores that order with a small, "
    "deterministic algorithm that is easy to inspect and to explain.",
    "The approach assumes two columns of similar width, does not reconstruct "
    "reading order inside complex tables, and can misclassify a page-wide "
    "element that touches the gutter. When the evidence is weak, reflow falls "
    "back to top-to-bottom order, which is imperfect but predictable; sidebars "
    "and learned classifiers are future work.",
    "In PDF-Advanced-Reader, **View > Reflow** reads each column in turn and "
    "keeps every figure and table in place.",
]

REFERENCES = [
    "Breuel, T. M. (2002). Two geometric algorithms for layout analysis. In "
    "Proceedings of the Workshop on Document Analysis Systems, 188-199.",
    "Cattoni, R., Coianiz, T., Messelodi, S., and Modena, C. M. (1998). "
    "Geometric layout analysis techniques for document image understanding: "
    "a review. Technical Report 9703-09, ITC-IRST, Trento.",
    "Dengel, A., and Shafait, F. (2014). Analysis of the logical layout of "
    "documents. In Handbook of Document Image Processing and Recognition, "
    "177-222. Springer.",
    "Ha, J., Haralick, R. M., and Phillips, I. T. (1995). Recursive X-Y cut "
    "using bounding boxes of connected components. In Proceedings of the "
    "International Conference on Document Analysis and Recognition, 952-955.",
    "Lovegrove, W. S., and Brailsford, D. F. (1995). Document analysis of PDF "
    "files: methods, results and implications. Electronic Publishing, 8(2-3), "
    "207-220.",
    "Nagy, G. (2000). Twenty years of document image analysis in PAMI. IEEE "
    "Transactions on Pattern Analysis and Machine Intelligence, 22(1), 38-62.",
    "O'Gorman, L. (1993). The document spectrum for page layout analysis. IEEE "
    "Transactions on Pattern Analysis and Machine Intelligence, 15(11), "
    "1162-1173.",
    "PDFAR Working Group (2026). Column-aware reflow in PDF-Advanced-Reader. "
    "Technical Report PDFAR-2026-01, Document Interaction Lab.",
    "Rosenfeld, K. (2023). Reading order in born-digital PDFs: a "
    "practitioner's guide. Journal of Digital Publishing, 8(2), 44-59.",
    "Shafait, F., Keysers, D., and Breuel, T. M. (2008). Efficient "
    "implementation of the recursive X-Y cut algorithm for page layout "
    "analysis. In Document Recognition and Retrieval XV, 6815, 45-56.",
]

TABLE_2_ROWS = [
    ("Article A", "14", "Two-column", "Yes", "100%", "168 ms"),
    ("Article B", "8", "Two-column + bands", "Yes", "100%", "191 ms"),
    ("Article C", "22", "Two-column", "Yes", "96%", "173 ms"),
    ("Article D", "6", "Single column", "No", "100%", "142 ms"),
    ("Article E", "31", "Two-column + sidebar", "Yes", "94%", "205 ms"),
    ("Scanned F", "12", "Image only", "n/a", "n/a", "88 ms"),
    ("Mean", "15.5", "-", "5 of 6", "97%", "161 ms"),
]


def build_page1(doc) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    _centered(page, 50.0,
              "JOURNAL OF DOCUMENT REFLOW  |  VOL. 12, NO. 3  |  2026",
              7.2, "helv", MUTED)
    page.draw_line(fitz.Point(MARGIN_L, 57.0),
                   fitz.Point(PAGE_W - MARGIN_R, 57.0), color=RULE, width=0.7)

    _centered(page, 94.0, "Column-Aware Reflow: Reading Two-Column",
              19.0, "hebo")
    _centered(page, 116.0, "Scientific PDFs on Small Screens", 19.0, "hebo")
    _centered(page, 140.0, "Ana Martin, Bruno Costa, and Chen Wei", 10.5)
    _centered(page, 154.0,
              "Document Interaction Lab, PDF Advanced Reader Project", 8.6,
              "heit", MUTED)
    _centered(page, 165.0,
              "Received 12 March 2026; accepted 2 September 2026; "
              "published 24 October 2026.", 7.0, "helv", MUTED)

    box = fitz.Rect(MARGIN_L, 174.0, PAGE_W - MARGIN_R, 302.0)
    page.draw_rect(box, color=None, fill=(0.955, 0.965, 0.985))
    abstract = Flow(page, [fitz.Rect(box.x0 + 9, box.y0 + 6,
                                    box.x1 - 9, box.y1 - 4)])
    abstract.paragraph(ABSTRACT, size=8.8, leading=11.2, indent=0.0,
                       space_after=5.0)
    abstract.paragraph(KEYWORDS, size=8.2, leading=10.6, indent=0.0,
                       space_after=0.0)

    flow = Flow(page, [
        fitz.Rect(MARGIN_L, 314.0, MARGIN_L + COL_W, BOTTOM),
        fitz.Rect(RIGHT_X, 314.0, RIGHT_X + COL_W, BOTTOM),
    ])
    flow.heading("1. Introduction", size=11.2, space_before=0.0)
    for text in INTRO:
        flow.paragraph(text)
    flow.heading("1.1 Contributions", size=10.0, space_before=6.0)
    for text in CONTRIBUTIONS:
        flow.paragraph(text)
    footer(page, 1)


def build_page2(doc, pipeline_png: bytes) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    top = Flow(page, [
        fitz.Rect(MARGIN_L, TOP, MARGIN_L + COL_W, 298.0),
        fitz.Rect(RIGHT_X, TOP, RIGHT_X + COL_W, 298.0),
    ])
    top.heading("2. Related Work", size=11.2, space_before=0.0)
    for text in RELATED:
        top.paragraph(text)

    figure = Flow(page, [fitz.Rect(MARGIN_L, 302.0, PAGE_W - MARGIN_R, 544.0)])
    figure.figure(
        pipeline_png, 196.0,
        "**Figure 1.** Column-aware reflow pipeline. Text and media boxes are "
        "grouped by their left edge to find the gutter; items that cross it "
        "split the page into bands, and each band is emitted left column "
        "first, then right column.",
        space_before=0.0, space_after=0.0)

    bottom = Flow(page, [
        fitz.Rect(MARGIN_L, 552.0, MARGIN_L + COL_W, BOTTOM),
        fitz.Rect(RIGHT_X, 552.0, RIGHT_X + COL_W, BOTTOM),
    ])
    bottom.heading("3. Method", size=11.2, space_before=0.0)
    bottom.paragraph(
        "The pipeline has three stages: text and media extraction, gutter "
        "detection with band ordering, and layout of the resulting flow. "
        "Each stage is deterministic and operates on one page at a time.",
        space_after=2.0)
    bottom.heading("3.1 Gutter Detection", size=10.0, space_before=4.0)
    for text in METHOD_1:
        bottom.paragraph(text)
    footer(page, 2)


def build_page3(doc, accuracy_png: bytes, cost_png: bytes) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    # Column table at the top of the right column.
    caption = Flow(page, [fitz.Rect(RIGHT_X, TOP, RIGHT_X + COL_W, 70.0)])
    caption.paragraph(
        "**Table 1.** Classification and placement of the elements that occur "
        "on a two-column page.", size=CAPTION_SIZE, leading=CAPTION_LEADING,
        indent=0.0, space_after=2.0)
    draw_table(
        page, fitz.Rect(RIGHT_X, 72.0, RIGHT_X + COL_W, 168.0),
        [68.0, 82.0, COL_W - 150.0],
        ["Element", "Detected as", "Position"],
        [("Title", "Page-wide band", "First"),
         ("Left column", "Column 1", "Top to bottom"),
         ("Full-width figure", "Page-wide band", "Between bands"),
         ("Column figure", "Column 2", "Next to text"),
         ("Page number", "Gutter crossing", "End of page")],
        size=6.6, header_size=6.8, row_height=16.0)

    flow = Flow(page, [
        fitz.Rect(MARGIN_L, TOP, MARGIN_L + COL_W, BOTTOM),
        fitz.Rect(RIGHT_X, 178.0, RIGHT_X + COL_W, BOTTOM),
    ])
    flow.figure(
        accuracy_png, 140.0,
        "**Figure 2.** Reading-order accuracy per element type in the "
        "annotated corpus.", space_before=0.0, space_after=7.0)
    flow.heading("3.2 Bands and Column-Major Order", size=10.0,
                 space_before=0.0)
    for text in METHOD_2:
        flow.paragraph(text)
    flow.figure(
        cost_png, 96.0,
        "**Figure 3.** Reflow cost per stage on an 80-page report. Column "
        "analysis adds about 4 ms; table detection dominates.",
        space_before=4.0, space_after=8.0)
    flow.heading("3.3 Text, Figures and Tables", size=10.0, space_before=2.0)
    for text in METHOD_3:
        flow.paragraph(text)
    flow.heading("3.4 Implementation Notes", size=10.0, space_before=4.0)
    for text in METHOD_4:
        flow.paragraph(text)
    footer(page, 3)


def build_page4(doc) -> None:
    page = doc.new_page(width=PAGE_W, height=PAGE_H)

    caption = Flow(page, [fitz.Rect(MARGIN_L, TOP, PAGE_W - MARGIN_R, 70.0)])
    caption.paragraph(
        "**Table 2.** Evaluation of the column-aware reading order. Time is "
        "the complete reflow preparation cost per page, including media "
        "rendering.", size=CAPTION_SIZE, leading=CAPTION_LEADING, indent=0.0,
        space_after=2.0)
    draw_table(
        page, fitz.Rect(MARGIN_L, 72.0, PAGE_W - MARGIN_R, 252.0),
        [78.0, 50.0, 125.0, 52.0, 58.0, CONTENT_W - 363.0],
        ["Document", "Pages", "Layout", "Gutter", "Order", "Time"],
        TABLE_2_ROWS, size=7.6, header_size=7.8, row_height=22.5)

    body = Flow(page, [
        fitz.Rect(MARGIN_L, 266.0, MARGIN_L + COL_W, 578.0),
        fitz.Rect(RIGHT_X, 266.0, RIGHT_X + COL_W, 578.0),
    ])
    body.heading("4. Evaluation", size=11.2, space_before=0.0)
    for text in EVAL:
        body.paragraph(text)
    body.heading("5. Discussion and Conclusion", size=11.2, space_before=6.0)
    for text in CONCLUSION:
        body.paragraph(text, size=BODY_SIZE - 0.3, leading=BODY_LEADING - 0.3)

    page.draw_line(fitz.Point(MARGIN_L, 580.0),
                   fitz.Point(PAGE_W - MARGIN_R, 580.0), color=RULE, width=0.6)
    _centered(page, 592.0, "References", 11.2, "hebo")

    refs = Flow(page, [
        fitz.Rect(MARGIN_L, 600.0, MARGIN_L + COL_W, BOTTOM),
        fitz.Rect(RIGHT_X, 600.0, RIGHT_X + COL_W, BOTTOM),
    ])
    for entry in REFERENCES:
        refs.paragraph(entry, size=8.0, leading=10.3, indent=0.0,
                       space_after=2.6)
    footer(page, 4)


def build(out_path: Path) -> Path:
    pipeline = figure_pipeline()
    accuracy = figure_accuracy()
    cost = figure_cost()

    doc = fitz.open()
    build_page1(doc)
    build_page2(doc, pipeline)
    build_page3(doc, accuracy, cost)
    build_page4(doc)

    doc.set_metadata({
        "title": "Column-Aware Reflow: Reading Two-Column Scientific "
                 "PDFs on Small Screens",
        "author": "Ana Martin, Bruno Costa, Chen Wei",
        "subject": "Demo article for PDF-Advanced-Reader two-column reflow",
        "keywords": "PDF reflow, two-column layout, reading order",
        "creator": "make_two_column_demo.py",
    })
    doc.set_toc([
        [1, "1. Introduction", 1],
        [1, "2. Related Work", 2],
        [1, "3. Method", 2],
        [2, "3.1 Gutter Detection", 2],
        [2, "3.2 Bands and Column-Major Order", 3],
        [2, "3.3 Text, Figures and Tables", 3],
        [1, "4. Evaluation", 4],
        [1, "5. Discussion and Conclusion", 4],
        [1, "References", 4],
    ])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path), garbage=3, deflate=True)
    page_count = doc.page_count
    doc.close()
    print(f"wrote {out_path} ({page_count} pages, "
          f"{out_path.stat().st_size / 1024:.0f} KiB)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"output PDF (default: {DEFAULT_OUT})")
    args = parser.parse_args()
    build(args.out)


if __name__ == "__main__":
    main()
