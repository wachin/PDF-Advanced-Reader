# Provenance and licensing — PdfCraft-Python prototype

This directory is an **isolated feasibility prototype**. It is not part of
`src/pdfar/`, imports nothing from it, and is not used by the running
application.

## What was written here

Every Python file under `poc/` is an independent implementation written for
this study. **No PdfCraft source code was copied or translated line by line.**
The comments name the Rust module whose behaviour each function follows, so a
reviewer can compare them side by side.

## What was studied

PdfCraft 0.21.1 (`external/pdfcraft/`, upstream commit `8034990`) is
dual-licensed **MIT OR Apache-2.0**, copyright the ArtCraft Team and the
PdfCraft contributors. The following modules were read to derive the
algorithms implemented here:

- `crates/edit/src/text.rs`, `text/interp.rs`, `text/lines.rs`,
  `text/rewrite.rs`, `text/blocks.rs`, `text/fallback.rs`
- `crates/edit/src/text_regions/grouping.rs`, `text_regions/styled.rs`,
  `text_regions/ocr_words.rs`
- `crates/edit/src/text_session.rs`
- `crates/fonts/src/pdf.rs`
- `crates/content/src/lib.rs`

Ideas and algorithms are not copyrightable; this implementation is original.
If any code were ever copied from PdfCraft into PDFAR, the MIT or Apache-2.0
notice and attribution would have to be reproduced with it.

## Other components

- **PyMuPDF** (`pymupdf`) is AGPL-3.0. PDF-Advanced-Reader is GPL-3.0+, so the
  licences are compatible; PyMuPDF is already a runtime dependency of PDFAR.
- The test PDFs under `external/` are used read-only and are not redistributed
  by this prototype.
