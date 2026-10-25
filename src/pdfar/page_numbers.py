"""Page numbering: live preview and a non-destructive numbered copy.

Numbering is applied to a *copy* of the document.  The open document is only
read (``Document.tobytes``) so unsaved highlights are included and the
original file is never modified.

Coordinates
-----------
``layout_number`` works in the page's own display space (``page.rect``, i.e.
with the PDF's /Rotate already applied).  Stamping converts that point to the
unrotated page space with ``page.derotation_matrix`` and passes
``rotate=page.rotation`` to ``insert_text``.  The viewer applies its own extra
rotation on top when drawing the red preview.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLabel,
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

_POSITION_KEYS = {key for key, _label in POSITIONS}
_FORMAT_KEYS = {key for key, _label, _template in FORMATS}
_FONT_KEYS = {key for key, _label, _family in FONTS}
_PREVIEW_FAMILIES = {key: family for key, _label, family in FONTS}
_TEMPLATES = {key: template for key, _label, template in FORMATS}


@dataclass
class PageNumberSettings:
    """How page numbers are formatted and placed (all sizes in points)."""

    position: str = "bottom-center"
    format: str = "number/total"
    font_family: str = "helv"
    font_size: int = 12
    margin_x: int = 36
    margin_y: int = 36
    start_page: int = 1          # 1-based first page to number
    end_page: int = 0            # 0 = last page
    start_number: int = 1        # number printed on start_page

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

        return cls(
            position=_text("pageNumbers/position", cls.position,
                           _POSITION_KEYS),
            format=_text("pageNumbers/format", cls.format, _FORMAT_KEYS),
            font_family=_text("pageNumbers/font", cls.font_family,
                              _FONT_KEYS),
            font_size=_int("pageNumbers/fontSize", cls.font_size, 5, 96),
            margin_x=_int("pageNumbers/marginX", cls.margin_x, 0, 300),
            margin_y=_int("pageNumbers/marginY", cls.margin_y, 0, 300),
            start_page=_int("pageNumbers/startPage", cls.start_page, 1,
                            100000),
            end_page=_int("pageNumbers/endPage", cls.end_page, 0, 100000),
            start_number=_int("pageNumbers/startNumber", cls.start_number,
                              0, 100000),
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
            text, anchor_x, baseline, alignment = layout_number(
                settings, page.rect.width, page.rect.height,
                number, total, font=font)
            left = anchor_x
            if alignment == "center":
                left -= font.text_length(text, settings.font_size) / 2.0
            elif alignment == "right":
                left -= font.text_length(text, settings.font_size)
            point = fitz.Point(left, baseline) * page.derotation_matrix
            page.insert_text(
                point, text, fontname=settings.font_family,
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


class PageNumbersDialog(QDialog):
    """Configure numbering; emits ``settings_changed`` for the live preview."""

    settings_changed = pyqtSignal(object)

    def __init__(self, page_count: int, initial: Optional[PageNumberSettings] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._page_count = max(1, int(page_count))
        initial = initial or PageNumberSettings()
        self.setWindowTitle("Page Numbers")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_position_group(initial))
        layout.addWidget(self._build_numbering_group(initial))

        self.range_label = QLabel(self)
        self.range_label.setWordWrap(True)
        layout.addWidget(self.range_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel, self)
        save = QPushButton("Save Copy…", self)
        save.setDefault(True)
        buttons.addButton(save, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for widget in (self.position_combo, self.format_combo,
                       self.font_combo, self.size_spin, self.margin_x_spin,
                       self.margin_y_spin, self.first_page_spin,
                       self.last_page_spin, self.first_number_spin):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._on_changed)
            else:
                widget.valueChanged.connect(self._on_changed)
        self._sync_range()
        self._update_range_label()

    def _build_position_group(self, initial: PageNumberSettings):
        group = QGroupBox("Position", self)
        form = QFormLayout(group)

        self.position_combo = QComboBox(group)
        for key, label in POSITIONS:
            self.position_combo.addItem(label, key)
        self.position_combo.setCurrentIndex(
            max(0, self.position_combo.findData(initial.position)))
        form.addRow("Placement:", self.position_combo)

        self.margin_x_spin = QSpinBox(group)
        self.margin_x_spin.setRange(0, 300)
        self.margin_x_spin.setSuffix(" pt")
        self.margin_x_spin.setValue(initial.margin_x)
        form.addRow("Horizontal offset:", self.margin_x_spin)

        self.margin_y_spin = QSpinBox(group)
        self.margin_y_spin.setRange(0, 300)
        self.margin_y_spin.setSuffix(" pt")
        self.margin_y_spin.setValue(initial.margin_y)
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
        form.addRow("Size:", self.size_spin)
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
        self.first_page_spin.setRange(1, self._page_count)
        self.first_page_spin.setValue(
            max(1, min(initial.start_page, self._page_count)))
        form.addRow("First page:", self.first_page_spin)

        self.last_page_spin = QSpinBox(group)
        self.last_page_spin.setRange(1, self._page_count)
        last = initial.end_page or self._page_count
        self.last_page_spin.setValue(max(1, min(last, self._page_count)))
        form.addRow("Last page:", self.last_page_spin)

        self.first_number_spin = QSpinBox(group)
        self.first_number_spin.setRange(0, 100000)
        self.first_number_spin.setValue(
            max(0, min(initial.start_number, 100000)))
        form.addRow("First printed number:", self.first_number_spin)
        return group

    def settings(self) -> PageNumberSettings:
        return PageNumberSettings(
            position=self.position_combo.currentData(),
            format=self.format_combo.currentData(),
            font_family=self.font_combo.currentData(),
            font_size=self.size_spin.value(),
            margin_x=self.margin_x_spin.value(),
            margin_y=self.margin_y_spin.value(),
            start_page=self.first_page_spin.value(),
            end_page=self.last_page_spin.value(),
            start_number=self.first_number_spin.value(),
        )

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
