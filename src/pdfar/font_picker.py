"""Font substitution dialog for existing-text editing (Phase 4).

Shown when the document's own font cannot draw the replacement characters.
It explains *why*, lists ranked system fonts that can, previews the result,
and lets the user browse for another file.  It never installs anything and
never edits the document: it only returns the chosen face.
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .font_discovery import FontFace, FontMatch, face_from_file, missing_characters
from .text_edit_existing import FontIssue


class FontSubstitutionDialog(QDialog):
    """Choose a replacement font for a word the original font cannot draw."""

    def __init__(self, parent: Optional[QWidget], issue: FontIssue,
                 text: str, matches: List[FontMatch]) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Substitute font"))
        self._issue = issue
        self._text = text
        self._matches: List[FontMatch] = list(matches)
        self._chosen: Optional[FontFace] = None
        self._preview_font_id = -1
        self._build_ui()
        self._populate()
        if self._matches:
            self.table.selectRow(0)

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        heading = QLabel(self.tr("The original font cannot draw this text"))
        font = heading.font()
        font.setBold(True)
        heading.setFont(font)
        layout.addWidget(heading)

        info = QGridLayout()
        info.setHorizontalSpacing(10)
        info.setVerticalSpacing(4)
        info.addWidget(QLabel(self.tr("Original font:")), 0, 0)
        info.addWidget(QLabel(self._font_description()), 0, 1)
        info.addWidget(QLabel(self.tr("Problem:")), 1, 0)
        problem = QLabel(self._issue.reason or self.tr("Unsupported characters."))
        problem.setWordWrap(True)
        info.addWidget(problem, 1, 1)
        missing = self._missing_text()
        if missing:
            info.addWidget(QLabel(self.tr("Missing:")), 2, 0)
            missing_label = QLabel(missing)
            missing_label.setWordWrap(True)
            info.addWidget(missing_label, 2, 1)
        layout.addLayout(info)

        layout.addWidget(QLabel(self.tr("Recommended fonts")))

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(
            [self.tr("Font"), self.tr("Style"), self.tr("Width")])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 220)
        self.table.setColumnWidth(1, 130)
        self.table.setMinimumHeight(150)
        self.table.itemSelectionChanged.connect(self._update_preview)
        layout.addWidget(self.table, 1)

        preview_box = QFrame()
        preview_box.setFrameShape(QFrame.Shape.StyledPanel)
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setContentsMargins(10, 8, 10, 8)
        self.preview = QLabel(self._text)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignLeft
                                  | Qt.AlignmentFlag.AlignVCenter)
        self.preview.setMinimumHeight(56)
        self.preview.setWordWrap(True)
        preview_layout.addWidget(self.preview)
        self.width_note = QLabel("")
        self.width_note.setWordWrap(True)
        self.width_note.setStyleSheet("color: #a06000;")
        preview_layout.addWidget(self.width_note)
        layout.addWidget(preview_box)

        buttons = QHBoxLayout()
        self.browse_button = QPushButton(self.tr("Browse…"))
        self.browse_button.clicked.connect(self._browse)
        buttons.addWidget(self.browse_button)
        buttons.addStretch(1)
        box = QDialogButtonBox()
        self.use_button = box.addButton(
            self.tr("Use this font"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

        self.resize(620, 460)

    # ------------------------------------------------------------------ #
    # Content
    # ------------------------------------------------------------------ #
    def _font_description(self) -> str:
        name = self._issue.font_name or self.tr("unknown")
        bits = []
        if self._issue.embedded:
            bits.append(self.tr("embedded"))
        if self._issue.subsetted:
            bits.append(self.tr("subset"))
        return f"{name} ({', '.join(bits)})" if bits else name

    def _missing_text(self) -> str:
        if not self._issue.missing:
            return ""
        parts = []
        for ch in self._issue.missing:
            parts.append(f"{ch} (U+{ord(ch):04X})")
        return ", ".join(parts)

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for match in self._matches:
            row = self.table.rowCount()
            self.table.insertRow(row)
            family = QTableWidgetItem(match.face.family or match.face.display_name)
            family.setData(Qt.ItemDataRole.UserRole, match)
            self.table.setItem(row, 0, family)
            self.table.setItem(row, 1, QTableWidgetItem(match.face.style))
            self.table.setItem(row, 2, QTableWidgetItem(self._width_label(match)))
        self.use_button.setEnabled(bool(self._matches))

    @staticmethod
    def _width_label(match: FontMatch) -> str:
        if not match.width_ratio or abs(match.width_ratio - 1.0) < 0.005:
            return "—"
        delta = (match.width_ratio - 1.0) * 100.0
        return ("+%.0f%%" % delta) if delta > 0 else ("%.0f%%" % delta)

    def selected_match(self) -> Optional[FontMatch]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        match = item.data(Qt.ItemDataRole.UserRole)
        return match if isinstance(match, FontMatch) else None

    def chosen_face(self) -> Optional[FontFace]:
        return self._chosen

    # ------------------------------------------------------------------ #
    # Interaction
    # ------------------------------------------------------------------ #
    def _update_preview(self) -> None:
        match = self.selected_match()
        if match is None:
            self.preview.setFont(QFont())
            self.preview.setText(self._text)
            self.width_note.setText("")
            return
        self.preview.setFont(self._qt_font(match.face))
        self.preview.setText(self._text)
        if match.width_ratio > 1.02:
            self.width_note.setText(
                self.tr("The replacement will be about %d%% wider than the "
                        "original word.") % round((match.width_ratio - 1) * 100))
        elif match.width_ratio and match.width_ratio < 0.98:
            self.width_note.setText(
                self.tr("The replacement will be about %d%% narrower than the "
                        "original word.") % round((1 - match.width_ratio) * 100))
        else:
            self.width_note.setText("")
        if match.notes:
            self.width_note.setText(
                (self.width_note.text() + " " + "; ".join(match.notes)).strip())

    def _qt_font(self, face: FontFace) -> QFont:
        """A QFont that renders *face* for the preview."""
        if self._preview_font_id < 0:
            self._preview_font_id = QFontDatabase.addApplicationFont(face.path)
        families = QFontDatabase.applicationFontFamilies(self._preview_font_id)
        font = QFont(families[0] if families else face.family)
        font.setPointSize(16)
        font.setBold(face.bold)
        font.setItalic(face.italic)
        return font

    def _browse(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self, self.tr("Choose a font file"), "",
            self.tr("Font files (*.ttf *.otf *.ttc *.otc)"))
        if not path:
            return
        face = face_from_file(path)
        missing = missing_characters(face, self._text)
        if missing:
            self.width_note.setText(
                self.tr("The chosen font cannot draw: ")
                + ", ".join(repr(c) for c in missing))
            return
        if not face.embeddable:
            self.width_note.setText(
                self.tr("The chosen font's licence does not allow embedding."))
            return
        if face.outline != "truetype":
            self.width_note.setText(
                self.tr("Only TrueType (.ttf) fonts can be embedded safely."))
            return
        self._chosen = face
        self.accept()

    def _accept(self) -> None:
        match = self.selected_match()
        if match is None:
            return
        self._chosen = match.face
        self.accept()
