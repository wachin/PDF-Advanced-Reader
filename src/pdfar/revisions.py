"""F10-3c: the document's live content, kept as whole files in a private directory.

An undo step used to be a byte buffer: 25 MB and 1.7 s per edit on a 540-page
book, paid twice (the snapshot and the read publish) because MuPDF can only hand
out the current content by serializing the whole document.  A *file* holding the
same content costs 6 ms to copy and 3 ms to open, so the history becomes a set of
numbered revisions on disk and the render workers open a path instead of parsing
a buffer.

The design is deliberately dumb: MuPDF cannot be trusted to give stable earlier
revisions of one appended file (an incremental save rewrites the section the
previous save added, verified by diffing the prefix between saves), so a revision
is never amended — a new edit copies the revision it starts from, is applied to
the copy, and the copy becomes the new revision.  That is also what keeps
copy-and-adopt honest: the copy is discarded if the edit raises, and the revision
the user was looking at is still on disk, untouched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional


class RevisionStoreError(RuntimeError):
    """The directory could not be made, or a revision could not be written."""


class RevisionStore:
    """Unique revision paths in a directory only this user can read.

    The store owns the *files*; the session owns the knowledge of which one is
    current.  Nothing here opens a document, so nothing here can hold a MuPDF
    handle open across a rename.
    """

    def __init__(self, directory: str):
        if not os.path.isdir(directory):
            raise RevisionStoreError(f"No revision directory at {directory}")
        self.directory = os.path.abspath(directory)
        self.first_revision: Optional[str] = None
        self._count = 0

    # ------------------------------------------------------------- creation
    @classmethod
    def create(cls) -> "RevisionStore":
        """A fresh private directory.  ``mkdtemp`` already means 0700."""
        return cls(tempfile.mkdtemp(prefix="pdfar-revisions-"))

    @classmethod
    def from_file(cls, path: str) -> "RevisionStore":
        """A store whose first revision is a copy of *path*.

        Copied, not read: the user's file keeps changing under us (Save, or
        another program), and a revision has to be the content it claims to be
        for as long as undo can reach it.
        """
        store = cls.create()
        store.first_revision = store.next_path()
        store.copy_into(path, store.first_revision)
        return store

    # ------------------------------------------------------------ allocation
    def next_path(self) -> str:
        """A path in this directory that nothing has written to yet."""
        self._count += 1
        return os.path.join(self.directory, f"r{self._count:04d}.pdf")

    def copy_into(self, source: str, target: str) -> str:
        """Write *source*'s bytes to *target* as a private file.

        Raises :class:`RevisionStoreError` if it cannot, which is the caller's
        signal to refuse the edit rather than half-apply it.
        """
        try:
            shutil.copyfile(source, target)
        except OSError as exc:
            self.release(target)
            raise RevisionStoreError(f"Could not write a working revision: {exc}") from exc
        try:
            os.chmod(target, 0o600)      # the document's content, in the open
        except OSError:
            pass                         # the directory is already 0700
        return target

    # ------------------------------------------------------------- lifecycle
    def release(self, path: Optional[str]) -> None:
        """Remove one revision the history no longer refers to."""
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            # Already gone, or never written.  A leftover file is a waste of
            # disk, not a reason to tell the user their edit failed.
            pass

    def release_all(self) -> None:
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            self.release(os.path.join(self.directory, name))

    def close(self) -> None:
        """Take the directory and every revision with it."""
        self.release_all()
        shutil.rmtree(self.directory, ignore_errors=True)

    def size_of(self, path: Optional[str]) -> int:
        """How much *path* holds, for the history's byte budget."""
        if not path:
            return 0
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def __len__(self) -> int:
        try:
            return len(os.listdir(self.directory))
        except OSError:
            return 0
