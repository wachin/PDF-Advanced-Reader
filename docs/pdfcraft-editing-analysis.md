# Research: PdfCraft Architecture — How It Edits PDFs

> **Source:** source code of `pdfcraft` (Rust workspace, MIT OR Apache-2.0),
> studied while it was available under `external/pdfcraft/`. That directory is
> reference material and is deleted after this study; **every finding needed to
> implement anything is recorded here**, with the original file paths so the
> reasoning stays traceable.
> **Goal:** adopt the techniques that make PDF editing safe and faithful,
> implemented our own way in Python/PyQt6/PyMuPDF.
> **Licence note:** unlike `external/okular/` (GPL), PdfCraft is permissively
> licensed, so even the analysis text here is unproblematic. Our code stays an
> original Python implementation; ideas and algorithms are not copyrightable.

---

## 1. The load-bearing decision: the object graph *is* the model

PdfCraft has no separate "editing model" that is later mapped back to PDF. An
open document holds one `pdfcraft_cos::Document`: the parsed object graph plus a
copy-on-write **overlay** (`crates/cos/src/document.rs`) — a
`BTreeMap<ObjRef, Slot>` of changes. Original bytes are never touched.

Consequences worth copying:

1. **Unknown data survives.** Nothing is re-serialised that the program did not
   understand, so a file with exotic or private structures round-trips.
2. **Dirty state is derived, not tracked by hand:** `is_modified()` is simply
   "the overlay is not empty (or the encryption changed)".
3. **Undo is a whole-document snapshot**, which is only cheap because the graph
   is CoW — cloning shares every unchanged object
   (`crates/engine/src/history.rs:100`,
   `type Snapshot = (Label, Document, Keys, Scope)`, `MAX_UNDO = 100` at `:8`).
4. **An edit never touches the live document.** `Session::apply`
   (`crates/engine/src/session.rs:142`) clones, runs the edit on the clone
   inside a `catch_unwind` guard, and only on success swaps it in and pushes the
   previous graph onto the undo stack. A failing or panicking edit leaves the
   document literally unchanged.

**PDFAR equivalent:** PyMuPDF gives us the same shape for free — a `Document`
opened once, mutated in memory, and `doc.tobytes()` for a snapshot. What we must
add is the discipline: mutate a *copy*, and only adopt it on success.

---

## 2. What you see is what will be saved

After **every** edit PdfCraft performs an incremental write *in memory* and
points the renderer at the result
(`crates/engine/src/session.rs:551` `refresh_content`, `:570` rebuilds the
render pool from the new bytes). The pixels on screen are produced from the same
bytes that Save writes — so a preview can never disagree with the file.

Invalidation is graded, not global. Each edit declares a `Scope`
(`crates/engine/src/history.rs:21`: `Full | Metadata | Comments | Form | Links |
Content`) and the refresh only rebuilds what that scope can have changed;
`Scope::Content` skips re-inspecting the page tree, annotations, fields, outline
and fonts entirely.

**PDFAR equivalent — this was our biggest structural gap, closed by F10-0.**
Before F10-0 the authoritative document was `PDFView.doc`
(`src/pdfar/viewer.py:150`), but four readers re-opened the file **from disk** in
their own threads:

| Worker | Used to open |
|---|---|
| `RenderPool` (`src/pdfar/render.py`) | `fitz.open(self.doc_path)` |
| `SearchWorker` (`src/pdfar/search.py`) | `fitz.open(self.doc_path)` |
| `ReflowWorker` (`src/pdfar/reflow.py`) | `fitz.open(self.doc_path)` |
| Document Properties (`src/pdfar/main.py`) | `fitz.open(path)` |

That was correct only while the app barely mutates the document and would be
**wrong the moment editing exists**: the page would render the saved file, not
the edited one. It is also unavoidable in principle — MuPDF is not thread-safe,
so one `Document` cannot be shared across threads. The pattern that resolves
both is implemented in **`src/pdfar/session.py`**: the GUI thread owns the
document and, per accepted change, a `version` counter; background readers get
`session.open_copy(version)` — the file on disk while `version == 0`, an
in-memory `doc.tobytes()` snapshot afterwards. The snapshot is serialized on the
owning thread only (`read_version()`); a worker that asks for unpublished
content gets `SnapshotNotPublished` instead of a data race. Render jobs and
results are stamped with the version, so a pre-edit result cannot be cached
after the edit.

