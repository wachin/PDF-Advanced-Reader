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

## 10. Phase 3 audit addendum (2026-11-12)

A second review focused on coordinate correctness, repeated words and Form
XObjects. Four defects were found and fixed.

### 10.1 The graphics-state replay did not model `q`/`Q`

The Phase 2 replay walked operators linearly and only tracked `cm`, `Tm`,
`Td`/`TD`, `T*`, `Tf`, `Tc`, `Tw` and `Tz`. It had no save/restore stack, so a
`q … Q` block leaked its CTM *and* its text state into everything after it.
Documents that wrap graphics in `q`/`Q` (LaTeX output does this around every
figure and every colour change) could therefore be mapped to the wrong place.

### 10.2 Nested `cm` matrices were composed in the wrong order

The old `_concat(a, b)` implemented column-vector multiplication. PDF uses row
vectors (`p' = p × M`), so concatenating `cm1` then `cm2` must produce
`M1 × M2`, not `M2 × M1`. With a single `cm` the result looked right; with two
nested ones (translation inside a scale, for example) the computed origin was
off by exactly the amount that hides the bug in simple test documents.

### 10.3 A word's position was compared against one corner of its box

Matching used `(word_rect[0], word_rect[3])` — the bottom-left corner of the
rendered box — and the run origin. For horizontal text that is close enough;
for rotated text the glyph box extends *left* of the baseline, and for text
under a scale the descent is scaled too. The error was small but the tolerance
was unbounded, so a run could be selected from a completely different part of
the page as long as its text matched.

### 10.4 Repeated words could not be distinguished

`locate_operator_for_word` took the first textual match inside a run, and
`_replace_in_operands` replaced the first match in the operator. A page with
"the cat and the dog" edited the *first* "the" no matter which one was
clicked, silently changing the wrong word. The engine also had no way to say
which occurrence it meant, so a refusal was the only safe behaviour it could
have offered.

### 10.5 Form XObjects were invisible to the engine

`_iter_page_runs` only read the page's content streams. Text drawn by a Form
XObject (`/Fm1 Do`) was never seen, so `apply()` reported "could not locate the
text … may be inside an unsupported structure (Form XObject …)" even when the
word was plainly visible. There was no code path that could resolve an
XObject's own `/Resources`, follow its `/Matrix`, or edit its stream — and
nothing that would have noticed if the same XObject were invoked twice.

### 10.6 Text extraction truncated at the first unknown advance

While rewriting the replay, `_operator_char_offsets` returned early when a
glyph advance could not be computed. That truncated the extracted text to the
characters before the first unknown glyph ("the quick brown fox" became "t"),
so the word was never found and the *font-inspection* refusal messages were
never reached. The fix records the failure but keeps every character, so
location still works and validation reports the real reason.

## 11. Phase 4 audit addendum (2026-11-12)

A fourth review asked a different question: *where exactly does the engine give
up when the original font cannot draw the replacement?* The audit traced every
`return` in `_validate_replacement`, the advance machinery used for geometry,
and the font-discovery/embedding primitives that did not yet exist.

### 11.1 The refusal points were identified precisely

`_validate_replacement` (`text_edit_existing.py`) refuses in four places:

| # | Condition | Message / effect |
|---|---|---|
| a | `_build_font_codec` returns `None` | "Cannot verify how characters are encoded…" |
| b | `_load_font_program` returns `None` | "Could not load the font program…" |
| c | the glyph is absent from the program | "Character … has no glyph…" / "…is not available…" |
| d | `codec.first_unencodable(text)` finds a character | "…cannot be encoded through the existing encoding…" |

Cases (c) and (d) are the ones that a substitute font can solve.  Before this
phase there was no continuation: the edit simply failed.  The audit also
confirmed that `_diagnose_font_issue` could only describe the *first* problem,
not the complete set of missing characters the UI needs.

### 11.2 Type0 advances were computed with the wrong API

`fitz.Font.glyph_advance()` takes a **Unicode code point**, not a glyph id.
`_advance_fn` passed the glyph id (`glyph_advance(cid)`), so for Identity-H
fonts it returned the advance of an unrelated code point (usually 0).  Every
computed character position in a Type0 font was therefore wrong, which also
made the new width-correction logic impossible to trust.  The function now
checks `has_glyph(ord(ch))` first and only then calls
`glyph_advance(ord(ch))`.

### 11.3 `_validate_replacement` discarded the codec on failure

All post-codec failure paths returned `(False, reason, None)`.  Substitution
needs the *original* codec to re-encode the text around the replaced word
(prefix/suffix).  With `None`, the substitution path could never run and would
have refused with "the encoding … cannot be verified".  The codec is now
returned on every path after it has been built.

### 11.4 The width correction had the wrong sign

The correction that keeps following text in place was computed as
`(original - new)`, but a positive `TJ` number moves the pen *backwards*.  A
wider replacement therefore pushed the suffix by the extra width *twice*
instead of not at all.  A test with "mundo tail" on one line caught it: the
tail moved 25.6 pt.  The correction is now `(new - original)`, and the tail
stays within 1 pt.

