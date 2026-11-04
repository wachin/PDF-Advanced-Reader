"""F10-4x: a document that forbids assembly does not let PDFAR change its pages.

ISO 32000-1 §7.6.4.2 Table 22 bit 11 is the right to assemble the document —
"insert, rotate or delete pages and create bookmarks", as PdfCraft quotes it
(`crates/engine/src/edit.rs:891`) — and bit 4 is the older right to modify the
contents, which covers the same ground in a file written before bit 11 existed.  A
PDF that clears both and keeps an owner password is what a producer writes when the
sheet order is the thing to protect, and PDFAR has to answer it for one reason above
all: **MuPDF does not.**  Measured on a user-password file with both bits cleared,
``delete_page``, ``move_page`` and ``set_toc`` all go through on the authenticated
handle, the result saves, and the only thing that said no was a number in the
encryption dictionary nobody read.  PdfCraft reads it: ``check_permission``
(`crates/engine/src/edit.rs:878-905`) puts every page-tree edit under ``assemble()``
and ``Document::allows_assembly`` (`crates/engine/src/document.rs:353-354`) is the
predicate the command palette and the organize panel both ask.

What was measured about *where* the answer can live:

* an authenticated handle reports the granted mask in ``Document.permissions`` — as a
  **negative** integer, the mask with its high bits set — and an unencrypted document
  reports ``-4``, every right, so a bit test needs no special case for a plain book;
* **the owner password lifts the restriction by itself**: the same file typed past the
  user password gives ``-3884``, typed past the owner password gives ``-4``.  MuPDF
  does that, so PDFAR has nothing to add — PdfCraft models the same fact inside their
  own parser (``bit()`` is ``self.owner || ...``, `crates/crypt/src/lib.rs:96-98`);
* an **owner-password-only** file reports ``needs_pass == 0``, opens with nobody
  typing anything, and still reports the restricted ``/P``.  PDFAR's
  :attr:`Encryption.is_owner_access` is true of that state, because the flag records
  which password was *used*, not which rights follow — so the gate reads the bits;
* and the live document is not a source the gate can trust: the working copy is
  normally a file copy that keeps its encryption and answers the truth, but when there
  is no room for revisions it is ``Document.tobytes()`` — **decrypted** — and answers
  ``-4`` to everything.  Only :attr:`DocumentSession.encryption`, captured at open,
  says the same thing on both paths (which is already why it exists, F10-2).
"""

import os
import tempfile as _tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest

from conftest import build_sample_pdf, make_session
from pdfar.edits import EditError, SetMetadata
from pdfar.main import MainWindow
from pdfar.pages import (DeletePages, InsertBlankPage, MovePages, ReversePages,
                         RotatePages, SettlePageTree)
from pdfar.save import Encryption

PAGES = 6
READER = "reader"
OWNER = "someone-elses-password"
ASSEMBLE = fitz.PDF_PERM_ASSEMBLE
MODIFY = fitz.PDF_PERM_MODIFY
#: What a producer who wants the sheet order kept tends to grant: read it, print it,
#: copy from it — and touch nothing about its pages.
PRINT_AND_COPY = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
#: The whole of what a refusal says, pinned as a literal rather than imported: the
#: sentence is the only feedback a refused edit gives, and F10-4z puts the same words in
#: the tooltip of the item it greys instead of refusing.
REFUSAL = "This document's permissions do not allow changing its pages"


def protected_pdf(path, permissions, *, user=READER, owner=OWNER, pages=PAGES):
    """The book, encrypted so that typing *user* grants exactly *permissions*.

    Built by re-saving the plain fixture, so what reaches the session is a file a
    producer wrote rather than one MuPDF is still holding open.
    """
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    staged = path + ".enc"
    doc.save(staged, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner,
             user_pw=user, permissions=permissions)
    doc.close()
    os.replace(staged, path)
    return path


