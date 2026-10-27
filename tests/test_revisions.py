"""F10-3c: the document as a sequence of revision files.

Two promises, and the tests are organized around them.

*An accepted edit costs a copy and an append, not a serialization.*  So the
history holds files, ``content_path()`` is what readers open, and publishing a
version for the render pool must not build a byte buffer at all.

*Copy-and-adopt still means a failed edit changes nothing.*  The working copy is
a separate file: if the edit raises, the file goes and the revision the user was
looking at is untouched — on disk as well as in memory.
"""

import os
import stat

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from conftest import build_sample_pdf, make_session
from test_edits import AddText, DropPage, HalfDoneThenFail

from pdfar.edits import EditHistory, SetMetadata, Snapshot
from pdfar.revisions import RevisionStore, RevisionStoreError
from pdfar.save import save_document
from pdfar.session import DocumentSession

from pdfar.edits import EditError


def directory_of(session) -> str:
    store = session._revisions
    assert store is not None, "this session fell back to byte snapshots"
    return store.directory


def _text(doc, page: int) -> str:
    return doc.load_page(page).get_text()


# ------------------------------------------------------------------ the store
def test_the_store_is_private_to_the_user():
    store = RevisionStore.create()
    try:
        mode = stat.S_IMODE(os.stat(store.directory).st_mode)
        assert mode & 0o077 == 0, f"{oct(mode)} is readable by someone else"
    finally:
        store.close()
    assert not os.path.isdir(store.directory)


def test_a_revision_is_a_copy_not_a_link(tmp_path):
    """MuPDF rewrites the section a previous save appended, so two revisions
    sharing one inode would let a later edit amend an earlier one."""
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    store = RevisionStore.from_file(path)
    try:
        first = os.path.join(store.directory, "r0001.pdf")
        second = store.next_path()
        store.copy_into(first, second)
        assert os.stat(first).st_ino != os.stat(second).st_ino
        doc = fitz.open(second)
        try:
            doc.delete_page(0)
            doc.save(second, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        finally:
            doc.close()
        with fitz.open(first) as untouched:
            assert untouched.page_count == 3
    finally:
        store.close()


def test_a_revision_of_an_encrypted_file_stays_encrypted(tmp_path):
    path = str(tmp_path / "enc.pdf")
    seed = fitz.open()
    page = seed.new_page(width=200, height=100)
    page.insert_text(fitz.Point(10, 80), "PLAIN", fontsize=10)
    seed.save(path, encryption=fitz.PDF_ENCRYPT_AES_256,
              owner_pw="own", user_pw="secret")
    seed.close()
    store = RevisionStore.from_file(path)
    try:
        copy = os.path.join(store.directory, "r0001.pdf")
        assert b"PLAIN" not in open(copy, "rb").read()
        doc = fitz.open(copy)
        try:
            assert doc.needs_pass == 1
        finally:
            doc.close()
    finally:
        store.close()


def test_release_removes_one_revision_and_survives_gone_files(tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=1))
    store = RevisionStore.create()
    try:
        copy = store.next_path()
        store.copy_into(path, copy)
        store.release(copy)
        assert not os.path.exists(copy)
        store.release(copy)          # releasing twice is not an error
        store.release(None)
    finally:
        store.close()


def test_paths_are_never_reused():
    store = RevisionStore.create()
    try:
        assert len({store.next_path() for _ in range(5)}) == 5
        assert store.size_of(None) == 0
    finally:
        store.close()


def test_a_directory_that_is_not_there_is_refused():
    try:
        RevisionStore("/definitely/not/here")
    except RevisionStoreError as exc:
        assert "No revision directory" in str(exc)
    else:
        raise AssertionError("a missing directory must be refused")


def test_copy_into_refuses_a_source_that_is_not_there(tmp_path):
    store = RevisionStore.create()
    try:
        try:
            store.copy_into(str(tmp_path / "gone.pdf"), store.next_path())
        except RevisionStoreError as exc:
            assert "Could not write a working revision" in str(exc)
        else:
            raise AssertionError("a missing source must be refused")
        assert len(store) == 0, "a failed copy left a file behind"
    finally:
        store.close()


