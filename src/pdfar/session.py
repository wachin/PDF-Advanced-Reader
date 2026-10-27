"""One authoritative document, stamped with the content it represents.

MuPDF is not thread-safe, so every background reader (render pool, search,
reflow) opens its own ``fitz.Document``.  Opening it *by path* means "the file
as it was last saved", so a change held in memory stayed invisible until a save
reached the disk — the gap described in ``docs/pdfcraft-editing-analysis.md``
(§2, "what you see is what will be saved").  ``DocumentSession`` closes it:

* ``version == 0`` means nothing has been edited, so the file on disk *is* the
  document and no revision is ever made (the fast path the large-PDF benchmark
  measures: the pool keeps opening the path and MuPDF keeps mmap-ing it).
* the first accepted change bumps the version, and background reads then come
  from the session's own *revision file* — :mod:`pdfar.revisions`.

``version`` is also how readers stay honest: a render job pins the version it
was submitted for, and the view drops results for any other version.

F10-1 adds the editing half: :meth:`DocumentSession.apply` runs an
:class:`pdfar.edits.Edit` against a *working copy* and adopts the result only if
the edit returns without raising, so a failed edit cannot be half-applied.
:meth:`undo` / :meth:`redo` walk whole-document snapshots.

F10-3c changes what a snapshot *is*: from the first accepted edit the document
lives in a private directory of revision files, so an edit costs a copy and an
incremental append (measured 55 ms against 1 730 ms for ``tobytes()`` on a
540-page book), undo opens a file instead of holding 25 MB of RAM, and readers
``fitz.open`` the revision rather than parsing a buffer.  Sessions with no file
to seed from — and no writable temp directory — keep the byte snapshots.
"""

from __future__ import annotations

import os
import threading
from typing import Optional, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

from .edits import Edit, EditError, EditHistory, Scope, Snapshot
from .revisions import RevisionStore, RevisionStoreError
from .save import Encryption, SaveError


class SnapshotNotPublished(RuntimeError):
    """A worker asked for content the GUI thread has not serialized yet."""


def _disk_stamp(path: str):
    """What the file looks like right now, for the changed-on-disk check."""
    try:
        info = os.stat(path)
    except OSError:
        return None
    return (info.st_mtime_ns, info.st_size)