### 11.5 Fontconfig metadata is not authoritative

`fc-list` reported "Arial Black" with weight 400, and some families expose a
style string that does not contain the weight word.  Ranking by the fontconfig
values alone selected thin/hairline faces as substitutes for regular text.  The
ranker now re-reads `OS/2.usWeightClass`, `fsSelection`, `post.isFixedPitch`
and the Panose family type from the font file and lets those override the
fontconfig hints.

### 11.6 Family normalisation dropped real name words

`normalize_family("Arial Black")` produced `"arial"`, so "Arial Black" scored
as an exact match for Arial and outranked regular Arial.  The function now
removes a trailing word only when the face's own style string contains it:
`"Helvetica-Bold"` + style `"Bold"` → `"helvetica"`, while
`"Arial Black"` + style `"Regular"` keeps its name.

### 11.7 Advance units were multiplied by 1000

`face_advances` returned `hmtx[name][0] * 1000 / upem`, i.e. 1000-unit glyph
space, while the query and the width-ratio maths expect em units.  Width ratios
of 1261 % were observed.  It now returns `hmtx[name][0] / upem`.

### 11.8 Resource dictionaries can be inline or shared

The embedding writer must add a `/Font` entry to the object that owns the text
(page or Form XObject).  That dictionary may be indirect, a direct
sub-dictionary, or shared with another page.  Writing into it directly would
leak the font into unrelated content or fail.  `font_embed._font_dict_target`
now clones an indirect `/Resources` and an indirect `/Font` first, parses
inline dictionaries with a depth-aware scanner, and falls back to copying
inherited resources into a private object.

## 12. Phase 5 addendum — Intelligent paragraph reflow (2026-11-12)

### 12.1 What was added

A separate, layout-aware paragraph editing feature on top of the surgical
word-edit engine:

- **Paragraph model** (`src/pdfar/paragraph.py`): extracts page-wide text lines
  (grouping across the blocks PyMuPDF splits for centred/right-aligned text),
  distinguishes real paragraph boundaries from visual line groupings using
  geometric + typographic evidence (shared body edges, leading, consistent
  font/size/colour), exposes the writing direction so rotated/vertical text is
  handled, and reports a confidence + problems list.
- **Line-breaking / layout engine** (`plan_reflow`): a greedy, metric-accurate
  wrapper that uses the *actual font advances* (PDF `/Widths`/`/W` tables first,
  font program second) rather than character counts; supports left/center/right/
  justified alignment, first-line indent, explicit newlines as hard breaks, and
  optional font-size reduction or widening into free space — both only with
  explicit user approval.
- **Collision / overflow detection**: scans the page for neighbouring text,
  images, drawings and the page boundary; refuses (does not silently overwrite)
  when the reflowed text would overlap a neighbour or run off the page, unless
  the user explicitly approves shrinking.
- **The edit** (`src/pdfar/paragraph_edit.py::ReflowParagraph`): one transaction;
  re-extracts the paragraph on the session clone, re-runs the *same* planner the
  preview used, and rewrites the paragraph's `BT … ET` block as a unit. Colours,
  clipping, transforms, Form XObjects (copy-on-write) and every other operator
  are untouched.
- **Editor + preview** (`src/pdfar/paragraph_dialog.py`): a dialog that edits the
  paragraph as ordinary text, lets the user adjust the line range, choose
  alignment, approve shrink/widen, and pick a substitute font; a live preview
  paints the original outline, the new lines at their real baselines, the new
  bounding box, and any collisions/overflow, so preview == commit.
- **Viewer wiring** (`src/pdfar/viewer.py`): Ctrl+click or the right-click
  "Edit paragraph…" menu in the Edit-Text tool opens the dialog; rejection
  falls back to the word editor.

### 12.2 Safety properties preserved

- Undo/redo, save/revert, dirty-state and rendering invalidation all work
  unchanged because `ReflowParagraph` is a normal `Scope.CONTENT` `Edit`.
- A paragraph edit refuses (with an explanation) when: the page content changed
  since selection (anchor mismatch), the paragraph spans streams, it is drawn by
  a shared/nested Form XObject, its lines use different transforms, or it has
  mixed fonts/sizes/colours / non-fill render mode / unrecoverable text matrix.
- Preview and commit cannot disagree: both call `plan_reflow` with the same
  options, font and region.

### 12.3 Known limitations (Phase 5)

- A paragraph reflow stays inside the paragraph's original region by default; it
  does **not** re-flow the whole page, move other paragraphs, handle multi-column
  or table text, or place text on a new page. OCR is a separate pipeline.
- Multi-line paragraphs rotated via an enclosing `cm` (graphics-state rotation)
  are recognised as rotated only when the text matrix itself carries the
  rotation; a `cm`-only rotated multi-line block is currently detected as
  horizontal for collision/overflow purposes (the writer still reproduces the
  rotation via the preserved `cm`, so the output is correct, but fit checking is
  approximate). Single-line rotated paragraphs are handled fully.
- Real-world refusals (genuine mixed styles, math spans, unmapped glyphs) are
  expected and explained, not silently "fixed".
