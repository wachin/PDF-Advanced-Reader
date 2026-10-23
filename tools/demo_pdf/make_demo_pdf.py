#!/usr/bin/env python3
"""Build demo.pdf, a comprehensive fixture for PDF-Advanced-Reader.

The document combines a ReportLab layout pass (text, tables, images,
headings H1-H5, TOC and outline) with a PyMuPDF post-processing pass
(annotations, form widgets, links, attachments, page labels).

Requirements: reportlab, pymupdf, pillow, cairosvg (or rsvg-convert).
LibreOffice is optional and only used to build the attached .ods workbook.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from ast import literal_eval
from datetime import datetime
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pragma: no cover
    import fitz as pymupdf

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A3, A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    Image as RLImage,
    KeepTogether,
    ListFlowable,
    ListItem,
    LongTable,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.platypus.tableofcontents import TableOfContents, drawPageNumbers

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[1]
ASSETS = BASE / "assets"
GENERATED = ASSETS / "generated"
ATTACHMENTS = BASE / "attachments"
DEFAULT_OUT = REPO / "demo.pdf"

DATE_TEXT = "23 October 2026"
BUILD_STAMP = "D:20261023090000Z"

PAGE_W, PAGE_H = A4
A3_W, A3_H = A3
LEFT = 58
RIGHT = 50
TOP = 72
BOTTOM = 64
CONTENT_W = PAGE_W - LEFT - RIGHT

INK = HexColor("#1f2937")
MUTED = HexColor("#64748b")
HAIR = HexColor("#d8e0eb")
NAVY = HexColor("#1e3a8a")
BLUE = HexColor("#1d4ed8")
SKY = HexColor("#0369a1")
INDIGO = HexColor("#4338ca")
CORAL = HexColor("#e11d48")
GREEN = HexColor("#15803d")
AMBER = HexColor("#b45309")
ZEBRA = HexColor("#f4f7fb")
HEAD_BG = HexColor("#1e3a8a")

# Collected during the final layout pass and consumed by post-processing.
FORM_FIELDS: dict[str, dict] = {}
HEADING_PAGES: dict[str, int] = {}
ROTATED_PAGE: int | None = None


# ---------------------------------------------------------------------------
# Fonts and styles
# ---------------------------------------------------------------------------

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
FONT_FILES = {
    "DejaVuSans": "DejaVuSans.ttf",
    "DejaVuSans-Bold": "DejaVuSans-Bold.ttf",
    "DejaVuSans-Oblique": "DejaVuSans-Oblique.ttf",
    "DejaVuSans-BoldOblique": "DejaVuSans-BoldOblique.ttf",
    "DejaVuSerif": "DejaVuSerif.ttf",
    "DejaVuSerif-Bold": "DejaVuSerif-Bold.ttf",
    "DejaVuSerif-Italic": "DejaVuSerif-Italic.ttf",
    "DejaVuSerif-BoldItalic": "DejaVuSerif-BoldItalic.ttf",
    "DejaVuSansMono": "DejaVuSansMono.ttf",
    "DejaVuSansMono-Bold": "DejaVuSansMono-Bold.ttf",
}

for _name, _file in FONT_FILES.items():
    _path = FONT_DIR / _file
    if not _path.exists():
        raise SystemExit(f"Missing font {_path}; install fonts-dejavu-core.")
    pdfmetrics.registerFont(TTFont(_name, str(_path)))

pdfmetrics.registerFontFamily(
    "DejaVuSans",
    normal="DejaVuSans",
    bold="DejaVuSans-Bold",
    italic="DejaVuSans-Oblique",
    boldItalic="DejaVuSans-BoldOblique",
)
pdfmetrics.registerFontFamily(
    "DejaVuSerif",
    normal="DejaVuSerif",
    bold="DejaVuSerif-Bold",
    italic="DejaVuSerif-Italic",
    boldItalic="DejaVuSerif-BoldItalic",
)
pdfmetrics.registerFontFamily(
    "DejaVuSansMono",
    normal="DejaVuSansMono",
    bold="DejaVuSansMono-Bold",
    italic="DejaVuSansMono",
    boldItalic="DejaVuSansMono-Bold",
)


def make_styles() -> dict[str, ParagraphStyle]:
    s: dict[str, ParagraphStyle] = {}
    s["Body"] = ParagraphStyle(
        "Body", fontName="DejaVuSerif", fontSize=10.1, leading=15.0,
        textColor=INK, alignment=TA_JUSTIFY, spaceAfter=6.5,
        allowWidows=1, allowOrphans=1,
    )
    s["BodySans"] = ParagraphStyle(
        "BodySans", parent=s["Body"], fontName="DejaVuSans",
        fontSize=9.9, leading=14.6,
    )
    s["Lead"] = ParagraphStyle(
        "Lead", parent=s["Body"], fontName="DejaVuSans", fontSize=11.3,
        leading=16.6, textColor=HexColor("#334155"), alignment=TA_LEFT,
        spaceAfter=9,
    )
    s["Small"] = ParagraphStyle(
        "Small", parent=s["Body"], fontName="DejaVuSans", fontSize=8.3,
        leading=11.6, textColor=MUTED, alignment=TA_LEFT, spaceAfter=4,
    )
    s["H1"] = ParagraphStyle(
        "H1", fontName="DejaVuSans-Bold", fontSize=21, leading=26,
        textColor=NAVY, spaceBefore=6, spaceAfter=11, keepWithNext=1,
    )
    s["H2"] = ParagraphStyle(
        "H2", fontName="DejaVuSans-Bold", fontSize=15.4, leading=20,
        textColor=BLUE, spaceBefore=14, spaceAfter=7, keepWithNext=1,
    )
    s["H3"] = ParagraphStyle(
        "H3", fontName="DejaVuSans-Bold", fontSize=12.7, leading=17,
        textColor=HexColor("#0f172a"), spaceBefore=11, spaceAfter=5,
        keepWithNext=1,
    )
    s["H4"] = ParagraphStyle(
        "H4", fontName="DejaVuSans-Bold", fontSize=11.1, leading=15.2,
        textColor=HexColor("#334155"), spaceBefore=9, spaceAfter=4,
        keepWithNext=1,
    )
    s["H5"] = ParagraphStyle(
        "H5", fontName="DejaVuSans-BoldOblique", fontSize=10.2,
        leading=14, textColor=HexColor("#475569"), spaceBefore=8,
        spaceAfter=3, keepWithNext=1,
    )
    s["Caption"] = ParagraphStyle(
        "Caption", parent=s["Small"], fontName="DejaVuSans", fontSize=8.4,
        leading=11.4, alignment=TA_CENTER, textColor=MUTED,
        spaceBefore=4, spaceAfter=11,
    )
    s["Code"] = ParagraphStyle(
        "Code", fontName="DejaVuSansMono", fontSize=8.1, leading=11.0,
        textColor=HexColor("#0f172a"), spaceBefore=0, spaceAfter=0,
    )
    s["Quote"] = ParagraphStyle(
        "Quote", parent=s["Body"], fontName="DejaVuSerif-Italic",
        fontSize=10.0, leading=15.0, textColor=HexColor("#475569"),
        leftIndent=12, rightIndent=8, alignment=TA_LEFT, spaceAfter=0,
    )
    s["Bullet"] = ParagraphStyle(
        "Bullet", parent=s["Body"], fontSize=10.0, leading=14.4,
        leftIndent=16, bulletIndent=3, alignment=TA_LEFT, spaceAfter=2.5,
    )
    s["Number"] = ParagraphStyle(
        "Number", parent=s["Bullet"], leftIndent=20, bulletIndent=3,
    )
    s["Cell"] = ParagraphStyle(
        "Cell", fontName="DejaVuSans", fontSize=8.5, leading=11.4,
        textColor=HexColor("#1f2937"), alignment=TA_LEFT,
    )
    s["CellSmall"] = ParagraphStyle(
        "CellSmall", parent=s["Cell"], fontSize=7.7, leading=10.2,
    )
    s["CellMono"] = ParagraphStyle(
        "CellMono", parent=s["Cell"], fontName="DejaVuSansMono",
        fontSize=7.6, leading=10.2,
    )
    s["CellRight"] = ParagraphStyle(
        "CellRight", parent=s["Cell"], alignment=TA_RIGHT,
    )
    s["CellCenter"] = ParagraphStyle(
        "CellCenter", parent=s["Cell"], alignment=TA_CENTER,
    )
    s["Head"] = ParagraphStyle(
        "Head", fontName="DejaVuSans-Bold", fontSize=8.6, leading=11.2,
        textColor=colors.white,
    )
    s["HeadRight"] = ParagraphStyle(
        "HeadRight", parent=s["Head"], alignment=TA_RIGHT,
    )
    s["HeadCenter"] = ParagraphStyle(
        "HeadCenter", parent=s["Head"], alignment=TA_CENTER,
    )
    s["TOCTitle"] = ParagraphStyle(
        "TOCTitle", fontName="DejaVuSans-Bold", fontSize=24, leading=30,
        textColor=NAVY, spaceAfter=4,
    )
    toc_sizes = [(10.4, 15.5, 0), (9.6, 14.0, 13), (9.0, 13.0, 26),
                 (8.6, 12.4, 39), (8.3, 11.9, 52)]
    for level, (size, leading, indent) in enumerate(toc_sizes):
        s[f"TOC{level}"] = ParagraphStyle(
            f"TOC{level}", fontName="DejaVuSans-Bold" if level == 0 else "DejaVuSans",
            fontSize=size, leading=leading, leftIndent=indent,
            textColor=NAVY if level == 0 else HexColor("#334155"),
            spaceBefore=3 if level == 0 else 0,
        )
    return s


S = make_styles()


# ---------------------------------------------------------------------------
# Flowables
# ---------------------------------------------------------------------------

class Heading(Paragraph):
    def __init__(self, text: str, level: int, key: str | None = None,
                 toc: bool = True):
        self.level = level
        self.toc = toc
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48]
        self.key = key or f"h-{level}-{slug}"
        super().__init__(text, S[f"H{level}"])


class Anchor(Flowable):
    def __init__(self, key: str, outline_title: str | None = None,
                 level: int = 0, doc=None):
        super().__init__()
        self.key = key
        self.outline_title = outline_title
        self.level = level
        self.doc = doc

    def wrap(self, aw, ah):
        return (0, 0)

    def draw(self):
        self.canv.bookmarkPage(self.key)
        if self.doc is not None and self.outline_title:
            self.doc.current_section = self.outline_title
        if self.outline_title:
            self.canv.addOutlineEntry(
                self.outline_title, self.key, level=self.level, closed=False
            )


class CoverMarker(Flowable):
    def wrap(self, aw, ah):
        return (0, 0)

    def draw(self):
        pass


class BlankExhibit(Flowable):
    """An empty page reserved for content drawn during post-processing."""

    def __init__(self, doc):
        super().__init__()
        self.doc = doc

    def wrap(self, aw, ah):
        return (aw, ah)

    def draw(self):
        global ROTATED_PAGE
        ROTATED_PAGE = self.canv.getPageNumber()


class FieldRow(Flowable):
    """Form row that draws its own box and records its page rectangle."""

    def __init__(self, label: str, kind: str = "text", *, width: float = 250,
                 height: float = 23, name: str | None = None,
                 value: str = "", choices: list[str] | None = None,
                 group: str | None = None, selected: bool = False,
                 caption: str | None = None, multiline: bool = False):
        super().__init__()
        self.label = label
        self.kind = kind
        self.field_w = width
        self.h = height
        self.name = name or re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        self.value = value
        self.choices = choices or []
        self.group = group
        self.selected = selected
        self.caption = caption
        self.multiline = multiline

    def wrap(self, aw, ah):
        self.avail = aw
        return (aw, self.h + 9)

    def draw(self):
        c = self.canv
        ax, ay = c.absolutePosition(0, 0)
        page_no = c.getPageNumber()
        page_h = c._pagesize[1]

        def to_page(x0, y0, x1, y1):
            # ReportLab uses a bottom-left origin; PyMuPDF uses top-left.
            return pymupdf.Rect(x0, page_h - y1, x1, page_h - y0)

        c.saveState()
        c.setFont("DejaVuSans", 9.3)
        c.setFillColor(INK)

        if self.kind in ("check", "radio"):
            cy = self.h / 2
            if self.kind == "check":
                c.setStrokeColor(HexColor("#475569"))
                c.setLineWidth(0.9)
                c.rect(0, cy - 8, 16, 16, stroke=1, fill=0)
                c.drawString(24, cy - 3.2, self.label)
            else:
                c.setStrokeColor(HexColor("#475569"))
                c.setLineWidth(0.9)
                c.circle(8, cy, 8, stroke=1, fill=0)
                c.drawString(24, cy - 3.2, self.label)
            rect = to_page(ax, ay + cy - 8, ax + 16, ay + cy + 8)
        else:
            c.drawString(0, self.h / 2 - 3.2, self.label)
            x0 = 172
            w = min(self.field_w, self.avail - x0 - 4)
            if self.kind == "button":
                w = 132
                c.setFillColor(HexColor("#eef2ff"))
                c.setStrokeColor(HexColor("#6366f1"))
                c.roundRect(x0, 1, w, self.h - 2, 4, stroke=1, fill=1)
                c.setFillColor(INDIGO)
                c.setFont("DejaVuSans-Bold", 8.8)
                c.drawCentredString(x0 + w / 2, self.h / 2 - 3, self.caption or self.label)
            else:
                c.setFillColor(colors.white)
                c.setStrokeColor(HexColor("#94a3b8"))
                c.setLineWidth(0.9)
                c.roundRect(x0, 1, w, self.h - 2, 3, stroke=1, fill=1)
            rect = to_page(ax + x0, ay + 1, ax + x0 + w, ay + self.h - 1)

        c.restoreState()
        FORM_FIELDS[self.label] = {
            "page": page_no,
            "kind": self.kind,
            "rect": rect,
            "name": self.name,
            "value": self.value,
            "choices": list(self.choices),
            "group": self.group,
            "selected": self.selected,
            "caption": self.caption,
            "multiline": self.multiline,
        }


class GradientPanel(Flowable):
    def __init__(self, width: float = CONTENT_W, height: float = 150):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, aw, ah):
        self.width = min(self.width, aw)
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(0, 0, w, h, 10)
        c.clipPath(clip, stroke=0, fill=0)
        c.linearGradient(0, h, w, 0,
                         [HexColor("#eef2ff"), HexColor("#38bdf8"),
                          HexColor("#e11d48")], (0, 0.55, 1))
        c.setFillAlpha(0.55)
        c.setFillColor(HexColor("#1e3a8a"))
        c.roundRect(28, 26, 150, 72, 8, stroke=0, fill=1)
        c.setFillColor(HexColor("#fef3c7"))
        c.roundRect(120, 46, 210, 72, 8, stroke=0, fill=1)
        c.setFillAlpha(0.28)
        c.setFillColor(colors.white)
        c.roundRect(300, 18, 150, 110, 8, stroke=0, fill=1)
        c.setFillAlpha(0.62)
        c.setFillColor(HexColor("#0b1220"))
        c.rect(0, h - 58, w, 58, stroke=0, fill=1)
        c.restoreState()
        c.saveState()
        c.setFillAlpha(1)
        c.setFillColor(colors.white)
        c.setFont("DejaVuSans-Bold", 15)
        c.drawString(26, h - 32, "Transparency and gradients")
        c.setFont("DejaVuSans", 9)
        c.setFillColor(HexColor("#f8fafc"))
        c.drawString(26, h - 50, "alpha blends, linear gradients, rounded clipping")
        c.restoreState()


class RadarChart(Flowable):
    def __init__(self, width: float = CONTENT_W, height: float = 280):
        super().__init__()
        self.width = width
        self.height = height

    def wrap(self, aw, ah):
        self.width = min(self.width, aw)
        return (self.width, self.height)

    def draw(self):
        c = self.canv
        w, h = self.width, self.height
        cx, cy = w / 2, h / 2 + 4
        radius = min(w, h) * 0.34
        labels = ["Render", "Search", "Memory", "Zoom", "Forms", "Tiles"]
        series = [
            ([0.94, 0.78, 0.62, 0.88, 0.52, 0.83], HexColor("#3b82f6")),
            ([0.60, 0.92, 0.85, 0.55, 0.76, 0.64], HexColor("#e11d48")),
        ]
        n = len(labels)
        angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
        c.saveState()

        for ring in (0.25, 0.5, 0.75, 1.0):
            c.setStrokeColor(HexColor("#dbe3f0"))
            c.setLineWidth(0.7)
            path = c.beginPath()
            for i, a in enumerate(angles):
                x = cx + radius * ring * math.cos(a)
                y = cy + radius * ring * math.sin(a)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.close()
            c.drawPath(path, stroke=1, fill=0)

        for a in angles:
            c.setStrokeColor(HexColor("#e2e8f0"))
            c.line(cx, cy, cx + radius * math.cos(a), cy + radius * math.sin(a))

        for values, color in series:
            c.setFillColor(color)
            c.setFillAlpha(0.22)
            c.setStrokeColor(color)
            c.setStrokeAlpha(0.9)
            c.setLineWidth(2)
            path = c.beginPath()
            for i, (a, v) in enumerate(zip(angles, values)):
                x = cx + radius * v * math.cos(a)
                y = cy + radius * v * math.sin(a)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.close()
            c.drawPath(path, stroke=1, fill=1)
            c.setFillAlpha(1)
            for a, v in zip(angles, values):
                c.circle(cx + radius * v * math.cos(a),
                         cy + radius * v * math.sin(a), 2.4, stroke=0, fill=1)

        c.setFillColor(INK)
        c.setFont("DejaVuSans", 8.2)
        for a, label in zip(angles, labels):
            x = cx + (radius + 22) * math.cos(a)
            y = cy + (radius + 22) * math.sin(a)
            c.drawCentredString(x, y - 3, label)
        c.setFont("DejaVuSans-Bold", 9)
        c.setFillColor(HexColor("#3b82f6"))
        c.drawString(18, h - 20, "Coverage A")
        c.setFillColor(HexColor("#e11d48"))
        c.drawString(110, h - 20, "Coverage B")
        c.restoreState()


# ---------------------------------------------------------------------------
# Content helpers
# ---------------------------------------------------------------------------

def P(text: str, style: str = "Body") -> Paragraph:
    return Paragraph(text, S[style])


def heading(level: int, text: str, key: str | None = None,
            toc: bool = True) -> Heading:
    return Heading(text, level, key=key, toc=toc)


def spacer(height: float = 8) -> Spacer:
    return Spacer(1, height)


def rule(color=HAIR, width=0.7, space_before=4, space_after=10) -> HRFlowable:
    return HRFlowable(width="100%", thickness=width, color=color,
                      spaceBefore=space_before, spaceAfter=space_after)


def bullets(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(P(item, "Bullet"), leftIndent=18) for item in items],
        bulletType="bullet", bulletFontName="DejaVuSans", bulletFontSize=7.5,
        start="•", leftIndent=16, spaceBefore=2, spaceAfter=7,
    )


def numbered(items: list[str]) -> ListFlowable:
    return ListFlowable(
        [ListItem(P(item, "Number"), leftIndent=22) for item in items],
        bulletType="1", bulletFormat="%s.", bulletFontName="DejaVuSans-Bold",
        bulletFontSize=9, leftIndent=20, spaceBefore=2, spaceAfter=7,
    )


def callout(title: str, text: str, bg="#eff6ff", border="#93c5fd",
            accent=BLUE) -> Table:
    flow = P(f"<b>{title}</b><br/>{text}", "BodySans")
    tbl = Table([[flow]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(bg)),
        ("BOX", (0, 0), (-1, -1), 0.6, HexColor(border)),
        ("LINEBEFORE", (0, 0), (0, -1), 4, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return tbl


def quote_box(text: str) -> Table:
    tbl = Table([[P(text, "Quote")]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8fafc")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, INDIGO),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return tbl


def code_block(text: str) -> Table:
    tbl = Table([[Preformatted(text, S["Code"])]], colWidths=[CONTENT_W])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#cbd5e1")),
        ("LINEBEFORE", (0, 0), (0, -1), 3, HexColor("#94a3b8")),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


def image(path: Path, width: float, caption: str | None = None) -> list:
    with Image.open(path) as probe:
        src_w, src_h = probe.size
    img = RLImage(str(path), width=width, height=width * src_h / src_w)
    img.hAlign = "CENTER"
    out: list = [img]
    if caption:
        out.append(P(caption, "Caption"))
    return out


def cell(text: str, style: str = "Cell") -> Paragraph:
    return Paragraph(text, S[style])


def data_table(rows, widths, *, header: bool = True, zebra: bool = True,
               align: str = "LEFT", font_size: float = 8.5,
               repeat: bool = False, extra=None) -> LongTable:
    body_style = ParagraphStyle(
        "cell-copy", parent=S["Cell"], fontSize=font_size,
        leading=font_size * 1.34,
    )
    head_style = ParagraphStyle(
        "head-copy", parent=S["Head"], fontSize=font_size + 0.1,
        leading=(font_size + 0.1) * 1.3,
    )
    cooked = []
    for r, row in enumerate(rows):
        out_row = []
        for c, value in enumerate(row):
            if isinstance(value, (Paragraph, Table, RLImage, Preformatted, Flowable)):
                out_row.append(value)
            elif header and r == 0:
                st = head_style
                if align == "RIGHT" and c > 0:
                    st = ParagraphStyle("hr", parent=head_style, alignment=TA_RIGHT)
                elif align == "CENTER" and c > 0:
                    st = ParagraphStyle("hc", parent=head_style, alignment=TA_CENTER)
                out_row.append(Paragraph(str(value), st))
            else:
                st = body_style
                if align == "RIGHT" and c > 0:
                    st = ParagraphStyle("cr", parent=body_style, alignment=TA_RIGHT)
                elif align == "CENTER" and c > 0:
                    st = ParagraphStyle("cc", parent=body_style, alignment=TA_CENTER)
                out_row.append(Paragraph(str(value), st))
        cooked.append(out_row)

    cls = LongTable if repeat else Table
    kwargs = {"colWidths": widths, "hAlign": "LEFT"}
    if repeat:
        kwargs["repeatRows"] = 1
    tbl = cls(cooked, **kwargs)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("GRID", (0, 0), (-1, -1), 0.4, HAIR),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ]
    if zebra:
        cmds.append(("ROWBACKGROUNDS", (0, 1 if header else 0), (-1, -1),
                     [colors.white, ZEBRA]))
    if extra:
        cmds += extra
    tbl.setStyle(TableStyle(cmds))
    return tbl


# ---------------------------------------------------------------------------
# Asset generation
# ---------------------------------------------------------------------------

SVG_SPECS = [
    ("logo.svg", "logo.png", 512),
    ("architecture.svg", "architecture.png", 1600),
    ("pipeline.svg", "pipeline.png", 1600),
    ("zoom-levels.svg", "zoom-levels.png", 1400),
    ("feature-grid.svg", "feature-grid.png", 1600),
]


def rasterize_svgs() -> None:
    try:
        import cairosvg
    except ImportError:
        cairosvg = None
    for src, dst, width in SVG_SPECS:
        src_path = ASSETS / src
        dst_path = GENERATED / dst
        if cairosvg is not None:
            cairosvg.svg2png(url=str(src_path), write_to=str(dst_path),
                             output_width=width)
            continue
        tool = shutil.which("rsvg-convert")
        if tool:
            subprocess.run([tool, "-w", str(width), "-o", str(dst_path),
                            str(src_path)], check=True)
            continue
        raise SystemExit(
            "Install cairosvg or rsvg-convert to rasterize the SVG assets."
        )


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / "DejaVuSerif.ttf"), size)


def _sans(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(str(FONT_DIR / name), size)


def make_photo() -> None:
    w, h = 1400, 900
    img = Image.new("RGB", (w, h), "#0c4a6e")
    d = ImageDraw.Draw(img)

    for y in range(h):
        t = y / h
        if t < 0.55:
            k = t / 0.55
            r = int(14 + (253 - 14) * k)
            g = int(116 + (230 - 116) * k)
            b = int(144 + (138 - 144) * k)
        else:
            k = (t - 0.55) / 0.45
            r = int(253 - (15 - 253) * k)
            g = int(230 - (23 - 230) * k)
            b = int(138 - (42 - 138) * k)
        d.line([(0, y), (w, y)], fill=(r, g, b))

    d.ellipse((980, 120, 1160, 300), fill=(255, 241, 190))
    d.ellipse((930, 70, 1210, 350), outline=(255, 237, 168), width=18)
    d.polygon([(0, 560), (230, 300), (430, 520), (620, 360), (860, 580),
               (0, 740)], fill=(30, 64, 110))
    d.polygon([(240, 560), (520, 250), (760, 540), (560, 610),
               (350, 600)], fill=(46, 78, 128))
    d.polygon([(0, 700), (300, 520), (560, 680), (900, 500), (1240, 690),
               (1400, 620), (1400, 900), (0, 900)], fill=(12, 74, 110))
    for i in range(18):
        y = 620 + i * 12
        d.line([(0, y), (w, y + 20)], fill=(90, 160, 185), width=2)
    for i in range(8):
        y = 640 + i * 28
        d.line([(100 + i * 60, y), (400 + i * 120, y + 8)],
               fill=(200, 230, 235), width=3)
    d.polygon([(0, 800), (180, 760), (360, 860), (0, 900)], fill=(9, 52, 78))

    grain = Image.effect_noise((w, h), 22).convert("RGB")
    img = Image.blend(img, grain, 0.05)
    img = img.filter(ImageFilter.GaussianBlur(0.4))
    img.save(GENERATED / "photo.jpg", quality=87, optimize=True)

    gray = img.convert("L")
    gray.save(GENERATED / "grayscale.jpg", quality=86, optimize=True)


def make_alpha_image() -> None:
    w, h = 760, 460
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((20, 20, 380, 260), radius=26,
                        fill=(59, 130, 246, 180))
    d.rounded_rectangle((250, 160, 720, 430), radius=26,
                        fill=(225, 29, 72, 150))
    d.ellipse((430, 40, 690, 300), fill=(251, 191, 36, 130))
    d.ellipse((90, 280, 250, 440), fill=(16, 185, 129, 120))
    d.text((46, 70), "alpha = 0.70", font=_sans(46, True), fill=(255, 255, 255, 235))
    d.text((46, 130), "transparent PNG", font=_sans(34), fill=(255, 255, 255, 200))
    img.save(GENERATED / "alpha.png", optimize=True)


SCAN_PARAGRAPHS = [
    "The render pool keeps a priority queue. Pages that are visible on screen "
    "are rendered before pages that are merely close by, and cached tiles are "
    "reused until the memory budget is exceeded.",
    "Selection follows the text layer, not the painted pixels. Dragging across "
    "a line selects real characters, and copying preserves reading order even "
    "when the page has been rotated or zoomed.",
    "Search accepts words, exact phrases and regular expressions. Matches are "
    "highlighted in the page and listed in the sidebar so the user can jump "
    "between them without losing the reading position.",
    "Thumbnails are produced by the same render pool at a lower resolution. "
    "They are always generated after the visible page, which keeps the first "
    "paint fast on documents with hundreds of pages.",
    "The outline is read from the PDF bookmarks and shown as a tree. Documents "
    "with five heading levels remain navigable because each level can be "
    "collapsed independently.",
]


def _wrap_text(draw, text: str, font, width: int) -> list[str]:
    words = text.split()
    lines, line = [], ""
    for word in words:
        probe = f"{line} {word}".strip()
        if draw.textlength(probe, font=font) <= width:
            line = probe
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def make_scanned_page() -> None:
    dpi = 200
    w = int(8.27 * dpi)
    h = int(11.69 * dpi)
    img = Image.new("RGB", (w, h), (247, 243, 233))
    d = ImageDraw.Draw(img)
    body = _font(27)
    mono = ImageFont.truetype(str(FONT_DIR / "DejaVuSansMono.ttf"), 22)
    bold = _font(40)

    d.rectangle((90, 80, w - 90, 190), fill=(238, 232, 216))
    d.rectangle((90, 80, w - 90, 190), outline=(150, 140, 115), width=3)
    d.text((120, 105), "PDFAR FIELD NOTES", font=bold, fill=(60, 55, 45))
    d.text((w - 620, 118), "scanned test sheet · 200 dpi",
           font=mono, fill=(90, 82, 66))
    d.line((90, 230, w - 90, 230), fill=(120, 110, 90), width=3)

    scan_lines = []

    def record(x, y, text, size, kind):
        scan_lines.append({"x": x, "y": y, "text": text,
                           "size": size, "font": kind})

    record(120, 105, "PDFAR FIELD NOTES", 40, "bold")
    record(w - 620, 118, "scanned test sheet · 200 dpi", 22, "mono")

    col_w = (w - 240) // 2
    x_left = 110
    x_right = 130 + col_w
    y = 280
    for block in SCAN_PARAGRAPHS[:3]:
        for line in _wrap_text(d, block, body, col_w):
            d.text((x_left, y), line, font=body, fill=(48, 44, 36))
            record(x_left, y, line, 27, "body")
            y += 40
        y += 24

    y_right = 280
    for block in SCAN_PARAGRAPHS[3:]:
        for line in _wrap_text(d, block, body, col_w):
            d.text((x_right, y_right), line, font=body, fill=(48, 44, 36))
            record(x_right, y_right, line, 27, "body")
            y_right += 40
        y_right += 24

    tx, ty = 110, max(y, y_right) + 40
    rows = [
        ["Item", "Structure", "Pages", "State"],
        ["A-01", "Text page", "12", "ok"],
        ["A-02", "Table, 4 columns", "14", "ok"],
        ["A-03", "Full-page image", "21", "ok"],
        ["A-04", "Annotations", "23", "ok"],
        ["A-05", "Form fields", "25", "ok"],
    ]
    row_h = 48
    col_ws = [150, 480, 180, 180]
    for r, row in enumerate(rows):
        x = tx
        for c, value in enumerate(row):
            d.rectangle((x, ty + r * row_h, x + col_ws[c], ty + (r + 1) * row_h),
                        outline=(130, 120, 100), width=2,
                        fill=(236, 229, 212) if r == 0 else None)
            d.text((x + 12, ty + r * row_h + 12), value, font=mono,
                   fill=(55, 50, 40))
            record(x + 12, ty + r * row_h + 12, value, 22, "mono")
            x += col_ws[c]

    stamp = Image.new("RGBA", (520, 220), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rounded_rectangle((10, 10, 510, 210), radius=18, outline=(178, 34, 52, 230), width=8)
    sd.text((46, 62), "SCANNED COPY", font=_sans(52, True), fill=(178, 34, 52, 230))
    sd.text((46, 130), "pdfar demo fixture", font=_sans(30), fill=(178, 34, 52, 210))
    stamp = stamp.rotate(-8, expand=True, resample=Image.BICUBIC)
    img.paste(stamp, (w - 620, ty + 80), stamp)

    d.ellipse((w - 260, 240, w - 90, 410), outline=(170, 140, 90), width=6)
    d.ellipse((w - 240, 260, w - 110, 390), outline=(185, 155, 105), width=2)
    d.text((110, h - 120), "page 1 · synthetic scan · generated for PDFAR",
           font=mono, fill=(105, 96, 80))
    record(110, h - 120, "page 1 · synthetic scan · generated for PDFAR",
           22, "mono")
    record(w - 590, ty + 110, "SCANNED COPY", 52, "bold")

    grain = Image.effect_noise((w, h), 16).convert("RGB")
    img = Image.blend(img, grain, 0.055)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img.save(GENERATED / "scan-page.jpg", quality=82, optimize=True)
    (GENERATED / "scan-text.json").write_text(
        json.dumps({"width": w, "height": h, "lines": scan_lines},
                   indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


TABLE_CSV_ROWS = [
    ("ST-001", "Typography", "H1 heading", "3", "Stable", "Section opener"),
    ("ST-002", "Typography", "H2/H3 subheads", "3", "Stable", "TOC level 2-3"),
    ("ST-003", "Typography", "H4/H5 subheads", "3", "Stable", "Deep outline"),
    ("ST-004", "Typography", "Justified paragraph", "4", "Stable", "Wide measure"),
    ("ST-005", "Typography", "Bullet list", "5", "Stable", "Nested bullets"),
    ("ST-006", "Typography", "Numbered list", "5", "Stable", "Two-digit items"),
    ("ST-007", "Typography", "Block quote", "6", "Stable", "Indented rule"),
    ("ST-008", "Typography", "Code block", "6", "Stable", "Monospace"),
    ("ST-009", "Typography", "Two columns", "7", "Stable", "Frame switch"),
    ("ST-010", "Typography", "Unicode samples", "7", "Stable", "Greek, Cyrillic"),
    ("IM-001", "Images", "PNG with alpha", "9", "Stable", "Transparent"),
    ("IM-002", "Images", "JPEG photograph", "10", "Stable", "Color"),
    ("IM-003", "Images", "Grayscale JPEG", "10", "Stable", "DeviceGray"),
    ("IM-004", "Images", "Vector diagram", "9", "Stable", "Large raster"),
    ("IM-005", "Images", "200 DPI scan", "12", "Stable", "Heavy page"),
    ("TB-001", "Tables", "Simple grid", "14", "Stable", "Header row"),
    ("TB-002", "Tables", "Zebra rows", "14", "Stable", "Alternating fill"),
    ("TB-003", "Tables", "Merged cells", "15", "Stable", "Colspan/rowspan"),
    ("TB-004", "Tables", "Long table", "16", "Stable", "Repeated header"),
    ("TB-005", "Tables", "Numeric columns", "17", "Stable", "Right aligned"),
    ("TB-006", "Tables", "Rich cells", "18", "Stable", "Nested content"),
    ("TB-007", "Tables", "Feature matrix", "19", "Stable", "Check marks"),
    ("NV-001", "Navigation", "Outline level 1", "21", "Stable", "Bookmark"),
    ("NV-002", "Navigation", "Outline level 5", "21", "Stable", "Nested bookmark"),
    ("NV-003", "Navigation", "Internal link", "22", "Stable", "GOTO"),
    ("NV-004", "Navigation", "External URL", "22", "Stable", "URI"),
    ("AN-001", "Annotations", "Highlight", "24", "Stable", "Markup"),
    ("AN-002", "Annotations", "Underline", "24", "Stable", "Markup"),
    ("AN-003", "Annotations", "Strikeout", "24", "Stable", "Markup"),
    ("AN-004", "Annotations", "Squiggly", "24", "Stable", "Markup"),
    ("AN-005", "Annotations", "Sticky note", "25", "Stable", "Popup"),
    ("AN-006", "Annotations", "Free text", "25", "Stable", "Callout"),
    ("AN-007", "Annotations", "Shapes", "26", "Stable", "Square/circle/line"),
    ("AN-008", "Annotations", "Ink", "26", "Stable", "Freehand"),
    ("AN-009", "Annotations", "Stamp", "26", "Stable", "Rubber stamp"),
    ("AN-010", "Annotations", "File attachment", "26", "Stable", "Paperclip"),
    ("FM-001", "Forms", "Text field", "28", "Stable", "Single line"),
    ("FM-002", "Forms", "Multiline field", "28", "Stable", "Comment box"),
    ("FM-003", "Forms", "Checkbox", "29", "Stable", "On/Off"),
    ("FM-004", "Forms", "Radio group", "29", "Stable", "Three options"),
    ("FM-005", "Forms", "Combo box", "30", "Stable", "Choice"),
    ("FM-006", "Forms", "List box", "30", "Stable", "Options"),
    ("AT-001", "Attachments", "CSV data", "32", "Stable", "Table source"),
    ("AT-002", "Attachments", "ODS workbook", "32", "Stable", "Calc"),
    ("AT-003", "Attachments", "JSON manifest", "32", "Stable", "Metadata"),
    ("AT-004", "Attachments", "Text notes", "32", "Stable", "Plain text"),
    ("RD-001", "Rendering", "Transparency", "34", "Stable", "Alpha blend"),
    ("RD-002", "Rendering", "Gradient", "34", "Stable", "Shading"),
    ("RD-003", "Rendering", "A3 page", "36", "Stable", "Mixed size"),
    ("RD-004", "Rendering", "Rotated page", "37", "Stable", "/Rotate 90"),
    ("RD-005", "Rendering", "Dense small type", "35", "Stable", "7.5 pt"),
]

FEATURE_CSV_ROWS = [
    ("Rendering", "Continuous scroll", "yes", "tiles"),
    ("Rendering", "Tiled zoom", "yes", "cache"),
    ("Rendering", "Rotation", "yes", "page"),
    ("Text", "Selection", "yes", "layer"),
    ("Text", "Search", "yes", "AND/OR"),
    ("Text", "Regex search", "yes", "pattern"),
    ("Reading", "Reflow", "yes", "columns"),
    ("Reading", "Text size", "yes", "scale"),
    ("Navigation", "Outline", "yes", "5 levels"),
    ("Navigation", "Thumbnails", "yes", "async"),
    ("Documents", "Attachments", "yes", "embedded"),
    ("Documents", "Forms", "yes", "widgets"),
]


def write_attachments() -> None:
    ATTACHMENTS.mkdir(parents=True, exist_ok=True)
    with (ATTACHMENTS / "table-data.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "category", "structure", "page", "state", "notes"])
        writer.writerows(TABLE_CSV_ROWS)

    with (ATTACHMENTS / "feature-matrix.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["area", "feature", "supported", "notes"])
        writer.writerows(FEATURE_CSV_ROWS)

    (ATTACHMENTS / "notes.txt").write_text(
        "PDFAR demo fixture - field notes\n"
        "================================\n\n"
        "This text file is embedded in demo.pdf so the attachments panel\n"
        "has more than one payload type to display. It intentionally uses\n"
        "plain ASCII plus a few Unicode characters: 25 °C, 120 %, ±3 mm.\n\n"
        "Check: Tools > Attachments (Ctrl+Shift+A), then the Attachments tab.\n",
        encoding="utf-8",
    )

    manifest = {
        "fixture": "PDFAR comprehensive demo",
        "version": 2,
        "generated": DATE_TEXT,
        "sections": 10,
        "table_styles": 7,
        "annotation_types": 10,
        "form_field_types": 6,
        "page_sizes": ["A4", "A3"],
        "special_pages": ["two-column", "rotated /Rotate 90", "200 dpi scan"],
    }
    (ATTACHMENTS / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    csv_path = ATTACHMENTS / "table-data.csv"
    ods_path = ATTACHMENTS / "demo-data.ods"
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice:
        try:
            subprocess.run(
                [soffice, "--headless", "--convert-to", "ods",
                 "--outdir", str(ATTACHMENTS), str(csv_path)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=90,
            )
            generated = ATTACHMENTS / "table-data.ods"
            if generated.exists():
                generated.replace(ods_path)
        except (subprocess.SubprocessError, OSError):
            pass


def generate_assets() -> None:
    GENERATED.mkdir(parents=True, exist_ok=True)
    rasterize_svgs()
    make_photo()
    make_alpha_image()
    make_scanned_page()
    write_attachments()


# ---------------------------------------------------------------------------
# Document template
# ---------------------------------------------------------------------------

class LabelTOC(TableOfContents):
    """Table of contents that also accepts string page labels such as "A-3".

    ReportLab formats page numbers with %d, which breaks for the appendix
    page labels; this override passes the label through verbatim.
    """

    def wrap(self, availWidth, availHeight):
        entries = self._lastEntries or [
            (0, "Placeholder for table of contents", "0", None)
        ]

        def drawTOCEntryEnd(canvas, kind, label):
            page, level, key = label.split(",", 2)
            level = int(level)
            style = self.getLevelStyle(level)
            dot = " . " if self.dotsMinLevel >= 0 and level >= self.dotsMinLevel else ""
            if page.isdigit() and self.formatter:
                page = self.formatter(int(page))
            drawPageNumbers(canvas, style, [(page, literal_eval(key))],
                            availWidth, availHeight, dot)

        self.canv.drawTOCEntryEnd = drawTOCEntryEnd
        table_data = []
        for level, text, page_num, key in entries:
            style = self.getLevelStyle(level)
            key_val = None
            if key:
                text = '<a href="#%s">%s</a>' % (key, text)
                key_val = repr(key).replace(",", "\\x2c").replace('"', "\\x2c")
            para = Paragraph(
                '%s<onDraw name="drawTOCEntryEnd" label="%s,%d,%s"/>'
                % (text, page_num, level, key_val),
                style,
            )
            if style.spaceBefore:
                table_data.append([Spacer(1, style.spaceBefore)])
            table_data.append([para])
        self._table = Table(table_data, colWidths=(availWidth,),
                            style=self.tableStyle)
        self.width, self.height = self._table.wrapOn(
            self.canv, availWidth, availHeight
        )
        return self.width, self.height


class DemoDoc(BaseDocTemplate):
    def __init__(self, filename: str):
        super().__init__(
            filename, pagesize=A4, leftMargin=0, rightMargin=0,
            topMargin=0, bottomMargin=0,
            title="PDFAR Comprehensive PDF Test Document",
            author="Washington Indacochea Delgado",
        )
        body = Frame(LEFT, BOTTOM, CONTENT_W, PAGE_H - BOTTOM - TOP,
                     id="body", leftPadding=0, rightPadding=0,
                     topPadding=0, bottomPadding=0)
        two_a = Frame(LEFT, BOTTOM, 232, PAGE_H - BOTTOM - TOP,
                      id="col-a", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        two_b = Frame(300, BOTTOM, 245, PAGE_H - BOTTOM - TOP,
                      id="col-b", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        large = Frame(LEFT, BOTTOM, A3_W - LEFT - RIGHT, A3_H - BOTTOM - TOP,
                      id="large", leftPadding=0, rightPadding=0,
                      topPadding=0, bottomPadding=0)
        cover = Frame(0, 0, PAGE_W, PAGE_H, id="cover", leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        blank = Frame(0, 0, PAGE_W, PAGE_H, id="blank", leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)

        self.addPageTemplates([
            PageTemplate(id="cover", frames=[cover], onPage=self.draw_cover),
            # Chrome is painted at page end so the running header can show a
            # section that starts on this very page.
            PageTemplate(id="body", frames=[body], onPageEnd=self.draw_chrome),
            PageTemplate(id="twocol", frames=[two_a, two_b],
                         onPageEnd=self.draw_chrome),
            PageTemplate(id="large", frames=[large], pagesize=A3,
                         onPageEnd=self.draw_chrome_large),
            PageTemplate(id="blank", frames=[blank], onPage=self.draw_blank),
        ])

        self.current_section = "Cover"
        self.appendix_start = None
        self.rotated_page = None
        self.toc = LabelTOC()
        self.toc.levelStyles = [S[f"TOC{i}"] for i in range(5)]
        self.toc.dotsMinLevel = 0
        self.toc.tableStyle = TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])

    def beforeDocument(self):
        global ROTATED_PAGE
        FORM_FIELDS.clear()
        HEADING_PAGES.clear()
        ROTATED_PAGE = None
        self.appendix_start = None
        self.rotated_page = None
        self.current_section = "Cover"

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Heading):
            return
        text = flowable.getPlainText()
        self.canv.bookmarkPage(flowable.key)
        self.canv.addOutlineEntry(text, flowable.key,
                                  level=flowable.level - 1,
                                  closed=flowable.level >= 4)
        HEADING_PAGES[text] = self.page
        if flowable.level == 1:
            self.current_section = text
        if not flowable.toc:
            return
        if flowable.level == 1 and text.startswith("10."):
            self.appendix_start = self.page
        if self.appendix_start:
            page_label = f"A-{self.page - self.appendix_start + 1}"
        else:
            page_label = str(self.page)
        self.notify("TOCEntry", (flowable.level - 1, text, page_label))

    # -- page decoration ---------------------------------------------------
    def draw_cover(self, canv, doc):
        canv.saveState()
        W, H = PAGE_W, PAGE_H
        canv.linearGradient(0, 0, W, H,
                            [HexColor("#0b1220"), HexColor("#1e3a8a"),
                             HexColor("#3730a3")], (0, 0.58, 1))
        canv.rect(0, 0, W, H, stroke=0, fill=1)

        canv.setFillAlpha(0.06)
        canv.setFillColor(colors.white)
        canv.circle(W - 80, H - 120, 220, stroke=0, fill=1)
        canv.circle(W - 40, H - 60, 120, stroke=0, fill=1)
        canv.setFillAlpha(0.05)
        canv.setFont("DejaVuSans-Bold", 430)
        canv.drawString(W - 330, 40, "P")
        canv.setFillAlpha(1)

        canv.setFillColor(HexColor("#93c5fd"))
        canv.setFont("DejaVuSans-Bold", 10)
        canv.drawString(LEFT, H - 92, "TEST DOCUMENT  ·  VERSION 2.0")

        canv.drawImage(str(GENERATED / "logo.png"), LEFT, H - 300, 150, 150,
                       mask="auto")

        canv.setFillColor(colors.white)
        canv.setFont("DejaVuSans-Bold", 62)
        canv.drawString(LEFT, H - 390, "PDFAR")
        canv.setFillColor(HexColor("#bfdbfe"))
        canv.setFont("DejaVuSans", 24)
        canv.drawString(LEFT, H - 428, "Comprehensive PDF test document")

        canv.setStrokeColor(HexColor("#818cf8"))
        canv.setLineWidth(1.2)
        canv.line(LEFT, H - 456, W - RIGHT, H - 456)

        canv.setFillColor(HexColor("#e2e8f0"))
        canv.setFont("DejaVuSerif", 12.2)
        lines = [
            "A single fixture for layout, navigation, rendering and interaction:",
            "images, seven table styles, a five-level table of contents, mixed",
            "page sizes, a rotated page, annotations, form fields and embedded",
            "attachments.",
        ]
        for i, line in enumerate(lines):
            canv.drawString(LEFT, H - 492 - i * 20, line)

        chips = ["Images", "Tables", "TOC H1-H5", "Annotations", "Forms",
                 "Attachments", "A3", "Rotate 90°"]
        x = LEFT
        y = H - 610
        canv.setFont("DejaVuSans-Bold", 9)
        for chip in chips:
            width = canv.stringWidth(chip, "DejaVuSans-Bold", 9) + 22
            canv.setFillColor(HexColor("#ffffff"))
            canv.setFillAlpha(0.10)
            canv.roundRect(x, y, width, 26, 13, stroke=0, fill=1)
            canv.setFillAlpha(1)
            canv.setFillColor(HexColor("#e0e7ff"))
            canv.drawCentredString(x + width / 2, y + 8.5, chip)
            x += width + 8
            if x > W - 120:
                x = LEFT
                y -= 34

        canv.setFillColor(HexColor("#94a3b8"))
        canv.setFont("DejaVuSans", 8.8)
        canv.drawString(LEFT, 96, f"Generated {DATE_TEXT}  ·  ReportLab + PyMuPDF  ·  GPL-3.0-or-later")
        canv.drawString(LEFT, 80, "github.com/wachin/PDF-Advanced-Reader")

        canv.bookmarkPage("cover")
        canv.addOutlineEntry("Cover", "cover", level=0, closed=False)
        canv.restoreState()

    def draw_chrome(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(HAIR)
        canv.setLineWidth(0.6)
        canv.line(LEFT, PAGE_H - 52, PAGE_W - RIGHT, PAGE_H - 52)
        canv.setFont("DejaVuSans", 7.8)
        canv.setFillColor(MUTED)
        canv.drawString(LEFT, PAGE_H - 43, "PDFAR · Comprehensive PDF Test Document")
        canv.drawRightString(PAGE_W - RIGHT, PAGE_H - 43, doc.current_section)
        canv.line(LEFT, 46, PAGE_W - RIGHT, 46)
        canv.drawString(LEFT, 31, f"Demo fixture · {DATE_TEXT}")
        canv.drawRightString(PAGE_W - RIGHT, 31, f"Page {doc.page}")
        canv.restoreState()

    def draw_chrome_large(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(HAIR)
        canv.setLineWidth(0.6)
        canv.line(LEFT, A3_H - 52, A3_W - RIGHT, A3_H - 52)
        canv.setFont("DejaVuSans", 7.8)
        canv.setFillColor(MUTED)
        canv.drawString(LEFT, A3_H - 43, "PDFAR · Comprehensive PDF Test Document")
        canv.drawRightString(A3_W - RIGHT, A3_H - 43, doc.current_section)
        canv.line(LEFT, 46, A3_W - RIGHT, 46)
        canv.drawString(LEFT, 31, f"Demo fixture · {DATE_TEXT}")
        canv.drawRightString(A3_W - RIGHT, 31, f"Page {doc.page}")
        canv.restoreState()

    def draw_blank(self, canv, doc):
        pass


# ---------------------------------------------------------------------------
# Story
# ---------------------------------------------------------------------------

def build_story(doc: DemoDoc) -> list:
    story: list = []
    story.append(CoverMarker())
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    # ---- Document information -------------------------------------------
    story.append(heading(1, "Document Information", key="sec_info"))
    story.append(P(
        "This file is a deliberately dense PDF fixture. It exists so every part "
        "of PDFAR can be exercised on one document: the continuous page layout, "
        "the tile renderer, text selection, search, the outline tree, the "
        "annotation layer, form widgets and the attachments panel. Nothing here "
        "is a real report; the content is synthetic, but the PDF structures are "
        "real and intentionally varied.",
        "Lead",
    ))
    meta_rows = [
        ["Property", "Value"],
        ["File name", "demo.pdf"],
        ["Title", "PDFAR — Comprehensive PDF Test Document"],
        ["Author", "Washington Indacochea Delgado"],
        ["Version", "2.0 (fixture revision 2)"],
        ["Generated", DATE_TEXT],
        ["Page sizes", "A4 portrait, one A3 spread, one /Rotate 90 page"],
        ["Structure", "5-level outline, printed TOC, internal and external links"],
        ["License", "GPL-3.0-or-later"],
    ]
    story.append(data_table(meta_rows, [120, CONTENT_W - 120]))
    story.append(spacer(8))

    story.append(heading(2, "What This Document Exercises"))
    feature_rows = [
        ["Area", "What to check", "Section"],
        ["Typography", "H1–H5, inline styles, lists, quotes, code, two columns",
         '<a href="#sec_typography" color="#1d4ed8">2</a>'],
        ["Images", "PNG with alpha, JPEG, grayscale, diagrams, 200 DPI scan",
         '<a href="#sec_images" color="#1d4ed8">3</a>'],
        ["Tables", "Grids, zebra rows, spans, long tables, rich and nested cells",
         '<a href="#sec_tables" color="#1d4ed8">4</a>'],
        ["Navigation", "Outline depth, TOC links, URI links, page labels",
         '<a href="#sec_navigation" color="#1d4ed8">5</a>'],
        ["Annotations", "Markup, notes, free text, shapes, ink, stamp, file",
         '<a href="#sec_annotations" color="#1d4ed8">6</a>'],
        ["Forms", "Text, multiline, check, radio, combo, list, button",
         '<a href="#sec_forms" color="#1d4ed8">7</a>'],
        ["Attachments", "CSV, ODS, JSON and TXT embedded files",
         '<a href="#sec_attachments" color="#1d4ed8">8</a>'],
        ["Rendering", "Transparency, gradients, A3, rotation, dense type",
         '<a href="#sec_rendering" color="#1d4ed8">9</a>'],
    ]
    story.append(data_table(feature_rows, [86, CONTENT_W - 156, 70]))

    story.append(heading(2, "How to Use This File"))
    story.append(numbered([
        "Scroll from the cover to the appendix and watch for layout jumps.",
        "Zoom with the toolbar, Ctrl+wheel and the 50–400% preset list.",
        "Open the outline sidebar and expand entries down to level 5.",
        "Search for <b>tile</b>, <b>annotation</b> or <b>reflow</b> to test hits.",
        "Use <b>Tools &gt; Attachments</b> (Ctrl+Shift+A) for embedded files.",
        "Tab through the form fields in section 7 and toggle the widgets.",
        "Select and copy text from the scanned page; it has a real text layer.",
    ]))

    # ---- Table of contents ----------------------------------------------
    story.append(PageBreak())
    story.append(Anchor("contents", outline_title="Contents", level=0, doc=doc))
    story.append(P("Table of Contents", "TOCTitle"))
    story.append(rule(INDIGO, 1.2, 2, 6))
    story.append(P(
        "The printed contents is generated from the same heading flowables that "
        "produce the PDF outline, so page numbers and hierarchy always agree. "
        "Level 1 is bold; levels 2–5 are progressively indented.",
        "Small",
    ))
    story.append(spacer(6))
    story.append(doc.toc)
    story.append(PageBreak())

    # ---- 1. Introduction -------------------------------------------------
    story.append(heading(1, "1. Introduction", key="sec_intro"))
    story.append(P(
        "PDFAR is a Linux-native reader built around PyMuPDF for parsing and "
        "rasterization and PyQt6 for the interface. Its design goal is simple: "
        "large documents should open quickly, scroll smoothly and remain "
        "searchable, selectable and navigable. This document is the counterpart "
        "to that goal — a PDF that is intentionally awkward in all the places a "
        "viewer is likely to be awkward too.",
        "Lead",
    ))
    story.append(heading(2, "1.1 Purpose"))
    story.append(P(
        "The fixture covers page geometry, text layout, raster and vector "
        "graphics, table structures, annotation dictionaries, AcroForm widgets, "
        "embedded files and a five-level outline. It also includes a small number "
        "of deliberate stress cases: a full-page 200 DPI scan, a transparent PNG, "
        "an A3 page inside an A4 document and a page whose orientation is encoded "
        "with a <b>/Rotate 90</b> entry rather than a landscape media box."
    ))
    story.append(heading(3, "1.1.1 Intended Audience"))
    story.append(P(
        "The document is useful to anyone testing a PDF viewer, but it is "
        "primarily aimed at PDFAR contributors. If a change touches rendering, "
        "navigation or the annotation layer, open this file before and after the "
        "change and compare the same pages. The section order is stable; the page "
        "numbers may move when content is added."
    ))
    story.append(heading(4, "1.1.1.1 Before You Begin"))
    story.append(P(
        "Use a build with the optional attachments panel available. The fixture "
        "assumes that the renderer honours the page tree, the outline and the "
        "AcroForm dictionary. A viewer that ignores annotations will still render "
        "the pages, but sections 6 and 7 will look empty."
    ))
    story.append(heading(5, "1.1.1.1.1 Conventions Used in This Document"))
    story.append(P(
        "Keyboard shortcuts appear as <b>Ctrl+F</b>. Literal values appear in "
        "<font face='DejaVuSansMono' size='8.8'>monospace</font>. Links are "
        "blue and clickable. Measurements use points (1 pt = 1/72 inch) and "
        "percentages of the page size. A superscript marker such as "
        "“tile<super>1</super>” refers to the notes at the end of this "
        "introduction."
    ))
    story.append(P(
        "<super>1</super> A tile is a rectangular render unit; PDFAR chooses a "
        "tile size large enough to amortize PDF interpretation but small enough "
        "to keep memory bounded.",
        "Small",
    ))
    story.append(rule())

    story.append(heading(2, "1.2 Feature Overview"))
    story.append(P(
        "The pages that follow are organised by feature area rather than by "
        "implementation layer. Each section ends with enough material to make the "
        "behaviour visible: for example, the table section contains a long table "
        "whose header repeats on the next page, and the annotation section places "
        "every supported markup annotation on a real text line."
    ))
    story.append(bullets([
        "<b>Layout:</b> continuous scrolling, fit width, fit page and additive zoom steps.",
        "<b>Text:</b> selection, search with AND/OR/phrase semantics and regex search.",
        "<b>Reading:</b> reflow mode with adjustable text size.",
        "<b>Navigation:</b> thumbnails, five-level outline and internal links.",
        "<b>Interaction:</b> annotations, forms and embedded attachments.",
    ]))
    story.append(heading(3, "1.2.1 Architecture"))
    story.extend(image(GENERATED / "architecture.png", 452,
                       "Figure 1.1 — Document loading and tiled rendering. "
                       "Visible pages are prioritised over prefetch work."))
    story.append(heading(4, "1.2.1.1 Rendering Pipeline"))
    story.append(P(
        "Rendering starts when the view asks for a page rectangle. The render "
        "pool looks up cached tiles first; on a miss it schedules a background "
        "job, and the view paints a low-resolution placeholder until the tile "
        "arrives. The pipeline is deliberately split into five stages so that a "
        "slow page never blocks the Qt event loop."
    ))
    story.extend(image(GENERATED / "pipeline.png", 452,
                       "Figure 1.2 — The five render stages. Stage 4 runs on "
                       "worker threads; the other stages are cheap."))
    story.append(heading(5, "1.2.1.1.1 Tile Lifecycle"))
    story.append(P(
        "Each tile moves through a small state machine. The table below is also "
        "a compact test of header styling, cell padding and zebra fills."
    ))
    tile_rows = [
        ["State", "Entered when", "Next state"],
        ["Requested", "The view needs the tile and the cache misses", "Rendering"],
        ["Rendering", "A worker starts interpreting the page", "Ready / Failed"],
        ["Ready", "The pixmap is available and uploaded", "Cached"],
        ["Cached", "The tile fits the memory budget", "Evicted"],
        ["Evicted", "The LRU budget is exceeded", "Requested"],
    ]
    story.append(data_table(tile_rows, [90, 265, CONTENT_W - 355]))

    # ---- 2. Text and typography -----------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "2. Text and Typography", key="sec_typography"))
    story.append(P(
        "Text is the hardest part of a PDF to render consistently: fonts may be "
        "subset or missing, glyphs may be positioned individually, and reading "
        "order may differ from drawing order. The following pages use embedded "
        "DejaVu fonts so that the same glyphs are available to the renderer and "
        "to text extraction.",
        "Lead",
    ))
    story.append(heading(2, "2.1 Paragraph Styles"))
    story.append(P(
        "This paragraph is justified and uses the serif body face at 10.1 pt. "
        "Inline styles include <b>bold</b>, <i>italic</i>, "
        "<b><i>bold italic</i></b>, <font color='#1d4ed8'>coloured text</font>, "
        "<font face='DejaVuSansMono' size='8.8'>monospace fragments</font>, "
        "H<sub>2</sub>O and x<super>2</super>. The line spacing is fixed so that "
        "the vertical rhythm is predictable when the text is reflowed."
    ))
    story.append(P(
        "A second paragraph checks hyphenation-free justification with a longer "
        "measure. Justified text on a 487 pt measure produces modest word spacing; "
        "if the renderer applies synthetic letter spacing, the difference is "
        "visible at 200% zoom. The fixture also contains a narrow two-column "
        "section later in this chapter, where the measure is roughly half as wide."
    ))
    story.append(P(
        "A third paragraph mixes inline code with prose: call "
        "<font face='DejaVuSansMono' size='8.8'>view.set_zoom(1.25, anchor=pos)</font> "
        "to keep the point under the cursor stable. The anchor is expressed in "
        "view coordinates, not page coordinates, which is the detail that makes "
        "wheel zoom feel natural rather than jumpy."
    ))
    story.append(heading(2, "2.2 Lists"))
    story.append(P("Bulleted lists use a compact bullet and a hanging indent:"))
    story.append(bullets([
        "Open a document and verify that the first page appears before the thumbnails finish.",
        "Press <b>PgDn</b> repeatedly and confirm that scrolling remains smooth across page boundaries.",
        "Toggle the sidebar and check that the document view keeps its scroll position.",
        "Rotate the page twice and confirm that text selection still follows the glyphs.",
    ]))
    story.append(P("Ordered lists are numbered explicitly; the fixture uses 1–4 to avoid ambiguity:"))
    story.append(numbered([
        "Load <font face='DejaVuSansMono' size='8.8'>demo.pdf</font> from the repository root.",
        "Open the outline and expand section 10 to level 5.",
        "Use <b>Ctrl+F</b> to search for <i>annotation</i>.",
        "Open <b>Tools &gt; Attachments</b> and inspect the five embedded files.",
    ]))
    story.append(heading(3, "2.2.1 Definition-Style Data"))
    term_rows = [
        ["Term", "Meaning in this fixture"],
        ["Tile", "Rectangular render unit cached independently of the page."],
        ["Anchor", "View point held fixed while zooming."],
        ["Reflow", "Single-column text mode that ignores the page geometry."],
        ["Widget", "An AcroForm field with a rectangle and a value."],
    ]
    story.append(data_table(term_rows, [90, CONTENT_W - 90]))

    story.append(heading(2, "2.3 Block Quotes and Code"))
    story.append(quote_box(
        "The goal is not to imitate a word processor. The goal is to make a "
        "structurally rich PDF feel as direct as a plain text file: scroll, "
        "select, search, annotate."
    ))
    story.append(spacer(8))
    story.append(code_block(
        "def on_viewport_changed(self, rect):\n"
        "    visible = self.tiles.intersecting(rect)\n"
        "    for tile in visible:\n"
        "        self.pool.request(tile, priority=HIGH)\n"
        "    self.pool.trim(self.cache.budget_bytes)\n"
    ))
    story.append(callout(
        "Note",
        "The code block is drawn as text, not as an image. It should be "
        "selectable, searchable and copyable — including the indentation.",
    ))

    story.append(heading(2, "2.4 Two-Column Layout"))
    story.append(NextPageTemplate("twocol"))
    story.append(PageBreak())
    story.append(P(
        "This page switches to a two-frame page template. Frames are filled in "
        "order, so the text flows down the left column and continues at the top "
        "of the right column. A viewer that reflows the page into one column "
        "should still preserve the reading order.",
        "BodySans",
    ))
    story.append(spacer(6))
    for text in [
        "The left column begins with a short introduction. The frame width is "
        "232 pt, which is narrow enough to expose excessive word spacing in "
        "justified text. The renderer should keep the text inside the frame and "
        "break lines at word boundaries.",
        "The second paragraph continues the test with a mix of long and short "
        "words. A wide word such as “interoperability” must not overflow the "
        "column edge; if it does, the overflow is a useful regression signal.",
        "The right column starts after the left frame is full. The two frames "
        "share the same baseline grid, so paragraphs should line up horizontally "
        "even though they are independent flowables.",
        "Finally, this sentence is intentionally long so that the last line of "
        "the right column ends near the frame boundary. Selection across the "
        "column break should follow reading order, not drawing order.",
    ]:
        story.append(P(text, "BodySans"))
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    story.append(heading(2, "2.5 Unicode and Special Characters"))
    story.append(P(
        "The body font is embedded as aTrueType subset. The samples below cover "
        "Latin accents, Greek, Cyrillic, currency, mathematical operators and "
        "arrows. Missing glyphs usually appear as boxes; the fixture makes that "
        "failure obvious."
    ))
    uni_rows = [
        ["Script", "Sample", "Notes"],
        ["Latin accents", "áéíóú ñ ç ü ø å œ", "Western European coverage"],
        ["Greek", "αβγδε ζηθικ λμνξο πράσινο", "Words plus individual letters"],
        ["Cyrillic", "Привет, мир! Документ PDF", "Russian sample"],
        ["Math", "∑ ∏ √ ∞ ≈ ≠ ≤ ≥ ± × ÷", "Symbols used in formulas"],
        ["Arrows", "← ↑ → ↓ ↔ ⇒ ⇐ ⇔", "Navigation notation"],
        ["Currency", "€ £ ¥ ₹ ₽ ₩ ¢", "Mixed currency symbols"],
    ]
    story.append(data_table(uni_rows, [88, 230, CONTENT_W - 318]))
    story.append(P(
        "Ligatures are intentionally avoided in the embedded subset: the text "
        "should round-trip through copy and paste as the original code points. "
        "The em dash — and the ellipsis … are present for punctuation tests."
    ))

    # ---- 3. Images and graphics -----------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "3. Images and Graphics", key="sec_images"))
    story.append(P(
        "The fixture mixes raster and vector content at several resolutions. "
        "The 200 DPI scan page is the heaviest object in the file; the diagrams "
        "are rasterised from SVG once and reused at display size.",
        "Lead",
    ))
    story.append(heading(2, "3.1 Raster Images"))
    story.append(P(
        "The application icon is a PNG with an alpha channel. The transparent "
        "corners must show the page background, not a black or white square."
    ))
    story.extend(image(GENERATED / "logo.png", 72,
                       "Figure 3.1 — Program icon rendered at 72 pt from a "
                       "512 px PNG with alpha."))
    story.append(heading(3, "3.1.1 Diagrams"))
    story.extend(image(GENERATED / "architecture.png", 452,
                       "Figure 3.2 — Architecture diagram rasterised at 1600 px "
                       "wide and placed at 452 pt."))
    story.extend(image(GENERATED / "pipeline.png", 452,
                       "Figure 3.3 — Render pipeline. The source is a vector SVG; "
                       "the PDF embeds a high-resolution PNG."))

    story.append(heading(2, "3.2 Colour and Transparency"))
    story.append(P(
        "JPEG and PNG exercise different decode paths. The photograph on the "
        "left is full colour; the image on the right is a grayscale JPEG with "
        "the same dimensions. Both are placed at 220 pt wide."
    ))
    photo_cell = [RLImage(str(GENERATED / "photo.jpg"), width=220, height=141),
                  P("Colour JPEG · 1400×900", "Caption")]
    gray_cell = [RLImage(str(GENERATED / "grayscale.jpg"), width=220, height=141),
                 P("Grayscale JPEG · DeviceGray", "Caption")]
    gallery = Table([[photo_cell, gray_cell]], colWidths=[240, 240])
    gallery.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(gallery)
    story.append(spacer(4))
    story.append(P(
        "The next image is an RGBA PNG placed over a coloured background. The "
        "semi-transparent shapes must blend with the band, and the text inside "
        "the image must remain crisp."
    ))
    story.append(P("", "Small"))
    band = Table([[RLImage(str(GENERATED / "alpha.png"), width=452, height=274)]],
                 colWidths=[CONTENT_W])
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#0f172a")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(band)
    story.append(P("Figure 3.4 — Transparent PNG over a dark band.", "Caption"))

    story.append(PageBreak())
    story.append(heading(2, "3.3 Feature Overview Diagram"))
    story.extend(image(GENERATED / "feature-grid.png", 452,
                       "Figure 3.5 — Six feature areas and the sections that "
                       "exercise them."))
    story.append(heading(2, "3.4 Zoom Levels"))
    story.extend(image(GENERATED / "zoom-levels.png", 430,
                       "Figure 3.6 — Additive zoom levels. Each step adds a fixed "
                       "percentage instead of multiplying the current scale."))
    story.append(heading(2, "3.5 High-Resolution Scan"))
    story.append(P(
        "The raster below is a synthetic 200 DPI scan: a single large JPEG with "
        "paper grain, a table, a stamp and a text-like page layout. The PDF also "
        "carries a real text layer on the page so search and selection can be "
        "tested against an image-heavy page."
    ))
    story.append(P(
        "The image is placed at 385 × 544 pt. At 100% zoom the viewer is "
        "downscaling a 1654 × 2338 JPEG, which is exactly the case where naïve "
        "renderers become blurry or slow. The caption is vector text separate "
        "from the image."
    ))
    story.append(RLImage(str(GENERATED / "scan-page.jpg"), width=385, height=544))
    story.append(P("Figure 3.7 — Synthetic scanned page, 200 DPI, embedded as JPEG.",
                   "Caption"))

    story.append(PageBreak())
    story.append(heading(2, "3.6 Image Formats in This File"))
    fmt_rows = [
        ["Object", "Format", "Colour space", "Alpha", "Purpose"],
        ["Icon", "PNG", "RGB", "Yes", "Transparent corners"],
        ["Architecture", "PNG", "RGB", "No", "Large diagram"],
        ["Pipeline", "PNG", "RGB", "No", "Wide diagram"],
        ["Photograph", "JPEG", "YCbCr", "No", "Full colour"],
        ["Grayscale", "JPEG", "Gray", "No", "DeviceGray path"],
        ["Overlay", "PNG", "RGBA", "Yes", "Alpha blending"],
        ["Scan", "JPEG", "YCbCr", "No", "Heavy page"],
    ]
    story.append(data_table(fmt_rows, [92, 72, 92, 52, CONTENT_W - 308]))

    # ---- 4. Tables -------------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "4. Tables", key="sec_tables"))
    story.append(P(
        "Tables are the densest test of a PDF layout engine: they combine grid "
        "lines, background fills, wrapped paragraphs, alignment, row spans and "
        "page breaks. The seven tables below are generated natively in the PDF "
        "so their text remains selectable and searchable. The same data is also "
        "embedded as a CSV and an ODS workbook in section 8.",
        "Lead",
    ))
    story.append(heading(2, "4.1 Simple Grid"))
    simple_rows = [
        ["Structure", "Where used", "Selectable", "Notes"],
        ["Paragraph", "Body text", "Yes", "Justified measure"],
        ["Heading", "Outline and TOC", "Yes", "Levels 1–5"],
        ["Table", "This section", "Yes", "Grid and fills"],
        ["Image", "Section 3", "No", "Raster content"],
        ["Annotation", "Section 6", "Partly", "Text in popups"],
    ]
    story.append(data_table(simple_rows, [100, 140, 80, CONTENT_W - 320]))

    story.append(heading(2, "4.2 Zebra Rows and Alignment"))
    zebra_rows = [
        ["Page", "Size", "Objects", "Render ms", "Cache", "State"],
        ["1", "A4", "12", "18", "2", "warm"],
        ["2", "A4", "9", "14", "2", "warm"],
        ["3", "A4", "15", "22", "3", "hot"],
        ["9", "A4", "7", "11", "1", "cold"],
        ["12", "A4", "1 image", "48", "6", "hot"],
        ["36", "A3", "38", "61", "8", "cold"],
        ["37", "A4 /Rotate", "6", "19", "2", "warm"],
    ]
    story.append(data_table(zebra_rows, [44, 56, 70, 76, 56, CONTENT_W - 302],
                            align="RIGHT"))

    story.append(heading(2, "4.3 Merged Cells"))
    merged_rows = [
        ["Region", "Quarter", "Visits", "Revenue", "Change"],
        ["North", "Q1", "1,240", "€ 48,200", "+4.2%"],
        ["", "Q2", "1,410", "€ 52,900", "+6.8%"],
        ["South", "Q1", "980", "€ 31,400", "−1.1%"],
        ["", "Q2", "1,105", "€ 36,750", "+2.9%"],
        ["Total", "", "4,735", "€ 169,250", "+3.6%"],
    ]
    merged_widths = [72, 62, 70, 92, CONTENT_W - 296]
    story.append(data_table(
        merged_rows, merged_widths, zebra=False,
        extra=[
            ("SPAN", (0, 0), (1, 0)),
            ("SPAN", (0, 1), (0, 2)),
            ("SPAN", (0, 3), (0, 4)),
            ("SPAN", (0, 5), (1, 5)),
            ("SPAN", (2, 5), (2, 5)),
            ("BACKGROUND", (0, 5), (-1, 5), HexColor("#e0e7ff")),
            ("FONTNAME", (0, 5), (-1, 5), "DejaVuSans-Bold"),
            ("LINEABOVE", (0, 5), (-1, 5), 1.1, INDIGO),
        ],
    ))

    story.append(heading(2, "4.4 Long Table with Repeated Header"))
    story.append(P(
        "The table below is built from the attached CSV and intentionally runs "
        "across a page break. The header row must repeat, and the grid must stay "
        "aligned after the break."
    ))
    with (ATTACHMENTS / "table-data.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    story.append(data_table(rows, [50, 72, 128, 38, 52, CONTENT_W - 340],
                            repeat=True, font_size=8.0))
    story.append(P("Source rows: 52. The CSV and ODS copies are attached to the PDF.",
                   "Caption"))

    story.append(PageBreak())
    story.append(heading(2, "4.5 Numeric Alignment and Units"))
    numeric_rows = [
        ["Metric", "Min", "Median", "Max", "Unit", "Sample"],
        ["Open time", "82", "141", "310", "ms", "GS262"],
        ["First paint", "44", "67", "128", "ms", "demo.pdf"],
        ["Tile render", "6.4", "12.1", "48.7", "ms", "A4 / 144 dpi"],
        ["Scroll frame", "7.9", "11.6", "19.2", "ms", "continuous"],
        ["Memory", "38", "112", "284", "MB", "tile cache"],
        ["Zoom anchor drift", "0.0", "0.4", "1.8", "pt", "wheel"],
    ]
    numeric_widths = [110, 52, 58, 52, 46, CONTENT_W - 318]
    story.append(data_table(numeric_rows, numeric_widths, align="RIGHT"))
    story.append(P(
        "Right-aligned numbers make the decimal points easier to compare. The "
        "unit column is deliberately narrow so that “MB” and “ms” stay on one line."
    ))

    story.append(heading(2, "4.6 Rich Cell Content"))
    rich_styles = [
        cell("<b>Checklist</b><br/>• line one<br/>• line two<br/>• line three", "CellSmall"),
        Preformatted("search(\n  query='tile',\n  regex=False,\n  case=False\n)", S["Code"]),
        data_table([["a", "b"], ["1", "2"], ["3", "4"]], [30, 30], zebra=False),
    ]
    rich = Table([rich_styles], colWidths=[160, 170, CONTENT_W - 330])
    rich.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.6, HAIR),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, HAIR),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (0, 0), HexColor("#eef2ff")),
        ("BACKGROUND", (1, 0), (1, 0), HexColor("#f8fafc")),
    ]))
    story.append(rich)
    story.append(P("Figure 4.1 — A table containing a list, a code block and a nested table.",
                   "Caption"))

    story.append(heading(2, "4.7 Comparison Matrix"))
    matrix_rows = [
        ["Feature", "PDFAR", "Viewer A", "Viewer B", "Notes"],
        ["Continuous scroll", "✓", "✓", "—", "required"],
        ["Five-level outline", "✓", "✓", "✓", "test section 5"],
        ["Annotation editing", "✓", "✓", "—", "section 6"],
        ["AcroForm widgets", "✓", "—", "✓", "section 7"],
        ["Embedded files", "✓", "—", "—", "section 8"],
        ["Rotated pages", "✓", "✓", "✓", "section 9"],
        ["Reflow mode", "✓", "—", "—", "PDFAR-only"],
    ]
    story.append(data_table(matrix_rows, [132, 54, 62, 62, CONTENT_W - 310],
                            align="CENTER"))

    # ---- 5. Navigation ---------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "5. Navigation and Links", key="sec_navigation"))
    story.append(P(
        "A PDF can carry three independent navigation structures: the outline "
        "tree, link annotations and page labels. This fixture uses all three, "
        "and the printed table of contents is generated from the same headings "
        "as the outline.",
        "Lead",
    ))
    story.append(heading(2, "5.1 Outline"))
    outline_rows = [
        ["Level", "Example in this file", "Expected behaviour"],
        ["1", "4. Tables", "Top-level chapter"],
        ["2", "4.3 Merged Cells", "Collapsible section"],
        ["3", "1.2.1 Architecture", "Third-level topic"],
        ["4", "1.2.1.1 Rendering Pipeline", "Fourth-level topic"],
        ["5", "1.2.1.1.1 Tile Lifecycle", "Fifth-level topic"],
    ]
    story.append(data_table(outline_rows, [40, 220, CONTENT_W - 260]))
    story.append(P(
        "The sidebar outline should expose all five levels. Expanding a parent "
        "must not move the reading position, and clicking an entry should jump "
        "to the heading, not merely to the page."
    ))

    story.append(heading(2, "5.2 Internal Links"))
    story.append(P(
        "Internal links are implemented as GOTO destinations. The links below "
        "were created by the layout engine, not added afterwards: "
        '<a href="#sec_tables" color="#1d4ed8">jump to Tables</a>, '
        '<a href="#sec_annotations" color="#1d4ed8">jump to Annotations</a>, '
        '<a href="#sec_rendering" color="#1d4ed8">jump to Rendering</a>. '
        'The '
        '<a href="#contents" color="#1d4ed8">back to contents</a> '
        "link returns to the printed table of contents."
    ))
    story.append(P(
        "The printed TOC is also clickable. Its entries are matched to the "
        "outline destinations during post-processing, so every line with a page "
        "number is a link target — including the appendix entries labelled A-1, "
        "A-2 and so on."
    ))
    story.append(heading(2, "5.3 External Links"))
    ext_rows = [
        ["Destination", "URL"],
        ["Project", '<a href="https://github.com/wachin/PDF-Advanced-Reader" color="#1d4ed8">github.com/wachin/PDF-Advanced-Reader</a>'],
        ["Author", '<a href="mailto:linuxfrontier@proton.me" color="#1d4ed8">linuxfrontier@proton.me</a>'],
        ["PyMuPDF", '<a href="https://pymupdf.readthedocs.io/" color="#1d4ed8">pymupdf.readthedocs.io</a>'],
        ["Qt for Python", '<a href="https://doc.qt.io/qtforpython-6/" color="#1d4ed8">doc.qt.io/qtforpython-6</a>'],
    ]
    story.append(data_table(ext_rows, [96, CONTENT_W - 96]))
    story.append(P(
        "External links should open in the desktop browser or mail client. The "
        "application must not try to render them as internal destinations."
    ))
    story.append(heading(2, "5.4 Page Labels"))
    story.append(P(
        "The appendix is labelled with an <b>A-</b> prefix instead of the plain "
        "physical page number. Viewers that display page labels should show "
        "<b>A-1</b> on the first appendix page, while the outline still points to "
        "the correct physical page. This is a deliberate mismatch between the "
        "printed footer and the PDF page-label tree — both are visible."
    ))
    story.append(callout(
        "Observation point",
        "If the status bar shows a page label, it should change from a decimal "
        "number to A-1 when the appendix begins. The footer also switches to A-n.",
    ))

    # ---- 6. Annotations --------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "6. Annotations", key="sec_annotations"))
    story.append(P(
        "Annotations are separate PDF objects layered above the page content. "
        "The markup annotations below target real text; the remaining objects "
        "are placed in reserved areas so their position is predictable. Open "
        "the annotation sidebar (if available) and inspect the contents, "
        "authors and modification dates.",
        "Lead",
    ))
    story.append(heading(2, "6.1 Text Markup"))
    story.append(P("This sentence is highlighted in the PDF annotation layer.", "BodySans"))
    story.append(P("This phrase carries an underline annotation.", "BodySans"))
    story.append(P("This clause has a strike-through annotation.", "BodySans"))
    story.append(P("This segment has a squiggly underline.", "BodySans"))
    story.append(P(
        "The four annotations were added after layout by searching for the exact "
        "sentences and attaching the markup to the resulting quads. They should "
        "move with the text when the page is zoomed or rotated."
    ))

    story.append(heading(2, "6.2 Notes and Comments"))
    story.append(CondPageBreak(150))
    story.append(P("<b>Sticky note anchor</b> — the note icon sits to the right of this line.",
                   "BodySans"))
    story.append(spacer(46))
    story.append(CondPageBreak(250))
    story.append(P("<b>Free-text anchor</b> — the callout box occupies the reserved area below.",
                   "BodySans"))
    story.append(spacer(112))

    story.append(heading(2, "6.3 Shapes and Freehand"))
    story.append(CondPageBreak(210))
    story.append(P("<b>Shape area anchor</b> — square, circle, line and arrow are drawn below.",
                   "BodySans"))
    story.append(spacer(150))
    story.append(CondPageBreak(190))
    story.append(P("<b>Ink area anchor</b> — a freehand stroke is drawn below.",
                   "BodySans"))
    story.append(spacer(130))
    story.append(CondPageBreak(120))
    story.append(P("<b>Stamp anchor</b> and <b>attachment-icon anchor</b>.",
                   "BodySans"))
    story.append(spacer(70))

    story.append(heading(2, "6.4 Annotation Inventory"))
    inv_rows = [
        ["Annotation", "Count", "Colour", "Purpose"],
        ["Highlight", "1", "Yellow", "Markup a sentence"],
        ["Underline", "1", "Blue", "Markup a phrase"],
        ["StrikeOut", "1", "Red", "Markup a clause"],
        ["Squiggly", "1", "Green", "Markup a segment"],
        ["Text (sticky note)", "1", "Amber", "Popup comment"],
        ["FreeText", "1", "Cream", "Visible callout"],
        ["Square / Circle", "2", "Blue / red", "Shape annotations"],
        ["Line / Arrow", "2", "Slate", "Directional marks"],
        ["Ink", "1", "Blue", "Freehand signature"],
        ["Stamp", "1", "Red", "Rubber stamp"],
        ["File attachment", "1", "Gray", "Embedded payload"],
    ]
    story.append(data_table(inv_rows, [132, 46, 92, CONTENT_W - 270]))

    story.append(heading(2, "6.5 Rendering Notes"))
    story.append(P(
        "Markup annotations store quads, so a correct renderer draws them along "
        "the text baseline rather than around a bounding box. Shape annotations "
        "store a rectangle and a border style; the line annotation adds line "
        "ending styles. The stamp and file icons use appearance streams that "
        "may be supplied by the viewer if the file does not embed one."
    ))
    story.append(bullets([
        "Markup annotations should not break text selection.",
        "Popup contents should be editable and persist after a round trip.",
        "Shape opacity is 0.35–0.55; overlapping shapes should show blending.",
        "The file annotation carries a small text payload independent of the document attachments.",
    ]))

    # ---- 7. Forms --------------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "7. Interactive Forms", key="sec_forms"))
    story.append(P(
        "AcroForm widgets are rendered by the viewer rather than baked into the "
        "page. The boxes below are intentionally left empty: the widget "
        "appearance streams provide the borders, check marks and selection "
        "dots. Use Tab to move between fields and verify that values survive a "
        "save or a form reset.",
        "Lead",
    ))
    story.append(heading(2, "7.1 Text Fields"))
    story.append(P("Single-line fields accept short values; the comment field accepts multiple lines.",
                   "Small"))
    story.append(FieldRow("Full name", "text", width=250, value="Ada Lovelace"))
    story.append(FieldRow("Email address", "text", width=250, value="ada@example.org"))
    story.append(FieldRow("Comments", "text", width=300, height=58,
                          value="Tiles render smoothly across page boundaries.",
                          multiline=True))

    story.append(heading(2, "7.2 Check Boxes and Radio Buttons"))
    story.append(P("Toggle at least one check box and select a different theme.",
                   "Small"))
    story.append(FieldRow("Enable tiled rendering", "check", name="tiled", selected=True))
    story.append(FieldRow("Show thumbnails", "check", name="thumbnails", selected=True))
    story.append(FieldRow("Remember window state", "check", name="remember_state"))
    story.append(spacer(4))
    story.append(FieldRow("Light", "radio", group="theme", name="theme_light"))
    story.append(FieldRow("Dark", "radio", group="theme", name="theme_dark", selected=True))
    story.append(FieldRow("System", "radio", group="theme", name="theme_system"))

    story.append(heading(2, "7.3 Choice Fields"))
    story.append(FieldRow("Render quality", "combo", width=250,
                          choices=["Draft (72 dpi)", "Normal (144 dpi)", "High (288 dpi)"],
                          value="Normal (144 dpi)"))
    story.append(FieldRow("Sections to include", "list", width=250, height=74,
                          choices=["Typography", "Images", "Tables", "Annotations",
                                   "Forms", "Attachments"],
                          value="Tables"))

    story.append(heading(2, "7.4 Buttons"))
    story.append(P("A push button has no persistent value; it should render its caption and stay interactive.",
                   "Small"))
    story.append(FieldRow("Reset form", "button", name="reset_button", caption="Reset form"))

    story.append(heading(2, "7.5 Field Inventory"))
    form_rows = [
        ["Field", "Type", "Value"],
        ["Full name", "Text", "Ada Lovelace"],
        ["Email address", "Text", "ada@example.org"],
        ["Comments", "Multiline text", "Tiles render smoothly…"],
        ["Enable tiled rendering", "Check box", "On"],
        ["Show thumbnails", "Check box", "On"],
        ["Remember window state", "Check box", "Off"],
        ["Theme", "Radio group", "Dark"],
        ["Render quality", "Combo box", "Normal (144 dpi)"],
        ["Sections to include", "List box", "Tables"],
        ["Reset form", "Push button", "—"],
    ]
    story.append(data_table(form_rows, [150, 120, CONTENT_W - 270]))

    # ---- 8. Attachments --------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "8. Attachments and Embedded Files", key="sec_attachments"))
    story.append(P(
        "Five payloads are embedded in the PDF file itself, not linked from the "
        "filesystem. They should appear in the attachments panel and remain "
        "available when the PDF is moved to another machine.",
        "Lead",
    ))
    story.append(heading(2, "8.1 Embedded Payloads"))
    att_rows = [
        ["File", "Format", "Purpose"],
        ["table-data.csv", "CSV", "Source rows for the long table in section 4.4"],
        ["demo-data.ods", "LibreOffice Calc", "Same data as a spreadsheet workbook"],
        ["feature-matrix.csv", "CSV", "Feature support matrix"],
        ["manifest.json", "JSON", "Build metadata and structure summary"],
        ["notes.txt", "Plain text", "Human-readable field notes"],
    ]
    story.append(data_table(att_rows, [110, 120, CONTENT_W - 230]))
    story.append(spacer(10))
    story.append(P(
        "Open <b>Tools &gt; Attachments</b> (Ctrl+Shift+A), or switch the sidebar "
        "to the Attachments tab, then select a file to preview or extract it."
    ))
    story.append(heading(2, "8.2 Why the Tables Are Native PDF"))
    story.append(P(
        "A spreadsheet is an excellent container for data, but it is a poor "
        "container for a table that must be read on a PDF page. If a Calc sheet "
        "were flattened into an image, the text would stop being selectable; if "
        "it were embedded as an attachment only, the table would not be visible "
        "while reading. The fixture therefore does both: every table is drawn as "
        "native PDF text, and the underlying rows are attached as CSV and ODS."
    ))
    story.append(P(
        "The ODS workbook was produced by LibreOffice from "
        "<font face='DejaVuSansMono' size='8.8'>table-data.csv</font> and then "
        "embedded unchanged. It is there to test the attachment panel, not to "
        "serve as the layout source for the PDF."
    ))
    story.append(callout(
        "Design note",
        "Native PDF tables preserve selection, search and copy/paste. Spreadsheet "
        "attachments preserve authoring and formulas. Keeping both representations "
        "makes the fixture useful for rendering tests and for attachment tests.",
    ))
    story.append(heading(2, "8.3 Attachments in the Reader"))
    story.append(numbered([
        "Open the Tools menu and choose Attachments, or press Ctrl+Shift+A.",
        "Switch the sidebar to the Attachments tab if it is not already visible.",
        "Select <font face='DejaVuSansMono' size='8.8'>demo-data.ods</font> and verify the size and description.",
        "Extract the file and confirm that it opens in LibreOffice Calc.",
        "Return to the document without losing the current page.",
    ]))

    # ---- 9. Rendering challenges ----------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "9. Rendering Challenges", key="sec_rendering"))
    story.append(P(
        "The final section pushes the renderer into less common territory: "
        "transparency, gradients, a page larger than A4, a rotated page and "
        "very small type. These objects are cheap to draw once and expensive to "
        "get wrong.",
        "Lead",
    ))
    story.append(heading(2, "9.1 Transparency and Gradients"))
    story.append(GradientPanel())
    story.append(P("Figure 9.2 — Alpha blends over a linear gradient, drawn as PDF vectors.",
                   "Caption"))
    story.append(RadarChart())
    story.append(P("Figure 9.3 — Radar chart with two translucent series and vector axes.",
                   "Caption"))

    story.append(heading(2, "9.2 Dense Text and Small Type"))
    small_style = ParagraphStyle(
        "Dense", parent=S["Body"], fontSize=7.5, leading=9.6, spaceAfter=3,
    )
    story.append(Paragraph(
        "This paragraph is set at 7.5 pt on a 9.6 pt leading. At 100% zoom it is "
        "still legible on a high-DPI display; at 50% zoom it becomes grey texture. "
        "When reflow is enabled the text size should increase without introducing "
        "horizontal scrolling. The paragraph deliberately repeats enough words to "
        "cross several lines so that hyphenation, justification and line-breaking "
        "are exercised at a small size.",
        small_style,
    ))
    story.append(Paragraph(
        "The next line contains an unbroken URL-like token: "
        "https://example.invalid/reports/2026/quarterly/summary-with-a-very-long-slug"
        "/download?format=pdf&amp;version=2 — it should wrap or clip predictably, "
        "not overlap the margin. The following code block has lines longer than "
        "the frame; the renderer must not expand the page to fit them.",
        small_style,
    ))
    story.append(code_block(
        "GET /api/v1/documents/8f2c1a/renders?page=36&zoom=1.25&dpi=144 HTTP/1.1\n"
        "Accept: image/png\n"
        "X-PDFAR-Tile: 3,2;cache=warm;priority=visible;generation=17\n"
    ))
    story.append(P(
        "A final paragraph at normal size confirms that the view returns to the "
        "regular leading after the dense block."
    ))

    story.append(NextPageTemplate("large"))
    story.append(PageBreak())
    story.append(heading(2, "9.3 Large-Format Page (A3)"))
    story.append(P(
        "This page is A3 portrait (842 × 1191 pt), inserted between A4 pages. "
        "Fit-page, fit-width and the zoom presets must all use the current page "
        "geometry rather than a document-wide default. The table below is 734 pt "
        "wide and would not fit on the A4 pages.",
        "BodySans",
    ))
    a3_rows = [
        ["Page", "Width (pt)", "Height (pt)", "Aspect", "Content"],
        ["A4", "595", "842", "0.707", "Chapters 1–9"],
        ["A3", "842", "1191", "0.707", "This spread"],
        ["Rotate", "595 / 842", "842 / 595", "swapped", "Exhibit"],
    ]
    story.append(data_table(a3_rows, [90, 110, 110, 90, CONTENT_W - 400],
                            font_size=9.2))
    story.append(spacer(10))
    story.append(P(
        "The wide measure is also a typography test: a 734 pt line is almost "
        "twice the normal measure, so justification has more freedom and the "
        "eye needs a generous leading. The page should scale cleanly in the "
        "thumbnail panel without forcing the sidebar to resize.",
        "BodySans",
    ))
    story.extend(image(GENERATED / "architecture.png", 700,
                       "Figure 9.1 — The same diagram at 700 pt on the wide "
                       "measure. Scaling happens on the current page, not on a "
                       "document-wide default."))
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    story.append(heading(2, "9.4 Rotated Page"))
    story.append(P(
        "The next page is stored as a portrait page with a <b>/Rotate 90</b> "
        "entry. Its media box is 595 × 842 pt, but the viewer displays it as "
        "842 × 595 pt landscape. A correct renderer applies the rotation when "
        "painting, selecting and searching; a naïve one shows the content "
        "sideways or reports the wrong page size."
    ))
    story.append(P(
        "The exhibit was drawn after layout so its text can be positioned in "
        "display coordinates. Scroll past this page and then navigate back to "
        "it: the rotation must survive caching and thumbnail generation."
    ))
    rotation_rows = [
        ["Element", "Value"],
        ["Media box", "595 × 842 pt (portrait)"],
        ["Rotate entry", "/Rotate 90"],
        ["Displayed size", "842 × 595 pt (landscape)"],
        ["Text layer", "Rotates with the page and stays selectable"],
        ["Thumbnail", "Must follow the displayed orientation"],
    ]
    story.append(data_table(rotation_rows, [130, CONTENT_W - 130]))
    story.append(bullets([
        "The page picker and thumbnail panel must show a landscape sheet.",
        "Search hits must be drawn on the visible glyphs, not the raw media box.",
        "Save and reopen must preserve the /Rotate entry and the page label.",
    ]))
    story.append(NextPageTemplate("blank"))
    story.append(PageBreak())
    story.append(BlankExhibit(doc))
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    story.append(heading(2, "9.5 Mixed Content"))
    story.append(P(
        "After the rotated exhibit the document returns to A4. The remaining "
        "pages mix text, a small table and an image to check that the view does "
        "not retain the previous page's geometry."
    ))
    mixed_rows = [
        ["Element", "Expected result"],
        ["Text", "Normal reading measure"],
        ["Table", "Grid aligned to the content width"],
        ["Image", "Centered with a caption"],
    ]
    story.append(data_table(mixed_rows, [120, CONTENT_W - 120]))
    story.append(spacer(6))
    story.extend(image(GENERATED / "zoom-levels.png", 330,
                       "Figure 9.4 — A smaller placement of the zoom diagram "
                       "to test scaling after a rotated page."))

    # ---- 10. Appendix ----------------------------------------------------
    story.append(PageBreak())
    story.append(heading(1, "10. Appendix", key="sec_appendix"))
    story.append(P(
        "The appendix uses page labels with the prefix A-. The printed footer "
        "switches to A-n as well, while the outline continues to reference the "
        "physical page. This is the last group of headings, including the "
        "deepest level in the document.",
        "Lead",
    ))
    story.append(heading(2, "10.1 Testing Checklist"))
    story.append(heading(3, "10.1.1 Rendering"))
    story.append(heading(4, "10.1.1.1 Scrolling"))
    story.append(heading(5, "10.1.1.1.1 Tile Boundaries"))
    checklist_rows = [
        ["Check", "Pass condition"],
        ["No white flashes while scrolling", "Placeholders fade into tiles"],
        ["Tile seams invisible", "Edges match at 100% and 200%"],
        ["Page boundaries clean", "No clipping or overlap"],
        ["Rotation applied", "Exhibit reads upright in landscape"],
        ["A3 page scaled", "Fit page shows the full sheet"],
    ]
    story.append(data_table(checklist_rows, [220, CONTENT_W - 220]))
    story.append(heading(3, "10.1.2 Interaction"))
    interaction_rows = [
        ["Check", "Pass condition"],
        ["Text selection", "Selection follows glyphs after rotation"],
        ["Search", "Hits in body, captions and scan page"],
        ["Outline", "Five levels expand independently"],
        ["Annotations", "Popup contents and colours persist"],
        ["Forms", "Values survive tabbing and reset"],
        ["Attachments", "All five payloads listed and extractable"],
    ]
    story.append(data_table(interaction_rows, [220, CONTENT_W - 220]))

    story.append(heading(2, "10.2 Glossary"))
    glossary_rows = [
        ["Term", "Definition"],
        ["AcroForm", "PDF dictionary that describes interactive form fields."],
        ["CID font", "Font whose glyphs are addressed by character IDs."],
        ["GOTO link", "Link annotation that moves to a destination in the same file."],
        ["Media box", "Physical page boundary before rotation is applied."],
        ["OCG", "Optional content group, also known as a PDF layer."],
        ["Quad", "Four-point polygon used by text markup annotations."],
        ["Tile", "Rectangular render unit stored in the cache."],
        ["URI action", "Link action that opens an external URL."],
    ]
    story.append(data_table(glossary_rows, [92, CONTENT_W - 92]))

    story.append(heading(2, "10.3 Revision History"))
    rev_rows = [
        ["Version", "Date", "Change"],
        ["1.0", "2026-10-01", "Initial six-page smoke test"],
        ["2.0", "2026-10-23", "Full fixture: images, tables, annotations, forms, attachments"],
    ]
    story.append(data_table(rev_rows, [62, 92, CONTENT_W - 154]))

    story.append(heading(2, "10.4 Colophon"))
    story.append(P(
        "This document was generated by "
        "<font face='DejaVuSansMono' size='8.8'>tools/demo_pdf/make_demo_pdf.py</font> "
        "using ReportLab for layout and PyMuPDF for post-processing. The source "
        "SVG assets live next to the script and are rasterised with cairosvg "
        "(or rsvg-convert). The attached workbook was produced with LibreOffice "
        "Calc in headless mode."
    ))
    story.append(P(
        "Fonts: DejaVu Sans, DejaVu Serif and DejaVu Sans Mono. Colours follow "
        "the PDFAR identity: indigo and blue with a coral accent. The fixture is "
        "released under GPL-3.0-or-later, like the reader itself."
    ))
    story.append(rule())
    story.append(P("End of document · demo.pdf · generated for PDF-Advanced-Reader",
                   "Caption"))
    return story


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def find_text(doc, phrase: str):
    for page in doc:
        rects = page.search_for(phrase)
        if rects:
            return page, rects
    return None, []


def add_markup(doc, phrase, kind, color, content):
    page, rects = find_text(doc, phrase)
    if not page:
        print(f"  ! markup anchor not found: {phrase!r}")
        return
    if kind == "highlight":
        annot = page.add_highlight_annot(rects)
    elif kind == "underline":
        annot = page.add_underline_annot(rects)
    elif kind == "strikeout":
        annot = page.add_strikeout_annot(rects)
    else:
        annot = page.add_squiggly_annot(rects)
    annot.set_colors(stroke=color)
    if kind == "highlight":
        annot.set_opacity(0.5)
    annot.set_info(title="Reviewer", subject="Markup",
                   content=content)
    annot.update()


def add_annotations(doc) -> None:
    add_markup(doc, "This sentence is highlighted in the PDF annotation layer.",
               "highlight", (1.0, 0.86, 0.2),
               "Highlighted to verify quad-based text markup.")
    add_markup(doc, "This phrase carries an underline annotation.",
               "underline", (0.12, 0.45, 0.9),
               "Underline annotation with a blue stroke.")
    add_markup(doc, "This clause has a strike-through annotation.",
               "strikeout", (0.86, 0.15, 0.15),
               "Strikeout annotation on a real text line.")
    add_markup(doc, "This segment has a squiggly underline.",
               "squiggly", (0.09, 0.6, 0.3),
               "Squiggly mark used to flag uncertain text.")

    page, rects = find_text(doc, "Sticky note anchor")
    if page:
        anchor = rects[0]
        annot = page.add_text_annot(
            pymupdf.Point(PAGE_W - RIGHT - 10, anchor.y0 + 4),
            "Sticky note added by the fixture generator.",
            icon="Comment",
        )
        annot.set_info(title="Fixture reviewer", subject="Note",
                       content="This note has a popup. It tests icon placement, "
                               "author and modification metadata.")
        annot.update()

    page, rects = find_text(doc, "Free-text anchor")
    if page:
        anchor = rects[0]
        rect = pymupdf.Rect(anchor.x0, anchor.y1 + 10, anchor.x0 + 360, anchor.y1 + 98)
        annot = page.add_freetext_annot(
            rect,
            "Free-text annotation: visible callout with a border and a fill colour.",
            fontsize=8.5, text_color=(0.11, 0.16, 0.23),
            fill_color=(1.0, 0.97, 0.86), border_color=(0.72, 0.53, 0.04),
            border_width=1, align=0, richtext=True,
        )
        annot.set_info(title="Fixture reviewer", subject="Callout",
                       content="FreeText annotations embed their text in the page.")
        annot.update()

    page, rects = find_text(doc, "Shape area anchor")
    if page:
        anchor = rects[0]
        y0 = anchor.y1 + 10
        square = pymupdf.Rect(anchor.x0 + 4, y0, anchor.x0 + 84, y0 + 66)
        circle = pymupdf.Rect(anchor.x0 + 104, y0, anchor.x0 + 174, y0 + 66)
        a = page.add_rect_annot(square)
        a.set_colors(stroke=(0.12, 0.45, 0.9))
        a.set_border(width=2, dashes=[4, 2])
        a.set_opacity(0.7)
        a.set_info(title="Fixture reviewer", subject="Shape",
                   content="Dashed square annotation.")
        a.update()
        a = page.add_circle_annot(circle)
        a.set_colors(stroke=(0.86, 0.15, 0.15), fill=(0.99, 0.88, 0.88))
        a.set_border(width=1.5)
        a.set_opacity(0.65)
        a.set_info(title="Fixture reviewer", subject="Shape",
                   content="Circle annotation with a light fill.")
        a.update()
        line = page.add_line_annot(
            pymupdf.Point(anchor.x0 + 200, y0 + 16),
            pymupdf.Point(anchor.x0 + 380, y0 + 54),
        )
        line.set_colors(stroke=(0.2, 0.25, 0.35))
        line.set_border(width=1.6)
        line.set_info(title="Fixture reviewer", subject="Line",
                      content="Plain line annotation.")
        line.update()
        arrow = page.add_line_annot(
            pymupdf.Point(anchor.x0 + 200, y0 + 62),
            pymupdf.Point(anchor.x0 + 380, y0 + 26),
        )
        arrow.set_colors(stroke=(0.2, 0.25, 0.35))
        arrow.set_border(width=1.6)
        arrow.set_line_ends(0, 5)
        arrow.set_info(title="Fixture reviewer", subject="Arrow",
                       content="Line annotation with a closed arrow head.")
        arrow.update()

    page, rects = find_text(doc, "Ink area anchor")
    if page:
        anchor = rects[0]
        base = anchor.y1 + 8
        points = []
        for i in range(48):
            x = anchor.x0 + 6 + i * 6.4
            y = base + 34 + 26 * math.sin(i / 4.2) * math.exp(-i / 55)
            points.append((x, y))
        ink = page.add_ink_annot([points])
        ink.set_colors(stroke=(0.12, 0.35, 0.85))
        ink.set_border(width=2.2)
        ink.set_opacity(0.9)
        ink.set_info(title="Fixture reviewer", subject="Ink",
                     content="Freehand stroke stored as an ink list.")
        ink.update()

    page, rects = find_text(doc, "Stamp anchor")
    if page:
        anchor = rects[0]
        rect = pymupdf.Rect(anchor.x0 + 230, anchor.y1 + 8,
                            anchor.x0 + 400, anchor.y1 + 60)
        stamp = page.add_stamp_annot(rect, stamp=3)
        stamp.set_info(title="Fixture reviewer", subject="Stamp",
                       content="Rubber stamp annotation.")
        stamp.update()

    page, rects = find_text(doc, "attachment-icon anchor")
    if page:
        anchor = rects[0]
        payload = (b"PDFAR annotation attachment\n"
                   b"Added by tools/demo_pdf/make_demo_pdf.py\n")
        annot = page.add_file_annot(
            pymupdf.Point(anchor.x1 + 9, anchor.y0 + 4),
            payload, filename="annotation-note.txt",
            ufilename="annotation-note.txt",
            desc="File annotation payload",
            icon="Paperclip",
        )
        annot.set_info(title="Fixture reviewer", subject="Attachment",
                       content="Paperclip annotation with an embedded text file.")
        annot.update()


def _widget_base(kind, rect, name):
    w = pymupdf.Widget()
    w.field_type = kind
    w.field_name = name
    w.rect = rect
    w.border_color = (0.35, 0.42, 0.55)
    w.border_width = 0.8
    w.fill_color = (1, 1, 1)
    w.text_color = (0.1, 0.14, 0.2)
    w.text_fontsize = 9
    return w


def add_form_fields(doc) -> None:
    radio_groups: dict[str, list] = {}
    for label, spec in FORM_FIELDS.items():
        page_no = spec["page"]
        if not page_no or page_no > doc.page_count:
            continue
        page = doc[page_no - 1]
        rect = spec["rect"]
        kind = spec["kind"]
        try:
            if kind == "text":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_TEXT, rect, spec["name"])
                w.field_value = spec["value"]
                if spec["multiline"]:
                    w.field_flags = pymupdf.PDF_TX_FIELD_IS_MULTILINE
                page.add_widget(w)
            elif kind == "check":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_CHECKBOX, rect, spec["name"])
                w.border_width = 0
                w.fill_color = None
                w.field_value = bool(spec["selected"])
                page.add_widget(w)
            elif kind == "radio":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON, rect, spec["name"])
                w.border_width = 0
                w.fill_color = None
                w.field_value = False
                annot = page.add_widget(w)
                radio_groups.setdefault(spec["group"], []).append((annot, spec))
            elif kind == "combo":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_COMBOBOX, rect, spec["name"])
                w.choice_values = spec["choices"]
                w.field_value = spec["value"]
                page.add_widget(w)
            elif kind == "list":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_LISTBOX, rect, spec["name"])
                w.choice_values = spec["choices"]
                w.field_value = spec["value"]
                page.add_widget(w)
            elif kind == "button":
                w = _widget_base(pymupdf.PDF_WIDGET_TYPE_BUTTON, rect, spec["name"])
                w.button_caption = spec["caption"] or label
                w.fill_color = (0.93, 0.95, 1)
                page.add_widget(w)
        except Exception as exc:  # keep the fixture buildable if a widget is rejected
            print(f"  ! form field {label!r} skipped: {exc}")

    for group, entries in radio_groups.items():
        parent = doc.get_new_xref()
        kids = " ".join(f"{annot.xref} 0 R" for annot, _ in entries)
        selected = next((s for _, s in entries if s["selected"]), None)
        state = "/Yes" if selected else "/Off"
        doc.update_object(
            parent,
            f"<< /FT /Btn /Ff 49152 /T ({group}) /V {state} /Kids [{kids}] >>",
        )
        for annot, spec in entries:
            doc.xref_set_key(annot.xref, "Parent", f"{parent} 0 R")
            doc.xref_set_key(annot.xref, "T", "null")
            doc.xref_set_key(annot.xref, "AS", "/Yes" if spec["selected"] else "/Off")


def add_toc_links(doc) -> None:
    toc_page = None
    for i, page in enumerate(doc):
        if page.search_for("The printed contents is generated"):
            toc_page = i
            break
    if toc_page is None:
        return
    outline = doc.get_toc()
    intro_page = None
    for level, title, pno in outline:
        if title.startswith("1. Introduction"):
            intro_page = pno - 1
            break
    if intro_page is None:
        return
    toc_pages = list(range(toc_page, max(toc_page, intro_page)))
    targets = {}
    for level, title, pno in outline:
        if pno - 1 in toc_pages:
            continue
        if title in ("Cover", "Contents"):
            continue
        targets.setdefault(title, pno - 1)
    added = 0
    for title, pno in targets.items():
        for pi in toc_pages:
            page = doc[pi]
            rects = page.search_for(title)
            if not rects:
                continue
            r = rects[0]
            link_rect = pymupdf.Rect(LEFT, r.y0 - 1.5,
                                     PAGE_W - RIGHT, r.y1 + 1.5)
            page.insert_link({
                "kind": pymupdf.LINK_GOTO,
                "from": link_rect,
                "page": pno,
                "to": pymupdf.Point(0, PAGE_H - 40),
            })
            added += 1
            break

    for phrase in ("back to contents", "Back to table of contents"):
        page, rects = find_text(doc, phrase)
        if page and rects:
            r = rects[0]
            page.insert_link({
                "kind": pymupdf.LINK_GOTO,
                "from": pymupdf.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1),
                "page": toc_page,
                "to": pymupdf.Point(0, PAGE_H - 60),
            })
    print(f"  TOC links: {added} entries over {len(toc_pages)} page(s)")


def draw_rotated_exhibit(doc) -> None:
    index = ROTATED_PAGE
    if not index:
        return
    page = doc[index - 1]
    page.set_rotation(90)
    W, H = PAGE_W, PAGE_H  # unrotated media box

    def pos(x, y):
        return pymupdf.Point(W - y, H - x)

    def draw_text(x, y, text, size=12, font="hebo", color=(1, 1, 1), align=0):
        p = pos(x, y)
        page.insert_text(p, text, fontsize=size, fontname=font,
                         color=color, rotate=90)

    def draw_box(x0, y0, x1, y1, fill, stroke=None):
        ux0, uy0 = W - y1, H - x1
        ux1, uy1 = W - y0, H - x0
        page.draw_rect(pymupdf.Rect(ux0, uy0, ux1, uy1),
                       color=stroke, fill=fill, width=1)

    draw_box(0, 0, PAGE_H, PAGE_W, (0.06, 0.11, 0.24))
    draw_box(0, 0, PAGE_H, 26, (0.26, 0.22, 0.66))
    draw_box(0, PAGE_W - 26, PAGE_H, PAGE_W, (0.26, 0.22, 0.66))
    draw_box(34, 34, PAGE_H - 34, PAGE_W - 34, None, (0.45, 0.55, 0.85))

    draw_text(64, PAGE_W - 74, "9.4  ROTATED EXHIBIT", 30)
    draw_text(64, PAGE_W - 108,
              "Media box 595 × 842 pt  ·  /Rotate 90  ·  displayed 842 × 595 pt",
              10, "helv", (0.72, 0.8, 0.98))
    draw_box(64, PAGE_W - 126, PAGE_H - 64, PAGE_W - 122, (0.35, 0.3, 0.75))

    columns = [
        ("Why this page exists",
         ["A page can be rotated by a PDF entry instead of by a landscape media "
          "box. The viewer must rotate painting, hit-testing, selection and "
          "thumbnails together.",
          "If only the page pixmap is rotated, the text layer will still point "
          "to the original coordinates and selection will drift."]),
        ("What to check",
         ["The page reads upright when opened.",
          "The displayed size is 842 × 595 pt.",
          "Text selection follows the visible glyphs.",
          "The thumbnail matches the displayed orientation.",
          "Going forward and back preserves the rotation."]),
        ("Implementation note",
         ["The exhibit is drawn after the layout pass with display coordinates "
          "mapped into the unrotated page. The rotation is then attached to the "
          "page dictionary.",
          "This is intentionally the last unusual page before the appendix so "
          "that page labels and the outline remain stable."]),
    ]
    col_w = 226
    for i, (title, paragraphs) in enumerate(columns):
        x = 64 + i * (col_w + 18)
        draw_text(x, PAGE_W - 156, title, 13, "hebo", (0.75, 0.86, 1))
        y = PAGE_W - 176
        for para in paragraphs:
            # Simple manual wrap in display units.
            words = para.split()
            line = ""
            for word in words:
                probe = (line + " " + word).strip()
                if len(probe) <= 42:
                    line = probe
                else:
                    draw_text(x, y, line, 8.5, "helv", (0.88, 0.92, 1))
                    y -= 12
                    line = word
            if line:
                draw_text(x, y, line, 8.5, "helv", (0.88, 0.92, 1))
                y -= 16

    draw_text(64, 60, "PDFAR · comprehensive demo fixture · generated page",
              8, "helv", (0.6, 0.68, 0.85))


def add_attachments(doc) -> None:
    payloads = [
        ("table-data.csv", "Source rows for the long table"),
        ("feature-matrix.csv", "Feature support matrix"),
        ("demo-data.ods", "LibreOffice Calc workbook"),
        ("manifest.json", "Build manifest"),
        ("notes.txt", "Plain-text field notes"),
    ]
    for name, desc in payloads:
        path = ATTACHMENTS / name
        if not path.exists():
            continue
        try:
            doc.embfile_add(
                name, path.read_bytes(), filename=name,
                ufilename=name, desc=desc,
            )
        except Exception as exc:
            print(f"  ! attachment {name} skipped: {exc}")


def add_page_labels(doc) -> None:
    appendix = None
    for level, title, pno in doc.get_toc():
        if title.startswith("10. Appendix"):
            appendix = pno - 1
            break
    if appendix is None:
        return
    labels = [
        {"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1},
        {"startpage": appendix, "prefix": "A-", "style": "D", "firstpagenum": 1},
    ]
    try:
        doc.set_page_labels(labels)
    except Exception as exc:
        print(f"  ! page labels skipped: {exc}")
        return
    for i in range(appendix, doc.page_count):
        page = doc[i]
        cover = pymupdf.Rect(PAGE_W - 170, PAGE_H - 44,
                             PAGE_W - RIGHT + 2, PAGE_H - 20)
        page.draw_rect(cover, color=None, fill=(1, 1, 1))
        label = f"Page A-{i - appendix + 1}"
        page.insert_textbox(
            pymupdf.Rect(PAGE_W - 190, PAGE_H - 41,
                         PAGE_W - RIGHT, PAGE_H - 23),
            label, fontsize=7.8, fontname="helv",
            color=(0.392, 0.443, 0.533), align=2,
        )


def add_scan_text_layer(doc) -> None:
    """Place an invisible OCR-style text layer over the scanned page."""
    data_path = GENERATED / "scan-text.json"
    if not data_path.exists():
        return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    for page in doc:
        for img in page.get_images(full=True):
            if (img[2], img[3]) != (data["width"], data["height"]):
                continue
            rects = page.get_image_rects(img[0])
            if not rects:
                continue
            rect = rects[0]
            sx = rect.width / data["width"]
            sy = rect.height / data["height"]
            for line in data["lines"]:
                x = rect.x0 + line["x"] * sx
                y = rect.y0 + (line["y"] + line["size"] * 0.8) * sy
                page.insert_text(
                    (x, y), line["text"],
                    fontsize=line["size"] * sy,
                    fontname={"bold": "hebo", "mono": "cour"}.get(
                        line["font"], "helv"),
                    color=(0, 0, 0), render_mode=3,
                )
            print(f"  OCR layer: {len(data['lines'])} lines on page "
                  f"{page.number + 1}")
            return


def postprocess(path: Path, out_path: Path) -> None:
    doc = pymupdf.open(path)
    add_scan_text_layer(doc)
    add_annotations(doc)
    add_form_fields(doc)
    draw_rotated_exhibit(doc)
    add_attachments(doc)
    add_page_labels(doc)
    add_toc_links(doc)

    manifest = {
        "fixture": "PDFAR comprehensive demo",
        "version": 2,
        "generated": DATE_TEXT,
        "pages": doc.page_count,
        "outline_depth": max((lvl for lvl, _, _ in doc.get_toc()), default=0),
        "annotations": sum(1 for p in doc for a in p.annots()),
        "widgets": sum(len(list(p.widgets())) for p in doc),
        "attachments": len(doc.embfile_names()),
    }
    (ATTACHMENTS / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    doc.set_metadata({
        "title": "PDFAR — Comprehensive PDF Test Document",
        "author": "Washington Indacochea Delgado",
        "subject": "Synthetic fixture for PDF rendering, navigation and interaction",
        "keywords": "PDF, demo, tables, images, annotations, forms, attachments, outline",
        "creator": "tools/demo_pdf/make_demo_pdf.py",
        "producer": "ReportLab + PyMuPDF",
        "creationDate": BUILD_STAMP,
        "modDate": BUILD_STAMP,
    })
    tmp = out_path.with_suffix(".tmp.pdf")
    doc.save(tmp, garbage=4, deflate=True, clean=True)
    doc.close()
    os.replace(tmp, out_path)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def summarize(path: Path) -> None:
    doc = pymupdf.open(path)
    toc = doc.get_toc()
    depth = max((lvl for lvl, _, _ in toc), default=0)
    annots = sum(1 for page in doc for _ in page.annots())
    widgets = sum(len(list(page.widgets())) for page in doc)
    sizes = sorted({(round(p.rect.width), round(p.rect.height)) for p in doc})
    rotated = [i + 1 for i, p in enumerate(doc) if p.rotation]
    links = sum(len(p.get_links()) for p in doc)
    print(f"  pages:        {doc.page_count}")
    print(f"  outline:      {len(toc)} entries, depth {depth}")
    print(f"  annotations:  {annots}")
    print(f"  widgets:      {widgets}")
    print(f"  attachments:  {doc.embfile_names()}")
    print(f"  page sizes:   {sizes}")
    print(f"  rotated:      {rotated}")
    print(f"  links:        {links}")
    print(f"  toc depth 5:  {'yes' if depth == 5 else 'NO'}")
    print(f"  file size:    {path.stat().st_size / 1024:.0f} KiB")
    doc.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out", nargs="?", default=str(DEFAULT_OUT))
    ap.add_argument("--keep-intermediate", action="store_true")
    args = ap.parse_args()
    out_path = Path(args.out).resolve()

    print("Generating assets...")
    generate_assets()

    print("Laying out document...")
    with tempfile.TemporaryDirectory() as tmpdir:
        intermediate = Path(tmpdir) / "demo-layout.pdf"
        doc = DemoDoc(str(intermediate))
        doc.multiBuild(build_story(doc))
        print(f"  layout pages: {doc.page}")
        postprocess(intermediate, out_path)
        if args.keep_intermediate:
            shutil.copy(intermediate, out_path.with_suffix(".layout.pdf"))

    print(f"Wrote {out_path}")
    summarize(out_path)


if __name__ == "__main__":
    main()
