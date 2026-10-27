"""F10-1: the edit framework — copy-and-adopt, undo/redo, scope, dirty state.

The promises under test:

* an edit is *intent* (label + scope + apply) and runs against a working copy,
  so a failing edit leaves the document exactly as it was — even when it had
  already mutated the copy before raising;
* undo/redo walk whole-document byte snapshots and keep a human label per step;
* the scope of an edit decides how much of the UI is thrown away;
* the session knows whether its content differs from the file on disk.
"""

import os
import threading
from dataclasses import dataclass
from typing import List, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PyQt6.QtGui import QImage

from conftest import build_sample_pdf, make_session
from pdfar.edits import Edit, EditError, EditHistory, Scope, SetMetadata
from pdfar.session import DocumentSession, SnapshotNotPublished
from pdfar.viewer import PDFView

# ----------------------------------------------------------------- test edits
# Real edits are one per user command; these three stand in for the metadata,
# content and structure families that F10-3..F10-7 will add.


@dataclass(frozen=True)
class AddText(Edit):
    """Write a mark on a page: a content change, nothing else."""

    page: int
    text: str
    at: Tuple[float, float] = (10.0, 80.0)

    scope = Scope.CONTENT

    @property
    def label(self) -> str:
        return f"Add text “{self.text}”"

    def apply(self, doc: fitz.Document) -> None:
        doc.load_page(self.page).insert_text(fitz.Point(*self.at), self.text,
                                             fontsize=10)


@dataclass(frozen=True)
class AppendPage(Edit):
    """A structure change: the page count and everything derived from it moves."""

    scope = Scope.STRUCTURE

    @property
    def label(self) -> str:
        return "Append page"

    def apply(self, doc: fitz.Document) -> None:
        doc.new_page(width=200, height=100)


@dataclass(frozen=True)
class DropPage(Edit):
    """Remove a page — the structure edit F10-2 checks a rewrite really deletes."""

    page: int

    scope = Scope.STRUCTURE

    @property
    def label(self) -> str:
        return f"Delete page {self.page + 1}"

    def apply(self, doc: fitz.Document) -> None:
        doc.delete_page(self.page)


@dataclass(frozen=True)
class HalfDoneThenFail(Edit):
    """Mutates the document it is given, then refuses to finish."""

    text: str = "SHOULD_NOT_APPEAR"

    scope = Scope.CONTENT

    @property
    def label(self) -> str:
        return "Half-done edit"

    def apply(self, doc: fitz.Document) -> None:
        doc.load_page(0).insert_text(fitz.Point(10, 45), self.text, fontsize=10)
        doc.set_metadata({"title": "SHOULD_NOT_APPEAR"})
        raise EditError("nope")


@dataclass(frozen=True)
class Spy(Edit):
    """Records the handle it was handed alongside the session's live document."""

    session: DocumentSession
    seen: List[Tuple[fitz.Document, fitz.Document]]

    scope = Scope.NONE

    @property
    def label(self) -> str:
        return "Spy"

    def apply(self, doc: fitz.Document) -> None:
        self.seen.append((doc, self.session.doc))


# ------------------------------------------------------------------- helpers
def _page_text(doc: fitz.Document, index: int = 0) -> str:
    return doc.load_page(index).get_text()


def _image(color: int = 0xFF00FF) -> QImage:
    image = QImage(4, 4, QImage.Format.Format_RGB888)
    image.fill(color)
    return image


def _view(qapp, path):
    view = PDFView(path)
    view.resize(600, 400)
    view.show()
    qapp.processEvents()
    return view


def _close(qapp, view) -> None:
    view.shutdown()
    view.deleteLater()
    qapp.processEvents()


# ------------------------------------------------------------------- apply
def test_edit_runs_on_a_copy_not_the_live_document(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=2))
    try:
        seen: List[Tuple[fitz.Document, fitz.Document]] = []
        session.apply(Spy(session, seen))
        handed, live_at_the_time = seen[0]
        assert handed is not live_at_the_time      # the edit never touches the live doc
        assert len(seen) == 1
    finally:
        session.close()


