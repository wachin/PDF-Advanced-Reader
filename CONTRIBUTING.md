# Contributing to PDFAR

Thanks for helping. PDFAR is a PyQt6 + PyMuPDF PDF reader for Linux, and it is
developed with a strong bias toward **not breaking existing behaviour**. This
guide covers the development loop; [`AGENTS.md`](AGENTS.md) covers the
architectural invariants that every change must respect, and
[`docs/README.md`](docs/README.md) indexes the design documents.

If you use an AI coding agent, point it at `AGENTS.md` first.

---

## 1. Prerequisites

- Python **3.9+** (CI runs 3.11, 3.12 and 3.13).
- A C-free stack: PyQt6 and PyMuPDF ship wheels for Linux, macOS and Windows.
- On Debian/Ubuntu, the system packages are enough:

  ```bash
  sudo apt update
  sudo apt install python3-pyqt6 python3-pymupdf fontconfig
  ```

  `fontconfig` (`fc-list`) is used to discover system fonts for embedding.

- Optional, only for regenerating the demo PDF: `reportlab`, `pillow`,
  `cairosvg` (or `rsvg-convert`). Optional, for translations: `lrelease`
  (Debian package `qt6-l10n-tools`).

---

## 2. Set up a checkout

```bash
git clone https://github.com/wachin/PDF-Advanced-Reader.git
cd PDF-Advanced-Reader

python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
```

`.[test]` adds pytest, pytest-qt, pytest-cov, hypothesis and typeguard. The
application itself needs only PyQt6, PyMuPDF and fonttools.

The reference checkouts under `external/` are **not needed to build or test**.
Two of them are submodules and stay empty unless you ask for them:

```bash
git submodule update --init external/okular
git submodule update --init external/LibreOffice-Getting-Started-Guides-PDF-BackUp
```

Tests that need a missing real-world PDF skip themselves.

---

## 3. Run it

```bash
python3 run.py                 # file dialog
python3 run.py demo.pdf        # open a file
```

Headless (CI, containers, over SSH without X):

```bash
QT_QPA_PLATFORM=offscreen python3 run.py demo.pdf
```

---

## 4. Test it

```bash
# Everything (~4 minutes; offscreen is set by tests/conftest.py)
PYTHONPATH=src python3 -m pytest tests/ -q

# One file
PYTHONPATH=src python3 -m pytest tests/test_session.py -q

# One test, with output
PYTHONPATH=src python3 -m pytest \
  tests/test_session.py::test_workers_never_serialize_the_gui_document -vv

# Research harness for the ported PdfCraft engine (separate conftest)
PYTHONPATH=src python3 -m pytest poc/tests -q
```

Test conventions:

- Tests live in `tests/`, one file per feature area.
- Use the fixtures in `tests/conftest.py` (`qapp`, `sample_pdf`, `session`,
  `big_pdf`) instead of building a `QApplication` or a PDF by hand.
- Prefer **generated PDFs** (`fitz.open()` + `insert_text`/`insert_image`) for
  structural cases: they are small, deterministic and license-clean.
  `tests/pdfcraft_fixtures.py` is the model for building unusual content
  streams (shared BT/ET, multiple streams, Form XObjects, rotated text).
- Real-world PDFs belong in `external/two-column-test-pdfs/` (CC BY 4.0 only,
  attribution in `CREDITS.md`) or the LibreOffice backup. Do not commit
  publisher-copyrighted downloads.
- A test that asserts "the edit was refused" is as valuable as one that
  asserts success. Both are expected for existing-text editing.
- Do not weaken a test to make it pass. If the behaviour is wrong, fix the
  behaviour.

---

## 5. Lint and types

```bash
python3 -m ruff check src/ tests/     # CI gate; must be clean
python3 -m mypy src/                  # advisory; large known backlog
```

The Ruff configuration is pinned in `ruff.toml`: line length 100, rules
`E4`, `E7`, `E9`, `F`. Do not add new ignores; fix the finding or explain why
in the PR.

