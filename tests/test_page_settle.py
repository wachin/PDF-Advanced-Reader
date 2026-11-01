"""F10-4o: every page states for itself before anything is reordered.

A ``/Page`` object does not have to say how big it is or how far it is turned:
ISO 32000 makes ``/MediaBox``, ``/CropBox``, ``/Rotate`` and ``/Resources``
**inheritable**, so a producer that wants 540 sheets to share one box writes it
once on the ``/Pages`` node above them.  MuPDF walks that chain for every page it
renders, which is why such a document looks ordinary right up to the moment
something moves a page: the reference a page hangs from is *where it gets its
geometry*, so a reorder that lands it under a different ancestor hands it that
ancestor's box and turn instead.

That is measured, not assumed.  On the fixture below — three sheets, two
intermediate ``/Pages`` nodes, one of them turned and one of them cropped —

    move the cropped page to the front:  260x360 → 400x300, its crop gone
    move the turned page to the back:    400x300 → 260x360, its turn gone

and no command raised, no page count changed and no text moved.  Every one of
the page numbers PDFAR has spent four increments making honest (F10-4g, 4h, 4l,
4n) would still be honest while the sheet in front of the reader quietly changed
shape under them.

So the tree is settled *before* a reorder runs, and this is what the step does:

* **Push down.**  Each page is given, in its own dictionary, the ``/MediaBox``,
  ``/CropBox`` and ``/Rotate`` it was resolving, nearest node first — so page 1 keeps
  the ``/Rotate 180`` it stated while the page that stated nothing receives the 90 it
  was being shown.  "Was resolving" is read up the page's own ``/Parent`` links,
  which is the walk MuPDF takes: ``adrift_pdf`` is a book whose ``/Kids`` were
  rewritten without touching ``/Parent``, and the tree there states a box and a turn
  the pages were never drawn with.
* **Leave the tree where the producer put it.**  The obvious way to ask MuPDF for
  a push-down is ``Document.select``, and it was measured and rejected: ``select``
  builds a sub-PDF, and the catalog that comes back no longer names
  ``/Metadata``, ``/PageLabels``, ``/Lang``, ``/AcroForm``, ``/OpenAction``,
  ``/Info``, ``/ViewerPreferences`` or ``/Threads`` — and on two of the four
  licensed papers it had no ``/Outlines`` either.  Once a page states its own box
  and turn, the node it hangs from says nothing about how it draws, so the
  rebuild buys nothing and costs the document.
* **Refuse rather than drop.**  An ancestor holding a ``/Group`` (a subtree's
  transparency) or a ``/Resources`` (inherited dictionary by dictionary, which a
  page cannot be handed a copy of) is refused by name, because moving those pages
  changes how they look and no per-page value says so.  So is a tree whose pages
  cannot be counted, a ``/Kids`` entry that is not a page, and a chain — down
  through ``/Kids`` or up through ``/Parent`` — that comes back around.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session

from pdfar.edits import EditError, Scope
from pdfar.pages import SettlePageTree, RotatePages
from pdfar.save import save_document

#: The sheet every fixture is built on: portrait, so an inherited turn changes it.
SHEET = (300.0, 400.0)

#: What the second ancestor crops a page down to — a box no page states itself.
INSET = "[20 20 280 380]"


# --------------------------------------------------------------------- fixtures on disk
def pages_root(doc):
    return int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])


def kids_of(doc, xref):
    return [int(found) for found in
            re.findall(r"(\d+)\s+0\s+R", doc.xref_get_key(xref, "Kids")[1])]


def own(doc, index, key):
    """What the page's *own* object states; ``'null'`` covers absent and empty."""
    return doc.xref_get_key(doc.load_page(index).xref, key)[1]


def own_numbers(doc, index, key):
    """A box the page states itself, as numbers — or ``None`` if it does not state one."""
    value = own(doc, index, key)
    if value == "null":
        return None
    return tuple(round(float(found), 3) for found in re.findall(r"-?[\d.]+", value))


def tree_types(doc):
    """The kind of every node hanging off the root — ``/Page`` means one level."""
    return [doc.xref_get_key(kid, "Type")[1] for kid in kids_of(doc, pages_root(doc))]


def digest(doc, index):
    return hashlib.sha256(
        doc.load_page(index).get_pixmap(dpi=72, annots=False).samples).hexdigest()


def digests(doc):
    return [digest(doc, index) for index in range(doc.page_count)]


def shape(doc):
    """What each sheet looks like to a reader — width, height, turn — as one tuple."""
    return [(round(doc.load_page(index).rect.width),
             round(doc.load_page(index).rect.height),
             doc.load_page(index).rotation) for index in range(doc.page_count)]


