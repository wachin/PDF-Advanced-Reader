"""PDFs with the content-stream structures the PdfCraft port must read.

These are deliberately *structural* fixtures: each one puts text in a shape
that a real producer writes (one BT/ET per paragraph, one per row, several
content streams, Form XObjects, turned text, mixed runs, a subset font), so the
adapter's grouping can be tested without a large real-world document.

They are built here rather than imported from ``poc/`` so the application's
test suite does not depend on the exploratory prototype directory.
"""

from __future__ import annotations

import glob

import pymupdf as fitz

SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the sun sets slowly "
    "behind the distant hills and the river keeps flowing through the meadow. "
)


def embedded_font_path():
    """A TrueType file to embed, or ``None`` when the system has none."""
    hits = glob.glob("/usr/share/fonts/**/DejaVuSans.ttf", recursive=True)
    return hits[0] if hits else None


def _save(doc: fitz.Document, path, subset: bool = False) -> None:
    if subset:
        doc.subset_fonts()
    doc.save(path, garbage=2, deflate=True)
    doc.close()


def simple_paragraph(path, text: str | None = None, *, embedded: bool = False,
                     align: int = 0, width: float = 400.0) -> None:
    """One paragraph of four rows inside a single BT/ET."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    body = text or (SENTENCE * 2)
    if embedded:
        page.insert_textbox(
            fitz.Rect(60, 80, 60 + width, 700), body, fontsize=12.0,
            align=align, fontname="F0", fontfile=embedded_font_path())
    else:
        page.insert_textbox(
            fitz.Rect(60, 80, 60 + width, 700), body, fontsize=12.0,
            align=align, fontname="helv")
    _save(doc, path, subset=embedded)


def embedded_subset(path) -> None:
    """A four-row paragraph in an embedded, subset Type0 font."""
    simple_paragraph(path, embedded=True)


def shared_btet(path) -> None:
    """Two paragraphs merged into one BT/ET (the Phase 5.4 failure shape)."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    a = ("Alpha paragraph first line of the first block that keeps going. "
         "Alpha second line continues the same paragraph thought here.")
    b = ("Beta paragraph first line of the second block below the first. "
         "Beta second line finishes the second paragraph block now.")
    page.insert_textbox(fitz.Rect(60, 80, 500, 200), a, fontsize=12.0, fontname="helv")
    page.insert_textbox(fitz.Rect(60, 300, 500, 440), b, fontsize=12.0, fontname="helv")
    xrefs = list(page.get_contents())
    first, second = doc.xref_stream(xrefs[0]), doc.xref_stream(xrefs[1])
    bt, et = second.rfind(b"BT"), second.rfind(b"ET")
    inner = second[bt + 2:et]
    et_a = first.rfind(b"ET")
    merged = first[:et_a] + b"\n" + inner + b"\n" + first[et_a:]
    doc.update_stream(xrefs[0], merged)
    page = doc[0]
    doc.xref_set_key(page.xref, "Contents", f"{xrefs[0]} 0 R")
    _save(doc, path)


def multi_stream(path) -> None:
    """Two three-row paragraphs, each in its own content stream."""
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


def two_column(path) -> None:
    """Two three-row paragraphs side by side, separated by a wide gutter."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    left = ("Left column first row continues across the narrow measure here. "
            "Left column second row carries the sentence on a little further. "
            "Left column third row closes the paragraph in this column.")
    right = ("Right column first row begins after the gutter between them. "
             "Right column second row continues the second paragraph text. "
             "Right column third row ends the column's paragraph here.")
    page.insert_textbox(fitz.Rect(60, 80, 280, 400), left, fontsize=12.0,
                        fontname="helv")
    page.insert_textbox(fitz.Rect(320, 80, 540, 400), right, fontsize=12.0,
                        fontname="helv")
    _save(doc, path)


def form_xobject(path) -> None:
    """Page text plus a paragraph drawn by a Form XObject."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((60, 60), "Page text before the form", fontsize=12.0,
                     fontname="helv")
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
            doc.xref_set_key(res_xref, "XObject",
                             body[:-2] + f" /Fm0 {form} 0 R >>")
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


