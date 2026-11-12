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

---

## 11. Phase 3 report (2026-11-12) — advanced compatibility

### 11.1 What was implemented

**Priority A — correct graphics-state replay.**
The replay in `_iter_stream_runs()` is now a proper interpreter of the graphics
state:

| Concern | Behaviour |
|---|---|
| `q` / `Q` | Saves and restores the CTM **and** the text state (`Tf`, `Tc`, `Tw`, `Tz`, `TL`, `Ts`, `Tr`). Balanced across content streams and inside Form XObjects. |
| `cm` | Concatenates in PDF row-vector order: `M_new × CTM`. Nested `cm` compose correctly. |
| `Tm` / `Td` / `TD` / `T*` | `Tm` sets both text and line matrix; `Td`/`TD` translate the line matrix; `T*` moves by `TL`; `TD` also sets `TL`. |
| `BT` | Resets `Tm` and `TLM` only (text state is *not* reset, per the spec). |
| `'` / `"` | Move to the next line first, then show; `"` also sets `Tw`/`Tc`. |
| Page mapping | `Tm × CTM × page.transformation_matrix`, which already encodes the y-flip, CropBox offset and page rotation. |

Each text-showing operator gets a `_TextRun` with a per-character offset list
built from real glyph advances (`Font.glyph_advance` / `has_glyph` for simple
fonts, CID → glyph id for Type0), so `point_at(i)` returns the page position of
character *i*. Unknown advances no longer truncate the extracted text; they only
mark the offsets as untrustworthy.

**Priority B — repeated-word disambiguation.**
`_OperatorLoc` carries `occurrence`, the index of the clicked match inside the
operator's text. Location scores every candidate occurrence by the distance from
its computed glyph position to the clicked word's box (distance to the rect, not
to a corner), and applies a tolerance of `max(6pt, 2 × font size)` when the
offsets are known — a run whose text matches but whose position is nowhere near
the click is rejected. Replacement then edits exactly that occurrence:
`_nth_index()` walks to the *n*-th match in `Tj` strings and across `TJ`
segments, and the surrounding kerning numbers are kept.

**Priority C — Form XObjects.**
`Do` operators are resolved against the current resource dictionary. For a Form
XObject:

- its `/Matrix` is composed with the CTM in force at the `Do`;
- its own `/Resources` (when present) replace the page's for everything inside
  it, so a `/F1` in an XObject cannot be confused with a page `/F1`;
- the invocation path is tracked (`_StreamRef.depth`), cycles are cut, and the
  number of invocations per XObject is counted.

Editing an XObject never modifies a shared object in place:

1. the page's `/Resources` is cloned if it is an indirect object (it may be
   inherited from the page tree or shared with other pages);
2. the `/XObject` sub-dictionary is cloned if it is an indirect object;
3. the XObject itself is cloned (`xref_copy` + `update_stream`);
4. the page's private dictionary is repointed at the clone, and only the clone's
   stream is rewritten.

The original object and every other page keep rendering exactly as before.
Because the copy is made *after* the replacement has been computed, a refused
edit leaves the document structurally untouched.

### 11.2 Test results

```
tests/test_existing_text_edit.py        29 passed
tests/test_existing_text_ui.py           5 passed
tests/test_existing_text_visual.py       4 passed
tests/test_existing_text_realworld.py   11 passed
tests/test_existing_text_graphics.py    16 passed   (new)
tests/test_existing_text_xobjects.py    12 passed   (new)
--------------------------------------------------
existing-text total                     77 passed
full suite                           2,937 passed  (0 failed, 0 skipped)
```

Run with `PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest tests/ -q`.
No pre-existing test was weakened or removed. One Phase 2 test
(`test_replace_in_operands_tj_multisegment_ambiguous`) encoded the old
"refuse repeated words" semantics; it was replaced by
`test_replace_in_operands_tj_multisegment_repeated_word`, which checks that
`occurrence=0` edits the first match, `occurrence=1` edits the second, and
`occurrence=2` reports `not_found`.

New coverage in `tests/test_existing_text_graphics.py`:

- nested `q`/`Q` restore, including text state;
- nested `cm` composition (exact origin check);
- `Tm` rotation and `cm` rotation, including an edit that keeps the origin;
- `Tc`/`Tw`/`Tz` saved and restored by `q`/`Q`;
- `Ts` (text rise) offsetting the origin;
- `BT` resetting the text matrix;
- CropBox offset;
- repeated word inside one `Tj` (edit the second only);
- repeated word across two operators on the same page;
- repeated word split across `TJ` segments;
- kerning numbers around the edited occurrence preserved;
- `occurrence` reported by the locator;
- a word whose box is far from the run is not matched;
- an absent word raises `EditError`.