def nested_pdf(path, pages=3):
    """A three-sheet book whose geometry lives on two intermediate nodes.

    ``mid1`` supplies the box and turns every page below it; ``mid2`` supplies the
    same box with a crop.  Each page is then stripped of those three keys, so the
    *only* way any of it renders correctly is by inheritance — and page 1 keeps a
    ``/Rotate 180`` of its own to prove the nearest value, not the furthest, is
    what gets copied down.  What each page holds for itself, including its
    ``/Resources``, is left in place: an ancestor holding a *dictionary* is a
    different problem, and a separate fixture covers it.
    """
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=SHEET[0], height=SHEET[1])
        page.insert_text((20, 200), f"SHEET {index} CONTENT", fontsize=18)
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    root = pages_root(doc)
    sheets = kids_of(doc, root)
    box = f"[0 0 {SHEET[0]:.0f} {SHEET[1]:.0f}]"
    mid1 = doc.get_new_xref()
    mid2 = doc.get_new_xref()
    doc.update_object(mid1,
                      f"<< /Type /Pages /Parent {root} 0 R /Count 2 "
                      f"/MediaBox {box} /Rotate 90 "
                      f"/Kids [{sheets[0]} 0 R {sheets[1]} 0 R] >>")
    doc.update_object(mid2,
                      f"<< /Type /Pages /Parent {root} 0 R /Count 1 "
                      f"/MediaBox {box} /CropBox {INSET} "
                      f"/Kids [{sheets[2]} 0 R] >>")
    doc.update_object(root,
                      f"<< /Type /Pages /Count {pages} "
                      f"/Kids [{mid1} 0 R {mid2} 0 R] >>")
    for xref, parent in ((sheets[0], mid1), (sheets[1], mid1), (sheets[2], mid2)):
        doc.xref_set_key(xref, "Parent", f"{parent} 0 R")
        for key in ("MediaBox", "CropBox", "Rotate"):
            # An explicit null is what a key that is absent reads as: MuPDF and
            # the standard both treat it as "not stated here, ask above".
            doc.xref_set_key(xref, key, "null")
    doc.xref_set_key(sheets[1], "Rotate", "180")
    staged = str(path) + ".staged"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, str(path))
    return str(path)


def shared_resources_pdf(path):
    """A book whose first ancestor holds the ``/Resources`` its pages draw with.

    This is the shape the step cannot settle: ``/Resources`` inherits *per
    sub-dictionary*, so a page under two ancestors that each declare one resolves
    names from both, and no single dictionary written on the page says the same
    thing.  The fixture is built so that the pages of ``mid1`` share one resource
    dictionary between them — which is also why the copy cannot go through the
    page's own object: another page is reading it.
    """
    doc = fitz.open()
    for index in range(2):
        page = doc.new_page(width=SHEET[0], height=SHEET[1])
        page.insert_text((20, 200), f"SHARED {index} CONTENT", fontsize=18)
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    root = pages_root(doc)
    sheets = kids_of(doc, root)
    shared = doc.xref_get_key(sheets[0], "Resources")[1]
    box = f"[0 0 {SHEET[0]:.0f} {SHEET[1]:.0f}]"
    mid1 = doc.get_new_xref()
    doc.update_object(mid1,
                      f"<< /Type /Pages /Parent {root} 0 R /Count 2 "
                      f"/MediaBox {box} /Resources {shared} "
                      f"/Kids [{sheets[0]} 0 R {sheets[1]} 0 R] >>")
    doc.update_object(root,
                      f"<< /Type /Pages /Count 2 /Kids [{mid1} 0 R] >>")
    for xref in sheets:
        doc.xref_set_key(xref, "Parent", f"{mid1} 0 R")
        doc.xref_set_key(xref, "Resources", "null")
    staged = str(path) + ".shared"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, str(path))
    return str(path)


def adrift_pdf(path, pages=2):
    """A book whose ``/Kids`` were rewritten and whose ``/Parent`` links were not.

    The two walks through a page tree — down from the root through ``/Kids``, up
    from a sheet through ``/Parent`` — agree only while somebody kept them
    consistent.  Here ``mid1`` states a box and a turn that the pages' own ``/Parent``
    (the root) does not, so the reader sees what ``/Parent`` says and the tree is a
    lie.  This is the shape a hand edit or a careless producer leaves behind, and it
    is the reason the step resolves inheritance the way the renderer does.
    """
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 400), f"SHEET {index} CONTENT", fontsize=18)
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    root = pages_root(doc)
    sheets = kids_of(doc, root)
    mid1 = doc.get_new_xref()
    doc.update_object(mid1,
                      f"<< /Type /Pages /Parent {root} 0 R /Count {pages} "
                      f"/MediaBox [0 0 595 842] /Rotate 90 "
                      f"/Kids [{' '.join(f'{s} 0 R' for s in sheets)}] >>")
    doc.update_object(root,
                      f"<< /Type /Pages /Count {pages} /Kids [{mid1} 0 R] >>")
    for xref in sheets:
        for key in ("MediaBox", "CropBox", "Rotate"):
            doc.xref_set_key(xref, key, "null")
        # Deliberately no /Parent rewrite: the sheets still name the root.
    staged = str(path) + ".adrift"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, str(path))
    return str(path)


def grouped_pdf(path):
    """The nested book with a transparency ``/Group`` written on its first ancestor.

    Written into the file rather than set on a live document, because the point of
    it is a session that opens a document this step has to refuse.
    """
    doc = fitz.open(path)
    mid1 = kids_of(doc, pages_root(doc))[0]
    doc.xref_set_key(mid1, "Group", "<< /S /Transparency /CS /DeviceRGB >>")
    staged = path + ".group"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)
    return path


def marked_pdf(path):
    """The nested book with a mark, a link, an outline and properties on it."""
    doc = fitz.open(path)
    for index in range(doc.page_count):
        page = doc.load_page(index)
        annot = page.add_highlight_annot(fitz.Rect(20, 190, 150, 212))
        annot.set_info(title="A Reader", content=f"note {index}")
        annot.update()
    doc.load_page(0).insert_link(
        {"kind": fitz.LINK_GOTO, "from": fitz.Rect(20, 220, 150, 290),
         "page": 2, "to": fitz.Point(20, 200)})
    doc.set_toc([[1, "Opening", 1], [2, "Opening detail", 1], [1, "Last", 3]])
    doc.set_metadata({"title": "Inherit me", "author": "Probe"})
    staged = path + ".marks"
    doc.save(staged)
    doc.close()
    os.replace(staged, path)
    return path


