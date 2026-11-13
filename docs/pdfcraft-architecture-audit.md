# Phase 6 — PdfCraft text-editing architecture: audit and adoption proposal

Status: **proposal only — no migration performed.** Nothing in this document has
been implemented. It is written for review and approval before any large-scale
change.

Date: 2026-11-13. Repository: `feature/pdfcraft-editiong`.

---

## 0. Executive summary

PDFAR's current paragraph editor (`paragraph.py`, `paragraph_edit.py`,
`paragraph_dialog.py`, plus the word engine in `text_edit_existing.py`) has
failed repeated real-world manual tests even after Phases 5.3 and 5.4. The
strategic decision is to stop patching that reconstruction and adopt the
architecture PdfCraft uses for editing existing text.

**Recommendation: Option C — a hybrid.** Keep PDFAR's PyQt6 UI,
`DocumentSession`, `Edit`, `Scope.CONTENT`, copy-and-adopt, undo/redo,
dirty-state, save/revert, revision/snapshot and render-invalidation exactly as
they are. Replace only the *text-editing engine* with a Rust component built
from PdfCraft's `pdfcraft-edit` crate, exposed to Python through PyO3 with a
narrow, document-oriented boundary (PDF bytes in, edited PDF bytes out, plus
read-only analysis and layout calls).

Do **not** port the algorithm to Python (Option A). The engine is roughly
300 KB of intricate Rust across content interpretation, line/paragraph
grouping, font shaping and content-stream rewriting; a faithful Python port
would be a multi-thousand-line reimplementation carrying the same class of
subtle failures that produced the current situation. Do **not** link the whole
PdfCraft workspace (Option B as "Rust integration" in the broad sense) — the
workspace drags in egui/craft UI git dependencies that PDFAR does not need.

The main adoption cost is build/packaging (the crates are not on crates.io and
the workspace uses inheritance), not algorithmic risk. That is addressed in
§3 and §6.

---

## 1. Local PdfCraft source versus upstream

### 1.1 State after the sync

| | Before this phase (paused snapshot) | After sync (current) |
|---|---|---|
| HEAD | `1afbc125` | `8034990072fcc745d7af54bd52f27fbe029aafaa` |
| Date | 2026-11-08 | 2026-11-13 |
| Version | 0.18.1 | **0.21.1** |
| Upstream `storytold/pdfcraft` HEAD | — | **same `8034990`** (verified via GitHub API) |

The local checkout is now **exactly at upstream HEAD**. The earlier 0.18.1
snapshot was five days and three minor versions behind.

### 1.2 What changed between 0.18.1 and 0.21.1 (relevant to editing)

The core editing architecture is **stable**; the changes are additive and
correctness-oriented:

- New modules: `crates/edit/src/colour.rs`, `convert_colours.rs`,
  `page_streams.rs`, `printer_marks.rs`, `text_regions/ocr_words.rs`,
  `find_replace_rows_tests.rs`.
- Growth in the core files: `text/lines.rs` 35 KB → 44 KB, `text/rewrite.rs`
  59 KB → 65 KB, `text/interp.rs` 45 KB → 57 KB, `text/fallback.rs` 34 KB →
  39 KB.
