# Audit: Existing-Text Editing in PDFAR

Date: 2026-11-08
Scope: existing-text (modify original PDF text) editing workflow.

## 1. Existing PDFAR text-editing architecture

PDFAR applies edits through a clean intent-based model (`src/pdfar/edits.py`):

- `Edit` (ABC): an immutable dataclass describing *what the user asked for* +
  `apply(doc)` that mutates a throwaway document clone. `scope` (e.g. `Scope.CONTENT`)
  drives graded cache invalidation; `requires_assembly` gates page-tree edits.
- `DocumentSession.apply(edit)` runs `apply()` on a clone and *adopts* it only if
  it returns without raising (`EditError` aborts and leaves the document untouched).
- Undo/redo are whole-document snapshots (`EditHistory`), so a text edit is
  automatically undoable/redoable once it is a proper `Edit`.

Two distinct things currently live under "text editing":

1. **Adding new text** (`src/pdfar/text_editing.py` + `text_box_dialog.py`,
   `main.py`): text boxes, header/footer, watermark, Bates, image insert. This is
   *overlay insertion*, not existing-text editing. It is fully implemented and
   tested (`tests/test_text_editing.py`).
2. **Existing-text editing** (the target of this task): spread across three
   modules, much of it dead or placeholder.

## 2. Current execution flow (existing text)

There is **no UI path** today that edits existing PDF text. `viewer.py` only has
`_update_hover_cursor` (a *mouse pointer* cursor, not a text highlight) and no
"edit existing text" tool mode. The editing modules below are only reachable from
their own tests.

## 3. Components found (and their real state)

| Component | File | State | Notes |
|---|---|---|---|
| `ContentStreamParser` / `ContentStreamSerializer` / `_extract_text_from_op` / `_make_new_operands` / `replace_text_in_stream` | `content_stream.py` | **Functional** | Real, working infrastructure. Parser keeps `start_pos`/`end_pos` per operator. Serializer emits literal strings and arrays. Limitation: dict operands serialize to `<< >>` (data loss) and inline images/complex color spaces are not preserved — so re-serializing a *whole* stream is unsafe. |
| `ContentStreamTokenizer` | `text_engine.py:87` | **Broken/placeholder** | `_parse_content_streams()` returns `[]`; `build_operator_map()` compares trace `text` equality (unreliable). Not imported by UI. |
| `GlyphOperatorMapper` | `text_engine.py:174` | **Placeholder** | `_build_map()` is `pass`. Not imported by UI. |
| `FontLadder` | `text_engine.py:213` | **Partial** | `_find_system_font()` returns `None` always; `_fonts_match()` always `True`. Resolves base-14 fine but never finds a system font. Not imported by UI. |
| `ContentStreamSplicer` | `text_engine.py:308` | **Partial** | `_create_splice_edit()` returns placeholder `ContentStreamEdit` with empty values. `apply()` calls `replace_text_in_stream`. |
| `ReplaceTextRun(Edit)` | `text_engine.py:450` | **Functional but flawed** | `apply()` really splices content streams, but (a) wraps the **entire** stream in `q/Q` and re-serializes the *whole* stream (unsafe — loses dict/inline-image fidelity), (b) does substring `old_text in text` replacement with no hit-testing/operator mapping from a click. |
| `TextEditEngine` | `text_engine.py:390` | **Partial** | Wires the above; drives `ReplaceTextRun`. No UI. |
| `TextRunLocator` | `text_edit_core.py:34` | **Broken** | Imports `TextRunFinder` from `content_stream`, but **`TextRunFinder` does not exist** → `text_edit_core.py` cannot be imported. Dead code. |
| `ReplaceTextRun(Edit)` | `text_edit_core.py:60` | **Dead** | Unreachable (import error above). |
| `EditTextRun(Edit)` | `text_edit_core.py:165` | **Dead** | Unreachable. Note: `apply()` mutates the live `doc` directly ("bypassing session"), violating the clone-apply model. |
| `ContentStreamParser` / `TextExtractor` / `TextReplacer` / `ReplaceText` / `ContentStreamSplicer` | `text_edit.py` | **Placeholder** | `parse_stream` is `pass`; `replace_text` is `pass`; `ReplaceText.apply` is `pass`; `_apply` uses redaction+overlay (NOT genuine editing). Mostly stubs. |
| `find_text_at_point` / `find_text_occurrences` | `text_edit_core.py:254` | **Dead** (unreachable). |

## 4. Duplicate / obsolete / conflicting implementations

- **Three** competing editing modules: `text_engine.py`, `text_edit_core.py`
  (broken, unimportable), `text_edit.py` (stubs). Only `content_stream.py` is
  shared and real.
- `text_edit_core.py` and `text_edit.py` are **obsolete/duplicate** and should be
  removed (or consolidated). They are not imported by `main.py`, `viewer.py`, or
  `text_editing.py`.
- Conflict: `text_engine.py`'s `ReplaceTextRun` re-serializes the whole stream
  (dangerous) whereas the intended "surgical" approach is single-operator
  replacement preserving all other bytes.

## 5. Tests and coverage

- `tests/test_text_engine.py`: exercises `text_engine.py` classes. `test_replace_text_run_apply` and `test_text_edit_undo` pass today **only because** `doc_with_text` uses `insert_text` which yields a single `TJ`/string per line and a whole-stream re-serialize happens to round-trip for trivial content. These tests do **not** exercise hit-testing, operator mapping, or real-document fidelity, and they import the broken-by-design modules.
- `tests/test_text_editing.py`: covers *adding* text, not existing-text editing.
- **Missing**: hit-testing, word→operator mapping, surgical replacement on real
  PDFs, font validation, save/reopen, and any UI test.