def catalogued_pdf(path):
    """A nested book whose *catalog* is loaded: a form, an outline, properties.

    Everything ``Document.select`` was measured dropping is written here, so a test
    can ask whether the catalog the reader opened is the catalog the reader saved.
    ``/PageLabels`` and ``/ViewerPreferences`` are stamped straight onto the catalog
    because that is where a producer puts them; ``/Info`` and ``/Outlines`` go
    through the document's own setters.
    """
    doc = fitz.open(path)
    page = doc.load_page(0)
    field = fitz.Widget()
    field.page = page
    field.rect = fitz.Rect(20, 300, 150, 330)
    field.field_name = "fullname"
    field.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    field.text = "typed"
    page.add_widget(field)
    doc.set_toc([[1, "Opening", 1], [2, "Opening detail", 1], [1, "Last", 3]])
    doc.set_metadata({"title": "Inherit me", "author": "Probe"})
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, "Lang", "(en-GB)")
    doc.xref_set_key(catalog, "PageMode", "/UseOutlines")
    doc.xref_set_key(catalog, "PageLabels", "<< /Nums [0 << /S /D /St 101 >>] >>")
    doc.xref_set_key(catalog, "ViewerPreferences",
                     "<< /Dir /L /DisplayDocTitle true >>")
    staged = path + ".catalogued"
    doc.save(staged, deflate=False)
    doc.close()
    os.replace(staged, path)
    return path


def ancestor_font_pdf(path):
    """A page that declares one font and an ancestor that declares a second.

    ``/Resources`` inherits *per resource dictionary*, not as a whole: measured
    with a page ``/Font << /helv >>`` under an ancestor ``/Font << /TIM >>``, a
    content stream naming both draws both.  So a push-down that stamps the
    nearest ``/Resources`` on the page keeps only the page's own entries, and the
    question this fixture answers is whether anything was lost.
    """
    doc = fitz.open()
    page = doc.new_page(width=320, height=200)
    page.insert_text((12, 60), "seed", fontsize=12)
    doc.save(str(path))
    doc.close()

    doc = fitz.open(str(path))
    root = pages_root(doc)
    kid = kids_of(doc, root)[0]
    own_res = int(re.findall(r"(\d+)\s+0\s+R",
                             doc.xref_get_key(kid, "Resources")[1])[0])
    times = doc.get_new_xref()
    doc.update_object(times, "<< /Type /Font /Subtype /Type1 /BaseFont /Times-Bold >>")
    ancestor_res = doc.get_new_xref()
    doc.update_object(ancestor_res, f"<< /Font << /TIM {times} 0 R >> >>")
    stream = int(re.findall(r"(\d+)\s+0\s+R",
                            doc.xref_get_key(kid, "Contents")[1])[0])
    doc.update_stream(stream,
                      b"BT /helv 12 Tf 12 150 Td (OWN FONT helv) Tj ET\n"
                      b"BT /TIM 12 Tf 12 110 Td (ANCESTOR FONT TIM) Tj ET\n")
    doc.xref_set_key(root, "Resources", f"{ancestor_res} 0 R")
    doc.xref_set_key(kid, "Resources", f"{own_res} 0 R")
    staged = str(path) + ".staged"
    doc.save(staged)
    doc.close()
    os.replace(staged, str(path))
    return str(path)


@pytest.fixture
def nested(tmp_path):
    return nested_pdf(tmp_path / "nested.pdf")


# --------------------------------------------------------------------- the harm, measured
def test_a_page_moved_under_the_other_ancestor_adopts_its_geometry(nested):
    """The reason the reorder has to wait for this step: nothing moves, and yet.

    ``move_page`` splices the reference into the *destination's* ``/Kids`` and
    reparents the page.  MuPDF then resolves the box, the turn and the crop from
    the new ancestors, so the sheet arrives reshaped with no command to explain
    it.  Pinned here as a measurement of what PDFAR must not ship.
    """
    doc = fitz.open(nested)
    try:
        assert shape(doc) == [(400, 300, 90), (300, 400, 180), (260, 360, 0)]
        doc.move_page(2, 0)
        assert shape(doc) != [(400, 300, 90), (300, 400, 180), (260, 360, 0)]
        assert shape(doc)[0] == (400, 300, 90), (
            "the cropped page came to the front and inherited the turn instead")
    finally:
        doc.close()


def test_the_words_and_the_page_count_survive_a_move_even_so(nested):
    """What makes the harm above quiet: the text is intact, only the sheet changed."""
    doc = fitz.open(nested)
    try:
        words = [doc.load_page(i).get_text().split() for i in range(3)]
        doc.move_page(2, 0)
        assert doc.page_count == 3
        assert [doc.load_page(i).get_text().split() for i in range(3)] == [
            words[2], words[0], words[1]]
    finally:
        doc.close()


# ------------------------------------------------------------------------- what it does
def test_no_page_renders_differently(nested):
    """The whole contract in one assertion: a settle that changes nothing seen."""
    doc = fitz.open(nested)
    try:
        before = digests(doc)
        SettlePageTree().apply(doc)
        assert digests(doc) == before
    finally:
        doc.close()