The page cache key deliberately did **not** gain a version dimension
(it stays `(page, round(zoom,4), rot%360, tag)`, `src/pdfar/cache.py:40`):
`PDFView.invalidate_content()` drops everything derived from the previous
content instead. Keeping several generations of the same page in a
byte-budgeted cache would trade a clear-on-change cost for pages the user will
never look at again.

---

## 3. The edit taxonomy

Every mutation is one value of a single data-carrying enum,
`enum Edit { … }` (`crates/engine/src/edit.rs:14`, 93 variants), dispatched by
`run_edit(&mut Document, &Edit, &EditCtx)` (`:1113`) to the owning crate.
`Edit::Batch { label, edits }` makes several edits one undoable step. The enum
carries *intent* (`RotatePages { pages, delta }`), not before/after values, and
`Edit::label()` feeds the undo menu.

That enum doubles as the **feature map of a complete PDF editor**. Grouped:

| Family | Operations |
|---|---|
| Pages | rotate, delete, move, reverse, duplicate, replace, insert blank, insert-from-file, extract, split, combine, set page box, transition, page numbering |
| Text/images | edit existing text line/block, add text, add image, edit image (transform/crop/replace/delete), move several objects, update/delete content, hide content |
| Annotations | add any subtype, move, resize, reshape, style, fill, font size, contents, reply, review status, lock, delete (with `/IRT` reply + popup closure), erase ink, stamps incl. custom |
| Forms | set value, reset, add field, field props, field image, duplicate/delete field, tab order, calculation order |
| Structure | bookmarks add/rename/delete/move/recolour, bookmarks-from-structure tree, auto-tag, alt text, tag properties |
| Marks | header/footer (+update/remove), watermark, background, Bates, remove marks |
| Redaction/security | redaction mark/apply/clear, sanitize (9 hidden-data categories), passwords, permissions, unlock, remove standard ID |
| Standards/signatures | PDF/A conversion, digital sign, certify, signature panel |
| Other | attachments, document/field JavaScript, measurement scale, OCR text layer, accessibility |

Above it sits a **command registry** (`crates/engine/src/commands.rs:165-402`,
~184 commands) with a `Needs` permission gate per command (`Modification`,
`Annotate`, `FillForms`, `HasRedactions`, …) — menus, shortcuts, the command
palette *and* the headless CLI/MCP tools are all generated from it, so no
feature can exist in the UI without also existing headlessly.

---

## 4. Saving: incremental by default, atomic always

Two pure writers in `crates/cos/src/writer.rs` (both return bytes, neither
touches the filesystem):

- `write_incremental` (`:199`) appends only the overlay's changed objects plus a
  new xref section and a trailer with `/Prev`. Original bytes stay untouched,
  which is what keeps **signatures valid**. It auto-falls back to a full write
  when the file was repaired on load, the encryption changed, or an operation
  demanded a full save.
- `write_full` (`:270`) does a reachability walk from the trailer, dense
  renumbering and object streams — the garbage-collected path, used for Save As
  to another file, redaction, PDF/A, password changes.

Around that:

- **Atomic replace** (`crates/platform/src/staging.rs:139`): write a random
  hidden `.{name}.pdfcraft-<hex>.tmp` with `create_new` (fails if the name
  exists — no symlink/hardlink hijack), `write_all`, `sync_all`, close, then
  `rename`. Any error deletes the staging file and the original is intact. A
  read-only target is refused up front.
- **Verify before writing**: `check_save` (`session.rs:709`) reopens the produced
  bytes and re-renders every page *before* the file is replaced. If `mark_saved`
  fails afterwards, the user is told "Saved, but reopening failed" rather than
  left guessing.
- **Save / Save As / Revert**: Save = incremental in place; Save As to a
  different path = full rewrite (so deleted text is not left in an older
  revision); Revert = reopen the saved bytes and drop history.