def the_page_tree_edits():
    """One of each thing a reader can ask the page tree to do."""
    return (DeletePages(pages=(1,)), MovePages(pages=(0,), target=3),
            ReversePages(pages=(2, 4)), RotatePages(pages=(0,), delta=90),
            InsertBlankPage(at=1))


def a_title_change():
    """A step that touches no page — which is what the gate is *not* about."""
    return SetMetadata(entries=(("title", "still mine"),))


def first_lines(session):
    """The sheets, named by what is printed on them, in the order they stand."""
    return [session.doc.load_page(n).get_text().splitlines()[0]
            for n in range(session.doc.page_count)]


# ---------------------------------------------------------------- what is recorded


def test_the_session_records_the_restriction_it_opened_with(tmp_path):
    """The capture carries the file's answer, and it is readable before any edit.

    ``-3884`` rather than ``0``: MuPDF hands back the granted mask with the high bits
    set, so asserting the value says what "no assembly" looks like in that
    representation before anything tests it.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        encryption = session.encryption
        assert encryption is not None
        assert encryption.permissions == -3884, hex(encryption.permissions)
        assert not encryption.permissions & ASSEMBLE
        assert not encryption.permissions & MODIFY
        assert not encryption.allows_assembly
    finally:
        session.close()


def test_the_working_copy_loses_the_protection_when_there_is_no_room_for_revisions(
        tmp_path, monkeypatch):
    """Why the gate reads the capture and not the document in front of it.

    The normal path edits a file copy, which keeps its encryption and answers the
    restriction honestly — measured, ``session.doc.permissions`` is ``-3884`` here too.
    The fallback path is a ``tobytes()`` buffer, and serializing an authenticated
    document yields *decrypted* bytes: measured, the adopted copy answers ``-4``, every
    right, for a file that grants three of twelve.  A check on the live document would
    pass there, and pass quietly.
    """
    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(_tempfile, "mkdtemp", refuse)
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        assert not session.doc.permissions & ASSEMBLE, "the file's own answer, so far"
        session.apply(a_title_change())
        assert session.doc.permissions & ASSEMBLE, "the copy has lost its protection"
        assert not session.encryption.permissions & ASSEMBLE, "the capture has not"
        assert not session.encryption.allows_assembly
        # The document in front of the gate now says every right is granted, and the
        # answer the reader gets has to come from the record rather than from it.
        with pytest.raises(EditError) as caught:
            session.apply(DeletePages(pages=(1,)))
        assert str(caught.value) == REFUSAL
        assert session.doc.page_count == PAGES
    finally:
        session.close()


def test_an_owner_password_lifts_the_restriction_before_anything_is_asked(tmp_path):
    """MuPDF grants an authenticated owner every right, so the bits already say yes.

    One file, two passwords, one session call each: ``-3884`` for the reader and
    ``-4`` for the owner.  PdfCraft keeps that rule in their own permission struct and
    ORs it into every bit they read; here the library has done it before PDFAR is
    asked, which is why nothing below mentions ``authenticated`` at all.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, OWNER)
    try:
        assert session.encryption.permissions == -4, session.encryption.permissions
        assert session.encryption.allows_assembly
        session.apply(DeletePages(pages=(1,)))
        assert session.doc.page_count == PAGES - 1
    finally:
        session.close()


def test_a_file_locked_only_by_its_owner_password_still_refuses_the_cut(tmp_path):
    """The state ``is_owner_access`` calls owner access is no freedom to edit.

    An owner-password-only file asks nothing when it is opened — ``needs_pass`` is 0 —
    so nobody authenticates, ``authenticated`` is 0, and :attr:`Encryption.is_owner_access`
    is true of it.  MuPDF still reports the restricted ``/P``, and that is what a page
    edit has to be measured against: a gate written on the flag would hand every such
    file — the common kind, a report sealed so it cannot be re-ordered — a page tree it
    was written to protect.
    """
    path = protected_pdf(str(tmp_path / "owner-only.pdf"), PRINT_AND_COPY,
                         user="", owner=OWNER)
    with fitz.open(path) as probe:
        assert not probe.needs_pass, "the fixture is the kind that opens unasked"
    session = make_session(path, "")
    try:
        assert session.encryption.is_owner_access, "and PDFAR records it as such"
        assert not session.encryption.allows_assembly
        with pytest.raises(EditError) as caught:
            session.apply(DeletePages(pages=(1,)))
        assert str(caught.value) == REFUSAL
        assert session.doc.page_count == PAGES
    finally:
        session.close()