def test_every_page_states_what_its_own_ancestors_used_to_state(nested):
    """The pages that inherited a turn state one; the cropped page states its crop.

    Asserted per sheet rather than for all four keys on all of them, because that
    is what the fixture actually inherits: ``mid1`` supplies the box and the turn
    to the first two, ``mid2`` the box and the crop to the last.  A page is only
    asked to state what it was being *given*.
    """
    doc = fitz.open(nested)
    try:
        SettlePageTree().apply(doc)
        for index in range(3):
            assert own(doc, index, "MediaBox") != "null"
            assert own(doc, index, "Resources") != "null"
        assert [own(doc, index, "Rotate") for index in (0, 1)] == ["90", "180"]
        assert own(doc, 2, "CropBox") != "null"
    finally:
        doc.close()


def test_the_tree_is_left_where_the_producer_put_it(nested):
    """No node is removed and no page is re-hung: only the pages' dictionaries grow.

    The flatten was measured and dropped — ``Document.select`` is the library's way
    of asking for it, and what it costs is the catalog (see
    :func:`test_the_document_keeps_everything_beside_the_page_tree`).  With every
    page stating its own box and turn, the node a page hangs from no longer says
    anything about how it draws, so flattening would be a cost with no benefit.
    """
    doc = fitz.open(nested)
    try:
        under_root = kids_of(doc, pages_root(doc))
        assert set(tree_types(doc)) == {"/Pages"}
        SettlePageTree().apply(doc)
        assert kids_of(doc, pages_root(doc)) == under_root
        assert set(tree_types(doc)) == {"/Pages"}
        assert [doc.xref_get_key(doc.load_page(i).xref, "Parent")[1]
                for i in range(3)] == [f"{under_root[0]} 0 R", f"{under_root[0]} 0 R",
                                       f"{under_root[1]} 0 R"]
    finally:
        doc.close()


def test_a_page_the_two_walks_disagree_about_keeps_what_it_renders(tmp_path):
    """``/Parent`` is the walk the renderer takes, so it is the walk this step takes.

    ``adrift_pdf`` lists its sheets under a node stating a box and a quarter turn
    while their own ``/Parent`` names a root that states neither, and MuPDF draws
    them 612×792 and upright — measured on a 540-sheet version of the same shape,
    where every page drew that way.  Copying what the *tree* says would have handed
    those pages a geometry they never had, a settle that itself needed settling.
    Asking the page writes the one value it really does resolve to — the default
    turn — and invents no box.
    """
    path = adrift_pdf(tmp_path / "adrift.pdf")
    doc = fitz.open(path)
    try:
        before, geo = digests(doc), shape(doc)
        assert geo == [(612, 792, 0), (612, 792, 0)], (
            "the fixture is only worth having if /Parent really does outrank /Kids")
        SettlePageTree().apply(doc)
        assert shape(doc) == geo
        assert digests(doc) == before
        assert [own(doc, index, "Rotate") for index in range(2)] == ["0", "0"]
        assert [own(doc, index, "MediaBox") for index in range(2)] == ["null", "null"], (
            "a box the page never resolved is a claim about the document not in it")
    finally:
        doc.close()


def test_the_document_keeps_everything_beside_the_page_tree(tmp_path):
    """What a rebuild would have cost, and the reason there is not one.

    ``Document.select`` — the library's own push-down — was measured against this
    shape and took the catalog apart: numbering, properties, the outline, the form
    and the viewer's own preferences are exactly what a sub-PDF build leaves behind,
    and the loss survives a save.  So the check is not "did the pages settle" but
    "is this still the same document", made on the keys the catalog names and on the
    values a reader would notice — before the settle, after it, and in the file the
    settle is saved to.
    """
    path = catalogued_pdf(nested_pdf(tmp_path / "catalogued.pdf"))
    doc = fitz.open(path)
    try:
        catalog = doc.pdf_catalog()
        held = sorted(doc.xref_get_keys(catalog))
        assert {"Lang", "PageLabels", "PageMode", "ViewerPreferences",
                "Outlines", "AcroForm", "Info"} <= set(held)
        SettlePageTree().apply(doc)
        assert sorted(doc.xref_get_keys(catalog)) == held
        assert doc.get_page_labels()[0]["firstpagenum"] == 101
        assert doc.metadata["title"] == "Inherit me"
        assert [f.field_name for f in doc.load_page(0).widgets()] == ["fullname"]
        out = str(tmp_path / "saved.pdf")
        doc.save(out, deflate=False)
    finally:
        doc.close()

    again = fitz.open(out)
    try:
        assert sorted(again.xref_get_keys(again.pdf_catalog())) == held, (
            "the settle rewrote the document around the page tree")
        assert again.get_page_labels()[0]["firstpagenum"] == 101
        assert again.get_toc() == [[1, "Opening", 1], [2, "Opening detail", 1],
                                   [1, "Last", 3]]
        assert [f.field_name for f in again.load_page(0).widgets()] == ["fullname"]
    finally:
        again.close()


def test_the_crop_a_page_inherited_is_now_the_page_s_own(nested):
    """The cropped sheet keeps its crop — which is the box it is drawn with."""
    doc = fitz.open(nested)
    try:
        rect = tuple(round(value) for value in doc.load_page(2).rect)
        assert rect == (0.0, 0.0, 260.0, 360.0)
        SettlePageTree().apply(doc)
        assert own_numbers(doc, 2, "CropBox") == (20.0, 20.0, 280.0, 380.0)
        assert tuple(round(v) for v in doc.load_page(2).rect) == rect
    finally:
        doc.close()


