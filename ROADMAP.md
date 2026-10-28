# PDFAR — Advanced PDF Reader for Linux
## Mission: Build a benchmark PDF reader for Linux

You are working on an existing project called:

PDFAR — Advanced PDF Reader for Linux

The long-term goal is to transform PDFAR into a professional-grade PDF reader for Linux: fast, stable, modern, accessible, secure, privacy-conscious, highly configurable, and capable of functionally competing with the most comprehensive PDF readers available on Linux.

You must NOT treat this as a new project.

You must preserve existing functionality and evolve the architecture incrementally.

The absolute priorities are:

1. Do not break existing functionality.
2. Do not introduce performance regressions.
3. Keep the interface responsive.
4. Maintain security and stability when handling malformed or massive PDFs.
5. Maintain a maintainable architecture.
6. Add features via well-defined modules.
7. Every new feature must have verifiable tests.

---

# 1. CURRENT STATE — PDFAR 2.0

Before modifying anything:

- Inspect the entire repository.
- Read the README, documentation, tests, and source code.
- Run the existing test suite.
- Verify that the initial state works correctly.
- Do not assume a feature exists simply because it is mentioned in the documentation.
- Always verify the code's actual behavior. The current reference version is:

PDFAR 2.0.0

Main stack:

- Python >= 3.9
- PyQt6
- PyMuPDF
- pytest
- Qt Linguist for translations
- GPL-3.0-or-later

The current structure includes:

src/pdfar/
main.py
viewer.py
session.py
render.py
cache.py
tiles.py
geometry.py
search.py
sidebar.py
tabs.py
bookmarks.py
annotations.py
selection_tools.py
reflow.py
page_numbers.py
fonts.py
icons.py
i18n.py

Additionally:

- tests/
- tools/
- translations/
- docs/
- run.py
- pyproject.toml
- Debian documentation

The current architecture already features:

- asynchronous rendering
- priority-based render queue
- independent workers
- one PyMuPDF document per worker
- memory-limited LRU cache
- tiled rendering for deep zoom
- continuous scrolling
- page view modes (single, facing, facing-centered, overview, continuous)
- actual text selection
- area and table selection
- text copying
- AND search
- OR search
- phrase search
- regex search
- case sensitivity
- whole-word matching
- result highlighting
- F3 / Shift+F3 navigation
- thumbnails, with configurable size, border and shadow
- index/outline
- user bookmarks
- highlights and notes (in-memory model; highlights persist)
- page numbering and erase masks, exported as a separate copy
- reflow (text and figures)
- zoom
- fit page
- fit width
- rotation
- password-protected PDFs
- recent files
- drag & drop
- window state persistence
- per-document reading position resume
- presentation mode
- dark mode
- Qt Linguist-based internationalization
- headless tests
- screenshot tool
- initial documentation for Debian packaging

Do not re-implement these features from scratch.

First, reuse and extend the existing architecture.

---

# 2. GOLDEN RULES — NON-NEGOTIABLE RULES

These rules are mandatory.

## 2.1 GUI and threads

NEVER:

- use QPixmap from a worker thread;
- modify Qt widgets from a worker thread;
- use QTimer.singleShot() from a worker thread expecting it to execute GUI code;
- directly access GUI objects from worker threads; - sharing the same `fitz.Document` across different threads.

Worker-to-GUI communication must be handled via properly connected Qt signals.

Render results should reach the GUI as safe data, such as:

- PNG bytes
- serializable data
- simple Python structures

Conversion to `QImage`/`QPixmap` must take place in the GUI thread.

---

## 2.2 PyMuPDF

Rule:

ONE FITZ DOCUMENT PER THREAD.

Any worker needing to work with a PDF must open its own:

`fitz.Document`

Never share the main `Document` object among workers.

The document used by the GUI and the documents used by workers must have independent lifecycles.

Always close documents properly.

---

## 2.3 Overlays

Overlays must never be permanently drawn onto the cached bitmap.

This includes:

- search results
- selections
- temporary annotations
- text selection
- page indicators
- other dynamic elements

The cached bitmap represents the page's base content.

Overlays are drawn during the `paintEvent` or via a separate layer.

This prevents highlights from accumulating every time a page is repainted.

---

## 2.4 Qt Signals

Avoid unnecessary lambdas connected to Qt signals if they could cause lifetime/GC issues or complicate teardown.

Prefer:

- bound methods
- explicit `QObject`s
- dedicated signals
- clearly defined slots

Every connection must have a clear lifecycle.

---

## 2.5 Shutdown

Pools and workers must be shut down correctly.

Where applicable:

shutdown(wait=True)

Do not allow a worker to keep running while the QObject destined to receive its signal is being destroyed.

The shutdown of:

- RenderPool
- SearchWorker
- QThread
- PyMuPDF documents

must be deterministic.

---

## 2.6 Geometry

All coordinate transformations must remain centralized.

Do not duplicate calculations for:

- rotation
- PDF coordinates
- screen coordinates
- zoom
- rectangles
- selection
- annotations

Use a common geometry layer.

Any new functionality that draws on a page must use that same transformation.

This is especially important for:

- annotations
- selection
- search
- redaction
- measurements
- forms
- signatures

---

# 3. ARCHITECTURAL PRINCIPLE

PDFAR must evolve towards a modular architecture.

Do not turn main.py or viewer.py into massive files.

When a feature reaches sufficient complexity, create an independent module.

Future examples:

annotations.py
bookmarks.py
tabs.py
forms.py
signatures.py
attachments.py
metadata.py
page_operations.py
accessibility.py
redaction.py
ocr.py
preferences.py
document_compare.py

The graphical interface should coordinate functionalities.

PDF logic should be kept separate from visual logic whenever possible.

Logic that does not require Qt should preferably be:

- pure
- testable
- GUI-independent

---

# 4. DEFINITION OF DONE

No task is finished simply because it "works."

A task is only DONE when:

1. The functionality is implemented.
2. Existing tests continue to pass.
3. New tests are added where applicable. 4. Tests are executed at least 3 times consecutively.
4. There are no known crashes.
5. There are no new critical warnings.
6. Visual verification is performed when the task affects the GUI.
7. `tools/screenshot.py` is used where applicable.
8. Behavior is checked with both small and large PDFs.
9. There is no performance regression exceeding 15% relative to the baseline.
10. Any introduced technical debt is documented.
11. Documentation is updated where applicable.

For GUI features:

- functional test
- headless test
- screenshot
- visual review

For PDF features:

- valid PDF
- large PDF
- PDF with special features (where applicable)
- protected PDF (where applicable)

---

# 5. PERFORMANCE BASELINE

Record a baseline before making significant changes.

As an initial reference, PDFAR 2.0 documents approximately:

- opening a 200-page document: ~0.31 s
- first page rendered: ~0.22 s
- jump to page 200 + render: ~0.13 s
- zoom 100% → 300% + render: ~0.5 s

These values ​​serve as a reference, not a universal guarantee.

Create or improve a reproducible benchmarking system.

Every significant modification must compare:

baseline vs. new version.

Rule:

Do not accept a regression >15% unless there is an explicit, documented technical reason.

---

# 6. PDFAR VS. PROFESSIONAL FEATURES

Do not blindly copy other readers.

Use a functional comparison matrix to identify gaps.

| Area | PDFAR 2.0 | Future Goal | Status |
|---|---|---|---|
| Rendering | Yes | Fast and stable rendering | [x] |
| Continuous scroll | Yes | Smooth scrolling | [x] |
| Text selection | Yes | Multi-column / precise | [~] |
| Search | Yes | Regex + advanced | [~] |
| Thumbnails | Yes | Advanced management | [~] |
| Outline | Yes | Editing/management | [ ] |
| Tabs | Yes | Multiple documents | [x] |
| Presentation | Yes | Fullscreen/slideshow | [x] |
| Dark mode | Yes | Full theme | [~] |
| Resumption | Yes | Per-document position | [x] |
| Reflow | Yes | Adaptive reading | [x] (text + figures) |
| Bookmarks | Yes | Full bookmarks | [x] |
| Properties | Partial | Full inspector | [~] |
| Attachments | Yes | Full management | [~] (list and extract; no add/remove) |
| Regex search | Yes | Secure regex | [x] |
| Layers | No | OCG | [ ] |
| Annotations | Partial | Full system | [~] (highlights persist; notes are in-memory only) |
| Forms | No | AcroForm | [ ] |
| Signatures | No | Cryptographic verification | [ ] |
| Page manipulation | No | Split/merge/reorder | [ ] → F10-4 |
| TTS | No | Accessible reading | [ ] |
| OCR | No | Optional OCR | [ ] |
| Redaction | No | TRUE redaction | [ ] → F10-5 |
| Comparison | No | Compare documents | [ ] |
| Content editing | No | PDF editing | [ ] → F10 |
| Distribution | Partial | .deb/AppImage/Flatpak | [~] |

This table serves as a coverage guide, not a list for simultaneous implementation.

Statuses follow the rules in section 7.

---

# 7. OFFICIAL ROADMAP

Implement features in phases.

Do not skip arbitrarily from one phase to another unless there is a technical reason.

## Statuses

- [x] Implemented
- [~] Partially implemented
- [ ] Not yet implemented

### Status rules

[x] means the functionality is implemented and verified.

[~] means there is a partial implementation, but at least one significant
requirement, test, integration, or validation step is still pending.

[ ] means it has not yet been implemented.

Do not change [ ] or [~] to [x] simply because some related code exists.

A task should only be marked as [x] when the full requirement has been
implemented and, where applicable, tested.

---

# P1 — READING EXPERIENCE

Objective:

Transform PDFAR into a modern reading experience.

## P1-1 Tabs

Implement support for multiple open documents.

Requirements:

- tabs
- close tab
- open PDF in new tab
- switch tab
- independent state per document
- independent zoom
- independent current page
- independent search
- independent selection
- independent annotations
- proper RenderPool closure per document

Acceptance criteria:

- open 3 PDFs simultaneously
- switch between them without corruption
- close any of them
- no active workers remain
- memory usage does not increase indefinitely

## P1-2 Presentation mode

- fullscreen
- hide UI
- keyboard navigation
- next/previous page
- ESC to exit

## P1-3 Dark mode

Do not limit changes to just the background.

Must cover:

- toolbar
- sidebar
- search panel
- menus
- dialogs
- selection
- indicators
- areas outside the document pages

## P1-4 Resume reading

Save per file:

- page
- zoom
- rotation
- fit mode
- scroll position
- tab/document state where applicable

## P1-5 Reflow

Implemented: text is extracted and wrapped into a single column, raster images
and ruled tables are rendered inline in reading order, figures scale with the
text size, and scanned pages are shown as fit-to-column page images. Two-column
pages are detected from the gutter between the columns and read left column
first, then right; page-wide elements (title, abstract, spanning figures and
tables, centred headings) split the page into bands that are read left to
right. Detection is conservative and falls back to block order when no clear
gutter exists. Table detection is best-effort: PDF has no semantic reading
order, and tables without ruling lines reflow as ordinary text.
`demo-two-columns.pdf` (generated by `tools/demo_pdf/make_two_column_demo.py`)
covers full-width and in-column figures and tables at different heights.

Do not pretend that all PDFs can reflow perfectly.

There must be a warning or fallback for PDFs where this is not possible.

---

# P2 — NAVIGATION AND SEARCH

## P2-1 Bookmarks

Implement:

- internal bookmarks
- persistent bookmarks where applicable
- navigation
- add
- delete
- rename
- hierarchy

## P2-2 Document properties

Display:

- title
- author
- subject
- keywords
- creator
- producer
- dates
- page count
- size
- PDF version
- encryption
- permissions

## P2-3 Attachments

Display and extract attachments.

Apply security controls.

Never automatically execute an attachment.

## P2-4 Regex search

Add regex search.

Must handle:

- invalid regex
- huge documents
- time limits
- result limits

A problematic regex must not freeze the GUI.

## P2-5 Layers / OCG

Explore PDF layer support.

Must allow:

- detecting layers
- show/hide
- state preservation

---

# P3 — i18n AND PREFERENCES

Convert current translation support into a true internationalization system.

Must allow:

- interface language
- language independent of the PDF language
- preferences
- accessibility
- rendering settings
- cache settings
- behavior settings

All new strings must be translatable.

Never introduce hard-coded GUI strings without going through the i18n system.

---

# P4 — ⭐ ANNOTATIONS

This is a top priority.

PDFAR must become a truly useful PDF reader for study and work. Implement:

## P4-1 Highlight

- select text
- create highlight
- color
- modify
- delete
- PDF persistence

## P4-2 Notes

- note associated with text
- comment
- editing
- deletion
- comments panel

## P4-3 Freehand

- pencil
- color
- thickness
- erase

## P4-4 Shapes

- rectangle
- circle
- line
- arrow
- polygon

## P4-5 Stamps

Support for standard and custom stamps.

## P4-6 Measurement

- distance
- area
- units
- calibration where possible

## P4-7 Annotation panel

Panel displaying:

- page
- type
- author
- date
- content
- color

Clicking an annotation → jump to it.

## P4-8 Undo/Redo

Annotation operations must support undo/redo.

Provided by the general edit framework, **F10-1**: annotations are the first
operations routed through it, so undo covers every document change and not only
comments.

## P4-9 XFDF

Investigate XFDF import/export support.

There must be a clear separation between:

- annotations stored in the PDF
- external annotations
- temporary UI state

VERY IMPORTANT:

Annotations must use the same geometric transformations as selection and search.

Rotating or zooming must not shift an annotation's position.

---

# F5 — FORMS AND SIGNATURES

## F5-1 AcroForms

Support:

- text fields
- checkboxes
- radio buttons
- combo boxes
- buttons

## F5-2 Form filling

Allow entering information without destroying the original document.

## F5-3 Signatures

Before implementing cryptographic signatures, clearly distinguish between:

A) drawing a visual signature

B) inserting an image/visual signature

C) cryptographic digital signature

D) verifying an existing digital signature

Do not confuse these four things. Cryptographic verification must clearly show:

- valid
- invalid
- unknown/unverifiable
- certificate
- issuer
- dates
- chain of trust (when verifiable)

## F5-4 XFA

XFA must be treated as a special feature.

If support is not complete:

DO NOT claim compatibility.

Display a clear warning.

---

# F6 — PAGE MANIPULATION

Tracked and implemented as **F10-4**.

Implement:

- insert
- delete
- extract
- duplicate
- reorder
- permanently rotate
- split PDF
- merge PDFs

Before modifying a file:

- protect against accidental loss
- prefer "Save As"
- confirm destructive operations
- preserve metadata whenever possible

---

# F7 — ACCESSIBILITY AND SECURITY

## F7-1 OCR

Modular architecture.

Do not introduce mandatory heavy OCR if it can be optional.

Ideally:

- detect scanned PDF
- offer OCR
- process pages in the background
- allow cancellation
- preserve original PDF

## F7-2 True redaction

Tracked and implemented as **F10-5**.

THIS IS CRITICAL.

A black rectangle over text is NOT true redaction.

Redaction must physically remove the underlying content.

There must be proof demonstrating that the redacted text can no longer be:

- selected
- copied
- searched
- extracted

## F7-3 PDF security

Protect against:

- corrupt PDFs
- huge files
- excessive resource usage
- dangerous attachments
- potentially dangerous URLs
- PDF JavaScript
- processing loops

Never execute active content automatically.

---

# F8 — STRETCH FEATURES

These features should only be addressed once the core is stable.

## F8-1 Content editing

Tracked and implemented as **F10-7**.

Modify existing text or graphic elements.

Do not promise perfect editing for any PDF.

## F8-2 Magnifier

