"""F10-4aa: the permissions password lifts the refusal inside the open document.

F10-4x refuses a page-tree edit on a book whose Table 22 rights withhold assembly, and
F10-4z greys the command before the reader asks.  Neither of them has a way *out*: the
reader who owns the file and knows the password that would lift the restriction has to
close the tab and re-open it, and on an **owner-password-only** book — ``needs_pass`` 0,
so nothing was ever typed — there is no box to type it into at all.  PdfCraft has the
out: ``protect.unlock``, "Enter permissions password…"
(``crates/engine/src/commands.rs:376``), offered exactly when ``can_unlock()`` says the
document is restricted and not already opened as owner
(``crates/engine/src/document.rs:442-445``), and ``Session::unlock``
(``crates/engine/src/session.rs:751-777``) authenticates the *owner channel* of a
document that was opened past its user password.  What it flips is a boolean on the
permission record — ``bit()`` is ``self.owner || ...`` (`crates/crypt/src/lib.rs:96-98`)
— so the lifted session grants everything while the file's ``/P`` stays exactly as the
producer wrote it.  That asymmetry is the whole design, and the last two cases here are
what keep it honest: an unlock is for this session, and the bytes on disk are still
sealed.

Measured on PyMuPDF 1.28.2 / MuPDF, all of it in this file's own fixtures:

* ``authenticate`` answers **4** for the owner channel and **2** for the user channel,
  and the same handle may be authenticated twice — 4 after 2 lifts it;
* the mask follows: ``-3884`` (print, copy) for a reader who typed the open password,
  ``-4`` (everything) once the permissions password is typed on top of it, which is why
  PdfCraft's model and MuPDF's agree about there being one boolean to set;
* **a failed attempt re-seals the handle it was made on**: ``authenticate("wrong")`` on
  a document that had been lifted answers 0 *and takes the rights back*.  So the
  password is checked on a throwaway handle of the file and only then applied; a reader
  who mistypes must not lose the unlock they already had;
* a save after an unlock — incremental with ``PDF_ENCRYPT_KEEP``, or a full rewrite
  replaying the capture — still writes ``/P -3884``, and the owner password still opens
  the result as owner.  Lifting is not de-protecting.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session
from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox

from pdfar.edits import ASSEMBLY_NOTE, EditError, SetMetadata
from pdfar.main import MainWindow
from pdfar.pages import RotatePages
from pdfar.save import (ASSEMBLY_RIGHTS, DEFINED_RIGHTS, Encryption,
                        ProtectionError, SaveReport, save_document)

PAGES = 6
READER = "reader"
OWNER = "someone-elses-password"
#: Read it, print it, copy from it — and touch nothing about the sheets.
SEALED = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
#: The same book with bit 11 granted: nothing is withheld from it.
WHOLE = SEALED | fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | \
    fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_ASSEMBLE | \
    fitz.PDF_PERM_PRINT_HQ
#: What the sealed book's mask looks like from the user password (F10-4x).
SEALED_MASK = -3884
PAGE_ACTIONS = ("act_page_rotl", "act_page_rotr", "act_page_delete",
                "act_page_insert", "act_page_duplicate")
WINDOW_WIDTH = 1100
WINDOW_HEIGHT = 760


def protected_pdf(path, permissions, *, user=READER, owner=OWNER, pages=PAGES):
    """The book, encrypted so that the owner password lifts *permissions*."""
    build_sample_pdf(path, pages=pages)
    doc = fitz.open(path)
    staged = path + ".enc"
    doc.save(staged, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner,
             user_pw=user, permissions=permissions)
    doc.close()
    os.replace(staged, path)
    return path


def open_as(path, password=""):
    """A session over *path*, typed past *password* the way the reader would."""
    return make_session(path, password)


def mask(doc) -> int:
    return doc.permissions


def granted(doc) -> set:
    """Which of Table 22's rights *doc* reports, read the way PyMuPDF means it."""
    bits = [("print", fitz.PDF_PERM_PRINT), ("modify", fitz.PDF_PERM_MODIFY),
            ("copy", fitz.PDF_PERM_COPY), ("annotate", fitz.PDF_PERM_ANNOTATE),
            ("form", fitz.PDF_PERM_FORM),
            ("access", fitz.PDF_PERM_ACCESSIBILITY),
            ("assemble", fitz.PDF_PERM_ASSEMBLE),
            ("print_hq", fitz.PDF_PERM_PRINT_HQ)]
    return {name for name, bit in bits if doc.permissions & bit}


