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

## ⚠️ Important: about `external/LibreOffice-Getting-Started-Guides-PDF-BackUp`

The `external/LibreOffice-Getting-Started-Guides-PDF-BackUp/` directory is
**NOT a library of this project** — it is **not compiled, not imported, not
linked** with PDF-Advanced-Reader, and **no files inside it are modified**.

**Purpose:** it is the user's backup repo of the LibreOffice "Getting Started"
PDF guides (downloaded from https://books.libreoffice.org/en/). It is there
**purely as test material**. It contains large, heavy PDF files — most notably:

- `external/LibreOffice-Getting-Started-Guides-PDF-BackUp/GS262-GettingStarted.pdf`

This big PDF is used to **test PDF-Advanced-Reader's performance** with large /
scanned-style documents (fast loading, smooth scrolling, tiled rendering), in
line with the Okular parity goal. It **has been verified to work** in PDFAR.

**How to apply:** use the PDFs here as a source of large documents for
performance/loading/scroll testing. Do **not** compile, import into
`src/pdfar/`, or modify anything inside this directory. It coexists with
`external/okular/` (also reference-only, see above).

---

## Project structure

```
PDF-Advanced-Reader/
├── src/pdfar/          # Application code (Python/PyQt6/PyMuPDF)
├── tests/              # pytest tests
├── external/           # ⚠️ REFERENCE / TEST material — not project code
│   ├── okular/                                        # Okular source (study)
│   └── LibreOffice-Getting-Started-Guides-PDF-BackUp/ # PDFs para pruebas (use)
├── debian/             # Debian packaging
└── run.py              # Entry point: python3 run.py
```

## Working conventions

- Work in small increments; one commit per task.
- Tests first when possible (`PYTHONPATH=src python3 -m pytest tests/ -v`).
- Don't mix large refactors with new features.
- See `AGENT_TASK.md` for the master phase plan and `ROADMAP.md` for the
  current roadmap status.