def test_apply_adopts_the_result_and_marks_the_session_dirty(tmp_path):
    path = str(build_sample_pdf(tmp_path / "a.pdf", pages=2))
    on_disk = open(path, "rb").read()
    session = make_session(path)
    try:
        live = session.doc
        assert (session.version, session.generation, session.dirty) == (0, 0, False)

        version, scope = session.apply(AddText(0, "APPLIED"))

        assert version == 1 and scope is Scope.CONTENT
        assert session.generation == 1
        assert session.dirty is True
        assert session.doc is not live             # a different Document object
        assert "APPLIED" in _page_text(session.doc)
        assert open(path, "rb").read() == on_disk   # the file is untouched
        assert session.history.undo_label == "Add text “APPLIED”"
    finally:
        session.close()


def test_workers_read_the_edited_bytes(tmp_path):
    path = str(build_sample_pdf(tmp_path / "a.pdf", pages=2))
    session = make_session(path)
    try:
        session.apply(AddText(0, "FORWORKERS"))
        with session.open_copy() as copy:
            assert "FORWORKERS" in _page_text(copy)
        with fitz.open(path) as on_disk:
            assert "FORWORKERS" not in _page_text(on_disk)
    finally:
        session.close()


def test_a_failing_edit_leaves_the_document_untouched(tmp_path):
    """Copy-and-adopt: whatever the edit did to the copy dies with the copy."""
    path = str(build_sample_pdf(tmp_path / "a.pdf", pages=2))
    on_disk = open(path, "rb").read()
    session = make_session(path)
    try:
        live = session.doc
        before = _page_text(session.doc)

        try:
            session.apply(HalfDoneThenFail())
            raise AssertionError("the edit should have failed")
        except EditError as exc:
            assert "nope" in str(exc)

        assert session.doc is live                 # not replaced, still open
        assert session.version == 0 and session.generation == 0
        assert session.dirty is False
        assert not session.history.can_undo
        assert _page_text(session.doc) == before
        assert "SHOULD_NOT_APPEAR" not in _page_text(session.doc)
        assert (session.doc.metadata or {}).get("title") != "SHOULD_NOT_APPEAR"
        assert open(path, "rb").read() == on_disk
    finally:
        session.close()


def test_an_edit_raising_any_error_becomes_an_edit_error(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    try:
        @dataclass(frozen=True)
        class Explodes(Edit):
            scope = Scope.FULL

            @property
            def label(self) -> str:
                return "Explode"

            def apply(self, doc):
                raise RuntimeError("mupdf said no")

        try:
            session.apply(Explodes())
            raise AssertionError("expected EditError")
        except EditError as exc:
            assert "Explode failed" in str(exc)
        assert session.version == 0 and not session.history.can_undo
    finally:
        session.close()


def test_apply_from_another_thread_is_refused(tmp_path):
    """MuPDF allows one thread per document, so ownership is not a suggestion."""
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    outcome = {}

    def worker():
        try:
            session.apply(AddText(0, "X"))
        except Exception as exc:                     # noqa: BLE001 - recorded
            outcome["error"] = exc

    try:
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        assert isinstance(outcome.get("error"), SnapshotNotPublished)
        assert session.version == 0 and session.generation == 0
    finally:
        session.close()


# -------------------------------------------------------------- undo / redo
def test_undo_and_redo_walk_the_document(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=2))
    try:
        session.apply(AddText(0, "UNDONE"))
        assert "UNDONE" in _page_text(session.doc)
        assert session.history.can_undo and not session.history.can_redo

        version, scope = session.undo()
        assert version == 2 and scope is Scope.CONTENT
        assert session.generation == 2
        assert "UNDONE" not in _page_text(session.doc)
        assert session.history.can_redo
        assert session.history.redo_label == "Add text “UNDONE”"

        version, _scope = session.redo()
        assert version == 3 and session.generation == 3
        assert "UNDONE" in _page_text(session.doc)
        assert not session.history.can_redo
    finally:
        session.close()