New coverage in `tests/test_existing_text_xobjects.py`:

- locating and editing XObject text;
- `/Matrix` positioning (scale) and position preservation after the edit;
- font resources scoped per object (page `/F1` = Helvetica, XObject `/F1` =
  Times) — both the resolution helper and the end-to-end edit;
- unrelated operators in the XObject preserved byte-for-byte;
- save/reopen;
- shared XObject across pages → copy-on-write, original untouched;
- XObject invoked twice on one page → refused, document unchanged;
- shared `/Resources` dictionary → split, other page unaffected;
- inline page `/Resources` and indirect `/XObject` dictionary shapes;
- nested XObject → refused;
- self-referencing XObject → cycle cut, no hang.

`tests/test_existing_text_visual.py` gained a transformed-text test: a word
drawn under a scale and another under a rotation are rendered before and after;
the edited word's box keeps its left edge and baseline, the rotated word is
pixel-identical, and the edited region's pixel diff is > 3 while the rotated
region stays < 1.

`tests/test_existing_text_realworld.py` gained a real Form XObject test: the
rotated arXiv stamp on page 1 of `arxiv-2610.30749.pdf` is located through the
invocation matrix, edited, saved and reopened.

### 11.3 Supported and refused

| Case | Result |
|---|---|
| Plain page text (`Tj`, `TJ`, `'`, `"`) | **Editable** |
| Text under `q`/`Q`, nested `cm`, scale, translation | **Editable** |
| Text rotated by `Tm` or by `cm` | **Editable** |
| Text in a CropBox-offset or rotated page | **Editable** |
| Word repeated inside one operator | **Editable** — the clicked occurrence only |
| Word split across `TJ` kerning segments | **Editable** |
| Text in a Form XObject invoked once | **Editable** |
| Form XObject shared with other pages | **Editable** via copy-on-write |
| Form XObject invoked twice on the same page | **Refused** (would change every copy) |
| XObject nested inside another XObject | **Refused** (enclosing object cannot be isolated) |
| XObject cycle | **Cut** during replay; edit refused if not locatable |
| Font without ToUnicode (Type0) or with an unknown encoding | **Refused** (fail-closed) |
| Glyph missing from the font program | **Refused** |
| Scanned image / OCR text | **Not supported** (no text operator) |
| Text spanning several operators (e.g. a paragraph) | **Refused** as a span edit |

### 11.4 Safety properties

- **Surgical bytes.** Only the located operator's span is re-serialized; every
  other byte of the stream is copied verbatim. Replacement of untouched
  characters inside a string keeps their original bytes (`_splice_segment`).
- **No whole-stream rewrite.** The splice is `data[:start] + op + data[end:]`.
- **Fail-closed.** Any inspection failure (fonts, encoding, glyph program,
  resource dictionary, XObject structure) is a refusal with a reason, never an
  implicit approval.
- **Atomicity.** All validation and the replacement computation happen before
  any structural change; the copy-on-write clone is created last.
- **No silent sharing.** Shared XObjects are cloned, never rewritten; the
  original stream is verified unchanged by tests.
- **Architecture.** `ReplaceExistingText` is still an `Edit` with
  `Scope.CONTENT`; `DocumentSession.apply` still runs it on a throwaway clone
  and adopts on success; undo/redo, save/revert, geometry and invalidation are
  untouched. No second document manager, no parallel editing system.
- **UI.** Unchanged: single click, double-click for a line, drag for a span,
  inline popover with live preview, apply/cancel. Refusals surface through
  `apply_edit` in the status bar with the engine's message.

### 11.5 Deferred features and recommended approach

| Deferred | Why it is deferred | Recommended approach |
|---|---|---|
| Automatic font substitution | Choosing a substitute changes metrics and appearance; a wrong choice silently reflows the line. | Add an explicit "substitute font" option: load the replacement font, check its advance widths against the original, and let the user confirm the visual difference. Never do it silently. |
| Font embedding for missing glyphs | Requires subsetting a new font and rewriting the font resource dictionary; high risk of producing an unreadable file. | Build a small font-subset writer on top of `fontTools`, embed as Type0/Identity-H with a generated ToUnicode CMap, and validate by re-extracting the text after saving. |
| Paragraph reflow | A visual paragraph is not one PDF object; reflow means re-laying-out text the original producer positioned explicitly. | Out of scope for surgical editing. A separate "reflow document" feature should regenerate text objects wholesale rather than patch them. |
| OCR-based editing | Scanned pages have no text operators; editing pixels is image editing, not PDF text editing. | Separate OCR pipeline that produces an invisible text layer plus an image patch; must be clearly presented as a different operation. |
| Nested Form XObjects | Isolating a shared parent chain needs recursive copy-on-write with cycle handling. | Extend `_copy_on_write_xobject` to clone the parent chain bottom-up; refuse if any link cannot be made private. |

