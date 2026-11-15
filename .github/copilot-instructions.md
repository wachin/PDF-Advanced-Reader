# GitHub Copilot instructions

The canonical guide for this repository is [`AGENTS.md`](../AGENTS.md). Read it
before proposing changes; it covers the commands, the architecture and the
invariants that must not be broken.

The short version:

- PyQt6 + PyMuPDF, Python 3.9+, GPL-3.0-or-later.
- One `fitz.Document` per thread; the GUI thread owns `DocumentSession`, workers
  open their own copy and return data through Qt signals.
- Every document change is an `Edit` applied to a working copy and adopted only
  on success; return the narrowest `Scope`.
- Page handles go stale after a mutation — re-open `doc[i]`.
- Run `PYTHONPATH=src python3 -m pytest tests/ -q` and
  `python3 -m ruff check src/ tests/`.
- Never modify `demo.pdf`, `external/**` or tracked fixtures.
- Never push or rewrite history without the maintainer's explicit approval.
