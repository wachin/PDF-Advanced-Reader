# AGENTS.md — working in PDF-Advanced-Reader (PDFAR)

This is the canonical guide for AI agents and contributors. Claude Code reads it
through `CLAUDE.md`; GitHub Copilot reads it through
`.github/copilot-instructions.md`; tools that understand `AGENTS.md` (Codex,
Cursor, Aider, Zed, …) read it directly. Human contributors should also read
[`CONTRIBUTING.md`](CONTRIBUTING.md).

If anything here conflicts with the code, **the code wins** — then fix this
file.

---

## 1. What this project is

PDFAR 2.0 is a fast, native-feeling PDF reader for Linux built with **PyQt6**
and **PyMuPDF**. It supports continuous scrolling, text selection, search,
thumbnails, an outline, fit modes, rotation, page operations, annotations,
reflow, and editing of existing text.

- **License:** GPL-3.0-or-later. Inbound contributions are accepted under the
  same license.
- **Python:** >= 3.9 (CI runs 3.11–3.13).
- **Runtime dependencies:** PyQt6, PyMuPDF, fonttools. Nothing else.
- **Entry point:** `run.py` → `pdfar.main:main`.
- **Size:** ~43 modules / ~36 kLOC across `src/pdfar/` (including the ported
  engine), ~100 test files, ~3 158 tests.

The long-term product plan is `ROADMAP.md` (8 600+ lines). It is useful for
intent and for the "golden rules", but it **stops at Phase 5.1 (2026-11-12)**
and contains statements that are no longer true (for example, it still mentions
PNG transport, while rendering now moves raw pixel buffers). Treat it as
history plus principles, not as current state.

---

## 2. Commands

```bash
# Run the application (file dialog, or open a file)
python3 run.py
python3 run.py path/to/file.pdf

# Full test suite (~4 minutes, headless)
PYTHONPATH=src python3 -m pytest tests/ -q

# One file / one test
PYTHONPATH=src python3 -m pytest tests/test_session.py -q
PYTHONPATH=src python3 -m pytest tests/test_session.py::test_name -q

# Research harness (separate conftest; run it separately from tests/)
PYTHONPATH=src python3 -m pytest poc/tests -q

# Lint (CI gate; must stay at zero findings)
python3 -m ruff check src/ tests/

# Types (advisory: ~750 known errors, not a gate)
python3 -m mypy src/
```

`tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`, so the suite needs no X
server. For a manual headless run, set it yourself:

```bash
QT_QPA_PLATFORM=offscreen python3 run.py demo.pdf
```