def test_the_turn_a_page_stated_itself_beats_the_ancestor_s(nested):
    """Nearest wins: the 180 the page held, not the 90 the node above it says."""
    doc = fitz.open(nested)
    try:
        assert [doc.load_page(i).rotation for i in range(3)] == [90, 180, 0]
        SettlePageTree().apply(doc)
        assert own(doc, 1, "Rotate") == "180"
        assert own(doc, 0, "Rotate") == "90"
        assert [doc.load_page(i).rotation for i in range(3)] == [90, 180, 0]
    finally:
        doc.close()


def test_the_words_stay_on_their_own_sheets(nested):
    doc = fitz.open(nested)
    try:
        words = [doc.load_page(i).get_text().strip() for i in range(3)]
        SettlePageTree().apply(doc)
        assert [doc.load_page(i).get_text().strip() for i in range(3)] == words
    finally:
        doc.close()


def test_a_move_after_the_settle_cannot_reshape_a_page(nested):
    """The property the reorder is waiting for, asserted by running the reorder."""
    doc = fitz.open(nested)
    try:
        before = shape(doc)
        SettlePageTree().apply(doc)
        assert shape(doc) == before
        doc.move_page(2, 0)
        assert [shape(doc)[i] for i in (1, 2, 0)] == before, (
            "the sheets moved but not the geometry they were drawn with")
    finally:
        doc.close()


def test_a_tree_three_deep_is_settled_too(tmp_path):
    """A second generation of ancestors: the push-down walks the whole chain."""
    path = nested_pdf(tmp_path / "deep.pdf")
    doc = fitz.open(path)
    try:
        root = pages_root(doc)
        mid1 = kids_of(doc, root)[0]
        sheets = kids_of(doc, mid1)
        lower = doc.get_new_xref()
        doc.update_object(lower,
                          f"<< /Type /Pages /Parent {mid1} 0 R /Count 1 "
                          f"/Rotate 270 /Kids [{sheets[0]} 0 R] >>")
        doc.xref_set_key(sheets[0], "Parent", f"{lower} 0 R")
        doc.xref_set_key(sheets[0], "Rotate", "null")
        doc.xref_set_key(mid1, "Kids",
                         f"[{lower} 0 R {sheets[1]} 0 R]")
        assert doc.load_page(0).rotation == 270
        before = digests(doc)
        SettlePageTree().apply(doc)
        assert own(doc, 0, "Rotate") == "270", (
            "the nearer ancestor's turn is what the page is shown, not the further one")
        assert own(doc, 1, "Rotate") == "180", "a page's own value is never overwritten"
        assert own(doc, 2, "Rotate") == "0", (
            "mid1 states a turn this sheet resolves through mid2 as the default, so the "
            "default is what it must be given — otherwise moving it under mid1 turns it")
        assert set(tree_types(doc)) == {"/Pages"}
        assert digests(doc) == before
    finally:
        doc.close()


def test_a_sheet_the_tree_lists_twice_stays_listed_twice(tmp_path):
    """A duplicated page is one object in two slots, and the settle keeps both.

    ``copy_page`` does not copy anything: it puts the *same* object in the list
    twice, so four pages hang off three objects and the walk's count still agrees
    with the document's.  Folding the two into one would drop a sheet the reader
    can see and turn, so the walk reports a repeated page rather than refusing it.
    """
    path = nested_pdf(tmp_path / "twice.pdf")
    doc = fitz.open(path)
    try:
        doc.copy_page(0, 1)
        assert doc.page_count == 4
        before, geometry = digests(doc), shape(doc)
        SettlePageTree().apply(doc)
        assert doc.page_count == 4
        assert digests(doc) == before
        assert shape(doc) == geometry
        # Both slots are the same object, so both now state the same geometry.
        assert own(doc, 0, "Rotate") == own(doc, 1, "Rotate") == "90"
        assert doc.xref_get_key(doc.load_page(0).xref, "Parent")[1] == \
            doc.xref_get_key(doc.load_page(1).xref, "Parent")[1]
    finally:
        doc.close()


def test_a_turn_and_a_settle_compose_in_either_order(tmp_path):
    """Settling first cannot change what turning a page means.

    A turn reads ``page.rotation`` — inheritance included — and writes the sum on
    the page, so it lands on the same angle whether or not the ancestors have
    already been copied down.  That is what lets a reorder call this step first
    without asking the user's edit to move.
    """
    def run(order):
        path = nested_pdf(tmp_path / f"compose-{order[0]}{order[1]}.pdf")
        doc = fitz.open(path)
        try:
            for kind in order:
                (SettlePageTree() if kind == "F"
                 else RotatePages((0, 2), 90)).apply(doc)
            return shape(doc), digests(doc)
        finally:
            doc.close()

    turned_then_settled = run("RF")
    settled_then_turned = run("FR")
    assert turned_then_settled == settled_then_turned
    # And it is the answer a turn on an ordinary document gives: the two sheets
    # asked for end at 180 and 90, the one nobody touched keeps its own 180.
    assert settled_then_turned[0] == [(300, 400, 180), (300, 400, 180),
                                      (360, 260, 90)]


# ------------------------------------------------------------------- what must come along
def test_the_marks_survive_the_settle(tmp_path):
    path = marked_pdf(nested_pdf(tmp_path / "marked.pdf"))
    doc = fitz.open(path)
    try:
        notes = [[annot.info["content"] for annot in doc.load_page(i).annots()]
                 for i in range(doc.page_count)]
        assert all(notes)
        SettlePageTree().apply(doc)
        assert [[annot.info["content"] for annot in doc.load_page(i).annots()]
                for i in range(doc.page_count)] == notes
    finally:
        doc.close()