- **No `.bak` file.** Safety comes from the appended revision chain (older
  revisions are still readable) plus a separate autosave/recovery store.
- **Dirty state** (`document.rs:30`, `sync_dirty` at `session.rs:416`) is
  recomputed after undo/redo, so returning to the saved revision becomes clean
  again. Surfaced in the title bar, tab label, and close/quit prompts.

**PDFAR equivalent:** PyMuPDF mirrors this closely —
`doc.can_save_incrementally()`, `doc.save(incremental=True,
encryption=fitz.PDF_ENCRYPT_KEEP)` (we already use exactly that in
`src/pdfar/annotations.py:222`), `doc.tobytes()`, and `doc.ez_save()` for the
full path. Our staging write exists but only for the burn-in copy
(`page_numbers.py:525-556`, and without the `fsync` before the rename). Missing
on our side: a staging write on the normal Save path, the reopen-and-verify step,
a dirty flag, and `setWindowModified(True)` in the title.

---

## 5. Editing existing text (the hard part)

`crates/content/src/lib.rs` (759 lines) is a tolerant content-stream layer:

```rust
pub struct Op { pub op: Vec<u8>, pub operands: Vec<Object>,
                pub inline: Option<(Dict, Vec<u8>)>, pub span: Range<usize> }  // :17
```

Every operator keeps its **byte span in the source**. The lexer never panics,
counts what it skipped, and keeps `BI … ID … EI` inline images whole (the
ambiguous `EI` is resolved by asking the renderer where the image data ends). A
page's several `/Contents` streams are modelled as one logical stream in
**pieces** (`Pieces`, `:571`), and the whole editing story reduces to one
function:

```rust
pub fn splice(&self, edits: impl IntoIterator<Item=(Range<usize>, Vec<u8>)>) -> Vec<Vec<u8>>  // :643
```

To rewrite a line of text, PdfCraft does **not** patch a `Tj`/`TJ` operand and
does **not** rewrite the whole `BT … ET`. It deletes the show operators of that
line and overwrites the first one with freshly emitted operators
(`crates/edit/src/text.rs:2565`):

```rust
// Rebuild: the line's first operator becomes the replacement, its others go.
let new_data = splice(&pieces, &ops, |i| if i == first { (replacement, false) } else { (Vec::new(), !drops.contains(&i)) });
```

Everything else on the page — every other row, all positioning, all vector
artwork, `gs`, paths — stays **byte-identical**, so its original kerning, `TJ`
word gaps and glyph widths survive untouched. `rewritten_contents` (`:806`) keeps
the original object reference for every unchanged piece; only changed pieces
become new objects. Combined with "no object streams on incremental saves",
unrelated pages are never recompressed.

### 5.1 From a clicked glyph to an operator index

`interpret(doc, ops, resources, …) -> Vec<Shown>` (`text.rs:1073`) is a small
interpreter over the operator list that reconstructs `TM`/`TLM`/`CTM`, text
state, colour, ExtGState, marked content and vertical writing mode; each `Shown`
records **the index of the operator that painted it**. `text_lines_in` (`:1470`)
joins shows into `TextLine { stream, ops, origin, paint, runs, clips, decodable }`
and `group_blocks` joins lines into paragraphs. Hit-testing is then just
rendered-rect ↔ `TextLine.ops`.

**PDFAR equivalent:** PyMuPDF already ships this mapping. `page.get_texttrace()`
returns one dict per painted run with `seqno`, per-glyph `chars`
(unicode + origin + bbox), `font`, `size`, `color`, `opacity`, `dir`, `wmode`,
`layer` and `flags` — verified working on 1.28.2. `seqno`/drawing order is our
handle back into the stream; we would still need our own tolerant operator
tokenizer with byte spans to splice, because PyMuPDF exposes the stream bytes
(`doc.xref_stream(xref)` / `doc.update_stream(xref, data)`) but not a parsed
operator model.

### 5.2 The font ladder

When the new characters cannot be drawn by the original font, PdfCraft walks a
strict order (`crates/edit/src/text_regions.rs:582-681`):

1. **Reuse the source font** if it can encode the string — reverse-mapping
   characters through ToUnicode/CMap (`Metrics::encode`,
   `crates/fonts/src/pdf.rs:897`).