## 6. PdfCraft reference analysis (`/external/pdfcraft`)

- **PdfCraft is a Rust project** (`Cargo.toml`, `crates/`), not Python. It cannot
  be imported or linked by PDFAR. It is usable only as a *conceptual* reference.
- Its editing model is the same intent-based "edit describes what the user asked"
  pattern already adopted by `edits.py` (see `docs/pdfcraft-editing-analysis.md`).
- Reusable *ideas* only: copy-and-adopt on a clone, snapshot-based undo, graded
  scope invalidation, and the principle of editing the original content stream
  rather than overlaying. No Rust code is copied.

## 7. Recommended integration strategy

1. **Keep** `content_stream.py` (real infra) but stop re-serializing whole
   streams. Replace the whole-stream `q/Q` approach with **surgical, single-operator**
   replacement: re-serialize *only* the matched operator and splice its bytes back
   into the original stream (`[start_pos:end_pos]`), preserving everything else.
2. **Delete** `text_edit_core.py` (unimportable) and `text_edit.py` (stubs) to
   remove duplicate/obsolete code. Keep `text_engine.py`'s useful pieces
   (`FontLadder` base-14 mapping) folded into the new module.
3. **New module** `text_edit_existing.py`:
   - `find_text_at_point(page, point)` — word-level hit testing via
     `page.get_text("words")` (gives per-word page-space bboxes) + font/size/color
     from `page.get_text("dict")`.
   - `locate_operator_for_word(stream, page, word, bbox)` — parse the stream,
     **replay the graphics state** (`cm`, `Tm`, `Td`, `TD`, `T*`, `Tf`, `Tc`,
     `Tw`, `Tz`) to compute each text operator's origin and rendered bbox in PDF
     space, then match the clicked word by bbox overlap + text containment +
     nearest origin.
   - `ReplaceExistingText(Edit)` — stores `page_num`, the original word, its
     page-space bbox, font info, and the new text. `apply()` re-locates the
     operator on the (possibly cloned) document and performs the surgical
     replacement, validating glyph support in the original font. Unsupported
     glyphs / words split across kerning segments → raise `EditError` (Strategy D).
4. **UI** (`viewer.py` + `main.py`): new `"edit_text"` tool mode with hover
   highlight (cached), click → inline editor with live preview, Apply/Cancel, all
   routed through `view.apply_edit` so undo/redo/save already work.
5. **Tests**: unit tests for each engine step + integration test for
   apply→undo→save→reopen→re-extract.

## 8. Open risks

- Real-world PDFs often encode text in `TJ` arrays with kerning numbers; a word
  may be split across multiple string elements. v1 handles the common
  single-element case and refuses (clearly) when a word spans kerning segments.
- Type0/CID, Type3, subsetted, and embedded fonts: v1 validates glyph support
  and refuses when the original font cannot represent the replacement. Full font
  substitution/embedding is a later improvement.
- Form XObjects, rotated/scanned text, and complex graphics states: detected and
  refused where mapping is unreliable (Strategy D), to avoid corrupting the file.

## 9. Phase 2 audit addendum (2026-11-12)

A reliability review of the Phase 1 engine found four concrete defects.

### 9.1 Font validation was fail-open

`_font_supports_text()` returned `(True, None)` on every inspection failure:
`page.get_fonts()` raising, the resource not being found, `extract_font()`
returning empty data, or `fitz.Font(...)` failing to load. It also read the
embedded flag from `info[6]`, but `get_fonts()` returns a **6-tuple**
`(xref, ext, type, basefont, name, encoding)`, so `embedded` was always false.
Net effect: an inspection failure was treated as proof that the font supported
the character.

### 9.2 Glyph presence was checked, but the PDF encoding was not

The engine checked `fitz.Font.has_glyph(ord(ch))` on the Unicode code point and
then wrote the replacement as a latin-1 literal string. That is only valid for
simple fonts whose encoding matches Latin-1. For Type0/Identity-H fonts the
stream contains 2-byte CIDs, and for WinAnsi the 0x80–0x9F range differs from
Latin-1. A glyph existing in the font file does not prove it can be reached
through the font's existing PDF encoding.

### 9.3 The content-stream parser could not split glued operators

LaTeX-generated PDFs commonly write `...]TJ/F42 9.9626 Tf` with no whitespace.
`_parse_all_operands()` only ended an operator token at whitespace, so `TJ` was
swallowed and the following `Tf` was parsed with the array as an operand and
`"F42"` as the font size, raising `ValueError: could not convert string to
float: 'F42'`. The same logic never recognised `T*`, `B*`, `b*`, `f*` or `W*`.

### 9.4 Literal-string operands were decoded twice

The parser decodes literal `(...)` strings to `str` (as UTF-8). The replacement
path then re-encoded that `str` to latin-1 and decoded it through the font
codec, so the text seen during location and the text seen during replacement
could diverge. Real documents (e.g. the Frontiers PDFs) produced "text could not
be found inside the located operator" as a result.

### 9.5 Selection model was word-only

The editor only ever opened on one word; there was no way to select several
words or a whole line, and no pixel-level verification that an edit left the
rest of the page visually intact.
