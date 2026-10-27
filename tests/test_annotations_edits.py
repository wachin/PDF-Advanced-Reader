"""F10-3a: an annotation is an edit.

Before this, a highlight lived only in the annotation manager and was pushed into
the document when the user pressed Save (F10-2).  That made it invisible to
``session.dirty`` and to Ctrl+Z, and left it dependent on a "have I written my
model into this document yet" cache.  Here the document becomes the store: every
annotation change is an :class:`pdfar.edits.Edit` with ``Scope.COMMENTS``, the
model is re-read from the document, and the annotation's ``/NM`` name is its
identity — so a sidebar row, a model key and a PDF annotation all mean the same
thing across an undo, a redo, a save and a reopen.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QInputDialog, QMessageBox

from conftest import build_sample_pdf, make_session
from pdfar.annotations import (
    AddHighlight,
    AnnotationManager,
    RemoveHighlight,
    SetHighlightColor,
    SetHighlightNote,
)
from pdfar.edits import EditError, Scope
from test_edits import AddText

RECT = (10.0, 51.0, 120.0, 63.0)
OTHER = (10.0, 71.0, 90.0, 83.0)


@pytest.fixture
def win(qapp):
    from pdfar.main import MainWindow
    w = MainWindow()
    w.show()
    qapp.processEvents()
    yield w
    w._close_all_tabs(confirm=False)
    w.close()
    w.deleteLater()
    qapp.processEvents()


@pytest.fixture
def open_tab(qapp, win, tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    assert win.open_document(path)
    qapp.processEvents()
    return win._active_tab()


@pytest.fixture
def dialogs(monkeypatch):
    """Capture the dialogs a modal test run must never actually show."""
    seen = []
    for name in ("critical", "warning", "information"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k: seen.append(a[2])))
    monkeypatch.setattr(QInputDialog, "getMultiLineText",
                        staticmethod(lambda *a, **k: ("Edited via sidebar", True)))
    return seen


def doc_highlights(doc, page):
    """The highlights on *page*, read while the Page object is alive.

    PyMuPDF drops an ``Annot`` handle once its page is gone, so a helper handing
    back live handles would be a trap for the next test to fall into.
    """
    out = []
    for annot in doc.load_page(page).annots():
        out.append({"id": annot.info.get("id"), "kind": annot.type[1],
                    "color": annot.colors["stroke"], "opacity": annot.opacity,
                    "note": annot.info.get("content"),
                    "title": annot.info.get("title"),
                    "quads": len(annot.vertices) // 4})
    return out


def tinted_pixels(page, zoom=2):
    """How many pixels of a rendered page are not plain white."""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), annots=True, alpha=False)
    return sum(1 for y in range(pix.height) for x in range(pix.width)
               if pix.pixel(x, y)[:3] != (255, 255, 255))


# ------------------------------------------------- the document is the annotation store
def test_a_highlight_is_in_the_document_as_soon_as_it_is_made(tmp_path):
    """No pending model and no sync at Save time: the edit lands in the document.

    This is what lets Ctrl+S say "there is nothing to save" about a document with
    highlights (F10-2 could not) and what makes Ctrl+Z reach annotations at all.
    """
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)

    made = manager.add_highlight(0, [RECT], color="#FFFF00", author="Wachin",
                                 note="why this matters")

    assert made.color == "#FFFF00"
    assert session.dirty is True
    assert session.version == 1
    assert len(session.history) == 1
    stored = doc_highlights(session.doc, 0)
    assert [h["kind"] for h in stored] == ["Highlight"]
    assert stored[0]["note"] == "why this matters"
    assert stored[0]["title"] == "Wachin"
    assert stored[0]["quads"] == 1
    session.close()


def test_a_highlights_identity_is_its_pdf_name(tmp_path):
    """``/NM`` is the model key, so it survives a save, an undo and a reopen."""
    path = build_sample_pdf(tmp_path / "d.pdf")
    session = make_session(path)
    manager = AnnotationManager(session)

    made = manager.add_highlight(0, [RECT], color="#90EE90")

    assert made.id == doc_highlights(session.doc, 0)[0]["id"]
    assert manager.get_annotation(made.id) is made

    assert manager.save_to_pdf() is True
    session.close()

    reopened = make_session(path)
    again = AnnotationManager(reopened)
    assert [h.id for h in again.get_highlights_for_page(0)] == [made.id]
    # The sidebar matches a highlight to its palette entry by hex text, so the
    # case read back out of the document has to be the case the palette uses —
    # otherwise a reopened highlight is listed as "Custom".
    assert again.get_highlights_for_page(0)[0].color == "#90EE90"
    reopened.close()


def test_several_lines_become_one_annotation_with_several_quads(tmp_path):
    """One selection is one annotation, not one annotation per line."""
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)

    manager.add_highlight(0, [RECT, OTHER])

    stored = doc_highlights(session.doc, 0)
    assert len(stored) == 1
    assert stored[0]["quads"] == 2
    assert manager.get_highlights_for_page(0)[0].rects == [RECT, OTHER]
    session.close()


def test_a_selection_over_several_pages_is_one_undo_step(tmp_path):
    """The user asked once, so undo has to undo the whole answer."""
    session = make_session(build_sample_pdf(tmp_path / "d.pdf", pages=3))
    manager = AnnotationManager(session)

    made = manager.add_highlights({0: [RECT], 2: [RECT, OTHER]}, color="#FF6B6B")

    assert len(made) == 2
    assert len(session.history) == 1
    assert len(doc_highlights(session.doc, 0)) == 1
    assert len(doc_highlights(session.doc, 2)) == 1
    assert doc_highlights(session.doc, 2)[0]["quads"] == 2

    session.undo()
    manager.reload_from_document()

    assert manager.get_annotations() == []
    assert doc_highlights(session.doc, 2) == []
    session.close()


def test_an_empty_selection_is_not_an_undo_step(tmp_path):
    """Nothing selected must not become a Ctrl+Z that appears to do nothing."""
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)

    assert manager.add_highlights({0: []}) == []
    assert manager.add_highlight(0, []) is None
    assert len(session.history) == 0
    assert session.dirty is False
    session.close()


def test_an_edit_that_replaces_the_document_keeps_the_highlights(tmp_path):
    """The F10-2 sync cache is gone: a highlight cannot fall out of a clone.

    It is in the document, so serializing the document carries it, so an
    unrelated content edit — or an undo — cannot lose it.
    """
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)
    made = manager.add_highlight(0, [RECT])

    session.apply(AddText(0, "MARGIN NOTE"))
    manager.reload_from_document()

    assert [h.id for h in manager.get_highlights_for_page(0)] == [made.id]
    assert len(doc_highlights(session.doc, 0)) == 1
    session.close()


def test_a_foreign_annotation_without_a_name_is_still_addressable(tmp_path):
    """Opening must not rewrite the file, so an unnamed annot needs a stand-in.

    A highlight made by another program has no ``/NM``.  Naming it on open would
    make merely looking at a document a change to it, so the model keys it by
    xref instead — and deleting it still works.
    """
    source = build_sample_pdf(tmp_path / "d.pdf")
    foreign = str(tmp_path / "foreign.pdf")
    with fitz.open(source) as doc:
        page = doc.load_page(0)
        annot = page.add_highlight_annot(fitz.Rect(*RECT))
        annot.set_colors(stroke=(1, 0, 0))
        annot.update()
        doc.xref_set_key(annot.xref, "NM", "null")
        doc.save(foreign)

    session = make_session(foreign)
    manager = AnnotationManager(session)
    listed = manager.get_highlights_for_page(0)

    assert len(listed) == 1
    assert listed[0].id.startswith("anon:")
    assert session.version == 0, "looking at a document is not an edit"
    assert session.dirty is False

    assert manager.remove_annotation(listed[0].id) is True
    assert doc_highlights(session.doc, 0) == []
    session.close()


def test_the_highlight_reaches_the_pixels_through_the_document(tmp_path):
    """A background reader of the session's content sees it, unsaved.

    ``render.py`` draws annotations, which is why the view's own highlight
    overlay went away: keeping it painted the band a second time.
    """
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    with fitz.open(session.path) as disk:
        before = tinted_pixels(disk.load_page(0))

    manager = AnnotationManager(session)
    manager.add_highlight(0, [RECT], color="#FFFF00")
    session.read_version()

    with session.open_copy(session.version) as copy:
        after = tinted_pixels(copy.load_page(0))

    assert after > before
    session.close()


# ------------------------------------------------------------------ undo / redo
def test_recoloring_is_an_undoable_edit(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)
    made = manager.add_highlight(0, [RECT], color="#FFFF00")

    assert manager.set_highlight_color(made.id, "#87CEEB") is True
    assert manager.get_annotation(made.id).color == "#87CEEB"
    assert doc_highlights(session.doc, 0)[0]["color"] == pytest.approx(
        (0x87 / 255, 0xCE / 255, 0xEB / 255))
    assert session.history.undo_label == "Change highlight color"

    session.undo()
    manager.reload_from_document()

    assert manager.get_annotation(made.id).color == "#FFFF00"
    session.close()


def test_a_note_is_written_into_the_annotation_and_undoes(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)
    made = manager.add_highlight(0, [RECT])

    updated = manager.add_note_to_highlight(made.id, "read this again")

    assert updated.id == made.id
    assert updated.has_note() and updated.get_note() == "read this again"
    assert doc_highlights(session.doc, 0)[0]["note"] == "read this again"

    assert manager.remove_note_from_highlight(made.id) is True
    assert session.history.undo_label == "Remove note"
    assert doc_highlights(session.doc, 0)[0]["note"] == ""

    session.undo()
    manager.reload_from_document()
    assert manager.get_note_for_highlight(made.id) == "read this again"
    session.close()


def test_deleting_a_highlight_is_an_undoable_edit(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "d.pdf", pages=3))
    manager = AnnotationManager(session)
    made = manager.add_highlight(0, [RECT])
    kept = manager.add_highlight(1, [OTHER])

    assert manager.remove_annotation(made.id) is True
    assert session.history.undo_label == "Delete annotation"
    assert [h.page for h in manager.get_annotations()] == [1]

    session.undo()
    manager.reload_from_document()

    assert [h.id for h in manager.get_annotations()] == [made.id, kept.id], \
        "undo brings this highlight back and leaves the other one alone"
    assert len(doc_highlights(session.doc, 0)) == 1
    session.close()


def test_an_edit_for_a_gone_annotation_refuses_its_own_document(tmp_path):
    """Undoing past a highlight leaves nothing named that id.

    Writing to some other annotation because its key happened to match would
    damage the document, so the edit says no and the adoption never happens.
    """
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)
    made = manager.add_highlight(0, [RECT])
    session.undo()
    manager.reload_from_document()
    before = session.version

    with pytest.raises(EditError, match="not in this document"):
        session.apply(SetHighlightNote(ann_id=made.id, page=0, note="orphan"))

    assert len(session.history) == 0, "a refused edit records nothing"
    assert session.version == before, "a refused edit changes no content"
    assert session.history.can_redo is True
    session.close()


def test_a_highlight_on_a_page_that_is_not_there_says_so(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "d.pdf"))
    manager = AnnotationManager(session)
    seen = []
    manager.annotation_edit_failed.connect(seen.append)

    assert manager.add_highlight(99, [RECT]) is None

    assert seen and seen[0].startswith("Highlight"), "the reason must be reported"
    assert manager.get_annotations() == []
    assert session.version == 0
    assert len(session.history) == 0
    session.close()


# ------------------------------------------------------------------ edit intent
def test_each_edit_names_the_scope_it_can_reach():
    assert AddHighlight(specs=()).scope is Scope.COMMENTS
    assert RemoveHighlight(ann_id="x", page=0).scope is Scope.COMMENTS
    assert SetHighlightColor(ann_id="x", page=0, color="#FFFF00").scope is Scope.COMMENTS
    assert SetHighlightNote(ann_id="x", page=0, note="n").scope is Scope.COMMENTS


def test_the_note_edit_is_named_for_what_it_does():
    assert SetHighlightNote(ann_id="x", page=0, note="first").label == "Add note"
    assert SetHighlightNote(ann_id="x", page=0, note="more",
                            had_note=True).label == "Edit note"
    assert SetHighlightNote(ann_id="x", page=0, note="  ").label == "Remove note"
    assert AddHighlight(specs=()).label == "Highlight"


def test_a_colour_is_read_back_exactly_as_it_was_written(tmp_path):
    """Palette colours have to survive the float round-trip through the PDF."""
    from pdfar.annotations import HIGHLIGHT_COLORS

    session = make_session(build_sample_pdf(tmp_path / "d.pdf", pages=3))
    manager = AnnotationManager(session)

    made = []
    for index, (_name, hex_color) in enumerate(HIGHLIGHT_COLORS):
        made.append(manager.add_highlight(index % 3, [RECT], color=hex_color))

    back = {h.id: h.color for h in manager.get_annotations()}
    assert back == {h.id: h.color for h in made}
    session.close()


# ---------------------------------------------------------------------- the UI
def test_a_highlight_marks_the_tab_dirty(qapp, win, open_tab):
    """The point of the whole increment: Ctrl+W now warns about highlights."""
    open_tab.view.annotation_manager.add_highlight(0, [RECT])
    qapp.processEvents()

    assert open_tab.dirty is True
    assert win.tabs.tabText(0).startswith("•")
    assert win.isWindowModified() is True
    assert win.act_revert.isEnabled() is True


def test_the_undo_menu_names_the_highlight_and_the_sidebar_follows(qapp, win,
                                                                  open_tab):
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [RECT])
    qapp.processEvents()

    assert win.act_undo.text() == "&Undo Highlight"
    assert _tree_ids(open_tab) == [h.id for h in manager.get_highlights_for_page(0)]

    win.act_undo.trigger()
    qapp.processEvents()

    assert manager.get_annotations() == []
    assert _tree_ids(open_tab) == []
    assert doc_highlights(open_tab.view.doc, 0) == []
    assert win.act_undo.text() == "&Undo"


def test_the_view_is_asked_to_repaint_when_comments_change(qapp, win, open_tab):
    """``Scope.COMMENTS`` is in the painted set, so the pixels come over."""
    seen = []
    open_tab.view.content_changed.connect(lambda v, s: seen.append(Scope(s)))

    open_tab.view.annotation_manager.add_highlight(0, [RECT])
    qapp.processEvents()

    assert seen == [Scope.COMMENTS]


def test_revert_drops_a_highlight_that_was_never_saved(qapp, win, open_tab,
                                                       monkeypatch, dialogs):
    manager = open_tab.view.annotation_manager
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    manager.add_highlight(0, [RECT])
    assert win._save_tab(open_tab) is True
    manager.add_highlight(1, [RECT])

    assert win._revert_tab(open_tab) is True
    qapp.processEvents()

    assert [h.page for h in manager.get_annotations()] == [0]
    assert _tree_ids(open_tab) == [h.id for h in manager.get_annotations()]
    assert open_tab.dirty is False


def test_the_sidebar_edits_through_the_manager_not_the_model(qapp, win, open_tab,
                                                             dialogs):
    """The annotation context menu used to reach into ``manager._annotations``."""
    manager = open_tab.view.annotation_manager
    made = manager.add_highlight(0, [RECT])
    qapp.processEvents()

    open_tab.sidebar._change_annotation_color(made.id, "#FF6B6B")
    open_tab.sidebar._edit_note(made.id)
    qapp.processEvents()

    assert manager.get_annotation(made.id).color == "#FF6B6B"
    assert manager.get_annotation(made.id).get_note() == "Edited via sidebar"
    assert doc_highlights(open_tab.view.doc, 0)[0]["note"] == "Edited via sidebar"
    assert dialogs == []


def test_a_highlight_is_saved_by_the_windows_write(qapp, win, open_tab, dialogs):
    """Save no longer has to materialize anything first."""
    manager = open_tab.view.annotation_manager
    manager.add_highlight(0, [RECT])

    assert win._save_tab(open_tab) is True
    qapp.processEvents()

    with fitz.open(open_tab.doc_path) as disk:
        assert doc_highlights(disk, 0) != []
    assert dialogs == []


def test_the_model_is_re_read_when_the_document_object_changes(qapp, win,
                                                              open_tab):
    """An adopted clone is a different ``fitz.Document``, so the list is re-read.

    The highlight survived in its bytes (F10-2 proved that), but nothing may rely
    on that: the model's page index and id come from whichever document the
    session is holding, which is why the rebuild is keyed on the generation.
    """
    manager = open_tab.view.annotation_manager
    made = manager.add_highlight(1, [RECT])

    open_tab.view.apply_edit(AddText(0, "ELSEWHERE"))
    qapp.processEvents()

    assert [h.id for h in manager.get_highlights_for_page(1)] == [made.id]


# ------------------------------------------------------------------- helpers
def _tree_ids(tab):
    """The annotation ids the sidebar tree currently shows."""
    tree = tab.sidebar.annotations_tree
    ids = []
    for i in range(tree.topLevelItemCount()):
        node = tree.topLevelItem(i)
        for j in range(node.childCount()):
            ids.append(node.child(j).data(0, Qt.ItemDataRole.UserRole))
    return ids
