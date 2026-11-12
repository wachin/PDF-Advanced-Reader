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

## 8. Known limitations (Phase 1)

- ~~Words split across `TJ` kerning segments are refused~~ — supported in Phase 2
  (see §10).
- Replacement glyphs missing from the original font are refused; font
  substitution/embedding is not implemented.
- Type0/CID and embedded-subset fonts: best-effort; some will be refused.
- Form XObjects, scanned (non-text) areas and exotic graphics states are detected
  and refused.
- No paragraph reflow, OpenType shaping, or complex CJK line breaking.

## 9. Remaining work (Phase 1 → addressed in Phase 2)

- ~~Editing words split by kerning (merge the segments and rebuild the `TJ`).~~
- ~~Stronger Type0/CID handling and verified visual rendering checks (pixel-diff).~~
- Font substitution/embedding so edits can proceed when the original font lacks a
  glyph (still open).

## 10. Phase 2 — Reliability and advanced editing (2026-11-12)

Phase 2 hardened the engine without replacing it: the `Edit`/`DocumentSession`
architecture, surgical single-operator splice, undo/redo, save and geometry are
unchanged. All 2,907 tests pass; 47 are dedicated existing-text tests.

### 10.1 Font validation is now fail-closed

`_font_supports_text()` was replaced by `_validate_replacement()`, which returns
`(ok, reason, codec)` and refuses unless **all** of the following hold:

1. `page.get_fonts()` succeeds and the operator's resource is found.
2. A trustworthy codec exists (the font's `/ToUnicode` CMap, or a known simple
   base encoding).
3. The font program loads (`extract_font` + `fitz.Font(fontbuffer=…)` for
   embedded fonts, `fitz.Font(fontname=…)` otherwise).
4. Every glyph exists (Type0: CID `< glyph_count`; simple: `has_glyph`).
5. Every character can be encoded through the font's actual encoding.

Any inspection failure is a refusal, never an implicit approval. The old
`info[6]` embedded check (wrong for the 6-tuple `get_fonts()` result) is gone.

### 10.2 Encoding-aware replacement

A `_FontCodec` maps character codes to/from Unicode. It is built from the
`/ToUnicode` CMap (`beginbfchar`/`beginbfrange`, 1- and 2-byte code spaces) or
from a known simple encoding (`WinAnsiEncoding`→cp1252, `MacRomanEncoding`,
`PDFDocEncoding`). Type0 fonts without a ToUnicode map are refused because their
byte codes are CIDs, not Unicode. Replacement text is encoded to bytes with the
codec and written as a hex string; untouched prefix/suffix bytes are preserved
verbatim, so characters that are already correct are never re-encoded.

`locate_operator_for_word()` now decodes operator text through the same codec,
which makes Type0/Identity-H text locatable (previously the 2-byte CIDs decoded
as garbage and never matched). Code width follows the font type: simple fonts are
always treated as one-byte even when their ToUnicode CMap declares a wider
codespace range (seen in the Frontiers documents), and mappings above 0xFF are
discarded for them.

### 10.3 `TJ` words split across segments

`_replace_in_tj_array()` reconstructs the concatenated text, finds the word,
and rebuilds the array:

- kerning numbers strictly inside the match are dropped (they positioned the old
  glyphs);
- numbers before and after the match are preserved;
- the replacement is inserted exactly once;
- text in unrelated segments is preserved;
- a word occurring more than once in the operator is refused as ambiguous.

### 10.4 Multi-word and whole-line editing

New helpers `words_in_line`, `words_between` and `combine_hits` build a span from
several adjacent words on one line. The viewer now supports:

- **drag-select** across words on one line, then release to edit the span;
- **double-click** to edit the whole visual line;
- single click still edits one word.

A span is only editable when it maps to a single text-showing operator; a span
that crosses operators is refused. This is a visual grouping, not a claim that a
paragraph is one PDF object. No reflow is performed.

### 10.5 Visual verification

`tests/test_existing_text_visual.py` renders the page before and after a
save/reopen round-trip at 144 dpi and compares pixel regions:

- the edited region changes (mean abs diff > 3);
- an unrelated footer and a filled graphic stay unchanged (diff < 1);
- an over-wide replacement changes pixels beyond the original word box
  (overflow is visible);
- page count and unrelated extraction are unchanged.

### 10.6 Real-world compatibility

Tested against the PDFs in `external/`:

| Document | Fonts | Result |
|---|---|---|
| `arxiv-2610.30749.pdf` | embedded subset Type1 + ToUnicode | **Editable** (22/23 prefix edits applied) |
| `frontiers-fgene-2024-1387588.pdf` | embedded subset Type1 + ToUnicode | **Editable** (19/24) |
| `frontiers-fpsyg-2025-1558407.pdf` | embedded subset fonts | **Editable** (19/24); some single-letter words refused as ambiguous |
| `frontiers-fpsyt-2024-1366186.pdf` | embedded subset Type1 + ToUnicode | **Editable** (22/22) |
| `GS262-GettingStarted.pdf` (LibreOffice) | subsetted TrueType, no usable Unicode cmap | **Refused** (fail-closed); document not modified |

Synthetic cases that pass: multiple font resources on one page, coloured text
(the separate `rg` operator is preserved), rotated text, and a subsetted
Type0/Identity-H font.

### 10.7 Parser corrections

Two defects in the shared `content_stream.py` were fixed because they blocked
real documents:

- operators glued to the next token (`]TJ/F42 12 Tf`) are now split correctly;
  `T*`, `B*`, `b*`, `f*` and `W*` are recognised;
- `_extract_text_from_op()` no longer returns after the first `TJ` operand.

### 10.8 Tests added (Phase 2)

- `tests/test_existing_text_edit.py`: 29 tests (was 14) — fail-closed
  inspection failures, Type0/Identity-H encode/decode, accented replacements,
  missing-ToUnicode refusal, multi-segment `TJ`, kerning preservation, multi-word
  spans, whole-line edits, cross-operator refusal, word deletion.
- `tests/test_existing_text_ui.py`: 5 tests (was 3) — span commit, double-click
  line editor.
- `tests/test_existing_text_visual.py`: 3 tests (new) — pixel-diff, overflow,
  structure preservation.
- `tests/test_existing_text_realworld.py`: 10 tests (new) — real corpus,
  save/reopen, subsetted-font refusal, multiple fonts, colour, rotation,
  subsetting.

### 10.9 Remaining limitations

- Font substitution/embedding is still not implemented: a replacement glyph
  absent from the original font (including subsetted fonts that stripped the
  Unicode cmap) is refused.
- A word/span that appears more than once in one operator is refused as
  ambiguous; no positional disambiguation yet.
- Paragraph-level reflow, OpenType shaping and complex CJK line breaking remain
  out of scope.
- Form XObjects and scanned content remain unsupported and are refused.
- Graphics-state replay tracks `cm`, `Tm`, `Td`, `TD`, `T*`, `Tf`, `Tc`, `Tw`
  and `Tz`, but does not yet model `q`/`Q` save/restore nesting or compose nested
  `cm` matrices. Pages that rely on those may mislocate a word; the edit is
  still applied only to an operator whose decoded text contains the word, but
  positional disambiguation is not guaranteed in that case.