# ------------------------------------------------------------------ the refusals


def test_a_book_that_forbids_assembly_refuses_every_page_tree_edit(tmp_path):
    """Four edits, one refusal each — and MuPDF would have let all four through.

    Measured before this case existed, on the authenticated handle: ``delete_page``,
    ``move_page`` and ``set_toc`` all succeed with bit 11 and bit 4 cleared, so none
    of what follows is a courtesy the library was already paying.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        for edit in the_page_tree_edits():
            with pytest.raises(EditError) as caught:
                session.apply(edit)
            assert str(caught.value) == REFUSAL, edit
    finally:
        session.close()


def test_the_repair_of_the_page_tree_is_asked_too(tmp_path):
    """``SettlePageTree`` rewrites the tree itself, so it is not a free step.

    Every reorder calls it inside its own ``apply``, which the gate has refused for
    them by then — but it is an :class:`Edit` with a label and callers of its own,
    and a sealed book has to answer for it the same way.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        with pytest.raises(EditError) as caught:
            session.apply(SettlePageTree())
        assert str(caught.value) == REFUSAL
    finally:
        session.close()


def test_a_refused_page_edit_leaves_the_session_exactly_as_it_was(tmp_path):
    """The reader asked, the answer was no, and nothing else happened.

    No version, no undo step, no published snapshot, no revision file, the same sheets
    in the same order — a *rejection*, not a step that failed halfway, which is what
    the gate running before the working copy is opened buys.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        before = first_lines(session)
        for edit in the_page_tree_edits():
            with pytest.raises(EditError):
                session.apply(edit)
        assert session.version == 0
        assert session.history.depth == 0
        assert session.snapshot() is None, "nothing was published"
        assert session._revision is None, "nothing was written to the store"
        assert not session.dirty
        assert first_lines(session) == before
        with pytest.raises(EditError) as caught:
            session.undo()
        assert str(caught.value) == "Nothing to undo"
    finally:
        session.close()


def test_the_refusal_is_a_sentence_the_status_bar_can_finish(tmp_path):
    """``PDFView.apply_edit`` shows ``f"{edit.label}: {exc}"``, so the reason stands
    alone — and is asserted whole because it is the only feedback a refusal gives.

    A reader who is told "Delete Pages failed" looks for a bug; told it is the
    document's own permissions, they look for the password or the producer.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        with pytest.raises(EditError) as caught:
            session.apply(DeletePages(pages=(2,)))
        assert str(caught.value) == REFUSAL
    finally:
        session.close()