Install for development:

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
```

On Debian/Ubuntu the system packages `python3-pyqt6` and `python3-pymupdf` also
work; see `README.md` for the exact package list.

**Mypy is advisory on purpose.** The codebase predates strict typing and has a
large backlog. Do not add new mypy errors, but do not block a task on the
existing ones either.

---

## 3. Repository map

| Path | What lives there |
|---|---|
| `src/pdfar/` | Application code. `main.py` (MainWindow, 4 600 lines), `viewer.py` (PDFView), `session.py`, `edits.py`, `render.py`, `tiles.py`, `cache.py`, `sidebar.py`, `pages.py`, `annotations.py`, `search.py`, `reflow.py`, text editing modules, `pdfcraft_adapter.py`. |
| `src/pdfar/pdfcraft/` | Python port of PdfCraft's existing-PDF text engine. **Read-only for now** — only `pdfcraft_adapter.py` is a public entry point. |
| `tests/` | pytest suite. `conftest.py` provides `qapp`, `sample_pdf`, `session`, `big_pdf`; `pdfcraft_fixtures.py` builds structural PDFs on the fly. |
| `poc/` | Research harness for the port (fixtures, demo, benchmark, prototype tests). Not production code. |
| `docs/` | Design and audit documents. Start at `docs/README.md`. |
| `tools/` | `benchmark.py`, `profile_open.py`, `screenshot.py`, demo-PDF generator. |
| `debian/` | Debian packaging (`changelog`, `copyright`). |
| `translations/` | Qt Linguist `.ts` sources; `.qm` files are build output. |
| `external/` | Reference/test material only — see §8. |

---

## 4. Non-negotiable invariants

These are the rules that keep the application correct. Breaking one produces
crashes, blank pages, corrupted documents or stale UI, usually far from the
change that caused it.

### 4.1 Threads and documents

- **One `fitz.Document` per thread.** PyMuPDF documents are not thread-safe.
  The GUI thread owns the live `DocumentSession`. Every worker opens its own
  copy through `session.open_copy(version)` (or the revision file) and closes
  it. `open_copy` may have to serialize the document, which is owner-only: let
  the owner call `read_version()` first, then pass the pinned version to the
  worker.
- **Never touch `session.doc` from a worker.** `DocumentSession._require_owner`
  enforces this for serialization; do not work around it.
- **Worker → GUI only through Qt signals.** Never create or touch `QWidget`,
  `QPixmap`, `QImage` painting, or `QTimer.singleShot` from a worker. Signals
  emitted from a non-Qt thread are queued to the GUI thread; timers created
  there never fire (this was the 1.x "no pages at all" bug).
- **`version` vs `generation`.** `DocumentSession.version` says *which content*
  this is; `generation` says *whether `session.doc` is still the same object*.
  Caches keyed by content use `version`; page handles and annotation references
  must rebind when `generation` changes.

### 4.2 Edits

- An `Edit` describes **intent**, never a before/after pair. It is applied to a
  throwaway working copy and adopted only if it returns without raising
  (`DocumentSession.apply`, `edits.py`). A failed edit leaves the live document
  byte-for-byte unchanged.
- **Undo is a snapshot**, not an inverse operation. The history is bounded by
  step count and bytes; revisions live as files in a private directory.
- **Invalidate by `Scope`.** Return the narrowest scope that describes what
  changed (`METADATA`, `STRUCTURE`, `CONTENT`, `COMMENTS`, `LINKS`, `FORM`,
  `FULL`). Re-rendering 540 pages for a title change is a bug.
- **Route every mutation through `session.apply`** (or `viewer.apply_edit`) so
  the version bumps and caches drop. In-place mutation without a version bump
  gets painted over by renders of the old content.
- **`fitz.Page` objects go stale after any mutation.** Re-open `doc[i]` after an
  edit; never keep a `Page` across one.

### 4.3 Rendering and overlays

- `RenderPool` renders in worker threads and returns plain data (`samples`,
  `stride`, `width`, `height`, words). `QImage`/`QPixmap` conversion happens on
  the GUI thread (`RenderResult.to_qimage`).
- `PageCache` is byte-budgeted and viewport-aware; large pages are split into
  tiles (`tiles.py`) so a single page bitmap cannot explode memory.
- **Overlays are painted at paint time**, never into the cached page bitmap:
  search hits, selections, annotations, page indicators. Painting them into the
  cache makes them accumulate on every repaint.

### 4.4 Geometry

All rotation/zoom/page-to-screen math goes through `geometry.py` (and
`map_rect`). Do not duplicate it in a new feature: annotations, selection,
search, redaction, measurements and forms must all agree.

### 4.5 Shutdown

Pools and workers are stopped deterministically (`shutdown(wait=True)`), and no
worker may outlive the `QObject` that receives its signal. Close documents.

---

## 5. Text editing: three engines — know which one you are touching

| Layer | Files | Status |
|---|---|---|
| Word-level existing text | `text_edit_existing.py`, `text_edit_inline.py`, `text_engine.py`, `content_stream.py` | Production. Surgical content-stream replacement of a word or line. |
| Paragraph reflow | `paragraph.py`, `paragraph_edit.py`, `paragraph_dialog.py` | Production. Model, planner, `ReflowParagraph` edit and dialog. |
| Ported PdfCraft engine | `src/pdfar/pdfcraft/`, `pdfcraft_adapter.py` | **Read-only adapter.** `analyze_page` / `analyze_session_page` return lines, paragraph candidates, operator provenance and refusal reasons. It must not write to a document. |

Rules:

- The existing paragraph editor remains authoritative until Phase 6C is
  approved. Do not rewire it to the port as a side effect of another task.
- The adapter must never claim support it does not have. Unsupported structures
  are refused with a reason: styled runs, Form XObjects, vertical writing,
  undecodable codes, mixed fonts/sizes/colours, non-zero render modes.
- `poc/` tests exercise the engine directly; `tests/test_pdfcraft_adapter.py`
  exercises the public adapter. Keep both green.

---

## 6. Current state (2026-11-15)

- Active branch: `feature/pdfcraft-editiong` (HEAD `721a6de`).
- Done: Phases 1–5.4 (word/line editing, reliability, graphics state,
  font substitution, paragraph reflow, shared BT/ET) and Phase 6A (audit) /
  6B (read-only adapter, engine moved to `src/pdfar/pdfcraft/`).
- **Not started: Phase 6C.** The open decisions are (a) whether to port
  styled-run rewriting, which would unlock most real-world paragraphs, and
  (b) how the adapter should drive `DocumentSession` commits. Do not start
  either without the maintainer's approval.
- Known adapter refusals and the legitimate grouping differences from the
  existing engine are documented in `docs/developers/status.md`.

Update `docs/developers/status.md` when a phase starts or finishes. Do not let
this section drift.

---

## 7. Rules for agents

1. **Never push, force-push, tag or publish a release without the maintainer's
   explicit, separate approval.** Local commits are the normal end of a task;
   publishing is not. This rule overrides any general "finish the task"
   instinct.
2. **Never modify** `demo.pdf`, `demo-two-columns.pdf`, anything under
   `external/`, or tracked test fixtures. If a test or tool rewrites
   `demo.pdf`, restore it from `HEAD`. If you change a translation `.ts`,
   rebuild its `.qm` with `lrelease` and commit both.
3. **Never rewrite history, delete branches, or discard uncommitted work.**
   Investigate unexpected state instead of resetting it.
4. **No new runtime dependency** without justification. PyQt6, PyMuPDF and
   fonttools are the budget; test-only extras are listed in `pyproject.toml`.
5. **One task per commit.** Use imperative, scoped subjects (`fix:`, `feat:`,
   `docs:`, `test:`, `ci:`, `chore:`). Do not mix a refactor with a feature.
6. **Run the tests before claiming success.** Report the exact command and
   result. A passing unit test is not proof that the UI works: for editing or
   rendering changes, exercise the real path (open a PDF, edit, save, reopen).
7. **Do not skip, xfail or `--no-verify` a failing test** to make a task look
   done. Fix the cause, or report the blocker.
8. **Do not silently broaden compatibility claims.** If a code path works only
   for a fixture, say so in the docstring and in the report.
9. **Keep the diff small.** No drive-by reformatting, no renames, no dead-code
   sweeps unrelated to the task.
10. **Ask before destructive or shared-state actions** (deleting files,
    changing CI, changing permissions, publishing).

---

## 8. `external/` — reference and test material, not project code

Nothing under `external/` is compiled, imported or linked. Never modify it.

| Directory | Purpose | Rules |
|---|---|---|
| `external/okular/` | Okular source (KDE, GPL-2.0+/GPL-3.0+), studied for document loading, tiling and scrolling. | Read only. No imports from `src/pdfar/`. Ideas are fine; copied code needs license compliance. See `docs/okular-architecture.md` first. |
| `external/LibreOffice-Getting-Started-Guides-PDF-BackUp/` | Large real PDFs (notably `GS262-GettingStarted.pdf`, 540 pages) for performance testing. | Use as test input only; do not modify or import. |
| `external/two-column-test-pdfs/` | Four CC BY 4.0 research articles used by `tests/test_reflow.py` and real-world tests. | Keep `CREDITS.md` intact. Only add papers whose license permits redistribution. |
| `external/pdfcraft/` | Local checkout of the Rust PdfCraft source (MIT OR Apache-2.0), the reference for the port. | Git-ignored, not a dependency. Read only. Provenance is recorded in `poc/NOTICE.md`. |
| `external/OCR-AI/` | Local research checkouts (docling, marker, OCRmyPDF, …). | Git-ignored. Not a dependency. |

`external/okular` and the LibreOffice backup are **git submodules**. A plain
`git clone` leaves them empty; initialise them only if you need them:

```bash
git submodule update --init external/okular
git submodule update --init external/LibreOffice-Getting-Started-Guides-PDF-BackUp
```

The test suite skips tests whose real-world PDF is absent, so a fresh clone is
still testable.

---

## 9. Where to read next

| Need | Read |
|---|---|
| How the pieces fit together | `docs/developers/architecture.md` |
| What is being worked on now | `docs/developers/status.md` |
| Documentation index and which docs are current | `docs/README.md` |
| Product plan and historical rules | `ROADMAP.md` (see the caveat in §1) |
| Okular techniques | `docs/okular-architecture.md` |
| PdfCraft engine analysis | `docs/pdfcraft-editing-analysis.md` (reference), `docs/pdfcraft-architecture-audit.md` (Phase 6A proposal, superseded by the port) |
| Setup, tests, PR process | `CONTRIBUTING.md` |

---

## 10. Definition of done

A task is done when:

- the change is minimal and in the right module;
- new behaviour has a test that fails without the change;
- `PYTHONPATH=src python3 -m pytest tests/ -q` passes;
- `python3 -m ruff check src/ tests/` reports no findings;
- for editing/rendering work, the real UI path has been exercised and the
  result survives save + reopen;
- `docs/developers/status.md` is updated if the phase state changed;
- the commit message says what changed and why, and nothing was pushed.