# ----------------------------------------------------------------- the predicate


def test_a_plain_book_has_nothing_to_lift(tmp_path):
    """No encryption dictionary: the question does not arise.

    ``encryption is None`` is a book with no rights withheld from it, so there is no
    password that could change anything and the command has to stay out of the menu.
    """
    path = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    session = open_as(path)
    try:
        assert session.encryption is None
        assert session.can_unlock is False
        assert session.allows_assembly
        # Answering True keeps the promise honest: the rights the reader has are the
        # rights the book grants, and that was already the case before the ask.
        assert session.unlock_permissions(OWNER) is True
        assert session.encryption is None
    finally:
        session.close()


def test_a_book_that_grants_everything_it_lists_has_nothing_to_lift(tmp_path):
    """The other half of ``can_unlock``: every bit set is not a restriction."""
    path = protected_pdf(str(tmp_path / "all.pdf"), WHOLE)
    session = open_as(path, READER)
    try:
        assert granted(session.doc) >= {"assemble", "modify", "annotate"}
        assert session.encryption is not None
        assert session.encryption.grants_everything
        assert session.can_unlock is False
        assert session.unlock_permissions(OWNER) is True
        assert session.encryption.unlocked is False, "nothing was needed"
    finally:
        session.close()


def test_a_file_that_moved_cannot_be_checked_and_the_book_stays_as_it_was(tmp_path):
    """The password lives in the file, so a missing file is not a wrong answer.

    The reader can still be working in a document whose file someone has moved or
    deleted — PDFAR notices that at save time, not before.  Asking a permissions password
    of it then says *nothing was lifted* rather than raising through the dialog, and the
    session they are in is untouched: same handle, same rights, same refusal.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.can_unlock
        os.remove(path)
        assert session.unlock_permissions(OWNER) is False
        assert session.encryption.unlocked is False
        assert mask(session.doc) == SEALED_MASK
        with pytest.raises(EditError):
            session.apply(RotatePages(pages=(0,), delta=90))
    finally:
        session.close()


# ------------------------------------------------------------------- the answers


def test_the_open_password_is_not_the_permissions_password(tmp_path):
    """Two channels, two answers: 2 is the door it read, not the one it needs.

    MuPDF says so (``authenticate`` 2 for the user channel), and the mask agrees — a
    book lifted by its open password is still sealed.  The reader who types the password
    they were given to *read* the file is told nothing was lifted; they are not told the
    password was wrong, because it was not.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert not session.allows_assembly
        assert session.unlock_permissions(READER) is False
        assert granted(session.doc) == {"print", "copy"}, "and nothing was lifted"
        assert session.encryption.unlocked is False
        with pytest.raises(EditError) as refused:
            session.apply(RotatePages(pages=(0,), delta=90))
        assert str(refused.value) == ASSEMBLY_NOTE
    finally:
        session.close()


def test_a_wrong_password_lifts_nothing_and_touches_nothing(tmp_path):
    """The capture is the gate's only record, so a failed ask cannot change it.

    The live handle is the reason this is a real risk rather than a formality:
    ``authenticate("wrong")`` answers 0 and takes the rights *back* from a document that
    had been lifted.  The password is therefore checked on a throwaway handle of the
    file, and the handle the reader is looking at is never asked.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        before = session.encryption
        handle_mask = mask(session.doc)
        assert session.unlock_permissions("not-it") is False
        assert session.encryption is before
        assert session.encryption.unlocked is False
        assert session.encryption.owner_pw != OWNER, "the real one was never stored"
        assert mask(session.doc) == handle_mask == SEALED_MASK
    finally:
        session.close()


def test_the_permissions_password_opens_the_gate(tmp_path):
    """The whole of the feature in one line: the same edit, accepted.

    Nothing about the document changed — same handle, same bytes, same page count — so
    no cache, no thumbnail and no reader's place has to be rebuilt for the command to
    be worth having.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.can_unlock
        assert session.unlock_permissions(OWNER) is True
        assert session.allows_assembly
        assert session.can_unlock is False, "there is nothing left to lift"
        session.apply(RotatePages(pages=(0,), delta=90))
        assert session.dirty
        assert session.doc.load_page(0).rotation == 90
    finally:
        session.close()


