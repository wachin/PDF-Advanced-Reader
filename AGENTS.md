# AGENTS.md — Guide for AI agents and contributors

## ⚠️ Important: about `external/okular`

The `external/okular/` directory is **NOT a library of this project**.

- It is **NOT compiled, NOT imported, NOT linked** with PDF-Advanced-Reader.
- **No files inside `external/okular/` are modified.**
- It is **reference material exclusively for consultation and reading**.

**Purpose:** Okular is a very mature PDF viewer (KDE). This repository includes
it as reference source code for **studying and researching**:

1. **How Okular loads documents** — especially PDFs with scanned pages
   (large, heavy images) that open fast and look good.
2. **How Okular handles scrolling** — smooth displacement with on-demand
   page rendering.

The goal is for **PDF-Advanced-Reader** to adopt the same document-loading
and scrolling techniques as Okular, but implemented in
Python/PyQt6/PyMuPDF.

### Rules when consulting `external/okular/`

| Allowed | Not allowed |
|---------|-------------|
| Reading the source code | Copying GPL code without respecting the license* |
| Studying architecture and patterns | Compiling it as a dependency |
| Documenting findings in `docs/` | Importing it from `src/pdfar/` |
| Comparing approaches with our code | Modifying its files |

\* Note: Okular is GPL-2.0+/GPL-3.0+. PDF-Advanced-Reader is GPL-3.0+, so
**taking inspiration from its techniques and architecture is valid**; if
textual code were ever copied, the license and attribution must be
respected. Ideas and algorithms are not copyrighted — the implementation in
Python/PyQt6/PyMuPDF will always be original.

### Most relevant Okular files to study

The analysis conclusions are already documented in
**`docs/okular-architecture.md`** — consult it before re-investigating.

| Area | Path in `external/okular/` |
|------|---------------------------|
| Document loading, async rendering, cache | `core/document.cpp` / `core/document.h` |
| Generator API (PDF backend) | `core/generator.h` |
| Page model and tiles | `core/page.h` / `core/tile.h` |
| PDF backend (Poppler) | `generators/poppler/generator_pdf.cpp` |
| Scroll/pages view widget | `part/pageview.cpp` / `part/part.cpp` |
| Memory/cache configuration | `conf/` |

---

## Project structure

```
PDF-Advanced-Reader/
├── src/pdfar/          # Application code (Python/PyQt6/PyMuPDF)
├── tests/              # pytest tests
├── external/okular/    # ⚠️ REFERENCE ONLY — not project code
├── debian/             # Debian packaging
└── run.py              # Entry point: python3 run.py
```

## Working conventions

- Work in small increments; one commit per task.
- Tests first when possible (`PYTHONPATH=src python3 -m pytest tests/ -v`).
- Don't mix large refactors with new features.
- See `AGENT_TASK.md` for the master phase plan and `ROADMAP.md` for the
  current roadmap status.
