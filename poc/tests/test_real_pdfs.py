"""Edits on the repository's real PDFs.

These use the large LibreOffice guide and the two-column research papers that
already live under ``external/`` (see AGENTS.md): the first has one BT/ET per
row in a subset font, the second has dense two-column body text.  Both are
skipped when the files are absent.
"""

from __future__ import annotations

import os
import subprocess

import pymupdf as fitz
import pytest

from pdfar.pdfcraft import read_page, replace_line, rewrite_block

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GS = os.path.join(
    ROOT, "external", "LibreOffice-Getting-Started-Guides-PDF-BackUp", "GS262-GettingStarted.pdf"
)
ARXIV = os.path.join(ROOT, "external", "two-column-test-pdfs", "arxiv-2610.30749.pdf")


def pdftotext(path: str, page_no: int) -> str:
    try:
        out = subprocess.run(
            ["pdftotext", "-f", str(page_no + 1), "-l", str(page_no + 1), path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("pdftotext is not available")
    return " ".join(out.split())


def check_edit(path: str, page_no: int, suffix: str, tmp_path, name: str) -> None:
    doc = fitz.open(path)
    pm = read_page(doc, page_no)
    block = max(pm.blocks, key=lambda item: len(item.rows))
    index = next(i for i, item in enumerate(pm.blocks) if item is block)
    edit = rewrite_block(doc, page_no, index, block.text + suffix, model=pm)
    assert edit.substituted in (None, "Helvetica", "Times", "Courier")
    target = tmp_path / f"{name}.pdf"
    doc.save(target, garbage=0, deflate=True)
    doc.close()

    assert " ".join(suffix.split()) in pdftotext(str(target), page_no)
    reopened = fitz.open(target)
    assert " ".join(suffix.split()) in " ".join(reopened[page_no].get_text().split())
    assert reopened.page_count == fitz.open(path).page_count
    reopened.close()


@pytest.mark.skipif(not os.path.exists(GS), reason="LibreOffice guide is not present")
def test_gs262_paragraph_with_one_bt_per_row(tmp_path):
    doc = fitz.open(GS)
    pm = read_page(doc, 1)
    block = pm.blocks[1]
    assert len(block.rows) >= 5
    doc.close()
    check_edit(GS, 1, " This sentence was added by the Python prototype.", tmp_path, "gs262")


@pytest.mark.skipif(not os.path.exists(ARXIV), reason="arXiv paper is not present")
def test_arxiv_two_column_paragraph(tmp_path):
    doc = fitz.open(ARXIV)
    pm = read_page(doc, 1)
    block = max(pm.blocks, key=lambda item: len(item.rows))
    assert len(block.rows) >= 10
    # The paragraph must sit inside one column, not across the gutter.
    assert block.rect[2] - block.rect[0] < 300
    line = pm.lines[block.lines[0]]
    following = pm.lines[block.lines[1]].text
    doc.close()

    doc = fitz.open(ARXIV)
    replace_line(doc, 1, block.lines[0], "Edited line by the Python prototype.")
    target = tmp_path / "arxiv_line.pdf"
    doc.save(target, garbage=0, deflate=True)
    doc.close()

    flat = pdftotext(str(target), 1)
    assert "Edited line by the Python prototype." in flat
    assert " ".join(following.split()) in flat
    assert " ".join(line.text.split()) not in flat
