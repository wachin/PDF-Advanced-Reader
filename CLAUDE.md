# CLAUDE.md

@AGENTS.md

`AGENTS.md` is the canonical guide for this repository: commands, architecture,
invariants and the rules that must not be broken. The import above pulls it into
every Claude Code session.

## Claude-specific notes

- **Do not push, force-push, tag or publish** without the maintainer's explicit,
  separate approval. Local commits are the normal end of a task.
- Run `PYTHONPATH=src python3 -m pytest tests/ -q` and
  `python3 -m ruff check src/ tests/` before reporting a task as done, and quote
  the actual results.
- The UI is PyQt6 and the document engine is PyMuPDF; both have thread rules
  that are easy to violate. Read `AGENTS.md` §4 before touching rendering,
  workers or edits.
- `docs/developers/status.md` is the current phase state; `ROADMAP.md` is
  historical context and stops at Phase 5.1.
