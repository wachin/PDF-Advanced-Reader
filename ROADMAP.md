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
shape tool (F10-3l/3m), the standard rubber stamps with their tool
(F10-3n), custom caption/image stamps (F10-3o), measurement annotations
with a chosen scale (F10-3p), perimeter/area measurements (F10-3q),
calibration from a line of known length (F10-3r), self-contained
appearance streams on every mark a save writes (F10-3s), XFDF export of
the document's comments (F10-3t), the reader that turns another program's
XFDF into a plan of edits (F10-3u), that plan as one undoable import
(F10-3v) and the two File-menu commands that reach either half (F10-3w)
done — and the identity those commands' marks rest on now survives a document
whose marks were all given the same name (F10-3x), while a square or a circle
keeps the box it was drawn into instead of growing a point on every side each
time the file is read (F10-3y).

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
- [x] Custom stamps: a text caption or an image file as the stamp's appearance
      (**F10-3o.** A stamp PDFAR draws itself rather than one MuPDF draws from
      a standard name. `AddCustomStamp` writes the appearance as a form XObject
      in the annotation's `/AP/N`: a caption becomes a red Helvetica-Bold line
      in a box that shrinks to fit the page, and an image is embedded as an
      image XObject with its alpha kept as an `/SMask`, so a logo shows the page
      through where the file is clear. The mark is `/Name /Custom`, which no
      viewer regenerates, and its `/Contents` is the caption (or the user's own
      note) so the comments panel says what the mark says. "Custom Text…" and
      "Custom Image…" sit under the fourteen in the stamp menu, ask for the
      caption or file, arm the click tool, and follow the same per-tab rule and
      reflow guard as the standard stamps. A file that is not an image, an
      empty definition, a caption the font cannot draw and one too long for the
      page are refused before the document changes.)
- [x] Measurement annotations: a distance with the scale it is drawn in
      (**F10-3p.** A measurement is a real `/Line` annotation carrying
      `/IT /LineDimension` and a rectilinear `/Measure` dictionary
      (`/Subtype /RL`, `/R (1 pt = 0.352778 mm)`, one `/NumberFormat` for the
      distance), so the file itself says what the number means — not the 3D
      `/Projection` dictionary an earlier version of this item named. MuPDF
      draws such a line bare, so PDFAR writes the end ticks and the reading
      into `/AP/N` itself, like a custom stamp's caption. The Measure split
      button offers millimetres, centimetres, inches, points and a custom
      "factor unit" scale; a drag places one as one edit, with a live reading
      that comes from the page geometry and follows the pointer under any
      zoom or view rotation. The scale is viewer state, so two tabs can
      measure in two units, and reflow disables the tool.)
- [x] Perimeter and area measurements (`/IT /PolygonDimension`, a polygon's
      `/Measure` and the area number format it needs)
      (**F10-3q.** A perimeter is a real `/PolyLine` annotation with
      `/IT /PolyLineDimension`, and an area a real `/Polygon` with
      `/IT /PolygonDimension`; both carry the same rectilinear `/Measure`
      dictionary as a distance, plus an `/A` number format for the square
      unit, so another editor computes the same perimeter and area from the
      file. The perimeter is the open path between the corners; the area is
      the shoelace, with a repeated closing corner dropped and a
      self-intersecting outline refused. Both are drawn from clicks — each
      corner, an area closing on its first corner or a double-click/Enter —
      with a live rubber band and one edit per finished outline, and PDFAR
      writes the path, the corner marks and the reading into `/AP/N` itself.)
- [x] Scale calibration: derive the scale from a line of known length instead
      of typing the factor
      (**F10-3r.** "Calibrate…" in the Measure menu arms the tool so the next
      drag is a reference line, not an annotation.  When the drag ends, the
      window asks how long that line really is and one page point becomes
      `known length / drawn length` units.  The drawn length is captured in
      page space, so a turned view calibrates against the same paper; the
      resulting factor is held to what a PDF number format can write, and the
      dialog starts with the line's current reading, so it can be corrected
      rather than retyped.  Nothing is written to the document: the scale is
      viewer state like the units, and Ctrl+Z has nothing to undo.)
- [x] Appearance streams, so other readers show the same thing without
      regenerating them
      (**F10-3s.** Every mark PDFAR made already carried its own `/AP/N` form
      XObject: MuPDF writes it on `update()` for highlights, notes, ink,
      shapes and standard stamps, and the custom-stamp and measurement edits
      write their own. What a file could not promise was that marks it
      *inherited* had one — a program that leaves appearance generation to the
      viewer writes only the annotation dictionary, and viewers disagree or
      skip it. `ensure_appearances()` now runs inside every save before the
      bytes are produced: each annotation without `/AP/N` gets the drawing
      MuPDF makes for it, an appearance the file already has is never
      rewritten, and links, popups and form widgets are left alone. The scan
      reads `/Annots` at the xref level, so the 540-page book with 1 046
      links pays **50–80 ms** on a multi-second save and a document with no
      annotations pays nothing.)
- [x] XFDF export: the document's comments as a file another reader can open
      (**F10-3t.** `xfdf.py` writes Adobe's XFDF from the annotation list: one
      element per mark, in the mark's own kind — `highlight`, `text`, `ink`,
      `square`, `circle`, `line`, `polygon`, `polyline`, `stamp` — with the
      geometry the PDF holds rather than the geometry the view shows. Every
      coordinate is flipped through the page's *crop box*, so a scan whose
      visible area starts at (36, 36), a media box that does not start at zero
      and a page the file turns with `/Rotate` all put the mark on the same
      words. A highlight's four corners come out in the order the specification
      gives a quadrilateral, an ink keeps one `<gesture>` per stroke, a
      measurement its `<vertices>` and its ends, and an arrow its `head`/`tail`.
      The scale of a measurement is the one thing XFDF has no word for, so it
      travels in `urn:pdfar:xfdf:1` attributes at the precision the file's own
      `/Measure` keeps. Nothing is claimed that the file never said: a mark with
      no `/CA` gets no `opacity`, a note or standard stamp no `color`, a mark
      with no `/NM` of its own no `name`, and a mark PDFAR cannot model — free
      text — is left out rather than described wrongly. No `href`, no
      `<fields/>`, no timestamp: the same document writes the same file.)
- [x] XFDF import: another reader's comments as marks in the document, as one
      undoable edit that refuses the file or takes all of it
      (**F10-3u** read the file: `parse_xfdf(text, page_anchors(doc), taken)`
      returns an `ImportPlan` of ready-to-apply `Add*` edits and the marks it left
      out, each with the reason the user will be shown. **F10-3v** applied it:
      `AddImportedMarks` carries the whole plan as one `Scope.COMMENTS` edit on the
      pages it wrote, and `AnnotationManager.import_xfdf(text)` reads and applies
      it against the live document — one Ctrl+Z for forty marks, and an import that
      cannot be carried out leaves no part of the file behind. What was still open
      when this landed is the item below — no menu command reached either half, so
      the capability existed and the user could not ask for it.)
- [x] The File-menu commands that reach the two halves
      (**F10-3w.** File ▸ *Export Comments (XFDF)…* and File ▸ *Import
      Comments (XFDF)*, offered whenever a document is open and never gated on
      the dirty mark: exporting describes the marks that are already in the
      file, and importing is how a document gets marks in the first place.  Both
      choosers open on `name.xfdf` beside the document, because a marks file that
      does not say which document it belongs to is a pile of numbers; an export
      whose name has no extension gets one, and a document with nothing on it
      still writes a valid empty file — the other reader is told there was
      nothing to send.  The import reads the file through `load_xfdf`, which puts
      the size bound on the *file* so oversized ones, missing ones and ones not
      text are refused as sentences before a byte is parsed, and holds the
      wait cursor across the apply because a thousand marks are four seconds of
      MuPDF work.  What comes back is said twice: one line in the status bar
      (`Imported 3 marks, 2 not taken from review.xfdf`) and, whenever anything was
      left out, a dialog naming every mark that did not arrive with the reason the
      reader gave — capped at twelve lines, so a file of a thousand refusals is
      still readable.  A plan the document refuses is the manager's failure to
      report, and the window stays out of it rather than saying the same thing
      twice.)
- [x] Marks that arrive without a name of their own are listed as **one** mark
      (**F10-3x.** found by F10-3t: PyMuPDF writes `/NM (fitz-A0)` for every
      annotation it adds without a name, and the counter restarts per page, so a
      document made by another PyMuPDF-based program carries the same name on
      every page and the id-keyed list kept one of 540.  A name is an identity
      only while it names one mark, so the first in reading order keeps it and
      every copy after it is listed by object number, which nothing else answers
      to; the page caches are keyed by that object number too, so two marks
      sharing a name on one page are two rows.  Both identities resolve back to
      the mark on the page the row names, so nothing that was listed can fail to
      be edited, and the export of such a document names its marks apart instead
      of writing one name 540 times.)
- [x] A square or a circle grows **1 pt on every side** each time it is read back
      (found by F10-3u: MuPDF writes a shape's `/Rect` a point larger than the box
      it was given, whatever `/BS /W` says, so the box an export states is a point
      wider than the box the mark was drawn into and an import takes the file's
      word for it. Two cycles measured: every other kind returns to the numbers it
      left with. The fix belongs to the shape model or its appearance, not to
      XFDF — subtracting a MuPDF constant would misread every other reader's file.
      **F10-3y.** it is taken back where the box is written: `AddShape` asks MuPDF
      for the shape, moves the array the file holds in by exactly the distance MuPDF
      moved the edges out, and only then lets `update()` paint it, so the appearance
      is drawn for the box that ends up in the file rather than scaled into it. A
      mark another program drew keeps the box that program wrote, because reading is
      not editing.)

## F10-4 Page manipulation (was F6) — [ ]

- [x] One coordinate rule for a page that arrives turned: the numbers the file
      states are counted on the sheet **before** the page's own `/Rotate`, so a
      mark, a word box, a search hit, a click and a preview all map with the sheet
      size and the *sum* of the two turns (**F10-4a**; measured against MuPDF's own
      ink for all sixteen pairs of page turn and view turn, where the rule the view
      had been using was wrong for twelve of them). This is the precondition of
      rotating permanently — without it, turning a page with PDFAR's own tool would
      jump every mark the user has on it a quarter of the page.
- [x] Rotate permanently: `RotatePages { pages, delta }` in a new `src/pdfar/pages.py`
      writes `/Rotate` into **each named page node** as an undoable `Scope.STRUCTURE`
      edit — never on a `/Pages` parent, and composed from `page.rotation` so a page
      whose turn it only *inherits* is carried into its own value instead of counted
      twice. It reaches the view, the thumbnails and the saved file, and refuses a
      non-quarter angle, an empty list and an out-of-range page whole (**F10-4b**)
- [x] Put the turn under the user's hands: Tools ▸ **Rotate Page Left / Right**
      (`Ctrl+Shift+Left` / `Ctrl+Shift+Right`) apply `RotatePages` to the page on screen
      through `view.apply_edit`, so the undo step, the re-render, the thumbnails and the
      dirty marker all come from F10-1 rather than new plumbing; the two commands are
      enabled by a document and survive reflow, and the pre-existing pair is now
      **Rotate View Left / Right** with tooltips that say the file keeps its own turn
      (**F10-4c**)
- [x] Reach the turn from the pages themselves: the thumbnail strip takes a **pick**
      (`ExtendedSelection`, where a plain click still selects one page) and its right-click
      menu turns every page in it — `Rotate Page Left / Right` for one, `Rotate N Pages`
      for a pick, and **Rotate All Pages** for the scanned book that arrived sideways end to
      end. The strip names the pages and asks; the window answers through
      `view.apply_edit(RotatePages(...))`, connected per tab and dropped with it, so one
      pick stays one undo step and a hidden document's strip cannot turn the file on screen
      (**F10-4d**)
- [x] Take pages out: `DeletePages { pages }` removes the pick as **contiguous runs**,
      highest run first, because MuPDF's cost is one page-tree rebuild per call rather
      than per page — a hundred-page block of the 540-page book goes in 62 ms where the
      same hundred removed one at a time took 2055 ms. It refuses an empty list, a
      **negative** index (`delete_page(-1)` silently deletes the *last* page) and a request
      that would leave no pages, which MuPDF does not notice until the save. The outline
      rows MuPDF leaves pointing at `-1` are pruned, and a tree the prune would break — a
      first row deeper than level 1, a step of two levels — is lifted rather than refused.
      `Edit.changed` now says what a cache keyed by page number must re-read, which for a
      delete is not the list it named: the marks above the cut move, and follow their pages
      (**F10-4e**)
- [x] Reach the delete from the strip: **Delete Page** on the pick, last in the menu behind
      a separator of its own, and **Tools ▸ Delete Page** (Ctrl+Shift+Backspace) for the page
      under the reader's eyes. It is the first page command that **asks**: the box names the
      sheets as the strip numbers them ("Delete pages 2–4?"), says what moves above the gap and
      says Undo brings them back, and a *No* leaves no edit, no undo step and no dirty file.
      A page-tree change rebuilds the strip, which is the thing the pick was made of, so the
      pick is re-aimed at the sheets that took those numbers and drops the row that went; the
      reader lands on the sheet that took a deleted page's place, and only scrolls for it where
      the shift and the clamp disagree — the scroll is a relayout of the whole book.
      `invalidate` now announces a page count that changed, which is what carries the toolbar's
      total and the page box through a delete *and* back from it: they used to re-agree only on
      a tab switch (**F10-4f**)
