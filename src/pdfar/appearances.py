"""Appearance streams: the file PDFAR writes paints itself in any reader.

An annotation carries its own drawing in ``/AP`` — a form XObject a viewer
paints in place of working the mark out from the annotation dictionary.  The
PDF specification expects it for the marks a user sees, and the viewers that
matter read it: without one, some regenerate a default from ``/Rect`` and the
colour keys, others paint nothing, and no two agree on a highlight's geometry.
PDFAR's own edits have always called ``Annot.update()``, which writes the
stream while the mark is made.  But a file PDFAR saves can also hold marks
from another program, and those may have been left for a viewer to regenerate.

:func:`ensure_appearances` is the save-time guard: every annotation with no
``/AP/N`` is asked for one through MuPDF, so the file leaves PDFAR
self-contained.  An annotation that already has an appearance is never
touched — rewriting what another editor drew is not a repair.

The scan runs at the xref level rather than walking every page.  A 540-page
book has a page object for every sheet but annotations on a few, and links
share the ``/Annots`` array with comments while never carrying an appearance
of their own; reading each array and skipping the subtypes that draw nothing
costs about a quarter of a page walk on the book, and nothing at all on a
document with no annotations.
"""

from __future__ import annotations

import re
from typing import Optional, Set, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

#: How MuPDF writes an indirect reference inside an array: ``12 0 R``.
_ANNOT_REF = re.compile(r"(\d+)\s+\d+\s+R")

#: Subtypes that carry no mark of their own and must be left alone: a link is
#: a hit region, a popup is the window its parent opens, and a widget's
#: appearance belongs to the form and ``/NeedAppearances`` — a system PDFAR
#: does not edit, so inventing one could disagree with the field's value.
_NOT_DRAWN = frozenset({"/Link", "/Popup", "/Widget"})


def has_appearance(doc: fitz.Document, xref: int) -> bool:
    """Whether an annotation object already says how it is painted."""
    kind, value = doc.xref_get_key(xref, "AP/N")
    return kind != "null" and value not in ("", "null")


def _needs_appearance(doc: fitz.Document, xref: int) -> bool:
    """Whether *xref* is a drawn annotation with no appearance stream."""
    kind, value = doc.xref_get_key(xref, "Subtype")
    if kind != "name" or value in _NOT_DRAWN:
        return False
    return not has_appearance(doc, xref)


def _annotation_refs(doc: fitz.Document, index: int) -> Optional[Tuple[int, ...]]:
    """The annotation objects a page names.

    ``()`` when the page has none and ``None`` when the array holds a direct
    dictionary, which has no xref to check and has to be read from the page.
    """
    kind, value = doc.xref_get_key(doc.page_xref(index), "Annots")
    if kind != "array":
        return ()
    if "<<" in value:
        return None
    return tuple(int(match.group(1)) for match in _ANNOT_REF.finditer(value))


def _write_appearance(annot: fitz.Annot) -> None:
    """Have MuPDF draw the annotation and write the result into ``/AP/N``."""
    annot.update()


def _repair_page(doc: fitz.Document, index: int,
                 missing: Optional[Set[int]]) -> int:
    """Write the appearances missing on one page; ``None`` means "find them"."""
    try:
        page = doc.load_page(index)
        annots = list(page.annots() or ())
    except Exception:                       # noqa: BLE001 - bad markup is not fatal
        return 0
    repaired = 0
    for annot in annots:
        try:
            if missing is not None and annot.xref not in missing:
                continue
            if missing is None and not _needs_appearance(doc, annot.xref):
                continue
            _write_appearance(annot)
        except Exception:                   # noqa: BLE001 - leave it as it was
            continue
        repaired += 1
    return repaired


def ensure_appearances(doc: fitz.Document) -> int:
    """Give every annotation without one the appearance a viewer would draw.

    Returns how many were written.  A damaged annotation, or a page whose list
    cannot be read, is skipped: a save has to succeed on the rest of the file.
    """
    repaired = 0
    for index in range(doc.page_count):
        try:
            missing = _annotation_refs(doc, index)
            if missing is not None:
                missing = {xref for xref in missing
                           if _needs_appearance(doc, xref)}
        except Exception:                   # noqa: BLE001 - bad markup is not fatal
            continue
        if missing is not None and not missing:
            continue
        repaired += _repair_page(doc, index, missing)
    return repaired