def test_the_links_still_point_where_they_did(tmp_path):
    """A ``/GoTo`` names a page object, and nothing here moves an object."""
    path = marked_pdf(nested_pdf(tmp_path / "linked.pdf"))
    doc = fitz.open(path)
    try:
        targets = [link["page"] for link in doc.load_page(0).get_links()]
        assert targets == [2]
        SettlePageTree().apply(doc)
        assert [link["page"] for link in doc.load_page(0).get_links()] == targets
    finally:
        doc.close()


def test_the_outline_and_the_properties_survive(tmp_path):
    path = marked_pdf(nested_pdf(tmp_path / "titled.pdf"))
    doc = fitz.open(path)
    try:
        toc, title = doc.get_toc(), doc.metadata["title"]
        assert title == "Inherit me"
        SettlePageTree().apply(doc)
        assert doc.get_toc() == toc
        assert doc.metadata["title"] == title
    finally:
        doc.close()


def test_a_font_only_the_ancestor_declares_is_not_lost(tmp_path):
    """``/Resources`` is inherited per resource dictionary, so a nearest copy is not enough.

    Stamping the nearest ``/Resources`` on the page would keep only the page's own
    ``/Font`` entries and drop the ancestor's ``/TIM`` — which is why this step
    copies no resource dictionary at all, and leaves the *root's* alone: the tree
    is untouched, so every page still reaches the node that declares the font.
    What this pins is that the line drawn with the ancestor's font still renders,
    and that the root still holds it.
    """
    path = ancestor_font_pdf(tmp_path / "fonts.pdf")
    doc = fitz.open(path)
    try:
        before = doc.load_page(0).get_text()
        assert "ANCESTOR FONT TIM" in before
        SettlePageTree().apply(doc)
        assert "ANCESTOR FONT TIM" in doc.load_page(0).get_text()
        assert doc.xref_get_key(pages_root(doc), "Resources")[1] != "null"
    finally:
        doc.close()


# ---------------------------------------------------------------------------------- refusals
def test_an_ancestor_holding_a_group_is_refused_by_name(nested):
    """A subtree's transparency is a property of the *group*, not of a page."""
    doc = fitz.open(nested)
    try:
        mid1 = kids_of(doc, pages_root(doc))[0]
        doc.xref_set_key(mid1, "Group", "<< /S /Transparency /CS /DeviceRGB >>")
        before, shapes = digests(doc), shape(doc)
        with pytest.raises(EditError, match="Group"):
            SettlePageTree().apply(doc)
        assert digests(doc) == before
        assert shape(doc) == shapes
    finally:
        doc.close()


def test_an_ancestor_holding_resources_is_refused_by_name(tmp_path):
    """A resource dictionary is not a value: two pages can be reading one object.

    ``mid1`` holds the ``/Resources`` both its pages draw with.  Copying that
    reference down would leave two pages naming one dictionary that a later edit
    could grow for one of them, and merging it into each page's own would write an
    object the producer never had.  Neither is a decision this step gets to make
    silently, so it refuses — and the refusal is total, with no page left
    half-settled by a push-down that never finished.
    """
    path = shared_resources_pdf(tmp_path / "shared.pdf")
    doc = fitz.open(path)
    try:
        before, shapes = digests(doc), shape(doc)
        with pytest.raises(EditError, match="Resources"):
            SettlePageTree().apply(doc)
        assert digests(doc) == before
        assert shape(doc) == shapes
        assert not doc.is_dirty, "a refused settle still rewrote an object"
    finally:
        doc.close()


def test_a_kid_that_is_not_a_page_is_refused(nested):
    """A ``/Kids`` entry that resolves to no dictionary is a sheet I cannot name.

    An integer is not a page, and counting it as one would let the walk's total
    agree with the document's while the list it settled belongs to something else —
    so the guard is on the *kind*, not on the arithmetic.
    """
    doc = fitz.open(nested)
    try:
        mid2 = kids_of(doc, pages_root(doc))[1]
        stray = doc.get_new_xref()
        doc.update_object(stray, "42")
        doc.xref_set_key(mid2, "Kids", f"[{stray} 0 R]")
        with pytest.raises(EditError, match="is not a page"):
            SettlePageTree().apply(doc)
    finally:
        doc.close()


def test_a_page_tree_that_cannot_be_counted_is_refused(nested):
    """A tree that lists a node twice holds more sheets than the document has.

    ``/Kids`` is the only place a page can be found from the root, so a walk that
    comes back with four sheets from a three-page document is looking at a tree the
    reader cannot reach — and pushing values down it would be guesswork about which
    three are real.
    """
    doc = fitz.open(nested)
    try:
        root = pages_root(doc)
        mid1, mid2 = kids_of(doc, root)
        doc.xref_set_key(root, "Kids", f"[{mid1} 0 R {mid2} 0 R {mid2} 0 R]")
        assert doc.page_count == 3
        before, shapes = digests(doc), shape(doc)
        with pytest.raises(EditError, match="cannot be counted"):
            SettlePageTree().apply(doc)
        assert digests(doc) == before
        assert shape(doc) == shapes
    finally:
        doc.close()


def test_a_page_tree_that_loops_is_refused(nested):
    """The walk stops at a node it has already stood on rather than spending the stack.

    The chain from the root is what is remembered, not every node ever seen, so a
    sheet the tree lists twice stays legal while a node that names itself does not.
    """
    doc = fitz.open(nested)
    try:
        mid1 = kids_of(doc, pages_root(doc))[0]
        doc.xref_set_key(mid1, "Kids", f"[{mid1} 0 R]")
        with pytest.raises(EditError, match="own ancestor"):
            SettlePageTree().apply(doc)
    finally:
        doc.close()


