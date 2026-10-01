"""Shared test fixtures: headless Qt app and generated sample PDFs."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pymupdf as fitz  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def build_sample_pdf(path, pages=3, width=200, height=100):
    """A small PDF with predictable geometry and text.

    Page 1 has:
      * a filled black square at (10,10)-(40,40)
      * the text "Hello PDFAR world" near the top-left
      * the text "BOTTOMRIGHT" near the bottom-right
    """
    doc = fitz.open()
    for p in range(pages):
        page = doc.new_page(width=width, height=height)
        if p == 0:
            page.draw_rect(fitz.Rect(10, 10, 40, 40), color=(0, 0, 0), fill=(0, 0, 0))
            page.insert_text(fitz.Point(10, 60), "Hello PDFAR world", fontsize=12)
            page.insert_text(fitz.Point(width - 80, height - 10), "BOTTOMRIGHT", fontsize=10)
        else:
            page.insert_text(fitz.Point(10, 30), f"Page {p + 1} content", fontsize=12)
            page.insert_text(fitz.Point(10, 60), "alpha beta gamma", fontsize=12)
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def sample_pdf(tmp_path):
    return build_sample_pdf(tmp_path / "sample.pdf")


@pytest.fixture
def big_pdf(tmp_path):
    return build_sample_pdf(tmp_path / "big.pdf", pages=40)
