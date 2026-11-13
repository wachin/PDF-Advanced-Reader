"""Test documents for the PdfCraft-Python proof of concept.

Each fixture is a real PDF built with PyMuPDF (plus raw content streams where
the structure must be exact).  They cover the cases the feasibility study
requires: a simple BT/ET block, several paragraphs sharing one BT/ET, several
content streams, a Form XObject, an embedded subset font, TJ arrays with
kerning, rotated text, mixed fonts on one row, and three-line paragraphs.
"""

from __future__ import annotations

import os
import tempfile

import pymupdf as fitz

TTF = "/opt/ZCode/resources/glm/packages/pdf-plugin/dist/ui/pdfjs/standard_fonts/LiberationSans-Regular.ttf"

SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the sun sets slowly "
    "behind the distant hills and the river keeps flowing through the meadow. "
)


def _save(doc: fitz.Document, path: str, subset: bool = False) -> None:
    if subset:
        doc.subset_fonts()
    doc.save(path, garbage=2, deflate=True)
    doc.close()


def _stream_xrefs(doc: fitz.Document, page: fitz.Page):
    return list(page.get_contents())


def simple_paragraph(path: str, text: str | None = None, embedded: bool = False,
                     align: int = 0, width: float = 400.0) -> None:
    """One paragraph in its own BT/ET (three or more rows)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    body = text or (SENTENCE * 2)
    kwargs = {"fontname": "F0", "fontfile": TTF} if embedded else {"fontname": "helv"}
    page.insert_textbox(
        fitz.Rect(60, 80, 60 + width, 700), body, fontsize=12.0,
        align=align, **kwargs,
    )
    _save(doc, path, subset=embedded)


def shared_btet(path: str) -> None:
    """Two paragraphs in one BT/ET block (the structure that broke Phase 5.4)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    a = ("Alpha paragraph first line of the first block that keeps going. "
         "Alpha second line continues the same paragraph thought here.")
    b = ("Beta paragraph first line of the second block below the first. "
         "Beta second line finishes the second paragraph block now.")
    page.insert_textbox(fitz.Rect(60, 80, 500, 200), a, fontsize=12.0, fontname="helv")
    page.insert_textbox(fitz.Rect(60, 300, 500, 440), b, fontsize=12.0, fontname="helv")
    xrefs = _stream_xrefs(doc, page)
    first, second = doc.xref_stream(xrefs[0]), doc.xref_stream(xrefs[1])
    bt, et = second.rfind(b"BT"), second.rfind(b"ET")
    inner = second[bt + 2:et]
    et_a = first.rfind(b"ET")
    merged = first[:et_a] + b"\n" + inner + b"\n" + first[et_a:]
    doc.update_stream(xrefs[0], merged)
    page = doc[0]
    doc.xref_set_key(page.xref, "Contents", f"{xrefs[0]} 0 R")
    _save(doc, path)


def multi_stream(path: str) -> None:
    """Two paragraphs in two separate content streams."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_textbox(fitz.Rect(60, 80, 400, 260),
                        "First stream paragraph line one here and it keeps going "
                        "until it has to wrap onto a second row of its own. "
                        "First stream paragraph line two here.",
                        fontsize=12.0, fontname="helv")
    page.insert_textbox(fitz.Rect(60, 300, 400, 460),
                        "Second stream paragraph line one and it also wraps onto "
                        "another row because the column is narrow. "
                        "Second stream paragraph line two.",
                        fontsize=12.0, fontname="helv")
    _save(doc, path)


def form_xobject(path: str) -> None:
    """A paragraph drawn by a Form XObject, with page text around it."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((60, 60), "Page text before the form", fontsize=12.0, fontname="helv")
    font_xref = doc.get_page_fonts(0)[0][0]
    form_data = (b"BT\n/F1 12 Tf\n1 0 0 1 0 0 Tm\n"
                 b"(Text inside a form XObject) Tj\nET\n")
    form = doc.get_new_xref()
    doc.update_object(
        form,
        f"<< /Type /XObject /Subtype /Form /BBox [0 0 300 40] "
        f"/Resources << /Font << /F1 {font_xref} 0 R >> >> >>",
    )
    doc.update_stream(form, form_data)
    page = doc[0]
    page_xref = page.xref
    resources = doc.xref_get_key(page_xref, "Resources")
    if resources[0] == "xref":
        res_xref = int(resources[1].split()[0])
        xobjects = doc.xref_get_key(res_xref, "XObject")
        if xobjects[0] == "dict":
            body = xobjects[1].rstrip()
            merged = body[:-2] + f" /Fm0 {form} 0 R >>"
            doc.xref_set_key(res_xref, "XObject", merged)
        else:
            doc.xref_set_key(res_xref, "XObject", f"<< /Fm0 {form} 0 R >>")
    page = doc[0]
    content = doc.xref_stream(page.get_contents()[0])
    content += b"\nq 1 0 0 1 60 600 cm /Fm0 Do Q\n"
    new = doc.get_new_xref()
    doc.update_object(new, "<<>>")
    doc.update_stream(new, content)
    page = doc[0]
    doc.xref_set_key(page.xref, "Contents", f"{new} 0 R")
    _save(doc, path)


