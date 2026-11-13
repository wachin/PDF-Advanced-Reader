"""PyMuPDF import used by the PdfCraft engine.

PDFAR supports PyMuPDF releases that only provide the ``fitz`` module name;
the rest of the application imports it the same way.
"""

try:  # PyMuPDF >= 1.24
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

__all__ = ["fitz"]
