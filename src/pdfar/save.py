"""Writing a document out so that a crash costs nothing.

A save is the only place in PDFAR that can destroy work it did not create, so
everything here is arranged around two rules:

1. **The destination is never half-written.**  The bytes go to a temporary file
   in the same directory, are flushed and ``fsync``-ed to disk, and only then
   ``os.replace``-d over the target — an atomic rename within a filesystem.  A
   power cut during a save loses the save, not the document.
2. **Nothing is renamed until the bytes have been read back.**  The produced
   buffer is reopened, its page count checked and a page rendered, so a file
   MuPDF wrote badly is refused while the user's original is still on disk.

The one exception is the incremental save, which by definition appends to the
file the document was opened from and so cannot be staged.  It is used only
while the live document *is* that file handle, which stops being true the moment
an edit is adopted (F10-1) — so the editing path always gets the safe writer,
and the append path is left for in-place changes such as annotations, where
preserving objects MuPDF does not model (signatures above all) is worth it.
"""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from dataclasses import dataclass
from typing import Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz


class SaveError(RuntimeError):
    """A refusal the user can act on.  The message is shown verbatim."""


class ProtectionError(SaveError):
    """The rewrite would have to give up protection PDFAR cannot rebuild."""


def _basename(path: str) -> str:
    return os.path.basename(os.path.abspath(path)) or path


# ------------------------------------------------------------------ encryption
def _method_of(doc: fitz.Document) -> int:
    """The encryption scheme the file came in, as a ``PDF_ENCRYPT_*`` constant.

    MuPDF reports it only as a description string (``metadata["encryption"]``,
    e.g. ``"Standard V5 R6 256-bit AES"``).  Anything unrecognised is written
    back at the strongest setting rather than the weakest: silently downgrading
    a document's protection is the failure this whole module exists to prevent.
    """
    text = (doc.metadata or {}).get("encryption") or ""
    lowered = text.lower()
    if "256" in lowered:
        return fitz.PDF_ENCRYPT_AES_256
    if "128" in lowered:
        return fitz.PDF_ENCRYPT_AES_128 if "aes" in lowered else fitz.PDF_ENCRYPT_RC4_128
    if "40" in lowered:
        return fitz.PDF_ENCRYPT_RC4_40
    return fitz.PDF_ENCRYPT_AES_256


def _new_owner_password() -> str:
    """An owner password for a copy whose original owner password is unknown."""
    return secrets.token_urlsafe(24)


@dataclass(frozen=True)
class Encryption:
    """How the file on disk is protected, captured while we still had it.

    ``DocumentSession`` holds one from the moment the document is opened,
    because it is the only record of it: an edit is applied to a copy serialized
    with ``tobytes()``, and serializing an authenticated document yields
    *decrypted* bytes.  Without this, the first save of an encrypted file would
    quietly remove its password.
    """

    method: int
    permissions: int
    owner_pw: str
    user_pw: str
    authenticated: int

    @classmethod
    def capture(cls, doc: fitz.Document, password: str,
                authenticated: int) -> Optional["Encryption"]:
        """``None`` when the file was not encrypted at all.

        Encryption is read off the document rather than inferred from
        ``needs_pass``, because an owner-password-only file reports
        ``needs_pass == 0`` — it opens without asking for anything — and would
        otherwise be rewritten as a plain, unprotected document.

        ``authenticated`` is what ``Document.authenticate`` answered: 1 or 4 for
        the owner password, 2 for the user password, 0 when no password was
        entered.  Getting those apart matters — an owner-protected document is
        readable by anyone and merely closed to changes, and writing it back as
        a user-protected one would lock the user out of a file they could open
        for free.
        """
        if not (doc.metadata or {}).get("encryption"):
            return None
        permissions = doc.permissions
        method = _method_of(doc)
        if authenticated & 2:
            # Only the user password is known, so the owner password is not
            # recoverable.  It must not be *re-used* either: when the two are
            # equal, typing the user password also satisfies the owner channel
            # and MuPDF resets the permissions to full — the copy would be
            # looser than the original.  A random owner password keeps the
            # restrictions enforced; what it costs is a permission change nobody
            # holding only the user password was entitled to make.
            return cls(method, permissions, _new_owner_password(), password,
                       authenticated)
        if authenticated:
            return cls(method, permissions, password, "", authenticated)
        # Encrypted, and nobody was asked for a password: an owner password we
        # have never seen is what guards changes to this file.
        return cls(method, permissions, "", "", 0)

    @property
    def is_owner_access(self) -> bool:
        return not self.authenticated & 2

    @property
    def reproducible(self) -> bool:
        """Whether a rewrite can rebuild this protection rather than approximate it."""
        return bool(self.owner_pw or self.user_pw)

    @property
    def save_kwargs(self) -> dict:
        """Options that rebuild the protection on a document that has lost it."""
        return {"encryption": self.method,
                "permissions": self.permissions,
                "owner_pw": self.owner_pw,
                "user_pw": self.user_pw}


