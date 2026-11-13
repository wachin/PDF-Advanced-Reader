"""Measure the prototype on fixtures and on the repository's real PDFs.

    python3 poc/bench.py [--pages N]

Reports page read time (operators -> lines -> paragraphs), a paragraph rewrite,
and a save, for each document.  No PDFAR code is involved.
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pymupdf as fitz  # noqa: E402

from fixtures import all_fixtures  # noqa: E402
from pdfar.pdfcraft import UnsupportedError, read_page, rewrite_block  # noqa: E402
from pdfar.pdfcraft.interp import FontCache  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REAL = {
    "GS262 (540 pages, 24 MB)": os.path.join(
        ROOT, "external", "LibreOffice-Getting-Started-Guides-PDF-BackUp",
        "GS262-GettingStarted.pdf",
    ),
    "arXiv (8 pages, two columns)": os.path.join(
        ROOT, "external", "two-column-test-pdfs", "arxiv-2610.30749.pdf"
    ),
    "Frontiers (11 pages, two columns)": os.path.join(
        ROOT, "external", "two-column-test-pdfs", "frontiers-fgene-2024-1387588.pdf"
    ),
}


def measure(path: str, pages: int) -> None:
    opened = time.perf_counter()
    doc = fitz.open(path)
    open_time = time.perf_counter() - opened
    fonts = FontCache(doc)
    read_times = []
    for page_no in range(min(pages, doc.page_count)):
        started = time.perf_counter()
        model = read_page(doc, page_no, fonts=fonts)
        read_times.append(time.perf_counter() - started)
        if page_no == 0:
            first = model
    print(f"{os.path.basename(path)}: open {open_time * 1000:.1f} ms, "
          f"pages {doc.page_count}")
    print(f"  read_page: median {statistics.median(read_times) * 1000:.1f} ms, "
          f"max {max(read_times) * 1000:.1f} ms over {len(read_times)} page(s)")

    block_index = next(
        (i for i, block in enumerate(first.blocks) if len(block.rows) >= 3), None
    )
    if block_index is not None:
        model = read_page(doc, 0)
        block = model.blocks[block_index]
        started = time.perf_counter()
        try:
            rewrite_block(doc, 0, block_index, block.text + " Extra words.", model=model)
        except UnsupportedError as error:
            print(f"  rewrite refused: {error}")
            doc.close()
            return
        edit_time = time.perf_counter() - started
        out = os.path.join(tempfile.mkdtemp(prefix="pdfcraft_bench_"), "out.pdf")
        started = time.perf_counter()
        doc.save(out, garbage=0, deflate=True)
        save_time = time.perf_counter() - started
        print(f"  rewrite {len(block.rows)}-row paragraph: {edit_time * 1000:.1f} ms; "
              f"save {save_time * 1000:.1f} ms")
        os.unlink(out)
    doc.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5)
    args = parser.parse_args()

    print("-- fixtures --")
    for path in all_fixtures().values():
        measure(path, args.pages)
    print("\n-- real documents --")
    for label, path in REAL.items():
        if os.path.exists(path):
            print(f"\n{label}")
            measure(path, args.pages)
        else:
            print(f"\n{label}: not present, skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
