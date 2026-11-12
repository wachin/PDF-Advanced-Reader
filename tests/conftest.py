"""Shared test fixtures: headless Qt app and generated sample PDFs."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pymupdf as fitz  # noqa: E402
from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from pdfar.session import DocumentSession  # noqa: E402


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    # Point QSettings at a throwaway directory so tests never read or write
    # the developer's real config (and cannot leak state between tests).
    cfg = tmp_path_factory.mktemp("pdfar-settings")
    QSettings.setPath(QSettings.Format.NativeFormat,
                      QSettings.Scope.UserScope, str(cfg))
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


def make_session(path, password=""):
    """A read-only session over *path*; the caller must ``session.close()``."""
    doc = fitz.open(str(path))
    authenticated = 0
    if password and doc.needs_pass:
        authenticated = doc.authenticate(password)
    return DocumentSession(doc, str(path), password, authenticated)


@pytest.fixture
def session(sample_pdf):
    handle = make_session(sample_pdf)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def big_session(big_pdf):
    handle = make_session(big_pdf)
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def big_pdf(tmp_path):
    return build_sample_pdf(tmp_path / "big.pdf", pages=40)


def build_paragraph_pdf(path, width=600, height=800, lines=3,
                        region=(100, 100, 500, 760), fontsize=12,
                        align="left"):
    """A PDF with one paragraph of wrapped lines.

    Uses :meth:`fitz.Page.insert_textbox` so the lines form a single text block
    (one ``BT … ET``), which is what the paragraph reflow machinery expects.
    ``align`` is one of ``left``, ``center``, ``right``, ``justify``.  ``height``
    lets a test create a short page so overflow is easy to trigger.
    """
    """A PDF with one paragraph of wrapped lines.

    Uses :meth:`fitz.Page.insert_textbox` so the lines form a single text block
    (one ``BT … ET``), which is what the paragraph reflow machinery expects.
    ``align`` is one of ``left``, ``center``, ``right``, ``justify``.
    """
    aligns = {"left": fitz.TEXT_ALIGN_LEFT, "center": fitz.TEXT_ALIGN_CENTER,
              "right": fitz.TEXT_ALIGN_RIGHT, "justify": fitz.TEXT_ALIGN_JUSTIFY}
    sentence = ("The quick brown fox jumps over the lazy dog near the river "
                "and the meadow while the sun sets slowly behind the distant "
                "hills. ")
    body = sentence * lines
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    page.insert_textbox(fitz.Rect(*region), body, fontsize=fontsize,
                        fontname="helv", align=aligns[align])
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def paragraph_session(tmp_path):
    path = build_paragraph_pdf(tmp_path / "paragraph.pdf")
    handle = make_session(path)
    try:
        yield handle
    finally:
        handle.close()


def build_rotated_paragraph_pdf(path, width=600, height=800, fontsize=12):
    """A PDF with one rotated (90°) paragraph."""
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    body = ("Rotated sample text line here flows down the page slowly while "
            "the reader follows each word in turn across the narrow column. ") * 2
    page.insert_text((width / 2, height - 120), body, fontsize=fontsize,
                     fontname="helv", rotate=90)
    doc.save(path)
    doc.close()
    return str(path)


@pytest.fixture
def rotated_session(tmp_path):
    path = build_rotated_paragraph_pdf(tmp_path / "rotated.pdf")
    handle = make_session(path)
    try:
        yield handle
    finally:
        handle.close()
