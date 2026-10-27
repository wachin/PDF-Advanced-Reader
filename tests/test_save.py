"""F10-2: writing a document out without losing the copy that needed fixing.

A save is the one place where PDFAR can destroy work it did not create, so every
promise under test is about not doing that:

* the destination is either the old file or the new one, never half of either
  (stage in the same directory, flush, fsync, then rename);
* nothing is renamed until the bytes have been reopened and proven to have the
  right page count and a renderable page;
* a refusal is actionable, and a read-only file stays read-only — staging would
  otherwise walk straight over the user's protection;
* an encrypted document is still encrypted afterwards, with the same password
  and the same permissions, even though the session copy is decrypted in memory;
* Revert throws away edits only when asked to.
"""

import os
import stat

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf
from pdfar.save import (ProtectionError, SaveError, save_document, staged_write,
                        verify_bytes)
from pdfar.session import DocumentSession
from test_edits import AddText, AppendPage, DropPage

# --------------------------------------------------------------------- helpers


def page_text(doc, page=0):
    return doc.load_page(page).get_text()


def file_bytes(path):
    with open(str(path), "rb") as handle:
        return handle.read()


def open_session(path, password=""):
    """A session over *path*, authenticated the way the viewer authenticates."""
    doc = fitz.open(str(path))
    authenticated = 0
    if doc.needs_pass:
        authenticated = doc.authenticate(password)
        if not authenticated:
            doc.close()
            raise AssertionError("the test fixture password was refused")
    return DocumentSession(doc, str(path), password, authenticated)


def in_place_change(session) -> None:
    """Change the document the way an annotation does: in place, no adoption.

    The live document is still the file handle it was opened from, which is what
    makes an incremental save possible at all.
    """
    session.doc.load_page(0).insert_text(fitz.Point(10, 92), "IN PLACE", fontsize=8)
    session.content_changed()


def build_encrypted_pdf(path, password, owner=False, pages=2):
    """An encrypted PDF, protected the way real files are."""
    doc = fitz.open()
    for i in range(pages):
        doc.new_page(width=200, height=100).insert_text(
            fitz.Point(10, 60), f"SECRET {i + 1}", fontsize=12)
    kwargs = {"encryption": fitz.PDF_ENCRYPT_AES_256,
              "permissions": fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY}
    if owner:
        kwargs["owner_pw"] = password
    else:
        kwargs["owner_pw"] = "someone-elses-password"
        kwargs["user_pw"] = password
    doc.save(str(path), **kwargs)
    doc.close()
    return str(path)


def is_owner_access(authenticated: int) -> bool:
    """MuPDF reports an owner password as 1 (or 4 with reset permissions)."""
    return bool(authenticated) and not authenticated & 2


# ------------------------------------------------------------- staging write
def test_a_staged_write_replaces_the_file_and_leaves_no_temp(tmp_path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"old contents")
    before = {p.name for p in tmp_path.iterdir()}

    written = staged_write(b"new contents", str(target))

    assert written == len(b"new contents")
    assert target.read_bytes() == b"new contents"
    assert {p.name for p in tmp_path.iterdir()} == before      # nothing left over


def test_the_bytes_reach_the_disk_before_the_rename(tmp_path, monkeypatch):
    """The order *is* the crash safety: rename-then-crash must not lose data."""
    target = tmp_path / "out.bin"
    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def spy_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    staged_write(b"x" * 100, str(target))

    assert events == ["fsync", "replace"]


