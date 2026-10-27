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

## F7-1 Text-to-Speech

Implement text reading.

Must respect:

- reading order
- selection
- pause
- resume
- speed

## F7-2 OCR

Modular architecture.

Do not introduce mandatory heavy OCR if it can be optional.

Ideally:

- detect scanned PDF
- offer OCR
- process pages in the background
- allow cancellation
- preserve original PDF

## F7-3 True redaction

Tracked and implemented as **F10-5**.

THIS IS CRITICAL.

A black rectangle over text is NOT true redaction.

Redaction must physically remove the underlying content.

There must be proof demonstrating that the redacted text can no longer be:

- selected
- copied
- searched
- extracted

## F7-4 PDF security

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
files for undo (F10-3c) and standalone `/Text` notes (F10-3d) done.

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
      appearance stream, so it paints without PDFAR writing one. What is left for
      the *interface* half is the tool to place one and a popup to type into; the
      document side is finished.)
- [ ] Comments panel listing annotations and jumping to them
- [ ] Freehand, shapes, stamps, measurement
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
annotation in the document (F10-3d). Next: the tool to place a note and the popup
to type into, then the comments panel.

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