2. **Reuse another font already on the page** of the same face.
3. **Embed the whole font program** — never a subset — as Type0 + CIDFontType2
   (or CIDFontType0 for CFF) with `CIDToGIDMap`, `/W`, and a ToUnicode CMap,
   named `PCFull{n}` (`crates/fonts/src/embedding.rs:121`). Full embedding is
   deliberate: a *subset* cannot supply glyphs it does not contain, so a partial
   font would silently fail later.
4. **Generate a Type 3 font** from an openly licensed bundled face, ≤ 240 glyphs
   per font (`MAX_TYPE3_GLYPHS`).
5. Fall back to a standard-14 `WinAnsi` font, and otherwise **refuse**, naming
   the character that has no home.

Unused generated fallbacks are pruned after the edit. Re-editing the same text
reuses the resource the page already has, and every resource made from one
program shares a single copy of the bytes.

### 5.3 Preserving state

`TextState::of` snapshots `Tf/Ts/Tz/Tc/Tw/Tl/Tr` plus the current `gs`. Because
`gs` has no inverse, the emitted operators carry only the **delta** against the
surrounding state, and each rewritten run is wrapped in its own `q … Q`.
Geometry is preserved by composing `Tm` from the original line's `tm · ctm ·
inverse(block.ctm)`; size comes from `hypot(trm[2], trm[3]) * textsize`; clips
travel with the line. Justification widens `Tw` for single-byte code 32 and
switches to `TJ` adjustments when the font maps space to another code. CJK
rewrapping follows JIS X 4051 / UAX #14 line-start and line-end rules.

Two refusals worth imitating: text in rendering mode 3 (invisible) is not
rewritten in place, and a paragraph whose formatting is mixed is not justified.

---

## 6. Images, annotations, forms, organization, redaction

**Images — placement only.** `crates/edit/src/images.rs` interprets the stream
tracking CTM/paths/clips and records `PageImage { op, … }` — again an operator
index. `ImageChange = Transform([f64;6]) | Crop | Replace(ObjRef) | Delete`.
Moving/resizing/rotating folds the new matrix into the **existing**
`q cm … Do Q` scope instead of nesting another graphics state, so repeated edits
do not stack transforms; a clip private to that image is conjugated along with it
(`clip_matrix`, `exclusive_clip`) while a clip shared with other content stays
put. Replacing an image never rewrites an existing stream: a **new** XObject is
created (`/PCImg{n}`) and only the `Do` operand is swapped. JPEG bytes are
inserted raw with `/DCTDecode` (no recompression); Flate otherwise; ICC becomes
`/ICCBased`, alpha becomes `/SMask`. Form XObjects are selected and transformed
as one unit via `/BBox` + `/Matrix` but are never ungrouped, and inline images
are out of scope.

**Annotations — appearances without a rasterizer.** `appearance::build(&Dict) ->
Option<Stream>` (`crates/annot/src/appearance.rs:374`) concatenates PDF operators
into a string and wraps them in a Form XObject (`/BBox = /Rect`, its own
`/Resources`); icon shapes are hand-written path data and text uses non-embedded
Helvetica/WinAnsi. It returns `None` rather than degrade. When an appearance is
regenerated (`apply_appearance:1479`) the `/AP` **dict is copied**, never shared,
`/D` and `/R` are dropped and `/N` set — because annotation dictionaries are
frequently shared between copies of a page. Failed redraws roll the dict back
(`redraw_or_restore:1439`). For display only, missing appearances are filled in a
side cache (`with_missing_appearances_cached:1579`) so the file is not rewritten
just to look right.

**Forms.** The `/Fields` tree is flattened resolving inherited `/FT /Ff /V /DV
/DA /Q /MaxLen /Opt`; widgets that appear only in a page `/Annots` array are
adopted and logged as a repair. Setting a value enforces `/MaxLen`, runs AF
keystroke/validate and document JS, writes `/V`, deletes `/RV`, then redraws the
widget appearance, reusing the form `/DR` font when it can encode the string.
Undrawable characters become `?` **and** set `/AcroForm /NeedAppearances` —
except in a file that declares PDF/A, where that flag is forbidden. Signature and
PushButton values are refused; password values are stripped at write time.

**Page organization.** `INHERITABLE = [Resources, MediaBox, CropBox, Rotate]`
(`crates/organize/src/lib.rs:63`) and every restructure calls `rebuild`, which
**pushes inherited attributes down onto each page and flattens the tree to a
single `/Pages` node** — the only safe way to reorder pages. Deleting pages also
drops their widgets, destinations, named destinations and outline entries
(otherwise the outline points at removed objects). Cross-document copy is a
deep-copy with an explicit skip list (`Parent`, `B`, `StructParents`, `Thumb`,
`PieceInfo`), depth-capped, rewiring `/P`, destinations, the field hierarchy and
`/OCProperties`; links whose target was not copied are dropped. Resource
de-duplication merges **only resource-like objects** (streams, font/ExtGState/
pattern/shading/function/encoding dicts, colour-space arrays) by structural
equality to a fixpoint — never pages, annotations, fields, OCGs, outline or
structure nodes — and only rewrites newly created candidates so incremental saves
stay small.

**Redaction — the model for "prove it".** `apply` works on a clone
(all-or-nothing), then per page: pins inherited `/Resources`, cuts the covered
glyphs out of `Tj/TJ/'/"` operators with width-preserving displacement, removes
`Do` image placements and inline images, removes or clips paths, rewrites nested
forms copy-on-write to a depth limit, strips its own added-content markers, then
removes overlapping annotations (including reply threads across pages), whole
form fields, structure-tree references, and **the XFA packets** (the datasets
mirror the redacted values). Images that cannot be re-encoded are removed
wholesale — fail closed. Then it **re-parses every redacted page in a Verify
pass and counts surviving glyphs**; any residue aborts the entire operation
(`RedactError::Residue(n)`, `crates/redact/src/lib.rs:698`). Vertical text and
ambiguous inline images abort rather than claim success. Finally
`doc.require_full_save()` (`:701`) forces a full rewrite, because the previous
revision still contains the secret. A separate "remove hidden information" path
covers 9 categories: metadata, attachments, comments, form fields, hidden text,
hidden layers, bookmarks, links/actions/scripts, private data.

