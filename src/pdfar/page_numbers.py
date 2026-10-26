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
from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from . import APP_NAME

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

# PyMuPDF base-14 font name, UI label, and the family used for the Qt preview.
FONTS = (
    ("helv", "Helvetica (sans-serif)", "Sans Serif"),
    ("tiro", "Times (serif)", "Serif"),
    ("cour", "Courier (monospace)", "Monospace"),
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
_FONT_KEYS = {key for key, _label, _family in FONTS}
_PREVIEW_FAMILIES = {key: family for key, _label, family in FONTS}
_TEMPLATES = {key: template for key, _label, template in FORMATS}
_UNIT_KEYS = {key for key, _label, _factor, _decimals in UNITS}
_UNIT_FACTORS = {key: factor for key, _label, factor, _decimals in UNITS}
_UNIT_DECIMALS = {key: decimals for key, _label, _factor, decimals in UNITS}
_MAX_OFFSET_POINTS = 2000.0


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
    font_family: str = "helv"
    font_size: int = 12
    margin_x: float = 36.0
    margin_y: float = 36.0
    start_page: int = 1          # 1-based first page to number
    end_page: int = 0            # 0 = last page
    start_number: int = 1        # number printed on start_page
    unit: str = "pt"             # display unit for the offsets
    mask: bool = True            # cover old content behind the number
    mask_padding: float = 4.0    # points of white margin around the glyphs

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

        return cls(
            position=_text("pageNumbers/position", cls.position,
                           _POSITION_KEYS),
            format=_text("pageNumbers/format", cls.format, _FORMAT_KEYS),
            font_family=_text("pageNumbers/font", cls.font_family,
                              _FONT_KEYS),
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
            mask_padding=_float("pageNumbers/maskPadding",
                                cls.mask_padding, 0, _MAX_OFFSET_POINTS),
        )

    def save(self) -> None:
        settings = QSettings(APP_NAME, APP_NAME)
        settings.setValue("pageNumbers/position", self.position)
        settings.setValue("pageNumbers/format", self.format)
        settings.setValue("pageNumbers/font", self.font_family)
        settings.setValue("pageNumbers/fontSize", self.font_size)
        settings.setValue("pageNumbers/marginX", self.margin_x)
        settings.setValue("pageNumbers/marginY", self.margin_y)
        settings.setValue("pageNumbers/startPage", self.start_page)
        settings.setValue("pageNumbers/endPage", self.end_page)
        settings.setValue("pageNumbers/startNumber", self.start_number)
        settings.setValue("pageNumbers/unit", self.unit)
        settings.setValue("pageNumbers/mask", self.mask)
        settings.setValue("pageNumbers/maskPadding", self.mask_padding)


def page_range(settings: PageNumberSettings,
               page_count: int) -> Tuple[int, int]:
    """Inclusive 0-based range of pages to number (empty: (0, -1))."""
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


def layout_number(settings: PageNumberSettings, page_w: float, page_h: float,
                  number: int, total: int,
                  font=None) -> Tuple[str, float, float, str]:
    """Return ``(text, anchor_x, baseline_y, alignment)`` in display points.

    ``anchor_x`` is the left, centre, or right edge of the text depending on
    the alignment; ``baseline_y`` is the text baseline.  Top/bottom margins
    refer to the visual top/bottom of the glyphs, not the baseline.
    """
    font = font or fitz.Font(settings.font_family)
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
    padding: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def mask_rect(self) -> Tuple[float, float, float, float]:
        pad = self.padding
        return (self.left - pad, self.top - pad,
                self.right + pad, self.bottom + pad)


def number_box(settings: PageNumberSettings, page_w: float, page_h: float,
               number: int, total: int, font=None) -> NumberBox:
    """Glyph and mask geometry for one page number."""
    font = font or fitz.Font(settings.font_family)
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
    padding = float(settings.mask_padding) if settings.mask else 0.0
    return NumberBox(text, left, top, left + width, bottom, baseline,
                     anchor_x, alignment, padding)


def box_to_settings(settings: PageNumberSettings, page_w: float, page_h: float,
                    box: NumberBox, dx: float, dy: float) -> PageNumberSettings:
    """Move a number by ``(dx, dy)`` display points and update the offsets.

    A centred number is re-anchored to the nearer side when dragged
    horizontally, so the glyphs never jump and the panel keeps showing a
    meaningful edge offset.
    """
    pad = box.padding
    width, height = box.width, box.height
    min_left = pad if page_w - width >= 2 * pad else 0.0
    max_left = max(min_left, page_w - width - min_left)
    new_left = min(max(box.left + dx, min_left), max_left)
    min_top = pad if page_h - height >= 2 * pad else 0.0
    max_top = max(min_top, page_h - height - min_top)
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


def numbered_copy_path(source_path: str) -> str:
    """Default output path: ``name-numbered.pdf`` next to the source."""
    base, ext = os.path.splitext(source_path)
    return f"{base}-numbered{ext or '.pdf'}"


def stamp_copy(source, dest_path: str,
               settings: PageNumberSettings) -> int:
    """Write a numbered copy of ``source`` (a fitz.Document) and return pages.

    The in-memory document is serialized to bytes first, so unsaved
    annotations are included.  The copy is decrypted, written atomically, and
    the source document is left untouched.
    """
    data = source.tobytes(garbage=0, deflate=True,
                          encryption=fitz.PDF_ENCRYPT_NONE)
    out = fitz.open(stream=data, filetype="pdf")
    tmp_path: Optional[str] = None
    try:
        first, last = page_range(settings, out.page_count)
        if last < first:
            raise ValueError("document has no pages to number")
        font = fitz.Font(settings.font_family)
        total = last - first + 1
        numbered = 0
        for index in range(first, last + 1):
            page = out.load_page(index)
            number = settings.start_number + (index - first)
            box = number_box(settings, page.rect.width, page.rect.height,
                             number, total, font=font)
            if settings.mask:
                page.draw_rect(
                    _display_rect_to_page(page, box.mask_rect),
                    color=None, fill=(1.0, 1.0, 1.0), overlay=True)
            point = fitz.Point(box.left, box.baseline) * page.derotation_matrix
            page.insert_text(
                point, box.text, fontname=settings.font_family,
                fontsize=settings.font_size, color=(0.0, 0.0, 0.0),
                rotate=page.rotation, overlay=True)
            numbered += 1

        directory = os.path.dirname(os.path.abspath(dest_path)) or "."
        fd, tmp_path = tempfile.mkstemp(prefix=".pdfar-numbers-",
                                        suffix=".pdf", dir=directory)
        os.close(fd)
        out.save(tmp_path, garbage=3, deflate=True)
        out.close()
        os.replace(tmp_path, dest_path)
        tmp_path = None
        return numbered
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


class PageNumbersPanel(QWidget):
    """Page-number controls embedded in the right dock.

    Emits ``settings_changed`` for the live preview and ``save_requested``
    when the user asks for a numbered copy.
    """

    settings_changed = pyqtSignal(object)
    save_requested = pyqtSignal()

    def __init__(self, page_count: int = 0,
                 initial: Optional[PageNumberSettings] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._page_count = max(0, int(page_count))
        self._updating = False
        self._unit = (initial or PageNumberSettings()).unit
        initial = initial or PageNumberSettings()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._build_position_group(initial))
        layout.addWidget(self._build_numbering_group(initial))
        # Canonical offsets in points, kept exact across unit switches where
        # the displayed (rounded) value would otherwise drift.
        self._offsets_points = (
            float(initial.margin_x), float(initial.margin_y),
            float(initial.mask_padding))
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
                       self.font_combo):
            widget.currentIndexChanged.connect(self._on_changed)
        self.unit_combo.currentIndexChanged.connect(self._on_unit_changed)
        for widget in (self.size_spin, self.first_page_spin,
                       self.last_page_spin, self.first_number_spin):
            widget.valueChanged.connect(self._on_changed)
        for widget in (self.margin_x_spin, self.margin_y_spin,
                       self.mask_padding_spin):
            widget.valueChanged.connect(self._on_offset_changed)
        self.mask_check.toggled.connect(self._on_mask_toggled)

        self._sync_range()
        self._update_range_label()

    # ------------------------------------------------------------- widgets
    def _build_position_group(self, initial: PageNumberSettings):
        group = QGroupBox("Position", self)
        form = QFormLayout(group)

        self.position_combo = QComboBox(group)
        for key, label in POSITIONS:
            self.position_combo.addItem(label, key)
        self.position_combo.setCurrentIndex(
            max(0, self.position_combo.findData(initial.position)))
        form.addRow("Placement:", self.position_combo)

        self.unit_combo = QComboBox(group)
        for key, label, _factor, _decimals in UNITS:
            self.unit_combo.addItem(label, key)
        self.unit_combo.setCurrentIndex(
            max(0, self.unit_combo.findData(initial.unit)))
        self._unit = self.unit_combo.currentData()
        form.addRow("Offset units:", self.unit_combo)

        self.margin_x_spin = QDoubleSpinBox(group)
        self._configure_offset_spin(self.margin_x_spin)
        self.margin_x_spin.setValue(
            points_to_unit(initial.margin_x, self._unit))
        form.addRow("Horizontal offset:", self.margin_x_spin)

        self.margin_y_spin = QDoubleSpinBox(group)
        self._configure_offset_spin(self.margin_y_spin)
        self.margin_y_spin.setValue(
            points_to_unit(initial.margin_y, self._unit))
        form.addRow("Vertical offset:", self.margin_y_spin)

        self.font_combo = QComboBox(group)
        for key, label, _family in FONTS:
            self.font_combo.addItem(label, key)
        self.font_combo.setCurrentIndex(
            max(0, self.font_combo.findData(initial.font_family)))
        form.addRow("Font:", self.font_combo)

        self.size_spin = QSpinBox(group)
        self.size_spin.setRange(5, 96)
        self.size_spin.setSuffix(" pt")
        self.size_spin.setValue(initial.font_size)
        form.addRow("Font size:", self.size_spin)

        self.mask_check = QCheckBox(
            "Cover existing numbers behind the new number", group)
        self.mask_check.setChecked(initial.mask)
        form.addRow("Mask:", self.mask_check)

        self.mask_padding_spin = QDoubleSpinBox(group)
        self._configure_offset_spin(self.mask_padding_spin)
        self.mask_padding_spin.setValue(
            points_to_unit(initial.mask_padding, self._unit))
        self.mask_padding_spin.setEnabled(initial.mask)
        form.addRow("Mask padding:", self.mask_padding_spin)
        return group

    def _build_numbering_group(self, initial: PageNumberSettings):
        group = QGroupBox("Numbering", self)
        form = QFormLayout(group)

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
        return group

    def _configure_offset_spin(self, spin: QDoubleSpinBox) -> None:
        unit = self.unit_combo.currentData() if hasattr(
            self, "unit_combo") else self._unit
        factor = _UNIT_FACTORS.get(unit, 1.0)
        spin.setDecimals(_UNIT_DECIMALS.get(unit, 1))
        spin.setRange(0.0, _MAX_OFFSET_POINTS / factor)
        spin.setSingleStep(1.0 / factor)
        spin.setSuffix(f" {unit}")

    # ------------------------------------------------------------ settings
    def settings(self) -> PageNumberSettings:
        margin_x, margin_y, mask_padding = self._offsets_points
        return PageNumberSettings(
            position=self.position_combo.currentData(),
            format=self.format_combo.currentData(),
            font_family=self.font_combo.currentData(),
            font_size=self.size_spin.value(),
            margin_x=margin_x,
            margin_y=margin_y,
            start_page=self.first_page_spin.value(),
            end_page=0 if self._end_is_last else self.last_page_spin.value(),
            start_number=self.first_number_spin.value(),
            unit=self.unit_combo.currentData(),
            mask=self.mask_check.isChecked(),
            mask_padding=mask_padding,
        )

    def set_page_count(self, page_count: int) -> None:
        """Match the active document; clamp the range and enable saving."""
        page_count = max(0, int(page_count))
        if page_count == self._page_count:
            self.save_btn.setEnabled(page_count > 0)
            return
        before = self.settings()
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
        if self.settings() != before:
            self.settings_changed.emit(self.settings())

    def apply_settings(self, settings: PageNumberSettings) -> None:
        """Update the controls without emitting (used after dragging)."""
        self._updating = True
        try:
            self._unit = settings.unit
            self._offsets_points = (
                float(settings.margin_x), float(settings.margin_y),
                float(settings.mask_padding))
            self.unit_combo.setCurrentIndex(
                max(0, self.unit_combo.findData(settings.unit)))
            for spin in (self.margin_x_spin, self.margin_y_spin,
                         self.mask_padding_spin):
                self._configure_offset_spin(spin)
            self.margin_x_spin.setValue(
                points_to_unit(settings.margin_x, settings.unit))
            self.margin_y_spin.setValue(
                points_to_unit(settings.margin_y, settings.unit))
            self.mask_padding_spin.setValue(
                points_to_unit(settings.mask_padding, settings.unit))
            self.position_combo.setCurrentIndex(
                max(0, self.position_combo.findData(settings.position)))
            self.format_combo.setCurrentIndex(
                max(0, self.format_combo.findData(settings.format)))
            self.font_combo.setCurrentIndex(
                max(0, self.font_combo.findData(settings.font_family)))
            self.size_spin.setValue(settings.font_size)
            self._start_page_value = max(1, int(settings.start_page))
            self._end_is_last = settings.end_page == 0
            self.first_page_spin.setValue(settings.start_page)
            self.last_page_spin.setValue(
                settings.end_page or max(1, self._page_count))
            self.first_number_spin.setValue(settings.start_number)
            self.mask_check.setChecked(settings.mask)
            self.mask_padding_spin.setEnabled(settings.mask)
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
            unit_to_points(self.mask_padding_spin.value(), unit),
        )
        self._on_changed()

    def _on_unit_changed(self) -> None:
        if self._updating:
            return
        points = self._offsets_points
        self._unit = self.unit_combo.currentData()
        self._updating = True
        try:
            for spin in (self.margin_x_spin, self.margin_y_spin,
                         self.mask_padding_spin):
                self._configure_offset_spin(spin)
            self.margin_x_spin.setValue(
                points_to_unit(points[0], self._unit))
            self.margin_y_spin.setValue(
                points_to_unit(points[1], self._unit))
            self.mask_padding_spin.setValue(
                points_to_unit(points[2], self._unit))
        finally:
            self._updating = False
        self._update_range_label()
        self.settings_changed.emit(self.settings())

    def _on_mask_toggled(self, checked: bool) -> None:
        self.mask_padding_spin.setEnabled(checked)
        if not self._updating:
            self.settings_changed.emit(self.settings())

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
        self.settings_changed.emit(self.settings())

    def _update_range_label(self) -> None:
        settings = self.settings()
        first, last = page_range(settings, self._page_count)
        if last < first:
            self.range_label.setText("No pages selected.")
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
    font = QFont(_PREVIEW_FAMILIES.get(settings.font_family, "Sans Serif"))
    font.setPixelSize(max(1, pixel_size))
    return font