def test_an_unlock_does_not_rewrite_what_the_file_grants(tmp_path):
    """The boolean moves; the mask the producer wrote does not.

    PdfCraft's ``Permissions`` keeps ``bits`` and adds ``owner``
    (``crates/crypt/src/lib.rs:90-98``), and this is the same split: what the *file*
    grants is a fact about the file, and typing a password into a viewer cannot change
    it.  Only :attr:`Encryption.owner_pw` gets better known, which is what makes the
    next save able to rebuild the protection instead of approximating it.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.encryption.permissions == SEALED_MASK
        owner_pw_before = session.encryption.owner_pw
        assert session.unlock_permissions(OWNER) is True
        encryption = session.encryption
        assert encryption.permissions == SEALED_MASK, "the /P the file carries"
        assert encryption.user_pw == READER
        assert encryption.owner_pw == OWNER != owner_pw_before
        assert encryption.unlocked and encryption.allows_assembly
        assert encryption.grants_everything
        assert granted(session.doc) >= {"assemble", "modify"}, "the handle agrees"
    finally:
        session.close()


def test_a_mistype_after_an_unlock_costs_the_reader_nothing(tmp_path):
    """Losing the unlock to a typo is the failure this design exists to avoid.

    Measured: on a document this session had lifted, ``authenticate("wrong")`` answers 0
    **and takes the rights back** — and so does ``authenticate(<the open password>)``,
    which is a real password for a different door.  A second ask after a lift therefore
    returns ``True`` (the reader's rights do cover the document, which is the promise the
    method makes) without ever putting the question to the live handle.  The last line is
    the point: the reader fumbled the box and is still able to work.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.unlock_permissions(OWNER) is True
        lifted = granted(session.doc)
        assert "assemble" in lifted
        for mistype in (READER, "nope", "", OWNER):
            assert session.unlock_permissions(mistype) is True
            assert session.encryption.unlocked
            assert granted(session.doc) == lifted, repr(mistype)
        before = session.doc.load_page(1).rotation
        session.apply(RotatePages(pages=(1,), delta=-90))
        assert session.doc.load_page(1).rotation != before
    finally:
        session.close()


def test_an_unlocked_book_saves_a_file_that_is_still_sealed(tmp_path):
    """The guarantee the whole design rests on, checked on the bytes.

    MuPDF is measured here rather than trusted: an edit applied after a lift and written
    back to the reader's own file — incrementally, ``PDF_ENCRYPT_KEEP``, the path a
    save-without-a-name-change takes — still carries ``/P -3884``, so the next reader
    gets the restrictions the producer asked for.  An unlock is a session, not an
    edit to the security.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.unlock_permissions(OWNER) is True
        session.apply(RotatePages(pages=(0,), delta=90))
        report = save_document(session)
        assert isinstance(report, SaveReport)
        assert not session.dirty
    finally:
        session.close()

    reopened = open_as(path, READER)
    try:
        assert reopened.encryption.permissions == SEALED_MASK
        assert granted(reopened.doc) == {"print", "copy"}
        assert not reopened.allows_assembly, "the next reader is refused again"
        assert reopened.can_unlock, "and can lift it the same way"
        assert reopened.doc.load_page(0).rotation == 90, "the edit is on disk"
    finally:
        reopened.close()


def test_an_owner_only_book_stops_being_unsavable_once_it_is_lifted(tmp_path,
                                                                   monkeypatch):
    """The second thing the password buys: a copy PDFAR can actually write.

    A book sealed with only an owner password opens unasked, so PDFAR never saw the
    password and cannot rebuild the protection — which is the question
    :func:`save_document` refuses to answer without ``allow_weakened``, and why the
    weakened write replaces the owner password with a random one.  Typing it turns that
    into an ordinary save that keeps the real passwords, and the file that comes out is
    still the one that opens without asking and still forbids everything about the sheets.

    The refusal needs a working copy with no file behind it — the buffered fallback
    F10-2 built for a disk with no room — so this pins it the way ``test_save.py`` does,
    by refusing ``mkdtemp``.
    """
    import tempfile as _tempfile

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    path = protected_pdf(str(tmp_path / "owneronly.pdf"), SEALED, user="")
    session = open_as(path)
    try:
        assert session.encryption is not None
        assert not session.encryption.reproducible
        assert session.can_unlock
        monkeypatch.setattr(_tempfile, "mkdtemp", refuse)
        # A title touches no page, so the sealed book still lets it through (F10-4x).
        session.apply(SetMetadata(entries=(("title", "LIFTED"),)))
        with pytest.raises(ProtectionError):
            save_document(session)
        monkeypatch.undo()

        assert session.unlock_permissions(OWNER) is True
        assert session.encryption.reproducible
        report = save_document(session)
        assert isinstance(report, SaveReport)
    finally:
        session.close()

    reopened = fitz.open(path)
    try:
        assert reopened.needs_pass == 0, "it still opens without a password"
        assert granted(reopened) == {"print", "copy"}, "and still forbids the sheets"
        assert reopened.authenticate(OWNER) & 4, "the owner password is the real one"
        assert (reopened.metadata or {}).get("title") == "LIFTED"
    finally:
        reopened.close()


def test_an_unlock_survives_a_revert(tmp_path):
    """Revert abandons the edits, not the password the reader typed.

    The file on disk was never unsealed, so a handle opened with only the open password
    would report the sealed mask again — and the gate stays open all the same, because
    what it reads is the capture, which still holds the password the reader just typed.
    Revoking an unlock at revert would ask the human to re-type something nothing had
    taken away.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.unlock_permissions(OWNER) is True
        session.apply(RotatePages(pages=(0,), delta=90))
        session.revert()
        assert not session.dirty
        assert session.encryption.unlocked
        assert session.allows_assembly
        assert not session.can_unlock
        assert granted(session.doc) >= {"assemble"}, "and so does the reopened handle"
        session.apply(RotatePages(pages=(2,), delta=90))
        assert session.doc.load_page(2).rotation == 90
    finally:
        session.close()