def test_a_book_that_forbids_assembly_still_takes_a_title(tmp_path):
    """The gate is about the page tree, not a read-only mode for the whole file.

    PdfCraft's Table 22 dispatch puts metadata under one branch and annotations under
    another (`crates/engine/src/edit.rs:890-925`), each with its own bit; refusing a
    title here would add a restriction the producer did not write — and would make
    every encrypted document read-only in practice, which is a larger decision than
    this one.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    session = make_session(path, READER)
    try:
        session.apply(a_title_change())
        assert session.doc.metadata["title"] == "still mine"
    finally:
        session.close()


# -------------------------------------------------------------- the two granted bits


def test_assembly_granted_by_itself_lets_the_pages_move(tmp_path):
    """Bit 11 alone is the right the page tree needs — bit 4 is not asked for."""
    path = protected_pdf(str(tmp_path / "assemble.pdf"), PRINT_AND_COPY | ASSEMBLE)
    session = make_session(path, READER)
    try:
        assert session.encryption.allows_assembly
        session.apply(DeletePages(pages=(1,)))
        assert session.doc.page_count == PAGES - 1
    finally:
        session.close()


def test_modify_granted_covers_a_book_that_never_heard_of_assembly(tmp_path):
    """Bit 4 is the fallback, and it is the spec's rather than a licence to be generous.

    Table 22 adds bit 11 in PDF 1.4, so a file written before that grants modification
    and says nothing about assembly, and PdfCraft reads exactly that way —
    ``assemble()`` is ``self.bit(11) || self.bit(4)``
    (`crates/crypt/src/lib.rs:116-118`).  Refusing here would lock an old file out of
    an edit its own producer allowed.
    """
    path = protected_pdf(str(tmp_path / "old.pdf"), PRINT_AND_COPY | MODIFY)
    session = make_session(path, READER)
    try:
        assert not session.encryption.permissions & ASSEMBLE, "the file is silent"
        assert session.encryption.allows_assembly, "and bit 4 answers for it"
        for edit in the_page_tree_edits():
            session.apply(edit)
        assert session.history.depth == len(the_page_tree_edits())
    finally:
        session.close()


def test_an_unencrypted_book_is_never_asked(tmp_path):
    """No ``/Encrypt``, no question.

    MuPDF reports ``-4`` for such a file, so the bit test would pass it by accident;
    this pins that the None case is held on purpose rather than inferred from a mask,
    because a rewrite that "simplifies" the check to ``permissions`` alone would crash
    on the plain book every other test in this repository uses.
    """
    path = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    session = make_session(path)
    try:
        assert session.encryption is None
        for edit in the_page_tree_edits():
            session.apply(edit)
        assert session.history.depth == len(the_page_tree_edits())
    finally:
        session.close()


# -------------------------------------------------------------------- the predicate


def test_the_permission_read_is_a_mask_and_the_rule_is_three_lines(tmp_path):
    """``Encryption.allows_assembly`` holds the whole rule, so it can be read alone.

    Built from hand rather than from a file: the masks the cases above measure off MuPDF,
    plus ``-1`` — the mask with no bit cleared, every right granted.  MuPDF was never seen
    to answer it (an unencrypted document reports ``-4``), but the predicate is a bit test
    over a whole word and has to be read on the whole word: the failure mode of guessing
    no is a reader locked out of their own file, with no way to ask why.
    """
    def granted(mask):
        return Encryption(fitz.PDF_ENCRYPT_AES_256, mask, OWNER, READER, 2)

    assert not granted(-3884).allows_assembly
    assert granted(-3884 | ASSEMBLE).allows_assembly
    assert granted(-3884 | MODIFY).allows_assembly
    assert granted(-4).allows_assembly
    assert granted(-1).allows_assembly, "all bits set is all rights granted"


# ------------------------------------------------------------------- the window


def test_the_refusal_reaches_the_status_bar(qapp, tmp_path):
    """A refusal nobody can see is a menu item that quietly does nothing.

    The real window, the real session, the real status bar: ``apply_edit`` turns the
    :class:`EditError` into ``f"{edit.label}: {exc}"``, and the tab keeps no dirty mark
    because there is no step to be dirty about — the reader is told, and their document
    is where they left it.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), PRINT_AND_COPY)
    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()
        assert win.open_document(path, password=READER)
        tab = win._active_tab()
        assert not tab.view.apply_edit(DeletePages(pages=(1,)))
        qapp.processEvents()
        assert win.statusBar().currentMessage() == f"Delete Page: {REFUSAL}"
        assert tab.dirty is False
        assert not win.isWindowModified()
        assert tab.view.session.doc.page_count == PAGES
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()
