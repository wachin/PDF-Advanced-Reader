#!/usr/bin/env python3
"""Generate a demo PDF to exercise the reader (headless-friendly).

Usage: python3 tools/make_demo_pdf.py [out.pdf] [--pages N]
"""

from __future__ import annotations

import argparse
import sys

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz

W, H = 595, 842  # A4 points

LOREM = [
    "PDFAR renders pages on background threads and paints them directly, so scrolling "
    "stays responsive even in very large documents. The render pool keeps a priority "
    "queue: pages you can see are always rendered before pages you cannot.",
    "Text selection works on the real PDF text layer. Drag across words, across pages, "
    "or press Ctrl+A to select everything, then Ctrl+C to copy. Rotation never breaks "
    "the mapping between what you see and what you select.",
    "Search supports AND, OR and exact-phrase queries with optional case sensitivity "
    "and whole-word matching. Every match is highlighted, and F3 jumps between hits "
    "without losing your place.",
    "The sidebar shows thumbnails rendered in the background and the document outline, "
    "so you can navigate long reports the way you would with a native application.",
]


def page_header(page, title, num):
    page.draw_rect(fitz.Rect(0, 0, W, 64), color=None, fill=(0.12, 0.29, 0.49))
    page.insert_text(fitz.Point(48, 41), title, fontsize=18, color=(1, 1, 1))
    page.insert_text(fitz.Point(W - 60, H - 30), f"{num}", fontsize=10, color=(0.4, 0.4, 0.4))


def make(path, pages=6):
    doc = fitz.open()

    # ---- cover ---------------------------------------------------------
    p = doc.new_page(width=W, height=H)
    p.draw_rect(fitz.Rect(0, 0, W, 220), color=None, fill=(0.12, 0.29, 0.49))
    p.insert_text(fitz.Point(60, 120), "PDFAR", fontsize=42, color=(1, 1, 1))
    p.insert_text(fitz.Point(60, 165), "Advanced PDF Reader for Linux", fontsize=18, color=(0.85, 0.9, 1))
    p.insert_textbox(fitz.Rect(60, 260, W - 60, 420),
                     "This document is a rendering test bench. It contains headings, "
                     "paragraphs, a chart and multi-column text so you can verify page "
                     "rendering, zooming, search highlights and text selection.",
                     fontsize=13, color=(0.15, 0.15, 0.15))
    p.draw_rect(fitz.Rect(60, 470, W - 60, 520), color=(0.12, 0.29, 0.49), fill=(0.90, 0.94, 0.99))
    p.insert_text(fitz.Point(80, 502), "Try:  Ctrl+F to search   •   F3 for next hit   •   Ctrl+wheel to zoom",
                  fontsize=12, color=(0.12, 0.29, 0.49))

    # ---- text pages ----------------------------------------------------
    titles = ["Architecture", "Rendering pipeline", "Text & search", "Navigation"]
    for i in range(1, max(2, pages - 1)):
        p = doc.new_page(width=W, height=H)
        page_header(p, f"{i}. {titles[(i - 1) % len(titles)]}", i + 1)
        y = 100
        for j, para in enumerate(LOREM):
            rect = fitz.Rect(48, y, W - 48, y + 150)
            used = p.insert_textbox(rect, para, fontsize=11.5, color=(0.13, 0.13, 0.13),
                                    align=fitz.TEXT_ALIGN_JUSTIFY)
            y += 150 - used + 24
            if j == 1 and i % 2 == 0:
                # a simple bar chart
                base = y + 20
                for k, val in enumerate([70, 120, 90, 160, 110]):
                    x = 70 + k * 90
                    p.draw_rect(fitz.Rect(x, base + 180 - val, x + 56, base + 180),
                                color=(0.12, 0.29, 0.49), fill=(0.32, 0.55, 0.85))
                p.draw_line(fitz.Point(60, base + 180), fitz.Point(W - 60, base + 180), color=(0.3, 0.3, 0.3))
                y = base + 210

    # ---- last page: two columns ---------------------------------------
    p = doc.new_page(width=W, height=H)
    page_header(p, f"{pages}. Two columns", pages)
    for col in (0, 1):
        x0 = 48 + col * (W / 2 - 24)
        rect = fitz.Rect(x0, 100, x0 + W / 2 - 60, H - 80)
        p.insert_textbox(rect, "\n\n".join(LOREM), fontsize=10.5,
                         color=(0.13, 0.13, 0.13), align=fitz.TEXT_ALIGN_JUSTIFY)

    doc.set_metadata({"title": "PDFAR Demo Document", "author": "PDFAR"})
    doc.save(path)
    doc.close()
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="demo.pdf")
    ap.add_argument("--pages", type=int, default=6)
    args = ap.parse_args()
    print(make(args.out, max(3, args.pages)))


if __name__ == "__main__":
    main()
