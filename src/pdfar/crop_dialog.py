"""F10-4: Crop page dialog for setting /CropBox on selected pages."""

from __future__ import annotations

import fitz
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .pages import get_page_content_bbox


class CropDialog(QDialog):
    """Dialog for setting page crop boxes."""

    def __init__(
        self,
        doc: fitz.Document,
        page_indices: tuple[int, ...],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Crop Pages")
        self.setMinimumSize(500, 420)
        self.resize(560, 480)

        self._doc = doc
        self._pages = page_indices

        layout = QVBoxLayout(self)

        # Page range selection
        range_group = QGroupBox("Apply to", self)
        range_layout = QVBoxLayout(range_group)

        self.rb_current = QRadioButton("Current page only", range_group)
        self.rb_selected = QRadioButton("Selected pages", range_group)
        self.rb_all = QRadioButton("All pages", range_group)
        self.rb_custom = QRadioButton("Page range:", range_group)

        if len(page_indices) == 1:
            self.rb_current.setChecked(True)
        else:
            self.rb_selected.setChecked(True)

        range_layout.addWidget(self.rb_current)
        range_layout.addWidget(self.rb_selected)
        range_layout.addWidget(self.rb_all)

        custom_row = QHBoxLayout()
        custom_row.addWidget(self.rb_custom)
        self.custom_range = QLineEdit(range_group)
        self.custom_range.setPlaceholderText("e.g., 1, 3-5, 10-")
        self.custom_range.setEnabled(False)
        custom_row.addWidget(self.custom_range)
        range_layout.addLayout(custom_row)

        self.rb_custom.toggled.connect(self.custom_range.setEnabled)
        layout.addWidget(range_group)

        # Crop mode
        mode_group = QGroupBox("Crop mode", self)
        mode_layout = QVBoxLayout(mode_group)

        self.rb_manual = QRadioButton("Manual crop rectangle", mode_group)
        self.rb_manual.setChecked(True)
        self.rb_auto = QRadioButton("Auto-crop to content", mode_group)
        self.rb_reset = QRadioButton("Reset to page size (MediaBox)", mode_group)

        mode_layout.addWidget(self.rb_manual)
        mode_layout.addWidget(self.rb_auto)
        mode_layout.addWidget(self.rb_reset)
        layout.addWidget(mode_group)

        # Manual crop inputs
        self.manual_group = QGroupBox("Manual crop (points, from top-left)", self)
        manual_layout = QHBoxLayout(self.manual_group)

        self.spin_x = QSpinBox(self.manual_group)
        self.spin_x.setRange(0, 10000)
        self.spin_x.setSuffix(" x")
        self.spin_x.setValue(0)

        self.spin_y = QSpinBox(self.manual_group)
        self.spin_y.setRange(0, 10000)
        self.spin_y.setSuffix(" y")
        self.spin_y.setValue(0)

        self.spin_w = QSpinBox(self.manual_group)
        self.spin_w.setRange(1, 10000)
        self.spin_w.setSuffix(" w")
        self.spin_w.setValue(612)

        self.spin_h = QSpinBox(self.manual_group)
        self.spin_h.setRange(1, 10000)
        self.spin_h.setSuffix(" h")
        self.spin_h.setValue(792)

        manual_layout.addWidget(QLabel("x:"))
        manual_layout.addWidget(self.spin_x)
        manual_layout.addWidget(QLabel("y:"))
        manual_layout.addWidget(self.spin_y)
        manual_layout.addWidget(QLabel("w:"))
        manual_layout.addWidget(self.spin_w)
        manual_layout.addWidget(QLabel("h:"))
        manual_layout.addWidget(self.spin_h)
        layout.addWidget(self.manual_group)

        # Auto-crop margin
        self.auto_group = QGroupBox("Auto-crop options", self)
        self.auto_group.setEnabled(False)
        auto_layout = QHBoxLayout(self.auto_group)

        self.spin_margin = QSpinBox(self.auto_group)
        self.spin_margin.setRange(0, 100)
        self.spin_margin.setSuffix(" pt margin")
        self.spin_margin.setValue(10)

        auto_layout.addWidget(QLabel("Margin:"))
        auto_layout.addWidget(self.spin_margin)
        auto_layout.addStretch(1)
        layout.addWidget(self.auto_group)

        self.rb_auto.toggled.connect(self.auto_group.setEnabled)
        self.rb_manual.toggled.connect(self.manual_group.setEnabled)

        # Preview area (showing first page's dimensions)
        preview_group = QGroupBox("Page dimensions (first selected page)", self)
        preview_layout = QVBoxLayout(preview_group)
        first_page = self._doc.load_page(self._pages[0])
        self.preview_label = QLabel(
            f"MediaBox: {first_page.mediabox.width:.0f} × {first_page.mediabox.height:.0f} pt\n"
            f"Current CropBox: {first_page.cropbox.width:.0f} × {first_page.cropbox.height:.0f} pt"
        )
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(preview_group)

        # Buttons
        self.btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            self,
        )
        self.btn_box.accepted.connect(self.accept)
        self.btn_box.rejected.connect(self.reject)
        layout.addWidget(self.btn_box)

        # Populate manual values from first page's current crop
        page = first_page
        self.spin_x.setValue(int(page.cropbox.x0))
        self.spin_y.setValue(int(page.cropbox.y0))
        self.spin_w.setValue(int(page.cropbox.width))
        self.spin_h.setValue(int(page.cropbox.height))

    def get_target_pages(self) -> list[int]:
        """Resolve the page range from the UI selection."""
        if self.rb_current.isChecked():
            return [self._pages[0]]
        if self.rb_selected.isChecked():
            return list(self._pages)
        if self.rb_all.isChecked():
            return list(range(self._doc.page_count))

        # Custom range
        text = self.custom_range.text().strip()
        if not text:
            return list(self._pages)

        pages = set()
        for part in text.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                start = int(a) - 1 if a else 0
                end = int(b) - 1 if b else self._doc.page_count - 1
                if start < 0:
                    start = 0
                if end >= self._doc.page_count:
                    end = self._doc.page_count - 1
                pages.update(range(start, end + 1))
            else:
                idx = int(part) - 1
                if 0 <= idx < self._doc.page_count:
                    pages.add(idx)
        return sorted(pages)

    def get_crop_rect(self) -> fitz.Rect | None:
        """Return the crop rectangle or None for reset."""
        if self.rb_reset.isChecked():
            return None
        if self.rb_auto.isChecked():
            return "auto"
        # Manual
        return fitz.Rect(
            self.spin_x.value(),
            self.spin_y.value(),
            self.spin_x.value() + self.spin_w.value(),
            self.spin_y.value() + self.spin_h.value(),
        )

    def get_auto_margin(self) -> float:
        return float(self.spin_margin.value())