# ------------------------------------------------------------------- reporting
@dataclass(frozen=True)
class SaveReport:
    path: str
    pages: int
    size: int
    incremental: bool


# -------------------------------------------------------------- staging a file
def _preserve_mode(source: str, destination: str) -> None:
    """Keep the permissions a file had; a fresh temp file is 0600."""
    try:
        mode = stat.S_IMODE(os.stat(source).st_mode)
    except OSError:
        mode = 0o644
    try:
        os.chmod(destination, mode)
    except OSError:
        pass          # a filesystem without permission bits is not our problem


def staged_write(data: bytes, path: str) -> int:
    """Put *data* at *path* so the file is either the old one or the new one.

    Returns the number of bytes written.  Raises :class:`SaveError` with a
    message that names what to do next.
    """
    target = os.path.abspath(path)
    directory = os.path.dirname(target) or "."
    if not os.access(directory, os.W_OK | os.X_OK):
        raise SaveError(f"You do not have permission to write in “{directory}”. "
                        "Use Save As to choose another folder.")
    existing = os.path.exists(target)
    if existing and not os.access(target, os.W_OK):
        # The rename would have succeeded anyway: it needs write permission on
        # the directory, not on the file.  The lock is the user's, so honour it.
        raise SaveError(f"“{_basename(target)}” is read-only. "
                        "Use Save As to keep a copy.")

    handle, tmp = tempfile.mkstemp(prefix=".pdfar-save-", suffix=".pdf",
                                   dir=directory)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if existing:
            _preserve_mode(target, tmp)
        else:
            # A brand-new file gets the mode a normal write would have given it;
            # the 0600 from mkstemp would leave it unreadable to a shared folder.
            try:
                os.chmod(tmp, 0o644)
            except OSError:
                pass
        os.replace(tmp, target)
    except OSError as exc:
        raise SaveError(f"Could not write “{_basename(target)}”: {exc}") from exc
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return len(data)


# ------------------------------------------------------ verifying what we wrote
def _open_for_reading(data_or_path, password: str) -> fitz.Document:
    doc = (fitz.open(stream=data_or_path, filetype="pdf")
           if isinstance(data_or_path, (bytes, bytearray))
           else fitz.open(data_or_path))
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise SaveError("The file cannot be opened with its own password.")
    return doc


def _render_proof(doc: fitz.Document) -> None:
    """Paint the first page: a document that parses but cannot be drawn is broken."""
    try:
        doc.load_page(0).get_pixmap(dpi=36)
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"The new file cannot be rendered: {exc}") from exc


def verify_bytes(data: bytes, expected_pages: int, password: str = "") -> int:
    """Read the produced bytes back the way a viewer would.  Raises SaveError."""
    try:
        doc = _open_for_reading(data, password)
    except SaveError:
        raise
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"The new file is not a readable PDF: {exc}") from exc
    try:
        if doc.page_count != expected_pages:
            raise SaveError(f"The new file has {doc.page_count} pages instead of "
                            f"{expected_pages}; nothing was written.")
        _render_proof(doc)
        return doc.page_count
    finally:
        doc.close()


def verify_file(path: str, expected_pages: int, password: str = "") -> int:
    """The same proof for a file that was written in place (incremental save)."""
    try:
        doc = _open_for_reading(path, password)
    except SaveError:
        raise
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"The saved file is not a readable PDF: {exc}") from exc
    try:
        if doc.page_count != expected_pages:
            raise SaveError(f"The saved file has {doc.page_count} pages instead of "
                            f"{expected_pages}.")
        _render_proof(doc)
        return doc.page_count
    finally:
        doc.close()


