"""P1-5 — Reflow (single-column reading) support.

A PDF is a fixed-layout format: text is positioned absolutely on the page, so
it cannot reflow like an EPUB without inventing a new layout.  This module
does the next best thing for text-based PDFs:

* extract the text blocks of every page (PyMuPDF ``dict`` mode, images are
  **not** transferred — that is what makes extraction cheap),
* drop the original geometry and group the runs into paragraphs,
* wrap the paragraphs into a single column whose width fits the viewport.

Scanned pages have no text layer and therefore cannot be reflowed.  They are
reported as ``has_text=False`` so the viewer can show an explicit warning
instead of pretending the feature works.

Threading (Golden Rules 2.1 / 2.2)
----------------------------------
:class:`ReflowWorker` is a ``QThread`` that opens its **own** ``fitz.Document``;
the GUI document is never shared with it.  Only plain dataclasses cross the
thread boundary through Qt signals.  ``QFontMetrics`` is reentrant and is used
only to measure text, never to touch a widget.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
# or illustration-heavy documents.
_DICT_FLAGS = fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES

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
class PageLayout:
    """Final, ready-to-paint layout of one page section."""

    page: int
    lines: List[Line] = field(default_factory=list)
    height: float = 0.0
    has_text: bool = False
    error: str = ""


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


def extract_paragraphs(page, page_num: int = 0) -> List[Paragraph]:
    """Extract the text of *page* as a list of paragraphs.

    Each PyMuPDF text block becomes one paragraph.  Consecutive spans with the
    same style are merged so the result stays small even for long documents.
    """
    try:
        data = page.get_text("dict", flags=_DICT_FLAGS) or {}
    except Exception:
        return []

    paragraphs: List[Paragraph] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue          # image block: reflow cannot represent it
        para = Paragraph(page=page_num)
        for line in block.get("lines", []):
            for span in line.get("spans", []):
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


# ------------------------------------------------------------------- layout
def layout_paragraphs(
    paragraphs: List[Paragraph],
    width: int,
    page_num: int = 0,
    measure=measure_text,
    size_scale: float = 1.0,
) -> PageLayout:
    """Wrap *paragraphs* into lines no wider than *width* pixels.

    ``measure(text, family, size, bold, italic) -> float`` is injectable so
    the wrapper can be unit-tested without a font backend.  ``size_scale``
    multiplies every run size before measuring and wrapping.
    """
    width = max(1, int(width))
    size_scale = clamp_font_scale(size_scale)
    lines: List[Line] = []
    y = 0.0

    for para in paragraphs:
        words: List[Tuple[str, Run]] = []
        for run in para.runs:
            for word in run.text.split():
                words.append((word, run))
        if not words:
            continue

        line_runs: List[LineRun] = []
        line_width = 0.0
        max_size = 0

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

        flush()
        # A visual gap separates paragraphs; headings (larger text) get more.
        y += min(20.0, max(5.0, max_size * 0.6))

    has_text = bool(lines)
    return PageLayout(
        page=page_num,
        lines=lines,
        height=float(y) if has_text else float(REFLOW_EMPTY_PAGE_HEIGHT),
        has_text=has_text,
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
                    paragraphs = extract_paragraphs(doc.load_page(i), i)
                    layout = layout_paragraphs(paragraphs, self.width, i,
                                               size_scale=self.font_scale)
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
