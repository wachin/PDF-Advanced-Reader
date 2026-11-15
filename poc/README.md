# `poc/` — research harness for the PdfCraft port

This directory is **not production code** and is not imported by the running
application. It exists to exercise the Python port of PdfCraft's existing-PDF
text engine directly, with small controlled fixtures and with real PDFs.

The engine itself lives in [`../src/pdfar/pdfcraft/`](../src/pdfar/pdfcraft/);
the application only sees it through
[`../src/pdfar/pdfcraft_adapter.py`](../src/pdfar/pdfcraft_adapter.py).

## Contents

| File | Purpose |
|---|---|
| `fixtures.py` | Builds structural PDFs on the fly: simple paragraphs, shared `BT/ET`, multiple content streams, Form XObjects, TJ kerning, rotated text, mixed styles, embedded subsets, one `BT` per row, lists. |
| `demo.py` | Walks through one page of a real PDF: operators with source spans → lines → paragraphs → rewrite one paragraph → save → verify with MuPDF and `pdftotext`. |
| `bench.py` | Measures page read, rewrite and save times on the fixtures and the repository's real PDFs. |
| `tests/test_prototype.py` | 22 tests over the engine: spans, lines, blocks, rewrites, fonts, previews, sessions. |
| `tests/test_real_pdfs.py` | 2 tests on real documents (LibreOffice guide, arXiv two-column paper). |
| `NOTICE.md` | Provenance and licensing of the port relative to PdfCraft and PyMuPDF. |

## Running

```bash
# Prototype tests (separate conftest from tests/)
PYTHONPATH=src python3 -m pytest poc/tests -q

# Walk through a real PDF
PYTHONPATH=src python3 poc/demo.py demo.pdf 0

# Benchmark
PYTHONPATH=src python3 poc/bench.py
```

The fixtures are generated, so the tests need no checked-in PDFs. The real-PDF
tests skip themselves when `external/` is not populated.

## Relationship to `tests/`

- `poc/tests/` tests the engine's internals and the rewrite path.
- `tests/test_pdfcraft_adapter.py` tests the public adapter, its read-only
  guarantees and its behaviour inside a real `DocumentSession`.

Keep both green when changing the port. See
[`../docs/developers/status.md`](../docs/developers/status.md) for the current
coverage and known refusals.