### 11.6 Files changed in Phase 3

- `src/pdfar/text_edit_existing.py` — graphics-state replay, per-character
  offsets, occurrence-based location and replacement, resource-dictionary
  helpers, Form XObject traversal, copy-on-write.
- `tests/test_existing_text_graphics.py` — new (15 tests).
- `tests/test_existing_text_xobjects.py` — new (12 tests).
- `tests/test_existing_text_edit.py` — occurrence semantics test updated.
- `tests/test_existing_text_visual.py` — transformed-text pixel test.
- `tests/test_existing_text_realworld.py` — real Form XObject test.
- `docs/existing-text-edit-audit.md`, `docs/existing-text-edit-report.md`,
  `ROADMAP.md` — this report.

### 11.7 Known limitations and conclusion

Known limitations that remain after Phase 3:

- A replacement is refused when the original font cannot draw or encode it.
  There is no substitution or embedding, by design (see 11.5).
- A Form XObject invoked more than once on a page, or nested inside another
  XObject, is refused rather than edited. This is deliberate: both cases would
  otherwise change text the user did not click.
- A span that crosses text-showing operators (a "paragraph" in the visual
  sense) is refused. Editing never claims that a visual paragraph is a single
  logical PDF object.
- Text inside Type3 fonts, inline images or scanned pages is not editable.
- The engine matches text by decoded content plus computed glyph position. If a
  document's font program is unavailable, only unique words in a run can be
  located; repeated words are refused.

The guiding rule for Phase 3 was the same as for Phase 2: **correctness and
preservation of the original PDF take priority over the number of documents
that can be edited.** Every new capability either proves it can map a word to
exactly one operator occurrence, or refuses with a message the user can act on.
A safe refusal is preferable to a corrupted document or an incorrect text
replacement.

---

## 12. Phase 4 report (2026-11-12) — font substitution and embedding

Phase 4 makes the existing-text editor usable on documents whose font cannot
draw the replacement.  It does **not** replace the editing engine: the same
`ReplaceExistingText` `Edit`, the same surgical operator splice, the same
`DocumentSession` clone-and-adopt cycle.  A new module pair discovers and ranks
system fonts, another embeds the chosen one, and the dialog only appears when
the original font provably cannot encode the text.

### 12.1 Architecture

```
ReplaceExistingText.apply()
        │
        ├─ _validate_replacement() ── ok ──────────────► in-place splice (Phases 1–3)
        │
        └─ not ok, no substitute
                └─ FontSubstitutionRequired(issue) ────► UI
                                                          │
                          find_font_alternatives() ───────┤ font_discovery
                          FontSubstitutionDialog ─────────┤ font_picker
                                                          │
                          user approves FontSubstitution ─┘
        │
        └─ not ok, substitute present
                └─ _apply_substitution()
                        ├─ missing_characters() / embedding_permission()
                        ├─ _split_operator()  (prefix, suffix in the original font)
                        ├─ font_embed.build_font()  (Type0/CIDFontType2/FontFile2/ToUnicode)
                        ├─ attach_font()  (copy-on-write /Font dictionary)
                        └─ prefix + /Fsub Tf + <gids> Tj + [correction] TJ + /Forig Tf + suffix
```

Three new modules:

| Module | Responsibility |
|---|---|
| `font_discovery.py` | enumerate faces (fontconfig, directory-scan fallback), read/caches `fsType`, coverage, weight, slant, family; rank candidates |
| `font_embed.py` | licence gate, TrueType subsetting, Type0/Identity-H writer, ToUnicode CMap, private resource attachment |
| `font_picker.py` | `FontSubstitutionDialog`: explanation, ranked table, preview, width warning, browse |

`text_edit_existing.py` gained only the substitution path
(`FontIssue`, `FontSubstitutionRequired`, `FontSubstitution`,
`_diagnose_font_issue`, `_word_advances`, `_font_query`, `_resolve_operator`,
`_split_operator`, `diagnose_replacement`, `find_font_alternatives`) and the
`substitute` field on the existing `Edit`.  The viewer gained one handler.

### 12.2 Font discovery

- `fc-list --format …` is the primary source (file, index, family, style,
  weight, slant, spacing, format).  When fontconfig is absent or fails, the
  standard font directories are scanned (`/usr/share/fonts`,
  `/usr/local/share/fonts`, `~/.local/share/fonts`, `~/.fonts`).