# ------------------------------------------------------- the session on files
def test_the_first_edit_makes_the_file_a_revision(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        assert session.content_path() is None      # the disk is still the authority
        session.apply(AddText(0, "EDITED"))
        revision = session.content_path()
        assert revision and os.path.isfile(revision)
        with fitz.open(revision) as doc:
            assert "EDITED" in doc.load_page(0).get_text()
    finally:
        session.close()


def test_the_user_file_is_untouched_until_a_save(tmp_path):
    path = str(build_sample_pdf(tmp_path / "doc.pdf", pages=3))
    on_disk = open(path, "rb").read()
    session = make_session(path)
    try:
        session.apply(AddText(0, "EDITED"))
        assert open(path, "rb").read() == on_disk
    finally:
        session.close()


def test_undo_and_redo_walk_the_revision_files(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "FIRST"))
        after_first = session.content_path()
        session.apply(AddText(1, "SECOND"))
        after_second = session.content_path()
        assert after_first != after_second

        session.undo()
        assert "FIRST" in _text(session.doc, 0)
        assert "SECOND" not in _text(session.doc, 1)
        assert session.content_path() == after_first
        assert session.dirty, "undo is not a save"

        session.redo()
        assert "SECOND" in _text(session.doc, 1)
        assert session.content_path() == after_second
    finally:
        session.close()


def test_a_structure_edit_round_trips_through_files(tmp_path):
    """Deleting a page has to come back on undo, which is where a half-written
    revision would show up as a document MuPDF cannot open."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=5)
    session = make_session(path)
    try:
        session.apply(DropPage(2))
        assert session.doc.page_count == 4
        session.undo()
        assert session.doc.page_count == 5
        with session.open_copy(session.read_version()) as copy:
            assert copy.page_count == 5
        session.redo()
        assert session.doc.page_count == 4
    finally:
        session.close()


def test_a_failed_edit_leaves_the_revision_and_the_history_alone(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "KEPT"))
        directory = directory_of(session)
        before = sorted(os.listdir(directory))
        good = session.content_path()
        text_before = _text(session.doc, 0)
        version = session.version

        try:
            session.apply(HalfDoneThenFail())
        except EditError:
            pass
        else:
            raise AssertionError("the edit was expected to fail")

        assert sorted(os.listdir(directory)) == before, "a refused edit wrote one"
        assert session.content_path() == good
        assert _text(session.doc, 0) == text_before
        assert "SHOULD_NOT_APPEAR" not in text_before
        assert session.version == version
        assert len(session.history) == 1
    finally:
        session.close()


def test_readers_get_the_revision_without_a_buffer(tmp_path):
    """Publishing must not serialize: that is the 1.7 s this increment removes."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "EDITED"))
        version = session.read_version()
        assert session._snapshot is None, "read_version() built a byte buffer"
        with session.open_copy(version) as copy:
            assert "EDITED" in copy.load_page(0).get_text()
    finally:
        session.close()