def test_a_page_that_inherits_from_itself_is_refused(nested):
    """A ``/Parent`` chain that comes back around is not something I can resolve.

    The tree walk's loop guard catches a node that lists itself; this is the other
    half of the document, where a sheet's own parent link names it.  What such a
    page resolves to is a question MuPDF itself does not answer, so nothing is
    written on the strength of it.
    """
    doc = fitz.open(nested)
    try:
        sheet = doc.load_page(0).xref
        doc.xref_set_key(sheet, "Parent", f"{sheet} 0 R")
        with pytest.raises(EditError, match="inherit from themselves"):
            SettlePageTree().apply(doc)
    finally:
        doc.close()


# --------------------------------------------------------------------------- when to bother
def test_a_book_that_already_states_everything_is_not_rewritten(tmp_path):
    """The cheap answer for a flat document: 540 sheets, nothing to settle."""
    path = build_sample_pdf(tmp_path / "plain.pdf", pages=3)
    doc = fitz.open(path)
    try:
        assert set(tree_types(doc)) == {"/Page"}
        assert not doc.is_dirty
        SettlePageTree().apply(doc)
        assert not doc.is_dirty, "the tree was rebuilt for nothing"
    finally:
        doc.close()


def test_a_root_that_turns_every_page_is_pushed_down_too(tmp_path):
    """One level deep is not enough: the pages still have to ask the node above them.

    This is the shape a producer that rotates a whole document writes — the one
    ``RotatePages`` was built around — and a reorder *inside* it is safe already,
    because every page shares that one ancestor.  It is settled anyway, because a
    document PDFAR has written should not leave its pages' geometry in a node that
    holds nothing but the list of them: the turn belongs on each sheet now, and the
    root keeps declaring it too, which is what the next reader inherits.
    """
    path = build_sample_pdf(tmp_path / "root-turn.pdf", pages=2)
    doc = fitz.open(path)
    try:
        root = pages_root(doc)
        doc.xref_set_key(root, "Rotate", "90")
        for kid in kids_of(doc, root):
            doc.xref_set_key(kid, "Rotate", "null")
        assert [doc.load_page(i).rotation for i in range(2)] == [90, 90]
        assert own(doc, 0, "Rotate") == "null"
        SettlePageTree().apply(doc)
        assert own(doc, 0, "Rotate") == "90"
        assert [doc.load_page(i).rotation for i in range(2)] == [90, 90]
    finally:
        doc.close()


def test_applying_it_to_a_settled_book_writes_nothing(nested):
    doc = fitz.open(nested)
    try:
        SettlePageTree().apply(doc)
        before = [doc.xref_object(doc.load_page(i).xref, compressed=True)
                  for i in range(doc.page_count)]
        doc2 = fitz.open("pdf", doc.tobytes())
        try:
            assert not doc2.is_dirty
            SettlePageTree().apply(doc2)
            assert not doc2.is_dirty
            assert [doc2.xref_object(doc2.load_page(i).xref, compressed=True)
                    for i in range(doc2.page_count)] == before
        finally:
            doc2.close()
    finally:
        doc.close()


# ---------------------------------------------------------------------------- the step's word
def test_the_step_says_what_it_can_have_changed():
    edit = SettlePageTree()
    assert edit.scope is Scope.STRUCTURE
    # Nothing a reader caches per page goes stale: the write is to a page's own
    # dictionary, and the value it writes is the one that page already resolved
    # to, so the render, the marks and the links all still describe the sheet.
    assert edit.changed == ()
    assert edit.page_order(4) == (0, 1, 2, 3)
    assert edit.label == "Settle Page Tree"


def test_the_step_publishes_an_identity_map(tmp_path):
    """No sheet moves, so the numbering a reader holds is already right.

    An edit that *says* the identity is what lets the carries in F10-4h and
    F10-4n keep the highlight and the reader's row; a step that said nothing
    would be read as a tree that moved.
    """
    path = nested_pdf(tmp_path / "identity.pdf")
    session = make_session(path)
    try:
        _version, scope = session.apply(SettlePageTree())
        assert scope is Scope.STRUCTURE
        assert session.renumbering is not None
        assert session.renumbering.followed(2) == 2
        assert session.renumbering.where(2) == 2
    finally:
        session.close()


def test_the_saved_file_states_its_own_geometry(tmp_path):
    """Read back from disk: the pages no longer ask the tree what size they are."""
    path = nested_pdf(tmp_path / "saved.pdf")
    session = make_session(path)
    try:
        wanted = digests(session.doc)
        session.apply(SettlePageTree())
        saved = save_document(session).path
    finally:
        session.close()
    with fitz.open(saved) as back:
        assert set(tree_types(back)) == {"/Pages"}      # left where it was put
        assert digests(back) == wanted
        assert own_numbers(back, 2, "CropBox") == (20.0, 20.0, 280.0, 380.0)
        assert all(own(back, index, "Rotate") != "null" for index in range(3))


def test_undoing_the_settle_brings_the_inheritance_back(tmp_path):
    """The snapshot is the whole point: the tree the producer wrote is still there."""
    path = nested_pdf(tmp_path / "undoable.pdf")
    session = make_session(path)
    try:
        session.apply(SettlePageTree())
        assert own(session.doc, 0, "MediaBox") != "null"
        session.undo()
        assert own(session.doc, 0, "MediaBox") == "null"
        mid1 = kids_of(session.doc, pages_root(session.doc))[0]
        assert session.doc.xref_get_key(mid1, "Rotate")[1] == "90"
    finally:
        session.close()