Reading magnifier.

## F8-3 Split view

Two pages/documents simultaneously.

## F8-4 Document comparison

Visual and/or textual comparison between two PDFs. Must distinguish between:

- added text
- deleted text
- modified text
- different pages

---

# F9 — DISTRIBUTION AND QUALITY

## F9-1 ​​CI

Create reproducible CI.

Must execute:

- tests
- lint
- type checking (where feasible)
- build
- smoke tests
- critical benchmarks

## F9-2 Debian package

Complete:

- debian/control
- debian/rules
- changelog
- copyright
- manpage
- icons

The project already includes a Debian-compatible structure and uses the package name `pdfar`. :contentReference[oaicite:3]{index=3}

## F9-3 AppImage

Create reproducible AppImage.

## F9-4 Flatpak

Create manifest.

## F9-5 Continuous benchmarks

Each release must be comparable to previous versions.

## F9-6 Privacy

Document:

- what data PDFAR stores
- where it stores it
- whether telemetry exists
- what information remains local

Default:

DO NOT introduce telemetry.

PDFAR must be local-first and privacy-first.

---

# F10 — PDF EDITING (MASTER PLAN)

Phase status: **[~] Under way.** F10-0 (document session), F10-1 (edit
framework) and F10-2 (save, save as, revert) are implemented; **F10-3** is under
way, with annotations as edits (F10-3a), the save-cost fix (F10-3b), revision
files for undo (F10-3c), standalone `/Text` notes (F10-3d), the interface that
places them (F10-3e), a comments panel that jumps to what it lists (F10-3f), a
highlight that stays on its words however the page is turned (F10-3g), a list
that re-reads the pages an edit touched (F10-3h), freehand ink in the
document (F10-3i), the pen tool and its style pickers (F10-3j/3k), the
shape tool (F10-3l/3m) and the standard rubber stamps with their tool
(F10-3n) done.

*Measured on F10-1:* the edit path never touches the open path — `tobytes()` runs
for the first time when an edit is accepted, not when a document is read — and
the GS262 benchmark is unchanged. Two rounds against the F10-0 baseline, on a
notably busy machine: F10-1 open 2.48 / 2.14 s, first page 0.24 / 0.25 s, jump
0.81 / 0.58 s, scroll 2.34 / 3.37 s, peak RSS 449 / 494 MB; F10-0 3.30 / 2.10,
0.34 / 0.41, 0.74 / 0.56, 3.77 / 3.36, 495 / 513 MB. Every F10-1 figure sits
inside the F10-0 spread, so the extra `Document` clone and history bookkeeping
cost nothing measurable.

*Measured on F10-2:* GS262 open 2.12 s, first page 0.25 s, jump 0.70 s, zoom
sequence 2.81 s, peak RSS 508 MB — the same shape as F10-1, because saving is not
on the read path. Recording the encryption at open costs two attribute reads
(`doc.permissions`, `doc.metadata`) and the verification render only happens on a
full rewrite, which is a command the user asked for.

*Measured on F10-3c:* GS262 open 1.67 s, first page 0.33 s, jump 0.44 s, zoom
sequence 1.85 s, peak RSS 494 MB — the read path has never been touched by this
change, and a worker reading a page off a revision file costs 27.6 ms, which is
what `fitz.open(path)` plus one page of text has always cost. What changed is the
edit: 2.1 s and a 25 MB buffer per highlight became 54–70 ms and nothing.

This is the umbrella for every capability that changes a document. F6 (page
manipulation), F7-3 (true redaction) and F8-1 (content editing) are now
sub-sections of this plan and are implemented here, not separately.

Reference study, with the reasoning behind each rule below:
`docs/pdfcraft-editing-analysis.md`.

The ordering is not arbitrary: every step depends on the one above it. In
particular, nothing in this phase is safe to build before F10-0 and F10-1 exist,
because today PDFAR has no way to know that a document changed, no undo, and no
save path that cannot lose the user's work.

## F10-0 Document session — the prerequisite — [x]

Nothing may read the document from disk any more while an edited version exists
in memory.

- [x] One authoritative `Document`, owned by the GUI thread (`viewer.py:150`),
      wrapped in `src/pdfar/session.py::DocumentSession`
- [x] A `version` counter that every accepted change bumps exactly once
      (`session.content_changed()`); `version == 0` means "the file on disk *is*
      the document", so an untouched document never pays for a snapshot
- [x] Render workers read `session.open_copy(version)` — the path while
      untouched, `fitz.open(stream=snapshot)` afterwards
- [x] Search worker: same (`SearchWorker(session, params)`)
- [x] Reflow worker: same (`ReflowWorker(session, width)`)
- [x] Document Properties reads the live copy (`main.py`, `_show_properties`)
- [x] The snapshot is produced once per version and reused, never per job
- [x] Only the owning thread may serialize: `read_version()` publishes from the
      GUI thread, and a worker that asks for unpublished content gets
      `SnapshotNotPublished` instead of a MuPDF data race
- [x] Stale results are dropped, not cached: every `RenderResult` carries the
      version it was rendered from, and `PDFView._on_result` /
      `Sidebar._on_render` ignore anything that is not current
- [x] `PDFView.invalidate_content()` bumps the version and drops everything
      derived from the previous content (page cache, words, tiles, hit rects,
      thumbnails, reflow layouts), then re-requests the viewport.
      *Design note:* the roadmap first said "the `PageCache` key gains the
      version". Clearing on change is what we do instead — the version travels
      on the job and its result, which covers the same case without a cache
      holding several generations of the same page.
      *Renamed in F10-1* to `invalidate(scope)`, which is what lets a metadata
      edit leave the rendered pages alone.
- [x] The large-PDF benchmark shows no regression (this step is on the hot path):
      GS262, 540 pages — open 1.89 s, first page 0.165 s, jump 0.42 s,
      peak RSS 408 MB
- [x] Tests: an in-memory change is visible to render, search and reflow without
      the file on disk changing at all (`tests/test_session.py`, 15 tests)

Why: MuPDF is not thread-safe, so each worker needs its own `Document`. The only
correct way to share state between them is a byte snapshot pinned to a version.

## F10-1 Edit framework — [x]

- [x] An `Edit` type carrying *intent* (`RotatePages { pages, delta }`), not
      before/after values — `src/pdfar/edits.py`: `Edit` is an ABC with `label`,
      `scope` and `apply(doc)`; a concrete edit is a frozen dataclass holding
      only its arguments (`SetMetadata` is the first)
- [x] Edits run on a copy of the document; the result is adopted only on success,
      so a failed edit cannot be half-applied (`session.apply()` works on
      `fitz.open(stream=doc.tobytes())` and only `_adopt()`s a clone whose
      `apply()` returned; any exception becomes an `EditError` and the live
      document keeps its identity, version and `generation`)
- [x] Undo/redo stacks with a human label per step and a fixed depth
      (`EditHistory`, bounded by *both* `max_steps=50` and `max_bytes=256 MB`
      because the snapshots are whole documents; the Edit menu names the step it
      would take — `Undo Add text "…"`)
- [x] Scope-based invalidation: an edit declares what it can change (metadata,
      annotations, page content…) and only those caches are rebuilt
      (`Scope` IntFlag; `PDFView.invalidate(scope)` grades the pixel caches, the
      word/hit-test caches and the page geometry separately, and restarts reflow
      only when `STRUCTURE | CONTENT` is set. `Sidebar.invalidate_thumbnails()`
      rebuilds the model only on `STRUCTURE`)
- [x] A dirty flag driving `setWindowModified(True)` and the tab indicator
      (`session.dirty` is `version != saved_version`; the title carries `[*]`,
      the tab gains a `•` prefix)
- [x] Confirm before discarding: closing a tab or the window with unsaved changes
      (`_confirm_close` offers Save / Discard / Cancel — Save writes the
      document's own file since F10-2; `closeEvent` asks about every dirty tab
      before anything is torn down)
- [x] A document `generation` so stale UI references are rejected rather than
      applied to the wrong object (bumped by `_adopt()` only — an in-place change
      keeps the same `Document` object, so it must not look like a new one)
- [x] Tests: apply, undo, redo, dirty state after each, and a failing edit leaving
      the document byte-identical (`tests/test_edits.py` 21,
      `tests/test_edits_ui.py` 12)
      *Design note:* "byte-identical" is asserted as object identity + page text
      + the bytes on disk, because `doc.tobytes()` is **not** deterministic —
      MuPDF regenerates the second `/ID` element on every serialization, so two
      writes of an untouched document differ.

Why this shape: keeping the *intent* rather than the before/after values is what
lets an edit be replayed, refused, or applied to a copy — and it is what gives
the user a label in the Edit menu. Holding whole-document snapshots instead of
deltas costs memory but is the only undo that cannot corrupt a PDF, and the byte
bound is what keeps that cost finite.

### Consequences F10-2 must handle (found here, fixed there)

- After the first adoption the document is stream-backed, so
  `doc.can_save_incrementally()` is False; `save_to_pdf()`'s incremental path in
  `Highlights` therefore returns False from that point on. ✅ Handled: the
  predicate is `doc.name` + `doc.is_dirty` + `can_save_incrementally()`, and a
  stream-backed copy takes the verified full rewrite instead.
- `tobytes()` on an authenticated encrypted document yields **decrypted** bytes,
  so the session copy of an encrypted PDF is unencrypted in memory after the
  first edit — saving it would silently strip the password. ✅ Handled:
  `Encryption.capture()` records method, permissions and the typed password at
  open, and every rewrite re-applies them (`save.py`).
- Save As retargets `session.path`, but `viewState/<path>` and `bookmarks/<path>`
  in QSettings are keyed by the old path and are not migrated yet. ✅ Handled:
  `_migrate_document_state()` moves both, through the new `view_state_key()` and
  `settings_key_for()` accessors.

## F10-2 Save, Save As, Revert — [x]