def test_snapshot_still_answers_with_bytes(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        assert session.snapshot() is None
        session.apply(AddText(0, "EDITED"))
        data = session.snapshot()
        assert data[:4] == b"%PDF"
        assert session.snapshot() is data          # published once, reused
        session.apply(AddText(1, "AGAIN"))
        assert session.snapshot() is not data
    finally:
        session.close()


def test_dropping_a_step_takes_its_file_with_it(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    session.history.max_steps = 2
    try:
        for i in range(4):
            session.apply(AddText(0, f"T{i}"))
        assert len(session.history) == 2
        assert len(session._revisions) <= 3, "a dropped step left its revision"
        assert os.path.isfile(session.content_path())
        session.undo()
        assert "T2" in _text(session.doc, 0)
    finally:
        session.close()


def test_the_byte_budget_counts_revisions(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=6)
    session = make_session(path)
    session.history.max_bytes = os.path.getsize(str(path)) + 10
    try:
        session.apply(AddText(0, "ONE"))
        session.apply(AddText(0, "TWO"))
        assert len(session.history) <= 1, "the budget did not bound the files"
        assert session.history.bytes_used <= session.history.max_bytes
    finally:
        session.close()


def test_closing_the_session_removes_the_directory(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    session.apply(AddText(0, "EDITED"))
    directory = directory_of(session)
    session.close()
    assert not os.path.isdir(directory)


def test_revert_throws_the_revisions_away(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "EDITED"))
        directory = directory_of(session)
        session.revert()
        assert not os.path.isdir(directory)
        assert session.content_path() is None
        assert "EDITED" not in _text(session.doc, 0)
        assert session.dirty is False
        session.apply(AddText(0, "AGAIN"))       # and editing starts a fresh store
        assert os.path.isfile(session.content_path())
    finally:
        session.close()


def test_a_session_with_no_file_falls_back_to_buffers(tmp_path):
    """No path to copy means the F10-1 mechanism, unchanged."""
    data = open(build_sample_pdf(tmp_path / "doc.pdf", pages=3), "rb").read()
    doc = fitz.open(stream=data, filetype="pdf")
    session = DocumentSession(doc, "", "")
    try:
        session.apply(AddText(0, "EDITED"))
        assert session.content_path() is None
        assert session._revisions is None
        assert "EDITED" in _text(session.doc, 0)
        session.undo()
        assert "EDITED" not in _text(session.doc, 0)
    finally:
        session.close()


def test_an_unwritable_temporary_falls_back_instead_of_failing(tmp_path,
                                                                monkeypatch):
    """No room for revisions is a reason to use buffers, not to refuse the edit."""
    import tempfile as _tempfile

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_tempfile, "mkdtemp", refuse)
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "EDITED"))
        assert "EDITED" in _text(session.doc, 0)
        assert session.content_path() is None
    finally:
        session.close()


# ------------------------------------------------------- still saves correctly
def test_a_saved_revision_is_the_document_the_user_asked_for(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = make_session(path)
    try:
        session.apply(AddText(0, "EDITED"))
        session.apply(AddText(1, "ALSO"))
        session.undo()
        report = save_document(session)
        assert report is not None and report.incremental is False
        with fitz.open(str(path)) as doc:
            assert "EDITED" in doc.load_page(0).get_text()
            assert "ALSO" not in doc.load_page(1).get_text()
    finally:
        session.close()


def test_an_edited_encrypted_document_saves_still_protected(tmp_path):
    """The revision knows its own encryption, so a save does not have to guess."""
    path = str(tmp_path / "enc.pdf")
    seed = fitz.open()
    page = seed.new_page(width=200, height=100)
    page.insert_text(fitz.Point(10, 80), "PLAIN", fontsize=10)
    seed.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="own",
              user_pw="secret")
    seed.close()

    opened = fitz.open(path)
    assert opened.needs_pass and opened.authenticate("secret")
    session = DocumentSession(opened, path, "secret", 2)
    try:
        session.apply(AddText(0, "EDITED"))
        assert os.path.isfile(session.content_path())
        save_document(session)
    finally:
        session.close()

    back = fitz.open(path)
    try:
        assert back.needs_pass == 1, "the saved file lost its password"
        assert back.authenticate("secret")
        assert "EDITED" in back.load_page(0).get_text()
    finally:
        back.close()


# ------------------------------------------------------------------ the types
def test_a_snapshot_accepts_both_shapes():
    buffer = Snapshot(data=b"%PDF-1.7", size=8)
    assert buffer.content() == b"%PDF-1.7"
    assert not buffer.is_revision

    history = EditHistory(max_steps=5, max_bytes=1_000_000)
    history.record(SetMetadata((("title", "t"),)), b"x" * 10)
    history.record(SetMetadata((("title", "t"),)), Snapshot(data=b"y" * 20, size=20))
    assert history.bytes_used == 30
    assert len(history) == 2


def test_history_releases_revisions_when_a_step_is_dropped():
    released = []
    history = EditHistory(max_steps=2, max_bytes=1_000_000,
                          on_release=released.append)
    for i in range(3):
        history.record(SetMetadata((("title", "t"),)),
                       Snapshot(path=f"/tmp/r{i}.pdf", size=10))
    assert released == ["/tmp/r0.pdf"]
    history.clear()
    assert sorted(released) == ["/tmp/r0.pdf", "/tmp/r1.pdf", "/tmp/r2.pdf"]