def tj_kerning(path: str) -> None:
    """A TJ array with kerning adjustments, and an untouched sibling line."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 700), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"BT\n/helv 12 Tf\n1 0 0 1 72 700 Tm\n"
        b"[(Kern) -30 (ing) -15 (is) -250 (fun)] TJ\nET\n"
        b"BT\n/helv 12 Tf\n1 0 0 1 72 680 Tm\n"
        b"(Second line stays exactly here) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def rotated(path: str) -> None:
    """A rotated paragraph and an upright sibling."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 700), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"q\n0 1 -1 0 200 120 cm\nBT\n/helv 12 Tf\n1 0 0 1 0 0 Tm\n"
        b"(Rotated line one here) Tj\n0 -16 Td\n(Rotated line two here) Tj\nET\nQ\n"
        b"BT\n/helv 12 Tf\n1 0 0 1 72 700 Tm\n(Upright sibling stays) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def mixed_styles(path: str) -> None:
    """Two fonts on one visual row, then a normal second row."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    bold = fitz.Font("hebo")
    page.insert_text((60, 120), "Bold lead-in ", fontsize=14.0, fontname="hebo")
    x = 60 + bold.text_length("Bold lead-in ", 14.0)
    page.insert_text((x, 120), "and regular text on the same row.", fontsize=10.0,
                     fontname="helv")
    page.insert_text((60, 100), "Second row in the same paragraph.", fontsize=10.0,
                     fontname="helv")
    _save(doc, path)


def embedded_subset(path: str) -> None:
    """A three-line paragraph in an embedded, subset Type0 font."""
    simple_paragraph(path, embedded=True)


def row_per_bt(path: str) -> None:
    """One paragraph whose rows each sit in their own BT/ET (LibreOffice style)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 760), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"BT\n/helv 12 Tf\n72 700 Td\n(First row of the paragraph that continues) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 682 Td\n(Second row of the paragraph that continues) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 640 Td\n(Other paragraph stays exactly here) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def list_items(path: str) -> None:
    """A bulleted list: each item is its own paragraph (PdfCraft #2564)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 760), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"BT\n/helv 12 Tf\n72 700 Td\n(\x95 First item) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 684 Td\n(\x95 Second item) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 668 Td\n(\x95 Third item) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 620 Td\n(1. Ordered item) Tj\nET\n"
        b"BT\n/helv 12 Tf\n72 604 Td\n(2. Another item) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def all_fixtures(directory: str | None = None) -> dict[str, str]:
    directory = directory or tempfile.mkdtemp(prefix="pdfcraft_poc_")
    os.makedirs(directory, exist_ok=True)
    builders = {
        "simple": simple_paragraph,
        "shared_btet": shared_btet,
        "multi_stream": multi_stream,
        "form_xobject": form_xobject,
        "tj_kerning": tj_kerning,
        "rotated": rotated,
        "mixed_styles": mixed_styles,
        "embedded_subset": embedded_subset,
        "row_per_bt": row_per_bt,
        "list_items": list_items,
    }
    out = {}
    for name, builder in builders.items():
        path = os.path.join(directory, f"{name}.pdf")
        builder(path)
        out[name] = path
    return out
