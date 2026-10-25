"""P1-5 — Reflow (single-column reading) support.

A PDF is a fixed-layout format: text is positioned absolutely on the page, so
it cannot reflow like an EPUB without inventing a new layout.  This module
does the next best thing for most PDFs:

* extract the text blocks of every page (PyMuPDF ``dict`` mode),
* render the page's raster images and detected tables to PNG,
* drop the original geometry and group the text into paragraphs,
* interleave images/tables with the text in reading order and wrap it all into
  a single column whose width fits the viewport.

Images and tables are rendered by the worker and transported as PNG bytes.
The GUI decodes them lazily (only when visible) and keeps a bounded cache, so
a document with hundreds of figures stays cheap in memory.

Scanned pages have no text layer but usually consist of one page-sized image;
they are shown as fit-to-column page images.  Only pages with neither text nor
media are reported as empty.

Threading (Golden Rules 2.1 / 2.2)
----------------------------------
:class:`ReflowWorker` is a ``QThread`` that opens its **own** ``fitz.Document``;
the GUI document is never shared with it.  Only plain dataclasses cross the
thread boundary through Qt signals.  ``QFontMetrics`` is reentrant and is used
only to measure text, never to touch a widget.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont, QFontMetrics

# ---------------------------------------------------------------- geometry
REFLOW_MAX_WIDTH = 760      # comfortable reading measure (px)
REFLOW_MIN_WIDTH = 240
REFLOW_MARGIN = 48          # outer margin on each side (px)
REFLOW_LINE_SPACING = 1.45
REFLOW_PAGE_GAP = 28        # vertical space between page sections (px)
REFLOW_EMPTY_PAGE_HEIGHT = 80

# PDF points -> screen pixels.  1:1 is unreadably small on a HiDPI screen.
REFLOW_SIZE_SCALE = 1.25
REFLOW_MIN_FONT = 10
REFLOW_MAX_FONT = 34

# User text-size multiplier (Ctrl +/- in reflow).  It is applied at layout
# time so the extracted document stays untouched and can be re-wrapped at any
# size without re-reading the PDF.
REFLOW_FONT_STEP = 1.15
REFLOW_FONT_SCALE_MIN = 0.6
REFLOW_FONT_SCALE_MAX = 3.0
REFLOW_UI_MIN_FONT = 8
REFLOW_UI_MAX_FONT = 72

# Extract text only: TEXT_PRESERVE_IMAGES would copy every embedded image
# bitmap through the dict, which is by far the most expensive part on scanned
# or illustration-heavy documents.  Images are rendered separately instead.
_DICT_FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES

# Embedded images and tables
REFLOW_MEDIA_GAP = 14          # vertical space above/below a figure (px)
REFLOW_MEDIA_QUALITY = 1.5     # render px per displayed px (HiDPI headroom)
REFLOW_MEDIA_MAX_PIXELS = 2_500_000
REFLOW_MEDIA_MIN_POINTS = 24   # ignore icons and rules smaller than this (pt)
REFLOW_MEDIA_MERGE_GAP = 8     # merge image rects closer than this (pt)
REFLOW_MEDIA_BACKGROUND = 0.85  # page-sized image on a text page = background
REFLOW_MEDIA_TABLE_FILL = 0.6  # tables fill at least this share of the column
REFLOW_TABLE_MIN_H_LINES = 4   # cheap pre-check before running find_tables()
REFLOW_TABLE_MIN_V_LINES = 3
REFLOW_TABLE_MERGE_GAP = 4

_WS_RE = re.compile(r"\s+")


def reflow_text_width(viewport_width: int) -> int:
    """Column width in pixels for a viewport of *viewport_width* pixels."""
    return max(REFLOW_MIN_WIDTH, min(REFLOW_MAX_WIDTH, int(viewport_width) - 2 * REFLOW_MARGIN))


# ---------------------------------------------------------------- data model
@dataclass
class Run:
    """A styled text chunk.  Whitespace is normalized to single spaces."""

    text: str
    family: str
    size: int          # pixels
    bold: bool
    italic: bool

    def key(self) -> Tuple[str, int, bool, bool]:
        return (self.family, self.size, self.bold, self.italic)


@dataclass
class Paragraph:
    page: int
    runs: List[Run] = field(default_factory=list)
    top: float = 0.0      # source y in PDF points (reading order)
    left: float = 0.0


# A laid-out line: y/height/baseline in page-section coordinates and a list of
# (text, family, size, bold, italic) runs.  Tuples keep transport cheap.
LineRun = Tuple[str, str, int, bool, bool]


@dataclass
class Line:
    page: int
    y: float
    height: float
    baseline: float
    runs: List[LineRun]


@dataclass
class Media:
    """A raster image or a detected table, pre-rendered to PNG.

    ``width``/``height`` are display sizes in pixels; ``y`` is assigned by
    :func:`layout_paragraphs`.  Only the plain bytes cross the thread
    boundary — the GUI decodes them lazily for the visible page.
    """

    page: int
    kind: str             # "image" | "table"
    top: float            # source y in PDF points (reading order)
    left: float
    width: float
    height: float
    data: bytes = b""
    y: float = 0.0


@dataclass
class PageLayout:
    """Final, ready-to-paint layout of one page section."""

    page: int
    lines: List[Line] = field(default_factory=list)
    height: float = 0.0
    has_text: bool = False
    error: str = ""
    media: List[Media] = field(default_factory=list)


# ------------------------------------------------------------- font helpers
_font_cache: Dict[Tuple[str, int, bool, bool], QFont] = {}
_metrics_cache: Dict[Tuple[str, int, bool, bool], QFontMetrics] = {}


def _family(name: str) -> str:
    """Map a PDF font name to a Qt family name.

    PDF names look like ``ABCDEF+LiberationSans-Bold``: strip the subset
    prefix and the style suffix.  Unknown families simply fall back to the
    platform default.
    """
    name = (name or "").split("+", 1)[-1].strip()
    if not name:
        return "Sans Serif"
    base = re.split(r"[-,_]", name, maxsplit=1)[0]
    return base or name


def _pixel_size(pdf_size: float) -> int:
    try:
        px = int(round(float(pdf_size) * REFLOW_SIZE_SCALE))
    except (TypeError, ValueError):
        px = 14
    return max(REFLOW_MIN_FONT, min(REFLOW_MAX_FONT, px))


def _font(family: str, size: int, bold: bool, italic: bool) -> QFont:
    key = (family, size, bold, italic)
    font = _font_cache.get(key)
    if font is None:
        font = QFont(family)
        font.setPixelSize(size)
        font.setBold(bold)
        font.setItalic(italic)
        _font_cache[key] = font
    return font


def _metrics(family: str, size: int, bold: bool, italic: bool) -> QFontMetrics:
    key = (family, size, bold, italic)
    metrics = _metrics_cache.get(key)
    if metrics is None:
        metrics = QFontMetrics(_font(family, size, bold, italic))
        _metrics_cache[key] = metrics
    return metrics


def measure_text(text: str, family: str, size: int, bold: bool, italic: bool) -> float:
    return float(_metrics(family, size, bold, italic).horizontalAdvance(text))


def clamp_font_scale(scale: float) -> float:
    """Clamp a user text-size multiplier to the supported range."""
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    return max(REFLOW_FONT_SCALE_MIN, min(REFLOW_FONT_SCALE_MAX, value))


def scaled_font_size(size: int, scale: float = 1.0) -> int:
    """Apply the user multiplier to an extracted pixel size."""
    try:
        px = int(round(float(size) * clamp_font_scale(scale)))
    except (TypeError, ValueError):
        px = int(size)
    return max(REFLOW_UI_MIN_FONT, min(REFLOW_UI_MAX_FONT, px))


# --------------------------------------------------------------- extraction
def _span_run(span: Dict) -> Optional[Run]:
    raw = span.get("text") or ""
    text = _WS_RE.sub(" ", raw).strip()
    if not text:
        return None
    name = span.get("font") or ""
    flags = int(span.get("flags") or 0)
    lowered = name.lower()
    bold = bool(flags & 16) or "bold" in lowered or "black" in lowered
    italic = bool(flags & 2) or "italic" in lowered or "oblique" in lowered
    return Run(
        text=text,
        family=_family(name),
        size=_pixel_size(span.get("size") or 11),
        bold=bold,
        italic=italic,
    )


def _text_dict(page) -> Dict:
    try:
        return page.get_text("dict", flags=_DICT_FLAGS) or {}
    except Exception:
        return {}


def _extract_paragraphs(data: Dict, page_num: int,
                        regions: List[Tuple[object, str]]) -> List[Paragraph]:
    """Turn a PyMuPDF text dict into paragraphs, skipping media regions.

    Each text block becomes one paragraph.  Consecutive spans with the same
    style are merged so the result stays small even for long documents.  Spans
    that belong to a rendered table/image are dropped: their pixels are
    already part of that image.
    """
    paragraphs: List[Paragraph] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue          # image block: rendered separately as Media
        bbox = block.get("bbox") or (0, 0, 0, 0)
        para = Paragraph(page=page_num, top=float(bbox[1]),
                         left=float(bbox[0]))
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if _in_regions(span.get("bbox"), regions):
                    continue
                run = _span_run(span)
                if run is None:
                    continue
                if para.runs and para.runs[-1].key() == run.key():
                    para.runs[-1].text += " " + run.text
                else:
                    para.runs.append(run)
        if para.runs:
            paragraphs.append(para)
    return paragraphs


def extract_paragraphs(page, page_num: int = 0) -> List[Paragraph]:
    """Extract the text of *page* as a list of paragraphs (text only)."""
    return _extract_paragraphs(_text_dict(page), page_num, [])


# ---------------------------------------------------- images and tables
def _in_regions(bbox, regions: List[Tuple[object, str]]) -> bool:
    """True when *bbox* lies inside one of the rendered media regions."""
    if not regions or not bbox:
        return False
    try:
        rect = fitz.Rect(bbox)
    except Exception:
        return False
    if rect.is_empty:
        return False
    center = fitz.Point((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)
    area = abs(rect)
    for region, _kind in regions:
        if region.contains(center):
            return True
        overlap = rect & region
        if not overlap.is_empty and area > 0 and abs(overlap) >= 0.5 * area:
            return True
    return False


def _overlap_ratio(rect, container) -> float:
    try:
        overlap = fitz.Rect(rect) & fitz.Rect(container)
    except Exception:
        return 0.0
    area = abs(rect)
    return 0.0 if area <= 0 or overlap.is_empty else abs(overlap) / area


def _merge_rects(rects: List[object], gap: float) -> List[object]:
    """Union rectangles that overlap or are closer than *gap* points."""
    merged = [fitz.Rect(rect) for rect in rects]
    changed = True
    while changed:
        changed = False
        result: List[object] = []
        for rect in merged:
            for index, other in enumerate(result):
                grown = fitz.Rect(other.x0 - gap, other.y0 - gap,
                                  other.x1 + gap, other.y1 + gap)
                if grown.intersects(rect):
                    result[index] = other | rect
                    changed = True
                    break
            else:
                result.append(rect)
        merged = result
    return merged


def _line_counts(page) -> Tuple[int, int]:
    """Count long horizontal/vertical strokes, used to skip find_tables()."""
    horizontal = vertical = 0
    try:
        drawings = page.get_drawings()
    except Exception:
        return (0, 0)
    for path in drawings:
        for item in path.get("items", ()):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) <= 0.5 and abs(p2.x - p1.x) >= 40:
                    horizontal += 1
                elif abs(p1.x - p2.x) <= 0.5 and abs(p2.y - p1.y) >= 10:
                    vertical += 1
            elif kind == "re":
                rect = item[1]
                if rect.width >= 40:
                    horizontal += 2
                if rect.height >= 10:
                    vertical += 2
    return (horizontal, vertical)


def _table_rects(page) -> List[object]:
    """Table bounding boxes found on *page* (ruled tables only)."""
    if not hasattr(page, "find_tables"):
        return []
    horizontal, vertical = _line_counts(page)
    if (horizontal < REFLOW_TABLE_MIN_H_LINES
            or vertical < REFLOW_TABLE_MIN_V_LINES):
        return []           # find_tables() is expensive; most pages have no grid
    try:
        finder = page.find_tables()
    except Exception:
        return []
    rects = []
    for table in getattr(finder, "tables", ()):
        try:
            rect = fitz.Rect(table.bbox) & page.rect
        except Exception:
            continue
        if (rect.is_empty or rect.width < REFLOW_MEDIA_MIN_POINTS
                or rect.height < REFLOW_MEDIA_MIN_POINTS):
            continue
        rects.append(rect)
    return _merge_rects(rects, REFLOW_TABLE_MERGE_GAP)


def _image_rects(page, has_text: bool) -> List[object]:
    """Visible raster image placements, excluding page backgrounds."""
    try:
        infos = page.get_image_info()
    except Exception:
        return []
    page_rect = page.rect
    page_area = abs(page_rect)
    rects = []
    for info in infos:
        try:
            rect = fitz.Rect(info["bbox"]) & page_rect
        except Exception:
            continue
        if (rect.is_empty or rect.width < REFLOW_MEDIA_MIN_POINTS
                or rect.height < REFLOW_MEDIA_MIN_POINTS):
            continue
        if has_text and page_area > 0 and abs(rect) >= REFLOW_MEDIA_BACKGROUND * page_area:
            # A page-sized image under a text layer is a background/watermark;
            # reflowing the text is more useful than showing the raster.
            continue
        rects.append(rect)
    return _merge_rects(rects, REFLOW_MEDIA_MERGE_GAP)


def _media_regions(page, has_text: bool) -> List[Tuple[object, str]]:
    tables = _table_rects(page)
    regions: List[Tuple[object, str]] = [(rect, "table") for rect in tables]
    for rect in _image_rects(page, has_text):
        if any(_overlap_ratio(rect, table) >= 0.7 for table in tables):
            continue        # already part of the rendered table
        regions.append((rect, "image"))
    regions.sort(key=lambda item: (round(item[0].y0, 1), round(item[0].x0, 1)))
    return regions


def _render_media(page, rect, kind: str, page_num: int, width: int,
                  size_scale: float) -> Optional[Media]:
    """Render one image/table region to PNG at its reflow display size."""
    try:
        source_w = float(rect.width)
        source_h = float(rect.height)
    except (TypeError, ValueError):
        return None
    if source_w <= 0 or source_h <= 0:
        return None

    column = max(1, int(width))
    natural = source_w * REFLOW_SIZE_SCALE * size_scale
    if kind == "table":
        display_w = min(float(column),
                        max(natural, column * REFLOW_MEDIA_TABLE_FILL))
    else:
        display_w = min(float(column), natural)
    display_w = max(1.0, display_w)
    display_h = max(1.0, display_w * source_h / source_w)

    # Render above the display resolution for crisp text on HiDPI screens,
    # bounded by a pixel budget so a huge table cannot blow up memory.
    zoom = (display_w / source_w) * REFLOW_MEDIA_QUALITY
    budget_zoom = math.sqrt(REFLOW_MEDIA_MAX_PIXELS / max(1.0, source_w * source_h))
    zoom = max(0.05, min(zoom, budget_zoom))
    try:
        pixmap = page.get_pixmap(clip=rect, matrix=fitz.Matrix(zoom, zoom),
                                 alpha=(kind == "image"))
        data = pixmap.tobytes("png")
    except Exception:
        return None
    if not data:
        return None
    return Media(page=page_num, kind=kind, top=float(rect.y0),
                 left=float(rect.x0), width=display_w, height=display_h,
                 data=data)


def extract_page(page, page_num: int = 0, width: int = REFLOW_MAX_WIDTH,
                 size_scale: float = 1.0
                 ) -> Tuple[List[Paragraph], List[Media]]:
    """Extract text and render the images/tables of *page* for reflow."""
    data = _text_dict(page)
    has_text = any(block.get("type", 0) == 0 and block.get("lines")
                   for block in data.get("blocks", ()))
    regions = _media_regions(page, has_text)
    paragraphs = _extract_paragraphs(data, page_num, regions)
    scale = clamp_font_scale(size_scale)
    media = []
    for rect, kind in regions:
        item = _render_media(page, rect, kind, page_num, width, scale)
        if item is not None:
            media.append(item)
    return paragraphs, media


# ------------------------------------------------------------------- layout
def layout_paragraphs(
    paragraphs: List[Paragraph],
    width: int,
    page_num: int = 0,
    measure=measure_text,
    size_scale: float = 1.0,
    media: Optional[List[Media]] = None,
) -> PageLayout:
    """Wrap *paragraphs* into lines no wider than *width* pixels.

    ``measure(text, family, size, bold, italic) -> float`` is injectable so
    the wrapper can be unit-tested without a font backend.  ``size_scale``
    multiplies every run size before measuring and wrapping.

    When *media* is given, images/tables are interleaved with the text by
    their source y position so the column follows the page's reading order.
    """
    width = max(1, int(width))
    size_scale = clamp_font_scale(size_scale)
    lines: List[Line] = []
    placed_media: List[Media] = []
    y = 0.0

    def wrap_paragraph(para: Paragraph) -> None:
        nonlocal y
        words: List[Tuple[str, Run]] = []
        for run in para.runs:
            for word in run.text.split():
                words.append((word, run))
        if not words:
            return

        line_runs: List[LineRun] = []
        line_width = 0.0
        max_size = 0
        para_size = 0

        def flush() -> None:
            nonlocal y, line_runs, line_width, max_size
            if not line_runs:
                return
            height = max(REFLOW_MIN_FONT, max_size) * REFLOW_LINE_SPACING
            tallest = max(line_runs, key=lambda r: r[2])
            metrics = _metrics(tallest[1], tallest[2], tallest[3], tallest[4])
            ascent = float(metrics.ascent())
            descent = float(metrics.descent())
            baseline = y + max(0.0, (height - ascent - descent) / 2.0) + ascent
            lines.append(Line(page_num, y, height, baseline, line_runs))
            y += height
            line_runs = []
            line_width = 0.0
            max_size = 0

        for word, run in words:
            size = scaled_font_size(run.size, size_scale)
            word_w = measure(word, run.family, size, run.bold, run.italic)
            space_w = measure(" ", run.family, size, run.bold, run.italic)
            if line_runs and line_width + space_w + word_w > width:
                flush()
            if line_runs:
                line_runs.append((" ", run.family, size, run.bold, run.italic))
                line_width += space_w
            line_runs.append((word, run.family, size, run.bold, run.italic))
            line_width += word_w
            max_size = max(max_size, size)
            para_size = max(para_size, size)

        flush()
        # A visual gap separates paragraphs; headings (larger text) get more.
        y += min(20.0, max(5.0, para_size * 0.6))

    if media:
        items: List[Tuple[float, float, int, object]] = [
            (para.top, para.left, 0, para) for para in paragraphs if para.runs
        ]
        items.extend((item.top, item.left, 1, item) for item in media)
        items.sort(key=lambda entry: (round(entry[0], 1), round(entry[1], 1),
                                      entry[2]))
        for _top, _left, kind, item in items:
            if kind == 0:
                wrap_paragraph(item)
                continue
            if y > 0:
                y += REFLOW_MEDIA_GAP
            placed_media.append(replace(item, y=y))
            y += item.height
            y += REFLOW_MEDIA_GAP
    else:
        for para in paragraphs:
            wrap_paragraph(para)

    has_content = bool(lines or placed_media)
    return PageLayout(
        page=page_num,
        lines=lines,
        height=float(y) if has_content else float(REFLOW_EMPTY_PAGE_HEIGHT),
        has_text=bool(lines),
        media=placed_media,
    )


# ------------------------------------------------------------------- worker
class ReflowWorker(QThread):
    """Extract and lay out every page on a background thread.

    One instance per ``PDFView``; it owns its own document handle and is
    stopped with ``requestInterruption()`` + ``wait()`` (Golden Rule 2.5).
    """

    page_ready = pyqtSignal(object)   # PageLayout
    progress = pyqtSignal(int, int)   # (done, total)
    failed = pyqtSignal(str)

    def __init__(self, doc_path: str, width: int, password: str = "",
                 font_scale: float = 1.0, parent=None):
        super().__init__(parent)
        self.doc_path = doc_path
        self.width = int(width)
        self.password = password
        self.font_scale = clamp_font_scale(font_scale)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        doc = None
        try:
            doc = fitz.open(self.doc_path)
            if doc.needs_pass and self.password:
                doc.authenticate(self.password)
            total = doc.page_count
            step = max(1, total // 50)
            for i in range(total):
                if self.isInterruptionRequested():
                    return
                try:
                    paragraphs, media = extract_page(
                        doc.load_page(i), i, self.width, self.font_scale)
                    layout = layout_paragraphs(paragraphs, self.width, i,
                                               size_scale=self.font_scale,
                                               media=media)
                except Exception as exc:  # malformed page: keep going
                    layout = PageLayout(i, [], float(REFLOW_EMPTY_PAGE_HEIGHT),
                                        False, str(exc))
                if self.isInterruptionRequested():
                    return
                self.page_ready.emit(layout)
                if i % step == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)
        except Exception as exc:  # pragma: no cover - defensive
            self.failed.emit(str(exc))
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:
                    pass