def test_a_failed_rename_leaves_the_original_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "out.bin"
    target.write_bytes(b"precious")

    def boom(src, dst):
        raise OSError("no room on the device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(SaveError):
        staged_write(b"replacement", str(target))

    assert target.read_bytes() == b"precious"
    # The half-written temp file is cleaned up, not left beside it for a year.
    assert [p.name for p in tmp_path.iterdir()] == ["out.bin"]


def test_a_read_only_file_is_refused_rather_than_bypassed(tmp_path):
    """``os.replace`` needs write permission on the *directory*, not the file.

    So staging would happily overwrite a file the user deliberately locked;
    the protection has to be checked by hand.
    """
    path = build_sample_pdf(tmp_path / "locked.pdf")
    session = open_session(path)
    session.apply(AddText(0, "CANNOT LAND"))
    os.chmod(path, 0o444)
    try:
        with pytest.raises(SaveError) as exc:
            save_document(session)
        assert "read-only" in str(exc.value).lower()
        assert session.dirty is True
    finally:
        os.chmod(path, 0o644)
    with fitz.open(str(path)) as doc:
        assert "CANNOT LAND" not in page_text(doc)
    session.close()


def test_an_unwritable_directory_is_refused(tmp_path):
    directory = tmp_path / "locked"
    directory.mkdir()
    path = build_sample_pdf(directory / "doc.pdf")
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    session = open_session(path)
    session.apply(AddText(0, "CANNOT GO"))
    try:
        with pytest.raises(SaveError) as exc:
            save_document(session)
        assert "permission" in str(exc.value).lower()
        assert session.dirty is True
    finally:
        directory.chmod(0o755)
    session.close()


# --------------------------------------------------------- verify before write
def test_bytes_that_are_not_a_pdf_are_refused(tmp_path):
    """MuPDF repairs a damaged file, so "it opened" is not the proof we need.

    What is refused here is output that is not a document at all; a document
    that opens but has lost pages is the next test.
    """
    with pytest.raises(SaveError):
        verify_bytes(b"PK\x03\x04 not a pdf at all", expected_pages=3)


def test_a_wrong_page_count_is_refused(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    data = fitz.open(path).tobytes()
    with pytest.raises(SaveError) as exc:
        verify_bytes(data, expected_pages=4)
    assert "3" in str(exc.value) and "4" in str(exc.value)


def test_verification_renders_a_page(tmp_path, monkeypatch):
    """A file that parses but cannot be painted is not a file worth keeping."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    data = fitz.open(path).tobytes()
    seen = []
    real = fitz.Page.get_pixmap

    def spy(self, *args, **kwargs):
        seen.append(self.number)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(fitz.Page, "get_pixmap", spy)
    assert verify_bytes(data, expected_pages=2) == 2
    assert seen == [0]


def test_verification_opens_an_encrypted_file_with_its_password(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    data = fitz.open(path).tobytes(encryption=fitz.PDF_ENCRYPT_AES_256,
                                  permissions=fitz.PDF_PERM_PRINT,
                                  owner_pw="owner-pw", user_pw="user-pw")
    assert verify_bytes(data, expected_pages=2, password="user-pw") == 2
    with pytest.raises(SaveError):
        verify_bytes(data, expected_pages=2)


# ------------------------------------------------------------------- saving
def test_save_writes_the_edited_bytes_and_clears_dirty(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = open_session(path)
    session.apply(AddText(0, "IN THE FILE"))
    assert session.dirty is True

    report = save_document(session)

    assert report.path == str(path)
    assert session.dirty is False
    with fitz.open(str(path)) as doc:
        assert "IN THE FILE" in page_text(doc)
    session.close()


def test_saving_a_clean_document_writes_nothing(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    before = os.stat(path)

    assert save_document(session) is None
    assert os.stat(path) == before
    session.close()


def test_saving_a_clean_document_to_a_new_name_still_writes_it(tmp_path):
    """Save As asks for a copy, not for a verdict on whether anything changed."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = open_session(path)
    target = str(tmp_path / "copy.pdf")

    report = save_document(session, target, full=True)

    assert report is not None
    assert report.path == os.path.abspath(target)
    assert os.path.getsize(target) > 0
    assert report.pages == 2
    session.close()


def test_a_failed_save_leaves_the_document_dirty_and_the_file_untouched(tmp_path,
                                                                       monkeypatch):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    session.apply(AddText(0, "MUST_NOT_LAND"))
    original = file_bytes(path)

    def boom(data, target):
        raise OSError("disk full")

    monkeypatch.setattr("pdfar.save.staged_write", boom)
    with pytest.raises(SaveError):
        save_document(session)

    assert file_bytes(path) == original
    assert session.dirty is True
    with fitz.open(str(path)) as doc:
        assert "MUST_NOT_LAND" not in page_text(doc)
    session.close()


def test_an_untouched_document_is_saved_incrementally(tmp_path):
    """Appending keeps what MuPDF does not model — digital signatures above all."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    original = file_bytes(path)
    session = open_session(path)
    in_place_change(session)

    report = save_document(session)

    assert report.incremental is True
    assert file_bytes(path).startswith(original)
    session.close()


def test_an_edited_document_cannot_be_saved_incrementally(tmp_path):
    """Adopting a working copy leaves no original file to append to."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = open_session(path)
    session.apply(AddText(0, "EDITED"))

    report = save_document(session)

    assert report.incremental is False
    with fitz.open(str(path)) as doc:
        assert "EDITED" in page_text(doc)
    session.close()


def test_save_as_rewrites_rather_than_appends(tmp_path):
    """A deleted page must leave the file, not sit in an older revision."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=6)
    with fitz.open(str(path)) as doc:
        objects_before = doc.xref_length()
    session = open_session(path)
    session.apply(DropPage(2))
    target = str(tmp_path / "copy.pdf")

    save_document(session, target, full=True)

    with fitz.open(target) as doc:
        assert doc.page_count == 5
        # garbage collection dropped the orphaned page objects as well.
        assert doc.xref_length() < objects_before
    with fitz.open(str(path)) as doc:
        assert doc.page_count == 6                 # the source is untouched
    session.close()


def test_save_as_retargets_the_session(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    session.apply(AddText(0, "ONLY IN THE COPY"))
    target = str(tmp_path / "copy.pdf")

    report = save_document(session, target, full=True)

    assert report.path == target
    assert session.path == target
    assert session.dirty is False
    with fitz.open(str(path)) as doc:
        assert "ONLY IN THE COPY" not in page_text(doc)
    session.close()


def test_a_file_changed_on_disk_is_not_overwritten_silently(tmp_path):
    """Two programs, one file: the second save must not stomp the first."""
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    session.apply(AddText(0, "OURS"))

    stranger = fitz.open(str(path))
    stranger.new_page(width=200, height=100)
    stranger.save(str(path) + ".tmp")
    stranger.close()
    os.replace(str(path) + ".tmp", str(path))

    with pytest.raises(SaveError) as exc:
        save_document(session)
    assert "changed on disk" in str(exc.value).lower()
    assert session.dirty is True
    with fitz.open(str(path)) as doc:
        assert doc.page_count == 4                 # the stranger's page survives
    session.close()


def test_our_own_write_is_not_treated_as_a_stranger(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    session.apply(AddText(0, "FIRST"))
    save_document(session)

    session.apply(AddText(0, "SECOND"))
    assert save_document(session) is not None
    with fitz.open(str(path)) as doc:
        assert "SECOND" in page_text(doc)
    session.close()


def test_the_saved_document_still_renders_the_same(tmp_path):
    """What was on screen is what the file now holds — the phase's whole rule."""
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = open_session(path)
    session.apply(AddText(1, "PAGE TWO MARK"))
    before = session.doc.load_page(1).get_pixmap(dpi=36).tobytes("png")

    save_document(session)

    with fitz.open(str(path)) as doc:
        assert doc.load_page(1).get_pixmap(dpi=36).tobytes("png") == before
    session.close()


# ---------------------------------------------------------------- encryption
def test_an_untouched_encrypted_document_stays_encrypted(tmp_path):
    path = build_encrypted_pdf(tmp_path / "secret.pdf", "pw-user")
    session = open_session(path, "pw-user")
    in_place_change(session)
    permissions = session.encryption.permissions

    report = save_document(session)

    assert report.incremental is True
    doc = fitz.open(str(path))
    assert doc.needs_pass == 1
    assert doc.authenticate("pw-user") & 2
    assert doc.permissions == permissions
    doc.close()
    session.close()


def test_an_edited_encrypted_document_stays_encrypted(tmp_path):
    """The session copy is decrypted in memory; the file must not become so."""
    path = build_encrypted_pdf(tmp_path / "secret.pdf", "pw-user")
    session = open_session(path, "pw-user")
    session.apply(AddText(0, "NOW YOU CAN SEE ME"))
    permissions = session.encryption.permissions

    report = save_document(session)

    assert report.incremental is False
    doc = fitz.open(str(path))
    assert doc.needs_pass == 1, "the password was silently stripped"
    assert doc.authenticate("wrong-password") == 0
    assert doc.authenticate("pw-user") & 2
    assert doc.permissions == permissions
    assert "NOW YOU CAN SEE ME" in page_text(doc)
    doc.close()
    session.close()


def test_save_as_keeps_the_password(tmp_path):
    path = build_encrypted_pdf(tmp_path / "secret.pdf", "pw-user")
    session = open_session(path, "pw-user")
    session.apply(AddText(0, "STILL SAFE"))
    target = str(tmp_path / "copy.pdf")

    save_document(session, target, full=True)

    doc = fitz.open(target)
    assert doc.needs_pass == 1
    assert doc.authenticate("pw-user") & 2
    assert "STILL SAFE" in page_text(doc)
    doc.close()
    session.close()


def test_an_owner_only_document_saves_incrementally_with_its_password(tmp_path):
    """An in-place change appends, and appending keeps what the file had."""
    path = build_encrypted_pdf(tmp_path / "owner.pdf", "pw-owner", owner=True)
    session = open_session(path)
    assert session.encryption is not None
    assert not session.encryption.reproducible     # nobody was asked for it
    in_place_change(session)

    report = save_document(session)

    assert report.incremental is True
    doc = fitz.open(str(path))
    assert (doc.metadata or {}).get("encryption")
    # Permissions are read before authenticating: the owner password *grants*
    # full access, so checking afterwards would prove nothing.
    assert doc.permissions == session.encryption.permissions
    assert is_owner_access(doc.authenticate("pw-owner"))
    doc.close()
    session.close()


def test_an_owner_only_document_is_not_silently_stripped(tmp_path):
    """A rewrite cannot rebuild a password PDFAR never had, so it asks instead."""
    path = build_encrypted_pdf(tmp_path / "owner.pdf", "pw-owner", owner=True)
    original = file_bytes(path)
    session = open_session(path)
    session.apply(AddText(0, "OWNER EDIT"))

    with pytest.raises(ProtectionError) as exc:
        save_document(session)

    assert "owner password" in str(exc.value).lower()
    assert file_bytes(path) == original
    assert session.dirty is True
    session.close()


def test_replacing_an_unknown_owner_password_is_a_choice_not_an_accident(tmp_path):
    """The copy keeps its restrictions; only the unknown password is replaced."""
    path = build_encrypted_pdf(tmp_path / "owner.pdf", "pw-owner", owner=True)
    session = open_session(path)
    session.apply(AddText(0, "WEAKENED COPY"))
    with pytest.raises(ProtectionError):
        save_document(session)

    report = save_document(session, allow_weakened=True)

    assert report.incremental is False
    doc = fitz.open(str(path))
    assert (doc.metadata or {}).get("encryption"), "the copy stopped being encrypted"
    assert doc.permissions == session.encryption.permissions
    assert "WEAKENED COPY" in page_text(doc)
    assert doc.authenticate("pw-owner") == 0, "the old owner password still works"
    doc.close()
    session.close()


def test_a_password_typed_at_open_survives_every_rewrite(tmp_path):
    """The normal case: the user knows the password, so the copy does too."""
    path = build_encrypted_pdf(tmp_path / "secret.pdf", "pw-user")
    session = open_session(path, "pw-user")
    assert session.encryption.reproducible
    session.apply(AddText(0, "ONE"))
    save_document(session)
    session.apply(AddText(0, "TWO"))
    save_document(session)

    doc = fitz.open(str(path))
    assert doc.needs_pass == 1
    assert doc.authenticate("pw-user") & 2
    assert "TWO" in page_text(doc)
    doc.close()
    session.close()


def test_a_document_that_was_never_encrypted_does_not_become_so(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    assert session.encryption is None
    session.apply(AddText(0, "PLAIN"))

    save_document(session)

    assert fitz.open(str(path)).needs_pass == 0
    session.close()


# --------------------------------------------------------------------- revert
def test_revert_discards_edits_and_clears_dirty(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=2)
    session = open_session(path)
    session.apply(AddText(0, "KEPT"))
    save_document(session)
    session.apply(AddText(0, "THROWN AWAY"))

    version, _scope = session.revert()

    assert session.dirty is False
    assert session.history.undo_label is None
    assert version > 0                             # readers must drop what they hold
    with fitz.open(str(path)) as doc:
        assert "THROWN AWAY" not in page_text(doc)
        assert "KEPT" in page_text(doc)
    assert "KEPT" in page_text(session.doc)
    session.close()


def test_revert_walks_back_every_edit_not_just_the_last(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf", pages=3)
    session = open_session(path)
    session.apply(AddText(0, "ZZMARK-A"))
    session.apply(AddText(0, "ZZMARK-B"))
    session.apply(AppendPage())

    session.revert()

    assert session.doc.page_count == 3
    assert "ZZMARK-A" not in page_text(session.doc)
    assert "ZZMARK-B" not in page_text(session.doc)
    session.close()


def test_revert_reports_a_missing_file_and_keeps_the_work(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    session.apply(AddText(0, "PRECIOUS"))
    os.remove(path)

    with pytest.raises(SaveError):
        session.revert()

    assert session.dirty is True
    assert "PRECIOUS" in page_text(session.doc)
    session.close()


def test_reverting_a_clean_document_is_refused(tmp_path):
    path = build_sample_pdf(tmp_path / "doc.pdf")
    session = open_session(path)
    with pytest.raises(SaveError) as exc:
        session.revert()
    assert "no unsaved changes" in str(exc.value).lower()
    session.close()


def test_revert_of_an_encrypted_document_reopens_it_with_the_password(tmp_path):
    path = build_encrypted_pdf(tmp_path / "secret.pdf", "pw-user")
    session = open_session(path, "pw-user")
    session.apply(AddText(0, "GONE"))

    version, _scope = session.revert()

    assert version > 0
    assert "SECRET 1" in page_text(session.doc)
    assert session.doc.permissions == session.encryption.permissions
    session.close()
