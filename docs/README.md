# Documentation index

Start with the document that matches what you need. The **status** column
matters: several documents describe earlier attempts or proposals that were
later superseded, and reading them as current guidance is a common trap.

| Document | Status | What it is for |
|---|---|---|
| [`../README.md`](../README.md) | current | User-facing overview, install, features, performance results. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | current | Setup, test/lint commands, branch and PR process. |
| [`../AGENTS.md`](../AGENTS.md) | current | Architectural invariants and rules for AI agents and contributors. |
| [`developers/architecture.md`](developers/architecture.md) | current | How the modules fit together: threads, sessions, edits, rendering, text editing. |
| [`developers/status.md`](developers/status.md) | current | What is being worked on now, what Phase 6B delivered, and what is next. |
| [`okular-architecture.md`](okular-architecture.md) | reference | How Okular loads documents, tiles pages and scrolls; what PDFAR adopted. |
| [`en/developers/performance/large-pdf-performance.md`](en/developers/performance/large-pdf-performance.md) | current | The large/scanned-PDF performance milestone and its measurements. |
| [`en/developers/ui/page-numbering.md`](en/developers/ui/page-numbering.md) | current | Page-numbering design. |
| [`en/developers/ui/vector-icons.md`](en/developers/ui/vector-icons.md) | current | Vector icon rendering. |
| [`pdfcraft-editing-analysis.md`](pdfcraft-editing-analysis.md) | reference | Deep study of PdfCraft's Rust text-editing architecture (196 KB). Read the sections you need; it is the source for the Python port. |
| [`pdfcraft-architecture-audit.md`](pdfcraft-architecture-audit.md) | **superseded** | Phase 6A proposal. It recommended a Rust + PyO3 hybrid (Option C); the maintainer approved a Python port (Option A) instead, implemented in Phase 6B. Keep it for the analysis, not for the decision. |
| [`existing-text-edit-audit.md`](existing-text-edit-audit.md) | historical | 2026-11-08 audit of the original existing-text editor. Describes the state before Phases 5–6. |
| [`existing-text-edit-report.md`](existing-text-edit-report.md) | historical | 2026-11-08 implementation report for that editor. |
| [`../ROADMAP.md`](../ROADMAP.md) | historical + principles | 8 600-line product plan and task history. It stops at Phase 5.1 (2026-11-12) and contains outdated statements; the code and `developers/status.md` are authoritative. Its "golden rules" (§2) are still binding and are summarized in `AGENTS.md` §4. |

## Reading order for a new contributor

1. [`../README.md`](../README.md) — what the program does.
2. [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — get it running and tested.
3. [`../AGENTS.md`](../AGENTS.md) — the rules you must not break.
4. [`developers/architecture.md`](developers/architecture.md) — where things live.
5. [`developers/status.md`](developers/status.md) — what to work on next.

## Conventions for new documents

- Put design notes in `docs/`; put user-facing instructions in the README.
- Add a header with **date**, **status** (`current`, `historical`, `reference`,
  `superseded`) and **scope**, so a later reader can tell whether it still
  applies.
- When a document's recommendation is not adopted, say so at the top and link
  to what was actually done. Do not delete the analysis; it is usually the
  reason the current design exists.