**This is exactly the "proof" our ROADMAP F7-3 asks for** — and PyMuPDF gets us
most of the way: `page.add_redact_annot(rect)` + `page.apply_redactions()`
physically delete the operators (verified on 1.28.2: the page content stream
became `q Q` and `get_text()` returned `''`). What we must add ourselves is the
verification pass, the annotation/field/XFA cleanup and the forced full save.

---

## 7. Refusals, permissions and budgets

A recurring stance that is cheap to adopt and pays for itself:

- **Unparseable ⇒ read-only, visibly.** If the object graph fails to load, the
  editing session is `None` and `read_only_reason` says why ("the document
  structure could not be read for editing"); every mutating entry point then
  returns that error. Repairs made by the tolerant parser accumulate in a log
  shown in Document Properties.
- **Signed documents are never rewritten**, only appended to. An edit that would
  require a full save on a signed file is refused with `EditError::SignedRewrite`
  *before* it is applied.
- **Permissions are checked per edit variant** against the ISO-32000 table-22
  flags, with messages like "not permitted: filling in form fields".
- **Every input-derived size is bounded**: decoded stream bytes per page and per
  stream, graphics-state depth, operators per page, objects copied, form depth,
  overlay budget for redaction text, Type 3 glyph counts, selection sizes. A
  document that exceeds a budget is refused and *nothing changes*.
- **Stale references are detected, not guessed.** Derived listings (text blocks,
  objects) carry a document `generation`; an edit that resolves references from
  an older generation is rejected rather than applied to the wrong paragraph.

---

## 8. What PDFAR already has, and the increments to build

Already in place: one authoritative `Document` (`viewer.py:150`), tiled
viewport-driven rendering with a byte-budgeted cache, a per-document memory
level, selection tools with word/line geometry, an annotation *model* with
highlight persistence via incremental save (`annotations.py:222`), a burn-in path
that writes a **copy** rather than the open file and already stages it atomically
(`tempfile.mkstemp` → `save` → `os.replace`, `page_numbers.py:525-556`),
page-number stamping, reflow, thumbnails, outline reading, and a toolbar/dock
architecture with exclusive tool groups and a Reflow-exclusion pattern to copy.
Missing: any dirty/undo machinery, a save-with-confirmation flow, content
versioning, and an `fsync` before the rename in that staging write.

Recommended order, each step independently shippable:

| # | Increment | Reuses | New risk |
|---|---|---|---|
| 0 | ✅ **Done (F10-0) — Document session**: GUI thread owns the doc; `DocumentSession` publishes `doc.tobytes()` + a `version`; workers open from the buffer; stale render results are dropped and `invalidate_content()` clears the derived caches | `session.py`, `render.py`, `search.py`, `reflow.py`, `viewer.py` | none user-visible — but everything after depends on it |
| 1 | **Edit framework**: `Edit` dataclasses + `apply(edit)` on a cloned doc, undo/redo as document snapshots, `Scope`-style invalidation, dirty flag + `setWindowModified`, atomic staging write, reopen-and-verify before replacing | step 0 | low |
| 2 | **Save/Save As/Revert** UI with the confirm-on-close flow, incremental when `can_save_incrementally()`, full otherwise | step 1 | low |
| 3 | **Annotation editing** (note, highlight/underline/strike, ink, shapes, free text, stamps): PyMuPDF annot APIs; regenerate `/AP` or keep painting in the view and flatten on save; delete/reply/lock/properties | steps 0-2 | medium (appearances) |
| 4 | **Page organization**: rotate, delete, extract, split, combine, insert blank, reorder, page boxes, metadata + outline editing; flatten inherited attributes before reordering | step 1 | medium |
| 5 | **True redaction**: marks in the view, `apply_redactions`, then *verify* by re-extracting text/images and asserting nothing survives; clean annotations/fields/XFA; force full save; refuse if verification fails | steps 1-2 | high — must be provably correct |
| 6 | **Add content**: text boxes, images, header/footer, watermark, background, Bates numbering (our page-numbering code is 80 % of Bates already) | step 1 | medium (font embedding) |
| 7 | **Edit existing text**: tolerant operator tokenizer with byte spans over `doc.xref_stream`, `get_texttrace()` for glyph→operator mapping, splice-replace, the font ladder | all above | very high |

Deliberately **not** attempted, at least initially: editing inside Form XObjects,
inline images (`BI/EI`), keeping unrelated pages byte-identical across a full
save, and reflow-preserving paragraph rewrites — PdfCraft needed a font engine,
OpenType shaping, JIS line-breaking and a 240-glyph Type 3 generator to make step
7 acceptable, and that is a project of its own.

---

## 9. The five ideas worth stealing

1. **Edit a clone, adopt on success.** Failure becomes impossible to half-apply.
2. **Render from the bytes you would save.** Preview and file can then never
   disagree.
3. **Incremental save is the default**, and it is what preserves signatures,
   unknown objects and unrelated pages; full rewrite is the exception, demanded
   by redaction, PDF/A, password changes and Save As.
4. **Splice, don't rebuild.** Keep operator byte spans; replace one show
   operator and delete its siblings so everything else stays byte-identical.
5. **Refuse rather than degrade.** A redaction that cannot be verified, a
   character no font can draw, a signed file that would need rewriting, a
   document over a memory budget — all are errors the user can act on, and none
   of them silently produces a wrong file.

---

## Revision history

| Date | Change | Summary |
|---|---|---|
| 2026-10-26 | created | Study of `external/pdfcraft/` (Rust, MIT OR Apache-2.0) before that reference copy is deleted: COS overlay model, snapshot undo, incremental save, content-stream splicing, font ladder, redaction verification, and an 8-step path to editing in PDFAR |
| 2026-10-26 | step 0 built | F10-0 landed: `src/pdfar/session.py` (`DocumentSession`, `version`, `snapshot()`, `open_copy()`) plus version-stamped render jobs/results and `PDFView.invalidate_content()`. §2 updated from "the gap" to "how it is closed", including why the cache key stayed version-free |