- [x] Atomic write: temp file in the same directory, write, `flush`, `os.fsync`,
      then `os.replace` (`save.staged_write`; the pattern already existed in
      `page_numbers.py:525-556` without the `fsync`, and it also carries the
      target's file mode over, which `mkstemp` would otherwise reset to 0600)
- [x] Incremental save when the document is still the file handle it came from,
      so unknown objects and signatures survive (`_can_append`; `doc.name` +
      `doc.is_dirty` + `can_save_incrementally()`, because the last one alone
      lies for a stream-backed copy)
- [x] Full rewrite for Save As, so deleted content is not left in an older
      revision (`full=True`, `garbage=2`: a measured page deletion took the file
      from 3836 to 3301 bytes and the xref table from 29 to 25 entries. This
      shipped as `garbage=3`, which costs 70× more for the last few percent of
      the table — corrected in F10-3b.)
- [x] Verify before replacing the file: reopen the produced bytes, check the page
      count and render page 0 (`verify_bytes` / `verify_file`; MuPDF *repairs*
      truncated input, so the page count and "is this a PDF at all" are the
      failures worth testing for)
- [x] Refuse, with an actionable message: read-only file, directory that cannot
      be written, a file changed on disk by someone else, and a produced buffer
      that does not read back (`SaveError`; `os.access(target, W_OK)` is needed
      explicitly because `os.replace` only needs the *directory* to be writable)
- [x] Re-apply the original encryption on save, including the case where PDFAR
      only ever had the owner password: a rewrite cannot rebuild a secret it was
      never given, so it asks (`ProtectionError`, and `allow_weakened=True`
      writes a copy with a fresh random owner password and the original
      permissions instead of silently dropping both)
- [x] Revert to saved (`DocumentSession.revert()` reopens the file, adopts it and
      clears the history; `AnnotationManager.reload_from_document()` drops the
      highlights that never reached the file, which the model alone still held)
- [x] Per-document state migration on Save As
- [x] Tests: 33 in `tests/test_save.py` for the disk-facing logic, 19 in
      `tests/test_save_ui.py` for the commands, the dialogs and the migration
- [ ] Refuse a **signed** document whose save would break the signature. Not
      done: nothing in PDFAR reads signatures yet, so the check would be a new
      per-page annotation scan invented for this one message. `save.py` is the
      place for it; it belongs with whichever task first cares about signatures.

Why this shape: the two rules a PDF editor cannot bargain with are that a file is
never half-written and never renamed until the replacement has been read back.
Everything else is a consequence — incremental saving is the exception to the
second rule precisely because it does not rename anything.

### Consequences F10-3 must handle (found here)

*Both handled in F10-3a; kept because they explain why Save is not gated on the
dirty mark.*

- Highlights lived in `AnnotationManager`'s model and only reached the document
  when something synced them, so `session.dirty` was False while the user had
  unsaved highlights. Save is therefore **not** gated on the dirty mark (it would
  switch Ctrl+S off on exactly the document the user is working on) and
  close-the-tab could not warn about them. Routing annotation changes through
  `Edit` removed the whole class of problem, which is what F10-3a did — the
  reason left for not gating Save is the ordinary one in the test.
- `sync_to_document()` was added here to skip its work while the model matched
  the document it was last written to, keyed on `session.generation` — otherwise
  a second Ctrl+S rewrote the whole file, and an undo that handed back a
  different `Document` would have saved without the highlights. **Deleted in
  F10-3a:** there is nothing left to sync, because the document holds the
  annotations in the first place.

## F10-3 Finish the annotation system (P4) — [~]

Highlights are implemented and persisted (`src/pdfar/annotations.py`).

- [x] Annotation operations routed through F10-1, so they undo and mark dirty
      (**F10-3a.** The document is the annotation store: creating, recolouring,
      noting and deleting a highlight are each an `Edit` with `Scope.COMMENTS`
      applied through the session, so they undo, mark the document dirty and
      reach a background render as a new content version. `AnnotationManager` is
      now a *view* of that document — `reload_from_document()` re-derives the
      list and is stamped with `session.generation`, so a document we have never
      read is an annotation list we have never read. The Qt highlight overlay is
      deleted: `render.py` paints annotations, and keeping the overlay painted
      the band a second time.
      *Identity:* an annotation's id is its `/NM` name, so a sidebar row, a model
      key and a PDF object agree across an undo, a save and a reopen. A foreign
      annotation with no `/NM` is filed under a read-only `anon:{xref}` fallback
      key, because opening a file must not have to write into it.
      *Granularity:* a selection of several lines is one annotation with several
      quads, and a selection spanning pages is one undo step, not one per page.
      `save_to_pdf()` no longer deletes every highlight in the file and rewrites
      them — it asks the writer, which stages the file atomically.)
- [x] Tests: `tests/test_annotations_edits.py` (23) — a highlight is in the
      document the moment it is made, its `/NM` survives a save and a reopen, an
      unrelated content edit does not lose it, a foreign unnamed annotation is
      listed without being rewritten, the band reaches the pixels through
      `session.open_copy()`, each operation names its own undo step, an edit
      whose annotation is gone refuses the document rather than writing to
      whatever shares its key, and the tab, title and Revert follow the dirty
      mark.
- [x] Saving after an edit stopped compacting the object table (F10-3b). Ctrl+S
      after one highlight on GS262 measured **123.9 s** with the rewrite level
      F10-2 shipped, and **1.75 s** at `garbage=2` — 3.5 s for the whole action
      once the undo snapshot is included, with the deleted page's bytes and
      objects still leaving the file.
- [x] A comment-only edit no longer has to copy the whole document to undo it
      (**F10-3c.** The session keeps its live document as a file in a private
      scratch directory; an edit copies the current revision, appends to that
      copy and hands the file it came from over as the undo point, so undo is a
      reopen instead of a replay. One highlight on GS262 went from **2.1 s and
      ~25 MB of RAM** to **54–70 ms and no buffer at all** — undo 4.5 ms, redo
      3.4 ms. Readers open the revision file as well, so the history no longer
      parks a serialized copy of the book in memory. The 256 MB cap still
      applies and now bounds scratch *disk*: the book keeps about ten revisions
      before the oldest steps fall off. Undoing by inverse intent is off the
      table — reopening a file is already cheaper than reasoning about what an
      edit undid.)
- [x] Notes persisted as real `/Text` annotations, not only in memory
      (**F10-3d.** A note *on* a highlight was already that annotation's own
      `/Contents` (F10-3a); a standalone sticky note did not exist at all —
      `NoteAnnotation` was a dataclass nothing constructed and
      `get_notes_for_page()` always answered `[]`. `AddNote` now writes a `/Text`
      annotation through the session, named by `/NM` like any other, the manager's
      list dispatches on the annotation subtype, and the note-editing steps stopped
      being highlight-specific (`RemoveAnnotation`, `SetAnnotationNote`,
      `set_note()` / `remove_note()` / `get_note()`) because both kinds keep their
      text in the same key. An empty note is still a note — the icon is what the
      user asked for and the popup is where the text goes — and MuPDF gives it an
      appearance stream, so it paints without PDFAR writing one. The document side
      is finished; the tool to place one is the next item.)
- [x] The interface half of a note: a tool to place one and a popup to type into
      (**F10-3e.** Right-clicking a page offers "Add note here…" and
      right-clicking the icon offers "Edit Note…" / "Delete Note"; the popup is a
      multi-line `QInputDialog` and every answer goes through the session, so the
      note tool shares F10-3d's document side and F10-3c's undo rather than
      adding a second way to change a PDF. The sidebar's row menu was extracted
      into `_annotation_menu()`, which is also what made a note's own text
      reachable from its row — the note actions used to be offered on highlight
      rows only.)
- [x] A note edit no longer costs the annotation *list* more than the edit
      (**found in F10-3e, fixed in F10-3h**): `AnnotationManager._run()` re-read
      every page after every edit, which measures 167–302 ms on the 540-page book
      against 59 ms for the edit itself — the walk of a document that has just been
      adopted is cold, so every page is parsed again. The list is now kept per page
      and an edit says which pages it wrote to (`Edit.pages`), so placing a note
      there re-reads one page: **1.6–2.8 ms**.
- [x] A highlight made while the view is rotated no longer lands on the wrong
      part of the page (**found in F10-3e, fixed in F10-3g**):
      `create_highlight_from_selection()` had been handing `add_highlights()`
      rectangles in *view* space — `_word_rect_view()` maps them through
      `map_rect()` — while the annotation edits write PDF space. The words the
      selection is made of are already in page space, so the mapping is now gone
      and the merge runs there too, which also puts the merged quads back on the
      text lines instead of on screen rows.
- [x] Comments panel listing annotations and jumping to them
      (**F10-3f.** The panel is the sidebar's Annotations tab: it groups what the
      document holds by page — both kinds, since F10-3d's subtype dispatch — and
      activating a row now goes to the *annotation*, not to its page. It used to
      emit `goto_page_requested`, which on a 540-page book left the user hunting
      for the band they had just clicked in the list; the row now emits the
      annotation's id, the view scrolls its rectangle to the middle of the
      viewport and draws a ring around it until the next click. What this panel
      is not is a reply thread: anchored popups and the appearance streams other
      readers need to show them are the item below.)
- [x] Freehand ink persisted as a real `/Ink` annotation
      (**F10-3i.** The third kind the document is the store for, and the first
      whose geometry is the user's own drawing: `AddInk` writes one `/Ink`
      annotation holding every stroke of the drawing in its `/InkList`, in page
      space, named by `/NM` like the others, with the colour in `/C` and the
      thickness in `/BS`. MuPDF writes the appearance stream, so the stroke
      reaches the pixels through `render.py`'s `annots=True` and any other reader
      shows it too. The manager dispatches on the subtype, so a drawing is
      listed, jumped to, noted and deleted by the code F10-3d left generic, and
      F10-3h's per-page re-read carries over: drawing on one page re-reads one
      page. An empty drawing is refused — MuPDF turns an empty `/InkList` into an
      annotation whose rectangle is the whole coordinate range.)
- [x] The tool that draws one: a pen mode that captures the mouse, turns the
      drag into page-space points, and hands them to `AddInk` as one edit per
      drawing (**F10-3j.** A "Freehand" tool beside Text, Area and Table; the
      press starts a stroke, the moves extend it, the release turns the whole
      drag into one `AddInk` — one Ctrl+Z, not one per pixel. Points are taken
      in page space, so a rotated view draws where the pointer is; points that
      leave the page are dropped rather than clamped; the stroke paints under
      the pointer while the button is held; Escape and changing tools cancel an
      unfinished stroke. The pen is a page tool, so reflow disables it with
      Area and Table. Colour and thickness are view attributes with the
      defaults from `annotations.py`, not yet a picker — that is F10-3k.)
- [x] Pen colour and thickness: a picker for both, and the stroke previewed in
      the chosen colour while drawing (**F10-3k.** The pen's controls sit in the
      toolbar beside the tool and appear only while it is active: a pencil
      swatch that opens the ink palette — Black, Red, Blue, Green, Orange,
      Purple — plus "Custom…" for a colour dialog, and a 1/2/4/8 pt combo. Both
      are viewer state on the active tab, so each document keeps its own pen,
      and neither dirties the file: the choice is written into an annotation's
      `/C` and `/BS` only when a stroke is committed. The preview paints from
      the same two attributes, so a style picked mid-drag recolours the line
      under the pointer.)
- [x] Shapes as real annotations: rectangle, ellipse, line, arrow, polygon
      (`/Square`, `/Circle`, `/Line`, `/Polygon`) (**F10-3l.** `AddShape`
      writes the geometry the user gives in page space; an arrow is a `/Line`
      whose `/LE` end carries a closed arrowhead, which is how PDF draws one.
      Corners arrive in any order and are normalised; a shape with no area is
      refused before MuPDF sees it, because a zero-height `/Square` is an
      annotation nobody can select. The manager reads the four subtypes back
      into `ShapeAnnotation` and distinguishes `"arrow"` from `"line"` by
      `line_ends`, so undo, redo, save/reopen, the comment list, notes and
      delete all reach shapes through the code the earlier kinds left generic.)
- [x] The shape tool: drag a rectangle, ellipse, line, arrow or polygon on the
      page and see it while dragging (**F10-3m.** A "Shape" tool beside
      Freehand, whose split button chooses the kind and keeps the choice per
      tab. The drag means what the kind needs: two opposite corners for a box,
      two endpoints for a line or arrow, the traced outline for a polygon. The
      capture is F10-3j's, so points are page space and a rotated or zoomed
      view draws where the pointer is. The trace is reduced with
      Ramer–Douglas–Peucker at `PEN_MIN_STEP / zoom`, so a shaky hand becomes
      the corners it meant rather than hundreds of vertices, and a trace closed
      back on its start does not repeat the first point. One drag is one
      `AddShape` and one Ctrl+Z; a click makes no shape, Escape or another
      tool gives up an unfinished one, and points off the page are ignored so
      the shape stops at the paper. The preview paints in view space in the
      chosen colour and thickness, arrowhead included, and F10-3k's pickers
      appear for this tool too.)
- [x] Stamps: the standard pre-defined rubber-stamp images (**F10-3n.** The
      fourteen `/Name`s every PDF reader knows — Approved, Draft, Confidential,
      Top Secret, Sold and the rest — are real `/Stamp` annotations whose
      appearance MuPDF draws from the name alone, so the mark looks the same in
      another reader without PDFAR shipping an image. `AddStamp` places one
      centred on a page-space point, fits it to the built-in 3.8:1 box within
      60 % of the page and slides it back inside at the margins. The manager
      reads `/Stamp` back into `StampAnnotation`, blanking MuPDF's own
      `/Contents` caption, so a stamp lists, takes a note, deletes, undoes and
      survives save/reopen through the code the other kinds left generic. The
      Stamp tool is a click, not a drag, and its split button offers all
      fourteen and keeps the chosen stamp per tab.)
- [ ] Custom stamps: a text caption or an image file as the stamp's appearance
      (**F10-3o, next**)
- [ ] Measurement annotations (`/Projection`), with the scale they are drawn in
- [ ] Appearance streams, so other readers show the same thing without
      regenerating them
- [ ] XFDF import/export

## F10-4 Page manipulation (was F6) — [ ]

- [ ] Rotate permanently
- [ ] Delete, extract, duplicate, move, reverse
- [ ] Insert blank page
- [ ] Split, merge/combine
- [ ] Page boxes (crop)
- [ ] Inherited page-tree attributes (`/Resources`, `/MediaBox`, `/CropBox`,
      `/Rotate`) pushed down and the tree flattened before any reorder
- [ ] Cross-document copy that rewires parents, destinations and fields, and
      drops links whose target was not copied
- [ ] Metadata and outline preserved; outline entries pointing at deleted pages
      removed
- [ ] Every operation undoable, and destructive ones confirmed

## F10-5 True redaction (was F7-3) — [ ]

A black rectangle is not redaction. The Erase tab of the numbering panel is
explicitly a cover, not a redaction.

- [ ] Redaction marks drawn in the view, with a preview
- [ ] `page.add_redact_annot()` + `apply_redactions()` to delete the operators
- [ ] Remove overlapping annotations, reply threads, popups, form fields and
      structure-tree references
- [ ] Strip XFA packets and document metadata when they mirror redacted content
- [ ] **Proof step:** after applying, re-extract text and images from every
      redacted page and abort the whole operation if anything survives
- [ ] Force a full save afterwards — the previous revision still holds the secret
- [ ] Report exactly what was removed, and fail closed when a page cannot be
      processed (vertical text, inline images, undecodable streams)
- [ ] Tests that assert the redacted string cannot be selected, copied, searched
      or extracted, in the saved file

## F10-6 Adding content — [ ]

- [ ] Add text (box with wrapping, font, size, colour)
- [ ] Add images, inserting JPEG bytes without recompression
- [ ] Header/footer, watermark, background
- [ ] Bates numbering (the page-numbering code is most of this already)
- [ ] Font embedding by full program, never a subset, with ToUnicode so the text
      stays searchable after saving
- [ ] Refuse to add a character no available font can draw, and name it
- [ ] Page rotation, crop box and `/UserUnit` accounted for in every placement

## F10-7 Editing existing text (was F8-1) — [ ]

The hardest step, and the one that must not be promised for arbitrary PDFs.

- [ ] A tolerant content-stream tokenizer that keeps the byte span of every
      operator, over `doc.xref_stream()` / `doc.update_stream()`
- [ ] Glyph-to-operator mapping through `page.get_texttrace()` (`seqno`,
      per-glyph `chars`)
- [ ] Splice, do not rebuild: replace the one show operator and delete its
      siblings, leaving every other byte of the page identical
- [ ] The font ladder: source font → another font of the same face on the page →
      embed the full program → generated fallback → base-14 → refuse
- [ ] Preserve text and graphics state by emitting only the delta, wrapped in its
      own `q … Q`
- [ ] Image placement editing by folding the new transform into the existing
      `q cm … Do Q`, not by nesting another graphics state
- [ ] Never share a page's `/Resources` or `/AP` dict when rewriting them

## Explicitly out of scope

- Editing inside Form XObjects, and arbitrary vector paths
- Inline images (`BI … ID … EI`)
- Keeping unrelated pages byte-identical across a full rewrite
- Reflow-preserving paragraph rewrites, OpenType shaping and CJK line breaking —
  PdfCraft needed a font engine and a Type 3 generator to make this acceptable,
  and that is a project of its own

## Rules for the whole phase

1. Never mutate the live document in place; edit a copy, adopt on success.
2. Never save over a file that has changed on disk since it was opened.
3. Refuse rather than degrade: an edit that cannot be completed exactly is an
   error the user can act on, never a silently wrong file.
4. Bound every size that comes from the file: decoded streams, operators per page,
   graphics-state depth, copied objects.
5. Every editing feature must be callable headlessly, so it can be tested without
   the GUI.
6. Signed documents are appended to, never rewritten.

---

# 8. CURRENT SPRINT PRIORITY

DO NOT attempt to implement the entire roadmap immediately.

The previous sprint list (F9-1 CI, P1-1 Tabs, P1-2 Presentation, P2-1
Bookmarks, P4-1 Highlight, P4-2 Notes) is complete and has been removed; P4-2
keeps one open item, tracked as F10-3.

The current sprint must focus exclusively on the editing foundation:

F10-0 — done
Document session (`version`, workers off the disk)

F10-1 — done
Edit framework (copy-and-adopt, undo, dirty state)

F10-2 — done
Save, Save As, Revert (atomic, verified, incremental when possible)

F10-3 — under way
Finish the annotation system. Done: annotation changes are routed through F10-1,
so they undo and mark dirty (F10-3a); a save stopped compacting the object table
(F10-3b); undo is a revision file in a scratch copy, so a highlight costs 55 ms
instead of 2.1 s and no buffer (F10-3c); a sticky note is a real `/Text`
annotation in the document (F10-3d), right-clicking a page is how you place one
(F10-3e), a comment row in the panel jumps to the comment it names (F10-3f), a
highlight lands on its words whatever the view rotation (F10-3g), the
annotation list re-reads only the pages an edit wrote to (F10-3h), a
freehand drawing is a real `/Ink` annotation in the document (F10-3i), the pen
tool draws one where the pointer is, under any rotation (F10-3j), and the pen's
colour and thickness come from pickers beside the tool (F10-3k); shapes are real
`/Square`, `/Circle`, `/Line` and `/Polygon` annotations (F10-3l), and the shape
tool drags all five kinds with a live preview (F10-3m); the 14 standard rubber
stamps are real `/Stamp` annotations with a click-to-place tool (F10-3n).
Next, in order: custom stamps (F10-3o), measurement, appearance streams
so other readers show what PDFAR wrote, and XFDF import/export.

Goal:

PDFAR 2.1

The sprint must be small, verifiable, and stable.

Do not add extra features simply because they seem easy.

---

# 9. AGENT WORK PROTOCOL

For each task:

## Step 1 — Inspect

Inspect related code.

## Step 2 — Baseline

Run tests before modifying. ## Step 3 — Technical plan

Identify:

- files to be modified
- new classes
- new signals
- new tests
- potential risks

## Step 4 — Test first

Create or modify tests before implementation, where reasonable.

## Step 5 — Implement

Make the minimum necessary change.

Do not rewrite entire modules unnecessarily.

## Step 6 — Test

Run:

python3 -m pytest tests/ -v

## Step 7 — Repeat

Run the full suite three consecutive times.

Do not accept:

- crashes
- flaky tests
- inconsistent results

## Step 8 — Visual verification

If it affects the GUI:

tools/screenshot.py

Create visual evidence.

## Step 9 — Performance

Compare against the baseline.

## Step 10 — Review

Check for:

- race conditions
- memory leaks
- live threads
- incorrectly connected signals
- circular references
- unclosed PyMuPDF documents
- QPixmap usage outside the GUI thread
- overlays polluting the cache

## Step 11 — Commit

One logical task = one commit.

The commit message must clearly explain what was implemented.

---

# 10. DO NOT BREAK WHAT ALREADY WORKS

Before modifying:

render.py
viewer.py
session.py
cache.py
geometry.py

fully understand how they work.

The current architecture solved significant historical issues:

- render results that never reached the GUI
- incorrect visible ranges
- obsolete geometry after zooming
- unused cache
- search operations that never executed
- problematic lambda connections
- shared PyMuPDF documents
- highlights accumulating in bitmaps
- incorrect cache invalidation

Do not reintroduce any of these issues.

---

# 11. TESTING MATRIX

Each major release must test at least the following:

### PDFs

1. Small PDF
2. 200-page PDF
3. PDF with images
4. PDF with heavy text
5. PDF with multiple columns
6. Rotated PDF
7. Protected PDF
8. PDF without text
9. PDF with bookmarks
10. PDF with annotations
11. PDF with forms
12. Potentially corrupt PDF

### Operations

- open
- close
- change page
- scroll
- zoom
- rotate
- search
- select
- copy
- switch tab
- annotate
- save
- reopen

---

# 12. MEMORY MANAGEMENT

PDFAR must be able to handle large documents.

Never unnecessarily load all pages as giant images. Use:

- lazy rendering
- priority rendering
- limited cache
- eviction
- moderate prefetching

The cache must be limited by memory usage, not simply by the number of pages.

Any new cache must document:

- maximum size
- eviction policy
- ownership
- thread ownership

---

# 13. RESPONSIVENESS

The GUI must never be blocked by:

- rendering
- searching
- OCR
- bulk text extraction
- heavy operations
- document comparison

A heavy operation must:

- execute outside the GUI thread
- display progress when appropriate
- be cancellable when reasonable
- shut down cleanly

---

# 14. SECURITY BY DESIGN

PDFAR processes potentially untrusted documents.

Therefore:

- do not automatically execute PDF JavaScript
- do not automatically open attachments
- do not automatically follow URLs
- do not trust filenames
- validate inputs
- handle exceptions
- limit resource-intensive operations
- prevent crashes caused by malformed documents

A PDF must be considered UNTRUSTED INPUT.

---

# 15. PRODUCT DECISIONS

Do not constantly ask the user for technical details.

Make reasonable technical decisions.

Ask the user only when there is a purely product-related decision that significantly alters behavior.

Examples:

- Should PDFAR digitally sign documents or only verify signatures?
- Should dark mode be automatic (system-based) or user-configurable?
- Should annotations be saved automatically or require a manual "Save" action?

Do not ask about:

- class names
- filenames
- internal structure
- tests
- threading architecture
- cache implementation
- PyMuPDF details

Those decisions are the agent's responsibility, while always adhering to the Golden Rules. ---

# 16. REPORT AFTER EACH TASK

Upon completing each task, report the following:

## Task

Name:

## Status

DONE / BLOCKED / PARTIAL

## Files changed

List of files.

## Tests

Result of:

pytest

and number of runs.

## Visual verification

Indicate whether a screenshot was taken or if not applicable.

## Performance

### Large / Scanned PDF Performance Engineering Milestone — [x] Implemented

**Completed**: 2026-10-07

**Scope**: Optimize opening, rendering, and navigation of large and scanned PDFs (100+ pages, high-resolution images).

**Verified Benchmark Results** (540-page LibreOffice Getting Started Guide, 24 MB):

| Metric | Baseline | After | Improvement |
|---|---:|---:|---:|
| open_document_time | 2.78 s | 1.96 s | 30% faster |
| jobs_queued_during_open | 2,052 | 0 | Eliminated |
| time_to_first_page | 41.47 s | 0.66 s | 63x faster |
| time_to_viewport_complete | 0.36 s | 0.0003 s | 1200x faster |
| jump_to_distant_page | 0.96 s | 0.43 s | 2.2x faster |
| zoom_sequence (100%→200%→300%) | 19.4 s | 1.81 s | 10.7x faster |
| peak_rss_mb | 4,783 MB | 642 MB | 7.5x less memory |

**Major Technical Changes**:

1. **Raw pixel buffer transport** — Replaced PNG encode/decode round-trip with direct pixel samples transfer (182x faster render delivery).
2. **Lazy, viewport-aware thumbnail rendering** — Only visible thumbnails rendered; stale jobs cancelled (540 → 5 thumbnails at open).
3. **Correct memory-level default** — Fixed persisted "greedy" preload bug; default now "normal".
4. **Faster page metadata** — `doc[i].rect` instead of `doc.load_page(i).rect` (3x faster).
4. **Per-worker DisplayList caching** — Reuse drawing command list for repeated zooms/tiles (2x zoom speedup).
5. **Visible-content-first rendering** — Priority queue + cancellation ensures visible pages render first.

**Documentation**: `docs/en/developers/performance/large-pdf-performance.md`

---

### Remaining Performance Work

| Item | Status | Description |
|---|---|---|
| Tile cache memory budget | [~] Partially | Tiles not bounded by byte budget; should use unified cache policy |
| AnnotationManager lazy loading | [ ] Not implemented | Scans all pages on init (~0.12 s for 540 pages) |
| Bounded DisplayList per-worker cache | [ ] Not implemented | DisplayList cache grows with unique pages/zooms |
| Manual Okular comparison | [ ] Not implemented | Controlled side-by-side benchmark not yet performed |

---

### General Performance Tracking

Baseline: See table above.

Current: See table above.

Delta: See table above.

## Technical debt

List of introduced or existing technical debt.

## Risks

Remaining risks.

## Next recommended task

A single next task.

---

### Page Numbering & Right Dock Milestone — [x] Implemented

**Completed**: 2026-10-25

**Scope**: Replace the modal Page Numbers dialog with an embedded, icon-only
tab in the right-hand Tools dock; add a live mask preview that can be dragged
on the page, explicit start/end ranges, and unit-aware offsets.

**Status**: DONE

**Files changed**:

- `src/pdfar/page_numbers.py` — settings model, units, `NumberBox`,
  `box_to_settings`, mask stamping, embedded `PageNumbersPanel`.
- `src/pdfar/viewer.py` — mask/dashed/red preview, hit-testing, drag-to-move
  with rotation-aware deltas, `numbering_changed`, Escape cancellation.
- `src/pdfar/main.py` — two icon-only right-dock tabs (Search, Page Numbers),
  preview lifecycle, reflow handoff, save-copy flow, dark-mode tinting.
- `src/pdfar/geometry.py` — `unmap_delta`.
- `assets/icons/search.svg`, `assets/icons/page_numbers.svg`.
- `tests/test_page_numbers.py`, `tests/test_geometry.py`,
  `tests/test_reflow.py`.
- `README.md`, `docs/en/developers/ui/page-numbering.md`, `ROADMAP.md`.

**Tests**: `PYTHONPATH=src python3 -m pytest tests/ -q` — **228 passed**.

**Visual verification**: Captured light and dark screenshots of the two-tab
dock, the mask covering an old printed number, the red preview and the Search
tab. The mask, dashed outline and drag target use the same `NumberBox`
geometry as the exported copy.

**Architecture notes**:

1. Canonical offsets stay in PDF points; unit switches only change the
   displayed values, avoiding rounding drift between pt/mm/cm/in.
2. “End page number” keeps the “last page” semantic until the user edits it,
   because the panel is built before a document is open.
3. Dragging recomputes from the press-time box on every mouse move, so
   centre-to-edge re-anchoring never makes the glyphs jump.
4. The white mask is a filled `draw_rect` stamped before the text, using the
   same display→unrotated conversion as `insert_text(rotate=…)`.

**Documentation**: `docs/en/developers/ui/page-numbering.md`

---

### Numbering Mask Customization — [x] Implemented

**Completed**: 2026-10-25

**Scope**: Let the mask behind each page number grow independently on every
side (for larger old numbers) and take any colour, including presets, a colour
dialog, and an on-screen eyedropper for tinted paper.

**Status**: DONE

**Files changed**:

- `src/pdfar/page_numbers.py` — per-side mask pads and `mask_color` in
  `PageNumberSettings` (with legacy `maskPadding` migration), `mask_rect`,
  `mask_rgb`, `normalize_color`, preset swatches, `QColorDialog`, and the
  `ScreenColorPicker` overlay plus its `sample_screen_color` mapping.
- `src/pdfar/viewer.py` — the preview fills `box.mask_rect` with the chosen
  colour instead of a fixed white.
- `tests/test_page_numbers.py` — per-side growth/clamping, stamp and preview
  colour, palette/custom selection, eyedropper mapping and buttons.
- `README.md`, `docs/en/developers/ui/page-numbering.md`, `ROADMAP.md`.

**Tests**: `PYTHONPATH=src python3 -m pytest tests/ -q` — **240 passed**.

**Architecture notes**:

1. Mask pads stay in PDF points like the number offsets, so unit switches do
   not drift.
2. Drag clamping measures the padded box, so a wide mask never pushes the
   glyphs outside the page.
3. The eyedropper captures the screen before showing its overlay; otherwise
   the overlay itself would be sampled.
4. Exported masks are opaque: the point is to hide the old number exactly.

---

### Page Numbers Sub-tabs — [x] Implemented

**Completed**: 2026-10-25

**Scope**: Split the embedded numbering panel into **Position** (default) and
**Mask** sub-tabs so only one group occupies vertical space in the narrow
right dock. The numbering range, range summary and `Save Copy…` stay below
both tabs.

**Status**: DONE

**Files changed**:

- `src/pdfar/page_numbers.py` — `QTabWidget` (document mode) hosting the
  position and mask pages; the group-box wrappers became plain pages.
- `tests/test_page_numbers.py` — sub-tab labels/order, default index and
  widget ownership.
- `README.md`, `docs/en/developers/ui/page-numbering.md`, `ROADMAP.md`.

**Tests**: `PYTHONPATH=src python3 -m pytest tests/ -q` — **241 passed**.

**Architecture notes**:

1. Hidden pages keep their widgets alive, so signals, preview updates and
   `apply_settings` work exactly as before.
2. The tab widget is sized by its tallest page (Position), so switching to
   Mask does not resize the dock.

---

### Page Number Fonts — [x] Implemented

**Completed**: 2026-10-25

**Scope**: Let page numbers use any installed system font and any of its
styles, not only the PDF base-14 families. Add a recommended Debian/Ubuntu
font catalog (Liberation, DejaVu, Noto, Google Fonts, URW, plus optional IBM
Plex and Red Hat) shown first in the UI, and a **Choose…** dialog with a
searchable tree, style list, size field, live sample and install hints.

**Status**: DONE

**Files changed**:

- `src/pdfar/fonts.py` (new) — catalog, Qt/fontconfig resolution, style
  matching, preview fonts and install hints.
- `src/pdfar/page_numbers.py` — persisted `font_style`, font/style combos,
  `FontDialog`, `pdf_number_font` and `number_font_source`; stamping embeds
  the resolved font file.
- `src/pdfar/viewer.py` — preview uses `pdf_number_font`.
- `tests/test_fonts.py` (new), `tests/test_page_numbers.py` — catalog,
  aliases, resolution, embedding, dialog and panel coverage.
- `README.md`, `docs/en/developers/ui/page-numbering.md`, `ROADMAP.md`.

**Tests**: `PYTHONPATH=src python3 -m pytest tests/ -q` — **281 passed**.

**Architecture notes**:

1. Font availability comes from `QFontDatabase`, not `fc-match`: fontconfig
   substitutes a different family for missing fonts, so `fc-match` would
   report every recommended family as available.
2. `fc-list` is used only to map a family/style pair to a font file; the
   numbered copy calls `insert_font(fontfile=…)` once per page and embeds a
   TTF/OTF, so it renders identically elsewhere.
3. Base-14 families still use PyMuPDF's styled aliases (`hebo`, `tibi`, …) and
   need no embedding.
4. `apply_style` stops at the first weight keyword so `SemiBold` cannot be
   downgraded by the generic `bold` match.
5. `RECOMMENDED_FONTS` is the single source for the dialog and the install
   hint; missing entries stay visible but disabled.

---

### Erase Mask — [x] Implemented

**Completed**: 2026-10-26

**Scope**: Add an **Erase** sub-tab to the page-numbering panel that covers
existing page numbers with a blank rectangle instead of adding a new number —
for example to remove a header/footer numbering before stamping new numbers
elsewhere. The erase rectangle has its own placement, width, height, offsets
and colour, shares the page range and unit with numbering, is draggable on the
page, and exports a new `name-erased.pdf` copy.

**Status**: DONE

**Files changed**:

- `src/pdfar/page_numbers.py` — `EraseSettings` (`pageNumbers/erase/*`),
  `erase_box`, `erased_copy_path`, `_save_modified_copy`, `erase_copy`; a
  third sub-tab built from width/height/placement/offsets/colour controls; a
  shared Units row; mode-aware range group, labels, save button and preview
  settings; `apply_erase_settings` for drag round-trips.
- `src/pdfar/viewer.py` — the preview pipeline accepts either settings type;
  `_preview_box` picks `erase_box`; erase draws only fill + dashed outline.
- `src/pdfar/main.py` — loads `EraseSettings`, previews the active sub-tab,
  routes drags by type, and saves erased copies through `_save_erased_copy`.
- `tests/test_page_numbers.py` — geometry, export, persistence, sub-tab
  relabelling, colour isolation, preview, drag and save coverage.
- `README.md`, `docs/en/developers/ui/page-numbering.md`, `ROADMAP.md`.

**Tests**: `PYTHONPATH=src python3 -m pytest tests/ -q` — **292 passed**.

**Architecture notes**:

1. Erase reuses `NumberBox`/`box_to_settings` with zero pads, so hit-testing,
   clamping and dragging need no separate implementation; `erase_box` returns
   a zero-pad box whose `mask_rect` is the rectangle itself.
2. The rectangle is clamped inside the page (width, height and margins), so a
   mask wider or taller than the paper covers the whole page instead of
   spilling outside.
3. This is a cover rectangle, not redaction: the underlying text/image remains
   in the PDF. The Erase tab says so explicitly.
4. One rectangle per page in v1; multiple erase bands would require a list
   model but the export loop already iterates pages.

---

### PDF Editing Master Plan — [x] Implemented

**Completed**: 2026-10-26

**Scope**: Study how the reference Rust editor `pdfcraft` performs PDF editing,
record the findings so they outlive the reference copy, and turn them into the
F10 phase of this roadmap. Refresh the stale state and comparison sections, and
adopt the `[x] / [~] / [ ]` status convention throughout.

**Status**: DONE

**Files changed**:

- `docs/pdfcraft-editing-analysis.md` (new) — the object-graph-as-model,
  copy-and-adopt edits, snapshot undo, incremental save, rendering from the bytes
  that will be saved, content-stream splicing, the font ladder, redaction that
  aborts on residue, and the refusal/permission/budget stance.
- `ROADMAP.md` — new F10 phase with eight ordered increments; the status legend
  and rules in section 7; the section 6 matrix converted to checkboxes with every
  value re-verified against the code; the module and capability lists in section 1
  brought up to date; F6, F7-3, F8-1 and P4-8 pointing at their F10 successor;
  the obsolete first-sprint lists in sections 8 and 20 replaced.

**Verified against the code, not assumed**:

1. Highlights persist through an incremental save; notes do not — so P4-2 and the
   Annotations row stay `[~]`.
2. Four readers open the document from disk (`render.py:232`, `search.py:235`,
   `reflow.py:824`, `main.py:1515`) while the view holds its own handle, and the
   cache key has no content dimension (`cache.py:40`). That is why F10-0 exists
   and why it comes first.
3. `page.add_redact_annot()` + `apply_redactions()` really do delete the operators
   (the page stream became `q Q` and text extraction returned empty), which is the
   base F10-5 builds its proof step on.

**Tests**: unchanged — this milestone adds no runtime code.

---

### F10-0 Document Session — [x] Implemented

**Completed**: 2026-10-26

**Scope**: Make one in-memory document authoritative, so a change that has not
been saved to disk is what every reader — pixels, words, search, reflow,
properties — sees.

**Status**: DONE

**Files changed**:

- `src/pdfar/session.py` (new) — `DocumentSession(doc, path, password)`:
  `version`, `content_changed()`, `snapshot()`, `read_version()`,
  `open_copy(version)`, `close()`. `version == 0` keeps the file on disk as the
  authority (no snapshot is ever built); the first accepted change publishes
  bytes that every worker then reads.
- `src/pdfar/render.py` — `RenderPool(session)`, jobs and results carry the
  content version, the in-flight key includes it, and a queued job whose content
  changed produces no result at all.
- `src/pdfar/search.py`, `src/pdfar/reflow.py` — workers take the session; the
  GUI-thread submission publishes the bytes their thread will read.
- `src/pdfar/viewer.py` — owns the session, adds `invalidate_content()` and the
  `content_changed(int)` signal, and drops stale render results.
- `src/pdfar/sidebar.py` — drops stale thumbnails and rebuilds them on
  `invalidate_thumbnails()`.
- `src/pdfar/main.py` — search and Document Properties read through the session;
  the view's `content_changed` is wired to the sidebar.

**The rule that keeps this safe**: only the thread that created the session may
serialize it (`read_version()` on the GUI thread). A worker that asks for
unpublished content gets `SnapshotNotPublished` rather than a MuPDF data race,
because `fz_document` allows one caller at a time.

**Measured**: GS262 (540 pages) — open 1.89 s, first page 0.165 s, distant jump
0.42 s, peak RSS 408 MB; unchanged from the pre-F10 baseline, since the
untouched document still renders straight from the file.

**Tests**: 383 passed (was 368) — `tests/test_session.py` adds 15, covering the
snapshot lifecycle, version-pinned renders, dropped stale jobs and results,
in-memory visibility for render/search/reflow, the off-thread serialization
guard, and that the file on disk never changes.

---

### F10-1 Edit Framework — [x] Implemented

**Completed**: 2026-10-27
**Commits**: `4c8e7b5` (framework), plus the dirty-state UI

**What landed**: the whole phase turns on one rule — *an edit never touches the
document the user is looking at*. `Edit` carries intent (`label`, `scope`,
`apply(doc)`); `DocumentSession.apply()` runs it against a throwaway clone made
from `doc.tobytes()`, and only a clone whose `apply()` returned replaces the live
document. A failed edit therefore cannot be half-applied, cannot be observed
halfway by a render worker, and leaves no history entry.

**Files changed**:

- `src/pdfar/edits.py` (new) — `Edit`, `EditError`, `Scope` (IntFlag:
  `METADATA / STRUCTURE / CONTENT / COMMENTS / LINKS / FORM`), `SetMetadata` as
  the reference edit, and `EditHistory` with `_Step(label, scope, before)`.
- `src/pdfar/session.py` — `apply()`, `undo()`, `redo()`, `_adopt()`, `dirty`,
  `mark_saved()`, `generation`; `close()` releases the byte snapshots.
- `src/pdfar/viewer.py` — `invalidate_content()` became `invalidate(scope)`,
  `apply_edit()` / `undo_edit()` / `redo_edit()`, `_refresh_page_geometry()`,
  `content_changed(int, int)` now carries the scope, and the `doc` property is
  documented as "read it through the session, never store it".
- `src/pdfar/sidebar.py` / `annotations.py` / `bookmarks.py` / `tabs.py` — every
  holder of a `fitz.Document` now holds the session, because `_adopt()`
  invalidates a stored handle.
- `src/pdfar/main.py` — Edit menu `Undo <step>` / `Redo <step>` with the standard
  shortcuts, `[*]` title + `setWindowModified()`, the `•` tab marker, and
  Save / Discard / Cancel before any tab or window closes.

**Undo is bytes, not deltas.** Each step stores the complete document as it was
*before* the edit, so undo is "open these bytes and adopt them" — the one undo
that cannot corrupt a PDF, since it never has to invert an operation. Whole
documents are large, so `EditHistory` is bounded twice over: `max_steps=50` and
`max_bytes=256 MB`, oldest evicted first, and the redo branch shares the byte
budget so a deep undo cannot double the cost.

**Two counters, because they answer different questions.** `version` says *which
content* — every accepted change bumps it once, and readers are turned away by
it. `generation` says *which object* — only `_adopt()` bumps it, so an in-place
annotation change keeps the identity its holders still rely on. UI code holding a
`Document` from an older generation is looking at a closed handle.

**Measured**: the open path is untouched — `tobytes()` first runs when an edit is
accepted, not when a document is read. GS262 (540 pages), two rounds against the
F10-0 baseline on a busy machine: F10-1 open 2.48 / 2.14 s, first page 0.24 /
0.25 s, distant jump 0.81 / 0.58 s, scroll 2.34 / 3.37 s, peak RSS 449 / 494 MB;
F10-0 3.30 / 2.10, 0.34 / 0.41, 0.74 / 0.56, 3.77 / 3.36, 495 / 513 MB. No
regression.

**Verified by eye** (headless, on a copy of `demo.pdf`): applying an `AddText`
edit paints the new text on the page *and* in the sidebar thumbnail, the tab
gains `•`, `isWindowModified()` is True, and the Edit menu reads
`Undo Add text "F10-1"`; undoing it repaints the original page while the dirty
mark stays (the content still differs from the file), `Redo` takes over the name
and `Undo` greys out; a failing edit leaves the page, the version, the generation
and the history exactly as they were; and closing the dirty tab asks
"…has changes that are not in the file" instead of discarding. The copy on disk
was byte-identical throughout.

**Tests**: 416 passed (was 383) — `tests/test_edits.py` (21) for the framework
and `tests/test_edits_ui.py` (12) for the title, tab marker, menu labels and the
close dialog.

**Found here, deferred to F10-2**: `tobytes()` is not deterministic (MuPDF
regenerates the second `/ID` element, so "unchanged" cannot be proven by bytes),
an edited encrypted document is decrypted in memory, the first adoption makes
`can_save_incrementally()` False, and Save As orphans the path-keyed
`viewState/` and `bookmarks/` settings.

### F10-2 Save, Save As, Revert — [x] Implemented

**Completed**: 2026-10-27

**What landed**: a document reaches the disk in exactly one way. `save.py` stages
the bytes in a temporary file beside the target, `flush` + `os.fsync` them, reads
them back and proves the page count and a render, and only then `os.replace`s the
target — so the file the user has is either the old one or the new one, never a
half of each. Every refusal is a `SaveError` with a sentence the user can act on.

**Files changed**:

- `src/pdfar/save.py` (new) — `staged_write`, `verify_bytes` / `verify_file`,
  `Encryption` (+ `capture`, `save_kwargs`, `reproducible`, `is_owner_access`),
  `SaveReport`, `SaveError`, `ProtectionError`, `save_document`.
- `src/pdfar/session.py` — `encryption` captured at open (the only surviving
  record of it, since `tobytes()` of an authenticated document is *decrypted*),
  `revert()`, `require_saved_file_unchanged()`, and a `_disk_stamp` refreshed by
  `mark_saved()`.
- `src/pdfar/annotations.py` — `sync_to_document()` (materialize the highlights,
  skip the work when the model already matches this document) and
  `reload_from_document()` (what Revert needs).
- `src/pdfar/bookmarks.py` / `main.py` — `settings_key_for()` and
  `view_state_key()` so a rename can move what it is keyed by.
- `src/pdfar/sidebar.py` — the Highlights tab's Save button now emits
  `save_requested` instead of writing on its own and guessing at the result.
- `src/pdfar/main.py` — File ▸ Save / Save As… / Revert to Saved, one
  `_write_tab()` funnel, the protection prompt, the state migration, and the
  close dialog's Save finally meaning Save.
- `src/pdfar/viewer.py` — passes the `authenticated` result of the open-time
  password to the session, which is what decides whether a password can be
  re-applied later.

**Incremental is the exception, and the predicate has to be honest.** Appending
touches no inode, so there is nothing to replace and nothing to verify by rename;
it is chosen only while the document is still the file handle it came from.
`can_save_incrementally()` alone is not that test — it stays True for a
stream-backed clone and the save then raises — so `_can_append` also requires
`doc.name` to be the target and `doc.is_dirty` to be set.

**A password PDFAR never saw cannot be rebuilt.** An owner-only file opens
without asking (`needs_pass == 0`), so the session holds the *method* and the
*permissions* but no secret. Rewriting it therefore raises `ProtectionError` and
the user chooses: don't save, or save a copy with a fresh random owner password
and the original permissions. Silently dropping the encryption is not offered.
`owner_pw == user_pw` is also refused on the way in, because MuPDF answers such an
authentication with 6 and resets the permissions to full. *(This was the story for
a document serialized out of memory. Since F10-3c the working copy is a copy of
the *file*, so MuPDF rebuilds the encryption from the encryption dictionary it
read and an edited owner-only document saves with its protection — and its
unknown owner password — intact. The refusal is now the fallback's behaviour: it
comes back only when there is no scratch space and the edit happened in a buffer.)*

**Measured**: GS262 (540 pages) open 2.12 s, first page 0.25 s, distant jump
0.70 s, zoom sequence 2.81 s, peak RSS 508 MB — unchanged from F10-1, since
saving is not on the read path. A full rewrite after deleting a page took the
sample file from 3836 to 3301 bytes and its xref table from 29 entries to 25;
the same document saved incrementally kept all 29 and grew. *(The rewrite level
in this report was `garbage=3`, which turned out to cost 121 s on GS262 against
1.4 s for the level that keeps the promise; see F10-3b.)*

**Verified by eye** (headless, on a scratch document): the tab shows
`• f10_visual.pdf` and the title `f10_visual.pdf[*]` with Revert enabled; after
Ctrl+S the bullet is gone, Revert greys out and the status bar reads
"f10_visual.pdf written — 3 KB"; a second Ctrl+S answers "Nothing to save" and
leaves the file's mtime alone; Save As retargets the document, writes the copy,
leaves the original untouched and moves both `viewState/` and `bookmarks/`
entries; Revert restores the file's content, empties the undo history and drops
the highlight that was never saved while keeping the one that was; and a
read-only target reports "…is read-only. Use Save As to keep a copy" with the
edit still in memory and still marked dirty.

**Tests**: 468 passed (was 416) — `tests/test_save.py` (33) for the disk-facing
logic and `tests/test_save_ui.py` (19) for the commands, the dialogs and the
migration. Three consecutive full runs.

**Found here, left open**: a signed document whose save would break the
signature is not detected (nothing in PDFAR reads signatures yet), and
highlights are still invisible to `session.dirty`, which is why Save is not
gated on it — both are recorded under F10-3. *(The second one closed in F10-3a;
Save stays ungated for the ordinary reason — a clean document can still be asked
for a copy of itself under a new name.)*

---

### F10-3a Annotations are edits — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the document became the annotation store. Before this, a
highlight lived in `AnnotationManager`'s model and was pushed into the document
by `sync_to_document()` at Save time (F10-2), which made it invisible to the undo
stack and to `session.dirty` and dependent on a "have I written my model into
this document yet" cache. Now creating, recolouring, noting and deleting a
highlight are each an `Edit` with `Scope.COMMENTS` applied through the session,
and `AnnotationManager` is a *view* of the document that re-reads itself.

**Identity is the annotation's `/NM` name**, so a sidebar row, a model key and a
PDF object agree across an undo, a save and a reopen. A foreign annotation with
no `/NM` is filed under a read-only `anon:{xref}` key — opening a file must not
have to write into it. An edit that cannot find its id refuses the document
(`EditError`) rather than writing to whatever shares the key, and a refused edit
records nothing and changes no content version.

**Files changed**:
- `src/pdfar/annotations.py` — `AddHighlight`, `RemoveHighlight`,
  `SetHighlightColor`, `SetHighlightNote` (each `Scope.COMMENTS`);
  `AnnotationManager` rebuilt around `session.apply()` and
  `reload_from_document()`; `sync_to_document()`, the `_pending` / `_materialized`
  cache and `update_annotation()` deleted; the model's own `set_note()` gone,
  because a note is now an edit rather than a field. *(The last two names became
  `RemoveAnnotation` and `SetAnnotationNote` in F10-3d, once a standalone note
  needed them too.)*
- `src/pdfar/viewer.py` — the highlight overlay painting is deleted (the renderer
  draws annotations, so the overlay double-painted the band); the manager's
  `annotations_edited` / `annotation_edit_failed` signals drive
  `invalidate(scope)` and the status bar; `invalidate()` re-reads the annotation
  list, which is what makes undo, redo and Revert agree with the sidebar.
- `src/pdfar/sidebar.py`, `src/pdfar/main.py` — the context menu and Save go
  through the manager's edits instead of reaching into `_annotations` or syncing a
  model before writing.

**Two PyMuPDF behaviours worth recording**: `Annot.set_info(content="")` *keeps*
the old note — clearing means `xref_set_key(xref, "Contents", "null")`; and an
`Annot` handle is unbound the moment its `Page` is garbage-collected, so every
edit keeps the page in a named local.

**Measured** on GS262 (540 pages, 25 MB): open 1.87 s, first page 0.199 s,
distant jump 0.484 s, zoom 2.17 s, peak RSS 496 MB — against the same tree at
HEAD 1.88 s / 0.190 s / 0.655 s / 2.53 s / 470 MB, i.e. the read path is
unchanged and the differences are run-to-run noise. The new cost is per edit:
**one highlight on that file takes 2.1 s and about 25 MB**, because the undo step
is a whole-document `tobytes()` snapshot, and ten of them reach the history's
256 MB cap. That is the price F10-1 already charged for rotating a page — but
highlighting is the action users really do, so undoing a comment-only edit by
inverse intent is now its own open item under F10-3.

**Verified by eye** (headless, on `demo.pdf`): Ctrl+A + highlight paints the band
once — the pixels read (248, 248, 152), which is one pass of `#FFFF00` at `/CA`
0.4 over the page's near-white ground, where a second pass would land near 92 —
the thumbnails repaint in the same step, the tab gains `•`, the Edit menu reads
"&Undo Highlight", and the file on disk is untouched until Save.

**Tests**: 490 passed (was 468) — `tests/test_annotations_edits.py` (23) for the
document-as-store, identity, granularity, undo labels and refusal; the older
`test_z_highlights.py` / `test_z_notes.py` re-targeted at the new API, which is
where "the handle you held before an edit is the read from before it" is now
pinned down. Three consecutive full runs.

---

### F10-3b A save stops compacting the object table — [x] Implemented

**Completed**: 2026-10-27

**What landed**: one option, `garbage: 3` → `garbage: 2`, in the full-rewrite
path of `save_document()`. F10-2 chose level 3 to honour "a deleted page must
leave the file", and nothing in the suite could see the difference — the promise
holds at level 2 as well, and the price only shows on a book big enough to have
a 38 000-entry object table.

**What the levels actually do** (measured, not read out of a docstring): level 1
writes the unreachable objects' *bytes* out — the sample document's dropped page
took the file from 3836 to 3381 bytes — while the cross-reference table keeps its
29 slots. Level 2 additionally merges duplicate streams and shrinks the table to
25, at the same cost. Level 3 then compacts cross references and strips unused
dictionary keys: on GS262 that buys 213 slots out of 37 187 — 0.6 % — and costs
**121.6 s against 1.40 s**.

**Why it is on the interactive path**: after an adopted edit `doc.name` is
`None`, so `_can_append` is False and *every* Ctrl+S on an edited document is a
full rewrite rather than the 0.03 s append. The first highlight on GS262 followed
by Save therefore took 123.9 s of frozen UI; it now takes 1.75 s, and 3.5 s
counting the undo snapshot the edit itself costs (apply 1.73 s + save 1.75 s).

**Files changed**: `src/pdfar/save.py` (the option and the comment recording the
numbers). `tests/test_save.py::test_save_as_rewrites_rather_than_appends` is the
guard: it deletes a page, saves and asserts the xref table shrank — which level 1
fails and level 2 passes, so the level cannot silently drop back.

**Measured**: GS262 read path untouched — open, first page, distant jump and
zoom are the F10-3a numbers, since nothing here runs before a save.

**Tests**: 490 passed, unchanged in count; three consecutive full runs.

---

### F10-3c Undo is a file, not a buffer — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the session keeps its live document in a private scratch
directory, and an undo step is a *revision file* rather than a `tobytes()`
buffer. `src/pdfar/revisions.py` (new) owns the directory — `create()`,
`from_file(path)` which seeds it with a copy of the user's file, `next_path()`
handing out `r0001.pdf`, `r0002.pdf`, …, `copy_into`, `release`, `size_of`, and
`close()` which removes the whole tree. `edits.py` gained `Snapshot` (bytes *or*
a path, with the `size` the byte budget counts) and an `on_release` callback, so
a step that leaves the history takes its file with it. `session.py`'s editing
core is now: copy the current revision → apply the edit to the copy → append to
the copy → adopt it, and record the file the copy came from as what undo returns
to. Readers go the same way: `open_copy()` opens the revision instead of parsing
a buffer, and `content_path()` tells the render workers which file to read.

**The cheaper idea that did not work**: an undo point as a byte offset into one
appended file. MuPDF's incremental save *rewrites the section a previous save
appended* — the prefix bytes shift and the object order changes — so a truncate
to a recorded mark produced a document with the annotations gone. Measured by
diffing the prefix of a save against the file it was appended to, then
abandoned. Whole revisions are the honest unit.

**What it costs** (GS262, 540 pages, 25 MB, through `DocumentSession`):

| step | before | after |
|------|--------|-------|
| one highlight | 2.1 s and ~25 MB of RAM | **54–70 ms, no buffer** |
| undo | reopen of a serialized copy | **4.5 ms** |
| redo | — | **3.4 ms** |
| a reader's page fetch | parse a 25 MB buffer | **27.6 ms** off the file |
| Ctrl+S afterwards | 1.75 s | 1.95 s (unchanged rewrite) |

Saving is still a full rewrite because the live document's `doc.name` is now a
scratch path, never the user's — `_can_append` correctly refuses to append into
someone else's file. The 256 MB history cap was written for RAM and now bounds
scratch disk: three highlights plus the seed sit at 100 MB, so the book keeps
about ten revisions before the oldest steps fall off, and the directory is gone
when the session closes.

**Fallbacks, because an edit must not fail for want of a temp dir**: a session
with no file to copy, no writable scratch, or a scratch that stops working edits
the way F10-1 did — `tobytes()` buffers in RAM — chosen per session on the first
accepted edit. `revert()` clears the revisions and the next edit re-seeds from
the file the user just restored.

**Two consequences found here**:
1. Four view tests in `tests/test_edits.py` started failing because a render
   job queued before `close()` called `read_version()` afterwards, when the
   revision pointer had already been cleared — the cached buffer used to hide
   that. `DocumentSession` now records that it is closed and reads return
   nothing instead of resurrecting content from a deleted file.
2. An **edited owner-only document keeps its protection** (see F10-2 above): the
   working copy carries the encryption dictionary, so MuPDF reproduces the
   original method, permissions *and* the owner password PDFAR never saw.
   `tests/test_save.py`'s two `ProtectionError` tests were rewritten — one now
   asserts preservation, the other forces the buffer fallback to keep the
   refusal covered.

**Files changed**: `src/pdfar/revisions.py` (new), `src/pdfar/edits.py`,
`src/pdfar/session.py`.

**Measured**: the read path is unchanged — GS262 open 1.67 s, first page 0.33 s,
distant jump 0.44 s, zoom sequence 1.85 s, peak RSS 494 MB, all inside the
F10-2/F10-3b spread.

**Verified by eye** (headless, on a copy of the book — `external/` is never
written to): three highlights on page 301, undone and redone, saved with
Ctrl+S; the saved file reopens with all three `/Highlight` annotations and the
screenshot shows the bands in the page view and in the thumbnail strip.

**Tests**: 514 passed (was 490) — `tests/test_revisions.py` (24) covers the
store's privacy, a failed write leaving nothing behind, the user's file staying
untouched, undo/redo walking the files, a structure edit round-tripping, a
half-applied edit leaving directory, content, version and history exactly as they
were, readers taking the file with no buffer published, trimming releasing files,
the byte budget counting revisions, close removing the directory, revert and
re-seeding, and both fallback routes. Three consecutive full runs.

---

### F10-3d A sticky note is a /Text annotation — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the second annotation kind. `AddNote` writes a PDF `/Text`
annotation at a point, names it with `/NM` like everything else, and goes through
the session, so it undoes, marks the document dirty and reaches the render pool as
a new content version. `AnnotationManager.reload_from_document()` now dispatches
on the annotation subtype instead of skipping everything that was not a highlight,
so a document's notes appear as `NoteAnnotation`s with a `position` — the class
that had existed, unused, since before F10-3.

**The names had to follow the model.** A note's text and a highlight's note are
the same `/Contents` key, so `SetHighlightNote` became `SetAnnotationNote`,
`RemoveHighlight` became `RemoveAnnotation` (its label was already "Delete
annotation"), `_find_highlight` became `_find_annotation`, and the manager's
`add_note_to_highlight` / `remove_note_from_highlight` / `get_note_for_highlight`
became `set_note` / `remove_note` / `get_note` — one pair of methods serving both
kinds rather than a highlight-named one that would have lied. `has_note()` and
`get_note()` moved to the base model for the same reason: the sidebar asks either
annotation the same question.

**What MuPDF does for us** (probed, not assumed): `add_text_annot(point, "")`
accepts an empty note and still generates an `/AP/N` appearance, so the icon
paints before there is any text; `update()` keeps both the icon's `/Name` and the
`/Contents`; PyMuPDF names what it creates (`fitz-A0`), which `_name()` overwrites
with our UUID; and clearing still needs `xref_set_key(xref, "Contents", "null")`
because `set_info(content="")` means "unchanged".

**One judgement call**: a note placed within 16 points of the bottom or right edge
is pulled inside the page. An icon that hangs off the page is one no reader lets
the user click, and a note they cannot open is not a note.

**Files changed**: `src/pdfar/annotations.py` (the `AddNote` edit, `_note_model`,
the subtype dispatch, the generalized names), `src/pdfar/sidebar.py` (the three
renamed calls, and "Change Color" hidden on a row it cannot act on).

**Verified by eye** (headless): a note placed through the running app at (60, 90)
marks the document dirty at version 1 with "Add note" as the undo label; Ctrl+S
writes it; a *new* window on the saved file lists it as a note at the same
position with its text, and the screenshot shows the yellow icon on the page.

**Tests**: 532 passed (was 514) — `tests/test_text_notes.py` (18) covers the
document holding the note the moment it is made, an empty note existing and
painting, the edge clamp, the two kinds staying distinguishable in the list, a
foreign `/Text` note listed under `anon:{xref}` without a `/NM` being written into
someone else's annotation, a shape being left out of the list entirely, identity
surviving an edit, "remove note" emptying the text but keeping the icon, the four
undo labels, every kind of note edit round-tripping through undo and redo, an edit
whose annotation is gone refusing rather than landing on whatever shares the key,
a page that is not there refusing, a background reader seeing the note without a
save, and a save-and-reopen round trip with a highlight in the same document.
Three consecutive full runs.

---

### F10-3e The page asks for a note — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the interface half of F10-3d. A right-click on a page offers
"Add note here…"; a right-click on a note's icon offers "Edit Note…" and
"Delete Note". The popup is one multi-line `QInputDialog`, and every answer goes
through `AnnotationManager`, so the tool inherits the document side (the note is
a `/Text` annotation) and the undo side (a revision file) instead of adding a
second way to change a PDF. `PDFView` gained `_pdf_point_at()`, `_note_at()`,
`_note_menu()`, `contextMenuEvent()`, `add_note_at()`, `edit_note()` and
`delete_note()`; the sidebar's row menu moved out of its signal handler into
`_annotation_menu(ann_id, ann_type)`, which is what made a sticky note's own text
reachable from its row — the note actions had been offered on highlight rows only,
because the note was the only kind of annotation that could not exist.

**A click is not a PDF point.** A note's position lives in page space, while a
mouse press is a widget coordinate, and between them are the scroll bars, the page
layout, the zoom and the view rotation. `_pdf_point_at()` walks that road back
using the same conversion the page-number preview already relies on
(`_content_to_page_view()` then `unmap_point()`), so the two tools cannot
disagree about where the cursor is; the round trip is pinned at five
zoom-and-rotation combinations. The page around the icon is deliberately not a
target — a click that misses the icon means "a note *here*", which is the other
half of the same menu — with two points of slack, because the icon is 16 points
across and a click rounded to a whole widget pixel lands a hair off wherever the
user aims.

**Four judgement calls.** Cancel places nothing and costs no undo step. An empty
answer still places the icon: the user asked for a note, and an empty one is a
note that has not been written in yet (F10-3d established that MuPDF paints it).
Clearing the text in "Edit Note" empties the note rather than deleting it, which
is what "Remove Note" has always meant for a highlight — deleting the icon is the
other menu item. And a menu whose annotation has disappeared says so in the status
bar instead of doing nothing: undoing some other edit can take the annotation with
it while the menu is open.

**Files changed**: `src/pdfar/viewer.py` (the note tool, the coordinate
conversion, the context menu), `src/pdfar/sidebar.py`
(`_annotation_menu()` extracted from `_on_annotations_context_menu()`, the note
actions offered on both kinds, the "Change Color" submenu no longer built for a
row that cannot use it, and a note row now reading `📝 Note: <text>` instead of
the raw `note:` subtype value).

**Verified by eye** (headless): a synthetic `QContextMenuEvent` on an empty part
of page 1 opens "Add note here…", and the note lands at the PDF point under the
cursor (120, 90 aimed, 119.7, 90.1 stored — a widget pixel of rounding); the
sidebar lists `📝 Note: check the figures on this page`; a second right-click on
the icon opens "Edit Note…" / "Delete Note" and rewrites the text with "Edit note"
as the undo label; rotating the view 90° and clicking the icon's *screen* position
returns the same PDF point and finds the same note; and the rendered page shows
the yellow icon where it was placed.

**Tests**: 549 passed (was 532) — `tests/test_note_ui.py` (17) covers the click
round trip at five zoom/rotation pairs and off the edge of the last page, a note
placed where the user clicked and on the page under the cursor, Cancel leaving the
version and the dirty mark alone, an empty answer still placing the icon, the
dialog naming the page, the icon being a target and the page around it not (one
point off its corner is still the icon), both menus offering what they should and
*doing* it when triggered — a menu carries the point and the annotation it was
built for, not whatever is under the cursor when it fires —, editing being
undoable and offering the current text, deleting taking the icon with it, a gone
annotation saying why, the sidebar row offering the note actions without a color,
a highlight row keeping its color submenu, and the row label showing the text.
Three consecutive full runs.

**Measured on the book**: the edit itself is still F10-3c — `session.apply()` for
a note on GS262 is **59 ms**, undo 10 ms, redo 5 ms, a reader's copy 13–22 ms.
What the tool adds is `AnnotationManager._run()` re-listing every page after each
edit: **108–175 ms** on 540 pages, which is where the ~220 ms a note takes in the
running app actually comes from. Recorded as its own open item above.

---

### F10-3f A comment row jumps to its comment — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the last half of the comments panel. The sidebar had listed
annotations since F10-3a, but double-clicking a row emitted
`goto_page_requested(page)` and the window centered that page — which on a long
document is a direction, not an address. A row now emits the annotation's id, the
view scrolls the annotation's own rectangle into the middle of the viewport and
draws a ring around it, and the ring goes away when the user clicks the page
again. `PDFView` gained `select_annotation()`, `clear_annotation_selection()` and
`_annotation_outline_rects()`; `Sidebar` gained the `annotation_activated(str)`
signal; `_on_annotation_activated()` now tells a comment row from a page row
instead of assuming every row is only a page.

**The selection is an id, never a rectangle.** The document can lose the
annotation between one paint and the next — an undo, a revert, a Revert to Saved —
and a ring drawn around the cached geometry would be reporting something that is
not there. So `_annotation_outline_rects()` resolves the id through the manager on
every call and paints nothing when it does not resolve, which is what the
round-trip test asserts: select, `Ctrl+Z`, no ring. A highlight keeps several
rectangles because that is what it is (one annotation, one quad per line), and
each gets its own ring.

**A border, not a fill.** The search hit that is currently selected paints a
translucent block; an annotation already paints itself — a highlight band, a note
icon — and covering it with a second color would hide the very thing the user
jumped to look at. Two points of solid blue around the mapped rectangle leaves it
readable, and the same helper feeds `paintEvent`, so the geometry the tests check
is the geometry drawn.

**Why the row does not go through the window**: `MainWindow` routes
`goto_page_requested` through `self.view`, the *active* tab's view. An annotation
id belongs to the document it came from, and the tab already owns both ends, so
the connection is `tab.sidebar.annotation_activated → tab.view.select_annotation`.
Routing it by window would mean a stale tab's row could ring something in the tab
now on screen.

**Files changed**: `src/pdfar/viewer.py` (the three methods, the ring in
`_paint_overlays()`, the clear on a left press, `ANNOTATION_SELECTED`),
`src/pdfar/sidebar.py` (the signal and the row dispatch),
`src/pdfar/main.py` (the per-tab connection and its disconnect).

**Verified by eye** (headless): with two comments on page 3 and the view on page
1, activating the highlight row moved the view to page 3 and put a 256×20 ring
over the band (275 sampled pixels in the selection color); activating the note row
moved the ring to the 16-point icon (23×23 with the pen); a left-click on the page
left zero ring pixels behind. The screenshot shows the yellow band ringed in blue
with the note icon above it unringed.

**Tests**: 560 passed (was 549) — `tests/test_annotation_navigation.py` (11)
covers the jump bringing the annotation inside the viewport, the outline matching
the annotation's rectangle at five zoom/rotation pairs, a multi-quad highlight
outlining every quad and nothing on a neighbouring page, a note being ringed at
its icon, clearing, selecting another annotation moving the ring, an undo drawing
nothing, an unknown id saying why, activating a sidebar row jumping to its
annotation, a row without an annotation still going to its page, and a click on
the page letting go. Three consecutive full runs.

---

### F10-3g A highlight stays on its words when the page is turned — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the rectangles a highlight is made of now come from the page
rather than from the screen. `create_highlight_from_selection()` had been mapping
every selected word through `_word_rect_view()` — `map_rect()` into view space —
before handing the list to `add_highlights()`, which writes the rectangles
straight into `page.add_highlight_annot()`. Both ends of that speak PDF points, so
the map was a harmless round trip at rotation 0 and a displacement at every other
angle. The words come out of `get_text("words")` in page space already, so the
mapping is simply gone: `word[:4]` goes into the per-page list and the merge runs
there. `_word_rect_view()` stays where it belongs, which is painting.

**Two wrong answers, one line.** The misplaced band is the visible one, but the
merge broke too: `_merge_selection_rects()` sorts by `y` and joins whatever
overlaps vertically, and at 90° a text line runs *down* the screen. So each word
became its own quad — four rectangles on a page with two lines of text — and the
whole thing landed a quarter of the page away from the text it was drawn over.
Measuring the same selection of page 1 (words spanning `(10.0, 47.1, 193.9, 93.0)`)
before the change: rotation 0 gave 2 rectangles with that union, rotation 90 gave 4
with `(7.0, 10.0, 52.9, 193.9)`. Both are fixed by working in the space the
document uses, and the fix is why the note tool (F10-3e) had to convert its click
back to PDF space by hand instead of reusing the selection.

**Files changed**: `src/pdfar/viewer.py` (the collection loop; the merge's
docstring now names the space it works in).

**Verified by eye** (headless): selecting everything and highlighting at 0°, 90°,
180° and 270°, then rendering page 1 through MuPDF and counting highlight-yellow
pixels inside each word's box — 0.79 to 0.81 of the pixels for all four words at
every rotation, 0.00 in the blank strip between the two lines, and two quads on the
page every time. The numbers are identical to two decimal places across the four
rotations, which is the point. The screen grabs show continuous bands riding on the
rotated text, not beside it.

**Tests**: 571 passed (was 560) — `tests/test_highlight_rotation.py` (11) fixes the
unrotated answer first (page 1 is two lines, so two rectangles, at the words' own
coordinates) and then holds 90°, 180° and 270° to it: the stored union equals the
selected words' union on every page, mapping the result back to the screen puts it
under the selection the user saw, a rotation-0 highlight and a rotation-90 one are
the same rectangles, and a three-word selection at 90° highlights those three words
and nothing else. Written before the fix, 8 of the 11 failed and every one of them
passed at rotation 0. Three consecutive full runs.

**Performance**: dropping the per-word `map_rect()` takes a 358-word, 60-page
selection from 3.03 ms to 0.57 ms to collect and merge. The edit itself is
unchanged — F10-3c's 54–70 ms — and what is left of the cost is the annotation list
re-read, the one item still open under F10-3.

---

### F10-3h The annotation list re-reads the pages an edit touched — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the list the sidebar shows is kept per page now, and an edit says
which pages it wrote to. `Edit` gained `pages` — `()` for none, `(3,)` for one,
`None` for "the edit cannot say" — the session records the pages of the step that
produced the current generation as `changed_pages`, and
`AnnotationManager.reload_from_document()` reads those pages and keeps what it had
read of the rest. `AddHighlight` names every page of its selection; the four
single-page annotation edits say so through `SinglePageEdit`.

**The walk was worse than it looked.** F10-3e measured the re-list at 108–175 ms;
measuring it again in the app, in pieces, put it at **167–302 ms** against 59 ms for
the edit. The reason it is that far above the cost of the same walk on a warm
document (30 ms) is F10-3c: an adopted edit is a *different* `fitz.Document`, opened
from a revision file, so its page cache starts empty and all 540 page dictionaries
are parsed again. The list was not slow because it reads annotations; it was slow
because it loads every page to find out that it has none.

**`None` is the answer that keeps this safe.** An edit that does not name its pages,
a revert (a whole document came in from disk), and a list that has fallen behind by
more than one generation all mean "read the document", which is exactly what F10-3a
did. The partial path only runs when the manager is one step behind and that step
said which pages it wrote. Getting this wrong would show a user a comment that is no
longer there, or hide one that is, so the tests count `load_page` calls rather than
trusting that the list looks right — a stale row and a correct one are the same
object otherwise.

**Files changed**: `src/pdfar/edits.py` (`Edit.pages`, `SetMetadata.pages`,
`_Step.pages` carried through `record`/`take_undo`/`take_redo` so an undo knows what
it un-did), `src/pdfar/session.py` (`changed_pages` set by `apply`, `_step` and
`revert`), `src/pdfar/annotations.py` (`SinglePageEdit`, the per-page cache,
`_read_page`).

**Verified by eye** (headless): through six steps in the app — empty, a highlight on
page 2, a note on page 4, the note rewritten, the note removed, the removal undone —
the comments panel listed exactly what the manager listed, including page 4's group
disappearing and coming back. The screenshot shows both rows, two pages apart, each
written by its own edit.

**Tests**: 584 passed (was 571) — `tests/test_annotation_list_cache.py` (13) counts
page reads: a note re-reads its own page and not the neighbours, an untouched page
keeps the model it had (identity, not equality), a multi-page highlight re-reads
each of its pages, a metadata edit re-reads nothing at all, an edit that cannot say
re-reads the document, a list two steps behind re-reads the document, a revert
re-reads it, undo re-reads the page the undone edit had written, and the list stays
in page order across partial reads. Three consecutive full runs.

**Performance on the 540-page book**: a note edit's re-list went from 167–302 ms to
**1.6–2.8 ms** (2 page reads for the note's own page instead of 540), and undo to
1 read in 19.9 ms. The whole edit is now 89–119 ms, of which the document change is
78–113 ms — the list is no longer the expensive part. What a note still costs on top
is the re-render of the page it painted, which is the point of the edit.

---

### F10-3i A freehand drawing is a real `/Ink` annotation — [x] Implemented

**Completed**: 2026-10-27

**What landed**: the third kind the document is the store for, and the first
whose geometry is the user's own rather than a text selection or a click.
`AddInk` writes one `/Ink` annotation whose `/InkList` holds every stroke of the
drawing, in page space, named by `/NM` like the others, colour in `/C` and
thickness in `/BS`. `InkAnnotation` carries `paths` and `width`;
`_read_page()` dispatches on `PDF_ANNOT_INK`. Nothing else changed: delete,
note, jump, the ring, the per-page re-read and the sidebar row all already spoke
annotation rather than highlight, which is what F10-3a and F10-3d left behind.

**One drawing, one edit.** The strokes live in one annotation because the user
drew once: a `/Ink` per stroke would make Ctrl+Z lift the drawing off a line at
a time, the same reason a several-line selection is one highlight with several
quads (F10-3a). MuPDF writes the appearance stream on `update()`, so the stroke
reaches the pixels through `render.py`'s existing `annots=True` and shows up in
other readers without PDFAR drawing it a second time.

**Two PyMuPDF surprises, both recorded in the code.** `add_ink_annot()` checks
each point for a sequence of length two and a `fitz.Point` is not one, so the
library's own object is rejected with "arg must be seq of seq of float pairs";
the points stay plain number pairs. And an empty `/InkList` is not an invisible
annotation but a broken one — MuPDF gives it the whole coordinate range as its
`/Rect` — so the edit refuses before writing it rather than leaving a page that
repaints forever and a panel row nothing can be clicked.

**Files changed**: `src/pdfar/annotations.py` (`INK_WIDTH`, `InkAnnotation`,
`_ink_model`, `_clean_paths`, `AddInk`, the subtype dispatch,
`AnnotationManager.add_ink()`).

**Verified by eye** (headless): a document carrying a red zig-zag, a note and a
highlight opens with three rows under Page 2, the panel saying exactly what the
model holds; the stroke is on the screen (3.9 % of the ringed box is its red);
clicking its row scrolls it into view and rings it; deleting it takes the row,
the ring and the red off the page (0.0 %); undo brings all three back at the
same coverage. Screenshots `/tmp/f103i_page.png`, `/tmp/f103i_sidebar.png`.

**Tests**: 598 passed (was 584) — `tests/test_freehand.py` (14): the stroke is
in the document the moment it is drawn, one annotation holds every stroke of one
drawing, it reaches the pixels, its box covers what was drawn, an empty drawing
is refused without an undo step or a version bump, undo takes the whole drawing
off and redo puts the same id back, it survives a save and a reopen point for
point, it re-reads one page rather than the book, it is listed with the other
kinds and deletable and notable like them, and a stroke another program drew is
listed under `anon:{xref}` without being renamed. Three consecutive full runs.

**Performance on the 540-page book**: the document change is 83.7 ms and the
re-list **1.2 ms with 1 page read** — F10-3h's per-page cache carries to a new
kind for free, because declaring `pages` was the edit's job, not the kind's.
Undo + re-list 30.6 ms, redo 29.0 ms; the whole action is the same 150–500 ms a
note costs, and most of that is repainting the page the user drew on.

**What is not here**: the tool. Nothing in the interface can make a stroke yet,
so this is the same split F10-3d/F10-3e made — the document first, the mouse
next (F10-3j).

---

### F10-3j The pen draws a stroke where the pointer is — [x] Implemented

**Completed**: 2026-10-27

**What landed**: a fifth left-button tool, "Freehand", in the selection split
button below a separator — it is the only one that writes a drawing rather than
selecting text. Press starts a stroke, moves extend it, release turns the whole
drag into one `AddInk`; so one drag is one annotation, one Ctrl+Z and one row
in the panel. The stroke previews under the pointer while the button is held.
Escape, a tool change or a view-mode change throws an unfinished stroke away.
A pencil icon, `draw_freehand.svg`, follows the toolbar's tint.

**Page space, not screen space.** Each point goes through `_pdf_point_at()` at
the moment Qt delivers it, so a rotated view — the F10-3g lesson — draws where
the pointer is, and the pairs handed to `AddInk` are already in the coordinates
the annotation stores. The preview is the only thing in view space: it maps
those same page points on every paint, so it follows zoom and rotation without
capturing anything twice.

**Two small rules that keep the stroke usable.** `PEN_MIN_STEP` of 2 screen
pixels (divided by zoom) keeps a held pen from filling `/InkList` with points a
thousandth of a point apart, while a press-and-release with no move still makes
a dot from the single point. And a point that leaves the page is dropped, not
clamped: clamping would drag the line along the page edge whenever the user's
hand strayed, whereas dropping ends the visible stroke where the paper does.

**Reflow.** The pen is a page tool like Area and Table — reflow has no page
geometry to draw on — so `set_reflow_mode()` and `_update_tool_actions()` both
refuse it, fall back to Text and say so in the status bar.

**Files changed**: `src/pdfar/viewer.py` (`TOOL_MODES`, `_PAGE_ONLY_TOOLS`,
`_stroke`, `ink_color`/`ink_width`, page-space capture, `_extend_stroke`,
`_commit_stroke`, `_cancel_stroke`, `_paint_stroke`, the mouse/key handlers),
`src/pdfar/main.py` (`act_draw`, the split-menu entry, mode mapping, enable and
reflow rules, tinted icon), `assets/icons/draw_freehand.svg`,
`tests/test_pen_tool.py`, `tests/test_main.py` (the split-menu assertion).

**Verified by eye** (headless): the real window opens a three-page sample, the
Freehand action switches the tool, and a four-point zig-zag leaves one drawing
whose points sit under the drag (within a widget pixel); the page darkens while
the button is held (1.0 % → 4.0 %) and stays after release (4.6 %); the sidebar
lists 📝 Freehand; a second stroke drawn under a 90° view still lands on the
page points; undo leaves one drawing; save and reopen returns the first stroke
point for point. Screenshots `/tmp/f103j_draw.png`, `/tmp/f103j_rotated.png`.

**Tests**: 618 passed (was 598) — `tests/test_pen_tool.py` (20): the pen is a
mouse tool, the text tool draws nothing, the edit is committed only on release
(one row, one undo step), two drags make two drawings and two Ctrl+Z, a tap is
a dot, colour and width come from the view, the stroke lands where the pen went
at 0/90/180/270° and at 0.8×/1×/2.5×, off-page points are dropped without
clamping, a press off-page draws nothing, the live preview repaints, Escape and
a tool change cancel, and reflow disables the pen. Three consecutive full runs.

**Performance on the 540-page book**: 39 moves are captured in 0.73 ms —
tracking is a list append and a viewport update. Releasing is the edit, not the
point count: a 40-point stroke commits in 89–253 ms and a one-point tap in
82–289 ms, the same `AddInk` the F10-3i bench measured at 83.7 ms plus this
machine's variance; undo + re-list is 99–225 ms. The annotation list reads only
the edited page — the neighbouring page loads visible in a trace are the tile
renderer repainting what the viewport shows.

**What is not here**: the colour and thickness pickers. `view.ink_color` and
`view.ink_width` exist and `AddInk` writes both, but nothing in the interface
sets them, so the pen draws the document default (black, 2 pt) for now — that
is F10-3k.

---

### F10-3k The pen's colour and thickness are picked, not fixed — [x] Implemented

**Completed**: 2026-10-27

**What landed**: two controls in the toolbar, visible only while Freehand is the
active tool. A pencil swatch tinted in the chosen ink opens the palette
(`INK_COLORS`: black, red, blue, green, orange, purple) and a "Custom…" entry
for anything else; a 1/2/4/8 pt combo picks the thickness (`INK_WIDTHS`). Both
set `view.ink_color` / `view.ink_width`, which `_paint_stroke` already read and
`_commit_stroke` already passed to `AddInk`, so the pickers add no new path
into the document.

**Per tab, and not an edit.** The controls mirror the active view on every
`_update_tool_actions` (tab switch, theme change, reflow), so each document
keeps its own pen. Choosing a style writes two attributes and a repaint
request: no history step, no version bump, no `dirty`. The annotation gets
`/C` and `/BS` at commit time, which is what makes the style survive save and
reopen and show in other readers.

**One UI trap.** Hiding the widget was not enough: `QToolBar` re-shows the
widgets it owns when it lays out, so the options came back with the text tool.
The hide belongs on the `QWidgetAction` that owns the widget
(`pen_action.setVisible(…)`). The controls sit after the page-view button,
which keeps the reflow/explore/tools/view-mode group the earlier toolbar
commit established contiguous.

**Files changed**: `src/pdfar/annotations.py` (`INK_COLORS`, `INK_WIDTHS`),
`src/pdfar/viewer.py` (`set_ink_color`, `set_ink_width`), `src/pdfar/main.py`
(the options widget, palette menu, custom colour, width combo, sync/show
logic), `tests/test_pen_options.py`.

**Verified by eye** (headless): the options are absent with the text tool and
appear with Freehand; Red at 4 pt draws a stroke whose `/C` is `D32F2F` and
whose `/BS` is 4.0, with 7.0 % of its box red on the page; a custom `#00695C`
at 1 pt draws a second stroke matching its tooltip and the file; save and
reopen returns both strokes with their colours and widths. Screenshots
`/tmp/f103k_ui.png`, `/tmp/f103k_red.png`, `/tmp/f103k_custom.png`.

**Tests**: 630 passed (was 618) — `tests/test_pen_options.py` (12): the
options hide with the tool and on close and show only with Freehand; the
palette is the ink palette plus Custom; a preset colour and the width combo set
the view, with exactly one palette entry checked; the dialog supplies a custom
colour and a cancelled dialog changes nothing; a Blue 4 pt stroke lands in
`/C` and `/BS`; the live preview is painted in the chosen colour; a style
choice is not an edit (version, dirty and history unchanged); the highlight
colour is untouched; and two tabs keep their own pens. Three consecutive full
runs.

**Performance on the 540-page book**: 1000 style choices take 28 ms — 0.03 ms
each, two attribute writes and a coalesced repaint — because there is no
document work in a picker. A red 8 pt stroke commits in 103 ms against 133 ms
for the black 2 pt one, the F10-3j `AddInk` cost with run-to-run noise on top;
colour and width do not change what the edit does.

**What is not here**: a pen style that survives a restart. Each document opens
with black at 2 pt; the tab state `QSettings` stores does not include the pen
yet.

---

### F10-3l Shapes are real `/Square`, `/Circle`, `/Line` and `/Polygon` — [x] Implemented

**Completed**: 2026-10-27

**What landed**: `AddShape` writes one of five shapes — rectangle, ellipse,
line, arrow, polygon — as the subtype a viewer knows how to select and resize:
`add_rect_annot` (`/Square`), `add_circle_annot` (`/Circle`),
`add_line_annot` (`/Line`, also used for arrows) and `add_polygon_annot`
(`/Polygon`). Box shapes carry a `rect`; lines, arrows and polygons carry
page-space `points`. `ShapeAnnotation` reads them back with `kind`, `points`
and `width`, and `AnnotationManager.add_shape()` makes each shape one edit —
one Ctrl+Z, one row in the comment list.

**An arrow is a line that says so.** PDF has no arrow subtype. An arrow is a
`/Line` whose `/LE` array ends in `CLOSED_ARROW`, set with
`set_line_ends(PDF_ANNOT_LE_NONE, PDF_ANNOT_LE_CLOSED_ARROW)`. The reader checks
`line_ends` and reports `"arrow"` rather than `"line"`, so the model says what
the user drew while the file stays a shape every other reader understands.

**Geometry rules.** Corners may arrive in any order — the user can drag up and
to the left — and are put in page order before MuPDF sees them. A shape with no
extent is refused: a zero-height `/Square` is a line MuPDF stores as a
degenerate box that nobody can select, and a polygon needs at least three
points. The refusal is the edit's (`_clean_shape`), so every caller is covered,
and it leaves the page, history and dirty flag untouched.

**Files changed**: `src/pdfar/annotations.py` (`SHAPE_LABELS`,
`ShapeAnnotation`, `_shape_model`, `_clean_shape`, `AddShape`, the `_read_page`
dispatch, `AnnotationManager.add_shape`), `tests/test_shapes.py`, and the
`tests/test_text_notes.py` case that used to call every foreign shape
unlistable.

**Verified by eye** (headless): one of each kind written into the document reads
back as `Square`, `Circle`, `Line`, `Line` with ends `(0, 5)`, `Polygon`; 8.6 %
of the rectangle's box is red on the rendered page; after saving and reopening
in a fresh window the page paints 220 red pixels and the models come back as
`rect, ellipse, line, arrow, polygon`. Screenshots `/tmp/f103l_ui.png`,
`/tmp/f103l_reopened.png`.

**Tests**: 674 passed (was 630) — `tests/test_shapes.py` (44): each kind is a
`ShapeAnnotation` in the document with the right subtype, colour and width; an
arrow is a line whose `/LE` carries the head and a plain line is not; every
kind reaches the pixels; the box covers the geometry; reversed corners are
normalised; line and polygon points round-trip in page space; degenerate boxes,
wrong point counts and unknown kinds are refused without history, version or
dirty; `AddShape.apply` raises `EditError` on its own; one undo takes the shape
off and redo puts the same id back; each kind's undo step is named for it; all
five survive save and reopen; a shape re-reads one page; shapes list with the
other kinds, take a note and delete; and a shape another program drew is listed
under `anon:{xref}` without being renamed. Three consecutive full runs.

**Performance on the 540-page book**: adding a shape is 53–65 ms — the same
edit machinery as the F10-3j ink (54 ms for a 40-point stroke), most of it the
revision-file copy F10-3c made cheap. Undo plus re-read is 14.2–14.7 ms, and a
second re-read with nothing pending is 0.00 ms because the edit's own re-read
already refreshed the page.

**What is not here**: the tool. Nothing in the interface draws a shape yet — the
same document-first split as F10-3d/F10-3e and F10-3i/F10-3j. The drag, the
live preview and the kind picker are F10-3m.

---

### F10-3m The shape tool — [x] Implemented

**Completed**: 2026-10-27

**What landed**: a sixth tool mode, `"shape"`, and one toolbar split button for
it. The button draws the last kind used and its menu offers the other four;
`view.shape_kind` is per tab, so two documents can each keep the kind they were
drawn with. `_shape_drag` holds one drag — page, kind, start corner, moving
corner, and for a polygon the trace — and `_commit_shape` turns it into exactly
one `add_shape`, the pen's rule that one press-to-release is one edit and one
Ctrl+Z.

**What a drag means depends on the kind.** A rectangle and an ellipse take the
press and release points as opposite corners, in either direction, because
`_clean_shape` already normalises them. A line and an arrow take them as the
two endpoints. A polygon cannot: it is the outline the user traced, so moves
are captured with F10-3j's `PEN_MIN_STEP / zoom` spacing and the finished trace
is reduced by Ramer–Douglas–Peucker at the same tolerance. A shaky triangle
traced in dozens of moves becomes three corners, not one per move; a trace that
comes back to where it started loses the repeated closing point, since
`/Polygon` closes itself. Fewer than three corners after reduction means no shape: `add_shape`
would refuse it, so the tool does not ask.

**The capture is F10-3j's, with one difference.** `_pdf_point_at` still owns
the walk from widget coordinates through scroll bars, zoom, rotation and page
layout into page space, which is why a shape drawn on a 90°-turned page lands on
the point under the pointer. A point that lands off the page or on a
neighbouring page is ignored rather than clamped, so the shape stops at the
paper edge instead of growing into the gutter; for a box that means it freezes
at the last on-page corner. A press in the gutter starts nothing. Escape and
changing tools cancel a shape in progress, which never reaches the document;
undoing, of course, removes a committed one.

**Preview.** `_paint_shape` draws in view coordinates straight from the live
drag: a normalized `QRectF` for boxes, a `drawLine` plus a hand-drawn arrowhead
for line/arrow, and a closed `QPainterPath` through the traced points for the
polygon. The arrowhead is sized in pixels from the ink width so it stays visible
at any zoom. The shape is painted under the pointer from the press on; nothing
is written until release.

**Style reuse.** The tool reads the same `view.ink_color` and `view.ink_width`
the pen does, so F10-3k's palette, custom colour and width combo appear for the
shape tool too. The swatch's icon becomes the current kind's, so the button
says what is being drawn, and its tooltip stays "Drawing colour".

**One UI trap, again.** F10-3k hid the options by hiding their `QWidgetAction`.
Once the toolbar had a split button between the tool and the page-view button,
that was not enough: the action stayed in the toolbar with `isHidden()` while
`isVisible()` could still read true. The options are now removed from the
toolbar when the tool changes and re-inserted with `addWidget()` when a drawing
tool becomes active.

**Files changed**: `src/pdfar/viewer.py` (`_segment_distance`,
`_simplify_outline`, `shape_kind`, `_shape_drag`, `set_shape_kind`,
`_cancel_shape`, `_extend_shape`, `_commit_shape`, `_paint_shape`,
`_paint_arrowhead`, the mouse and key branches, `TOOL_MODES` and
`_PAGE_ONLY_TOOLS`), `src/pdfar/main.py` (the five kind actions, the
`shape_split` button, `_choose_shape`, per-tab kind sync, shape icons, the pen
options shown for both drawing tools), five `assets/icons/shape_*.svg`,
`tests/test_shape_tool.py`, and the toolbar-order assertion in
`tests/test_main.py`.

**Verified by eye** (headless): a 300×200 page at 150 % took one drag of each
kind and read back `Square`, `Circle`, `Line`, `Line` with ends `(0, 5)`,
`Polygon`, with history labels Rectangle, Ellipse, Line, Arrow, Polygon; the
shape options were visible with the tool; an arrow dragged on the same page at
90° stored the page-space points `(30, 30)–(120, 60)`; after saving and
reopening in a fresh window the six annotations came back as
`rect, ellipse, line, arrow, polygon, arrow`. Screenshots `/tmp/f103m_ui.png`,
`/tmp/f103m_rotated.png`, `/tmp/f103m_reopened.png`.

**Tests**: 709 passed (was 674) — `tests/test_shape_tool.py` (35): each kind is
one tool with a kind and its own menu entry; another tool draws nothing; every
kind's drag produces the shape the document holds; nothing is written until
release; box corners, line endpoints and the arrow's `(0, 5)` are right; a
22-point jittered triangle trace is reduced to its three corners; a line
survives 0°, 90°, 180° and 270° and three zooms; a drag leaving the page stops
at the paper;
a press in the gutter draws nothing; a click draws nothing; the live preview
paints; Escape and a tool change cancel; reflow disables the tool; colour and
width reach `/C` and `/BS`; the pickers appear with the tool; and two tabs keep
their own kind. Three consecutive full runs.

**Performance on the 540-page book** (page 1, real `PDFView` driven with mouse
events): capturing the drag is the pen's cost — 60 moves trace in 3.2 ms — and
the release applies the shape in 56–187 ms, the F10-3l `add_shape` edit plus
the view's update. The number that looks large is the repaint after any edit,
299–533 ms; it is the edited page being re-rasterised, not the tool, because an
undo costs the same 533 ms and a repaint with no edit is 2.8–6.3 ms. Shapes
therefore have no measurable cost of their own beyond the edit every annotation
kind already pays.

**What is not here**: constraining a box to a square/circle with Shift, a fill
choice (shapes are hollow), and editing a committed shape's geometry — the
comment list can select, note and delete one, but changing its corners is not a
tool yet. Stamps (F10-3n) are next.

### F10-3n The standard rubber stamps and the Stamp tool — [x] Implemented

**Completed**: 2026-10-27

**What landed**: PDF has fourteen standard stamp names — `/Approved`,
`/Draft`, `/Confidential`, `/TopSecret`, `/Sold` and nine more — and every
viewer draws the same red rubber-stamp appearance for each from the `/Name`
alone. `AddStamp` writes one as a `/Stamp` annotation, so PDFAR ships no image
of its own and another reader shows the same mark: `page.add_stamp_annot()`
generates the appearance stream in the file. The manager reads `/Stamp` back
into the new `StampAnnotation` (`type`, `name`, plus the generic page, rect,
colour, opacity, note fields), so stamps go through the list, note, delete,
undo and save paths F10-3a–3h built for every kind.

**Where the stamp goes.** The standard appearance is 3.8:1, and
`add_stamp_annot` stretches whatever rect it is given into that shape, so the
box has to be right before the call: `stamp_box` takes the page size and a
centre, uses 190 pt wide unless the page is narrow (60 % of the width, or of
the height if that is the tighter limit), then slides the box back inside the
paper. A click at a corner therefore leaves a whole stamp, not half of one.

**One MuPDF quirk.** `add_stamp_annot` writes `/Contents` equal to the raw
name — "Draft", not "Draft" as a note the user typed. The reader blanks that
caption when it matches the name, so a fresh stamp has no note; a note the user
adds later is a different string and survives. `AddStamp.apply` also renames
the annotation to the `NM` id F10-3d introduced, so undo/redo and the comment
list recognise it like any other kind.

**The tool.** "Stamp" joins the tool modes and the tool button's page-only
group: it needs a page, not a drag. A press places the chosen stamp centred on
the page point under the pointer and is one edit and one Ctrl+Z; a click in the
gutter or on a neighbouring page does nothing. The split button offers all
fourteen names (label order as PDF lists them, menu checked on the current
one) and keeps the choice per tab, like F10-3m's shape kind. The click goes
through F10-3g's page-space mapping, so it lands where the pointer is at 90°,
180° and 270°; Reflow disables the tool with the others and falls back to
Text.

**Files changed**: `src/pdfar/annotations.py` (`STAMP_NAMES`,
`STAMP_LABELS`, `STAMP_VALUES`, `STAMP_WIDTH`, `STAMP_ASPECT`,
`StampAnnotation`, `stamp_box`, `_stamp_model`, `AddStamp`,
`AnnotationManager.add_stamp`), `src/pdfar/viewer.py` (`stamp_name`,
`set_stamp_name`, the `"stamp"` tool branch, tool lists),
`src/pdfar/main.py` (fourteen stamp actions, the `stamp_split` button,
`_choose_stamp`, per-tab name sync, reflow fallback, `stamp.svg`), a new
`assets/icons/stamp.svg`, `tests/test_stamps.py`, and the annotation-kind
expectations in `tests/test_text_notes.py` / the toolbar order in
`tests/test_main.py`.

**Verified by eye** (headless): a 300×200 page at 150 % took four clicks —
Approved, Draft, Confidential, Top Secret — each read back with the matching
`/Name` and a box inside the page; the corner click (2, 198) placed TopSecret
at `(0, 152.6, 180, 200)` rather than hanging off the paper; a Final stamp on
page 2 stayed on page 2. The page rendered 25,956 red stamp pixels; a Sold
stamp clicked at 90° landed centred on `(150, 100)`; after saving and
reopening, the five stamps came back as Approved, Draft, Confidential, Top
Secret and Sold with their ids. Screenshots `/tmp/f103n_ui.png`,
`/tmp/f103n_rotated.png`, `/tmp/f103n_reopened.png`.

**Tests**: 747 passed (was 709) — `tests/test_stamps.py` (38): a stamp is a
real `/Stamp` with its `/Name`, an `NM` id and the label "Stamp"; all fourteen
names keep their key; the box is `stamp_box`'s 3.8:1 box; three page edges
clamp it inside; the appearance paints; an unknown name is refused with the
version, dirty flag and history unchanged; `AddStamp.apply` raises `EditError`;
undo/redo restores the same id; save + reopen; the list holds stamps and shapes
and skips a `/FreeText` note; a stamp takes a note and deletes; a stamp whose
`NM` PDFAR did not write is listed as `anon:{xref}` and not renamed. On the
tool side: the click places exactly one stamp of the chosen name, other tools
place none, an off-page click places none, the centre is right at 0°/90°/180°/
270°, Reflow disables it, and two tabs keep Approved and TopSecret separately
with the default action following the tab. Three consecutive full runs.

**Performance on the 540-page book** (page 1): adding one stamp costs the same
as a shape, 96–99 ms — the copy-and-adopt edit dominates, not the appearance —
and so does re-applying it on the undo/redo path. The view side is one click:
591 ms to apply the stamp including the page re-read, and 965 ms for the
first repaint of the re-rasterised page (later repaints 6–21 ms with no edit).
A save of the book with the stamps is 3.1 s for 23.6 MB, the same rewrite
every save pays.

**What is not here**: custom stamps — a text caption or an image file as the
stamp's appearance — which is F10-3o next; dragging to size a stamp instead of
clicking its standard size; and replacing the appearance of a stamp placed by
another program.

---

# 17. RELEASE CRITERIA

A version must not be declared stable solely because it compiles.

It must meet the following requirements:

- passing tests
- three consecutive runs
- no known crashes
- no regressions >15%
- updated documentation
- screenshots of key GUI features
- clean shutdown
- workers properly closed
- test PDFs processed correctly
- updated changelog

---

# 18. FUNDAMENTAL PROJECT RULE

Speed ​​must not be achieved by sacrificing stability.

Feature count must not be achieved by sacrificing architecture.

Compatibility must not be achieved by faking capabilities that PDFAR does not actually possess.

The interface must not hide critical errors.

PDFAR must prioritize:

"unsupported, but correctly explained"

over:

"seems to work, but might destroy or alter the document."

---

# 19. ULTIMATE GOAL

The goal is not simply to create:

"another PDF viewer."

The goal is to create:

PDFAR — Advanced PDF Reader for Linux

A reader that is:

- fast
- stable
- professional
- modular
- accessible
- secure
- private
- extensible
- cross-platform within the Linux ecosystem
- comfortable for reading
- powerful for study
- useful for professional work
- compliant with PDF standards
- maintainable for years

Architectural quality is just as important as the number of features.

---

# 20. START NOW

DO NOT implement the entire roadmap.

First:

1. inspect the repository;
2. run the current tests;
3. confirm the baseline;
4. review the actual state of PDFAR 2.0;
5. do not duplicate existing functionality;
6. start with the first open task of the current sprint (section 8);
7. implement;
8. test;
9. verify visually;
10. measure performance;
11. document;
12. make a clean commit.

Do not move on to the next task until the previous one meets the Definition of
Done. The current priorities are listed in section 8 and tracked in F10.

Target version:

PDFAR 2.1

Following version 2.1, work will continue on the roadmap incrementally.