- `docs/editing-existing-text.md` 9.5 KB → 17 KB. New material includes
  single-style Keep-source-fonts fallback (#2750), Unicode row breaks
  (U+2028/U+2029/U+0085/VT/FF/U+001C–1E), word-level OCR cover/redraw, and
  colour conversion.
- The files the Phase 6 brief named (`lines.rs`, `rewrite.rs`) are present but
  under `crates/edit/src/text/` (not `crates/edit/src/`). `text_session.rs`
  remains at `crates/edit/src/text_session.rs`.

**Conclusion:** the architecture described below reflects the current upstream
0.21.1, not the stale 0.18.1 snapshot.

---

## 2. The exact files and algorithms responsible for existing-text editing

### 2.1 Reading and line detection

| File | Responsibility |
|---|---|
| `crates/content/src/*` | PDF content tokenizer/serializer: `parse`, `serialize_ops`, `Pieces`, `Op`, `Matrix`. Inline images end where the renderer ends them. |
| `crates/cos/src/*` | COS object model: `Document`, `Dict`, `Object`, `Stream`, `ReadState`, incremental/full writers. |
| `crates/edit/src/text/interp.rs` | Interprets the operator stream: graphics/text state, `q/Q`, `cm`, `Tm/Td/TD/T*`, `Tf/Tc/Tw/Tz/Ts/Tr`, clip regions, paint, `BT/ET`, `Do` forms, `'`/`"` side effects. Produces `Origin`, `TextState`, `Paint`, `Clip`, `Shown`. |
| `crates/edit/src/text/lines.rs` | `text_lines(doc, page)` → `Vec<TextLine>`; joins page `/Contents` (`content_streams`, `joined`), reads form XObjects to a bounded depth (`MAX_FORM_DEPTH`, `MAX_FORM_VISITS`), recovers `TJ`/kerning/glyph advances (`dropped_advances`), word ink boxes, run boundaries, bidi/ActualText, soft/hard breaks. Also `content_streams`, `splice`, `rewritten_contents`, `reopen_text_object`. |
| `crates/edit/src/text.rs` | `TextLine` model (text, rect, ink, font/base_font/size/bold/italic/color, stream + operator indices, `origin`, `scope`, `paint`, `runs`, break markers). Module doc states the core strategy. |

### 2.2 Paragraph / region grouping

| File | Responsibility |
|---|---|
| `crates/edit/src/text_regions.rs` | Dispatcher: `group_blocks`, `alignment_frame`, `is_centred`, `rewrite_changed_words`, `rewrite_styled_block`. |
| `crates/edit/src/text_regions/grouping.rs` | `group_blocks(lines)`: groups lines into `TextBlock`s. `same_plane` (scope + paint + orientation), row grouping by baseline/geometry, `neighbour_left`, `alignment_frame` (column detection), centred detection. |
| `crates/edit/src/text/blocks.rs` | `TextBlock` model (`text`, `rect`, style, `rotation`, `lines`, `rows`, `editor_text`, `reflow_text`, `spans`, `invisible`), `reading_blocks`, `text_blocks`, `BlockKey` (text + box identity), `edited_spans` (word-level edit script preserving untouched runs), `wrap`, `row_break`, `paragraph_separator`. |
| `crates/edit/src/text_regions/styled.rs` | Mixed-style region rewriting (`rewrite_styled_block`), span/style mapping. |
| `crates/edit/src/text_regions/ocr_words.rs` | Invisible/OCR text word grouping and word-level cover/redraw. |

### 2.3 Session lifecycle and staleness

| File | Responsibility |
|---|---|
| `crates/edit/src/text_session.rs` | `TextSession::open(doc, page, block)`: reads the page once (`text_lines`, `group_blocks`), clones the document (`base: Document`), records `base_state: pdfcraft_cos::ReadState`. `layout(text, style)` lays a draft on the **copy** (no mutation). `commit(doc, …)` refuses when `!base_state.matches(doc)` ("the paragraph changed while it was being edited") and calls `rewrite_block_from`. `is_current(doc)` exposes the staleness test. |
| `crates/engine/src/text_preview.rs` | `Document::text_edit_layout` (measure without writing), `text_editor_fonts`, `text_edit_preview` (clone COS → `clear_block` → incremental bytes → render background), `added_text_preview`, `without_marks`, `added_content_alone`. |
| `crates/engine/src/edit.rs` | The `Edit` enum and edit dispatch/permissions. |
| `crates/engine/src/session.rs` | `Session` (open documents), save (`write_full`/`write_incremental`), undo/history, render pools. |

### 2.4 Layout, preview and rewriting

| File | Responsibility |
|---|---|
| `crates/edit/src/text/rewrite.rs` | `replace_line`, `rewrite_block`, `rewrite_block_from` (`:852`), `replace_line_with_limit`, `rewrite_block_with_limit`; `choose_rewrite_path` (single-style vs styled), `paragraph_text`, `ParaGeometry` (width/alignment/anchor), `coincident_ops` (drop duplicate lines on the same baseline), `refuse_clipping_text`, `show_starts`, `translate_blocks`, `reveal_ops`, `selectable_blocks`. |
| `crates/edit/src/text/fallback.rs` | Source-font reuse and substitution: `source_show`, `source_encodes`, `source_advance`, `source_fallback_runs`, Type 3 fallback generation (`type3_font`, `write_type3_glyphs`, `MAX_TYPE3_GLYPHS`), subset-tag handling, unused-fallback cleanup. |
| `crates/edit/src/text/bidi.rs`, `indic.rs`, `linebreak.rs`, `paragraph_align.rs` | Reading order, Indic visual order, line breaking (UAX #14 / JIS X 4051), paragraph alignment. |
| `crates/fonts/src/*` | `Metrics`, sfnt parsing (skrifa), shaping (harfrust), embedding, source shaping, licence gating. |

### 2.5 The algorithm in one paragraph

A *line* is a run of compatible text-showing operators on one baseline in one
content stream. `text_lines` reads every page stream (and bounded form
XObjects), interpreting the full graphics/text state so each line carries its
text, ink boxes, font resource, size, colour, transform, paint and the exact
operator indices that drew it. `group_blocks` groups compatible lines into a
`TextBlock` (paragraph) by geometry, paint, scope and style. `TextSession`
snapshots the document and the block. `layout` runs the real rewrite on a
**copy** to measure a draft; `commit` re-checks that the document is unchanged
and then `rewrite_block_from` rewrites **only the rows whose characters changed
or that moved** (or all rows when the paragraph's formatting/width/position
changed), keeping every untouched row's original operators, `TJ` adjustments
and kerning byte-for-byte. The preview background is produced by cloning the
COS, calling `clear_block` (removing the source glyphs) and writing incremental
bytes, so the editor's text is not drawn twice.

---

## 3. Dependency graph for the Rust integration option

### 3.1 Edit-subtree graph (normal dependencies)

```
pdfcraft-edit
├─ pdfcraft-annot ── pdfcraft-cos, pdfcraft-fonts, pdfcraft-geom, pdfcraft-model, thiserror
├─ pdfcraft-cos ───── pdfcraft-crypt, pdfcraft-filters, thiserror, chrono
│   ├─ pdfcraft-crypt ── aes, md-5, stringprep, …
│   └─ pdfcraft-filters ── (flate/lzw/… decoders)
├─ pdfcraft-content ─ pdfcraft-cos, hayro-syntax
├─ pdfcraft-fonts ─── pdfcraft-cos, skrifa, harfrust, unicode-bidi
├─ pdfcraft-model ─── pdfcraft-cos
├─ thiserror, sha2, kurbo, unicode-bidi, icu_segmenter
```

External crates in the subtree are all **crates.io version dependencies**
(no git dependencies): `aes`, `md-5`, `stringprep`, `chrono`, `hayro-syntax`,
`skrifa`, `harfrust`, `unicode-bidi`, `icu_segmenter`, `sha2`, `kurbo`,
`thiserror`.

### 3.2 The real blocker: workspace inheritance, not the edit crate

The PdfCraft workspace (`external/pdfcraft/Cargo.toml`) is 35 crates with
`Cargo.lock` = 810 packages. The UI crates pull **git dependencies**:

- `craft-ui`, `craft-quit`, `craft-menu`, `craft-keymap`, `craft-i18n`,
  `craft-print`, `craft-docpath`, `craft-update` from
  `https://github.com/storytold/craft-libs`
- an egui fork (`ecolor`, …) from `https://github.com/storytold/egui.git`

The edit subtree itself has **no git dependencies**, but because its manifests
use `version.workspace = true` / `[workspace.dependencies]`, Cargo resolves the
whole workspace. Verified: `cargo tree -p pdfcraft-edit --offline` fails with
`failed to load source for dependency 'ecolor' … can't checkout … offline`.

`pdfcraft-edit` is **not published on crates.io** (verified: crates.io returns
"crate `pdfcraft-edit` does not exist"), so a version dependency is not
available.

### 3.3 Viable build strategies (in order of preference)

1. **Vendored subtree in PDFAR's own Rust workspace (recommended).**
   Copy `crates/{edit,cos,crypt,filters,content,fonts,model,annot,geom}` into
   `rust/pdfcraft/` under PDFAR, mechanically replace `*.workspace = true` with
   explicit values, and inline the needed `[workspace.dependencies]`. Add a
   `pyext` crate that depends on `pdfcraft-edit` and PyO3. This is a one-time
   scriptable transform; no egui/craft deps; fully offline; pinned to a commit.
   Cost: a vendored fork to track upstream. Licence permits it with attribution
   (§8).
2. **Copy the workspace and add a `pyext` member.**
   Copy the whole `external/pdfcraft` to a build directory, add
   `crates/pyext` to `members`, build `-p pdfcraft-pyext`. Cargo still needs
   the git deps present for resolution (network at build time), but does not
   compile egui for the pyext target. Keeps upstream layout; heavier and
   network-dependent.
3. **Add `pyext` inside `external/pdfcraft/`.**
   Simplest technically, but **violates AGENTS.md** ("no files inside
   `external/pdfcraft/` are modified"). Not recommended.

The PoC (§7) should validate strategy 1 first; strategy 2 is the fallback.

### 3.4 Toolchain

`rustc 1.99.1`, `cargo 1.99.1` are installed. The workspace requires
`rust-version = 1.95`, edition 2024 — satisfied.

---

## 4. Python-port versus Rust-integration

| Criterion | A. Faithful Python port | B. Full Rust app | C. Hybrid (recommended) |
|---|---|---|---|
| Reuse of PdfCraft code | None (reimplementation) | Full | Full engine, PDFAR UI |
| Correctness risk | High — re-derives ~300 KB of subtle logic | Low | Low |
| Effort | Very high (months) | Very high (rewrite app) | Medium (engine + adapter) |
| Preserves PDFAR architecture | Yes | No | Yes |
| Build/packaging | None | Replaces the app | PyO3 extension + wheels |
| Performance | PyMuPDF-bound | Native | Native engine, PyMuPDF render |
| Maintenance | Own the port forever | Own the fork of the app | Track upstream `pdfcraft-edit` |
| Licence | n/a (own code) | MIT/Apache + GPL app | MIT/Apache component + GPL app |

The Python port is rejected because it repeats the failure mode: the current
engine already is a large, hand-written reconstruction, and the PdfCraft
algorithm's value is precisely in the details (per-operator preservation,
graphics-state interpretation, shaping, fallback). Reimplementing those in
Python is not a smaller risk than the current code.

A full Rust application is rejected because the brief explicitly requires
preserving PDFAR's UI, session, edits and save/undo infrastructure.

---

## 5. Recommended architecture for PDFAR

### 5.1 Components

```
┌──────────────────────────── PDFAR (Python / PyQt6) ───────────────────────────┐
│  viewer.py / paragraph_dialog.py        (UI only: selection, dialog, preview)  │
│        │                                                                       │
│  pdfar/textengine.py  (thin Python wrapper around the native module)          │
│        │  analyze(pdf_bytes, page) -> [BlockInfo]                             │
│        │  layout(pdf_bytes, page, key, text, style) -> LayoutInfo              │
│        │  clear_block(pdf_bytes, page, key) -> preview_pdf_bytes              │
│        │  rewrite(pdf_bytes, page, key, text, style) -> new_pdf_bytes         │
│        │                                                                       │
│  pdfar/edits.py  TextEdit(Edit)  ── Scope.CONTENT ── uses rewrite()           │
│        │                                                                       │
│  pdfar/session.py DocumentSession (clone → apply → persist → adopt)            │
└───────────────────────────────────┬───────────────────────────────────────────┘
                                    │ PyO3
┌───────────────────────────────────▼───────────────────────────────────────────┐
│  pdfar_textengine (Rust cdylib)                                                │
│    pdfcraft-edit (text lines, grouping, TextSession, rewrite)                  │
│    pdfcraft-cos / content / fonts / model / annot                              │
└───────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Boundary

The engine is **document-oriented**: it accepts a complete PDF byte string and
returns a complete PDF byte string (or read-only analysis). This avoids
exposing PdfCraft's COS types across the FFI and keeps the two object models
(PyMuPDF and `pdfcraft-cos`) from leaking into each other.

- `analyze(bytes, page) -> [BlockInfo]` replaces `extract_paragraphs` /
  `paragraph_for_word`. Each `BlockInfo` carries a stable `BlockKey`
  (text + rect + rotation) and the display text/rect/style, mirroring
  `TextBlock`/`BlockKey`.
- `layout(bytes, page, key, text, style) -> LayoutInfo` replaces the dialog's
  `plan_reflow`/`_replan` preview. It runs on a clone internally, so it cannot
  mutate the document.
- `clear_block(bytes, page, key) -> preview_bytes` replaces the "textless
  preview" need (PdfCraft clones the COS and removes the source glyphs).
- `rewrite(bytes, page, key, text, style) -> new_bytes` performs the commit.
  Internally it opens a `TextSession`, checks staleness against the bytes it
  was given, and rewrites only the changed rows.

### 5.3 How it plugs into `DocumentSession` without bypassing anything

`DocumentSession.apply` already: snapshots (`_before_snapshot`), opens a
separate working copy (`_open_working_copy`), calls `edit.apply(clone)`,
persists (`_persist`), adopts (`_adopt`), records history and bumps the
version. To keep that untouched, extend the `Edit` contract minimally:

- Add an optional `Edit.apply_document(self, data: bytes) -> Optional[bytes]`
  (default `None`). `DocumentSession.apply` checks it **before**
  `edit.apply(clone)`; when it returns bytes, those bytes are written to the
  working copy and the clone is reopened from them, then the normal
  `_persist`/`_adopt`/history path continues.
- `TextEdit` implements `apply_document` by calling `rewrite(doc.tobytes(), …)`.
  It is still `Scope.CONTENT`, still recorded in history, still undoable, still
  saved/reverted by the existing machinery. Nothing bypasses clone/persist/
  adopt.
- For the PoC, whole-document bytes are acceptable. A later optimization can
  replace `apply_document` with a stream/object **patch** (list of
  `(xref, bytes)` plus new objects and page-resource changes) applied through
  `doc.update_stream`/`doc.xref_set_key`, preserving incremental save and
  object identity. This is a performance refinement, not a correctness need.

### 5.4 Preview and rendering

The dialog's preview should use `clear_block` on the current bytes to render a
textless background, then draw the editor's draft text in the existing
overlay/preview layer. This matches PdfCraft's "textless preview prevents a
second copy underneath" and fits PDFAR's render pipeline (invalidate the page,
render the preview bytes). No duplicate text, and the committed output comes
from the same `rewrite` call the preview measured.

### 5.5 Selection identity

Use PdfCraft's `BlockKey` (text + box + rotation) to re-find the paragraph at
commit time, plus the session's own revision/version check. The dialog stores
the key; `rewrite` refuses if the key no longer matches (the document changed
or the paragraph was merged/split), mirroring `stale_paragraphs`.

---

## 6. Migration plan (incremental milestones)

Each milestone is independently shippable and reversible behind a feature flag.

- **M0 — Build spike (no app changes).** Produce `pdfar_textengine` from the
  vendored subtree; call `analyze`/`rewrite` from a standalone Python script on
  a handful of PDFs. Exit: the extension imports and edits one paragraph.
- **M1 — Engine API.** Implement `analyze`, `layout`, `clear_block`, `rewrite`,
  `BlockKey`, `BlockInfo`, `LayoutInfo`, `Style` over PyO3. Unit tests in Rust
  and Python.
- **M2 — Adapter + `TextEdit`.** Add `pdfar/textengine.py` and
  `TextEdit(Edit)`; extend `Edit`/`DocumentSession` with `apply_document`
  behind a flag. Prove undo/redo, dirty-state, save/revert, Save As and
  revision snapshots are unchanged.
- **M3 — Detection swap.** `paragraph_for_word` → `analyze` behind
  `PDFAR_TEXT_ENGINE=1`; keep the old detector as fallback. Compare block
  boundaries on the corpus.
- **M4 — Dialog swap.** Replace `plan_reflow`/`_replan` with `layout`; replace
  the preview with `clear_block`. Keep the old dialog path behind the flag.
- **M5 — Commit swap.** `ReflowParagraph` → `TextEdit` behind the flag. Verify
  the full GUI flow: select → edit → Apply → save → close → reopen → render.
- **M6 — Corpus validation.** Run the PoC matrix (§7) plus the existing test
  suite; independent readers (`pdftotext`, `pdftoppm`, PyMuPDF render, qpdf
  check, optionally PDFium) validate text and rendering.
- **M7 — Retire or retain the old engine.** Decision point: delete
  `paragraph_edit.py`/`paragraph.py` once the new path passes; or retain them
  as a fallback for structures the engine refuses (documented). Do **not**
  maintain two paragraph editors indefinitely.

---

## 7. Proof-of-concept plan and acceptance criteria

### 7.1 Harness

A standalone script (`tools/pdfcraft_poc.py`, not part of the app) plus a
fixture directory. It drives the native module directly, and a second mode
drives the real `PDFView`/`ParagraphEditorDialog` once M4/M5 exist.

### 7.2 Required capabilities (all must pass)

1. Select a multiline paragraph.
2. Edit an existing word.
3. Add a word at the end.
4. Change paragraph length (grow/shrink, rewrap).
5. Preserve adjacent paragraphs.
6. Preserve unchanged lines and their original operators (byte comparison of
   untouched rows).
7. Handle font substitution (embedded subset lacking a glyph; source reuse).
8. Save the document.
9. Close and reopen it.
10. Verify resulting text **and** visual rendering (pixel compare within
    tolerance, and independent extraction).

### 7.3 Required PDF structures

- Simple `BT/ET` block, single paragraph.
- Multiple paragraphs sharing one `BT/ET` block (the Phase 5.4 failure).
- Multiple content streams (`/Contents` array).
- Form XObjects (text inside a form).
- Embedded subset fonts.
- `TJ` arrays with nontrivial kerning/word gaps.
- Rotated text.
- Mixed fonts/styles in one paragraph.
- Three or more lines.

### 7.4 Acceptance criteria

- All 10 capabilities pass on all required structures (where the structure is
  applicable), through both the direct engine and the real GUI.
- Untouched rows are byte-identical to the source (operator-level assertion).
- Saved output extracts the expected text in an independent reader and renders
  without the old text showing underneath.
- Undo/redo, dirty-state, Save As, and reopen preserve the expected state.
- No regression in the existing test suite.

---

## 8. Risks, compatibility and licensing

### 8.1 Build and packaging

- The workspace drags egui/craft git deps; **vendor the edit subtree** (§3.3)
  or copy the workspace. Pin a commit (`8034990`, 0.21.1).
- PyO3 extension means platform wheels (Linux/macOS/Windows) and Debian
  packaging changes. Build time and binary size grow (harfrust, skrifa,
  icu_segmenter compiled data).
- Upstream moves fast (0.18 → 0.21 in five days). Pin and budget for periodic
  re-syncs; keep the vendored diff minimal and scripted.

### 8.2 PDF compatibility

- The engine parses with `pdfcraft-cos`, not MuPDF. Risk areas: encrypted
  files, xref/object streams, damaged files, unusual filters, incremental
  updates, linearisation. Validate on the corpus; fall back to the word editor
  or the old path when the engine refuses.
- Whole-document bytes-out re-serialises the file; it may not preserve
  byte-level incremental structure. Correctness is unaffected; incremental
  save is. The patch-based refinement (§5.3) addresses this if needed.
- Behavioural differences in font substitution policy and refusal messages;
  map them to PDFAR's user-facing messages.

### 8.3 Licensing and attribution

- PdfCraft: **MIT OR Apache-2.0**, `LICENSE-MIT`, `LICENSE-APACHE`, `NOTICE`
  (Copyright (c) 2026 ArtCraft Team and the PdfCraft contributors).
  MIT and Apache-2.0 are compatible with PDFAR's GPL-3.0+; the combined work is
  distributed under GPL-3.0+, with the Rust component's MIT/Apache notices
  retained.
- Vendored/copied code must keep copyright and licence texts; document which
  parts are adapted, independently implemented, or linked. Add a
  `THIRD_PARTY_NOTICES`/`CREDITS` entry and the PdfCraft `NOTICE`.
- Third-party material inside PdfCraft that must be accounted for if bundled:
  `edit-session` (MIT/Apache), hayro (Apache-2.0/MIT; BSD-3 fonts/CMaps),
  lopdf (MIT), Lucide icons (ISC/MIT), Inter/JetBrains Mono/Dancing Script
  (OFL-1.1), egui default fonts (Ubuntu/Hack/Noto/OFL/MIT), CGATS profile
  (CC0). **Do not copy** `docs/brand/`, `assets/icons/`, `assets/app-icon/` or
  any trademark/logo asset.
- If only the Rust crates are linked (not copied), their licences still require
  including the licence texts and NOTICE in distributions.
- The `vendor/edit-session` crate is a separate MIT/Apache project by
  PerchAnton; if used, its notices apply.

### 8.4 Security

Rust's memory safety is an advantage for parsing untrusted PDFs. The engine
already has decode budgets (`DecodeBudget`, 16 MiB, depth/visit caps). Keep
those; do not raise them casually.

### 8.5 Rollback

Every milestone is behind `PDFAR_TEXT_ENGINE`; disabling it restores the
current path exactly. Do not delete the old modules until M7 is approved.

---

## 9. Decision requested

Approve **Option C (hybrid)** and the milestone plan, or choose an alternative:

- **A** — faithful Python port (not recommended).
- **B** — full Rust application (out of scope; replaces PDFAR).
- **C** — hybrid Rust engine + PDFAR UI/session (recommended).

No code has been changed. `demo.pdf` remains untouched. Nothing has been
committed or pushed.