# --------------------------------------------------------------------- saving
def _can_append(doc: fitz.Document, target: str) -> bool:
    """Is *doc* still the handle that was opened from *target*?

    ``can_save_incrementally()`` alone is not enough: it answers True for a
    document opened from a buffer, and saving one incrementally then fails with
    ``incremental needs original file``.  After an adopted edit ``doc.name`` is
    ``None``, which is what turns the editing path over to the staged writer.
    """
    name = doc.name
    return (bool(name) and os.path.abspath(name) == target
            and doc.is_dirty and doc.can_save_incrementally())


def _save_incremental(session, doc: fitz.Document, target: str) -> SaveReport:
    """Append to the file the document came from, leaving the old revision intact."""
    try:
        doc.save(target, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"Could not append to “{_basename(target)}”: {exc}") from exc
    pages = verify_file(target, doc.page_count, session.password)
    size = os.path.getsize(target)
    session.mark_saved(target)
    return SaveReport(target, pages, size, True)


def _encryption_options(session, doc: fitz.Document,
                        allow_weakened: bool) -> dict:
    """How to write *doc* so the file keeps the protection the user gave it.

    Three cases, in order of how much MuPDF can do for us: a document that is
    still the file handle knows its own encryption and ``PDF_ENCRYPT_KEEP``
    reproduces it exactly; an adopted copy knows nothing, so the parameters
    captured at open time are replayed; and a document we could never unlock
    cannot be rebuilt at all, which is a question for the user rather than a
    decision for this function.
    """
    encryption = session.encryption
    if encryption is None:
        return {"encryption": fitz.PDF_ENCRYPT_NONE}
    if doc.name:
        return {"encryption": fitz.PDF_ENCRYPT_KEEP}
    if encryption.reproducible:
        return encryption.save_kwargs
    if not allow_weakened:
        raise ProtectionError(
            f"“{_basename(session.path)}” is encrypted with an owner password "
            "that was never entered, so PDFAR cannot rebuild it. The copy can "
            "still be written with the same restrictions and a new owner "
            "password — it will open without a password as before, but the "
            "original owner password will no longer change its permissions.")
    # A fresh owner password rather than an empty one: with both passwords
    # empty, MuPDF writes the permission flags but stops enforcing them, and a
    # document that anyone can re-permission is not the document the user had.
    return {"encryption": encryption.method,
            "permissions": encryption.permissions,
            "owner_pw": _new_owner_password(), "user_pw": ""}


def save_document(session, path: Optional[str] = None, *, full: bool = False,
                  allow_weakened: bool = False) -> Optional[SaveReport]:
    """Write *session*'s live content to a file, or refuse trying.

    ``path`` other than the document's own makes this a Save As, which is always
    a full rewrite: a copy that still carries the previous revision in an
    appended trailer is not a copy, it is the same mistake in a new location.

    Returns ``None`` when there was nothing to write, and calls
    ``session.mark_saved()`` only once the bytes have been proven on disk — so
    the dirty mark cannot lie.  An unmodified document still counts as something
    to write when the user named a different file: Save As is a request for a
    copy, not a request to compare the document with itself.
    """
    if not session.dirty and not path:
        return None
    doc = session.doc
    own_path = os.path.abspath(session.path) if session.path else ""
    target = os.path.abspath(path) if path else own_path
    if not target:
        raise SaveError("This document has no file to save to; use Save As.")
    if target == own_path:
        session.require_saved_file_unchanged()
        if not full and _can_append(doc, target):
            return _save_incremental(session, doc, target)

    # Level 1 frees the bytes of the objects a deleted page leaves behind; level
    # 2 also merges duplicate streams and shrinks the object table, and costs the
    # same (1.40 s against 1.37 s on a 540-page book, 37185 slots written from
    # 38172).  Level 3 then compacts cross references — 213 more slots out of a
    # 25 MB file, for 121 s instead of 1.4 s — so compacting the table is a
    # request of its own, never something a Save sneaks in.
    options = {"garbage": 2, "deflate": True}
    options.update(_encryption_options(session, doc, allow_weakened))
    try:
        data = bytes(doc.tobytes(**options))
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"Could not produce the new file: {exc}") from exc
    verify_bytes(data, doc.page_count, password=session.password)
    try:
        size = staged_write(data, target)
    except SaveError:
        raise
    except Exception as exc:                       # noqa: BLE001 - user-facing
        raise SaveError(f"Could not write “{_basename(target)}”: {exc}") from exc
    session.mark_saved(target)
    return SaveReport(target, doc.page_count, size, False)