def test_undo_stays_dirty_because_nothing_has_been_saved(tmp_path):
    """Undo restores the content, not the saved state: the file on disk still
    holds neither the edit nor its absence, so the user must still save (F10-2)."""
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    try:
        session.apply(AddText(0, "X"))
        assert session.dirty
        session.undo()
        assert session.dirty
    finally:
        session.close()


def test_a_new_edit_drops_the_redo_branch(tmp_path):
    """Undo, then edit: the undone branch is gone, as it is in every editor."""
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    try:
        session.apply(AddText(0, "FIRST"))
        session.apply(AddText(0, "SECOND"))
        session.undo()                                  # back to FIRST
        assert session.history.can_redo

        session.apply(AddText(0, "THIRD"))
        assert not session.history.can_redo
        assert len(session.history) == 2                 # FIRST, then SECOND's place
        text = _page_text(session.doc)
        assert "FIRST" in text and "THIRD" in text and "SECOND" not in text

        session.undo()
        assert "THIRD" not in _page_text(session.doc)
        session.undo()
        assert "FIRST" not in _page_text(session.doc)
        try:
            session.undo()
            raise AssertionError("expected EditError")
        except EditError as exc:
            assert "Nothing to undo" in str(exc)
    finally:
        session.close()


def test_undo_and_redo_without_any_work_are_reported(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    try:
        for step in (session.undo, session.redo):
            try:
                step()
                raise AssertionError("expected EditError")
            except EditError as exc:
                assert "Nothing to" in str(exc)
        assert session.version == 0 and session.generation == 0
    finally:
        session.close()


def test_history_is_bounded_by_steps_and_bytes():
    edit = SetMetadata((("title", "t"),))
    history = EditHistory(max_steps=3, max_bytes=10_000)
    for _ in range(6):
        history.record(edit, b"x" * 100)
    assert len(history) == 3
    assert history.bytes_used == 300
    assert history.undo_label == "Edit document properties"

    by_bytes = EditHistory(max_steps=50, max_bytes=250)
    for _ in range(6):
        by_bytes.record(edit, b"y" * 100)
    assert len(by_bytes) == 2 and by_bytes.bytes_used == 200

    history.clear()
    assert len(history) == 0 and history.bytes_used == 0
    assert not history.can_undo and history.undo_target is None


def test_a_snapshot_bigger_than_the_budget_is_dropped_immediately():
    """The budget is a limit, not an average: one oversized step cannot park
    500 MB on the stack just because max_steps allowed it."""
    history = EditHistory(max_steps=50, max_bytes=100)
    history.record(SetMetadata((("title", "t"),)), b"z" * 5_000)
    assert len(history) == 0 and history.bytes_used == 0
    assert not history.can_undo


# ------------------------------------------------------------------- scopes
def test_scope_decides_what_the_view_throws_away(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=3))
    view = _view(qapp, path)
    try:
        view.cache.put(0, view.zoom, _image(), tag="view")
        view._words[0] = [(0, 0, 1, 1, "x")]

        view.invalidate(Scope.METADATA)
        assert len(view.cache) == 1               # pixels survive a title change
        assert view._words                         # so does the text layer
        assert view.session.version == 1           # but readers are still turned away

        view.invalidate(Scope.LINKS)
        assert len(view.cache) == 1

        view.invalidate(Scope.CONTENT)
        assert len(view.cache) == 0
        assert view._words == {}
    finally:
        _close(qapp, view)


