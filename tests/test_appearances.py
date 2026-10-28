"""F10-3s: a file PDFAR writes carries the drawing of every mark in it.

An annotation's visible content lives in its ``/AP`` appearance stream.  Some
viewers regenerate a missing one from the annotation dictionary, others paint
nothing, and none of them agree on a highlight's exact geometry — so a file is
portable only when the stream is inside it.  PDFAR's own edits write theirs as
the mark is made (the annotation items before this one); these tests pin that
for every kind, and that a save gives one to a mark another program left for
the viewer to regenerate, without rewriting an appearance the file already had.
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar import appearances
from pdfar.annotations import AnnotationManager, MeasurementScale
from pdfar.save import save_document

HIGHLIGHT = (10.0, 51.0, 120.0, 63.0)
SCALE = MeasurementScale(2.5, "cm", 2)

#: The fill operator MuPDF writes for a highlight's colour.
_FILL = re.compile(rb"([-.0-9]+) ([-.0-9]+) ([-.0-9]+) rg")


def make_image(path):
    """A 16x8 RGBA image: red on the left, transparent on the right."""
    raw = bytearray()
    for _y in range(8):
        for x in range(16):
            raw += bytes((255, 0, 0, 255)) if x < 8 else bytes((0, 0, 0, 0))
    fitz.Pixmap(fitz.csRGB, 16, 8, bytes(raw), 1).save(str(path))
    return str(path)


@pytest.fixture
def manager(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    made = AnnotationManager(session)
    try:
        yield made
    finally:
        made.session.close()


def page_annots(doc, index):
    """One page's annotations, keeping the Page alive beside each handle."""
    page = doc.load_page(index)
    return page, list(page.annots() or ())


def appearance_form(doc, annot):
    """An annotation's ``/AP/N``: the form's dictionary and its content."""
    kind, value = doc.xref_get_key(annot.xref, "AP/N")
    assert kind == "xref", (annot.type, kind, value)
    xref = int(value.split()[0])
    return (doc.xref_object(xref, compressed=True),
            bytes(doc.xref_stream(xref) or b""))


def has_appearance(doc, annot):
    return appearances.has_appearance(doc, annot.xref)


def find(doc, ann_id):
    """The page and annotation object named *ann_id*."""
    for index in range(doc.page_count):
        page, annots = page_annots(doc, index)
        for annot in annots:
            if annot.info.get("id") == ann_id:
                return page, annot
    raise AssertionError(f"“{ann_id}” is not in the document")


def find_kind(doc, subtype):
    for index in range(doc.page_count):
        page, annots = page_annots(doc, index)
        for annot in annots:
            if annot.type[1] == subtype:
                return page, annot
    raise AssertionError(f"no {subtype} in the document")


def build_foreign(path, *, squares=1, link=False, custom_appearance=None):
    """A file from another editor: squares whose appearance was left out.

    That is what a program relying on ``/NeedAppearances``-style regeneration
    leaves behind — the dictionary describes the mark, no stream draws it.
    """
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text(fitz.Point(10.0, 95.0), "foreign document", fontsize=9)
    for number in range(1, squares + 1):
        annot = page.add_rect_annot(
            fitz.Rect(20.0, 20.0 * number, 60.0, 20.0 * number + 30.0))
        annot.set_colors(stroke=(0.0, 0.0, 1.0))
        annot.update()
        doc.xref_set_key(annot.xref, "NM", f"(foreign-square-{number})")
        if custom_appearance is None:
            doc.xref_set_key(annot.xref, "AP", "null")
        else:
            _kind, value = doc.xref_get_key(annot.xref, "AP/N")
            doc.update_stream(int(value.split()[0]), custom_appearance)
    if link:
        page.insert_link({"kind": fitz.LINK_URI,
                          "from": fitz.Rect(100.0, 20.0, 180.0, 40.0),
                          "uri": "https://example.com"})
    doc.save(str(path))
    doc.close()
    return str(path)


# ------------------------------------------------- PDFAR's own marks are complete
def test_every_kind_pdfar_writes_saves_its_own_appearance(manager, tmp_path):
    """Fourteen marks, fourteen self-contained drawings after a save."""
    image = make_image(tmp_path / "mark.png")
    manager.add_highlight(0, [HIGHLIGHT])
    manager.add_note(0, (150.0, 10.0), "a note")
    manager.add_ink(1, [[(10.0, 20.0), (60.0, 40.0), (90.0, 20.0)]])
    manager.add_shape(0, "rect", rect=(10.0, 10.0, 40.0, 30.0))
    manager.add_shape(0, "ellipse", rect=(50.0, 10.0, 80.0, 30.0))
    manager.add_shape(1, "line", points=[(10.0, 60.0), (90.0, 60.0)])
    manager.add_shape(1, "arrow", points=[(10.0, 70.0), (90.0, 70.0)])
    manager.add_shape(2, "polygon",
                      points=[(10.0, 20.0), (60.0, 30.0), (30.0, 50.0)])
    manager.add_stamp(2, "Draft", center=(100.0, 50.0))
    manager.add_custom_stamp(0, (100.0, 80.0), caption="PAID")
    manager.add_custom_stamp(1, (120.0, 50.0), image=image)
    manager.add_measurement(1, [(10.0, 90.0), (110.0, 90.0)], SCALE)
    manager.add_measurement(
        2, [(110.0, 60.0), (160.0, 60.0), (160.0, 90.0)], SCALE,
        kind="perimeter")
    manager.add_measurement(
        2, [(120.0, 10.0), (180.0, 10.0), (150.0, 40.0)], SCALE,
        kind="area")

    out = str(tmp_path / "complete.pdf")
    assert save_document(manager.session, out) is not None

    with fitz.open(out) as doc:
        found = 0
        for index in range(doc.page_count):
            _page, annots = page_annots(doc, index)
            for annot in annots:
                definition, content = appearance_form(doc, annot)
                assert "/Subtype/Form" in definition, annot.type
                assert "/BBox" in definition, annot.type
                assert content.strip(), f"{annot.type} paints nothing"
                if b"/F1" in content:
                    assert "/Font" in definition, f"{annot.type} lost its font"
                found += 1
        assert found == 14
        # A file that carries every appearance needs no viewer to rebuild one.
        assert doc.xref_get_key(doc.pdf_catalog(), "AcroForm") == (
            "null", "null")
        assert appearances.ensure_appearances(doc) == 0