def test_a_refused_settle_leaves_the_session_where_it_was(tmp_path):
    """The refusal is a whole answer: no version, no step, no rewritten tree.

    This is the copy-and-adopt rule earning its keep on the worst input the step
    can meet — a document the user has *not* asked to be changed and which the
    reorder path tried to settle anyway.  The live document still hangs its pages
    off the ancestors the producer wrote, the undo stack is still empty, and the
    session is still able to take an edit afterwards.
    """
    path = grouped_pdf(nested_pdf(tmp_path / "refused.pdf"))
    session = make_session(path)
    try:
        before = digests(session.doc)
        with pytest.raises(EditError, match="Group"):
            session.apply(SettlePageTree())
        assert session.version == 0
        assert session.history.depth == 0
        assert not session.history.can_undo
        assert session.renumbering is None
        assert set(tree_types(session.doc)) == {"/Pages"}
        assert digests(session.doc) == before
        session.apply(RotatePages((1,), 90))
        assert session.version == 1
        assert session.history.undo_label == "Rotate Page"
    finally:
        session.close()


# ------------------------------------------------------------------------ real documents
PAPERS = sorted((Path(__file__).resolve().parents[1]
                 / "external" / "two-column-test-pdfs").glob("*.pdf"))


@pytest.mark.parametrize("path", PAPERS, ids=lambda path: path.name)
def test_a_real_paper_renders_the_same_after_the_settle(tmp_path, path):
    """The licensed corpus: nested trees that lift nothing, and settle anyway.

    Every one of these has one or two intermediate nodes and no attribute on them,
    which is the ordinary case: the walk finds nothing a move could change and
    writes nothing at all.  Asserted as "nothing visible moved and nothing in the
    catalog went either", which is the damage the library's own transform did.
    """
    doc = fitz.open(str(path))
    try:
        before = digests(doc)
        catalog = sorted(doc.xref_get_keys(doc.pdf_catalog()))
        under_root = kids_of(doc, pages_root(doc))
        SettlePageTree().apply(doc)
        assert doc.page_count == len(before)
        assert digests(doc) == before
        assert sorted(doc.xref_get_keys(doc.pdf_catalog())) == catalog
        assert kids_of(doc, pages_root(doc)) == under_root
    finally:
        doc.close()


# ------------------------------------------------------- the tree a move leaves behind
def emptied_nodes(doc):
    """Every ``/Pages`` node under the root with no children of its own."""
    return [kid for kid in kids_of(doc, pages_root(doc))
            if doc.xref_get_key(kid, "Type")[1] == "/Pages" and not kids_of(doc, kid)]


def test_a_node_a_move_emptied_is_walked_rather_than_refused(nested):
    """MuPDF's own ``move_page`` produces this shape, so the settle must read it.

    A page that moves is re-parented into the node it lands in, and the node it
    leaves keeps its dictionary with ``/Count 0`` and ``/Kids []``.  Refusing that
    is refusing the document the previous edit just made, which is what the first
    version of this step did until the move step ran a book through twice.
    """
    doc = fitz.open(nested)
    doc.move_page(2, 0)
    assert emptied_nodes(doc), "MuPDF left no emptied node, so this is the wrong shape"
    SettlePageTree().apply(doc)
    assert doc.page_count == 3
    for index in range(doc.page_count):
        assert doc.xref_get_key(doc.load_page(index).xref, "MediaBox")[1] != "null"
    doc.close()


def test_a_group_no_page_reaches_through_any_more_does_not_stop_the_settle(tmp_path):
    """The checks belong to the chains the pages resolve, not to the whole tree.

    Two bare moves take every sheet out from under the ancestor that carries the
    transparency ``/Group``, and that node still states it — to nobody.  The step
    refuses a ``/Group`` a page *inherits*, because moving that page changes how it
    looks; a node no page's ``/Parent`` chain reaches changes nothing, and refusing
    it would mean a book that had been reordered once could never be settled.
    """
    path = grouped_pdf(nested_pdf(tmp_path / "grouped.pdf"))
    doc = fitz.open(path)
    doc.move_page(0, 2)
    doc.move_page(0, 2)
    holders = [node for node in ([kid for kid in kids_of(doc, pages_root(doc))])
               if doc.xref_get_key(node, "Group")[1] != "null"]
    assert holders and all(not kids_of(doc, node) for node in holders), \
        "the /Group node still governs pages, so this proves nothing"
    SettlePageTree().apply(doc)
    assert doc.page_count == 3
    doc.close()


def test_a_pages_node_that_names_no_children_at_all_is_refused(nested):
    """Absent ``/Kids`` is not the same document as an empty one.

    MuPDF counts a ``/Pages`` node with no ``/Kids`` key as a page of its own, so a
    book like this does not even agree with itself; the refusal is the sentence the
    reader needs, not a walk that quietly drops a sheet.
    """
    doc = fitz.open(nested)
    root = pages_root(doc)
    broken = doc.get_new_xref()
    doc.update_object(broken, f"<< /Type /Pages /Parent {root} 0 R /Count 1 >>")
    kids = kids_of(doc, root)
    doc.xref_set_key(root, "Kids",
                     f"[{' '.join(f'{kid} 0 R' for kid in kids + [broken])}]")
    with pytest.raises(EditError, match="no children"):
        SettlePageTree().apply(doc)
    doc.close()