def test_metadata_edit_keeps_pixels_and_content_edit_drops_them(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = _view(qapp, path)
    try:
        view.cache.put(0, view.zoom, _image(), tag="view")
        view.cache.put(1, view.zoom, _image(), tag="view")

        assert view.apply_edit(SetMetadata((("title", "New title"),)))
        assert len(view.cache) == 2
        assert (view.doc.metadata or {}).get("title") == "New title"

        assert view.apply_edit(AddText(0, "SEEME"))
        assert len(view.cache) == 0
    finally:
        _close(qapp, view)


def test_structure_edit_refreshes_page_geometry(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = _view(qapp, path)
    try:
        assert (view.page_count, len(view.page_sizes)) == (2, 2)

        assert view.apply_edit(AppendPage())
        assert view.page_count == 3
        assert len(view.page_sizes) == 3
        assert len(view._page_tops) == 3

        assert view.undo_edit()
        assert view.page_count == 2
        assert len(view.page_sizes) == 2
    finally:
        _close(qapp, view)


def test_failed_edit_reports_to_the_user_and_touches_nothing(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = _view(qapp, path)
    messages: List[str] = []
    view.transient_message.connect(messages.append)
    try:
        view.cache.put(0, view.zoom, _image(), tag="view")
        version, generation = view.session.version, view.session.generation

        assert view.apply_edit(HalfDoneThenFail()) is False
        assert view.session.version == version
        assert view.session.generation == generation
        assert len(view.cache) == 1                # nothing was re-rendered
        assert messages and "nope" in messages[0]
    finally:
        _close(qapp, view)


def test_view_undo_and_redo_round_trip(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = _view(qapp, path)
    messages: List[str] = []
    view.transient_message.connect(messages.append)
    try:
        assert view.apply_edit(AddText(0, "ROUND"))
        assert "ROUND" in _page_text(view.doc)

        assert view.undo_edit()
        assert "ROUND" not in _page_text(view.doc)
        assert view.redo_edit()
        assert "ROUND" in _page_text(view.doc)
        assert view.session.dirty

        assert view.redo_edit() is False           # nothing left to redo
        assert messages and "Nothing to redo" in messages[-1]
    finally:
        _close(qapp, view)


def test_content_changed_carries_the_scope(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = _view(qapp, path)
    seen: List[Tuple[int, Scope]] = []
    view.content_changed.connect(lambda version, scope: seen.append((version, Scope(scope))))
    try:
        view.apply_edit(SetMetadata((("author", "someone"),)))
        view.apply_edit(AddText(0, "S"))
        assert seen == [(1, Scope.METADATA), (2, Scope.CONTENT)]
    finally:
        _close(qapp, view)


def test_generation_tracks_the_document_object_not_the_content(tmp_path):
    """``version`` answers "which content?", ``generation`` answers "which object?".

    An in-place change (annotations today, F10-3) moves the content without
    replacing the document; an adopted edit moves both.
    """
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    try:
        doc = session.doc
        doc.load_page(0).insert_text(fitz.Point(10, 45), "in place", fontsize=10)
        session.content_changed()
        assert session.version == 1 and session.generation == 0
        assert session.doc is doc

        session.apply(AddText(0, "adopted"))
        assert session.version == 2 and session.generation == 1
        assert session.doc is not doc
    finally:
        session.close()


def test_closing_a_session_releases_the_snapshots(tmp_path):
    session = make_session(build_sample_pdf(tmp_path / "a.pdf", pages=1))
    session.apply(AddText(0, "X"))
    assert session.history.bytes_used > 0
    session.close()
    assert session.history.bytes_used == 0


def test_an_encrypted_document_survives_an_edit(tmp_path):
    """The working copy is decrypted in memory; the file on disk keeps its lock."""
    path = str(tmp_path / "enc.pdf")
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.insert_text(fitz.Point(10, 80), "PLAIN", fontsize=10)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
             owner_pw="own", user_pw="secret")
    doc.close()
    on_disk = open(path, "rb").read()

    opened = fitz.open(path)
    assert opened.needs_pass
    assert opened.authenticate("secret") in (1, 2)   # 1 = owner, 2 = user password
    session = DocumentSession(opened, path, "secret")
    try:
        session.apply(AddText(0, "EDITED"))
        assert "EDITED" in _page_text(session.doc)
        with session.open_copy() as copy:
            assert copy.load_page(0).get_text().count("EDITED") == 1
        assert open(path, "rb").read() == on_disk
    finally:
        session.close()