Style expectations:

- Type hints on public functions; `from __future__ import annotations` in new
  modules.
- Docstrings explain **why** a non-obvious decision was made, not what the
  next line does.
- Keep the diff focused. A bug fix does not need surrounding cleanup.
- Never silence a failure with `# noqa`, `--no-verify` or a skipped test
  without a comment that says why.

---

## 6. Architecture in one minute

The full map is in [`docs/developers/architecture.md`](docs/developers/architecture.md).
The three rules that cause the most bugs if ignored:

1. **One `fitz.Document` per thread.** The GUI thread owns the live
   `DocumentSession`; workers open their own copy and return plain data
   through Qt signals.
2. **Every document change is an `Edit`** applied to a working copy and
   adopted only on success (`session.apply`). A failed edit must leave the
   document untouched, and the returned `Scope` decides what the UI
   invalidates.
3. **Page handles go stale after a mutation.** Re-open `doc[i]`; never cache a
   `fitz.Page` across an edit.

Text editing has three layers (word-level, paragraph reflow, and the ported
PdfCraft engine behind a read-only adapter). Read
[`AGENTS.md` §5](AGENTS.md#5-text-editing-three-engines--know-which-one-you-are-touching)
before touching any of them.

---

## 7. Branches, commits and pull requests

- `main` is the stable branch. Do not commit directly to it.
- Create a topic branch from `main` (or from the active feature branch if the
  maintainer asks you to continue that work):

  ```bash
  git switch -c fix/search-timeout main
  ```

- One logical change per commit. Imperative, scoped subjects:

  ```
  fix: keep the render cache key stable across zoom changes
  feat: add page-label aware go-to dialog
  docs: explain the revision store
  test: cover Form XObject paragraph refusal
  ```

- In the PR description, say **what changed, why, and how you verified it**.
  Include the exact test command and result. For editing or rendering changes,
  describe the real UI path you exercised (open → edit → save → reopen), not
  just the unit tests.
- Do not commit generated files, virtualenvs, caches or personal notes. The
  `.gitignore` covers the usual ones.
- Do not push to someone else's fork, force-push a shared branch, or rewrite
  published history.

The PR template in `.github/pull_request_template.md` mirrors this checklist.

---

## 8. Adding a feature: checklist

1. Read the relevant module and its tests; find the existing extension point.
2. If the change affects the document, express it as an `Edit` with the
   narrowest `Scope`.
3. If it renders, keep the work in the render pool and the Qt objects on the
   GUI thread.
4. Add tests at the lowest level that can fail: pure geometry first, then the
   edit, then the UI wiring.
5. Run `pytest tests/ -q` and `ruff check src/ tests/`.
6. Update `docs/developers/status.md` if the phase state changed, and the
   README if user-facing behaviour changed.

---

## 9. Translations

Sources are `translations/pdfar_en.ts` and `translations/pdfar_es.ts`; the
compiled `.qm` files are tracked because the application loads them at runtime.
After editing a `.ts` file:

```bash
lrelease translations/pdfar_es.ts -qm translations/pdfar_es.qm
```

Use Qt Linguist (`linguist translations/pdfar_es.ts`) for larger edits.

---

## 10. Security

PDFAR parses untrusted files. If you find a crash, infinite loop, memory
exhaustion or content-stream bug that can be triggered by a malicious PDF,
**do not open a public issue with the file attached**. Contact the maintainer
privately (see `README.md`) and include the smallest reproducer you can.

When fixing such a bug, add a regression test with a generated PDF that
reproduces it, and make the failure mode a clean refusal rather than a crash.

---

## 11. License

PDFAR is **GPL-3.0-or-later**. By contributing you agree that your work is
licensed under the same terms. Do not paste code from a project whose license
is incompatible; if you adapt an idea from Okular or PdfCraft, record the
provenance in the relevant docstring or document (see `poc/NOTICE.md` for the
existing example).
