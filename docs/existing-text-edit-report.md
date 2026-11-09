# Final Implementation Report — Existing-Text Editing in PDFAR

Date: 2026-11-08

## 1. What was already working in PDFAR

- A solid intent-based editing model (`edits.py`): `Edit.apply(doc)` runs on a
  clone; `DocumentSession.apply` adopts only on success; undo/redo are whole-document
  snapshots. Any new `Edit` is automatically undoable and survives save.
- A real content-stream parser/serializer (`content_stream.py`) that records each
  operator's byte span (`start_pos`/`end_pos`).
- A **text-box / header / watermark / Bates** feature (`text_editing.py`) — this
  *adds* new text; it is **not** existing-text editing.
- The viewer (`viewer.py`) already had a generic tool-mode framework, painting
  pipeline, and page↔screen coordinate helpers.

## 2. What was learned from PdfCraft

`/external/pdfcraft` is a **Rust** project; it cannot be imported, linked, or
copied into PDFAR. It was used only as a conceptual reference and confirmed the
same design PDFAR already follows: describe intent, edit a clone, snapshot undo,
graded scope invalidation. No PdfCraft code was reused.

## 3. Code reused or adapted

- `content_stream.ContentStreamParser` / `ContentStreamSerializer` / `_extract_text_from_op`: reused unchanged.
- `edits.Edit`, `DocumentSession.apply`: the engine's `ReplaceExistingText` is a plain `Edit`, so undo/redo/save/dirty-state come for free.
- Viewer tool-mode + paint + coordinate helpers: adapted to add `"edit_text"`.

## 4. New code implemented

- `src/pdfar/text_edit_existing.py`
  - `find_text_at_point` — word hit testing via `get_text("words")` (precise
    page-space boxes) + font/size/color from `get_text("dict")`.
  - `locate_operator_for_word` — replays the content-stream graphics state
    (`cm`, `Tm`, `Td`, `TD`, `T*`, `Tf`, `Tc`, `Tw`, `Tz`) to compute each
    text operator's baseline origin, then matches the clicked word by text
    containment + nearest baseline-left point (correctly disambiguates words
    that repeat on different lines).
  - `ReplaceExistingText(Edit)` — stores the word, its box and font info, and at
    `apply()` re-locates the operator on whatever clone it is given, validates
    glyph support in the original font, and performs a **surgical** replacement:
    only the matched operator's span `[start_pos:end_pos)` is re-serialized and
    spliced back; everything else is byte-identical.
  - Strategy D guards: refuses glyphs the original font cannot draw, and refuses
    words split across `TJ` kerning segments.
- `src/pdfar/text_edit_inline.py` — `ExistingTextEditor` popover (QLineEdit +
  Apply/Cancel, Enter/Esc handling, live-preview callback).
- `viewer.py` — `"edit_text"` tool mode: hover highlight, click→editor,
  live preview overlay (with overflow tint), all routed through `apply_edit`.
- `main.py` — "Edit Text" toolbar action wired to the existing tool framework.

## 5. Files modified

- `src/pdfar/text_edit_existing.py` (new)
- `src/pdfar/text_edit_inline.py` (new)
- `src/pdfar/viewer.py` (edit-text tool, hover, preview, editor wiring)
- `src/pdfar/main.py` (toolbar action + `_set_tool_mode` support)
- `src/pdfar/text_edit.py` (deleted — placeholder stubs)
- `src/pdfar/text_edit_core.py` (deleted — unimportable: referenced a
  non-existent `TextRunFinder`)
- `docs/existing-text-edit-audit.md` (audit), `docs/existing-text-edit-report.md`
  (this file)
- `ROADMAP.md` (feature status appended)
- `tests/test_existing_text_edit.py`, `tests/test_existing_text_ui.py` (new)

## 6. Tests added

- `tests/test_existing_text_edit.py` (14 tests): hit testing, multi-occurrence
  disambiguation, surgical replacement in literal `Tj` and hex `TJ`, accented
  text, font-refusal (Strategy D), kerning-split refusal, session apply/undo/redo,
  save/reopen/extract, low-level operand replacement.
- `tests/test_existing_text_ui.py` (3 tests): viewer edit→commit→undo→redo, no-op
  when text unchanged, and overlay paint path.

Run: `PYTHONPATH=src python3 -m pytest tests/test_existing_text_edit.py tests/test_existing_text_ui.py -q`

## 7. Test results

All 17 existing-text tests pass. A broader regression subset (selection tools,
page rotation, session, viewer) — 75 tests — also passes. A manual check on the
real `demo.pdf` (embedded DejaVuSans-Bold, literal `Tj`) replaced an existing
word and confirmed the change via text extraction.

## 8. Known limitations

- Words split across `TJ` kerning segments are refused (no silent corruption).
- Replacement glyphs missing from the original font are refused; font
  substitution/embedding is not yet implemented.
- Type0/CID and embedded-subset fonts: best-effort; some will be refused.
- Form XObjects, scanned (non-text) areas and exotic graphics states are detected
  and refused.
- No paragraph reflow, OpenType shaping, or complex CJK line breaking.

## 9. Remaining work

- Font substitution/embedding so edits can proceed when the original font lacks a
  glyph (Strategy B/C).
- Editing words split by kerning (merge the segments and rebuild the `TJ`).
- Stronger Type0/CID handling and verified visual rendering checks on reopened
  files (pixel-diff) for the supported cases.
