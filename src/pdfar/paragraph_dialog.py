"""Paragraph reflow editor (Phase 5, §3 / §5 / §7 of the specification).

A dedicated dialog where the user edits an existing PDF paragraph as ordinary
text and sees, before committing, exactly where each new line will land, how the
line count changes, and whether the reflow would overflow the page or collide
with a neighbour.

The dialog never edits the document itself.  It uses the *same* planner
(:func:`pdfar.paragraph.plan_reflow`) that :class:`pdfar.paragraph_edit.ReflowParagraph`
runs on commit, so the preview cannot disagree with the saved result.  When the
planner says the text no longer fits or needs a different font, the dialog asks
for explicit approval (reduce the font size, widen into free space, or choose a
substitute) rather than silently changing the document.
"""

from __future__ import annotations

import math
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from .paragraph import (
    LayoutOptions,
    Paragraph,
    ParagraphPlan,
    ParagraphUnsupported,
    extract_range,
    font_query_for,
    plan_reflow,
)
from .text_edit_existing import FontIssue

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover
    import fitz


# --------------------------------------------------------------------------- #
# Preview
# --------------------------------------------------------------------------- #
class ParagraphPreview(QFrame):
    """A faithful, layout-accurate drawing of a :class:`ParagraphPlan`.

    It paints the page region around the paragraph: the original outline, the
    new bounding box, each planned line's box and text at its real baseline, and
    any overflow / collision it detected.  Glyph *shapes* come from a generic
    Qt font (the page's own font is embedded only in the document), but the line
    positions, widths, bounding boxes and alignment all come from the plan, so
    what the user approves is what the writer reproduces.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(200)
        self.setMinimumWidth(320)
        self._para: Optional[Paragraph] = None
        self._plan: Optional[ParagraphPlan] = None
        self._view = QRectF(0, 0, 1, 1)
        self._scale = 1.0
        self._pad = 12.0

    def set_plan(self, para: Optional[Paragraph],
                 plan: Optional[ParagraphPlan]) -> None:
        self._para = para
        self._plan = plan
        self._recompute_view()
        self.update()

    # -- geometry --------------------------------------------------------- #
    def _recompute_view(self) -> None:
        if self._para is None:
            self._view = QRectF(0, 0, 1, 1)
            self._scale = 1.0
            return
        boxes: List[QRectF] = [QRectF(*self._para.rect)]
        if self._plan is not None:
            if self._plan.rect and self._plan.rect[2] > self._plan.rect[0]:
                boxes.append(QRectF(*self._plan.rect))
            for line in self._plan.lines:
                boxes.append(QRectF(*line.rect))
            for col in self._plan.collisions:
                boxes.append(QRectF(*col.rect))
        x0 = min(b.x() for b in boxes)
        y0 = min(b.y() for b in boxes)
        x1 = max(b.x() + b.width() for b in boxes)
        y1 = max(b.y() + b.height() for b in boxes)
        margin = max(20.0, (y1 - y0) * 0.12)
        x0 -= margin
        y0 -= margin
        x1 += margin
        y1 += margin
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)
        w = max(1.0, self.width() - 2 * self._pad)
        h = max(1.0, self.height() - 2 * self._pad)
        self._scale = min(w / rect.width(), h / rect.height())
        # Keep a sane zoom range so tiny paragraphs are not blown up absurdly.
        self._scale = min(self._scale, 4.0)
        self._view = rect

    def _to_widget(self, x: float, y: float) -> QPointF:
        return QPointF(
            self._pad + (x - self._view.x()) * self._scale,
            self._pad + (y - self._view.y()) * self._scale,
        )

    def _rect_to_widget(self, rect: tuple) -> QRectF:
        tl = self._to_widget(rect[0], rect[1])
        br = self._to_widget(rect[2], rect[3])
        return QRectF(tl, br).normalized()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._recompute_view()

    # -- painting --------------------------------------------------------- #
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._paint(painter)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        if self._para is None:
            return
        # Page backdrop for the visible region.
        view = self._rect_to_widget(
            (self._view.x(), self._view.y(),
             self._view.x() + self._view.width(),
             self._view.y() + self._view.height()))
        painter.fillRect(view, QColor(255, 255, 255))
        painter.setPen(QPen(QColor(210, 210, 210)))
        painter.drawRect(view)

        para = self._para
        angle_deg = math.degrees(para.angle)

        # Original paragraph outline (dashed grey).
        painter.save()
        painter.setPen(QPen(QColor(150, 150, 150), 1, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect_to_widget(para.rect))
        painter.restore()

        plan = self._plan
        if plan is None:
            return

        # Collisions behind the new text (red fill + border).
        for col in plan.collisions:
            r = self._rect_to_widget(col.rect)
            painter.fillRect(r, QColor(255, 120, 120, 60))
            painter.setPen(QPen(QColor(190, 30, 30), 1.2))
            painter.drawRect(r)

        # Overflow band (orange) when the text runs past the page bottom.
        if plan.overflow_bottom and plan.rect and plan.rect[3] > 0:
            bottom = self._to_widget(
                plan.rect[0], min(plan.overflow_bottom,
                                 self._view.y() + self._view.height()))
            top = self._to_widget(plan.rect[0], plan.rect[3])
            band = QRectF(top.x(), top.y(),
                          max(1.0, bottom.x() - top.x()
                              + (plan.rect[2] - plan.rect[0]) * self._scale),
                          max(1.0, bottom.y() - top.y()))
            painter.fillRect(band, QColor(255, 170, 0, 55))
            painter.setPen(QPen(QColor(200, 110, 0), 1.2))
            painter.drawRect(band)

        # Each planned line.
        family = "serif" if para.serif else (
            "monospace" if para.monospaced else "sans-serif")
        for line in plan.lines:
            r = self._rect_to_widget(line.rect)
            painter.fillRect(r, QColor(70, 130, 220, 28))
            painter.setPen(QPen(QColor(60, 110, 200), 1))
            painter.drawRect(r)
            if line.blank:
                continue
            baseline = self._to_widget(line.baseline[0], line.baseline[1])
            painter.save()
            painter.translate(baseline)
            painter.rotate(angle_deg)
            font = QFont(family)
            font.setPointSizeF(
                max(4.0, para.font_size * self._scale))
            font.setBold(para.bold)
            font.setItalic(para.italic)
            painter.setFont(font)
            painter.setPen(QColor(20, 20, 20))
            painter.drawText(QPointF(0, 0), line.text)
            painter.restore()

        # New bounding box (solid blue) on top.
        if plan.rect and plan.rect[2] > plan.rect[0]:
            painter.setPen(QPen(QColor(30, 80, 180), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._rect_to_widget(plan.rect))


# --------------------------------------------------------------------------- #
# Dialog
# --------------------------------------------------------------------------- #
class ParagraphEditorDialog(QDialog):
    """Edit one paragraph as plain text with a live, accurate preview.

    The caller passes the page and an initial :class:`Paragraph`.  Line-range
    spinners let the user widen or narrow the selection (``extract_range`` is
    re-run), which satisfies the "adjust boundaries manually" requirement.  On
    acceptance the caller reads :meth:`build` to obtain a ready
    :class:`pdfar.paragraph_edit.ReflowParagraph`.
    """

    def __init__(self, parent: Optional[QWidget], page: fitz.Page,
                 para: Paragraph, page_num: int = 0,
                 options: Optional[LayoutOptions] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Edit paragraph"))
        self._page = page
        self._page_num = page_num
        self._para = para
        self._total_lines = para.last_index  # highest global index on page
        self._options = LayoutOptions() if options is None else options
        self._substitute = None
        self._issue: Optional[FontIssue] = None
        self._plan: Optional[ParagraphPlan] = None
        self._edited = False

        self._build_ui()
        self._seed_text(keep_breaks=False)
        self._replan()

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        self.heading = QLabel(self.tr("Paragraph"))
        font = self.heading.font()
        font.setBold(True)
        self.heading.setFont(font)
        layout.addWidget(self.heading)

        # Selection range + options row.
        opts = QGridLayout()
        opts.setHorizontalSpacing(10)
        opts.setVerticalSpacing(6)
        opts.addWidget(QLabel(self.tr("Lines")), 0, 0)
        self._from = QSpinBox(self)
        self._from.setRange(1, max(1, self._para.first_index + 1))
        self._from.setValue(self._para.first_index + 1)
        self._to = QSpinBox(self)
        self._to.setRange(1, max(1, self._para.last_index + 1))
        self._to.setValue(self._para.last_index + 1)
        opts.addWidget(self._from, 0, 1)
        opts.addWidget(QLabel(self.tr("to")), 0, 2)
        opts.addWidget(self._to, 0, 3)
        self._range_btn = QPushButton(self.tr("Apply range"))
        self._range_btn.clicked.connect(self._apply_range)
        opts.addWidget(self._range_btn, 0, 4)

        opts.addWidget(QLabel(self.tr("Alignment")), 1, 0)
        self._align = QComboBox(self)
        self._align.addItems([
            self.tr("Keep original"), self.tr("Left"), self.tr("Center"),
            self.tr("Right"), self.tr("Justify")])
        self._align.setCurrentIndex(0)
        self._align.currentIndexChanged.connect(self._replan)
        opts.addWidget(self._align, 1, 1, 1, 2)

        opts.addWidget(QLabel(self.tr("Fit")), 2, 0)
        self._shrink = QPushButton(self.tr("Allow smaller font"))
        self._shrink.setCheckable(True)
        self._shrink.setChecked(self._options.allow_shrink)
        self._shrink.toggled.connect(self._on_shrink)
        opts.addWidget(self._shrink, 2, 1)
        self._expand = QPushButton(self.tr("Widen into free space"))
        self._expand.setCheckable(True)
        self._expand.setChecked(self._options.allow_expand)
        self._expand.toggled.connect(self._on_expand)
        opts.addWidget(self._expand, 2, 3)
        layout.addLayout(opts)

        # Editor.
        self._editor = QPlainTextEdit(self)
        self._editor.setMinimumHeight(110)
        self._editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._editor.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._editor, 1)

        self._breaks_btn = QPushButton(self.tr("Use original line breaks"))
        self._breaks_btn.clicked.connect(self._use_original_breaks)
        layout.addWidget(self._breaks_btn)

        # Preview.
        self._preview = ParagraphPreview(self)
        layout.addWidget(self._preview, 2)

        # Status.
        self._status = QLabel(self.tr(""))
        self._status.setWordWrap(True)
        self._status.setMinimumHeight(40)
        layout.addWidget(self._status)

        # Buttons.
        buttons = QHBoxLayout()
        self._font_btn = QPushButton(self.tr("Choose font…"))
        self._font_btn.clicked.connect(self._choose_font)
        buttons.addWidget(self._font_btn)
        buttons.addStretch(1)
        box = QDialogButtonBox()
        self._apply_btn = box.addButton(
            self.tr("Apply"), QDialogButtonBox.ButtonRole.AcceptRole)
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self._on_accept)
        box.rejected.connect(self.reject)
        buttons.addWidget(box)
        layout.addLayout(buttons)

        self.resize(680, 620)

    # ------------------------------------------------------------------ #
    # Text seeding / ranges
    # ------------------------------------------------------------------ #
    def _seed_text(self, keep_breaks: bool) -> None:
        if keep_breaks:
            text = "\n".join(line.text for line in self._para.lines)
        else:
            text = " ".join(line.text for line in self._para.lines)
        self._editor.blockSignals(True)
        self._editor.setPlainText(text)
        self._editor.blockSignals(False)
        self._edited = False

    def _use_original_breaks(self) -> None:
        self._seed_text(keep_breaks=True)

    def _apply_range(self) -> None:
        first = self._from.value() - 1
        last = self._to.value() - 1
        if first < 0 or last < first:
            self._set_status(self.tr("The line range is invalid."), "warn")
            return
        try:
            self._para = extract_range(self._page, first, last)
        except ParagraphUnsupported as exc:
            self._set_status(str(exc), "error")
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._set_status(str(exc), "error")
            return
        self._total_lines = last
        self._seed_text(keep_breaks=False)
        self._replan()

    # ------------------------------------------------------------------ #
    # Planning
    # ------------------------------------------------------------------ #
    def _current_text(self) -> str:
        return self._editor.toPlainText()

    def _on_text_changed(self) -> None:
        self._edited = True
        if not hasattr(self, "_debounce"):
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.timeout.connect(self._replan)
        self._debounce.start(120)

    def _alignment_value(self) -> Optional[str]:
        idx = self._align.currentIndex()
        return (None, "left", "center", "right", "justify")[idx]

    def _replan(self) -> None:
        if self._para is None:
            return
        options = LayoutOptions(
            alignment=self._alignment_value(),
            allow_expand=self._expand.isChecked(),
            allow_shrink=self._shrink.isChecked(),
        )
        text = self._current_text()
        try:
            plan = plan_reflow(
                self._page, self._para, text, options,
                face=self._substitute.face if self._substitute else None)
        except ParagraphUnsupported as exc:
            self._plan = None
            self._preview.set_plan(self._para, None)
            self._set_status(str(exc), "error")
            self._apply_btn.setEnabled(False)
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._plan = None
            self._preview.set_plan(self._para, None)
            self._set_status(str(exc), "error")
            self._apply_btn.setEnabled(False)
            return

        self._plan = plan
        self._preview.set_plan(self._para, plan)
        self._render_status(plan)
        self._apply_btn.setEnabled(plan.feasible)
        self._font_btn.setEnabled(plan.needs_substitution)

    def _render_status(self, plan: ParagraphPlan) -> None:
        parts: List[str] = []
        delta = plan.line_count_delta
        if delta == 0:
            parts.append(self.tr(f"{plan.line_count} line(s)."))
        elif delta > 0:
            parts.append(f"{plan.line_count} lines (+{delta}).")
        else:
            parts.append(f"{plan.line_count} lines ({delta}).")
        if plan.feasible:
            parts.append(self.tr("Fits inside the available space."))
        else:
            if plan.problems:
                parts.append(" ".join(plan.problems))
            if plan.overflow_bottom:
                parts.append(self.tr(
                    "The text runs past the bottom of the page."))
        if plan.collisions:
            labels = [c.label for c in plan.collisions if c.label]
            parts.append(self.tr("Would touch: ")
                         + (", ".join(labels) if labels
                            else f"{len(plan.collisions)} object(s)"))
        if plan.warnings:
            parts.append(" ".join(plan.warnings))
        if plan.needs_substitution:
            parts.append(plan.substitution_reason
                         or self.tr("The original font cannot draw the text. "
                                    "Choose a font."))
        kind = "ok" if plan.feasible else "error"
        self._set_status(" ".join(parts), kind)

    def _set_status(self, text: str, kind: str) -> None:
        color = {"ok": "#1a7a1a", "warn": "#a06000",
                 "error": "#b00000", "": "#000000"}.get(kind, "#000000")
        self._status.setText(f"<span style='color:{color};'>{text}</span>")

    # ------------------------------------------------------------------ #
    # Options toggles
    # ------------------------------------------------------------------ #
    def _on_shrink(self, checked: bool) -> None:
        self._options = self._options
        self._replan()

    def _on_expand(self, checked: bool) -> None:
        self._replan()

    # ------------------------------------------------------------------ #
    # Font substitution
    # ------------------------------------------------------------------ #
    def _choose_font(self) -> None:
        from .font_discovery import find_alternatives
        from .font_picker import FontSubstitutionDialog

        if self._para is None:
            return
        plan = self._plan
        text = self._current_text()
        issue = self._issue
        if issue is None:
            issue = FontIssue(
                text=text, font_name=self._para.font,
                missing=plan.missing if plan else ())
        try:
            query = font_query_for(self._para, text)
            matches = find_alternatives(query, limit=8)
        except Exception:
            matches = []
        dialog = FontSubstitutionDialog(self, issue, text, matches)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        face = dialog.chosen_face()
        dialog.deleteLater()
        if not accepted or face is None:
            return
        from .text_edit_existing import FontSubstitution
        self._substitute = FontSubstitution(face=face)
        self._replan()

    # ------------------------------------------------------------------ #
    # Acceptance
    # ------------------------------------------------------------------ #
    def _on_accept(self) -> None:
        if self._plan is None or not self._plan.feasible:
            return
        self.accept()

    # ------------------------------------------------------------------ #
    # Output
    # ------------------------------------------------------------------ #
    def build(self):
        """Return a :class:`ReflowParagraph` describing the requested edit."""
        from .paragraph_edit import ReflowParagraph

        options = LayoutOptions(
            alignment=self._alignment_value(),
            allow_expand=self._expand.isChecked(),
            allow_shrink=self._shrink.isChecked(),
        )
        return ReflowParagraph(
            page_num=self._page_num,
            first_index=self._para.first_index,
            last_index=self._para.last_index,
            new_text=self._current_text(),
            anchor=self._para.text,
            options=options,
            substitute=self._substitute,
        )
