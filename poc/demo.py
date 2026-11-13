"""Walk through the prototype on a real PDF.

    PYTHONPATH=src python3 poc/demo.py <file.pdf> [page] [block]

Prints the content-stream operators with their source spans, the lines and
paragraphs the engine reads, then rewrites one paragraph, saves, and checks the
result with MuPDF and (when installed) ``pdftotext``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import pymupdf as fitz  # noqa: E402

from pdfar.pdfcraft import read_page, rewrite_block  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    page_no = int(argv[1]) if len(argv) > 1 else 0
    block_index = int(argv[2]) if len(argv) > 2 else 0

    doc = fitz.open(path)
    model = read_page(doc, page_no)
    print(f"{path}: {doc.page_count} pages; page {page_no + 1} has "
          f"{len(model.streams)} stream(s), {len(model.ops)} operators, "
          f"{len(model.lines)} lines, {len(model.blocks)} paragraphs")

    print("\nfirst operators and their byte spans:")
    for op in model.ops[:12]:
        print(f"  {op.start:6d}..{op.end:<6d} {op.op:3s} {op.operands!r}"[:140])

    print("\nlines:")
    for i, line in enumerate(model.lines[:10]):
        print(f"  {i:3d} rect={tuple(round(v, 1) for v in line.rect)} "
              f"size={line.size:.1f} font={line.base_font} {line.text!r}"[:150])

    print("\nparagraphs:")
    for i, block in enumerate(model.blocks[:10]):
        print(f"  {i:3d} rows={len(block.rows)} rect={tuple(round(v, 1) for v in block.rect)} "
              f"{block.text[:80]!r}")

    if not 0 <= block_index < len(model.blocks):
        print(f"\nno paragraph {block_index}; nothing to rewrite")
        doc.close()
        return 1
    block = model.blocks[block_index]
    suffix = " This sentence was added by the Python prototype."
    print(f"\nrewriting paragraph {block_index} ({len(block.rows)} rows) ...")
    edit = rewrite_block(doc, page_no, block_index, block.text + suffix, model=model)
    print(f"  substitute font: {edit.substituted or 'none'}")

    out = os.path.join(tempfile.mkdtemp(prefix="pdfcraft_demo_"), "edited.pdf")
    doc.save(out, garbage=0, deflate=True)
    doc.close()
    print(f"  saved {out}")

    reopened = fitz.open(out)
    text = " ".join(reopened[page_no].get_text().split())
    print(f"  MuPDF sees the new sentence: {' '.join(suffix.split()) in text}")
    reopened.close()
    try:
        plain = subprocess.run(
            ["pdftotext", "-f", str(page_no + 1), "-l", str(page_no + 1), out, "-"],
            capture_output=True, text=True, check=True,
        ).stdout
        print(f"  pdftotext sees it too: {' '.join(suffix.split()) in ' '.join(plain.split())}")
    except (OSError, subprocess.CalledProcessError):
        print("  pdftotext is not installed; skipped the independent check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
