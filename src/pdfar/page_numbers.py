"""Page numbering: embedded controls, live preview, and numbered copies.

Numbering is applied to a *copy* of the document.  The open document is only
read (``Document.tobytes``) so unsaved highlights are included and the
original file is never modified.

Coordinates
-----------
``layout_number``/``number_box`` work in the page's own display space
(``page.rect``, i.e. with the PDF's /Rotate already applied).  Stamping
converts that point to unrotated page space with ``page.derotation_matrix``
and passes ``rotate=page.rotation`` to ``insert_text``.  The viewer applies
its own extra rotation on top when drawing the preview.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, replace
from typing import Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QPoint, QRect, QSettings, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QGuiApplication, QImage, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
    QDialogButtonBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import APP_NAME
from .fonts import (
    BASE14_FONTS, BASE14_KEYS,
    all_families, base14_alias, default_style, display_family, font_file,
    missing_note, missing_recommended, qt_family, qt_preview_font,
    recommended_hint, recommended_installable, recommended_tooltip,
    resolve_family_label, styles_for,
)

POSITIONS = (
    ("top-left", "Top left"),
    ("top-center", "Top center"),
    ("top-right", "Top right"),
    ("bottom-left", "Bottom left"),
    ("bottom-center", "Bottom center"),
    ("bottom-right", "Bottom right"),
)

FORMATS = (
    ("number", "1, 2, 3", "{number}"),
    ("number/total", "1/5, 2/5, 3/5", "{number}/{total}"),
    ("page-number", "Page 1", "Page {number}"),
    ("page-number/total", "Page 1 of 5", "Page {number} of {total}"),
    ("dash-number", "- 1 -", "- {number} -"),
)

# Unit key, label, points per unit, and decimals to display.
UNITS = (
    ("pt", "Points (pt)", 1.0, 1),
    ("mm", "Millimeters (mm)", 72.0 / 25.4, 1),
    ("cm", "Centimeters (cm)", 72.0 / 2.54, 2),
    ("in", "Inches (in)", 72.0, 2),
)

_POSITION_KEYS = {key for key, _label in POSITIONS}
_FORMAT_KEYS = {key for key, _label, _template in FORMATS}
_TEMPLATES = {key: template for key, _label, template in FORMATS}
_UNIT_KEYS = {key for key, _label, _factor, _decimals in UNITS}
_UNIT_FACTORS = {key: factor for key, _label, factor, _decimals in UNITS}
_UNIT_DECIMALS = {key: decimals for key, _label, _factor, decimals in UNITS}
_MAX_OFFSET_POINTS = 2000.0
_DEFAULT_MASK_PADDING = 4.0

# Light tones that blend into common page backgrounds instead of pure white.
MASK_COLORS = (
    ("White", "#ffffff"),
    ("Ivory", "#fffff0"),
    ("Paper", "#faf6ec"),
    ("Light gray", "#eeeeee"),
    ("Beige", "#f0e6d2"),
    ("Blue gray", "#e8eef4"),
)


def normalize_color(value, default: str = "#ffffff") -> str:
    """Return ``#rrggbb`` for any colour Qt understands, else *default*."""
    color = QColor(str(value))
    return color.name() if color.isValid() else default



def unit_to_points(value: float, unit: str) -> float:
    """Convert a length in ``unit`` to PDF points."""
    return float(value) * _UNIT_FACTORS.get(unit, 1.0)


def points_to_unit(points: float, unit: str) -> float:
    """Convert PDF points to ``unit``."""
    return float(points) / _UNIT_FACTORS.get(unit, 1.0)