- Platform-specific enumeration is isolated in `system_faces()`.  A Windows or
  macOS backend only has to yield `FontFace` objects; ranking and embedding are
  platform neutral.
- Per-face metadata is read with **fontTools** when installed (authoritative
  `fsType`, `OS/2`, `post`, cmap, upem) and with PyMuPDF otherwise.  It is
  cached per `(path, index)`; `clear_cache()` exists for tests and font
  installs.
- `FontFace` carries `path`, `index`, `family`, `style`, `weight`, `italic`,
  `monospaced`, `serif`, `outline` (`truetype`/`cff`/`unknown`), `fs_type`,
  `embeddable`, `subset_allowed`, `source`.

### 12.3 Ranking — never family name alone

Hard requirements first: the face must be embeddable, have TrueType outlines,
and contain **every character** of the replacement.  Only survivors are
scored:

| Signal | Effect |
|---|---|
| Same normalised family | +100 |
| Family name contains the original (or vice versa) | +70 |
| Same family group (Helvetica/Arial/DejaVu Sans…, Times/…, Courier/…) | +55 |
| Same serif classification | +20 |
| Italic matches | +25 / −40 |
| Bold matches | +25 / −40 |
| Weight proximity to 400/700 | up to +15 |
| Monospace matches | +10 |
| Width class mismatch (condensed/expanded) | −25 |
| Text width close to the original | up to +30 |

The `OS/2` values override the fontconfig hints, and duplicate installations
(same family/style/weight/italic) are collapsed.  Each `FontMatch` reports its
`score`, `missing` characters, `width_ratio` and licence notes, so the UI can
explain the choice.

### 12.4 Embedding

The writer only handles TrueType outlines (`glyf`), which map cleanly to
`CIDFontType2` + `FontFile2`.  CFF/OpenType is refused with an explanation
rather than written incorrectly.

1. Check `fsType` **before** touching the document:
   `0`/`4`/`8` allow embedding, `2` forbids it, `0x0100` forbids subsetting,
   `0x0200` allows only bitmaps (refused for text).
2. Subset with `fontTools.subset` when permitted; otherwise embed the whole
   program.
3. Re-read the bytes that will actually be embedded: subsetting renumbers glyph
   ids, so the Unicode→GID map and advances come from the final program.
4. Write `/FontFile2` (+`/Length1`), `/FontDescriptor` (flags, bbox, ascent,
   descent, cap height), `/CIDFontType2` with `/CIDToGIDMap /Identity`, `/DW`
   and a real `/W` array, `/ToUnicode`, and a `/Type0` parent with
   `/Encoding /Identity-H`.
5. Attach the resource to a **private** `/Font` dictionary: shared `/Resources`
   and `/Font` objects are cloned first, so the font never appears on another
   page or XObject.

Text is written as two-byte glyph ids; `/ToUnicode` preserves copying and
extraction.

### 12.5 Metrics and layout

- `_word_advances` measures the original word from the per-character pen
  offsets computed during location, in em units.
- The replacement's advance is recomputed from the embedded subset's `hmtx`
  (plus `Tc`, `Tw`, `Tz`).
- A `TJ` correction `(new − original) × 1000 / (size × h_scale)` is inserted
  after the substituted word, so the following text keeps its original x
  position.  A positive TJ number moves the pen back, which is why the sign is
  `new − original`.
- The font size, horizontal scale, rise, colour and render mode are untouched.
  The replacement is **not** squeezed or scaled: if it is wider, the dialog
  says so and the text after it is displaced by the correction, not reflowed.
- No paragraph reflow is attempted.

### 12.6 User interface

`FontSubstitutionDialog` shows:

- the original font name, and whether it is embedded and/or subsetted;
- the exact reason the replacement is blocked, and every missing character
  with its code point;
- a ranked table of fonts (family, style, width delta);
- a live preview rendered with the candidate face, plus a width warning;
- a **Browse…** button that validates coverage, embedding permission and
  TrueType outlines before accepting a file.

Nothing is installed and nothing is written until the user presses **Use this
font**.  Cancelling leaves the document byte-identical.

### 12.7 Supported and refused