class DocumentSession:
    def __init__(self, doc: fitz.Document, path: str, password: str = "",
                 authenticated: int = 0):
        """*authenticated* is what ``Document.authenticate`` answered — 1 or 4
        for an owner password, 2 for a user password, 0 when the file needed
        none.  It is recorded here because it is unrecoverable later: an edit is
        applied to a serialized copy, and serializing an opened encrypted
        document yields *decrypted* bytes, so after the first adoption nothing
        about ``doc`` says it was ever protected (F10-2).
        """
        self.doc = doc
        self.path = path
        self.password = password or ""
        self.version = 0
        self._saved_version = 0
        # Bumped whenever ``self.doc`` becomes a different Document object.
        # ``version`` answers "which content is this?", generation answers
        # "is it the same object?" — caches built from the object itself
        # (annotation references, page handles) must rebind on the latter.
        self.generation = 0
        self.history = EditHistory(on_release=self._release_revision)
        self.encryption = Encryption.capture(doc, self.password, authenticated)
        self._disk_stamp = _disk_stamp(path)
        self._snapshot: Optional[bytes] = None
        # The private directory of revision files, made on the first accepted
        # edit; ``_revision`` is the file ``self.doc`` is reading (F10-3c).
        self._revisions: Optional[RevisionStore] = None
        self._revision: Optional[str] = None
        self._revisions_failed = False
        self._closed = False
        # fitz documents may only be touched by one thread at a time, so
        # serializing happens here and workers read the immutable bytes.
        self._owner_thread = threading.get_ident()

    @property
    def dirty(self) -> bool:
        """The live content is not what the file on disk holds."""
        return self.version != self._saved_version

    def mark_saved(self, path: Optional[str] = None) -> None:
        """The file now holds exactly this content, so the session is clean.

        ``path`` moves where that file is (Save As); omit it when the existing
        file was overwritten.  Only :func:`pdfar.save.save_document` may call
        this, and only after the bytes have been proven to be on disk.
        """
        if path:
            self.path = path
        self._saved_version = self.version
        # Our own write is not a stranger's: re-stamp so the next save can tell
        # the difference between itself and another program.
        self._disk_stamp = _disk_stamp(self.path)

    def require_saved_file_unchanged(self) -> None:
        """Refuse to overwrite a file someone else has changed since we opened it.

        Without this, saving would silently discard their work — and in the
        incremental case the file we append to would not be the one we read.
        """
        if not self.path or self._disk_stamp is None:
            return
        if _disk_stamp(self.path) != self._disk_stamp:
            raise SaveError(
                f"“{os.path.basename(self.path)}” has changed on disk since it "
                "was opened, so nothing was written. Reopen it to see the "
                "current file, or use Save As to keep these changes separately.")

    # ------------------------------------------------------------- ownership
    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise SnapshotNotPublished(
                f"content v{self.version} must be serialized on the thread that "
                "owns the document; call read_version() before starting workers")

    # --------------------------------------------------------------- editing
    def apply(self, edit: Edit) -> Tuple[int, Scope]:
        """Carry *edit* out on a working copy; adopt the copy if it succeeds.

        Returns ``(version, scope)`` — scope is what the caller invalidates on.
        The live document is never handed to the edit, so however far into an
        edit the failure happens, the document is byte-for-byte what it was and
        the undo stack is untouched: the working copy is a separate file (or a
        separate buffer) that is thrown away, and the revision the user was
        looking at is still on disk under the history's hands.
        """
        self._require_owner()
        before = self._before_snapshot()
        clone, staged = self._open_working_copy(before)
        try:
            edit.apply(clone)
            self._persist(clone, staged)
        except EditError:
            self._discard(clone, staged)
            raise
        except Exception as exc:                      # noqa: BLE001 - user-facing
            self._discard(clone, staged)
            raise EditError(f"{edit.label} failed: {exc}") from exc
        self._adopt(clone)
        if staged:
            self._revision = staged
        self.history.record(edit, before)
        return self.version, edit.scope

    def undo(self) -> Tuple[int, Scope]:
        """Step back to the previous document.  Returns ``(version, scope)``."""
        self._require_owner()
        return self._step("undo")

    def redo(self) -> Tuple[int, Scope]:
        """Step forward again.  Returns ``(version, scope)``."""
        self._require_owner()
        return self._step("redo")

    def _step(self, direction: str) -> Tuple[int, Scope]:
        history = self.history
        target = history.undo_target if direction == "undo" else history.redo_target
        if target is None:
            raise EditError(f"Nothing to {direction}")
        leaving = self._before_snapshot()
        # Open the destination *before* touching the history: if MuPDF refuses
        # this snapshot, the stacks still describe the document we are in.
        clone = self._open_snapshot(target.before)
        self._adopt(clone)
        if target.before.path:
            self._revision = target.before.path
        if direction == "undo":
            history.take_undo(leaving)
        else:
            history.take_redo(leaving)
        return self.version, target.scope

    # ---------------------------------------------------------- revisions
    def _ensure_revisions(self) -> Optional[RevisionStore]:
        """The private directory, made on the first accepted edit.

        Seeded by copying the document's own file, so the first revision is the
        saved content byte for byte — encryption and all.  A session with no file
        to copy (opened from a buffer), or with nowhere writable to put it, edits
        the way F10-1 did: ``tobytes()`` buffers in RAM.
        """
        if self._revisions is None and not self._revisions_failed:
            if self.path and os.path.isfile(self.path):
                try:
                    self._revisions = RevisionStore.from_file(self.path)
                except (RevisionStoreError, OSError):
                    self._revisions_failed = True
            else:
                self._revisions_failed = True
        return self._revisions

    def _before_snapshot(self) -> Snapshot:
        """What undo would return to, if an edit were accepted now."""
        store = self._ensure_revisions()
        if store is not None and self._revision is None:
            # Nothing has been edited yet, so the file is the document, and the
            # store's first revision — its copy — is what undo returns to.
            self._revision = store.first_revision
        if self._revision is not None:
            return Snapshot(path=self._revision,
                            size=store.size_of(self._revision))
        data = self._serialize()
        return Snapshot(data=data, size=len(data))

    def _open_working_copy(self, before: Snapshot) -> Tuple[fitz.Document, Optional[str]]:
        """A copy to edit, and the file it came from (``None`` for a buffer)."""
        if not before.is_revision:
            return self._open_clone(before.data), None
        staged = self._revisions.next_path()
        self._revisions.copy_into(before.path, staged)
        try:
            return self._open_revision(staged), staged
        except Exception:
            self._revisions.release(staged)
            raise

    def _persist(self, clone: fitz.Document, staged: Optional[str]) -> None:
        """Write the edit into its revision so readers can open the file.

        An incremental append is the whole point: it costs milliseconds and
        leaves the bytes MuPDF did not touch alone.  It is only offered because
        the copy *is* a file handle on ``staged``.
        """
        if not staged:
            return
        try:
            clone.save(staged, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        except Exception as exc:                      # noqa: BLE001 - user-facing
            raise EditError(f"Could not write the new revision: {exc}") from exc

    def _discard(self, clone: fitz.Document, staged: Optional[str]) -> None:
        try:
            clone.close()
        except Exception:
            pass
        if staged and self._revisions is not None:
            self._revisions.release(staged)

    def _release_revision(self, path: str) -> None:
        """Take back a revision no step refers to — unless we are reading it."""
        if self._revisions is not None and path != self._revision:
            self._revisions.release(path)

    def _open_snapshot(self, snapshot: Snapshot) -> fitz.Document:
        if snapshot.path:
            return self._open_revision(snapshot.path)
        return self._open_clone(snapshot.content())

    def _open_revision(self, path: str) -> fitz.Document:
        """Open a revision file, unlocking it the way the document was unlocked.

        A revision is a copy of the user's file rather than of the decoded
        content, so an encrypted document stays encrypted in the history and this
        is the place that knows its password.
        """
        try:
            doc = fitz.open(path)
        except Exception as exc:                      # noqa: BLE001 - user-facing
            raise EditError(f"Could not open a working copy: {exc}") from exc
        try:
            if doc.needs_pass and self.password:
                doc.authenticate(self.password)
        except Exception:
            doc.close()
            raise
        return doc

    def _serialize(self) -> bytes:
        try:
            return bytes(self.doc.tobytes())
        except Exception as exc:                      # noqa: BLE001 - user-facing
            raise EditError(f"Could not read the document: {exc}") from exc

    def _open_clone(self, data: bytes) -> fitz.Document:
        try:
            return fitz.open(stream=data, filetype="pdf")
        except Exception as exc:                      # noqa: BLE001 - user-facing
            raise EditError(f"Could not open a working copy: {exc}") from exc

    def revert(self) -> Tuple[int, Scope]:
        """Throw the edits away and read the document back from its file.

        Revert is the only operation that discards work, so it is the caller's
        job to have asked first.  The history goes with the edits: undoing back
        into content that was just abandoned would resurrect it, and the user
        has said they do not want it.
        """
        self._require_owner()
        if not self.dirty:
            raise SaveError("There are no unsaved changes to revert.")
        name = os.path.basename(self.path) if self.path else "this document"
        if not self.path or not os.path.isfile(self.path):
            raise SaveError(f"“{name}” is no longer where it was, so there is "
                            "nothing to revert to.")
        try:
            reopened = fitz.open(self.path)
        except Exception as exc:                      # noqa: BLE001 - user-facing
            raise SaveError(f"Could not reopen “{name}”: {exc}") from exc
        if reopened.needs_pass and not reopened.authenticate(self.password):
            reopened.close()
            raise SaveError(f"“{name}” needs its password to be reopened.")
        self._adopt(reopened)
        self.history.clear()
        self._close_revisions()
        self.mark_saved()
        return self.version, Scope.FULL

    def _adopt(self, clone: fitz.Document) -> None:
        old, self.doc = self.doc, clone
        try:
            old.close()
        except Exception:
            pass
        self.generation += 1
        self.content_changed()

    # ------------------------------------------------------------- content
    def content_changed(self) -> int:
        """Announce one accepted change.  Returns the new version.

        Call it exactly once per accepted edit (a batch of edits is one call),
        then drop everything derived from the previous content — see
        ``PDFView.invalidate``.
        """
        self.version += 1
        self._snapshot = None
        return self.version

    def content_path(self) -> Optional[str]:
        """The revision file holding the current content, or ``None`` while the
        user's own file is the authority.

        ``open_copy()`` is what readers use; this is for a caller that needs the
        path itself — to hand to another process, or to ask whether a session is
        working from a scratch copy at all.
        """
        return self._revision if self.version else None

    def snapshot(self) -> Optional[bytes]:
        """Current content as PDF bytes, or ``None`` while the disk is the authority.

        ``None`` after :meth:`close` too: a closed session has no live document
        to serialize, and a queued render job that outlives its tab is normal,
        not an error to report.
        """
        if self._closed or self.version == 0:
            return None
        if self._snapshot is None:
            self._publish()
        return self._snapshot

    def _publish(self) -> None:
        self._require_owner()
        if self._revision:
            with open(self._revision, "rb") as handle:
                self._snapshot = handle.read()
        else:
            self._snapshot = self._serialize()

    def read_version(self) -> int:
        """Version a new background read should target.  Call from the GUI thread.

        Publishing happens here so worker threads only ever read something
        immutable — the revision file, or a frozen buffer — instead of touching
        ``self.doc`` concurrently with the GUI.
        """
        if self._closed:
            return self.version
        if self._revision is None:
            self.snapshot()
        return self.version

    # -------------------------------------------------------------- readers
    def open_copy(self, version: Optional[int] = None) -> fitz.Document:
        """A private handle for the caller's thread, safe to use off-thread.

        ``version`` pins the handle to the content a job was submitted for;
        omitting it reads whatever is current.  Reads older than the current
        version may see the newest content: the result is stamped with the
        version it really came from, so the view drops it as stale.  A revision
        file is never amended in place, so a handle opened before an edit keeps
        reading the revision it started on.
        """
        if version is None:
            version = self.version
        if not self._closed and version and self._revision:
            return self._open_revision(self._revision)
        data = None if version == 0 else self.snapshot()
        doc = fitz.open(self.path) if data is None else fitz.open(stream=data,
                                                                  filetype="pdf")
        try:
            if doc.needs_pass and self.password:
                doc.authenticate(self.password)
        except Exception:
            doc.close()
            raise
        return doc

    def _close_revisions(self) -> None:
        if self._revisions is not None:
            self._revisions.close()
        self._revisions = None
        self._revision = None
        self._revisions_failed = False

    def close(self) -> None:
        self.history.clear()          # the snapshots are the expensive part
        try:
            self.doc.close()
        except Exception:
            pass
        self._closed = True           # after this, reads come from the file
        self._close_revisions()       # the directory goes with the document

    def __enter__(self) -> "DocumentSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