@dataclass
class PageNumberSettings:
    """How page numbers are formatted and placed (lengths in points)."""

    position: str = "bottom-center"
    format: str = "number/total"
    font_family: str = "helv"    # base-14 key or an installed Qt family
    font_style: str = "Regular"  # Regular, Bold, Italic, Light, …
    font_size: int = 12
    margin_x: float = 36.0
    margin_y: float = 36.0
    start_page: int = 1          # 1-based first page to number
    end_page: int = 0            # 0 = last page
    start_number: int = 1        # number printed on start_page
    unit: str = "pt"             # display unit for offsets and mask margins
    mask: bool = True            # cover old content behind the number
    mask_left: float = _DEFAULT_MASK_PADDING
    mask_right: float = _DEFAULT_MASK_PADDING
    mask_top: float = _DEFAULT_MASK_PADDING
    mask_bottom: float = _DEFAULT_MASK_PADDING
    mask_color: str = "#ffffff"  # page-background tone for the mask

    @classmethod
    def load(cls) -> "PageNumberSettings":
        settings = QSettings(APP_NAME, APP_NAME)

        def _text(key: str, default: str, valid: set) -> str:
            value = str(settings.value(key, default))
            return value if value in valid else default

        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(settings.value(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        def _float(key: str, default: float, low: float, high: float) -> float:
            try:
                value = float(settings.value(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        def _font(key: str, default: str, limit: int) -> str:
            value = str(settings.value(key, default) or "").strip()
            return value[:limit] or default

        # Per-side mask margins replaced the single maskPadding value; keep
        # old settings working by using it as the default for every side.
        legacy_padding = _float("pageNumbers/maskPadding",
                                _DEFAULT_MASK_PADDING, 0, _MAX_OFFSET_POINTS)
        return cls(
            position=_text("pageNumbers/position", cls.position,
                           _POSITION_KEYS),
            format=_text("pageNumbers/format", cls.format, _FORMAT_KEYS),
            font_family=_font("pageNumbers/font", cls.font_family, 120),
            font_style=_font("pageNumbers/fontStyle", cls.font_style, 64),
            font_size=_int("pageNumbers/fontSize", cls.font_size, 5, 96),
            margin_x=_float("pageNumbers/marginX", cls.margin_x, 0,
                            _MAX_OFFSET_POINTS),
            margin_y=_float("pageNumbers/marginY", cls.margin_y, 0,
                            _MAX_OFFSET_POINTS),
            start_page=_int("pageNumbers/startPage", cls.start_page, 1,
                            100000),
            end_page=_int("pageNumbers/endPage", cls.end_page, 0, 100000),
            start_number=_int("pageNumbers/startNumber", cls.start_number,
                              0, 100000),
            unit=_text("pageNumbers/unit", cls.unit, _UNIT_KEYS),
            mask=settings.value("pageNumbers/mask", cls.mask, type=bool),
            mask_left=_float("pageNumbers/maskLeft", legacy_padding, 0,
                             _MAX_OFFSET_POINTS),
            mask_right=_float("pageNumbers/maskRight", legacy_padding, 0,
                              _MAX_OFFSET_POINTS),
            mask_top=_float("pageNumbers/maskTop", legacy_padding, 0,
                            _MAX_OFFSET_POINTS),
            mask_bottom=_float("pageNumbers/maskBottom", legacy_padding, 0,
                               _MAX_OFFSET_POINTS),
            mask_color=normalize_color(
                settings.value("pageNumbers/maskColor", cls.mask_color),
                cls.mask_color),
        )

    def save(self) -> None:
        settings = QSettings(APP_NAME, APP_NAME)
        settings.setValue("pageNumbers/position", self.position)
        settings.setValue("pageNumbers/format", self.format)
        settings.setValue("pageNumbers/font", self.font_family)
        settings.setValue("pageNumbers/fontStyle", self.font_style)
        settings.setValue("pageNumbers/fontSize", self.font_size)
        settings.setValue("pageNumbers/marginX", self.margin_x)
        settings.setValue("pageNumbers/marginY", self.margin_y)
        settings.setValue("pageNumbers/startPage", self.start_page)
        settings.setValue("pageNumbers/endPage", self.end_page)
        settings.setValue("pageNumbers/startNumber", self.start_number)
        settings.setValue("pageNumbers/unit", self.unit)
        settings.setValue("pageNumbers/mask", self.mask)
        settings.setValue("pageNumbers/maskLeft", self.mask_left)
        settings.setValue("pageNumbers/maskRight", self.mask_right)
        settings.setValue("pageNumbers/maskTop", self.mask_top)
        settings.setValue("pageNumbers/maskBottom", self.mask_bottom)
        settings.setValue("pageNumbers/maskColor", self.mask_color)


@dataclass
class EraseSettings:
    """Blank mask that covers old page numbers (lengths in points)."""

    position: str = "top-left"
    width: float = 150.0
    height: float = 24.0
    margin_x: float = 24.0
    margin_y: float = 24.0
    start_page: int = 1
    end_page: int = 0
    unit: str = "pt"
    color: str = "#ffffff"

    @classmethod
    def load(cls) -> "EraseSettings":
        settings = QSettings(APP_NAME, APP_NAME)

        def _text(key: str, default: str, valid: set) -> str:
            value = str(settings.value(key, default))
            return value if value in valid else default

        def _int(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(settings.value(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        def _float(key: str, default: float, low: float, high: float) -> float:
            try:
                value = float(settings.value(key, default))
            except (TypeError, ValueError):
                value = default
            return max(low, min(high, value))

        return cls(
            position=_text("pageNumbers/erase/position", cls.position,
                           _POSITION_KEYS),
            width=_float("pageNumbers/erase/width", cls.width, 0,
                         _MAX_OFFSET_POINTS),
            height=_float("pageNumbers/erase/height", cls.height, 0,
                          _MAX_OFFSET_POINTS),
            margin_x=_float("pageNumbers/erase/marginX", cls.margin_x, 0,
                            _MAX_OFFSET_POINTS),
            margin_y=_float("pageNumbers/erase/marginY", cls.margin_y, 0,
                            _MAX_OFFSET_POINTS),
            start_page=_int("pageNumbers/erase/startPage", cls.start_page, 1,
                            100000),
            end_page=_int("pageNumbers/erase/endPage", cls.end_page, 0, 100000),
            unit=_text("pageNumbers/erase/unit", cls.unit, _UNIT_KEYS),
            color=normalize_color(
                settings.value("pageNumbers/erase/color", cls.color),
                cls.color),
        )

    def save(self) -> None:
        settings = QSettings(APP_NAME, APP_NAME)
        settings.setValue("pageNumbers/erase/position", self.position)
        settings.setValue("pageNumbers/erase/width", self.width)
        settings.setValue("pageNumbers/erase/height", self.height)
        settings.setValue("pageNumbers/erase/marginX", self.margin_x)
        settings.setValue("pageNumbers/erase/marginY", self.margin_y)
        settings.setValue("pageNumbers/erase/startPage", self.start_page)
        settings.setValue("pageNumbers/erase/endPage", self.end_page)
        settings.setValue("pageNumbers/erase/unit", self.unit)
        settings.setValue("pageNumbers/erase/color", self.color)


def page_range(settings, page_count: int) -> Tuple[int, int]:
    """Inclusive 0-based range of pages to number or erase ((0, -1) if none)."""
    if page_count <= 0:
        return (0, -1)
    first = max(1, min(int(settings.start_page), page_count))
    last = page_count if not settings.end_page else int(settings.end_page)
    last = max(first, min(last, page_count))
    return (first - 1, last - 1)


def number_text(settings: PageNumberSettings, number: int,
                total: int) -> str:
    template = _TEMPLATES.get(settings.format, _TEMPLATES["number/total"])
    return template.format(number=number, total=total)


def text_alignment(position: str) -> str:
    """Horizontal anchor: 'left', 'center', or 'right'."""
    return position.rsplit("-", 1)[-1]


def pdf_number_font(settings: PageNumberSettings) -> "fitz.Font":
    """Font used for metrics: base-14, an installed file, or Helvetica."""
    alias = base14_alias(settings.font_family, settings.font_style)
    if alias:
        try:
            return fitz.Font(alias)
        except Exception:
            pass
    path = font_file(settings.font_family, settings.font_style)
    if path:
        try:
            return fitz.Font(fontfile=path)
        except Exception:
            pass
    return fitz.Font("helv")


def number_font_source(
        settings: PageNumberSettings) -> Tuple[Optional[str], str]:
    """Return ``(font_file, fontname)`` for stamping into the copy."""
    alias = base14_alias(settings.font_family, settings.font_style)
    if alias:
        return None, alias
    path = font_file(settings.font_family, settings.font_style)
    if path:
        return path, "pdfar-number"
    return None, "helv"


def layout_number(settings: PageNumberSettings, page_w: float, page_h: float,
                  number: int, total: int,
                  font=None) -> Tuple[str, float, float, str]:
    """Return ``(text, anchor_x, baseline_y, alignment)`` in display points.

    ``anchor_x`` is the left, centre, or right edge of the text depending on
    the alignment; ``baseline_y`` is the text baseline.  Top/bottom margins
    refer to the visual top/bottom of the glyphs, not the baseline.
    """
    font = font or pdf_number_font(settings)
    text = number_text(settings, number, total)
    size = float(settings.font_size)
    alignment = text_alignment(settings.position)
    if alignment == "left":
        x = float(settings.margin_x)
    elif alignment == "right":
        x = page_w - float(settings.margin_x)
    else:
        x = page_w / 2.0
    if settings.position.startswith("top"):
        baseline = float(settings.margin_y) + font.ascender * size
    else:
        baseline = page_h - float(settings.margin_y) + font.descender * size
    return text, x, baseline, alignment


@dataclass(frozen=True)
class NumberBox:
    """Text and mask geometry in page display points."""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    baseline: float
    anchor_x: float
    alignment: str
    pad_left: float = 0.0
    pad_right: float = 0.0
    pad_top: float = 0.0
    pad_bottom: float = 0.0

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def pads(self) -> Tuple[float, float, float, float]:
        """Mask margins as ``(left, right, top, bottom)`` points."""
        return self.pad_left, self.pad_right, self.pad_top, self.pad_bottom

    @property
    def mask_rect(self) -> Tuple[float, float, float, float]:
        return (self.left - self.pad_left, self.top - self.pad_top,
                self.right + self.pad_right, self.bottom + self.pad_bottom)


def number_box(settings: PageNumberSettings, page_w: float, page_h: float,
               number: int, total: int, font=None) -> NumberBox:
    """Glyph and mask geometry for one page number."""
    font = font or pdf_number_font(settings)
    text, anchor_x, baseline, alignment = layout_number(
        settings, page_w, page_h, number, total, font=font)
    size = float(settings.font_size)
    width = font.text_length(text, size)
    if alignment == "left":
        left = anchor_x
    elif alignment == "right":
        left = anchor_x - width
    else:
        left = anchor_x - width / 2.0
    top = baseline - font.ascender * size
    bottom = baseline - font.descender * size
    if settings.mask:
        pads = (settings.mask_left, settings.mask_right,
                settings.mask_top, settings.mask_bottom)
    else:
        pads = (0.0, 0.0, 0.0, 0.0)
    return NumberBox(text, left, top, left + width, bottom, baseline,
                     anchor_x, alignment, *pads)


def erase_box(settings: EraseSettings, page_w: float,
              page_h: float) -> NumberBox:
    """Blank mask rectangle in display space; no glyph is drawn."""
    width = min(max(0.0, float(settings.width)), float(page_w))
    height = min(max(0.0, float(settings.height)), float(page_h))
    margin_x = min(max(0.0, float(settings.margin_x)),
                   max(0.0, float(page_w) - width))
    margin_y = min(max(0.0, float(settings.margin_y)),
                   max(0.0, float(page_h) - height))
    alignment = text_alignment(settings.position)
    if alignment == "left":
        left = margin_x
    elif alignment == "right":
        left = float(page_w) - margin_x - width
    else:
        left = (float(page_w) - width) / 2.0
    top = margin_y if settings.position.startswith("top") \
        else float(page_h) - margin_y - height
    right, bottom = left + width, top + height
    return NumberBox("", left, top, right, bottom, top, left + width / 2.0,
                     alignment)


def box_to_settings(settings: PageNumberSettings, page_w: float, page_h: float,
                    box: NumberBox, dx: float, dy: float) -> PageNumberSettings:
    """Move a number by ``(dx, dy)`` display points and update the offsets.

    A centred number is re-anchored to the nearer side when dragged
    horizontally, so the glyphs never jump and the panel keeps showing a
    meaningful edge offset.
    """
    pad_left, pad_right, pad_top, pad_bottom = box.pads
    width, height = box.width, box.height
    min_left = (pad_left if page_w - width >= pad_left + pad_right
                else 0.0)
    max_left = max(min_left, page_w - width - pad_right)
    new_left = min(max(box.left + dx, min_left), max_left)
    min_top = (pad_top if page_h - height >= pad_top + pad_bottom
               else 0.0)
    max_top = max(min_top, page_h - height - pad_bottom)
    new_top = min(max(box.top + dy, min_top), max_top)

    position = settings.position
    alignment = box.alignment
    if alignment == "center" and abs(dx) > 0.5:
        if new_left + width / 2.0 >= page_w / 2.0:
            position = position.replace("center", "right")
            alignment = "right"
        else:
            position = position.replace("center", "left")
            alignment = "left"

    if alignment == "left":
        margin_x = new_left
    elif alignment == "right":
        margin_x = page_w - (new_left + width)
    else:
        margin_x = settings.margin_x
    if position.startswith("top"):
        margin_y = new_top
    else:
        margin_y = page_h - (new_top + height)

    return replace(
        settings, position=position,
        margin_x=max(0.0, min(page_w, margin_x)),
        margin_y=max(0.0, min(page_h, margin_y)),
    )


def _display_rect_to_page(page, rect: Tuple[float, float, float, float]):
    """Map a display-space rect to unrotated page coords (for masking)."""
    x0, y0, x1, y1 = rect
    pts = [fitz.Point(x, y) * page.derotation_matrix
           for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def mask_rgb(color: str) -> Tuple[float, float, float]:
    """Colour for ``draw_rect``, normalized to PyMuPDF's 0..1 range."""
    qcolor = QColor(normalize_color(color))
    return qcolor.redF(), qcolor.greenF(), qcolor.blueF()


def numbered_copy_path(source_path: str) -> str:
    """Default output path: ``name-numbered.pdf`` next to the source."""
    base, ext = os.path.splitext(source_path)
    return f"{base}-numbered{ext or '.pdf'}"


def erased_copy_path(source_path: str) -> str:
    """Default output path: ``name-erased.pdf`` next to the source."""
    base, ext = os.path.splitext(source_path)
    return f"{base}-erased{ext or '.pdf'}"


def _save_modified_copy(source, dest_path: str, modify) -> int:
    """Apply ``modify(page, index)`` to a serialized copy and write it.

    The in-memory document is serialized to bytes first, so unsaved
    annotations are included.  The copy is decrypted, written atomically, and
    the source document is left untouched.
    """
    data = source.tobytes(garbage=0, deflate=True,
                          encryption=fitz.PDF_ENCRYPT_NONE)
    out = fitz.open(stream=data, filetype="pdf")
    tmp_path: Optional[str] = None
    try:
        count = modify(out)
        directory = os.path.dirname(os.path.abspath(dest_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".pdfar-numbers-",
                                        suffix=".pdf", dir=directory)
        os.close(fd)
        out.save(tmp_path, garbage=3, deflate=True)
        out.close()
        os.replace(tmp_path, dest_path)
        tmp_path = None
        return count
    finally:
        try:
            out.close()
        except Exception:
            pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def stamp_copy(source, dest_path: str,
               settings: PageNumberSettings) -> int:
    """Write a numbered copy of ``source`` (a fitz.Document) and return pages.

    The in-memory document is serialized to bytes first, so unsaved
    annotations are included.  The copy is decrypted, written atomically, and
    the source document is left untouched.
    """
    def modify(out) -> int:
        first, last = page_range(settings, out.page_count)
        if last < first:
            raise ValueError("document has no pages to number")
        font = pdf_number_font(settings)
        font_file_path, font_name = number_font_source(settings)
        total = last - first + 1
        mask_fill = mask_rgb(settings.mask_color)
        for index in range(first, last + 1):
            page = out.load_page(index)
            if font_file_path:
                page.insert_font(fontname=font_name, fontfile=font_file_path)
            number = settings.start_number + (index - first)
            box = number_box(settings, page.rect.width, page.rect.height,
                             number, total, font=font)
            if settings.mask:
                page.draw_rect(
                    _display_rect_to_page(page, box.mask_rect),
                    color=None, fill=mask_fill, overlay=True)
            point = fitz.Point(box.left, box.baseline) * page.derotation_matrix
            page.insert_text(
                point, box.text, fontname=font_name,
                fontsize=settings.font_size, color=(0.0, 0.0, 0.0),
                rotate=page.rotation, overlay=True)
        return total

    return _save_modified_copy(source, dest_path, modify)


def erase_copy(source, dest_path: str, settings: EraseSettings) -> int:
    """Write a copy of ``source`` with blank masks on the selected pages.

    This is a cover rectangle, not true redaction: the underlying text or
    image is hidden but remains in the file.  Returns the number of pages
    that were covered.
    """
    if float(settings.width) <= 0 or float(settings.height) <= 0:
        raise ValueError("the erase mask needs a non-zero width and height")

    def modify(out) -> int:
        first, last = page_range(settings, out.page_count)
        if last < first:
            raise ValueError("document has no pages to erase")
        fill = mask_rgb(settings.color)
        for index in range(first, last + 1):
            page = out.load_page(index)
            box = erase_box(settings, page.rect.width, page.rect.height)
            page.draw_rect(
                _display_rect_to_page(page, box.mask_rect),
                color=None, fill=fill, overlay=True)
        return last - first + 1

    return _save_modified_copy(source, dest_path, modify)


_PICKER_HINT = "Click a colour to sample it  ·  Esc to cancel"


def sample_screen_color(image: QImage, pos: QPoint,
                        size: QSize) -> QColor:
    """Map a point in a scaled overlay back to a pixel of *image*."""
    if image.isNull() or size.width() <= 0 or size.height() <= 0:
        return QColor()
    if not (0 <= pos.x() < size.width() and 0 <= pos.y() < size.height()):
        return QColor()
    x = int(pos.x() * image.width() / size.width())
    y = int(pos.y() * image.height() / size.height())
    if not (0 <= x < image.width() and 0 <= y < image.height()):
        return QColor()
    return image.pixelColor(x, y)


class ScreenColorPicker(QWidget):
    """Full-screen snapshot that samples the colour under the pointer.

    The screen is captured before the overlay is shown, so the user can
    click the PDF page (or anything else visible) and get that exact tone.
    """

    color_picked = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(
            parent,
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        screen = (QGuiApplication.screenAt(QCursor.pos())
                  or QGuiApplication.primaryScreen())
        self._image = (screen.grabWindow(0).toImage()
                       if screen is not None else QImage())
        if screen is not None:
            self.setScreen(screen)
            self.setGeometry(screen.geometry())

    def start(self) -> None:
        self.showFullScreen()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        if not self._image.isNull():
            painter.drawImage(self.rect(), self._image)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(_PICKER_HINT) + 24
        hint = QRect((self.width() - width) // 2, 12, width,
                     metrics.height() + 12)
        painter.fillRect(hint, QColor(20, 20, 20, 200))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(hint, Qt.AlignmentFlag.AlignCenter, _PICKER_HINT)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        color = sample_screen_color(
            self._image, event.position().toPoint(), self.size())
        if color.isValid():
            self.color_picked.emit(color.name())
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)


class FontDialog(QDialog):
    """Browse installed fonts, recommended families, styles and a sample."""

    def __init__(self, family: str, style: str, size: int,
                 parent: Optional[QWidget] = None, sample: str = "1/5"):
        super().__init__(parent)
        self.setWindowTitle("Choose font")
        self.setMinimumSize(680, 480)
        self.resize(800, 560)
        self._family = family
        self._style = style
        self._sample = sample or "1/5"

        layout = QVBoxLayout(self)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search fonts…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._filter)
        layout.addWidget(self.search_edit)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.tree = QTreeWidget(splitter)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self._populate_tree()
        splitter.addWidget(self.tree)

        side = QWidget(splitter)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        self.family_label = QLabel(side)
        bold = self.family_label.font()
        bold.setBold(True)
        self.family_label.setFont(bold)
        side_layout.addWidget(self.family_label)
        side_layout.addWidget(QLabel("Styles:", side))
        self.style_list = QListWidget(side)
        self.style_list.setMaximumWidth(260)
        side_layout.addWidget(self.style_list, 1)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:", side))
        self.size_spin = QSpinBox(side)
        self.size_spin.setRange(5, 96)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setValue(max(5, min(96, int(size))))
        size_row.addWidget(self.size_spin)
        size_row.addStretch(1)
        side_layout.addLayout(size_row)

        self.sample_label = QLabel(side)
        self.sample_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sample_label.setWordWrap(True)
        self.sample_label.setMinimumHeight(150)
        self.sample_label.setFrameShape(QFrame.Shape.StyledPanel)
        self.sample_label.setMargin(16)
        side_layout.addWidget(self.sample_label, 1)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._update_note()
        layout.addWidget(self.note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.tree.currentItemChanged.connect(self._on_family_changed)
        self.style_list.currentItemChanged.connect(self._on_style_changed)
        self.size_spin.valueChanged.connect(self._update_preview)
        self._select_family(family)

    @property
    def selected_family(self) -> str:
        return self._family

    @property
    def selected_style(self) -> str:
        return self._style

    @property
    def selected_size(self) -> int:
        return self.size_spin.value()

    def _populate_tree(self) -> None:
        recommended = QTreeWidgetItem(["Recommended"])
        everything = QTreeWidgetItem(["All installed fonts"])
        self.tree.addTopLevelItem(recommended)
        self.tree.addTopLevelItem(everything)
        recommended.setExpanded(True)

        for key, label, _preview in BASE14_FONTS:
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, key)
            item.setToolTip(0, "Built into every PDF viewer; nothing to install.")
            recommended.addChild(item)

        recommended_names = set()
        for rec, installed in recommended_installable():
            if installed:
                qt_name = qt_family(rec.family)
                label = display_family(qt_name)
                item = QTreeWidgetItem([label])
                item.setData(0, Qt.ItemDataRole.UserRole, qt_name)
                item.setToolTip(0, f"Recommended · {recommended_tooltip(rec)}")
                recommended_names.add(label.casefold())
            else:
                item = QTreeWidgetItem([f"{rec.family} — not installed"])
                item.setToolTip(0, recommended_hint(rec))
                item.setFlags(item.flags()
                              & ~Qt.ItemFlag.ItemIsEnabled)
            recommended.addChild(item)

        for name in all_families():
            label = display_family(name)
            if label.casefold() in recommended_names:
                continue
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.ItemDataRole.UserRole, name)
            everything.addChild(item)

    def _select_family(self, family: str) -> None:
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            for child in range(root.childCount()):
                item = root.child(child)
                if item.data(0, Qt.ItemDataRole.UserRole) == family:
                    self.tree.setCurrentItem(item)
                    self.tree.scrollToItem(
                        item, QAbstractItemView.ScrollHint.PositionAtCenter)
                    return
        self._fill_styles(family)
        self._update_preview()

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            visible = 0
            for child in range(root.childCount()):
                item = root.child(child)
                match = not needle or needle in item.text(0).casefold()
                item.setHidden(not match)
                visible += match
            root.setHidden(visible == 0)
            if needle and visible:
                root.setExpanded(True)

    def _on_family_changed(self, current, _previous) -> None:
        if current is None:
            return
        family = current.data(0, Qt.ItemDataRole.UserRole)
        if not family:
            return
        self._family = family
        self._fill_styles(family)
        self._update_preview()

    def _fill_styles(self, family: str) -> None:
        styles = styles_for(family)
        chosen = default_style(family, self._style)
        self.style_list.blockSignals(True)
        try:
            self.style_list.clear()
            selected = 0
            for index, style in enumerate(styles):
                item = QListWidgetItem(style)
                item.setData(Qt.ItemDataRole.UserRole, style)
                self.style_list.addItem(item)
                if style == chosen:
                    selected = index
            self.style_list.setCurrentRow(selected)
        finally:
            self.style_list.blockSignals(False)
        self._style = chosen

    def _on_style_changed(self, current, _previous) -> None:
        if current is None:
            return
        style = current.data(Qt.ItemDataRole.UserRole)
        self._style = style or current.text()
        self._update_preview()

    def _update_preview(self) -> None:
        family = self._family
        style = self._style or "Regular"
        pixels = max(16, min(72, int(self.size_spin.value() * 2.2)))
        self.family_label.setText(
            f"{resolve_family_label(family)} · {style}")
        self.sample_label.setFont(
            qt_preview_font(family, style, pixels))
        self.sample_label.setText(
            f"{self._sample}\nABC abc 0123456789\nHamburgefontsiv")

    def _update_note(self) -> None:
        text = missing_note(missing_recommended())
        if not text:
            self.note.hide()
            return
        self.note.setText(text)


class PageNumbersPanel(QWidget):
    """Page-number controls embedded in the right dock.

    Emits ``settings_changed`` for the live preview and ``save_requested``
    when the user asks for a numbered copy.
    """

    settings_changed = pyqtSignal(object)
    save_requested = pyqtSignal()

    def __init__(self, page_count: int = 0,
                 initial: Optional[PageNumberSettings] = None,
                 erase_initial: Optional[EraseSettings] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._page_count = max(0, int(page_count))
        self._updating = False
        initial = initial or PageNumberSettings()
        erase_initial = erase_initial or EraseSettings()
        self._unit = initial.unit
        self._mask_color = normalize_color(initial.mask_color)
        self._erase_color = normalize_color(erase_initial.color)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._build_units_row(initial))

        self.position_page = self._build_position_page(initial)
        self.mask_page = self._build_mask_page(initial)
        self.erase_page = self._build_erase_page(erase_initial)
        self.sub_tabs = QTabWidget(self)
        self.sub_tabs.setDocumentMode(True)
        self.sub_tabs.addTab(self.position_page, "Position")
        self.sub_tabs.addTab(self.mask_page, "Mask")
        self.sub_tabs.addTab(self.erase_page, "Erase")
        self.sub_tabs.setCurrentIndex(0)
        layout.addWidget(self.sub_tabs)
        layout.addWidget(self._build_numbering_group(initial))
        # Canonical lengths in points, kept exact across unit switches where
        # the displayed (rounded) value would otherwise drift.
        self._offsets_points = (
            float(initial.margin_x), float(initial.margin_y),
            float(initial.mask_left), float(initial.mask_right),
            float(initial.mask_top), float(initial.mask_bottom))
        self._erase_offsets_points = (
            float(erase_initial.width), float(erase_initial.height),
            float(erase_initial.margin_x), float(erase_initial.margin_y))
        # end_page == 0 means "last page": keep that semantic until the user
        # picks an explicit end, even though the panel is built before a
        # document (and its page count) is known.
        self._start_page_value = max(1, int(initial.start_page))
        self._end_is_last = initial.end_page == 0

        self.range_label = QLabel(self)
        self.range_label.setWordWrap(True)
        layout.addWidget(self.range_label)

        self.save_btn = QPushButton("Save Copy…", self)
        self.save_btn.setEnabled(self._page_count > 0)
        self.save_btn.clicked.connect(self.save_requested.emit)
        layout.addWidget(self.save_btn)
        layout.addStretch(1)

        for widget in (self.position_combo, self.format_combo,
                       self.font_style_combo, self.erase_position_combo):
            widget.currentIndexChanged.connect(self._on_changed)
        self.font_combo.currentIndexChanged.connect(self._on_font_changed)
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        for widget in (self.size_spin, self.first_page_spin,
                       self.last_page_spin, self.first_number_spin):
            widget.valueChanged.connect(self._on_changed)
        for widget in (self.margin_x_spin, self.margin_y_spin,
                       self.mask_left_spin, self.mask_right_spin,
                       self.mask_top_spin, self.mask_bottom_spin,
                       self.erase_width_spin, self.erase_height_spin,
                       self.erase_margin_x_spin, self.erase_margin_y_spin):
            widget.valueChanged.connect(self._on_offset_changed)
        self.mask_check.toggled.connect(self._on_mask_toggled)
        self.sub_tabs.currentChanged.connect(self._on_mode_changed)

        self._sync_range()
        self._on_mode_changed(0)

    def _build_units_row(self, initial: PageNumberSettings):
        row = QWidget(self)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("Units:", row))
        self.unit_combo = QComboBox(row)
        for key, label, _factor, _decimals in UNITS:
            self.unit_combo.addItem(label, key)
        self.unit_combo.setCurrentIndex(
            max(0, self.unit_combo.findData(initial.unit)))
        self._unit = self.unit_combo.currentData()
        row_layout.addWidget(self.unit_combo, 1)
        return row

    # ------------------------------------------------------------- widgets
    def _build_position_page(self, initial: PageNumberSettings):
        page = QWidget(self)
        form = QFormLayout(page)
        form.setContentsMargins(8, 8, 8, 8)

        self.position_combo = QComboBox(page)
        for key, label in POSITIONS:
            self.position_combo.addItem(label, key)
        self.position_combo.setCurrentIndex(
            max(0, self.position_combo.findData(initial.position)))
        form.addRow("Placement:", self.position_combo)

        self.margin_x_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.margin_x_spin)
        self.margin_x_spin.setValue(
            points_to_unit(initial.margin_x, self._unit))
        form.addRow("Horizontal offset:", self.margin_x_spin)

        self.margin_y_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.margin_y_spin)
        self.margin_y_spin.setValue(
            points_to_unit(initial.margin_y, self._unit))
        form.addRow("Vertical offset:", self.margin_y_spin)

        font_row = QWidget(page)
        font_layout = QHBoxLayout(font_row)
        font_layout.setContentsMargins(0, 0, 0, 0)
        self.font_combo = QComboBox(font_row)
        self._populate_font_combo(initial.font_family)
        self.font_button = QPushButton("Choose…", font_row)
        self.font_button.setToolTip(
            "Browse every installed font, its styles and a live sample")
        self.font_button.clicked.connect(self._choose_font)
        font_layout.addWidget(self.font_combo, 1)
        font_layout.addWidget(self.font_button)
        form.addRow("Font:", font_row)

        self.font_style_combo = QComboBox(page)
        self._populate_style_combo(initial.font_family, initial.font_style)
        form.addRow("Style:", self.font_style_combo)

        self.size_spin = QSpinBox(page)
        self.size_spin.setRange(5, 96)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setValue(initial.font_size)
        form.addRow("Font size:", self.size_spin)
        return page

    def _build_mask_page(self, initial: PageNumberSettings):
        page = QWidget(self)
        form = QFormLayout(page)
        form.setContentsMargins(8, 8, 8, 8)

        self.mask_check = QCheckBox(
            "Cover existing numbers behind the new number", page)
        self.mask_check.setChecked(initial.mask)
        form.addRow(self.mask_check)

        color_row = QWidget(page)
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        self.mask_color_btn = QPushButton(color_row)
        self.mask_color_btn.setToolTip("Choose the mask colour…")
        self.mask_color_btn.clicked.connect(self._choose_mask_color)
        self.pick_color_btn = QPushButton("Pick…", color_row)
        self.pick_color_btn.setToolTip(
            "Sample a colour anywhere on the screen")
        self.pick_color_btn.clicked.connect(self._pick_screen_color)
        color_layout.addWidget(self.mask_color_btn, 1)
        color_layout.addWidget(self.pick_color_btn)
        form.addRow("Colour:", color_row)

        swatches = QWidget(page)
        swatch_layout = QHBoxLayout(swatches)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(4)
        self.swatch_buttons = []
        for label, color in MASK_COLORS:
            button = QPushButton(swatches)
            button.setFixedSize(24, 18)
            button.setToolTip(f"{label} ({color})")
            button.clicked.connect(
                lambda _checked=False, value=color:
                self._set_mask_color(value))
            swatch_layout.addWidget(button)
            self.swatch_buttons.append(button)
        swatch_layout.addStretch(1)
        form.addRow("Swatches:", swatches)
        self._update_mask_color_ui()

        margins = QWidget(page)
        grid = QGridLayout(margins)
        grid.setContentsMargins(0, 0, 0, 0)
        self.mask_left_spin = QDoubleSpinBox(margins)
        self.mask_right_spin = QDoubleSpinBox(margins)
        self.mask_top_spin = QDoubleSpinBox(margins)
        self.mask_bottom_spin = QDoubleSpinBox(margins)
        margins_spins = (
            (self.mask_left_spin, initial.mask_left, "Left:"),
            (self.mask_right_spin, initial.mask_right, "Right:"),
            (self.mask_top_spin, initial.mask_top, "Top:"),
            (self.mask_bottom_spin, initial.mask_bottom, "Bottom:"),
        )
        for row, (spin, value, label) in enumerate(margins_spins):
            self._configure_offset_spin(spin)
            spin.setValue(points_to_unit(value, self._unit))
            grid.addWidget(QLabel(label, margins), row // 2, (row % 2) * 2)
            grid.addWidget(spin, row // 2, (row % 2) * 2 + 1)
        form.addRow("Margins:", margins)

        self._set_mask_controls_enabled(initial.mask)
        return page

    def _build_erase_page(self, initial: EraseSettings):
        page = QWidget(self)
        form = QFormLayout(page)
        form.setContentsMargins(8, 8, 8, 8)

        hint = QLabel(
            "Cover old page numbers with a blank rectangle. It hides them "
            "visually; the text is not removed from the file.", page)
        hint.setWordWrap(True)
        form.addRow(hint)

        self.erase_position_combo = QComboBox(page)
        for key, label in POSITIONS:
            self.erase_position_combo.addItem(label, key)
        self.erase_position_combo.setCurrentIndex(
            max(0, self.erase_position_combo.findData(initial.position)))
        form.addRow("Placement:", self.erase_position_combo)

        self.erase_width_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.erase_width_spin)
        self.erase_width_spin.setValue(
            points_to_unit(initial.width, self._unit))
        form.addRow("Width:", self.erase_width_spin)

        self.erase_height_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.erase_height_spin)
        self.erase_height_spin.setValue(
            points_to_unit(initial.height, self._unit))
        form.addRow("Height:", self.erase_height_spin)

        self.erase_margin_x_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.erase_margin_x_spin)
        self.erase_margin_x_spin.setValue(
            points_to_unit(initial.margin_x, self._unit))
        form.addRow("Horizontal offset:", self.erase_margin_x_spin)

        self.erase_margin_y_spin = QDoubleSpinBox(page)
        self._configure_offset_spin(self.erase_margin_y_spin)
        self.erase_margin_y_spin.setValue(
            points_to_unit(initial.margin_y, self._unit))
        form.addRow("Vertical offset:", self.erase_margin_y_spin)

        color_row = QWidget(page)
        color_layout = QHBoxLayout(color_row)
        color_layout.setContentsMargins(0, 0, 0, 0)
        self.erase_color_btn = QPushButton(color_row)
        self.erase_color_btn.setToolTip("Choose the erase colour…")
        self.erase_color_btn.clicked.connect(self._choose_erase_color)
        self.erase_pick_color_btn = QPushButton("Pick…", color_row)
        self.erase_pick_color_btn.setToolTip(
            "Sample a colour anywhere on the screen")
        self.erase_pick_color_btn.clicked.connect(self._pick_erase_color)
        color_layout.addWidget(self.erase_color_btn, 1)
        color_layout.addWidget(self.erase_pick_color_btn)
        form.addRow("Colour:", color_row)

        swatches = QWidget(page)
        swatch_layout = QHBoxLayout(swatches)
        swatch_layout.setContentsMargins(0, 0, 0, 0)
        swatch_layout.setSpacing(4)
        self.erase_swatch_buttons = []
        for label, color in MASK_COLORS:
            button = QPushButton(swatches)
            button.setFixedSize(24, 18)
            button.setToolTip(f"{label} ({color})")
            button.clicked.connect(
                lambda _checked=False, value=color:
                self._set_erase_color(value))
            swatch_layout.addWidget(button)
            self.erase_swatch_buttons.append(button)
        swatch_layout.addStretch(1)
        form.addRow("Swatches:", swatches)
        self._update_erase_color_ui()
        return page

    def _build_numbering_group(self, initial: PageNumberSettings):
        group = QGroupBox("Numbering", self)
        form = QFormLayout(group)
        self.numbering_group = group
        self._numbering_form = form

        self.format_combo = QComboBox(group)
        for key, label, _template in FORMATS:
            self.format_combo.addItem(label, key)
        self.format_combo.setCurrentIndex(
            max(0, self.format_combo.findData(initial.format)))
        form.addRow("Format:", self.format_combo)

        self.first_page_spin = QSpinBox(group)
        self.first_page_spin.setRange(1, max(1, self._page_count))
        self.first_page_spin.setValue(
            max(1, min(initial.start_page, max(1, self._page_count))))
        form.addRow("Start page number:", self.first_page_spin)

        self.last_page_spin = QSpinBox(group)
        self.last_page_spin.setRange(1, max(1, self._page_count))
        last = initial.end_page or max(1, self._page_count)
        self.last_page_spin.setValue(
            max(1, min(last, max(1, self._page_count))))
        form.addRow("End page number:", self.last_page_spin)

        self.first_number_spin = QSpinBox(group)
        self.first_number_spin.setRange(0, 100000)
        self.first_number_spin.setValue(
            max(0, min(initial.start_number, 100000)))
        form.addRow("First printed number:", self.first_number_spin)
        self._first_page_label = form.labelForField(self.first_page_spin)
        self._last_page_label = form.labelForField(self.last_page_spin)
        return group

    def _populate_font_combo(self, family: str) -> None:
        """Built-ins first, then installed recommended families, then current."""
        self.font_combo.blockSignals(True)
        try:
            self.font_combo.clear()
            for key, label, _preview in BASE14_FONTS:
                self.font_combo.addItem(label, key)
            added = set(BASE14_KEYS)
            for rec, installed in recommended_installable():
                if not installed:
                    continue
                qt_name = qt_family(rec.family)
                if qt_name in added:
                    continue
                self.font_combo.addItem(display_family(qt_name), qt_name)
                added.add(qt_name)
            if family and family not in added:
                self.font_combo.addItem(display_family(family), family)
            index = self.font_combo.findData(family)
            if index < 0:
                index = self.font_combo.findData(qt_family(family))
            self.font_combo.setCurrentIndex(max(0, index))
        finally:
            self.font_combo.blockSignals(False)

    def _populate_style_combo(self, family: str,
                              preferred: str = "") -> None:
        self.font_style_combo.blockSignals(True)
        try:
            self.font_style_combo.clear()
            for style in styles_for(family):
                self.font_style_combo.addItem(style, style)
            chosen = default_style(family, preferred)
            index = self.font_style_combo.findData(chosen)
            self.font_style_combo.setCurrentIndex(max(0, index))
        finally:
            self.font_style_combo.blockSignals(False)

    def _on_font_changed(self) -> None:
        family = self.font_combo.currentData()
        if not family:
            return
        self._populate_style_combo(
            family, self.font_style_combo.currentData() or "")
        if not self._updating:
            self.settings_changed.emit(self.preview_settings())

    def _font_sample(self) -> str:
        settings = self.settings()
        return number_text(settings, settings.start_number, 5)

    def _choose_font(self) -> None:
        dialog = FontDialog(
            self.font_combo.currentData() or "helv",
            self.font_style_combo.currentData() or "Regular",
            self.size_spin.value(), self, sample=self._font_sample())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_font_choice(dialog.selected_family,
                                    dialog.selected_style,
                                    dialog.selected_size)

    def _apply_font_choice(self, family: str, style: str,
                           size: int) -> None:
        self._updating = True
        try:
            if self.font_combo.findData(family) < 0:
                self.font_combo.addItem(display_family(family), family)
            self.font_combo.setCurrentIndex(
                max(0, self.font_combo.findData(family)))
            self._populate_style_combo(family, style)
            self.size_spin.setValue(int(size))
        finally:
            self._updating = False
        self._update_range_label()
        self.settings_changed.emit(self.preview_settings())

    def _configure_offset_spin(self, spin: QDoubleSpinBox) -> None:
        unit = self.unit_combo.currentData() if hasattr(
            self, "unit_combo") else self._unit
        factor = _UNIT_FACTORS.get(unit, 1.0)
        spin.setDecimals(_UNIT_DECIMALS.get(unit, 1))
        spin.setRange(0.0, _MAX_OFFSET_POINTS / factor)
        spin.setSingleStep(1.0 / factor)
        spin.setSuffix(f" {unit}")

    # ------------------------------------------------------------ settings
    def erase_active(self) -> bool:
        """True when the Erase sub-tab is selected."""
        return self.sub_tabs.currentIndex() == 2

    def preview_settings(self):
        """Settings for the active sub-tab, used by the live preview."""
        return self.erase_settings() if self.erase_active() else self.settings()

    def settings(self) -> PageNumberSettings:
        (margin_x, margin_y, mask_left, mask_right,
         mask_top, mask_bottom) = self._offsets_points
        return PageNumberSettings(
            position=self.position_combo.currentData(),
            format=self.format_combo.currentData(),
            font_family=self.font_combo.currentData() or "helv",
            font_style=self.font_style_combo.currentData() or "Regular",
            font_size=self.size_spin.value(),
            margin_x=margin_x,
            margin_y=margin_y,
            start_page=self.first_page_spin.value(),
            end_page=0 if self._end_is_last else self.last_page_spin.value(),
            start_number=self.first_number_spin.value(),
            unit=self.unit_combo.currentData(),
            mask=self.mask_check.isChecked(),
            mask_left=mask_left,
            mask_right=mask_right,
            mask_top=mask_top,
            mask_bottom=mask_bottom,
            mask_color=self._mask_color,
        )

    def erase_settings(self) -> EraseSettings:
        width, height, margin_x, margin_y = self._erase_offsets_points
        return EraseSettings(
            position=self.erase_position_combo.currentData(),
            width=width,
            height=height,
            margin_x=margin_x,
            margin_y=margin_y,
            start_page=self.first_page_spin.value(),
            end_page=0 if self._end_is_last else self.last_page_spin.value(),
            unit=self.unit_combo.currentData(),
            color=self._erase_color,
        )

    def set_page_count(self, page_count: int) -> None:
        """Match the active document; clamp the range and enable saving."""
        page_count = max(0, int(page_count))
        if page_count == self._page_count:
            self.save_btn.setEnabled(page_count > 0)
            return
        before = self.preview_settings()
        self._page_count = page_count
        self._updating = True
        self.save_btn.setEnabled(page_count > 0)
        upper = max(1, page_count)
        try:
            for spin in (self.first_page_spin, self.last_page_spin):
                spin.setRange(1, upper)
            self.first_page_spin.setValue(
                min(self._start_page_value, upper))
            self.last_page_spin.setValue(
                upper if self._end_is_last
                else min(self.last_page_spin.value(), upper))
            self._sync_range()
        finally:
            self._updating = False
        self._update_range_label()
        if self.preview_settings() != before:
            self.settings_changed.emit(self.preview_settings())

    def _apply_unit(self, unit: str) -> None:
        """Switch every length spin to *unit*, keeping their point values."""
        self._unit = unit
        self.unit_combo.setCurrentIndex(
            max(0, self.unit_combo.findData(unit)))
        number_spins = (self.margin_x_spin, self.margin_y_spin,
                        self.mask_left_spin, self.mask_right_spin,
                        self.mask_top_spin, self.mask_bottom_spin)
        erase_spins = (self.erase_width_spin, self.erase_height_spin,
                       self.erase_margin_x_spin, self.erase_margin_y_spin)
        for spin in number_spins + erase_spins:
            self._configure_offset_spin(spin)
        for spin, value in zip(number_spins, self._offsets_points):
            spin.setValue(points_to_unit(value, unit))
        for spin, value in zip(erase_spins, self._erase_offsets_points):
            spin.setValue(points_to_unit(value, unit))

    def apply_settings(self, settings: PageNumberSettings) -> None:
        """Update the controls without emitting (used after dragging)."""
        self._updating = True
        try:
            self._offsets_points = (
                float(settings.margin_x), float(settings.margin_y),
                float(settings.mask_left), float(settings.mask_right),
                float(settings.mask_top), float(settings.mask_bottom))
            self._apply_unit(settings.unit)
            self.position_combo.setCurrentIndex(
                max(0, self.position_combo.findData(settings.position)))
            self.format_combo.setCurrentIndex(
                max(0, self.format_combo.findData(settings.format)))
            index = self.font_combo.findData(settings.font_family)
            if index < 0:
                self.font_combo.addItem(
                    display_family(settings.font_family),
                    settings.font_family)
                index = self.font_combo.count() - 1
            self.font_combo.setCurrentIndex(index)
            self._populate_style_combo(
                settings.font_family, settings.font_style)
            self.size_spin.setValue(settings.font_size)
            self._start_page_value = max(1, int(settings.start_page))
            self._end_is_last = settings.end_page == 0
            self.first_page_spin.setValue(settings.start_page)
            self.last_page_spin.setValue(
                settings.end_page or max(1, self._page_count))
            self.first_number_spin.setValue(settings.start_number)
            self._mask_color = normalize_color(settings.mask_color)
            self._update_mask_color_ui()
            self.mask_check.setChecked(settings.mask)
            self._set_mask_controls_enabled(settings.mask)
            self._sync_range()
        finally:
            self._updating = False
        self._update_range_label()

    def apply_erase_settings(self, settings: EraseSettings) -> None:
        """Update the erase controls without emitting (used after dragging)."""
        self._updating = True
        try:
            self._erase_offsets_points = (
                float(settings.width), float(settings.height),
                float(settings.margin_x), float(settings.margin_y))
            self._apply_unit(settings.unit)
            self.erase_position_combo.setCurrentIndex(
                max(0, self.erase_position_combo.findData(settings.position)))
            self._start_page_value = max(1, int(settings.start_page))
            self._end_is_last = settings.end_page == 0
            self.first_page_spin.setValue(settings.start_page)
            self.last_page_spin.setValue(
                settings.end_page or max(1, self._page_count))
            self._erase_color = normalize_color(settings.color)
            self._update_erase_color_ui()
            self._sync_range()
        finally:
            self._updating = False
        self._update_range_label()

    # -------------------------------------------------------------- events
    def _on_offset_changed(self) -> None:
        if self._updating:
            return
        unit = self.unit_combo.currentData()
        self._offsets_points = (
            unit_to_points(self.margin_x_spin.value(), unit),
            unit_to_points(self.margin_y_spin.value(), unit),
            unit_to_points(self.mask_left_spin.value(), unit),
            unit_to_points(self.mask_right_spin.value(), unit),
            unit_to_points(self.mask_top_spin.value(), unit),
            unit_to_points(self.mask_bottom_spin.value(), unit),
        )
        self._erase_offsets_points = (
            unit_to_points(self.erase_width_spin.value(), unit),
            unit_to_points(self.erase_height_spin.value(), unit),
            unit_to_points(self.erase_margin_x_spin.value(), unit),
            unit_to_points(self.erase_margin_y_spin.value(), unit),
        )
        self._on_changed()

    def _on_unit_changed(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self._apply_unit(self.unit_combo.currentData())
        finally:
            self._updating = False
        self._update_range_label()
        self.settings_changed.emit(self.preview_settings())

    def _on_mode_changed(self, _index: int) -> None:
        erase = self.erase_active()
        self.numbering_group.setTitle("Erase range" if erase else "Numbering")
        self._numbering_form.setRowVisible(0, not erase)
        self._numbering_form.setRowVisible(3, not erase)
        if self._first_page_label is not None:
            self._first_page_label.setText(
                "First page:" if erase else "Start page number:")
        if self._last_page_label is not None:
            self._last_page_label.setText(
                "Last page:" if erase else "End page number:")
        self.save_btn.setText(
            "Save Erased Copy…" if erase else "Save Copy…")
        self._update_range_label()
        if not self._updating:
            self.settings_changed.emit(self.preview_settings())

    def _on_mask_toggled(self, checked: bool) -> None:
        self._set_mask_controls_enabled(checked)
        if not self._updating:
            self.settings_changed.emit(self.preview_settings())

    def _set_mask_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.mask_left_spin, self.mask_right_spin,
                       self.mask_top_spin, self.mask_bottom_spin,
                       self.mask_color_btn, self.pick_color_btn,
                       *self.swatch_buttons):
            widget.setEnabled(enabled)

    @staticmethod
    def _paint_color_button(button: QPushButton, color: str) -> None:
        qcolor = QColor(color)
        text = "#000000" if qcolor.lightness() > 140 else "#ffffff"
        button.setText(color)
        button.setStyleSheet(
            "QPushButton { background-color: %s; color: %s;"
            " border: 1px solid #7a7a7a; border-radius: 3px;"
            " padding: 2px 6px; }" % (color, text))

    @staticmethod
    def _paint_swatches(buttons, color: str) -> None:
        for (_label, value), button in zip(MASK_COLORS, buttons):
            border = ("2px solid #0078d7" if value == color
                      else "1px solid #9a9a9a")
            button.setStyleSheet(
                "QPushButton { background-color: %s; border: %s;"
                " border-radius: 3px; }"
                "QPushButton:hover { border-color: #0078d7; }"
                "QPushButton:disabled { border-color: #b4b4b4; }"
                % (value, border))

    def _update_mask_color_ui(self) -> None:
        self._paint_color_button(self.mask_color_btn, self._mask_color)
        self._paint_swatches(self.swatch_buttons, self._mask_color)

    def _update_erase_color_ui(self) -> None:
        self._paint_color_button(self.erase_color_btn, self._erase_color)
        self._paint_swatches(self.erase_swatch_buttons, self._erase_color)

    def _set_mask_color(self, color: str) -> None:
        color = normalize_color(color, self._mask_color)
        if color == self._mask_color:
            return
        self._mask_color = color
        self._update_mask_color_ui()
        if not self._updating:
            self.settings_changed.emit(self.preview_settings())

    def _set_erase_color(self, color: str) -> None:
        color = normalize_color(color, self._erase_color)
        if color == self._erase_color:
            return
        self._erase_color = color
        self._update_erase_color_ui()
        if not self._updating:
            self.settings_changed.emit(self.preview_settings())

    def _choose_mask_color(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._mask_color), self, "Mask colour")
        if color.isValid():
            self._set_mask_color(color.name())

    def _choose_erase_color(self) -> None:
        color = QColorDialog.getColor(
            QColor(self._erase_color), self, "Erase colour")
        if color.isValid():
            self._set_erase_color(color.name())

    def _pick_screen_color(self) -> None:
        self._pick_color(self._set_mask_color)

    def _pick_erase_color(self) -> None:
        self._pick_color(self._set_erase_color)

    def _pick_color(self, setter) -> None:
        picker = ScreenColorPicker(self)
        picker.color_picked.connect(setter)
        picker.start()

    def _sync_range(self) -> None:
        first = self.first_page_spin.value()
        last = self.last_page_spin.value()
        self.last_page_spin.blockSignals(True)
        self.first_page_spin.blockSignals(True)
        if last < first:
            if self.sender() is self.last_page_spin:
                self.first_page_spin.setValue(last)
            else:
                self.last_page_spin.setValue(first)
        self.last_page_spin.blockSignals(False)
        self.first_page_spin.blockSignals(False)

    def _on_changed(self) -> None:
        if self._updating:
            return
        if self.sender() is self.first_page_spin:
            self._start_page_value = self.first_page_spin.value()
        elif self.sender() is self.last_page_spin:
            self._end_is_last = False
        self._sync_range()
        self._update_range_label()
        self.settings_changed.emit(self.preview_settings())

    def _update_range_label(self) -> None:
        settings = self.preview_settings()
        first, last = page_range(settings, self._page_count)
        if last < first:
            self.range_label.setText("No pages selected.")
            return
        if self.erase_active():
            if first == last:
                self.range_label.setText(
                    f"Page {first + 1} will be covered by the mask.")
            else:
                self.range_label.setText(
                    f"Pages {first + 1}–{last + 1} will be covered "
                    "by the mask.")
            return
        total = last - first + 1
        first_text = number_text(settings, settings.start_number, total)
        last_text = number_text(settings, settings.start_number + total - 1,
                                total)
        if total == 1:
            self.range_label.setText(
                f"Page {first + 1} will show “{first_text}”.")
        else:
            self.range_label.setText(
                f"Pages {first + 1}–{last + 1} will show "
                f"“{first_text}” … “{last_text}”.")


def preview_font(settings: PageNumberSettings, pixel_size: int) -> QFont:
    """Qt font used to draw the in-page preview."""
    return qt_preview_font(
        settings.font_family, settings.font_style, pixel_size)