def test_a_lifted_session_opens_every_copy_with_the_password_it_holds(tmp_path):
    """An unlock that fell over at the next edit would be a lie in the menu.

    Every working copy is a fresh MuPDF handle, authenticated with whatever the session
    knows, and PdfCraft puts the same answer to the states its history keeps
    (`crates/engine/src/session.rs:769-775`).  So the revision this edit is adopted from
    is opened past the owner password, and the handle the reader is looking at says the
    same thing the record does — instead of going back to ``-3884`` one step later.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        assert session.unlock_permissions(OWNER) is True
        assert granted(session.doc) >= {"assemble"}
        session.apply(RotatePages(pages=(0,), delta=90))
        assert session.doc.name, "a copy adopted from a revision file"
        assert granted(session.doc) >= {"assemble"}, "and it came in lifted"
    finally:
        session.close()


def test_a_sealed_book_is_refused_even_when_its_handle_grants_everything(
        tmp_path, monkeypatch):
    """Why the gate reads the capture and the password is asked of the **file**.

    With no room for a revision file the session works in MuPDF's **decrypted** buffer
    (F10-2), and a decrypted document reports ``-4`` — every right — for a file that
    grants two.  Ask that handle what it allows and the refusal disappears along with
    the producer's restrictions.  Here the buffer says everything, the capture says
    sealed, and what the reader gets is the sentence.

    The same buffer is why ``unlock_permissions`` does not ask the live handle: measured,
    an unencrypted document answers ``1`` — success — to *every* password, so
    ``authenticate("nope")`` on it would read as the owner stepping up.  The file behind
    the session is the only thing that knows the real answer, and it gives it here.
    """
    import tempfile as _tempfile

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_as(path, READER)
    try:
        monkeypatch.setattr(_tempfile, "mkdtemp", refuse)
        session.apply(SetMetadata(entries=(("title", "BUFFERED"),)))
        monkeypatch.undo()
        assert not session.doc.name, "the buffered copy has no file behind it"
        assert mask(session.doc) != SEALED_MASK, "and it claims every right"
        assert session.doc.authenticate("nope") == 1, "asked directly, it accepts all"
        with pytest.raises(EditError) as refused:
            session.apply(RotatePages(pages=(0,), delta=90))
        assert str(refused.value) == ASSEMBLY_NOTE
        assert session.unlock_permissions("nope") is False, "the file was asked"
        assert not session.encryption.unlocked
        # The same buffer, lifted by its password: the capture moves, the edit goes in.
        assert session.unlock_permissions(OWNER) is True
        session.apply(RotatePages(pages=(0,), delta=90))
        assert session.doc.load_page(0).rotation == 90
    finally:
        session.close()


# ------------------------------------------------------------------ Encryption


def test_an_encryption_record_can_be_lifted_without_being_rewritten():
    """The unit of it: one boolean, the mask kept, the password remembered."""
    sealed = Encryption(fitz.PDF_ENCRYPT_AES_256, SEALED_MASK, "", READER, 2)
    assert sealed.allows_assembly is False
    assert sealed.grants_everything is False
    lifted = sealed.with_owner_password(OWNER)
    assert lifted.permissions == sealed.permissions
    assert lifted.user_pw == READER
    assert lifted.owner_pw == OWNER
    assert lifted.authenticated == 2, "how the file was opened is history"
    assert lifted.allows_assembly and lifted.grants_everything
    assert sealed.allows_assembly is False, "and the original is untouched"
    assert Encryption(fitz.PDF_ENCRYPT_AES_256, -4, "", "", 0).grants_everything


def test_the_defined_rights_are_the_rights_the_standard_names():
    """``0b1111_0011_1100`` is the eight bits PyMuPDF names, and nothing else.

    :data:`DEFINED_RIGHTS` is written as a literal the way PdfCraft writes it, so this is
    where the arithmetic is checked against the library's own constants: a bit added,
    dropped or renumbered fails here rather than turning a restricted book into an
    unrestricted one.
    """
    assert DEFINED_RIGHTS == (fitz.PDF_PERM_PRINT | fitz.PDF_PERM_MODIFY
                              | fitz.PDF_PERM_COPY | fitz.PDF_PERM_ANNOTATE
                              | fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY
                              | fitz.PDF_PERM_ASSEMBLE | fitz.PDF_PERM_PRINT_HQ)
    assert DEFINED_RIGHTS == 3900
    assert ASSEMBLY_RIGHTS & DEFINED_RIGHTS == ASSEMBLY_RIGHTS


# ------------------------------------------------------------------------ the UI


@pytest.fixture
def answers(monkeypatch):
    """Answer the boxes a dirty document raises, and record the complaints."""
    complained: list = []

    def question(_parent, _title, _text, buttons, *rest):
        if buttons & QMessageBox.StandardButton.Save:
            return QMessageBox.StandardButton.Discard
        return QMessageBox.StandardButton.Yes

    def warn(*a):
        complained.append(a[1:3])
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    for name in ("warning", "critical", "information"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(warn))
    return complained


@pytest.fixture
def box(monkeypatch, answers):
    """Control the password box: the answer it hands back, and what it was asked."""
    state = {"answer": (OWNER, True), "asked": []}

    def get_text(_parent, title, label, echo=QLineEdit.EchoMode.Normal, *rest):
        state["asked"].append((title, label, echo))
        return state["answer"]

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(get_text))
    return state


@pytest.fixture
def window(qapp, answers):
    """The real main window, with documents opened through the password path."""
    win = MainWindow()
    win.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


def open_book(window, path, password=""):
    assert window.open_document(path, password=password)
    window._update_zoom_actions(window.view.reflow_mode)
    return window.view.session


def page_actions_enabled(window) -> dict:
    return {name: getattr(window, name).isEnabled() for name in PAGE_ACTIONS}


def test_the_command_is_offered_only_where_it_would_do_something(window, box, qapp,
                                                                tmp_path):
    """Plain: no.  Sealed: yes.  Lifted: no again, because nothing is left to lift.

    The item is the answer to ``can_unlock``, so it cannot promise a box for a document
    that has no restrictions and cannot keep promising one once they are lifted — and it
    belongs to the document in the current tab, not to the tab that last had it.
    """
    plain = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    sealed = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)

    assert window.act_unlock_permissions.isEnabled() is False, "nothing is open"
    open_book(window, plain)
    assert window.act_unlock_permissions.isEnabled() is False

    session = open_book(window, sealed, READER)
    qapp.processEvents()
    assert window.act_unlock_permissions.isEnabled()

    window._unlock_permissions()
    qapp.processEvents()
    assert session.encryption.unlocked
    assert window.act_unlock_permissions.isEnabled() is False

    window.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert window.act_unlock_permissions.isEnabled() is False


def test_the_lifted_items_come_back_and_the_box_names_the_book(window, box, qapp,
                                                              tmp_path):
    """F10-4z's grey items and this command read the one predicate.

    So the menu needs no rebuild: the strip assembles its page commands per right click
    and asks ``allows_assembly``, which the unlock has just moved.  The window's three
    actions are the other half — and their tooltips, which that gate wrote, have to come
    back as the sentences the actions were built with, or *Delete Page* would go on
    explaining a restriction the reader has just ended.  The baseline is therefore read
    with a plain book open, exactly as F10-4z learned to read it.
    """
    plain = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    open_book(window, plain)
    design = {name: getattr(window, name).toolTip() for name in PAGE_ACTIONS}
    assert all(tooltip != ASSEMBLY_NOTE for tooltip in design.values())

    session = open_book(window, path, READER)
    qapp.processEvents()
    assert page_actions_enabled(window) == {name: False for name in PAGE_ACTIONS}
    assert all(getattr(window, name).toolTip() == ASSEMBLY_NOTE
               for name in PAGE_ACTIONS)

    window._unlock_permissions()
    qapp.processEvents()

    title, label, echo = box["asked"][-1]
    assert echo == QLineEdit.EchoMode.Password, "a password box, not a text box"
    assert "sealed.pdf" in label, label
    assert session.encryption.unlocked
    assert page_actions_enabled(window) == {name: True for name in PAGE_ACTIONS}
    assert {name: getattr(window, name).toolTip()
            for name in PAGE_ACTIONS} == design

    strip = window.sidebar
    menu = strip._page_commands_menu((1, 3))
    assert all(action.isEnabled() for action in menu.actions()
               if not action.isSeparator())


def test_a_cancelled_box_unlocks_nothing(window, box, answers, qapp, tmp_path):
    """No answer is not a wrong answer: no complaint, no change, box not asked twice."""
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    box["answer"] = ("", False)

    window._unlock_permissions()
    qapp.processEvents()

    assert len(box["asked"]) == 1, "asked once, and once only"
    assert answers == [], "a cancelled box is not an error worth a dialog"
    assert session.encryption.unlocked is False
    assert page_actions_enabled(window) == {name: False for name in PAGE_ACTIONS}
    assert window.act_unlock_permissions.isEnabled()


def test_a_wrong_password_complains_and_still_lets_the_reader_try_again(
        window, box, answers, qapp, tmp_path):
    """The feedback is a box, and the item stays where it was.

    ``QInputDialog`` cannot redraw itself with the error in it, so the answer is a warning
    and another chance — PdfCraft shows the error inside its own modal
    (``crates/ui-egui/src/dialogs.rs:2018-2027``).  What matters is that a mistyped
    password costs the reader nothing: the gate is still open to the right one.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    box["answer"] = ("guess", True)

    window._unlock_permissions()
    qapp.processEvents()

    assert answers, "the reader was told the password did not work"
    assert session.encryption.unlocked is False
    assert session.can_unlock
    assert window.act_unlock_permissions.isEnabled()

    box["answer"] = (OWNER, True)
    window._unlock_permissions()
    qapp.processEvents()
    assert session.encryption.unlocked
    assert not session.can_unlock


