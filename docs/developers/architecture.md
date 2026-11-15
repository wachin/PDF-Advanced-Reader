# Architecture

This is a map, not a specification. It explains how the modules fit together,
which invariants cross module boundaries, and where to add things. When a detail
matters, follow the link into the code; the docstrings there are the source of
truth.

For the rules you must not break, read [`../../AGENTS.md`](../../AGENTS.md) §4
first.

---

## 1. Bird's-eye view

```
GUI thread                                    worker threads
──────────                                    ──────────────
MainWindow
 ├── DocumentTab (one per open PDF)
 │    ├── PDFView ──────── RenderPool ───────► render workers
 │    │      ▲                 │                 (own fitz.Document)
 │    │      └── result_ready ┘                       │
 │    └── Sidebar ◄── thumbnails / outline             │
 └── menus, actions, dialogs                          │
        │                                             │
        ▼                                             ▼
  DocumentSession ── open_copy(version) ──► SearchWorker, reflow, analysis
  (owns the live fitz.Document,             (each opens its own handle)
   version, history, revisions)
```

Two rules generate most of the design:

1. **PyMuPDF objects are not thread-safe.** Exactly one thread touches a given
   `fitz.Document`. The GUI thread owns the live one inside `DocumentSession`;
   every background reader opens its own copy.
2. **The GUI is only touched from the GUI thread.** Workers return plain data
   (pixel buffers, word boxes, lists) through Qt signals; `QImage`/`QPixmap`
   construction and painting happen on the GUI thread.

---

## 2. Startup and composition

`run.py` inserts `src/` on `sys.path` and calls `pdfar.main:main`.
`MainWindow` (`main.py`) builds the menus, the toolbar, the sidebar dock, the
tab widget and the status bar, and wires actions to `DocumentTab`s.

A `DocumentTab` (`tabs.py`) owns the per-document objects: one `PDFView`, one
`Sidebar`, and one `RenderPool`. Closing a tab tears all three down
deterministically (Golden Rule: no worker outlives its receiver).

`PDFView` (`viewer.py`) is a `QAbstractScrollArea` that paints pages in
`paintEvent`, handles zoom/scroll/selection/search, and owns the viewport
geometry (page tops, current page, fit mode).

---

## 3. `DocumentSession` — one authoritative document

`session.py` wraps the live `fitz.Document` and everything that must agree with
it:

| Member | Meaning |
|---|---|
| `doc` | The live document. **GUI thread only.** |
| `version` | Which content this is. Starts at 0 ("the file on disk is the document"). The first accepted edit bumps it. |
| `generation` | Whether `session.doc` is still the same object. Page handles and annotation references must rebind when this changes. |
| `history` | `EditHistory`: undo/redo snapshots, bounded by steps and bytes. |
| `changed_pages`, `renumbering` | What the last step touched, and how page numbers moved. Used by caches and by saved reading positions. |
| `dirty` | `version != _saved_version`. |
| `encryption` | Captured at open; serializing an encrypted document yields decrypted bytes, so it cannot be recovered later. |

Key operations:

- `apply(edit) -> (version, scope)` — see §4.
- `undo()` / `redo()` — walk snapshots.
- `read_version()` — owner thread; publishes the content a worker is about to
  read and returns the version it should pin.
- `open_copy(version=None) -> fitz.Document` — a private handle for a worker.
  With `version == 0` it opens the file on disk (fast path, mmap); after an
  edit it opens the revision file. **May publish a snapshot**, so call it on the
  owner thread or let the owner publish first.
- `snapshot()` — bytes of the current content (owner thread).
- `close()` — drops history and revisions, closes the document.

The revision store (`revisions.py`) keeps the content as whole files in a
private directory from the first accepted edit. That makes undo a file open
instead of a 25 MB buffer, and lets workers read a stable file while the GUI
keeps editing.

---

## 4. Edits: intent, copy-and-adopt, graded invalidation

`edits.py` defines the model:

```python
class Edit(ABC):
    scope: Scope                  # what may have changed
    requires_assembly: bool = False
    def label(self) -> str: ...
    def apply(self, doc) -> None: ...   # runs on a throwaway copy
```

`DocumentSession.apply` (`session.py`):

1. refuses early if the edit needs the assembly permission and the document
   does not grant it;
2. takes a `before` snapshot and the old page order;
3. opens a working copy;
4. runs `edit.apply(clone)`; on any exception it discards the copy and raises
   `EditError` — the live document is untouched;
5. adopts the copy, records the history step, and returns
   `(version, edit.scope)`.