- [ ] Shift what the *user's own* state keys on a page number, which no document-level undo
      reaches.  Measured, that is four stores and one panel, each broken differently, so each is
      its own step:
      - [x] **F10-4g** — *Bookmarks follow their sheet.*  A user bookmark is a 0-based page in
            `QSettings` under `bookmarks/<absolute path>` **and** a row in the sidebar's tree,
            and a page-tree edit moves neither: a delete re-points the reader's own marks at
            other sheets, permanently, and no undo reaches it.  The mechanism is a step saying
            what it did to the numbering — `Edit.page_order(before_count)`, *new index → old
            index*, carried on the step and published by the session for the transition that
            just happened (forward when a step is applied or redone, back when it is undone) —
            and the first consumer renumbers the store, saves it and rebuilds the tree.  A
            bookmark on a page that went lands on the sheet that took its place, the rule the
            reader's own page already follows (F10-4f).  Undo moves every bookmark back along
            the bijection rather than restoring a snapshot, so a bookmark the reader makes
            between a delete and its undo keeps its page instead of being overwritten.
      - [x] **F10-4h** — *The reading position, and the reader's page across undo.*
            `viewState/<path>` holds `PDFView.get_state()`'s 0-based page, written on tab change
            and on close and never by an edit; and after an *undo* the reader keeps the number
            they had, which now names another sheet — the gap F10-4f left for the pick too.  One
            map answers all three: the view re-aims `current_page`, the strip's pick and the
            saved position with the sheets.
      - [ ] **F10-4i** — *The numbering range stops echoing itself.* `startPage`/`endPage` are
            1-based and stored **globally** (`pageNumbers/…`, no document in the key),
            `page_range()` clamps them to the open document at use, and the stamp/erase copy
            path saves the clamped pair straight back — so deleting the tail of a book
            permanently narrows a range the user set.  Keep the stored range authoritative and
            clamp only where it is used.
      - [ ] **F10-4j** — *An XFDF sidecar exported before the delete.* The sidecar is named from
            the PDF's path, so a later export overwrites the file that holds the pre-delete
            numbering; an import of such a file into a shortened document skips the entries whose
            pages are gone and lands the rest on sheets that moved.  Record the page count an
            export was written against and say at import what disagrees.
      - [ ] **F10-4k** — *The strip says "here" and "picked" with one channel.*
            `QListWidget.setCurrentItem(item)` selects as well as making the row current, and the
            delegate paints only `State_Selected`, so the reader's own row is drawn as a one-page
            pick: `selected_pages()` cannot tell a gathered range from the place the view happens
            to be, which is why a pick that shrinks to nothing reads back as page 1 picked
            (F10-4h's test says so out loud, and its `Current | NoUpdate` marking is the stopgap).
            Give "you are here" its own paint role so a marking can never edit a pick and
            `_picked_pages` can tell the two answers apart.
      - [ ] **F10-4l** — *The saved position is written against a tree the file may never have.*
            `viewState/<path>` is written on a tab change and on close from the **live** document,
            so a delete that is then discarded leaves the store holding a page number for a page
            tree the file on disk does not have, and reopening lands the reader a sheet over.
            F10-4h fixed the number for the tree the reader is looking at; this is the case where
            the two documents are different.  Write the position against the saved revision, or
            re-aim it through the steps that were dropped.
- [ ] The search-results dock keeps each hit's 0-based page in `UserRole` and is cleared only by
      a new search or a tab change, while the view's own hit list *is* cleared by
      `Scope.STRUCTURE` — so after a delete the dock still offers pages that have moved, out of
      step with the view it came from
- [ ] Extract, duplicate, move, reverse — the same runs against the same pick, and for a
      move the inherited-attribute push-down below
- [ ] The **assemble** permission asked before a page-tree edit, as PdfCraft gates
      `DeletePages` / `MovePages` / `RotatePages` on the document's `/P` flags: PDFAR keeps
      a file's permissions through a save and never consults them at edit time, so a
      document that forbids assembling accepts a delete like any other
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
stamps are real `/Stamp` annotations with a click-to-place tool (F10-3n), and a
custom stamp can be a typed caption or an image file whose appearance PDFAR
writes itself (F10-3o); a measurement is a real `/LineDimension` with its own
`/Measure` scale, a live reading and a split button for the units (F10-3p); a
perimeter and an area are real `/PolyLineDimension` and `/PolygonDimension`
annotations with the square-unit format their reading needs (F10-3q), and a
reference line of known length sets the scale instead of a typed factor
(F10-3r); a save now gives every annotation in the file, including marks
another program left without one, the appearance stream that lets any reader
paint it (F10-3s); the document's comments write out as XFDF, in the
file's own coordinates and with the scale a measurement was taken at
(F10-3t); another program's XFDF reads back into the document as one
undoable import that refuses the file or takes all of it (F10-3u,
F10-3v); and both halves are now commands a user can reach, under File
(Export / Import Comments (XFDF)) — the exchange is finished at the
interface, not only in the library (F10-3w).
Next, in order: the two defects that exchange found are
now closed — marks that share a name are listed as the separate marks they
are (F10-3x), and a square or a circle keeps the box it was drawn into
instead of growing a point on every side each time the file is read
(F10-3y). F10-4's page manipulation has
begun at the end that has to be true before a page can be turned for keeps: a page
that carries its own `/Rotate` — the ordinary case in a scanned book, which is what
PDFAR is being built for — now puts its marks, its words, its hits and its clicks
on the pixels MuPDF actually paints them on (F10-4a). The turn itself has followed:
`RotatePages` writes the angle into the page's own node, undoably, and the view, the
thumbnails and the saved file all agree on the page that comes out (F10-4b) — and it is
now a command, Tools ▸ Rotate Page Left / Right, on the page the user is looking at
(F10-4c), and a pick in the thumbnail strip turns every page in it, all the pages included
(F10-4d). Taking pages out follows the same shape and is where the page tree first stops
being a cosmetic affair: what a delete changes is the *number* of every page above it, so
this is also where an edit learned to say what it invalidated separately from what it named
(F10-4e). Reaching it is the strip's **Delete Page** and the window's question — the first page
command that asks before it edits, because a delete is the first one whose undo the reader has
to be told about rather than be able to work out — and the pick and the reader's own place are
moved with the sheets rather than left pointing at the numbers they used to hold (F10-4f). The
first state the reader owns *outside* the document follows its sheets too: a page-tree step says
what it did to the numbering, carries that statement between the undo and redo stacks, and the
session publishes it for the generation that exists now — so a bookmark moves with its page, is
written back to the settings store, and moves home again on undo, without a snapshot that would
overwrite a mark made since the step (F10-4g). The same map now answers for the reader: the view
re-aims the page they were on when a step moves the sheets — including the undo, which is not a
command and was the gap — the strip's pick follows the sheets it named and is allowed to shrink, and
the position written to `viewState/<path>` is right because the view is (F10-4h). Next in order: the
numbering range that saves its own clamped values back (F10-4i), the XFDF sidecar exported before a
delete (F10-4j), and the two the reading-position step found behind it — the strip's one channel for
"here" and "picked" (F10-4k) and the saved position written against a tree the file may not have
(F10-4l).

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

### F10-3o Custom stamps — a caption or an image PDFAR draws itself — [x] Implemented

**Completed**: 2026-10-27

**What landed**: `AddCustomStamp`, a stamp whose appearance PDFAR writes
instead of letting MuPDF derive it from a standard name. A **caption** becomes
one red Helvetica-Bold line inside a red box; an **image file** is embedded
whole, transparency included. Either way the appearance is a form XObject in
the annotation's `/AP/N` and the annotation is named `/Name /Custom`, so every
reader shows what PDFAR put there and no viewer regenerates a standard stamp
over it. `/Contents` is the caption (or a note the caller supplied), which is
what makes the comments panel say what the mark says. The box keeps the
stamp's own aspect — a text layout or the image's pixels — is placed around
the click and slid inside at the page edges.

**Text layout.** The caption collapses whitespace and must be Latin-1, the
only range the built-in Helvetica-Bold can draw; the font starts at 18 pt and
shrinks until the line fits 80 % of the page width, never below 8 pt. A
caption that still would not fit at 8 pt, or one with characters outside
Latin-1, is refused before the document is touched rather than written
unreadably small. Padding is 10 pt on every side, and the text is escaped for
a PDF literal string.

**Image layout.** The file is decoded with `fitz.Pixmap`; a source with alpha
keeps it as a grey `/SMask` (the last channel becomes the mask, the colour
channels become a DeviceRGB or DeviceGray image), and CMYK is converted to
RGB. The image XObject is drawn into the form at its pixel size, so the form
scales to whatever box the stamp takes; the box is the standard stamp's
190 pt wide (or 60 % of a small page) at the image's aspect. An unreadable or
missing file is an `EditError`, not a broken annotation.

**Two MuPDF traps, both about order.** `get_new_xref()` creates an *empty*
object and `update_stream()` only accepts one that is already a dictionary, so
an appearance is written dictionary-first, stream-second (and passing
pre-compressed bytes as well gets them compressed twice and the image does not
show). And `add_stamp_annot()` gives every stamp the 3.8:1 Draft appearance,
which `update()` regenerates — refitting `/Rect` to that shape. The custom box
is therefore set *after* `update()`, the caption `update()` writes into
`/Contents` is cleared when the stamp has no note (`/Contents null`, the
F10-3a finding), and only then are `/AP/N`, `/Name` and the id installed.

**The interface.** The stamp menu now ends with "Custom Text…" and "Custom
Image…" below a separator: the first asks for the caption, the second opens an
image file dialog, and each arms the click-to-place tool with that definition.
The chosen definition is viewer state, like the pen's colour and the shape
kind, so each tab places its own; switching tabs restores that tab's standard
or custom stamp and the split button's default action and tooltip follow.
Cancelling a dialog (or picking an unreadable image) restores the stamp that
was armed before. Reflow disables the two entries with the rest of the stamp
tool.

**Files changed**: `src/pdfar/annotations.py` (`CUSTOM_STAMP_NAME`, the
caption font constants, `stamp_box`'s `aspect`, `_caption_layout`,
`_pdf_caption`, `_new_stream_object`, `_caption_appearance`,
`_image_appearance`, `_place_custom_stamp`, `AddCustomStamp`,
`AnnotationManager.add_custom_stamp`, `StampAnnotation`'s docstring),
`src/pdfar/viewer.py` (`custom_stamp`, `set_custom_stamp`, the custom branch
of the stamp click), `src/pdfar/main.py` (the two custom actions, the menu
separator, `_set_custom_stamp`, `_choose_custom_stamp`, the cancel path,
per-tab sync, enable/tint), `tests/test_custom_stamps.py`, and the menu
assertion in `tests/test_stamps.py`.

**Verified by eye** (headless): a 300×200 page at 150 % took two captions and
a 32×16 RGBA logo — `PAID 2026-10-27`, `CONFIDENTIAL - INTERNAL REVIEW`
(shrunk to fit) and the logo — and read back all three as `/Name Custom`, with
the captions in `/Contents` and the image stamp blank. The page rendered
37,488 red pixels, and a pixel in the logo's transparent half equalled the
bare page's pixel, so the `/SMask` works. A `REVIEWED` caption placed after
turning the view 90° stored its box centred on the click. After saving and
reopening, the four stamps came back with their captions and their `/AP`
streams, and re-rendered at 41,357 red pixels. Screenshots `/tmp/f103o_ui.png`,
`/tmp/f103o_rotated.png`.

**Tests**: 778 passed (was 747) — `tests/test_custom_stamps.py` (31): a text
stamp is a `/Stamp` named `Custom` with the caption in `/Contents`, an `/AP/N`
stream, the `NM` id and the history label "Custom stamp"; the caption paints;
the box centres on the click and clamps at three page edges; a long caption
shrinks inside 80 % of the page; an image stamp keeps its 2:1 aspect, shows
the image, keeps transparency (a clear pixel equals the page) and clamps; an
empty definition, an unreadable file, a missing file, a non-Latin-1 caption
and a caption too long for the page are each refused with the version, dirty
flag and history unchanged; `apply()` raises `EditError` and carries
`Scope.COMMENTS`; undo removes and redo restores the same id; save + reopen
keeps the caption, the box and a painted appearance; a custom stamp lists in
the panel with its caption and takes a note without losing its appearance. On
the tool side: arming a caption or image switches to the stamp tool and sets
the default action and tooltip; a click places it; choosing a standard stamp
clears it; it lands on the click under a 90° view; reflow disables it; the
dialogs arm/cancel correctly (including an unreadable image restoring the
previous stamp); and two tabs keep a caption stamp and an image stamp
separately. Three consecutive full runs.

**Performance on the 540-page book** (page 1): the custom appearance is
invisible beside the edit — 61 ms for a text stamp, 57 ms for an image stamp
and 55 ms to re-apply, against 58 ms for a standard stamp. Through the real
`PDFView` a click applies in 432 ms (text) and 312 ms (image), including the
page re-read, with 69 ms and 201 ms to repaint; a no-edit repaint is 4–6 ms.
Saving the book with the stamps is 1.7 s for 23.6 MB.

**What is not here**: a library of custom stamps kept across documents or
restarts — a definition is given each time the tool is armed; a colour or font
choice for a caption (the caption is red Helvetica-Bold like the standard
stamps); multi-line captions; and editing the appearance of a stamp already
placed (its note can be changed, but the mark is what was drawn). Distance
measurement is F10-3p, perimeter and area F10-3q, and calibration from a
known length F10-3r.

---

### F10-3p Measuring distances with a chosen scale — [x] Implemented

**Completed**: 2026-10-27

**What landed**: `MeasurementAnnotation`, `MeasurementScale`, `AddMeasurement`
and `AnnotationManager.add_measurement()`, plus a Measure tool whose split
button chooses the units and whose drag places one dimension. A measurement is
a real PDF dimension: a `/Line` annotation with `/IT /LineDimension` and a
rectilinear `/Measure` dictionary (`/Subtype /RL`, `/R (1 pt = …)`, one
`/NumberFormat` in `/X` for the distance), so the file itself carries the
scale and a reader that understands dimensions shows the same number PDFAR
does. The old roadmap item said `/Projection`, which is a 3D projection
dictionary and has nothing to do with 2D measurement; `/Measure` is the
mechanism PdfCraft and other editors use.

**The dictionary.** The factor is written with six decimals, the unit as a
PDF literal string, and `precision` as the number format's denominator
(`/D 10` = one decimal, up to `/D 10000` = four). The reader accepts the
dictionary either direct or as an indirect object, understands only `/RL`,
and maps the denominator back to 0–4 decimals; anything else — a compound or
geospatial measure, a missing `/X` array, an unusable unit — leaves the
annotation to be read as the plain line it also is. `checked_measure_scale()`
refuses a non-finite or out-of-range factor, more than four decimals, and a
unit that is empty, over 16 characters, has control characters or is outside
Latin-1, before the document is touched.

**The appearance.** MuPDF paints a dimension line as a bare line, so the
ticks and the number are PDFAR's own form XObject in `/AP/N`: a line, two
perpendicular end ticks and a Helvetica reading above it (below it near the
page top), in `MEASURE_COLOR` and the pen's width. The box pads the geometry
by the tick and the label, is clamped to the page, and refuses a reading
wider than the page. Two MuPDF traps: a `/Line` annotation has no Rect
property, so `set_rect()` fails with "Line annotations have no Rect
property" and the box is written straight into `/Rect` in PDF space, whose y
axis runs up from the mediabox bottom; and `update()` must run first because
it refits `/Rect` to the bare line and draws a plain appearance, after which
`/Rect`, `/IT`, `/Measure`, `/Subj`, `/Contents` and `/AP` go on in that
order. `xref_get_key()` follows nested paths (`Measure/Subtype`) but does not
index arrays, so the `/X` format is parsed with regexes.

**The tool.** Measure is a page-only left-button tool: press captures one end
in page space with `_pdf_point_at()`, moves extend the other end, and release
commits the drag as one `AddMeasurement` — one annotation, one Ctrl+Z. The
preview paints the line, the ticks and the live reading in view space from
the captured page points, so zoom and view rotation carry it; a press-release
with no extent is dropped, a point off the page stops the end at the paper,
and Escape, a tool change or a view-mode change cancels. The split button
offers Millimetres, Centimetres, Inches, Points and "Custom Scale…" (a factor
and a unit, e.g. `0.5 in`); the scale is viewer state like the pen's colour,
so each tab keeps its own, and the toolbar's colour/width pickers style the
measurement as they style ink and shapes. Reflow disables the tool with the
other page-only tools.

**Files changed**: `src/pdfar/annotations.py` (`MeasurementScale`,
`MEASURE_SCALES`, `checked_measure_scale`, `MeasurementAnnotation`,
`MEASURE_*` constants, `_pdf_number`, `_measure_dictionary`,
`_measurement_appearance`, `_read_measurement_scale`, `_measurement_model`,
`AddMeasurement`, `AnnotationManager.add_measurement`, the `/LineDimension`
branch of `_read_page`, the `_pdf_caption` → `_pdf_string` rename),
`src/pdfar/viewer.py` (`measure_scale`, `_measure_drag`, `set_measure_scale`,
`_cancel_measure`, `_extend_measure`, `_commit_measure`, `_paint_measure`,
the tool's mouse and Escape paths), `src/pdfar/main.py` (the measure action
and split button, the scale menu, `_choose_measure_scale`,
`_sync_measure_scale`, `_choose_custom_scale`, per-tab sync, reflow guard,
pen options and tint), `assets/icons/measure.svg`,
`tests/test_measurements.py`, and the toolbar-order assertion in
`tests/test_main.py`.

**Verified by eye** (headless): a 300×200 "plan" took a 180 pt horizontal
line in millimetres (63.5 mm), a diagonal in centimetres (7.63 cm) and a line
at the page top in points (180.0 pt, reading below the line). The saved page
re-rendered 2,819 red pixels with the ticks and all three readings, and after
save/reopen the three scales came back from `/Measure` with the same
readings. The real window, armed from the split button, showed the live
"6.35 cm" preview mid-drag and a dirty tab with the placed dimension after
release. Screenshots `/tmp/f103p_page.png`, `/tmp/f103p_ui_preview.png`,
`/tmp/f103p_ui_placed.png`.

**Tests**: 833 passed (was 778) — `tests/test_measurements.py` (55): a
measurement is a `/Line` with `/IT /LineDimension`, `/Measure /RL`, the
`Distance` subject, an `/AP/N` stream, the `NM` id and the history label; the
reading follows the distance and each preset scale (mm, cm, in, pt) with the
period it promises; the reading paints red on the page; the box makes room
above the line, moves below it at the page top, and stays on the page at four
corners; the pen colour and width reach `/C` and `/BS`; one undo removes it
and redo restores it; save + reopen keeps the points, the scale, the reading
and a painted appearance; a note replaces `/Contents` without losing the
drawing; two equal points, an unusable factor, a non-Latin-1 unit and a
reading wider than the page are refused with the version, dirty flag and
history unchanged; `checked_measure_scale` trims and refuses the bad cases;
a plain line with no `/Measure`, a foreign `/Measure` subtype and an indirect
`/Measure` object read as a line / a line / a measurement respectively. On
the tool side: a preset arms the tool and sets the default action and
tooltips; the menu lists the presets and Custom Scale; a drag places one
measurement while a click places none; Escape, a tool change and reflow stop
it; the preview follows the drag; the custom dialog arms a scale, warns and
keeps the old one for out-of-range input, and cancels cleanly; two tabs keep
two scales; the pen controls appear and style the mark; and a 90° view still
lands the dimension on the dragged page points. Three consecutive full runs.

**Performance on the 540-page book** (page 1): the measurement itself is the
same copy-and-adopt edit as every other kind — 59 ms median (one 103 ms
first run), undo 16.9 ms and redo 16.6 ms including the one-page re-read.
Through a real `PDFView`, 60 pt of drag previews in 0.3 ms, release applies
the dimension in 260 ms including the page re-read, the re-rasterised page
paints in 307 ms and a no-edit repaint is 2.9 ms. Saving the book with the
measurement is 2.05 s for 24 MB.

**What is not here**: snapping to geometry or existing dimensions, moving or
editing the endpoints of a placed measurement, and scale calibration from a
known length (F10-3r) — undo and redo are the only ways to change a placed
distance.

---

### F10-3q Measuring perimeters and areas — [x] Implemented

**Completed**: 2026-10-27

**What landed**: a measurement is now one of three kinds, and each is a real
PDF dimension: a distance stays a `/Line` with `/IT /LineDimension` (F10-3p),
a perimeter is a `/PolyLine` with `/IT /PolyLineDimension`, and an area is a
`/Polygon` with `/IT /PolygonDimension`. All three carry the same rectilinear
`/Measure` dictionary — `/Subtype /RL`, `/R (1 pt = …)`, the unit in `/X` —
and the multi-point kinds add an `/A` array with a `^2` unit (e.g.
`mm^2`) and the same precision, so an editor that understands dimensions
computes the number PDFAR shows. `MeasurementAnnotation` gained `kind` and a
list of `points`; `measurement_reading()` is the one place that turns a kind
and its geometry into text.

**The geometry.** A perimeter is the open path between the corners: the run
from the last corner back to the first is not part of the reading, so a
100 × 50 pt rectangle is 250 pt, not 300. An area closes implicitly and is the
shoelace about the first vertex; a corner repeated at the end (a user clicking
the first corner) is dropped first, so the same outline gives one number
whether or not it was closed by hand. Before the document is touched,
`_checked_measurement_points()` refuses a distance that is not two distinct
points, a perimeter shorter than two, an area with fewer than three, adjacent
duplicate corners (division by zero in the normal), a zero-area or
self-intersecting outline (a bow tie would give a meaningless number), any
non-finite coordinate, more than 2048 points, and an unknown kind. A valid
area must be positive.

**The appearance.** MuPDF paints polylines and polygons bare, so the whole mark
is PDFAR's own form XObject in `/AP/N`: the path (with `h` for an area), a
3 × 3 square at every corner, and a Helvetica reading above the topmost
geometry (below it near the page top), in `MEASURE_COLOR` and the pen's width.
As with a distance, `/Rect` is written directly into the PDF in bottom-up
space after `update()`, and `/IT`, `/Measure`, `/Subj` (the kind's label),
`/Contents` and `/AP` go on in that order. The reader maps the intent name
back to the kind; a polyline with no usable `/Measure` is skipped like any
other unknown measurement, while a polygon without one is still a plain
`/Polygon` shape and is read as `ShapeAnnotation(kind="polygon")`.

**The tool.** Measure gained a kind: Distance still draws with one drag;
Perimeter and Area are click-to-place — each click adds a corner, a double-click
or Enter finishes, and an area also closes when a click lands within 6 screen
pixels of its first corner. The segment from the last corner follows the
pointer with no button held, the area preview shows its closing edge dashed,
and Enter accepts the live pointer position as a final corner. Too few corners
place nothing; Escape, a tool change, a kind change or a view-mode change
cancel the draft. The split button lists Distance, Perimeter and Area above
the units, the scale and kind are viewer state kept per tab, and the tooltips
spell out the interaction for the chosen kind. Reflow still disables it.

**Files changed**: `src/pdfar/annotations.py` (`MEASURE_KINDS`,
`MEASURE_KIND_LABELS`, `MEASURE_INTENTS`, `MEASURE_MAX_POINTS`,
`MeasurementScale.area_unit`/`area_reading`, `open_polygon`,
`measure_length`, `measure_area`, `measurement_reading`,
`_checked_measurement_points`, the kind-aware `MeasurementAnnotation`,
`_measure_dictionary`, `_measurement_appearance`, `_measurement_model`,
`AddMeasurement.kind`, the `/PolyLineDimension` and `/PolygonDimension`
branches of `_read_page`), `src/pdfar/viewer.py` (`measure_kind`,
`set_measure_kind`, `_add_measure_corner`, `_finish_measure`, the multi-point
drag paths, the kind-aware `_paint_measure`), `src/pdfar/main.py`
(`MEASURE_KIND_TIPS`, the kind actions and their menu entries,
`_choose_measure_kind`, the kind-aware tooltips and per-tab sync),
`tests/test_measurements.py`.

**Verified by eye** (headless): on a 400 × 300 page, a rectangle 100 × 50 pt
in millimetres read 88.2 mm around and 622.3 mm² inside, drawn with its corner
squares and label; a 200 pt polygon in points read 200.0 pt / 5,000.0 pt²;
save and reopen brought the kind, vertices, scale and reading back from
`/IT` and `/Measure`. The real window, armed on Area, showed the live
"970.7 mm²" rubber band mid-draft and a dirty tab with the rectangle after
Enter. Screenshots `/tmp/f103q/page.png`, `/tmp/f103q/ui_preview.png`,
`/tmp/f103q/ui_placed.png`.

**Tests**: 862 passed. `tests/test_measurements.py` grew from 55 to 85 cases:
a perimeter is a `/PolyLine` with `/IT /PolyLineDimension`, the `Perimeter`
subject and an `/A` format; an area is a `/Polygon` with
`/IT /PolygonDimension` and the `Area` subject; the readings follow the
distance, each preset scale and the squared unit (mm², cm², pt²); the repeated
closing corner and the open perimeter are pinned; the mark paints; save and
reopen keep each kind; a `/Polygon` with no `/Measure` still reads as a
polygon shape; and seven refusals (too few corners, only repeated ends, a
bow tie, a collinear outline, a one-point perimeter, adjacent duplicates and
an unknown kind) leave the version, dirty flag and history untouched. On the
tool side: a kind arms the tool and its tooltip; clicks plus Enter, a live
Enter corner, closing on the first corner, a double-click, too few corners,
Escape, a kind change, the preview, per-tab kind, pen colour/width and a 90°
view all behave as with a distance.

**Performance on the 540-page book** (page 1): the edit is the same
copy-and-adopt path — 63.6 ms median for a perimeter and 65.4 ms for an area
(distance was 59.2 ms), undo and redo 11.2 ms and 9.5 ms including the
one-page re-read. Through a real `PDFView`, a 60 pt draft preview costs 2.0 ms,
the release applies with the page re-read in 441.9 ms against 1.4 ms for a
no-edit repaint, and saving the book with the new mark is 1.99 s for 24.7 MB.
Peak RSS of the UI run was 319 MB.

**What is not here**: snapping to geometry or existing dimensions, moving or
editing the corners of a placed measurement, a superscript `mm²` in the label
(the unit is written as `mm^2`), holes/subpaths in an area, appearance streams
written separately from the annotation's own `/AP`, XFDF, and scale
calibration from a known length (F10-3r).

---

### F10-3r Calibrating the scale from a known length — [x] Implemented

**Completed**: 2026-10-27

**What landed**: "Calibrate…" in the Measure split menu derives the scale from
a line the user draws and declares. `scale_from_reference()` in
`annotations.py` is the whole arithmetic — one page point becomes
`known_length / page_length` units — and it returns a `MeasurementScale`
through `checked_measure_scale()`, so a calibration can only set a scale the
file can describe. The viewer gained a calibration mode and a
`calibration_requested(float)` signal; the window asks for the real length and
adopts the implied scale. Nothing is written to the document: calibration is
viewer state, like the units themselves, so the tab does not become dirty and
there is no edit to undo.

**The gesture.** Calibrate arms the measure tool and makes the next drag a
reference line, whatever kind is selected. The preview is the familiar
distance line with its end ticks, labelled "Set known length…" instead of a
reading. On release the view emits the length it captured in **page space**,
so a turned view calibrates against the same paper the line lies on; a press
and release with no extent emits nothing and leaves the calibration armed, so
a stray click does not open a dialog. The dialog's default text is the line's
current reading (e.g. "3.53 cm"), which the user edits to the known length, so
a near-miss is corrected rather than retyped. Escape, another tool, another
kind, another scale, switching tabs and entering Reflow all put the pending
calibration away; leaving the dialog unchanged or cancelling it keeps the old
scale.

**Validation.** `scale_from_reference()` refuses a drawn length that is not
finite and positive, a line shorter than one millionth of a point, and a known
length that is not finite and positive; the factor then has to fall inside the
1e-6 … 1e6 range a PDF number format can store, the unit has to be 1–16
printable Latin-1 characters, and the precision 0–4. In the window, a text
without a number and a unit, a zero or negative length and an unusable unit
produce a warning with the old scale intact. The calibration keeps two
decimals, like "Custom Scale…", independent of the preset's own precision.

**Files changed**: `src/pdfar/annotations.py` (`scale_from_reference`),
`src/pdfar/viewer.py` (`calibration_requested`, `_measure_calibration`,
`set_measure_calibration`, the calibration branches of the measure press,
release and preview, and `_cancel_measure` putting the armed calibration
away), `src/pdfar/main.py` (`act_measure_calibrate`, the menu entry,
`_start_measure`, `_start_calibration`, `_on_calibration_requested`, the
signal connection and its tab cleanup, the tab-change disarm, and the
calibrate action's enablement), `tests/test_measurements.py`.

**Verified by eye** (headless): a 400 × 300 plan drew a reference line
between 150 and 250 pt; the declared 250 mm made one point 2.5436 mm, and the
plan's 299.4 pt top edge then read **761.63 mm** in the saved file. The preview
showed "Set known length…" above its ticks, and the placed dimension carried
its reading above the line. Screenshots `/tmp/f103r/ui_preview.png`,
`/tmp/f103r/ui_placed.png`, `/tmp/f103r/page.png`.

**Tests**: 890 passed. `tests/test_measurements.py` grew from 85 to 112
cases: the factor is `known / drawn` and its reading and area reading follow;
a calibrated scale gets into `/Measure` as `/R (1 pt = 0.02 m)`; twelve
malformed calibrations (zero, negative, NaN or infinite lengths, a
sub-millionth line, empty and non-Latin-1 units, a factor beyond the file's
range) are refused by `scale_from_reference`; the menu lists "Calibrate…"; and
in the window: a drag sets the per-tab scale and places no annotation or
history, the dialog is prefilled with the current reading, the preview is a
calibration line, a click keeps waiting, cancelling keeps the old scale, a bad
length warns, Escape and a scale, measure or tool change disarm it, a 90° view
still calibrates on the dragged page length, and two tabs keep two calibrated
scales. Three consecutive full runs.

**Performance on the 540-page book** (page 1): calibration writes nothing, so
a complete arm → drag → adopt cycle costs **5.24 ms median** (20 cycles,
4.72–31.68 ms, including Qt event processing) and leaves the document clean
and the history empty. Measuring in the calibrated scale afterwards is the
same path as F10-3p: the distance applied in 373.1 ms through a real
`PDFView`, including the page re-read.

**What is not here**: calibrating from an existing measurement instead of a
fresh line, plan ratios ("1 mm on paper = 50 mm"), recalibrating an
already-placed measurement, snapping the reference line to geometry, and
honouring a PDF `/UserUnit` other than 1.

---

### F10-3s Appearance streams for marks from any editor — [x] Implemented

**Completed**: 2026-10-28

**What landed**: a save now guarantees that every annotation in the file it
writes carries its own drawing in `/AP/N`. The new `appearances.py` holds the
scan and the repair; `save_document()` runs it on the live document just
before the bytes are produced, on the incremental path and the staged rewrite
alike. It never invents a second drawing for a mark the file already
describes: an annotation with `/AP/N` is left byte for byte as it was, so
another editor's appearance is not "corrected". Links, popups and form
widgets are skipped — they have no mark of their own, and a widget's
appearance belongs to the form and `/NeedAppearances`, which PDFAR does not
edit.

**What was already true.** All fourteen kinds PDFAR writes were confirmed to
save a self-contained form before any code changed: `AddHighlight`, `AddNote`,
`AddInk`, `AddShape` and `AddStamp` call `Annot.update()`, which is what
writes the stream, and `AddCustomStamp`/`AddMeasurement` write their own with
`_new_stream_object()` (the measurement's caption needs it, MuPDF draws bare
geometry). The gap was on the *reading* side of the promise: a file saved by
PDFAR can contain a mark another program left for the viewer to regenerate —
the annotation dictionary says where and what, no stream says how — and the
viewers that matter disagree about whether to draw one. That is the case this
step closes, and the invariant the tests now pin for PDFAR's own writes.

**How the scan stays cheap.** Walking `page.annots()` over the 540-page book
cost 356 ms and found nothing: GS262 has 1 046 link annotations, and links
share the page's `/Annots` array while never carrying an appearance. The scan
instead reads each page's `/Annots` at the xref level, skips `/Link`,
`/Popup` and `/Widget`, and only loads a page when one of its objects has no
`/AP/N` — so a clean book pays about a quarter of that walk, and a document
with no annotations is not touched at all. A page whose array holds a direct
dictionary, or whose markup cannot be read, is skipped rather than failing the
save; a repair MuPDF refuses is skipped the same way (one damaged mark must
not stop the rest of the file from being written).

**Files changed**: `src/pdfar/appearances.py` (new: `has_appearance`,
`ensure_appearances` and the xref-level scan), `src/pdfar/save.py` (import and
the two calls around the incremental/staged split), `tests/test_appearances.py`
(new), `ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Verified by eye**: a three-page file with all fourteen kinds was saved and
painted by **Poppler** (`pdftoppm`, an independent renderer): the yellow and
sky-blue highlights, note icon, rectangle, ellipse, line, arrowhead, polygon,
Draft stamp, "PAID" caption stamp, ink stroke and the three dimensions with
their readings all appeared with no viewer-side regeneration
(`/tmp/f103s/page-1.png`, `page-2.png`, `page-3.png`). A foreign file with a
blue square whose `/AP` had been stripped, plus a PDFAR note, was saved and
also painted by Poppler with the square present — and a direct xref check
showed the square gained a form while the link kept none
(`/tmp/f103s/foreign-1.png`).

**Tests**: 898 passed, three consecutive full runs. `tests/test_appearances.py`
adds 8 cases: all fourteen kinds save a `/Subtype /Form` with a `/BBox`,
non-empty content and, where the stream names `/F1`, the font resource; the
saved file sets no `/AcroForm` to ask a viewer for help; a foreign square with
no appearance gains one on the next save while its colour survives; an
appearance the file brought is preserved byte for byte; a Save As repairs the
copy and leaves the original alone; the repair is idempotent; one undrawable
mark is skipped while the next is repaired; and a refusing repair does not
stop the save. Recolouring a highlight is checked from the saved stream, so
the file other readers get carries the new colour.

**Performance on the 540-page book** (24.7 MB, 1 046 links, nothing to
repair): the scan takes **52–77 ms** cold and ~50 ms warm; inside a full save
with one new highlight it costs about **55 ms** on a 2.04 s write (the same
save measured 2.10 s with it and 2.42 s against 2.13 s in a noisier first
pair), and a second scan after the save is 49 ms. The edit that produced the
mark is unchanged at 76 ms.

**What is not here**: deciding that an existing but blank or malformed
`/AP/N` needs redrawing (presence is the line — rewriting what another editor
wrote is not a repair), form widgets and `/NeedAppearances`, a side cache for
missing appearances at display time (PDFAR already paints what MuPDF
synthesises), and pixel-for-pixel agreement with another editor's own
default appearance for the same dictionary.

---

### F10-3t XFDF export: the document's comments as a file that can travel — [x] Implemented

**Completed**: 2026-10-28

**What landed**: `src/pdfar/xfdf.py` (new) writes Adobe's XFDF for the marks an
`AnnotationManager` lists. `export_xfdf(manager)` returns the document as text —
envelope, `<annots>`, one element per mark — and asks the document for nothing
but each page's box. The import half and the menu commands are separate steps
(F10-3u, F10-3v), so nothing in the interface reaches this yet.

**The coordinates are the crux, and they were measured rather than assumed.**
PDFAR's models, its rendering, its selection and its tools all measure *down*
from the top-left of the page's visible area; `/Rect`, `/QuadPoints`, `/L` and
`/InkList` measure *up* from the bottom-left of the page. The rule that covers
every case is `x = crop.x0 + page_x`, `y = crop_top − page_y`, where `crop_top`
is the crop box's top edge in the file's own space — which is *not* what PyMuPDF
reports as `cropbox.y1`, because that is a distance down from the media box. So
the anchor is `(cropbox.x0, mediabox.y1 − cropbox.y0)`, and four page shapes were
checked against the raw arrays rather than against a picture of the format: a
plain page, a page whose `CropBox` starts at (36, 36), one whose `MediaBox` also
starts away from zero, and one the file itself turns with `/Rotate 90`. MuPDF
reports an annotation's rectangle in the *unturned* space whatever the rotation,
which is why a mark on a scanned-turned page needs no rotation handling here.
`/QuadPoints` corner order was measured too: upper-left, upper-right, lower-left,
lower-right — the other pairing traces a bow tie across the band instead of its
two edges.

**What each kind says.** `highlight` (with `coords` per selected line), `text`
(a note, `icon="Note"`), `ink` (one `<gesture>` per stroke, so a multi-stroke
drawing keeps its strokes), `square`, `circle`, `line`, `polygon`, `polyline`,
`stamp`. An arrow is a `line` carrying `head="None" tail="ClosedArrow"`, the way
the file stores `/LE`; a measurement is its geometry's element plus `intent`
(`LineDimension`/`PolyLineDimension`/`PolygonDimension`, the slash dropped because
an XFDF name attribute carries names, not PDF tokens), a `subject` of
Distance/Perimeter/Area and `<vertices>` or `start`/`end`. The reading itself
travels as `<contents>`, which is the text the mark shows on the page.

**The scale is the one thing the format cannot hold**, and it is the one number
without which a measurement is a squiggle. It goes out as `pdfar:measure`,
`pdfar:scale`, `pdfar:unit` and `pdfar:precision`, bound to
`xmlns:pdfar="urn:pdfar:xfdf:1"`: a conforming reader ignores a prefix it does
not own, and the six decimals are what the file's own `/Measure /C` keeps, so the
reading in the exchange file and the reading on the page are the same reading. An
import that has not read the namespace may keep the geometry but must not print
the number as PDFAR's.

**What is deliberately not claimed.** No `href`/`<f>` — an unresolvable path is
worse than none, and the marks are in the elements. No empty `<fields/>`, which a
reader can take as an instruction to clear every form field. No `opacity` for a
mark with no `/CA` (PyMuPDF reports `-1`, and `-1` is not a claim about
transparency). No `color` for a note or a standard stamp, because PDFAR writes
none and the appearance paints what it paints. No `date`/`creationdate` for a mark
PDFAR made — no edit writes `/M` — so the file does not change every time it is
written. No `name` for a mark that came in without `/NM`: its object number means
nothing in another copy of the file. No `<popup>`, no FDF, no `/FreeText` (PDFAR
has no model for it, and leaving a mark out is a smaller loss than describing it
wrong). An image stamp's *picture* cannot travel; it goes out as a stamp carrying
its note, like a caption stamp.

**Found on the way — one unnamed mark becomes one row.** `annot_key()` falls back
to `anon:<xref>` when an annotation has no `/NM`, on the reasonable assumption
that PyMuPDF reports `/NM` as `info["id"]`. It does, and it also *invents* one:
every annotation a PyMuPDF-based program adds without a name is written into the
file as `/NM (fitz-A0)`, with the counter restarting per page. A 540-page
document built that way therefore carries the same name 540 times, and
`AnnotationManager`'s id-keyed flat list keeps **one** mark of them — the sidebar
shows one row, and the export writes one element, for 540 real marks. This is not
an XFDF bug and is not fixed here (identity is F10-3h's machinery, and a fix has
to say what survives a save and a clone); it is now a checklist item of its own.
The benchmark below gives each mark a distinct name, which is what a file from
another editor usually looks like.

**Two smaller findings, recorded not fixed.** `_highlight_model` keeps the `-1`
that PyMuPDF reports for an unset `/CA`, so recolouring an *inherited* highlight
would call `set_opacity(-1)`; the export sidesteps it by writing `opacity` only
inside `0.0 <= opacity < 1.0`. And `AddMeasurement` flips its `/Rect` through
`page.mediabox.height` rather than the crop box, which differs only on a page with
an offset `CropBox` — the export computes the box from the mark's own points, so it
does not inherit that.

**Files changed**: `src/pdfar/xfdf.py` (new), `tests/test_xfdf.py` (new),
`ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Verified by eye**: a three-page file holding all fourteen kinds was saved,
exported, and then **the exchange file's own numbers were drawn back onto the
pages they name** — each `rect` as a magenta box, each `coords` pair as the two
edges of the highlighted line, each `<vertices>` as a polyline — and painted by
**Poppler** (`pdftoppm`, an independent renderer, `/tmp/f103t/visual/sheet-*.png`).
Every box sat on its mark: the highlight's edges on the yellow band, the note
icon, ellipse, PAID caption and DRAFT stamp inside their frames, the ink stroke,
line and arrow, the green rectangle, the polygon, and the three dimensions with
their vertices on the paths and their readings. Read back through the *media*
box instead, the same drawing lands 36 pt off on the offset page — which is the
claim the crop-box anchor is making.

**Tests**: 921 passed, three consecutive full runs. `tests/test_xfdf.py` adds 23
cases: the envelope (XML declaration, XFDF namespace, `xml:space="preserve"`, no
`<fields>`), an empty document, thirteen marks becoming thirteen correctly-named
elements each carrying its own `name`, and the same document writing the same
file twice; the flip stated as numbers (`coords="10,80,100,80,10,70,100,70"`),
every mark's geometry compared with the raw `/QuadPoints`, `/InkList`, `/L`,
`/Vertices` and `/Rect` arrays it claims to describe, a page the file turns by
`/Rotate 90`, a crop box at (36, 36) and a media box at (10, 20); text that would
break the file (`&`, `<`, quotes, tab, CR/LF, non-ASCII) surviving a round trip
through a real parser; arrow ends, ink strokes, stamp icons and the note's missing
colour; a measurement's reading, intent, subject, unit, precision and its scale
equal to the file's `/C`; an inherited highlight with no `/CA` exported without
an `opacity` while PDFAR's own keeps `0.4`; author and both PDF dates travelling
verbatim; free text and a URI link left out of a foreign file, whose named square
keeps the colour and border width the file holds; a nameless mark travelling
nameless; a non-finite coordinate costing that mark and not the file; and control
characters being dropped instead of written.

**Performance**: about **28 µs per mark** (1 000 marks on one page: 28 ms,
138 KiB), and the per-page box lookup is cached, so 540 pages with one mark each
cost **82 ms** and 1 620 marks over 540 pages **125 ms**. On the real 540-page
GS262 book with 200 marks spread over 200 pages: **156 ms**, 27 KiB. An export is
therefore cheaper than the save it would usually follow, by two orders of
magnitude.

**What is not here**: the import half (F10-3u) and the File-menu commands that
reach either (F10-3v), so no user action produces or consumes this file yet;
`<popup>`, FDF, `<template>`, `<trails>` and the other XFDF vocabulary PDFAR has
no model for; page-number labels and `page` attributes in the `/Pages`-relative
form Adobe's own writers sometimes emit (PDFAR addresses a page by index, which is
what the format's `page` attribute carries); XFA and form fields, which are not
comments; and the identity fix the finding above asks for.

---

### F10-3u XFDF import: another reader's file becomes a plan of edits — [x] Implemented

**Completed**: 2026-10-28

**What landed**: the reading half of `src/pdfar/xfdf.py` —
`parse_xfdf(text, anchors, taken)` and `page_anchors(doc)` — and
`tests/test_xfdf_import.py` (new). A file of text goes in; an `ImportPlan` of
ready-to-apply `Add*` edits comes out, together with the marks that were left out
and the reason beside each. **Nothing here opens a PDF or changes one**: the
function is pure, the page anchors are handed to it, and the names the document
already holds are handed to it, so the decision about what an import would do is
separable from the decision to do it. That separation is why the roadmap's XFDF
import item stays open: applying the plan as one undoable edit is F10-3v, and the
menu that reaches it is F10-3w.

**Two errors, because a file can fail in two places.** `XfdfError` refuses the
whole document — it is not XML, it is not XFDF, its root is somebody else's
namespace, it is too big or built in a way no reader should attempt — and nothing
is applied. `_Skip` refuses *one element*, and the plan carries on without it and
says so: `"13 marks, 7 not taken"`, each with a label and a reason a dialog can
show. The difference matters because the second is the common case: a file from
another program contains marks PDFAR has no model for, and a reader that throws
them all away for that reason teaches the user nothing about their own comments.

**The bounds are checked before anything is parsed**, against the raw text: 32 MB
of characters, no `<!DOCTYPE` or `<!ENTITY` at all, at most 200 000 elements, no
nesting past 24 levels, and at most 10 000 marks in a plan. Counting tags as text
is deliberate — the scan decides nothing about what an element *means*, so a file
built to exhaust a reader is refused rather than attempted. What it refuses is the
one thing Python's own XML reader cannot be blamed for missing: an external entity
it would fetch, and an internal one that expands until the machine stops. The
parse itself is left to that reader, which fetches nothing outside the document
and refuses an entity no definition inside it provides.

**The coordinates are the export's rule run backwards**, through the same
function: `page_anchors` calls the `_anchor` that F10-3t wrote, so the crop box a
mark was measured from on the way out is the crop box it is measured back against
on the way in, and a round trip cannot move a mark. Checked on a page whose
`CropBox` starts at (36, 36) and whose `MediaBox` starts at (10, 20) — the numbers
come back where they went out, which is the claim the shared anchor makes and not
something either half could prove alone.

**The edits' own validators do the accepting.** `_clean_shape`,
`_checked_measurement_points` and `checked_measure_scale` are called at parse
time, so a mark whose geometry the edit would refuse is left out of the plan with
the edit's own words as the reason, and every edit in a plan is an edit that
works. That is what makes F10-3v's job small: it applies a list and cannot be
partway through a failure.

**Identity, because a name is an identity.** A `/NM` is written into the file as a
PDF literal string by `_name()`, which escapes nothing, so an importable name is
one whose characters that string can hold — `[A-Za-z0-9._:-]`, at most 128 of them
— and anything else gets a UUID of PDFAR's own. A name the document already holds
claims no new mark, and a name the file repeats claims nothing the second time:
the first copy wins. That is what makes *importing the same file twice be
importing it once*, which is the property a user who clicks the menu item twice
deserves.

**Nothing is invented.** An open `polyline` is not closed into a polygon — closing
it would draw the edge the file did not. A `Custom` stamp is taken as a caption
when it carries one and refused when its picture is the whole mark, because
redrawing a picture as text is a different comment. A measurement whose scale is
not in PDFAR's namespace becomes a plain line: the geometry is the file's and is
taken, the number is not the file's and is not printed. A note's `icon` and a
mark's `intent`, `subject`, `popup` and `<template>` are ignored, because they
say how to label a mark rather than what to draw. A `<contents>` that merely
repeats the reading the dimension computes is not stored as a note on top of it.
Three-digit colours expand, `opacity` outside 0…1 is no opacity, and a colour the
file never states stays PDFAR's default instead of becoming black by accident.

**Found by the hand-written file: a gesture that holds its points in an
attribute.** Adobe's own example writes an ink stroke's numbers as the
`<gesture>` element's *content*, which is what PDFAR writes; a file that puts them
in the `vertices` attribute the format uses for a polygon says the same drawing in
another punctuation. Both are read now — losing a sketch over how its pen was
lifted is the worse reading. (The same check is what found the 1 pt growth of a
square's and a circle's `/Rect`, recorded above as a task of its own: the box the
file states is a point wider on each side than the box PDFAR drew, MuPDF's doing
whatever `/BS /W` says, and the import believes the file.)

**Files changed**: `src/pdfar/xfdf.py`, `tests/test_xfdf_import.py` (new),
`ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Verified by eye**: a **foreign** file — twenty elements over the four pages of a
plain Letter document, every number written by hand in the file's own space
measured *up* from the bottom-left, none of it produced by PDFAR's export — was
read, applied and saved, and the result was rendered by **Poppler**
(`pdftoppm`, `/tmp/f103u/visual/sheet-*.png`). The highlight lay on the second
line; the two sticky notes sat at the top of the right margin; the dimension
spanned the fifth line and read `750.00 cm` at the 2.5 cm/pt the namespace
carried; the green two-stroke sketch and the brown single stroke — the first
written Adobe's way, the second in the attribute form — lay across the text; the
red rectangle and the blue ellipse filled their boxes; the arrow ran from
(60, 100) to (300, 220) with its open head at the far end; the teal polygon held
its four vertices; `APPROVED` sat centred on the rect that named it and
`Verified by import` above it; the area triangle read `3.658 m²`. The seven
refusals each said which element and why: the name the document already held, the
`strikeout`, the open `polyline`, the `freetext`, the page the document does not
have, the five-number `coords`, the `Paste` stamp.

**Tests**: 973 passed, three consecutive full runs. `tests/test_xfdf_import.py`
adds 52 cases, most of them on hand-written files rather than on PDFAR's own
output — the point of an exchange is that the other reader is not you. The
envelope (not XML, not XFDF, a foreign namespace, an empty file that is valid);
the four bounds and the mark ceiling; the numbers (the offset crop box round
trip, `coords` becoming one box per line, the `rect`-only fallback, malformed and
non-numeric lists, a 3-digit colour, an out-of-range opacity leaving the default);
every kind in both directions it can arrive in; the refusals and their exact
reasons; identity (a held name, a repeated name, importing twice, an unnameable
mark becoming a UUID, three nameless marks being three marks); pages out of range
in four spellings; a plan that counts what it left out and labels it; that
reading a file changes nothing; and one case that reads thirteen marks of every
kind PDFAR draws, applies every edit in the plan, re-reads the pages and finds
exactly what the plan asked for.

**Performance**: about **20–28 µs per mark** — 1 000 marks on one page in
28–47 ms, 10 000 (the ceiling) in 200 ms, 1 620 marks spread over 540 pages in
39 ms. The per-page anchor lookup is outside the parse and costs **57–78 ms** for
the whole 540-page GS262 book, once per import; that book with one mark on every
page reads in **10 ms**. An import is therefore the same order as the export
F10-3t measured, and both are dominated by the document work that follows.

**What is not here**: applying the plan (F10-3v) and the menu commands that would
reach either half (F10-3w), so no user action can yet produce or consume an XFDF
file; FDF and the `<f href>` form of an exchange, where the comments live in
another file; `<fields>` and anything to do with form values; page labels; XFA;
`<trails>`, `<popup>` geometry, `MK`/`RA` and the rest of the vocabulary PDFAR has
no model for; and the two identity and geometry findings recorded above.

---

### F10-3v XFDF import as one undoable edit — [x] Implemented

**Completed**: 2026-10-28

**What landed**: `AddImportedMarks` in `src/pdfar/annotations.py`, the
`ImportPlan.edit` property in `src/pdfar/xfdf.py`, and
`AnnotationManager.import_xfdf(text)` — which reads a file against the document it
is managing and carries the whole plan out as one step. The XFDF import checklist
item closes here; what is left of the feature is the menu that reaches it
(F10-3w).

**One import, one undo step.** The user did one thing, so the document has to
agree: an edit per mark would make Ctrl+Z take back the conversation one comment at
a time, and it would pay for a whole copy-and-adopt of the document per mark. The
composite holds the plan's edits and applies them to the one copy the session made,
so the copy happens once. Measured on a thousand marks, that is the difference
between **5.4 s and 36.2 s**.

**"Refuses the file or takes all of it" is now true at the document too**, not only
at the parser. The session already guarantees it — an edit runs against a
throwaway clone and the live document is replaced only when the edit returns — and
the composite is what turns that guarantee into one step: a mark that fails half
way through, for a reason the reading could not have known (a page that stopped
existing between the parse and the apply), takes the *import* down and leaves the
first mark of it nowhere. `Import 2 comments failed: page not in document` is the
whole of what the user sees; the version, the history and the pages are what they
were.

**It says what it touched.** `scope` is `COMMENTS`, so an import repaints no page
content and rebuilds no layout; `pages` is the sorted union of the pages its marks
name, so F10-3h's per-page re-read reads those pages and no others — an import that
wrote two pages of the 540-page book re-lists two, not five hundred and forty. A
mark that cannot say reverts the whole edit to "cannot say", which is the safe
direction: the list reads everything again rather than showing a stale row.

**A plan with nothing in it applies nothing.** An empty file, or a file whose every
mark was left out, returns its plan — so the dialog can still say "0 marks, 6 not
taken" and name the reasons — but writes no document and leaves no undo step the
user would have to pass through on the way back to their own edits.

**The names come from the document as it stands**, which is why the method
re-reads its own list before parsing: `taken` is what the document holds *now*, so
importing the same file a second time takes no mark and changes no version. The
`xfdf` import sits inside the method rather than at the top of the module because
the dependency runs the other way — the reader builds the edits this module owns —
and neither half should have to load the other to be read.

**Files changed**: `src/pdfar/annotations.py`, `src/pdfar/xfdf.py`,
`tests/test_xfdf_import.py`, `ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Verified by eye**: the same **foreign** file F10-3u was verified with — twenty
hand-written elements over four Letter pages — imported through the manager into a
document with nothing on it, and page 2 painted by **Poppler** at each step
(`/tmp/f103v/visual/{a-before,b-imported,c-undone,d-refused}-2.png`). The four
images are two pairs, **byte for byte identical**: the page after the undo is the
page before the import, and the page after a refused composite import is the page
the accepted one left. In between, the ink, the rectangle and the ellipse sit where
their numbers said, and the undo entry reads `Import 14 comments`.

**Tests**: 981 passed, three consecutive full runs. Eight cases in
`tests/test_xfdf_import.py` cover the properties above: three marks over two pages
arriving and leaving together; the composite being a `COMMENTS` edit that names
each touched page once; the undo entry carrying the count and the singular; a
failing composite leaving no part of itself; the counts a user is told; importing
the same file twice through the manager being importing it once; an empty file
making no undo step; and a file that is not XFDF being refused before anything is
applied.

**Performance** (parse + apply + re-list, on a 3-page file unless stated):

| marks | one composite edit | one edit per mark |
|---|---|---|
| 10 | 29 ms | 39 ms |
| 200 | 642 ms | 1 671 ms |
| 1 000 | **6.4 s** | **37.5 s** |
| 500 on the 540-page book | 2.45 s | (100 marks: 1.51 s) |

The parse is the cheap part throughout — 27 ms of the 6.4 s — and what remains is
MuPDF writing a mark and its appearance: about **2.8–5.4 ms per mark**, which grows
slightly with the count. **That is the number F10-3w has to design around**: a
thousand-mark import is five seconds on the GUI thread, so the menu command either
shows it is working or does not offer files of that size.

**What is not here**: the File-menu commands for either half (F10-3w), so the
import still cannot be asked for; any progress or busy indication while a large
file is applied; the export's side of the same dialog (a file chooser, a default
name, a write error); importing into a *second* document or a page range; and
merging a file whose marks the document already holds by position rather than by
name — a repeated name is refused, not reconciled.

---

### F10-3w Export and Import Comments: the exchange, at the menu — [x] Implemented

**Completed**: 2026-10-28

**What landed**: File ▸ *Export Comments (XFDF)…* and File ▸ *Import Comments
(XFDF)* in `src/pdfar/main.py` (`act_export_xfdf` / `act_import_xfdf`,
`_export_tab_comments` / `_import_tab_comments`, `skipped_report`), and two
file-level helpers in `src/pdfar/xfdf.py` — `load_xfdf(path)` and
`comments_file_for(doc_path)`. The XFDF checklist closes here: the writer, the
reader and the step that applies what the reader planned were all in the library,
and until now no action could reach any of them.

**Offered whenever a document is open, and never because one is dirty.** Revert
waits on the dirty mark because discarding nothing is not a thing to do; these two
do not. An export describes marks that are already in the file, and an import is
how a document gets marks in the first place — a clean document is the most likely
thing to be given comments, not the least.

**The name the chooser offers is the document's own** (`review.pdf` →
`review.xfdf`, beside it), because a marks file that does not say which document it
belongs to is a pile of numbers, and the folder the file came out of is where the
other reader will be pointed. A name typed without an extension gets one; a
document with nothing on it still writes a valid empty file, which is the answer to
"send me your comments" and not a silent refusal.

**The size bound moved from the string to the file.** `_guard` counts characters,
which is one read too late: `load_xfdf` checks the byte count first (UTF-8 never
holds more characters than bytes) so a two-gigabyte mistake is refused without being
read, and a missing file, a directory and a JPEG are refused in the same breath as
a file that is not XFDF — as a sentence, through `XfdfError`, because the chooser
offers every file and a user picking one has not made an error in the program.

**A thousand marks is four seconds, and the cursor says so.** The apply is MuPDF
work per mark, measured at **2.8–5.4 ms** each in F10-3v, and the wait cursor is
that number's answer: the command holds it across the whole import rather than
letting a five-second window look like a freeze. It is not conditional on the count,
because the count is only known after the parse that the apply follows.

**What the user is told, twice, on purpose.** The status bar gets one line —
`Imported 3 marks, 2 not taken from review.xfdf` — and any mark that did not arrive
is *named*, in a dialog, with the reason the reader gave:

```
Took 14 marks, 6 not taken.

Left out:
• strikeout on page 1: is not a kind of mark PDFAR can draw
• polyline on page 2: draws an open path, which PDFAR has no shape for
• text on page 10: names a page this document does not have
• stamp on page 4: is a "Paste" stamp, which is not one PDFAR draws
```

A comment that vanishes silently cannot be told apart from one that was never sent,
so a count alone would not do. The list is capped at twelve lines
(`MAX_REPORTED_SKIPS`) with the remainder counted, because a file may carry
`MAX_MARKS` refusals and a dialog nobody scrolls to the end of says less than one
that ends.

**A plan the document refuses is the manager's to explain.** `import_xfdf` returns
`None` when the edit itself failed, and every failed edit in this application
already reports through `transient_message` — so the command adds no dialog of its
own, and the user hears the reason once.

**An export is not a save.** It touches no document, marks nothing dirty, asks
nothing of the writer, and cannot fail in a way Ctrl+Z has to undo. The one thing
that can go wrong is the disk, and `OSError` becomes a critical dialog naming the
file it could not write.

**Files changed**: `src/pdfar/main.py`, `src/pdfar/xfdf.py`,
`tests/test_xfdf_ui.py` (new), `tests/test_xfdf_import.py`, `ROADMAP.md`,
`docs/pdfcraft-editing-analysis.md`.

**Tests**: **21 new** — 15 in `tests/test_xfdf_ui.py` for the window (offered only
with a document; both choosers open on the document's own name; a cancelled chooser
writes nothing and changes nothing; the marks that go out are the marks the document
holds; a name without an extension becomes a file; an empty document still writes
one; a write failure is said; a whole-file import is one undo step; a non-XFDF file
is refused out loud with no history entry; left-out marks are named; importing twice
imports once; a mark for a page the document lacks is left out and says which; a
manager's refusal is not reported as an import) and 6 in
`tests/test_xfdf_import.py` for the file boundary (`comments_file_for`, the text
round trip, a missing file, a non-text file, and the size bound applied *before* the
read — asserted by patching `open` so a file that was read anyway fails the test).
**1002 passed, three consecutive runs** (107.3 / 107.7 / 107.2 s). Ruff unchanged at
the 7 pre-existing errors. Mypy moves 542 → **545**, the three new ones being the
`QMenu | None` stub artifact `_build_menubar` already reports on every one of its 56
lines (`menubar.addMenu()` is optional in the bindings, not in fact); no new kind of
error, and the alternative was an inconsistency no reader would understand.

**Verified by eye**: `/tmp/f103w/visual/menu.py` drives the window's own handlers
with the choosers pointed at real files — never the library functions — over four
documents. Six marks of six different kinds (highlight, note, square, ink,
measurement, stamp) go out through Export as a 1 213-byte file; a second document
imports them through Import, and **Poppler** — a reader that knows nothing of
PDFAR — paints page 2 with all six in their places, including the dimension reading
`657.65 cm`, which is the scale travelling in PDFAR's own namespace
(`/tmp/f103w/visual/{a-before,b-imported,c-undone}-2.png`). One Ctrl+Z empties the
document and the page after it is **byte-identical** to the page before
(`c9a5e2ca…` both). The foreign twenty-element file the parser was verified with
arrives as `Imported 14 marks, 6 not taken` and the dialog above. Then the whole
circle: the six-kind file imported into a third document and exported again is the
same file **except one line** — the square's rect, `59,491,241,593` becoming
`58,490,242,594`, which is the 1 pt-per-side growth the open defect item below
records, now confirmed on the menu path as well as the parser's.

**Performance**:

| through the menu | measured |
|---|---|
| export, 6 marks of 6 kinds | **17.9 ms** |
| import, 6 marks of 6 kinds | **28.7 ms** |
| import, 1 000 notes over 8 pages | **4 302 ms**, one undo step ("Import 1000 comments") |
| export, 1 000 notes | **1 000 names held**, file 57 KB |
| a file that is not XFDF | refused before any mark is planned |

**What is not here**: a progress dialog for the four-second import (a busy cursor is
not a percentage, and a percentage needs a total the parse has to finish to know);
exporting a *selection* or a page range; a default that remembers where the last
file went; writing the marks into the PDF's own `/Annots` as well (they are already
there — the file is for the reader that has no PDF); and any of it on the keyboard,
since neither command has a shortcut to take without displacing one the save group
owns.

---

### F10-3x One name, one mark: identity in a document that repeats itself — [x] Implemented

**Completed**: 2026-10-28

**What landed**: the annotation list settles an identity for the document instead
of trusting each mark's own, in `src/pdfar/annotations.py` — `annot_object_key()`,
`AnnotationManager._identities()`, a page cache keyed by object number
(`_read_page`), and a `_find_annotation` that resolves either spelling. This is
the defect F10-3t found on the way out of the export, and the last open item the
XFDF work left behind.

**The defect**: PyMuPDF reports `/NM` as `info["id"]` and *invents* one for every
annotation it adds without a name — `fitz-A0`, with the counter restarting per
page. A document built by another PyMuPDF-based program therefore carries that
name once per page, and a list keyed by it showed **one** mark where the file held
540: one row in the panel, one name in the export, one mark the user could delete.
Reproduced before anything was written (`/tmp/f103x/verify.py` on a 540-page book):
540 marks in the file, one in the list.

**The rule**: a name is an identity only while it names one mark. The first in
reading order keeps it and every copy after it is listed by object number, which
nothing else in the document answers to. That is the same rule `annot_key` already
applied to a mark with no `/NM` at all — extended to a mark whose `/NM` is already
spoken for, which is the case that was missing.

**Why the pages are keyed by object number too**: the collapse was never only
between pages. A page dict keyed by name loses a mark the same way, and another
reader's file is free to repeat a name within one page. Keying the caches by object
number means no read can drop anything, and leaves the merging — which sees every
page — the single place where a name is awarded.

**Why reading order, and not the order pages happen to be read in**: the list is
incremental (F10-3h) — an edit re-reads only the pages it wrote. A rule that
depended on that would renumber the rows under the user depending on which marks
had been touched last, so `_identities` walks the cached pages in ascending order
on every reload and the answer depends only on the document.

**The settled id is written back onto the model**, not just into the dict, because
the panel row, the delete and the export all read `ann.id`: an identity that lived
only in the dictionary would fix the list and still write one name 540 times.

**What a demoted mark gives up** is portability: an object number means nothing in
another copy of the file, so across a full save a mark that lost its name may be
listed under a different one. That was already the deal for every mark that arrived
nameless, and the alternative — naming foreign marks while reading them — turns
opening a file into changing one, which is the older rule of the two. The export
already refuses to write an `anon:` id as a name (F10-3t), so none of this leaks
into another reader's file: a document of 540 shared-name marks exports as 540
marks, one of them named and 539 nameless, and the importer gives those their own
ids.

**Lookups take either form** but stay on the page the row names, so the mark that
claimed `fitz-A0` on page 1 cannot be reached by the row for page 270's copy, and
the row that lost the name still edits and deletes its own mark.

**Files changed**: `src/pdfar/annotations.py`, `tests/test_annotation_identity.py`
(new), `ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Tests**: **10 new**, in three groups because the fix can be half-right in three
different ways — nothing is lost (all listed, ids unique, page 1 claims the name, a
re-read gives the same ids, two marks named alike on *one* page are two rows), each
row still reaches its mark (rewrite and delete through a borrowed object number,
neighbors untouched, undo restores the deleted one, and an edit by the shared name
lands on the mark that claimed it), and nothing that was already unique is
disturbed (PDFAR's own UUID-named marks keep theirs, and an export names its marks
apart). **1012 passed, three consecutive runs** (108.4 / 107.5 / 107.7 s). Ruff
unchanged at the 7 pre-existing errors; mypy unchanged at 545.

**Verified by eye**: `/tmp/f103x/panel.py` opens a four-mark document in the real
window, every one of them named `fitz-A0` in the file — two of them on page 1. The
comments panel shows **four rows in three page groups**
(`/tmp/f103x/a-panel.png`), and editing the row that lost the name (`anon:26`)
changes that second mark on page 1 and no other. Poppler paints the pages
underneath (`/tmp/f103x/b-page-{1,2,3}.png`), where the note icons are four marks
where the list used to see one.

**Performance** (the 540-page book, one `fitz-A0` mark per page):

| | measured |
|---|---|
| marks listed, before → after | **1 → 540**, ids unique |
| first full read | **154 ms** |
| a whole re-read | **125 ms**, ids unchanged |
| `set_note` through a borrowed object number, mark 270 | **11 ms**, reaches page 270 |
| export | 540 marks, **1 named**, 539 nameless by design |

The merge replaced a dict comprehension with a loop over the same models, so the
fix costs no extra MuPDF work: it is one `xref` read per mark at the point the mark
was already being built.

**What is not here**: writing names into foreign documents (see above); a
guarantee that an object number survives a full save, which it does not and cannot;
and the case where a file forges a `/NM` that is *exactly* PDFAR's own spelling for
an object number — the two marks then want one key and one row is lost. No reader
produces that name, and PDFAR's export cannot; it is recorded here rather than
designed around.

---

### F10-3y The box a shape is kept in — [x] Implemented

**Completed**: 2026-10-28

**What landed**: `_trim_stored_box()` in `src/pdfar/annotations.py`, called from
`AddShape.apply` for the two kinds whose geometry *is* a box, before `update()`
paints it. This is the second of the two defects the XFDF exchange found, and the
last open item F10-3 left behind.

**The defect**: MuPDF writes a `/Square`'s or `/Circle`'s `/Rect` **one point
larger on every side** than the box it is handed, at the moment it creates the
annotation. For the other three kinds the geometry lives in `/L` and `/Vertices`
and the box is derived, so only these two carry the widening in what they are. And
the box is what an exchange carries: the export states the box the file holds, the
import draws a shape at that box, MuPDF pads it again — so the mark grew and the room
around it shrank, once per round trip, forever.

**Measured before anything was written** (`/tmp/f103y/orders.py`, `spaces.py`,
`thick.py`):

* the pad is **1 pt per side, always** — at `/BS /W` of 1, 2, 3 and 6 pt, and with
  `/Rotate` 0/90/180/270, an offset crop box and a media box that does not start at
  the origin. The array the file holds differs in those last cases; the box as the
  library reports it in page space does not, and the pad is equal on all four sides
  either way, which is what lets it be taken back without knowing which way that
  space's axes run;
* `set_rect()` cannot take it back — a no-op before `update()` and re-padded after,
  the same refusal the measurement work found for `/Line`, so writing `/Rect` at the
  xref is the library's own door rather than a bypass of it;
* the order matters: write the box *after* `update()` and the appearance MuPDF
  already made is scaled down into it, which shrinks the mark by its padding and
  thins the border with it (measured ink at 19.875…69.875 for a 50×40 box). Written
  *before*, `update()` paints the box that ends up in the file, and the border stays
  exactly 1, 2, 3 and 6 pt — the correction costs the drawing nothing.

**Ten rounds through the exchange**, both kinds, one mark each: **one distinct box**,
`(20, 20, 70, 60)` — the box that was drawn. With the repair switched off the same
ten rounds give ten distinct boxes, ending at `(9, 9, 81, 71)`: a mark that was 50×40
is 72×62, 44 % wider and 55 % taller, and it would keep going.

**Files changed**: `src/pdfar/annotations.py`, `tests/test_shapes.py`, `ROADMAP.md`,
`docs/pdfcraft-editing-analysis.md`.

**Tests**: **9 new**, in four groups — the drawn box is the stored box, in the file
and on the model (2); a saved-and-reopened shape redrawn at the box that was read
back keeps that size, which is the compounding itself (2); three exchange rounds keep
it (2); and the painted ink stays inside the box with the border the user picked, at
1, 2 and 6 pt, measured by rendering the page with and without its annotations and
comparing the two so the sample page's own text and square cancel out (3). All nine
fail with `_trim_stored_box` switched off (`/tmp/f103y/mutated.py`): without the
repair the ink lies partly outside the box the file claims, so only 5.0 pt of a 6 pt
stroke is measured inside it. The 25 pt box is deliberate — the smaller the mark, the
more an appearance left drawn for the padded box and scaled down into the corrected
one shows up as a thinned border: `/tmp/f103y/naive.py` builds that mistake on purpose
and the 6 pt case reads 5.625 pt of ink, which the test refuses. **1021 passed, three
consecutive runs** (131.5 / 129.2 / 130.5 s). Ruff unchanged at the 7
pre-existing errors; mypy unchanged at 545.

**Verified by eye**: `/tmp/f103y/verified.py` draws a rectangle and a circle over a
grey guide rectangle that is exactly the requested box, the old way and the new, at
4× (`/tmp/f103y/before.png`, `after.png`). Before, the red ink hangs a point outside
the guide on all four sides — visible as the circle crossing the box it is supposed
to fit. After, the ink sits on the guide (30.00…129.75 for a 30…130 box), the circle
is tangent to all four guide edges rather than squashed, and the border is unbroken
wherever it runs.

**Performance**: one `xref_get_key` and one `xref_set_key` per box shape, at a point
where the annotation has just been created. Shapes drawn through the manager, half
squares and half ellipses over 60 pages:

| shapes | before | after |
|---|---|---|
| 100 | 6.7 ms each | **6.8 ms** each |
| 500 | 14.0 ms each | **13.7 ms** each |
| 1000 | 22.3 ms each | **23.1 ms** each |

The growth across those rows is not this change: it is F10-3c's revision copy, which
has more document to duplicate each time, and it is the same with the repair switched
off.

**What is not here**: any change to a mark PDFAR did not draw. A square that arrives
from another reader keeps the box that reader wrote, padded or not, because reading a
file is not editing it — the same rule F10-3x recorded for names, and the reason the
checklist said the fix belongs to the shape model rather than to XFDF, where
subtracting a MuPDF constant would misread every other program's box. Also not here:
the vertex kinds, whose boxes MuPDF derives from `/L` and `/Vertices` and which are
left to it, and any attempt to make `/Rect` hold room for a border the way MuPDF's
pad did — the border is now drawn inside the box the user dragged, which is what the
selection ring and the scroll-to already assume.

### F10-4a A page that arrives turned — [x] Implemented

**Completed**: 2026-10-28

**What landed**: `_sheet_space()` and `_measure_pages()` in `src/pdfar/viewer.py`, and
the ten places that put a number out of the file on the screen now take their size and
their turn from the first. `geometry.py`'s own docstring, which had been describing a
rule the view did not follow, now describes the rule it does. This is the first item of
F10-4 and the precondition of the rest: a page that carries its own `/Rotate` is drawn,
hit, selected and clicked in one place rather than two.

**The defect**: a PDF page can say *turn me 90° before you show me* without changing
one number in its content stream, and PyMuPDF obeys that instruction unevenly across its
own API. `page.rect` and the bitmap MuPDF hands back are already turned; an annotation's
`/Rect`, a word box and a highlight's `/QuadPoints` are not — they are counted on the
sheet as it lies in the file. The view mapped those numbers with the **painted** size and
only its own turn, so on a page with `/Rotate 90` a mark sat a quarter of the page away
from its own ink: the selection ring circled blank paper, a click on the mark selected
nothing, a search hit underlined other words, and a highlight drawn over one line covered
another. Nothing in the repo showed it, because nothing had ever asked the **view** where
a mark goes on a turned page — every rotation test turns the view (`view.set_rotation`),
and the two places a page is turned (`tests/test_page_numbers.py:144`,
`tests/test_xfdf.py:306`) check the file's numbers, which are the half that was already
right. Scanned books are the ordinary case: a landscape sheet stored upright with
`/Rotate 90` is how a scanner writes it, and AGENTS.md names that document class as a
goal.

**Measured before anything was written** (`/tmp/f104a/probe.py`, `spaces.py`,
`compose.py`):

* the two spaces really are different: a 200×100 sheet at `/Rotate 90` reports
  `page.rect` `(0, 0, 100, 200)` and `page.cropbox` `(0, 0, 200, 100)`, and its text
  keeps the content stream's own `1 0 0 1 20 50 Tm` — the rendered ink moves when the
  page is turned, the numbers do not;
* **one turn of the sheet by `/Rotate` + the view's turn is the screen**.
  `map_rect(rect, W_sheet, H_sheet, page_rot + view_rot)` equals the mark's ink box in
  MuPDF's bitmap for **all sixteen** pairs of quarter turns, and `display_size()` on the
  same sum equals the bitmap size. The rule the view had been using — the painted size
  and the view's turn alone — was wrong in **twelve** of them, missing only the
  `/Rotate 0` row, which is why four years of tests never saw it;
* `rect * page.rotation_matrix` is that same turn of the sheet by the page's own angle,
  which is what the numbering code already does — the two rules agree, which is why
  numbering was never wrong either;
* `page.set_rotation()` folds what it is given: 45→0, 100→0, −90→270, 360→0, 540→180.
  F10-4b will state a resulting angle rather than assume a delta survives.

**Why the mapping moved and the model did not**: the other way was to rotate every
stored number into painted space on read and back on write. That is some fifteen
boundaries — the annotation model, the import, the export, the search hits, the
appearance streams — and it would drift two promises PDFAR has made in writing: that a
mark reports *the numbers the file states*, and that an XFDF round trip carries the
file's own coordinates the way Acrobat reads them. It is also not how PdfCraft thinks:
its annotation geometry stays in unrotated page space and only the painted appearance is
counter-rotated. One function that knows both turns keeps every promise and costs ten
call sites.

**Files changed**: `src/pdfar/viewer.py`, `src/pdfar/geometry.py`,
`tests/test_page_rotation.py` (new), `ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Left in painted space, on purpose**: layout (`_unit_size`, `_relayout`), because a page
is laid out as it lies on the screen; the tile grid and `tiles.clip_points`, because
MuPDF clips a render in the space it is rendering into; the render cache and job keys,
because they key on the view's turn and a page's own turn changes only under a
`Scope.STRUCTURE` edit, which clears them anyway; and all of the numbering geometry,
which derotates through `page.derotation_matrix` and was already correct.

**Tests**: **21 new**, in six groups, with MuPDF's own pixels as the ground truth — a
page is rendered with and without its annotations and the difference is the ink's box, so
no case is checked against the rule it is meant to test: the view reads the sheet and the
painted page apart (1); the page's turn is added to the view's (4); a mark is painted
where the view puts it, centre within 1 pt and box within 2 pt (4); a click at a mark's
painted centre is a click inside the box the file states (4); the words are where the view
puts them, every mapped box inside the ink and their union within 24 pt of it (4); and a
highlight made on a turned page covers the words it was made on (4), measured with 5 pt of
slack because MuPDF paints a highlight band a few points wider than the quad it is handed
— padding, not place. **All 21 fail** with `_sheet_space()` put back to the old rule.
**1042 passed, three consecutive runs** (191.6 / 154.2 / 162.3 s). Ruff unchanged at the
7 pre-existing errors; mypy unchanged at 545.

**Verified by eye**: `/tmp/f104a/verified.py` draws, over MuPDF's own render of each
page-turn × view-turn pair, both the rectangle the view computes now and the one the old
rule gave. The new one sits on the mark in every cell; the old one sits on blank paper in
twelve — up-left at 90, the opposite corner at 180, up-right at 270 — which is the exact
shape of what a user would have seen.

**Performance**: two extra reads per page (`cropbox`, `rotation`) at load and on a
structural edit — 36 ms for 500 pages against the 23 ms the loop it replaces took, 26 µs
a page, once per document rather than once per paint.

**What is not here**: the edit. Nothing in PDFAR can yet write a `/Rotate` into a file;
`RotatePages { pages, delta }` is F10-4b and *Rotate permanently* stays open on the
checklist for it. Also not here: a page whose `/Rotate` is not a quarter turn — PDF
requires a multiple of 90, PyMuPDF folds anything else to 0, and the view now follows
that reading instead of guessing — and the offset crop box and `/UserUnit` questions that
belong to the page-box item.

---

### F10-4b Turning a page for keeps — [x] Implemented

**Completed**: 2026-10-29

**What landed**: `src/pdfar/pages.py`, a new module for the page tree, and its first
edit — `RotatePages { pages, delta }`, a `Scope.STRUCTURE` edit that writes `/Rotate`
into the node of every page named and into no other object in the file. No viewer,
sidebar or save code changed: F10-4a's re-measure and F10-1's scope grading already
handled a structural edit, and this is the first edit that proves they do.

**The two spaces again**: `page.rotation` is the turn a page is *displayed* at,
including a `/Rotate` it only inherits from an ancestor `/Pages` node, while
`set_rotation()` writes the page's own node with an absolute angle and never touches a
parent. Composing the two — `set_rotation((page.rotation + delta) % 360)` — is what
turns one page of a shared tree without moving its neighbours, and it is why a page
that arrives inherited-at-90 leaves at 180 rather than at 90 or 270. Writing the delta
alone would silently *undo* the inherited half of the turn on such a page, which is the
bug the inheritance tests hold shut.

**Measured first** (`/tmp/f104b/probe*.py`, `iso.py`):

* PyMuPDF does resolve inheritance: with `/Rotate 90` on the root and the key stripped
  from the page, `page.rotation == 90`, `page.rect` is turned and its bitmap is
  100×200, while `/Rect` stays on the unturned sheet — F10-4a's split, arriving through
  an ancestor instead of the page;
* `doc.new_page()` writes `/Rotate 0` on **every** page it creates, so a fixture built
  that way cannot inherit anything until the key is removed with
  `xref_object`/`update_object` — the test's `inherited_pdf()` does exactly that;
* `set_rotation()` folds 45→0 and 100→0 — a *silently unchanged* document after a
  command that promised a turn — so the edit refuses an angle that is not a multiple of
  90 instead of reporting it; −90 → 270, 360 → 0, 540 → 180 all fold the way PDF says.
* **a lifetime trap** found while writing these tests, and worth the note: an
  `Annotation` reached through a *temporary* `Page` — `next(doc[0].annots()).rect` —
  segfaults (exit 139) once MuPDF frees the page the annotation hung off, while
  `.info[...]` survives, which is why two older tests never tripped it. Binding
  `page = doc[0]` before walking is safe. Every walk in PDFAR already binds its page,
  so nothing in the application was exposed; the rule is written into the test module.

**Refusals, whole**: an empty selection, an angle that is not a quarter turn, and any
page index outside the document are each an `EditError` before a single page is
written, so `RotatePages(pages=(0, 1, 7))` on a three-page file turns **nothing**
rather than the first two on the way out. A page named twice is turned once — the list
says *which* pages, not how many times it said so.

**Files changed**: `src/pdfar/pages.py` (new), `tests/test_page_rotate.py` (new),
`ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Tests**: **27 new**, in seven groups — the file read back after a save (4: neighbours
keep theirs, the angle lands on the page and not the tree, `page.rect` follows, the
words do not); inheritance (2); angles (9, including four turns returning to the
start as four separate undo steps); refusals (5, each checking the version and the
pages are untouched); undo/redo and the pages an edit names (2 — `changed_pages` is
`(1, 3)`, not every page, which is F10-1h's incremental re-list); the marks and the
window F10-4a settled (4, a `/Rect` that keeps its exact numbers, the view re-measuring
both page spaces, the selection ring on the mark's **own ink** rendered with and without
annots, and a save/reopen carrying the turn and the mark together into the view); and
the sidebar (1). **Five mutations** of `pages.py` — the empty guard (1 test), the
quarter-turn guard (1), the bounds check (3), composing with `page.rotation` (3), and
de-duplicating the list (1) — each fail the suite; **two** of `sidebar.py`'s scope
grading do the same: letting `METADATA` reach the thumbnails clears a painted icon, and
rebuilding on every `_THUMB_SCOPES` edit replaces the item a `COMMENTS` edit must have
kept. **1069 passed, three consecutive runs** (200.0 / 217.4 / 148.7 s).
Ruff unchanged at the 7 pre-existing errors
(`ruff check src tests`; both new files are clean, and the repo-wide 13 is those 7 plus
6 long-standing ones under `tools/`); mypy **547 in 16 files**, the +2 over F10-4a being
the `import-untyped` + `no-redef` pair every module pays for the house
`try: import pymupdf as fitz / except ImportError: import fitz` idiom — `xfdf.py:58`,
`edits.py:31` and `annotations.py:31` carry the identical two.

**Verified by eye**: `/tmp/f104b/verified.py` draws the ring over the mark's ink before
and after a permanent turn, and re-opens the saved file in MuPDF alone — the ring is on
the mark in all three, and the saved page's `/Rect` is still exactly
`(30.0, 12.0, 130.0, 42.0)`.

**Performance**: on the 540-page, 23.9 MB GS262 — a metadata-only edit for the floor is
40.4 ms, turning one page **48.8 ms** (58.4 ms cold), turning all 540 **210.2 ms**,
undo 5.0 ms and redo 4.4 ms. The edit itself is two dictionary writes per page; the
rest is F10-1's copy-and-adopt, which is what every structural edit already pays.

**What is not here**: the command. `main.py` still exposes only the *view* rotation
(`Ctrl+Left`/`Ctrl+Right`, Tools menu, toolbar), so nothing a user can click reaches
`RotatePages` yet — that is F10-4c, and it is now the smallest remaining gap between
the library and the window. Also not here: the other page operations that will share
`pages.py` (delete, extract, duplicate, move, reverse, insert, split/merge, page boxes),
the tree flattening that has to precede any reorder, and a file whose `/Rotate` is not
a multiple of 90 — PDF forbids it, PyMuPDF folds it, and the edit refuses to write one.

---

### F10-4c The turn reaches the menu — [x] Implemented

**Completed**: 2026-10-29

**What landed**: Tools ▸ **Rotate Page Left** and **Rotate Page Right**
(`Ctrl+Shift+Left` / `Ctrl+Shift+Right`) in `src/pdfar/main.py`, each one line of
real work — `view.apply_edit(RotatePages(pages=(view.current_page,), delta=±90))` —
and the two commands that were already called *Rotate Left* and *Rotate Right* are now
**Rotate View Left** / **Rotate View Right**, with tooltips that say the page keeps the
turn the file states.

**Why the rename is part of the feature**: a menu that reads *Rotate Left · Rotate
Right · Rotate Page Left · Rotate Page Right* is a trap, and the trap is the one this
feature set exists to spring — a user who means the screen and gets the file has
changed a document they only wanted to look at differently. The old pair's labels were
the only place the distinction was not already stated: `viewer.py` says `rotation`,
`pages.py` says `/Rotate`, and the tests say view turn and page turn. The toolbar's two
buttons, which are the same view commands, are renamed with them; `test_toolbar_icon_actions`
asserts their tooltips word for word, so the old wording was pinned in a test and had to
be moved on purpose rather than by accident.

**Why the current page, and not a chooser**: the smallest thing that reaches the edit.
A page-range dialog, a thumbnail multi-selection and *Rotate All Pages* each need
surface that PDFAR does not have yet (the thumbnail list is single-select), and none of
it is needed to answer "this page is upside down". They are F10-4d.

**Enablement**: the commands need a document and nothing else — no selection, no tool
mode. Reflow is the interesting case rather than an edge case: turning a page changes
the tree the reflowed reading is taken from, and `invalidate()` already rebuilds it
(`Scope.STRUCTURE` is in both `_PAINTED` and `_STALE_TEXT`, and reflow re-enters
through `_enter_reflow()`), so the command stays live where the drawing tools do not.

**Files changed**: `src/pdfar/main.py`, `tests/test_page_rotate.py`,
`tests/test_main.py` (the toolbar tooltips, which the rename moves), `ROADMAP.md`,
`docs/pdfcraft-editing-analysis.md`.

**Tests**: **7 new** in the command group — the page under the user's eyes is the one
that turns and its neighbours do not (and the view turn stays where it was), the page
command and the view command are different things (`Ctrl+Right` leaves the file
untouched and un-dirtied), one command is one undo step, the document goes dirty and
the title marker follows, the commands are dead without a document, they sit beside the
view pair with the shortcuts the tooltips promise, and a turned page still reads in
reflow. **Six mutations** of the wiring each fail the group: turning page 1 whatever the
user is on (1 test), left and right collapsing to the same turn (1), the page command
turning the view instead (5), the commands staying live with no document (1), leaving
the menu (1), and giving up under reflow (1). The reflow case is worth the note because
the first version of that test **passed while proving nothing**: it entered reflow
through `view.set_reflow_mode()` directly, which never re-ran the window's gating, so
the enablement assertion was reading a stale `True`. Driving it through the window's own
`_toggle_reflow()` is what makes the mutation land.
**1076 passed, three consecutive runs** (158.2 / 155.3 / 185.7 s). Ruff unchanged at the 7
pre-existing errors; mypy **549 in 16 files**, +2 over F10-4b and both of them the two
new `tools_menu.addAction()` lines joining the 46 already counted in `_build_menus()`
for the same reason — `menuBar()` and `addMenu()` are typed Optional, so every line
that hangs an action off a menu is a `union-attr`. No new kind of error, and fixing the
family is a typing pass this sprint has not taken.

**Verified by eye**: `/tmp/f104c/verified.py` opens the real window on a three-page
document with a mark on page 2 and grabs it. Before: three landscape sheets, page 2
carrying its rectangle. After `Ctrl+Shift+Right`: page 2 is a tall sheet with its text
running down it and the rectangle still on the words it was drawn over, while pages 1
and 3 have not moved. After undo: page 2 is landscape again with the mark where it was.
`/tmp/f104c/tools_menu.png` is the menu itself, showing the four commands and the two
shortcut pairs.

**Performance**: nothing new in the hot path. The command is the edit measured in
F10-4b — 48.8 ms to turn one page of the 540-page book against a 40.4 ms floor — plus
the re-render every `Scope.STRUCTURE` edit already pays; the action itself is two
attribute reads and a construction.

**What is not here**: page commands *in* the toolbar — its two rotate buttons stay the
*view* pair, now labelled as such, because a third and fourth icon beside them would be
the same trap in a smaller place — the thumbnail context menu and multi-page selection,
and *Rotate All Pages*: all F10-4d. Also not here: a confirmation, which an undoable
edit does not need.

---

### F10-4d The turn reaches the pages — [x] Implemented

**Completed**: 2026-10-29

**What landed**: the thumbnail strip takes a *pick* and turns it. `ThumbnailList` is
`ExtendedSelection` with a `CustomContextMenu`, and right-clicking it offers **Rotate
Page Left / Right** for one page, **Rotate N Pages Left / Right** for a pick and, under
a separator, **Rotate All Pages Left / Right** (`src/pdfar/sidebar.py`). The strip emits
`page_rotation_requested(pages, delta)`; `MainWindow._rotate_pages` answers with
`view.apply_edit(RotatePages(...))`, connected to whichever tab is on screen in
`_connect_tab` and dropped with it in `_disconnect_tab` (`src/pdfar/main.py`).

**Why the strip asks instead of editing**: it has a `session` in reach and could write
the angle itself, and that would be the bug — the file turns while the view goes on
painting the page it already drew, with nothing on the undo stack and no dirty marker.
Everything an edit owns already comes from the view (F10-1's adopt-and-invalidate, the
re-render, the thumbnails, the title), so the sidebar's job stops at naming the pages.
A mutation that makes the strip apply the edit itself fails a test on purpose.

**Why *Rotate All Pages* is a menu item and not a dialog**: PdfCraft keeps the same
choice in a dialog field — *which*, current or all, beside a from/to range
(`crates/ui-egui/src/commands.rs:689-695`) — and dispatches the plain command on
`targets`, the pages selected in its organize surface (`:360-365`). For the book that
arrived sideways end to end the answer is one click, and a dialog that asks *which
pages* before doing the one thing the reader meant is a step the menu can skip. The
range is the part PDFAR still does not have.

**Measured Qt behaviour, every one of it load-bearing**: a right click on a page that is
*not* part of a pick replaces the pick, and on a page that *is* one leaves it whole, so
the "N Pages" in the label is honest about what the reader is looking at and
`_picked_pages` does not have to second-guess the mouse; the offscreen platform never
turns a right click into a `QContextMenuEvent` — `QTest.mouseClick(…, RightButton)`
moves the selection and stops — so the tests hand the viewport the event themselves;
`QTest.mouseDClick` does not reach `itemDoubleClicked` either, in either selection mode,
so the jump test sends press, release, double click, release; and `QMenu.exec` blocks,
while PyQt allows the attribute to be replaced, so a fixture records the menu instead of
putting it on the screen.

**Files changed**: `src/pdfar/sidebar.py`, `src/pdfar/main.py`,
`tests/test_page_rotate.py`, `ROADMAP.md`, `docs/pdfcraft-editing-analysis.md`.

**Tests**: **10 new** in the strip group — a plain click still picks one page and
`Ctrl` adds and drops another; the double click still asks for the jump (the gesture the
selection change was most able to break quietly); the menu acts on the whole pick in page
order whatever the click order, on the page under the cursor with no pick, and on nothing
in the blank tail below the last thumbnail; the labels count the pages they will move and
the separator sits where it belongs; the menu *asks* — `((0, 2), -90)` and
`((0, 1, 2), 90)` out of the signal, and the file untouched behind it; a right click from
the mouse to a menu on the screen; blank space opens nothing; the picked pages are the
pages that turn in the real window, the view's own turn stays where it was and one undo
returns both; *Rotate All Pages* turns every page of the document in one step; and a
sidebar whose tab is no longer on screen cannot turn the document that is. **Twelve
mutations**, each caught by the test that names it: the pick staying single, the menu
policy or its connection missing (2), the pick ignored for the last page clicked, a page
invented where the click landed on nothing, a menu opened on blank space, the count
dropping out of the label, *All* meaning the pick, the strip editing through its own
session, the jump connection lost, the request turning the *view* instead of the file,
and the connection outliving its tab. One honest note:
`test_the_blank_space_below_the_pages_offers_nothing` **passes before the feature
exists** — nothing opens a menu when nothing is connected — so its green means nothing
until its mutation is tried, and it is written down here rather than counted as evidence.

**Suite**: **1086 passed, three consecutive runs** (140.9 / 129.9 / 123.7 s). Ruff
unchanged at the 7 pre-existing errors; mypy **551 in 16 files**, +2 over F10-4c and
both of them new lines
joining families the same file already had: `QMenu.addAction()` is typed Optional, which
the bookmarks handler has already been charged for, and `viewport()` is typed Optional,
which the three other context handlers have been charged for. No new kind of error.

**Verified by eye**: `/tmp/f104d/visual.py` drives the real window on a four-page
landscape book. `/tmp/f104d/pick.png` shows pages 2 and 3 both highlighted in the strip
after click and ctrl-click; `/tmp/f104d/menu.png` is the menu that opens on the right
click, *Rotate 2 Pages Left / Right* over the separator and *Rotate All Pages Left /
Right*; `/tmp/f104d/turned.png` has pages 2 and 3 as tall sheets, in the view and in
their thumbnails, page 1 still lying landscape, the title marked dirty and the view's own
turn unchanged; `/tmp/f104d/undone.png` returns all four to where they started. The strip
loses the pick when the turn rebuilds it (printed empty after the edit) — see below.

**Performance**: on the 540-page book, best of three, the new code is free — resolving
the pick out of a 540-item strip **0.0 ms**, building a menu for a 300-page pick
**0.2 ms**. What costs is what F10-4b measured: **50.2 ms** to turn one page, **228.2 ms**
for all 540, **3.9 ms** to undo, plus the strip's `STRUCTURE` rebuild at **40–95 ms**.
End to end — turn, rebuild, undo, rebuild — **458 ms** for one page and **703 ms** for
the whole book.

**What is not here**: the pick does not survive the turn. A `STRUCTURE` edit rebuilds the
strip and the selection goes with it (measured above), which is harmless when the reader
asked to turn those pages and is not when they asked to *delete* them — so keeping a pick
across a rebuild is the next line's problem, not one to solve by holding thumbnails
back. Also not here: a page range (PdfCraft's from/to dialog), the menu on any surface
but the strip, and the other page operations, which now have a selection to build on.

---

### F10-4e Pages out, and the numbers that move — [x] Implemented

**What landed**: `DeletePages { pages }` in `src/pdfar/pages.py`, and a distinction the
rest of F10-4 will keep needing: `Edit.changed`, *what a cache keyed by page number has
to look at again*, separated from `Edit.pages`, *what the user named*. Nothing in the UI
reaches the delete yet — this is the edit, its refusals and its invalidation, with no
surface attached.

**Why runs rather than pages**: MuPDF's cost is one page-tree rebuild per call, not per
page. Measured on the 540-page book: one page out **44 ms**, a hundred-page block out
**45 ms**, the same hundred removed one at a time **2055 ms**. So the pick is cut into
`contiguous_runs` and each run goes through `delete_pages(start, end)` — which also fixes
the order, because a delete shifts the numbers above it and working from the front would
spend the rest of the list on the wrong sheets. PdfCraft does the same job in one rebuild
(`crates/organize/src/lib.rs:265-293` rewrites `/Kids` from the keepers); the runs are what
PyMuPDF's API allows, and they make the common case — a block — cost the same single
rebuild.

**Why the refusals are here rather than left to MuPDF**, measured case by case:

* an empty list, refused with the same sentence `RotatePages` uses;
* a **negative** index. `delete_page(-1)` is *accepted* and removes the last page — MuPDF
  counts a negative index from the end. The range form `delete_pages(-1, -1)` does refuse,
  with `bad page number(s)`, so the check buys the wording and the fact that nothing is cut
  before it, not the corruption itself; the test therefore reads the sentence
  (`is not in this document`) rather than the exception type, which MuPDF would also raise;
* a request that would leave **no pages**. MuPDF takes the last page out without comment
  and raises `cannot save with zero pages` at the save — after the edit was accepted,
  undoable and shown as done. PdfCraft refuses the same thing up front
  (`OrganizeError::WouldRemoveAllPages`, `lib.rs:269-271`).

**Why the outline is repaired, not just pruned**: MuPDF leaves an entry whose page went
reading `page == -1` with its destination demoted to kind 0 — a row the sidebar draws and
no click can follow — while entries naming survivors are already shifted. Dropping the dead
rows is not enough, because the tree that is left can be one MuPDF refuses to write: a
first entry deeper than level 1 (`hierarchy level of item 0 must be 1`) once the row above
it went, and a step of two levels (`bad hierarchy level in row N`) once the row in between
went. One clamp fixes both — no entry may sit more than one level below the entry before it
— and levels, titles and destination points all survive the round trip through
`set_toc(simple=False)`. The tree is rewritten only when something actually died, since
`set_toc` writes every entry again. Links need nothing: measured, a link to a deleted page
is gone from the file on its own.

**Why `changed` exists**: `pages` for a delete is the list that went, and that is the wrong
answer for the annotation list, the thumbnails and the panel, because every page *above* the
cut now means a different sheet. With the deleted indices handed over as the invalidation
set, the marks above the cut keep the page numbers they were read under — the mutation is
caught by `test_a_deletion_restamps_the_marks_that_moved_up`, which is the only test in this
step that watches behaviour rather than a refusal. The cost of saying nothing is a full
re-read: **155 ms** against **49 ms** for a one-page read on the 540-page book. A cheaper
correct answer exists — everything from the lowest named page onwards — and is left for the
day an edit can be pointed at a page near the end of a big book and called out on it.

**Files changed**: `src/pdfar/pages.py` (`DeletePages`, `contiguous_runs`),
`src/pdfar/edits.py` (`Edit.changed`, `_Step.changed`), `src/pdfar/session.py` (the two
places that publish the invalidation set), `tests/test_page_delete.py` (new).

**Tests**: **26 new**. The pages that leave and the order the rest keep, named by the text
each page carries rather than by a page count that could be right by luck; a page named
twice leaving once; every refusal, each with its sentence; the runs themselves as a pure
function, and the number of rebuilds watched on a recording document (the cost contract is
otherwise only visible on a clock); the outline losing its dead row, keeping its levels and
its destination points, being promoted when its head went, being lifted when a row was left
two levels deep, and being left alone when nothing died; `scope`/`pages`/`changed`/`label`;
the marks re-stamped; undo returning every page with its marks and its outline, redo taking
them again; and the file on disk holding what the session holds. **Twelve mutations, all
caught**: the lower bound of the range check dropped, an empty request allowed, a document
emptied, nothing deduplicated (both sites at once — either one alone hides it), a run broken
into single deletes, the runs going lowest first, the dead outline rows left, the levels
unrepaired, the outline rewritten when nothing died, `changed` answering `pages`, the
session asking `pages`, and the pre-check removed whole. One methodological note the sweep
itself produced: the first pass reported a mutation *missed* that had silently edited
`RotatePages`'s identical guard instead, which is a reminder that an anchor has to be unique
in the file, not just in the class.

**Suite**: **1112 passed, three consecutive runs** (120.6 / 120.2 / 119.8 s). Ruff unchanged
at the 7 pre-existing errors; mypy unchanged at **551 in 16 files** — no new line joined a
family, which is the first step of F10-4 that can say that.

**Verified by eye**: `/tmp/f104e/visual.py` deletes page 2 of a ruled four-page book and
paints the sheet before and after (`/tmp/f104e/before.png.png`, `/tmp/f104e/after.png.png`):
PAGE 2 gone, PAGE 3 sitting at position 2 with the outline row *Same chapter* naming it and
PAGE 4 at 3 with *Chapter two*. The same run, on a book whose level-1 row named the deleted
page, prints the outline as `[[1, 'Same chapter', 2], [1, 'Chapter two', 3]]` — the orphan
promoted rather than refused.

**Performance**: on the 540-page book, best of three, through the session: one page out
**77.9 ms**, a hundred-page block out **61.8 ms** (flat, as the raw measurement predicted),
**40 scattered pages 707.7 ms** — forty runs, forty rebuilds, the honest worst case —
**undo 3.4 ms**, the strip's `STRUCTURE` rebuild **73.4 ms**. A rotate of one page is
**50.2 ms**, so a delete costs about a rotation and a rebuild of the page tree.

**What is not here**: the surface. No menu item, no confirmation, and the reader cannot ask
for a delete yet — which is deliberate, because the two things a delete needs and a rotation
could do without are both UI-side and both belong to the next line: the pick has to survive
the rebuild that removes the picked thumbnails, and a page command that destroys work needs
to say so. Also not here: the state that is keyed to a page number *outside* the document —
the saved bookmarks, the reading position, the page-numbering range and an XFDF sidecar
exported before the delete — none of which the document's own undo reaches, and which are
the reason a delete is not simply wired up today. What *is* already handled: the deleted
page's own objects do not survive a save, because an adopted edit forces the staged writer,
which runs `garbage=2` — measured, the page's object and its content stream are gone from
the file F10-2's save path writes, and are exactly what an incremental save would have left
behind.

---

### F10-4f The delete reaches the reader — [x] Implemented

**What landed**: the strip's **Delete Page** / **Delete N Pages** as the last item of the
context menu, behind a separator of its own; **Tools ▸ Delete Page**
(`Ctrl+Shift+Backspace`) for the page under the reader's eyes; `Sidebar`'s
`page_deletion_requested(pages)` connected per tab to `MainWindow._delete_pages`; the
confirmation in front of both routes; and `shifted_index(page, deleted, count)` in
`src/pdfar/pages.py`, the one formula both re-aiming numbers use.

**Why this command asks and the others do not**: a turn is undone by asking for the other way
round, so `Ctrl+Shift+Right` needs no box. A delete's undo is a keystroke the reader has to
*know to look for*, and its side effect is that every number above the gap starts naming a
different sheet. So the question is half the work: it names the sheets as the strip numbers
them (`Delete pages 2–4?`, via `_page_words` over `contiguous_runs`), says what moves, and
says Undo brings them back. A *No* is a true no-op — no edit, no undo step, no dirty file —
which is why the default button is No. The asking lives in the window rather than the strip
so the Tools route gets the same question, and the strip keeps its F10-4d boundary: it names
pages and asks, and never touches its own `session`.

**Why one function for two numbers**: the reader's place and the strip's pick are both page
numbers held outside the document, and both mean another sheet once pages go out.
`shifted_index` is the page less the deleted pages *below* it, clamped to the new last page —
the clamp being for the last sheet going, when nothing arrives to fill its number, and a page
that went itself answering with the sheet that took its place. Using it for both is what keeps
`test_the_reader_lands_on_the_sheet_that_took_the_deleted_page_s_place` and
`test_the_pick_survives_the_rebuild_it_causes` describing the same arithmetic.

**The landing is paid for only where it is needed**: `invalidate` already clamps
`current_page`, and for a delete *below* the reader — or one that leaves them on the last
sheet — the clamp's answer and the shift's answer are the same page. Measured, the scroll is a
relayout of the whole book (**886 ms** on the 540-page book), so `goto_page` is asked for only
when `landing != view.current_page`. The first version called it unconditionally and the
command cost 3.02 s on the big book; guarded, it is 2.79 s including the undo that returns.

**The defect only the painted window could show**: `invalidate` clamps `current_page` when the
count falls and emitted no `page_changed`, so the toolbar kept the *pre-delete* maximum — page
five of a four-page document was untypeable until a tab switch re-read it. `invalidate` now
emits when the count moved, placed after `content_changed` so the strip that gets the current
row is the rebuilt one, and `_on_page_changed` sets `maximum` *before* the value (on the way
back from a delete the document is wider than the box still believes, and the value would
clamp). Setting the total in the one signal every route to a new page passes through also
retires the open-time and tab-change-time duplication for the delete path.

**Files changed**: `src/pdfar/pages.py` (`shifted_index`, the singular "1 page" in the two
refusal sentences), `src/pdfar/sidebar.py` (`page_deletion_requested`, `selected_pages`,
`select_pages`, the menu entry), `src/pdfar/main.py` (`act_page_delete`, `_page_delete`,
`_delete_pages`, `_page_words`, the tab connect/disconnect, enablement,
`_on_page_changed`), `src/pdfar/viewer.py` (`invalidate` announcing a changed count),
`tests/test_page_delete.py`.

**Tests**: **24 new** (module 26 → 50). Eight parametrized `shifted_index` cases including the
clamp, the deep cut and the duplicated index; the menu entry's name for one page and for a
pick; the confirmation's title and its wording; *No* changing nothing; the delete being one
undo step with redo taking it again; the reader's landing, and the same landing from a delete
made elsewhere in the book; the toolbar total following the document out *and* back; the pick
surviving, shrinking by the row that went, and vanishing when all of it went; a refused delete
leaving the reader and their pick exactly as they were; the Tools route naming the page on
screen; the command sitting in the Tools menu next to the page turn, on its own shortcut; and
the command needing a document. **Seventeen mutations, all caught** — the strip
entry missing or mis-named, the Tools menu never showing it, the pick never put back or read as
nobody-picked, the question not waited for, the reader left on the old number, the reader given
the number they typed instead of the sheet's new one, a refused delete still reshuffling the
view, the pick not shortened by the sheet that went, the pages named one by one rather than as
runs, the Tools route forgetting which page is on screen, the request never connected, the
command not needing a document, and three parts of the shift formula (its clamp, counting every
page above the cut rather than those below it, and not deduplicating). The twelfth of those was
found by suspecting a test rather than the source: as written it asserted that
`window.findChildren(QAction)` contained "Delete Page", which is true whether or not
`tools_menu` ever displays it, since the action is parented to the window. Asserting the
*menu's* action list — the idiom `test_page_rotate.py` already uses — is what makes the
placement line in `main.py` a tested line, and deleting it now fails. One test was also wrong in
a way worth naming: reading the **last** page while a page below it goes out lands the reader
correctly even if their number was never shifted, because `goto_page`'s own clamp hides the
mistake — it became a test when the reader moved to page 4 of five.

**Suite**: **1136 passed, three consecutive runs** (151.3 / 135.2 / 137.4 s). Ruff unchanged at the 7
pre-existing errors; mypy **553 in 16 files**, two new and both already-common union-attr
results on `addAction`'s return (`QAction | None` in `sidebar.py`, `QMenu | None` in
`main.py`), the same lines every neighbour in those files produces.

**Verified by eye**: `/tmp/f104f/visual.py` drives the real window over a ruled six-page book
whose pages 2, 3 and 4 carry a red rectangle each. The reader is on page 4, the strip is
`1   2   3 >*4   5   6` with pages 2–4 picked, and the menu opened over page 4 reads
**Rotate 3 Pages Left / Right, Rotate All Pages Left / Right, Delete 3 Pages**
(`/tmp/f104f/menu.png`). Answering Yes gives `1 > 2   3` — pages 2–4 gone, reading page 2
(the sheet that took the deleted page's number), the sheets `PAGE 1 / PAGE 5 / PAGE 6`, the
number box showing 2, the outline now `[[1, 'Chapter one', 1], [1, 'Chapter two', 3]]`
(`/tmp/f104f/after.png`). The pick is *empty*, correctly: every page in it went out, and
`select_pages` clears before adding, which matters because the rebuild's own current-row
marking selects too. Undo returns all six sheets and leaves the reader on page 2
(`/tmp/f104f/undo.png`).

**Performance**: whole command including the undo that comes back from it, best of three. On an
eight-page document: a turn **22.1 ms**, one page out **36.3 ms**, three pages out **40.6 ms**,
a delete of the reader's own page **25.9 ms**. On the 540-page book, whose open alone is
**2.81 s**: a turn round trip **1.91 s**, one page out **2.79 s**, a hundred-page block
**1.73 s**, forty scattered pages **2.86 s**; `goto_page` **886 ms**, `select_pages` over 540
rows **0.17 ms**. So a delete costs roughly a turn plus a relayout, and the surface itself adds
almost nothing on top of the edit.

**What is not here**: the numbers the *user* owns outside the document — saved bookmarks, the
reading position in `QSettings`, the page-numbering range and an XFDF sidecar exported before
the delete — none of which the document's own undo reaches, and which are the next line.  The
bookmarks are that line's first consumer and have landed with it (**F10-4g**, below), and so has the
saved reading position and the reader's page across an *undo* (**F10-4h**, below); what is still not
here is the
numbering range, and the sidecar. Also
not here: the **assemble** permission, which PdfCraft consults before any page-tree edit
(`crates/engine/src/edit.rs:880-895`) and PDFAR still never asks at edit time; the pick across
an **undo** of a delete, since the reader's page is announced by the count change but the
selection is not rebuilt, a gap the reorder steps will share; and no page *range* input, so
"pages 12 to 40" is still either a pick or *all*.

---

### F10-4g Bookmarks follow their sheet — [x] Implemented

**What landed.** A page-tree step now states what it did to the page *numbers*, and the first
thing that holds a number outside the document reads that statement.

* `Edit.page_order(before_count)` (`edits.py`) answers **new index → old index**: `None` says
  the numbering is untouched, and a `-1` in the tuple says a sheet has no past — the answer the
  insert and move steps will need and the delete cannot produce. `DeletePages` overrides it with
  the survivors in the order they keep (`pages.py`), which is the same question `changed` cannot
  answer: `changed` says *which pages to re-read* and can only answer "everything from the cut
  onwards"; this says *where each page went*.
* `Renumbering(order, backward=False).where(page)` is one rule in both directions. Forward, a
  sheet that stayed is its position among the survivors and a sheet that went is the count of
  survivors under it, clamped to the last page. Backward, position `i` returns `order[i]` — the
  number the sheet came from — and a sheet with no past keeps the number it is on. The
  gone-sheet half *is* `shifted_index` (F10-4f) re-derived rather than duplicated, and a test
  cross-checks the two against each other over six delete shapes, because two ways to answer
  "where did my page go" is one too many to keep in agreement by care alone.
* The order belongs to the **step**: `_Step.order`, `EditHistory.record(…, order)`, and both
  `take_undo` / `take_redo` carry it into the step they park, so it travels with its step
  between the two stacks instead of being recomputed from a document that has already moved.
* `DocumentSession.renumbering` publishes the map for the generation that exists now, and is
  read off the edit *before* the working copy is adopted — after the adoption the document
  cannot be asked what its pages used to be. It is assigned on every step, so a turn after a
  delete clears the delete's map instead of leaving it published, and `revert()` clears it with
  the history it came from.
* `BookmarkManager.renumber(move)` moves the marks, re-sorts by `(page, level)` and writes the
  settings store **once** — and writes nothing when nothing moved, which is reachable: taking
  the last page out of a book whose marks all sit on the pages that stay.
* `Sidebar._renumber_bookmarks()` runs in the STRUCTURE branch beside the thumbnails and the
  outline: renumber, then rebuild the rows.

**Why a map and not a snapshot.** The obvious mechanism — keep the bookmark list with the step
and write it back on undo — was rejected on a case the tests hold: a bookmark the reader makes
*between* a delete and its undo is in no document revision, so restoring the old list destroys
work the user did after the step. Moving each mark with its sheet keeps it on its sheet, and the
way back is exact for every mark on a sheet that exists. It is stable rather than identity for
one mark: a bookmark whose own page went came back on the sheet it was parked on, which is the
honest answer and the one the reader can see.

**Tests**: `tests/test_bookmark_renumbering.py`, 33 of them, over a six-page book whose sheets
name themselves — so an assertion can ask *which sheet does this mark reach* (`reaches()`)
rather than comparing a number against the same arithmetic that produced it. The map's shapes,
the session's publication and clearing, the store, the tree rows, the click that follows a moved
row, the undo, the redo, two deletes undone in sequence, a mark made between a delete and its
undo, a reopen of the saved file, and two rebuilds that must not spend the map again (a
comment's scope, a thumbnail-width change).

**Mutations**: 24 of the new guards, **22 caught**. The two survivors are known and said here
rather than quietly dropped: reading `page_count` after `_open_working_copy` instead of before
is an *equivalent* mutant — the live document is not touched until `_adopt`, which is where the
comment points — and moving `_renumber_bookmarks()` outside the STRUCTURE branch survives
because the spent-once key stops the second rebuild before the scope is ever consulted. Both
guards stay: the scope branch is where the move belongs, and the key is what makes it safe.

**Suite**: **1169 passed, three consecutive runs** (187.5 / 198.6 / 246.0 s — the third run
shared the machine with the measurements below, which is what the extra 40 s is). Ruff unchanged
at the 7 pre-existing errors; mypy unchanged at **553 in 16 files** — the new lines add none.

**Verified by eye**: `/tmp/f104g/visual.py` drives the real window over a ruled six-page book
with a bookmark on every sheet, `QSettings` pointed at the scratch directory so the run cannot
touch the developer's own store. The tree reads `ch1 (p. 1) … ch6 (p. 6)`; after *Delete Page*
of page 2 the sheets are `PAGE 1, 3, 4, 5, 6` and the tree reads
`ch1 (p. 1), ch2 (p. 2), ch3 (p. 2), ch4 (p. 3), ch5 (p. 4), ch6 (p. 5)` — `ch2`'s own sheet went
and it shares page 2 with `ch3`, which is that sheet's mark. Clicking `ch4 (p. 3)` lands on
`PAGE 4`, the sheet it was made on. A thumbnail-width rebuild afterwards leaves the store
exactly as it was. Undo puts all six sheets back and the rows read
`ch1 (p. 1), ch2 (p. 3), ch3 (p. 3), ch4 (p. 4), ch5 (p. 5), ch6 (p. 6)`: every mark that could
come home did, and `ch2` stays on the sheet it was parked on (`/tmp/f104g/{before,after,resize,undo}.png`).

**Performance**: on the 540-page book, best of three, the new work in isolation and inside the
command. Renumber plus tree rebuild: **0.1 ms** with no bookmarks, **6.5 ms** with 200,
**28.6 ms** with 1 000 — against a delete-and-undo round trip of **2.0–2.3 s**, which did not
move measurably (2 174 / 2 251 / 2 042 ms at the three mark counts). A move that changes nothing
costs **4.3 ms** at 1 000 marks and writes no settings file.

**What is not here**: the reading position in `viewState/<path>` and the reader's page across an
undo (**F10-4h**), which is the same map's second consumer; the numbering range that echoes
clamped values back (**F10-4i**); the XFDF sidecar (**F10-4j**); and the search dock's stale hit
pages. Inserting a page is also where `-1` first gets used — a delete cannot produce it.

---

### F10-4h The reading position, and the reader's page across undo — [x] Implemented

**What landed.** The map F10-4g published has two more consumers, and the rule F10-4f worked out
inside the delete command now lives on the **step**, which is the only place every way a page tree
moves has to pass.

* `Renumbering.followed(page)` (`edits.py`) is `where` asked of a thing that may not survive: the
  same answer wherever the sheet is still there, `None` where it went out with the cut, and `None`
  on the way back for a sheet the step invented. A reader cannot be nowhere; a pick can. A test
  walks both directions over four orders and asserts the two agree wherever `followed` answers at
  all, because one rule under two names is only safe while nobody lets them drift.
* `PDFView` carries the reader. `invalidate` takes the page *before* the geometry is re-measured —
  `_measure_pages` clamps `current_page` into the new count and emits nothing — and after the
  relayout asks the step where that sheet is now, then goes there with the same `goto_page` the
  command used, so the page is announced to the number box and the strip rather than corrected in
  silence. A step that renumbers nothing publishes no map and moves nobody, which is the case
  `Scope.STRUCTURE` alone cannot tell from a delete: a turn is a structure edit too.
* `Sidebar` carries the pick. `_carried_pick` is asked *before* `thumbs.clear()`, because that is
  the moment the rows the reader picked by number stop existing, and the answer is put back after
  the rebuild. With no move to follow it comes back as it was, so a turn or a drag of the thumbnail
  width no longer drops a gather of pages on the floor — the cost F10-4d measured and accepted for
  the turn, which this step cannot leave in place for the delete.
* One spent-once key for the whole sidebar (`_page_tree_move`), handing the same map to the pick and
  to the bookmarks: a second spend would move a mark past its sheet *and* shrink a pick that had
  already been shrunk. `_renumber_bookmarks` now takes the map it was given instead of reading the
  session a second time, which is what makes the two consumers one decision.
* `set_current_page` marks the reader's row without editing a pick that exists
  (`Current | NoUpdate` of `QItemSelectionModel`), which is what lets a restored pick survive the
  `page_changed` that a count change announces. Measured first: a plain `setCurrentItem` on a strip
  holding the pick (1, 3) with the view landing on page 5 leaves the pick **(1, 4)** — the marking
  edits the selection through the same channel the reader gathered pages on.
* `MainWindow._delete_pages` keeps its own `shifted_index` arithmetic for the moment and is now
  redundant with the view; a test pins the two to the same landing, so the duplicate cannot drift
  before the step that removes it.

**Why the rule moved off the command.** F10-4f could put it there because the command was the only
way a page tree moved. Undo is the second way and it is not a command: `session.undo()` adopts an
older document, `invalidate` re-measures it, and the reader keeps the *number* they had, which after
an undo names the sheet below the one they were reading. Copying the same three lines into
`_step_edit` would be a second home for a formula that has to agree with the step's own map, and
the extract, insert and reorder steps would each add one. On the step, any path that moves sheets
moves the reader with them whether or not anyone remembered to ask.

**The saved position needed no new mechanism** — `get_state()` copies `current_page`, so a view
that is on the right sheet writes the right number — but it needed saying, because
`viewState/<path>` is the third thing that holds a page number outside the document and the only one
written on a tab change and on close rather than by an edit. A test asserts that the number stored
after a delete names the sheet the reader was on. What is still wrong with that store — it is
written against a tree the file on disk may never have — is F10-4l.

**Tests**: `tests/test_reading_position.py`, 25 of them, over the six-page book whose sheets name
themselves, so an assertion asks *which sheet does the reader reach* rather than comparing a number
against the arithmetic that produced it. `followed`'s shapes including its domain boundary; the
reader across a delete above, below and on their own page, a cut that takes the end of the book with
them (the case that separates "carry the sheet" from "clamp the number"), the undo, the redo, two
deletes undone in sequence, a turn, a second rebuild at the same generation, the number box, the
command and the view landing in the same place; the pick across the same moves and a comment's
scope; and the position written to the store.

**Mutations**: 18 of the new guards, **15 caught**. The three survivors are one shape — a guard
today's edits cannot step on — and are said here rather than quietly dropped: keying the spend by
*value* instead of identity needs two distinct steps publishing equal orders, which `DeletePages`
cannot produce because its order's length is the page count and every step changes it; asking the
map at any painted scope rather than STRUCTURE only needs an unspent map at a non-STRUCTURE rebuild,
which cannot happen because the step that publishes a map always invalidates with STRUCTURE; and
re-marking a row that is already current is a no-op in either channel. All three become reachable
with the insert and move steps, which is where the identity key earns its keep, so all three stay.

**Suite**: **1194 passed, three consecutive runs** (162.6 / 155.1 / 155.1 s). Ruff unchanged
at the 7 pre-existing errors; mypy unchanged at **553 in 16 files**, and none of them falls on a
line this step wrote.

**Verified by eye**: `/tmp/f104h/visual.py` drives the real window over a nine-page book whose
sheets name themselves, and reads the strip back out of its own **pixels** — the rows painted with
the palette highlight, sampled beside the sheet where only the row background can be that colour —
rather than from the selection model. Reading page 4 with pages 1, 3 and 5 picked, the painted rows
are those three; after page 2 goes out the same three *sheets* are painted under the numbers 1, 2
and 4, the reader is still on `PAGE FOUR` and the box says 3 of 8 (`/tmp/f104h/2-delete.png`). Undo
returns nine sheets, the pick as it was named and the box at 4. A turn of page 2 leaves all three
untouched. A cut that takes the whole pick leaves the reader's own row and nothing else. The
**Delete Page** command lands where the view lands, on the same sheets.

**Performance**: on the 540-page book, the map is one scan of the order per thing held by number, so
a select-all pick is 540 scans — **2.45 ms** for the whole book, against a delete command of
**385 ms** and a delete-and-undo round trip of **747 ms**. With the two new lookups stubbed out the
same delete costs 369 ms and the same round trip 719 ms, so the rule is **+16 ms** and **+28 ms** at
the worst pick the feature can be handed, and the undo is where it is cheapest because the map is
half the length. A turn, which has no map to read, is **312 ms** and unchanged. For scale, putting a
539-row pick back over 540 rows is **55 ms** on top of a rebuild that costs **93 ms**.

**What is not here**: `MainWindow._delete_pages` still works its own copy of the shift, which the
next step can now delete; the strip says "here" and "picked" through one channel, so a pick that
shrank to nothing reads as a one-page pick (**F10-4k**); the saved position is written against a
tree the file may never have (**F10-4l**); the numbering range (**F10-4i**) and the XFDF sidecar
(**F10-4j**) still hold numbers no step reaches; and the search dock's hit pages are unmoved.

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