def test_a_recolour_reaches_the_saved_appearance(tmp_path, manager):
    """The stream follows the colour, or another reader paints the old one."""
    made = manager.add_highlight(0, [HIGHLIGHT], color="#FFFF00")
    assert manager.set_highlight_color(made.id, "#87CEEB") is True
    _page, annot = find_kind(manager.doc, "Highlight")
    _definition, recoloured = appearance_form(manager.doc, annot)

    out = str(tmp_path / "recoloured.pdf")
    assert save_document(manager.session, out) is not None

    with fitz.open(out) as doc:
        _page, annot = find(doc, made.id)
        _definition, content = appearance_form(doc, annot)
    assert content == recoloured, "the save rewrote an appearance it had"
    colour = tuple(float(channel)
                   for channel in _FILL.search(content).groups())
    assert colour == pytest.approx((0x87 / 255, 0xCE / 255, 0xEB / 255))


# ------------------------------------------------ marks from another program
def test_a_foreign_mark_without_an_appearance_gets_one_when_pdfar_saves(tmp_path):
    path = build_foreign(tmp_path / "foreign.pdf", link=True)
    session = make_session(path)
    try:
        manager = AnnotationManager(session)
        assert manager.add_note(0, (150.0, 10.0), "ours") is not None
        assert save_document(session) is not None
    finally:
        session.close()

    with fitz.open(path) as doc:
        _page, square = find(doc, "foreign-square-1")
        definition, content = appearance_form(doc, square)
        assert "/Subtype/Form" in definition
        assert b"re" in content                # the rectangle the file described
        assert doc.xref_get_key(square.xref, "C") == ("array", "[0 0 1]")

        _page, note = find_kind(doc, "Text")
        assert has_appearance(doc, note)

        # The link shares the page's /Annots array and keeps no appearance.
        link = doc.load_page(0).get_links()[0]
        assert doc.xref_get_key(link["xref"], "AP/N") == ("null", "null")
        assert appearances.ensure_appearances(doc) == 0


def test_an_appearance_the_file_brought_is_left_exactly_as_it_was(tmp_path):
    mark = b"q 0 1 0 RG 3 w 5 5 20 20 re S Q"
    path = build_foreign(tmp_path / "foreign.pdf", custom_appearance=mark)
    session = make_session(path)
    try:
        manager = AnnotationManager(session)
        assert manager.add_note(0, (150.0, 10.0), "ours") is not None
        assert save_document(session) is not None
    finally:
        session.close()

    with fitz.open(path) as doc:
        _page, square = find(doc, "foreign-square-1")
        _definition, content = appearance_form(doc, square)
        assert content == mark, "an appearance the file had was regenerated"
        assert appearances.ensure_appearances(doc) == 0


def test_saving_a_copy_repairs_the_copy_and_leaves_the_original_alone(tmp_path):
    path = build_foreign(tmp_path / "foreign.pdf")
    copy = str(tmp_path / "copy.pdf")
    session = make_session(path)
    try:
        assert save_document(session, copy) is not None
    finally:
        session.close()

    with fitz.open(copy) as doc:
        _page, square = find(doc, "foreign-square-1")
        assert has_appearance(doc, square)
    with fitz.open(path) as doc:
        _page, square = find(doc, "foreign-square-1")
        assert not has_appearance(doc, square), "the original was touched"


def test_repairing_twice_writes_nothing_the_second_time(tmp_path):
    path = build_foreign(tmp_path / "foreign.pdf", squares=2)
    with fitz.open(path) as doc:
        assert appearances.ensure_appearances(doc) == 2
        assert appearances.ensure_appearances(doc) == 0


def test_one_undrawable_mark_does_not_stop_the_others(tmp_path, monkeypatch):
    path = build_foreign(tmp_path / "foreign.pdf", squares=2)
    real = appearances._write_appearance
    refused = []

    def selective(annot):
        if not refused:
            refused.append(annot.xref)
            raise RuntimeError("MuPDF cannot draw this one")
        real(annot)

    monkeypatch.setattr(appearances, "_write_appearance", selective)
    with fitz.open(path) as doc:
        assert appearances.ensure_appearances(doc) == 1
        _page, first = find(doc, "foreign-square-1")
        _page, second = find(doc, "foreign-square-2")
        assert not has_appearance(doc, first)
        assert has_appearance(doc, second)


def test_a_refused_repair_does_not_stop_the_save(tmp_path, monkeypatch):
    path = build_foreign(tmp_path / "foreign.pdf")
    session = make_session(path)
    monkeypatch.setattr(
        appearances, "_write_appearance",
        lambda annot: (_ for _ in ()).throw(RuntimeError("cannot draw")))
    try:
        manager = AnnotationManager(session)
        assert manager.add_note(0, (150.0, 10.0), "ours") is not None
        report = save_document(session)
    finally:
        session.close()

    assert report is not None
    with fitz.open(path) as doc:
        _page, square = find(doc, "foreign-square-1")
        assert not has_appearance(doc, square)
        _page, note = find_kind(doc, "Text")
        assert has_appearance(doc, note)