def test_the_command_sits_with_the_items_it_turns_back_on(window, box, qapp,
                                                          tmp_path):
    """An action no menu holds is a way out nobody can find.

    The item is added directly after *Delete Page* because it answers the question those
    three greyed entries raise, and it is the **same** `QAction` the enablement refresh
    writes — so the menu route has to be the handler, not a copy of it.  A gate that
    enabled an action nobody could reach, or a menu entry wired to nothing, both fail
    here rather than shipping as a greyed mystery.
    """
    tools = next(menu for menu in window.findChildren(QMenu)
                 if menu.title().replace("&", "") == "Tools")
    names = [action.text() for action in tools.actions() if not action.isSeparator()]
    entry = "Enter Permissions Password…"
    assert entry in names
    assert names.index(entry) == names.index("Delete Page") + 1, names

    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    item = next(action for action in tools.actions() if action.text() == entry)
    assert item is window.act_unlock_permissions
    assert item.isEnabled()

    item.trigger()
    qapp.processEvents()
    assert len(box["asked"]) == 1, "one box, from the menu route"
    assert session.encryption.unlocked


def test_a_save_after_an_unlock_through_the_window_does_not_unseal_the_file(
        window, box, qapp, tmp_path, answers):
    """The reader's own file, rewritten by the real save path, still forbids the sheets.

    End to end, because this is the one place the feature could do damage: an editor that
    lifted a book, turned a page and saved must not hand out an unrestricted copy to
    everyone who opens it afterwards.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    window._unlock_permissions()
    qapp.processEvents()

    window._rotate_pages((0,), 90)
    qapp.processEvents()
    report = save_document(session)
    assert isinstance(report, SaveReport)

    reopened = open_as(path, READER)
    try:
        assert granted(reopened.doc) == {"print", "copy"}
        assert not reopened.allows_assembly
        assert reopened.can_unlock
        assert reopened.doc.load_page(0).rotation == 90
    finally:
        reopened.close()