def rotated(path) -> None:
    """A two-row paragraph turned 90 degrees, plus an upright sibling."""
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


def mixed_styles(path) -> None:
    """One row drawn by a bold 14 pt and a regular 10 pt run, then a plain row."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    bold = fitz.Font("hebo")
    page.insert_text((60, 120), "Bold lead-in ", fontsize=14.0, fontname="hebo")
    x = 60 + bold.text_length("Bold lead-in ", 14.0)
    page.insert_text((x, 120), "and regular text on the same row.",
                     fontsize=10.0, fontname="helv")
    page.insert_text((60, 100), "Second row in the same paragraph.",
                     fontsize=10.0, fontname="helv")
    _save(doc, path)


def row_per_bt(path) -> None:
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


def list_items(path) -> None:
    """A bulleted and a numbered list: every item is its own paragraph."""
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


def undecodable_text(path) -> None:
    """A two-row paragraph whose first row holds a code with no Unicode mapping."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((50, 20), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"BT\n/helv 12 Tf\n50 150 Td\n(Hello \001 world) Tj\n0 -18 Td\n"
        b"(Second row intact) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def quote_and_surplus(path) -> None:
    """A row drawn with ``"`` and a row drawn by a TJ with a surplus array."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 760), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b'BT\n/helv 12 Tf\n72 700 Td\n0 0 (Quoted operator row) "\nET\n'
        b"BT\n/helv 12 Tf\n72 660 Td\n[(Ignored) ] [(array row)] TJ\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def missing_font(path) -> None:
    """One readable row and one row whose font resource does not exist."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=800)
    page.insert_text((72, 760), "seed", fontsize=12.0, fontname="helv")
    stream = (
        b"BT\n/helv 12 Tf\n72 700 Td\n(Readable row) Tj\nET\n"
        b"BT\n/NoSuchFont 12 Tf\n72 660 Td\n(This text has no font metrics) Tj\nET\n"
    )
    page = doc[0]
    doc.update_stream(page.get_contents()[0], stream)
    _save(doc, path)


def _new_stream(doc, data: bytes) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, "<<>>")
    doc.update_stream(xref, data)
    return xref


def vertical_text(path) -> None:
    """A row drawn with a Type0 font whose CMap declares ``/WMode 1``."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((50, 380), "seed", fontsize=12.0, fontname="helv")

    cid = doc.get_new_xref()
    doc.update_object(
        cid,
        "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /Test "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) "
        "/Supplement 0 >> /DW 1000 /W [1 [500]] >>",
    )
    encoding = _new_stream(
        doc,
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CMapName /Test-V def /CMapType 1 def /WMode 1 def "
        b"1 begincodespacerange <0000> <ffff> endcodespacerange "
        b"endcmap end end",
    )
    tounicode = _new_stream(
        doc,
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
        b"/CMapName /Test-UCS2 def /CMapType 2 def "
        b"1 begincodespacerange <0000> <ffff> endcodespacerange "
        b"1 beginbfchar <0001> <0056> endbfchar endcmap end end",
    )
    font = doc.get_new_xref()
    doc.update_object(
        font,
        f"<< /Type /Font /Subtype /Type0 /BaseFont /Test "
        f"/Encoding {encoding} 0 R /DescendantFonts [{cid} 0 R] "
        f"/ToUnicode {tounicode} 0 R >>",
    )
    page = doc[0]
    resources = doc.xref_get_key(page.xref, "Resources")
    if resources[0] == "xref":
        res_xref = int(resources[1].split()[0])
        doc.xref_set_key(res_xref, "Font/V0", f"{font} 0 R")
    page = doc[0]
    doc.update_stream(page.get_contents()[0],
                      b"BT /V0 12 Tf 1 0 0 1 100 300 Tm <0001> Tj ET\n")
    _save(doc, path)


def empty_page(path) -> None:
    """A page with no text at all."""
    doc = fitz.open()
    doc.new_page(width=600, height=800)
    _save(doc, path)
