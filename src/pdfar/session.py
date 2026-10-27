"""One authoritative document, stamped with the content it represents.

MuPDF is not thread-safe, so every background reader (render pool, search,
reflow) opens its own ``fitz.Document``.  Opening it *by path* means "the file
as it was last saved", so a change held in memory stayed invisible until a save
reached the disk — the gap described in ``docs/pdfcraft-editing-analysis.md``
(§2, "what you see is what will be saved").  ``DocumentSession`` closes it:

* ``version == 0`` means nothing has been edited, so the file on disk *is* the
  document and no snapshot is ever built (the fast path the large-PDF benchmark
  measures: the pool keeps opening the path and MuPDF keeps mmap-ing it).
* the first accepted change bumps the version, and background reads then come
  from an in-memory byte snapshot.

``version`` is also how readers stay honest: a render job pins the version it
was submitted for, and the view drops results for any other version.
"""

from __future__ import annotations

import threading
from typing import Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz


class SnapshotNotPublished(RuntimeError):
    """A worker asked for content the GUI thread has not serialized yet."""


class DocumentSession:
    def __init__(self, doc: fitz.Document, path: str, password: str = ""):
        self.doc = doc
        self.path = path
        self.password = password or ""
        self.version = 0
        self._snapshot: Optional[bytes] = None
        # fitz documents may only be touched by one thread at a time, so
        # serializing happens here and workers read the immutable bytes.
        self._owner_thread = threading.get_ident()

    # ------------------------------------------------------------- content
    def content_changed(self) -> int:
        """Announce one accepted change.  Returns the new version.

        Call it exactly once per accepted edit (a batch of edits is one call),
        then drop everything derived from the previous content — see
        ``PDFView.invalidate_content``.
        """
        self.version += 1
        self._snapshot = None
        return self.version

    def snapshot(self) -> Optional[bytes]:
        """Current content as PDF bytes, or ``None`` while the disk is the authority."""
        if self.version == 0:
            return None
        if self._snapshot is None:
            self._publish()
        return self._snapshot

    def _publish(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise SnapshotNotPublished(
                f"content v{self.version} must be serialized on the thread that "
                "owns the document; call read_version() before starting workers")
        self._snapshot = bytes(self.doc.tobytes())

    def read_version(self) -> int:
        """Version a new background read should target.  Call from the GUI thread.

        Publishing happens here so worker threads only ever read an immutable
        buffer instead of touching ``self.doc`` concurrently with the GUI.
        """
        self.snapshot()
        return self.version

    # -------------------------------------------------------------- readers
    def open_copy(self, version: Optional[int] = None) -> fitz.Document:
        """A private handle for the caller's thread, safe to use off-thread.

        ``version`` pins the handle to the content a job was submitted for;
        omitting it reads whatever is current.  Reads older than the current
        version may see the newest bytes: the result is stamped with the
        version it really came from, so the view drops it as stale.
        """
        if version is None:
            version = self.version
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

    def close(self) -> None:
        try:
            self.doc.close()
        except Exception:
            pass

    def __enter__(self) -> "DocumentSession":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