| Case | Result |
|---|---|
| Original font covers the replacement | **In-place edit** (no substitution) |
| Original font missing glyphs / encoding | **Substitution offered** |
| Embedded, subsetted Type0/CID font | **Substitution supported** |
| Non-embedded base-14 font | **Substitution supported** (original codec re-encodes the rest) |
| Bold / italic original | **Style-matched substitute** preferred |
| Spanish accents, Latin extended | **Supported** (DejaVu, Liberation, Noto, …) |
| `Tj` and `TJ` operators | **Supported** |
| Text inside a Form XObject | **Supported**; font added to the XObject |
| `'` / `"` show operators | **Refused** (cannot be split without duplicating line movement) |
| Original codec unknown | **Refused** (surrounding text cannot be re-encoded) |
| Chosen font lacks a glyph | **Refused** |
| `fsType` forbids embedding | **Refused** |
| CFF/PostScript outlines | **Refused** (no safe writer in this phase) |
| Bitmap-only embedding permission | **Refused** |
| Paragraph reflow / OCR / scanned pages | **Out of scope** |

### 12.8 Tests

95 new tests, all passing:

| File | Tests | Covers |
|---|---|---|
| `tests/test_font_discovery.py` | 27 | fontconfig parsing and fallback, caching, `fsType` matrix, metadata, coverage, advances, ranking, real-system search |
| `tests/test_font_embedding.py` | 28 | licence gate, Type0 structure, subsetting, ToUnicode, extraction, save/reopen, copy-on-write, CFF/missing-glyph/fontTools refusals |
| `tests/test_existing_text_substitution.py` | 27 | diagnosis, refusal without approval, alternatives, bold/italic, Spanish and Latin-extended, TJ arrays, width correction, XObjects, restricted licence, save/reopen, portability, session undo/redo, viewer dialog |
| `tests/test_font_picker_ui.py` | 13 | dialog contents, selection, accept/cancel, width labels, preview, browse validation |

The substitution fixture is a real subsetted Type0 font: the font program is
re-subsetted with `retain_gids=True` and its `/ToUnicode` is replaced by one
that only maps the visible characters, so the missing glyphs are genuinely
absent rather than simulated by a mock.

**Full suite: 3,032 passing** (2,937 before Phase 4).  No existing test was
deleted or weakened.

### 12.9 Licensing compliance

- `fsType` is read from the font file, not guessed from its name.
- Restricted fonts (`fsType` 2) are never embedded.
- Preview-and-print fonts (`fsType` 4) are embedded as subsets only, keeping
  the document read-only with respect to the font.
- Fonts that forbid subsetting (`0x0100`) are embedded whole.
- The writer never modifies or redistributes the original font file; it
  writes a subset into the user's own document, which is the normal use of an
  embeddable font.
- No font is installed, downloaded or copied to a system directory.

### 12.10 Limitations and deferred work

| Deferred | Why | Recommended approach |
|---|---|---|
| CFF/OpenType embedding | Needs `CIDFontType0` + `FontFile3` and CFF charset handling | Add a CFF writer behind the same `EmbeddedFont` interface; keep refusing until it is verified |
| Variable fonts | Named instances need `fvar`/`gvar` handling | Instantiate a static instance with `fontTools.varLib.instancer`, then embed normally |
| Windows/macOS discovery | Only fontconfig and directory scanning are implemented | Add `DirectWrite`/`CoreText` enumerators that yield `FontFace`; ranking is already platform neutral |
| Paragraph reflow | A visual paragraph is not one PDF object | Separate feature that re-lays-out text, never part of a surgical edit |
| OCR editing | Scanned pages have no text operators | Separate OCR pipeline with an invisible text layer |
| Automatic installation of missing fonts | Would change the user's system without consent | Keep the Browse… path and the explanation; never install silently |

### 12.11 Files changed in Phase 4

- `src/pdfar/font_discovery.py` — new.
- `src/pdfar/font_embed.py` — new.
- `src/pdfar/font_picker.py` — new.
- `src/pdfar/text_edit_existing.py` — diagnosis, substitution path, operator
  splitting, width correction; advance lookup fixed for Type0.
- `src/pdfar/viewer.py` — `FontSubstitutionRequired` handling and the dialog.
- `tests/test_font_discovery.py`, `tests/test_font_embedding.py`,
  `tests/test_existing_text_substitution.py`, `tests/test_font_picker_ui.py` —
  new.
- `docs/existing-text-edit-audit.md`, `docs/existing-text-edit-report.md`,
  `ROADMAP.md` — this report.

### 12.12 Conclusion

Phase 4 closes the last common reason an existing-text edit was refused, without
weakening any of the Phase 1–3 guarantees.  The replacement font is chosen from
evidence (coverage, style, weight, metrics), shown to the user, embedded as a
portable subset with a real ToUnicode map, and attached without touching shared
resources.  Everything that cannot be done safely — unknown encodings, `'`/`"`
operators, CFF fonts, restricted licences, unsupported glyphs — is still
refused with an explanation.

**Correctness, licensing compliance and document integrity take priority over
attempting unsupported font substitutions.**