`Scope` (`edits.py`) is a bit flag: `METADATA`, `STRUCTURE`, `CONTENT`,
`COMMENTS`, `LINKS`, `FORM`, `FULL`. The caller invalidates only what the scope
says. `PDFView.apply_edit` does this in one place:

```python
_version, scope = self.session.apply(edit)
self.invalidate(scope)
```

`PDFView.invalidate` drops stale render results, cache entries and derived
tables, then requests new renders. If a change did not arrive through the
session (rare), it bumps the version itself so in-flight renders of the old
content cannot be painted.

Undo is a snapshot, not an inverse operation: writing the inverse of an
arbitrary PDF edit correctly is the hard part; keeping the previous state is
not. `EditHistory` bounds both the step count and the bytes.

---

## 5. Rendering

`render.py`:

- `RenderPool(session, workers=…)` starts a small pool of threads. Each worker
  opens its own handle for the content version it is rendering, and reopens it
  when the version changes (a worker never touches `session.doc`).
- `request(page, zoom, priority, tag, alpha, rotation, clip)` queues a job.
  The job key includes the content **version**, so a pre-edit render can never
  satisfy a post-edit request. Duplicate in-flight jobs are dropped.
- `result_ready` is a Qt signal carrying a `RenderResult` — plain data:
  `samples`/`stride` (raw pixels), or `png` as a fallback, plus `words` and the
  page geometry. `RenderResult.to_qimage()` converts on the GUI thread.

`cache.py` (`PageCache`) is a byte-budget LRU keyed by
`(page, zoom, rotation, tag)`. When it is told the viewport centre it evicts the
page farthest from it first, mirroring Okular.

`tiles.py` is pure geometry. When a page bitmap would exceed
`THRESHOLD_TIMES_SCREEN` (4×) the viewport area, the page is split into
`TILE_SIZE` (1024 px) tiles and each tile is a separate `RenderPool` job with a
`clip` in page points. This is what keeps a 300 % zoom on a scanned page from
allocating a gigapixel bitmap.

---

## 6. Viewer and overlays

`PDFView` paints, in order: page background, cached page image or tile, then
**overlays** — search hits, current match, selection, annotations, link hints,
tool previews, page indicators. Overlays are never drawn into the cached
bitmap; doing so would accumulate them on every repaint.

Coordinate mapping goes through `geometry.py` (`map_rect`, rotation helpers).
Do not re-derive page↔screen math in a feature.

Selections and tool gestures live in `viewer.py` and `selection_tools.py`; the
pure geometry is separated so it can be tested without Qt.

---

## 7. Sidebar and tabs

`sidebar.py` hosts thumbnails, outline, bookmarks, annotations and attachments.
Thumbnails are rendered lazily for visible rows only and cancelled when
scrolled away. The sidebar holds *views* of document state; it re-reads after a
change rather than keeping a pending copy.

`tabs.py` is intentionally small: it owns the per-document widgets and their
teardown, while `MainWindow` coordinates activation, menus and saving.

---

## 8. Text editing: three layers

### 8.1 Word-level existing text (production)

- `content_stream.py` — tokenizer/parser that records each operator's byte
  span, plus the splicer that replaces only the bytes that changed.
- `text_engine.py` — glyph↔operator mapping, font metrics, encoding checks.
- `text_edit_existing.py` — `ReplaceExistingText`, `find_text_at_point`, and the
  fail-closed validation (font, encoding, graphics state, TJ spans, siblings).
- `text_edit_inline.py` — the in-place editor popover.

This layer is surgical: untouched operators keep their original bytes.

### 8.2 Paragraph reflow (production)

- `paragraph.py` — paragraph model, line breaking, alignment, font substitution,
  collision/overflow checks. The planner is shared by preview and commit so
  "what you see is what you save".
- `paragraph_edit.py` — `ReflowParagraph`, one `Scope.CONTENT` edit that
  re-extracts, re-plans and rewrites the paragraph's `BT … ET` block (or the
  changed rows inside a shared block).
- `paragraph_dialog.py` — the editor and preview.
- `viewer.py` — click/drag selection and the menu entries that open the word or
  paragraph editor.

### 8.3 Ported PdfCraft engine (read-only)

`src/pdfar/pdfcraft/` is a Python port of PdfCraft's existing-PDF text engine:

| Module | Role |
|---|---|
| `stream.py` | Byte-exact content-stream tokenizer, parser, splicer. |
| `matrix.py` | PDF affine matrices and frame-space projection. |
| `fonts.py` | Simple/composite fonts, encodings, CMaps, `/Differences`, widths, ToUnicode. |
| `interp.py` | Content-stream interpreter → positioned runs (including Form XObjects). |
| `lines.py` | Join runs into lines by geometry. |
| `blocks.py` | Group lines into paragraphs (alignment, leading, list markers). |
| `rewrite.py` | Replace changed rows, preserve sibling operators. |
| `page.py` | `read_page` → `PageModel`. |
| `preview.py`, `session.py` | Textless preview and a single-paragraph edit session (used by the PoC). |

Only `pdfcraft_adapter.py` is a public entry point for the application:

```python
analysis = analyze_page(doc, page_no, version=session.read_version())
analysis = analyze_session_page(session, page_no)   # reads a private copy
```

It returns frozen `AnalyzedLine` / `AnalyzedParagraph` objects with operator
provenance, a stable `ParagraphKey`, refusal reasons (`problems`) and notes.
**It never writes to a document.** Phase 6C will decide whether and how it
replaces the existing paragraph editor; until then the existing engine remains
authoritative.

The known refusals are listed in [`status.md`](status.md).

---

## 9. Other subsystems

| Area | Module(s) | Notes |
|---|---|---|
| Search | `search.py` | `SearchWorker` opens its own handle, pins a version, emits `progress`/`done`/`failed`; regex has a deadline. |
| Annotations | `annotations.py`, `appearances.py` | Annotations are PDF objects and every change is an `Edit`; `AnnotationManager` is a view that re-reads. `/NM` is the identity. |
| Bookmarks | `bookmarks.py` | Outline entries and bookmark model; page renumbering is applied on edits. |
| Page tree | `pages.py` | Rotate, move, delete, duplicate, crop, insert, extract, merge, split, reverse — each an `Edit` with `Scope.STRUCTURE` and `requires_assembly` where needed. |
| Page labels/numbers | `page_labels.py`, `page_numbers.py` | Reading the file's numbering; stamping/erasing numbered copies. |
| Reflow (reading mode) | `reflow.py` | Single-column reading layout, unrelated to paragraph editing. |
| Save | `save.py` | Atomic write (temp file + rename), preserving what the document states. |
| Revisions | `revisions.py` | Private revision files backing undo and background readers. |
| XFDF | `xfdf.py` | Export/import of annotations. |
| i18n / icons | `i18n.py`, `icons.py` | Qt Linguist lookup; SVG icons rendered at the requested size. |
| PDF port | `pdfcraft/`, `pdfcraft_adapter.py` | See §8.3. |

---

## 10. Where do I add…?

| Task | Start here |
|---|---|
| A menu action or dialog | `main.py` (wiring), then the module that performs the operation. |
| A document change | A new `Edit` subclass in the owning module (`pages.py`, `annotations.py`, `text_editing.py`, …), with the narrowest `Scope`. |
| A rendering feature | `render.py` for the job, `viewer.py` for the overlay/paint path. |
| A geometry helper | `geometry.py` (pure, no Qt). |
| A page-tree operation | `pages.py`; add `requires_assembly = True` only on the `Edit` **class**. |
| Text editing | §8 — pick the layer, then add tests at that layer. |
| A test fixture PDF | `tests/pdfcraft_fixtures.py` (structural) or `tests/conftest.py` (simple); real-world PDFs go in `external/two-column-test-pdfs/` with attribution. |

---

## 11. Testing strategy

- **Pure logic first.** `geometry.py`, `tiles.py`, `selection_tools.py`,
  `pdfcraft/matrix.py`, `pdfcraft/blocks.py` are testable without Qt or PDFs.
- **Generated PDFs** for structure: `fitz.open()` + `insert_text` /
  `insert_image`, or the raw content-stream builders in
  `tests/pdfcraft_fixtures.py`. They are deterministic and license-clean.
- **Real-world PDFs** for compatibility: the four CC BY 4.0 papers and the
  LibreOffice guide. Tests skip when the file is absent.
- **Edits** are tested through `DocumentSession.apply`, not by calling
  `edit.apply` directly, so the copy-and-adopt contract is exercised.
- **UI** tests use `pytest-qt` and the `qapp` fixture (offscreen).
- **Ported engine**: `poc/tests/` exercises it directly; `tests/test_pdfcraft_adapter.py`
  exercises the public adapter and its read-only guarantees.

Run everything before a PR:

```bash
PYTHONPATH=src python3 -m pytest tests/ -q
PYTHONPATH=src python3 -m pytest poc/tests -q
python3 -m ruff check src/ tests/
```
