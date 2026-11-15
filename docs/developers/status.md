# Current status

> **Last updated:** 2026-11-15
> **Branch:** `feature/pdfcraft-editiong`
> **Last commit:** `721a6de` — “Phase 6: port PdfCraft’s text engine and add a
> read-only adapter”

This is the one file to update when the phase state changes. Everything else in
`docs/` describes a design; this describes where the work actually stands.

---

## Done

| Phase | Delivered |
|---|---|
| 1–4 | Existing-text editing: content-stream parsing/splicing, glyph and font mapping, fail-closed validation, graphics-state replay, Form XObjects, repeated-word targeting, font discovery and safe embedding. |
| 5 / 5.1 | Paragraph model, line breaking, alignment, collision/overflow checks; PdfCraft-style click-to-select-paragraph UX; `ReflowParagraph`. |
| 5.2 | Apply/commit reliability and detection fixes. |
| 5.3 | Multi-line font substitution fix (the real-UI persistence bug). |
| 5.4 | Editing paragraphs that share one `BT … ET` block with siblings. |
| 6A | Audit and adoption proposal (`docs/pdfcraft-architecture-audit.md`). Recommendation was Rust + PyO3; the maintainer chose a Python port. |
| 6B | Native Python port moved to `src/pdfar/pdfcraft/`; read-only adapter `src/pdfar/pdfcraft_adapter.py`; 34 adapter cases; 24 PoC tests. |

Phase 6B is committed but **not** wired into the application. The existing
paragraph editor is still the one the UI uses.

---

## What Phase 6B provides

```python
from pdfar.pdfcraft_adapter import analyze_page, analyze_session_page

analysis = analyze_page(doc, page_no, version=0)      # read-only
analysis = analyze_session_page(session, page_no)     # private copy, no UI invalidation
```

- `AnalyzedLine` — text, rect, baseline, frame, font, size, rotation,
  `OperatorSpan` provenance (stream + local byte offsets), `from_form`,
  `decodable`, `vertical`.
- `AnalyzedParagraph` — stable `ParagraphKey`, text, rect, rows, operators,
  `problems` (refusals) and `notes`.
- `PageAnalysis` — paragraphs, lines, diagnostics, `fingerprint`, `version`;
  `is_current(session)`, `find(key)`, `paragraph_at(x, y)`,
  `paragraph_overlapping(rect)`.

The adapter never writes. It reads a private copy through
`DocumentSession.open_copy`, so it cannot invalidate the UI's `Page` handles.

Performance (median on this machine, shared `FontCache`): 47 ms for the
540-page LibreOffice guide, 79 ms for a two-column arXiv page, 52 ms for a
Frontiers page, 5 ms for `demo.pdf`.

---

## Known limitations and refusals

The adapter refuses a paragraph, with a reason, when it cannot rewrite it
faithfully:

- **Styled runs** — more than one font resource, base font, size, colour or
  bold/italic style in the paragraph. This is the dominant refusal on real
  papers (49 of 58 paragraphs on arXiv page 1, 25 of 39 on Frontiers, 4 of 14
  in the LibreOffice guide). The Rust `rewrite_styled_block` is **not ported**.
- **Form XObjects** — text is read, but rewriting it is not implemented.
- **Vertical writing** (`/WMode 1`) — detected, not implemented.
- **Undecodable character codes** — a rewrite could lose glyphs.
- **Non-zero render modes** (invisible/outline text).
- **Unreadable font programs** — the text is absent and the page diagnostic
  says so.

Other known gaps:

- **No push-down.** A reflow that grows a paragraph can overlap the next block;
  the existing paragraph engine refuses instead of moving neighbours.
- **Type 3 `/FontMatrix`** is assumed to be the usual 0.001 scale.
- **Font substitution** for subsets reuses standard-14 Helvetica; validated on
  DejaVu and one arXiv font only.
- **Grouping differs from the existing engine in two legitimate ways**: list
  markers start a new paragraph (PdfCraft #2564) where the existing grouper
  joins the items, and the existing grouper applies a page-wide body width that
  splits two-column text differently. Both are recorded in
  `tests/test_pdfcraft_adapter.py`; neither is a bug.

---

## Next: Phase 6C (not started)

The open decisions, in order:

1. **Port styled-run rewriting?** It would unlock most real-world paragraphs,
   but it is the largest remaining piece of the Rust engine
   (`rewrite_styled_block`, shaped text, font selection per run).
2. **How should the adapter drive `DocumentSession`?** A paragraph edit must
   become an `Edit` with `Scope.CONTENT`, run on a working copy, and return
   enough identity (`ParagraphKey`) to re-find the paragraph after the commit.
3. **What happens to the existing paragraph editor?** Keep it as a fallback for
   structures the port refuses, or replace it once coverage is high enough.
4. **Push-down.** Decide whether growing paragraphs may move following blocks;
   this is a document-wide layout operation, not a local rewrite.

Do not start any of these without the maintainer's approval.

---

## Verifying the current state

```bash
# The application still uses the existing editor
python3 run.py demo.pdf

# Adapter tests (read-only guarantees, structures, session staleness)
PYTHONPATH=src python3 -m pytest tests/test_pdfcraft_adapter.py -q

# Engine prototype tests
PYTHONPATH=src python3 -m pytest poc/tests -q

# Whole suite and lint
PYTHONPATH=src python3 -m pytest tests/ -q
python3 -m ruff check src/ tests/
```
