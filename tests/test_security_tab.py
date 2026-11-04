"""F10-4ab: Document Properties shows all of Table 22, read from the capture.

F10-4x refuses a page-tree edit on a book whose rights withhold assembly, F10-4z greys
the commands, and F10-4aa lifts the refusal for the session that types the password —
and through all three the Security tab still listed four of the twelve bits and said
nothing about the one that matters, so the reader could not see the reason the refusal
quoted.  This tab is also the first *display* surface that has to read the capture
rather than a document handle: the buffered working copy F10-2 falls back to when there
is no room for revisions reports "not encrypted" and every right, and a tab that asked
it would tell the reader of a sealed book that nothing is withheld.  The rows therefore
answer what the **file** grants; the lifted session says so in its own line beside them
rather than rewriting the file's answer — the same asymmetry F10-4aa is built on.

Row names are Table 22's own eight, in bit order (3-6, 9-12); the assemble row carries
:data:`pdfar.edits.ASSEMBLY_NOTE` as its tooltip while the right is withheld, because
that is the sentence the reader has seen in the status bar and could not find.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf
from PyQt6.QtWidgets import QDialog, QFormLayout, QLabel, QMessageBox, QTabWidget

from pdfar.edits import ASSEMBLY_NOTE, SetMetadata
from pdfar.main import MainWindow

PAGES = 6
READER = "reader"
OWNER = "someone-elses-password"
SEALED = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
WHOLE = SEALED | fitz.PDF_PERM_MODIFY | fitz.PDF_PERM_ANNOTATE | \
    fitz.PDF_PERM_FORM | fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_ASSEMBLE | \
    fitz.PDF_PERM_PRINT_HQ
#: The eight rows the tab must name, in the order the standard's table lists them.
RIGHTS = ("Print", "Modify", "Copy", "Annotate", "Fill forms",
          "Accessibility", "Assemble", "High-quality print")


def protected_pdf(path, permissions, *, user=READER, owner=OWNER):
    build_sample_pdf(path, pages=PAGES)
    doc = fitz.open(path)
    staged = path + ".enc"
    doc.save(staged, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=owner,
             user_pw=user, permissions=permissions)
    doc.close()
    os.replace(staged, path)
    return path


@pytest.fixture
def window(qapp, monkeypatch):
    """The real window, with every complaint box answered and none shown for real."""
    for name in ("question", "warning", "critical", "information"):
        monkeypatch.setattr(
            QMessageBox, name,
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Ok))

    win = MainWindow()
    win.resize(1100, 760)
    win.show()
    qapp.processEvents()
    try:
        yield win
    finally:
        win._close_all_tabs(confirm=False)
        win.close()
        win.deleteLater()
        qapp.processEvents()


@pytest.fixture
def security(monkeypatch):
    """Open the properties dialog and read its Security tab as ``row -> (value, tooltip)``."""
    def read(win) -> dict:
        caught = {}

        def fake_exec(dlg):
            tabs = dlg.findChild(QTabWidget)
            index = next(i for i in range(tabs.count())
                         if tabs.tabText(i) == "Security")
            tab = tabs.widget(index)
            form = tab.findChild(QFormLayout)
            for i in range(form.count()):
                label = form.itemAt(i, QFormLayout.ItemRole.LabelRole)
                field = form.itemAt(i, QFormLayout.ItemRole.FieldRole)
                if label is None or field is None:
                    continue
                label_w, field_w = label.widget(), field.widget()
                if isinstance(label_w, QLabel) and isinstance(field_w, QLabel):
                    caught[label_w.text().rstrip(":")] = (field_w.text(),
                                                           field_w.toolTip())
            return 0

        monkeypatch.setattr(QDialog, "exec", fake_exec)
        win._show_properties()
        return caught
    return read


def open_book(win, path, password=""):
    assert win.open_document(path, password=password)
    return win.view.session


# ------------------------------------------------------------------- the rows


def test_a_plain_book_says_there_is_nothing_to_withhold(window, security, tmp_path):
    """No encryption dictionary: one line, and no rights table to misread."""
    path = build_sample_pdf(str(tmp_path / "plain.pdf"), pages=PAGES)
    open_book(window, path)
    rows = security(window)
    assert rows["Encrypted"][0] == "No"
    assert not any(name in rows for name in RIGHTS), \
        "a book that withholds nothing has no rights to list"


def test_a_sealed_book_shows_assemble_as_the_right_it_withholds(window, security,
                                                                tmp_path):
    """The whole point: the reason the refusal quoted, where the reader can find it."""
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    open_book(window, path, READER)
    rows = security(window)
    assert rows["Encrypted"][0] == "Yes"
    assert {name for name in RIGHTS} <= set(rows), sorted(rows)
    assert rows["Print"][0] == "Yes"
    assert rows["Copy"][0] == "Yes"
    for name in ("Modify", "Annotate", "Fill forms", "Accessibility",
                 "Assemble", "High-quality print"):
        assert rows[name][0] == "No", name
    assert ASSEMBLY_NOTE in rows["Assemble"][1], \
        "the withheld right says, in the refusal's own words, what it stops"


def test_a_book_that_grants_everything_says_so_eight_times(window, security, tmp_path):
    """Bit 11 granted: the row answers Yes and stops quoting a refusal nobody made."""
    path = protected_pdf(str(tmp_path / "all.pdf"), WHOLE)
    open_book(window, path, READER)
    rows = security(window)
    assert rows["Encrypted"][0] == "Yes"
    for name in RIGHTS:
        assert rows[name][0] == "Yes", name
    assert ASSEMBLY_NOTE not in rows["Assemble"][1]


# --------------------------------------------------------- the capture, not a handle


def test_the_buffered_copy_cannot_tell_a_sealed_book_is_free(window, security,
                                                             tmp_path, monkeypatch):
    """The display lesson of F10-2/F10-4x, applied to the first viewer of it.

    With no room for revisions the working copy is MuPDF's **decrypted** buffer: asked
    directly it reports not-encrypted and every right.  The tab reads the capture, so
    what the reader sees is what the file grants — not what the bytes in memory happen
    to look like after ``tobytes()``.
    """
    import tempfile as _tempfile

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    monkeypatch.setattr(_tempfile, "mkdtemp", refuse)
    session.apply(SetMetadata(entries=(("title", "BUFFERED"),)))
    monkeypatch.undo()
    assert not session.doc.name, "the working copy is the buffer"
    assert session.doc.permissions != -3884, "and the buffer itself would lie"

    rows = security(window)
    assert rows["Encrypted"][0] == "Yes", "the capture, not the copy"
    assert rows["Assemble"][0] == "No"
    assert rows["Print"][0] == "Yes"


def test_an_owner_only_book_shows_its_forbidden_sheets_though_nobody_typed(
        window, security, tmp_path):
    """``needs_pass`` 0 is not a licence: the file still states what it forbids.

    This is the state F10-4z had to grey for and F10-4aa had to make liftable — the tab
    is where the reader can *see* why the commands are pale without typing anything.
    """
    path = protected_pdf(str(tmp_path / "owneronly.pdf"), SEALED, user="")
    session = open_book(window, path)
    assert session.doc.needs_pass == 0
    rows = security(window)
    assert rows["Encrypted"][0] == "Yes"
    assert rows["Assemble"][0] == "No"
    assert ASSEMBLY_NOTE in rows["Assemble"][1]


# --------------------------------------------------------------------- the lift


def test_a_lifted_session_shows_both_answers(window, security, tmp_path):
    """The F10-4aa asymmetry, drawn: the file's rows unchanged, the session's own line.

    Typing the permissions password changes what this reader may ask, not what the
    producer wrote; the tab keeps the eight answers as the file grants them and says
    the lift beside them, so the reader who lifted a book can still see the mask that
    stayed behind — the thing F10-4aa's own "what is not here" named.
    """
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    assert session.unlock_permissions(OWNER)
    rows = security(window)
    assert rows["Assemble"][0] == "No", "the file's answer, not this session's"
    assert rows["Print"][0] == "Yes"
    lifted = [value for name, (value, _tip) in rows.items()
              if "session" in name.lower()]
    assert lifted, "the tab has to say the lift happened"
    assert any("lifted" in value.lower() for value in lifted), lifted


def test_the_lifting_line_appears_only_where_a_lift_happened(window, security,
                                                             tmp_path):
    """A sealed, unlifted book gets no line about a password nobody typed."""
    path = protected_pdf(str(tmp_path / "sealed.pdf"), SEALED)
    session = open_book(window, path, READER)
    assert not session.encryption.unlocked
    rows = security(window)
    assert not [name for name in rows if "session" in name.lower()], sorted(rows)
