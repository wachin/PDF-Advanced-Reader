"""Thread-safe asynchronous page rendering.

Why this exists
---------------
The 1.x viewer handed results back to the GUI with ``QTimer.singleShot(0, ...)``
called from ``ThreadPoolExecutor`` worker threads.  Those threads have no Qt
event loop, so the timer never fired and *no page was ever displayed*.

This module replaces that with a proper design:

* a small pool of render threads, each holding its own ``fitz.Document``
  (PyMuPDF documents are not thread-safe and must not be shared);
* a priority queue so visible pages are rendered before prefetch;
* results are delivered with a Qt signal emitted from the worker thread, which
  Qt queues to the receiver's (GUI) thread automatically.  Emitting a signal
  from a non-Qt thread is safe; ``QTimer.singleShot`` from such a thread is not.
* duplicate in-flight requests for the same (page, zoom, tag) are dropped.
"""

from __future__ import annotations

import heapq
import itertools
import threading
from dataclasses import dataclass, field
from typing import Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QObject, pyqtSignal


@dataclass
class RenderResult:
    """Outcome of a single page render job."""

    page: int
    zoom: float
    tag: str
    png: Optional[bytes]
    width: int
    height: int
    ok: bool
    error: str = ""
    rotation: int = 0

    @property
    def key(self):
        return (self.page, round(self.zoom, 4), self.rotation, self.tag)


@dataclass(order=True)
class _Job:
    priority: int
    seq: int = field(compare=True)
    page: int = field(compare=False)
    zoom: float = field(compare=False)
    tag: str = field(compare=False)
    alpha: bool = field(compare=False)
    rotation: int = field(default=0, compare=False)
    cancelled: bool = field(default=False, compare=False)


class RenderPool(QObject):
    """Priority render queue with worker threads.

    Parameters
    ----------
    doc_path:
        Path of the PDF to render.  Every worker thread opens its own handle.
    workers:
        Number of render threads (defaults to min(4, cpu_count)).
    """

    result_ready = pyqtSignal(object)  # RenderResult

    def __init__(self, doc_path: str, workers: int = 4, parent: Optional[QObject] = None,
                 password: str = ""):
        super().__init__(parent)
        self.doc_path = doc_path
        self.password = password
        self._workers = max(1, int(workers))
        self._cv = threading.Condition()
        self._heap: list[_Job] = []
        self._seq = itertools.count()
        self._inflight: set[tuple] = set()
        self._closed = False
        self._threads: list[threading.Thread] = []
        for i in range(self._workers):
            t = threading.Thread(target=self._worker_loop, name=f"pdfar-render-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    # ------------------------------------------------------------------ API
    def request(self, page: int, zoom: float, priority: int = 5, tag: str = "view",
                alpha: bool = False, rotation: int = 0) -> bool:
        """Queue a render job.  Returns False if the job was already in flight."""
        rotation = int(rotation or 0) % 360
        key = (page, round(zoom, 4), rotation, tag)
        with self._cv:
            if self._closed:
                return False
            if key in self._inflight:
                return False
            self._inflight.add(key)
            heapq.heappush(self._heap, _Job(priority, next(self._seq), page, float(zoom), tag, alpha, rotation))
            self._cv.notify()
        return True

    def pending(self) -> int:
        with self._cv:
            return len(self._heap) + len(self._inflight)

    def is_running(self) -> bool:
        """True while the pool still accepts render jobs (not shut down)."""
        with self._cv:
            return not self._closed

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting jobs and (by default) wait for workers to exit.

        Waiting matters: a worker emitting ``result_ready`` on a destroyed
        ``RenderPool`` would crash the interpreter.
        """
        with self._cv:
            self._closed = True
            for job in self._heap:
                job.cancelled = True
            self._cv.notify_all()
        if wait:
            for t in self._threads:
                t.join(timeout=5.0)

    # ------------------------------------------------------------- internal
    def _worker_loop(self) -> None:
        tls = threading.local()
        while True:
            with self._cv:
                while not self._heap and not self._closed:
                    self._cv.wait(0.5)
                if self._closed and not self._heap:
                    return
                job = heapq.heappop(self._heap)
            if job.cancelled:
                self._finish_key(job)
                continue
            result = self._run_job(tls, job)
            self._finish_key(job)
            if job.cancelled:
                continue
            with self._cv:
                closed = self._closed
            if closed:
                continue
            try:
                # Signal emission from this worker thread is queued by Qt to
                # the GUI thread.  This is the whole point of the class.
                self.result_ready.emit(result)
            except RuntimeError:
                # The pool object is being destroyed; nothing to deliver to.
                return

    def _finish_key(self, job: _Job) -> None:
        with self._cv:
            self._inflight.discard((job.page, round(job.zoom, 4), job.rotation, job.tag))

    def _run_job(self, tls, job: _Job) -> RenderResult:
        try:
            doc = getattr(tls, "doc", None)
            if doc is None or getattr(tls, "path", None) != self.doc_path:
                if doc is not None:
                    try:
                        doc.close()
                    except Exception:
                        pass
                doc = fitz.open(self.doc_path)
                if self.password:
                    doc.authenticate(self.password)
                tls.doc = doc
                tls.path = self.doc_path
            page = doc.load_page(job.page)
            mat = fitz.Matrix(job.zoom, job.zoom)
            if job.rotation:
                mat = mat.prerotate(job.rotation)
            pix = page.get_pixmap(matrix=mat, alpha=job.alpha, annots=True)
            return RenderResult(
                page=job.page,
                zoom=job.zoom,
                tag=job.tag,
                png=pix.tobytes("png"),
                width=pix.width,
                height=pix.height,
                ok=True,
                rotation=job.rotation,
            )
        except Exception as exc:  # pragma: no cover - defensive
            return RenderResult(job.page, job.zoom, job.tag, None, 0, 0, False, str(exc), job.rotation)
