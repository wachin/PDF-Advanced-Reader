"""F10-0: the document session.

One authoritative ``Document``, a ``content_version`` that every accepted change
bumps, and a byte snapshot for the background workers.  While nothing has been
edited the file on disk is authoritative and the snapshot stays ``None`` (that
is the fast path the large-PDF benchmark measures); afterwards every worker must
see the edited bytes without anything being written to disk.
"""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
from PyQt6.QtCore import Qt, QThread
from PyQt6.QtGui import QImage
from PyQt6.QtTest import QTest

from conftest import build_sample_pdf, make_session
from pdfar.render import RenderPool, RenderResult, _Job
from pdfar.session import DocumentSession, SnapshotNotPublished
from pdfar.viewer import PDFView


def _wait(qapp, predicate, timeout_ms=12000):
    """Pump the event loop until *predicate* holds (workers deliver by signal)."""
    elapsed = 0
    while not predicate() and elapsed < timeout_ms:
        qapp.processEvents()
        QTest.qWait(10)
        elapsed += 10
    return predicate()


def _shown_view(qapp, path):
    view = PDFView(path)
    view.resize(600, 400)
    view.show()
    qapp.processEvents()
    return view


def _tiny_image():
    image = QImage(4, 4, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    return image


# ------------------------------------------------------------- the session
def test_session_starts_with_the_disk_as_authority(tmp_path):
    with make_session(build_sample_pdf(tmp_path / "s.pdf", pages=3)) as session:
        assert session.version == 0
        assert session.snapshot() is None      # nothing to publish yet
        assert session.path.endswith("s.pdf")


def test_content_change_publishes_in_memory_bytes(tmp_path):
    """An edit that never touches the disk still reaches ``snapshot()``."""
    path = str(build_sample_pdf(tmp_path / "s.pdf", pages=2))
    on_disk = open(path, "rb").read()
    doc = fitz.open(path)
    try:
        session = DocumentSession(doc, path)
        doc.load_page(0).insert_text((10, 80), "typed in memory", fontsize=12)

        assert session.version == 0            # nobody announced the change yet
        session.content_changed()
        assert session.version == 1

        data = session.snapshot()
        assert data is not None
        probe = fitz.open(stream=data, filetype="pdf")
        try:
            assert "typed in memory" in probe.load_page(0).get_text()
        finally:
            probe.close()
        # the file on disk is untouched
        assert open(path, "rb").read() == on_disk
        disk = fitz.open(path)
        try:
            assert "typed in memory" not in disk.load_page(0).get_text()
        finally:
            disk.close()
    finally:
        doc.close()


def test_snapshot_is_cached_per_version(tmp_path):
    with make_session(build_sample_pdf(tmp_path / "s.pdf")) as session:
        session.content_changed()
        first = session.snapshot()
        assert session.snapshot() is first      # materialized once, reused
        session.content_changed()
        assert session.snapshot() is not first
        assert session.version == 2


def test_password_is_carried(tmp_path):
    path = str(tmp_path / "enc.pdf")
    doc = fitz.open()
    doc.new_page(width=400, height=300)
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="o",
             user_pw="secret", permissions=16)
    doc.close()

    with make_session(path, password="secret") as session:
        assert session.password == "secret"
        handle = session.open_copy()             # unlocked, reading the file
        try:
            assert handle.page_count == 1
        finally:
            handle.close()
        session.content_changed()
        handle = session.open_copy()             # and after an edit
        try:
            assert handle.page_count == 1
        finally:
            handle.close()


def test_workers_never_serialize_the_gui_document(tmp_path):
    """A background read must fail loudly rather than touch the GUI document.

    MuPDF allows one thread at a time per document, so the bytes are published
    by the thread that owns it (``read_version``) and workers only read them.
    """
    with make_session(build_sample_pdf(tmp_path / "s.pdf")) as session:
        session.content_changed()
        errors = []

        def read():
            try:
                session.open_copy().close()
            except Exception as exc:            # noqa: BLE001 - the point of the test
                errors.append(exc)

        thread = threading.Thread(target=read)
        thread.start()
        thread.join(5)
        assert errors, "a worker thread serialized the GUI document"
        assert isinstance(errors[0], SnapshotNotPublished)


# ---------------------------------------------------------------- the pool
def test_render_pool_reopens_when_the_version_changes(qapp, tmp_path):
    """A job submitted after an edit renders the edited page, not the file."""
    path = str(build_sample_pdf(tmp_path / "s.pdf"))
    with make_session(path) as session:
        pool = RenderPool(session, workers=1)
        results = []
        pool.result_ready.connect(results.append)
        try:
            assert pool.request(0, 1.0) is True
            assert _wait(qapp, lambda: results)
            before = results[0]
            assert before.version == 0

            session.doc.load_page(0).insert_text((10, 30), "MUTATED", fontsize=12)
            session.content_changed()
            del results[:]
            assert pool.request(0, 1.0, tag="view") is True
            assert _wait(qapp, lambda: results)
            after = results[0]
            assert after.version == 1
            assert after.ok and after.samples is not None
            assert bytes(after.samples) != bytes(before.samples)
        finally:
            pool.shutdown(wait=True)


def test_pool_keeps_reading_the_disk_while_untouched(qapp, tmp_path):
    """The fast path: version 0 means no snapshot is ever built."""
    path = str(build_sample_pdf(tmp_path / "s.pdf", pages=2))
    with make_session(path) as session:
        pool = RenderPool(session, workers=2)
        results = []
        pool.result_ready.connect(results.append)
        try:
            for page in range(2):
                assert pool.request(page, 1.0) is True
            assert _wait(qapp, lambda: len(results) == 2)
            assert session.snapshot() is None
            assert all(r.version == 0 for r in results)
        finally:
            pool.shutdown(wait=True)


def test_job_for_older_content_is_dropped(qapp, tmp_path):
    """A queued job whose content changed produces no result: the view would
    discard it anyway, so its thread should not even load the page."""
    path = str(build_sample_pdf(tmp_path / "s.pdf"))
    with make_session(path) as session:
        pool = RenderPool(session, workers=1)
        try:
            job = _Job(priority=0, seq=0, page=0, zoom=1.0, tag="view", alpha=False)
            job.version = session.read_version()
            assert pool._run_job(threading.local(), job) is not None
            session.content_changed()
            assert pool._run_job(threading.local(), job) is None
        finally:
            pool.shutdown(wait=True)


def test_pool_reports_words_from_the_session(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "s.pdf"))
    with make_session(path) as session:
        pool = RenderPool(session, workers=1)
        results = []
        pool.result_ready.connect(results.append)
        try:
            session.doc.load_page(0).insert_text((10, 80), "ZZTESTWORD", fontsize=12)
            session.content_changed()
            assert pool.request_words(0) is True
            assert _wait(qapp, lambda: results)
            result = results[0]
            assert result.tag == "text"
            assert result.version == 1
            assert any("ZZTESTWORD" in w[4] for w in result.words)
        finally:
            pool.shutdown(wait=True)


# ------------------------------------------------------------------- view
def test_view_owns_the_session_and_drops_stale_results(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=3))
    view = PDFView(path)
    try:
        assert view.session.doc is view.doc
        assert view.session.path == view.doc_path == path
        assert view.session.version == 0

        # a result rendered from an older version must not be cached
        stale = _stale_render(view, view.session.version - 1)
        view._on_result(stale)
        assert len(view.cache) == 0

        fresh = _stale_render(view, view.session.version)
        view._on_result(fresh)
        assert len(view.cache) == 1
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def _stale_render(view, version):
    image = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8))
    return RenderResult(page=0, zoom=view.zoom, tag="view",
                        png=image.tobytes("png"), width=8, height=8,
                        ok=True, version=version)


def test_invalidate_content_clears_caches_and_bumps_version(qapp, tmp_path):
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=2))
    view = PDFView(path)
    try:
        view.cache.put(0, view.zoom, _tiny_image(), tag="view")
        view._words[0] = [(0, 0, 1, 1, "x")]
        view._tiles[0] = {(0, 0): _tiny_image()}
        before = view.session.version

        assert view.invalidate_content() == before + 1

        assert view.session.version == before + 1
        assert len(view.cache) == 0
        assert view._words == {}
        assert view._tiles == {}
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_edited_content_reaches_the_view(qapp, tmp_path):
    """After an edit the view repaints from memory: new pixels arrive, the
    pre-edit cache is gone, and the file on disk never changed."""
    path = str(build_sample_pdf(tmp_path / "v.pdf", pages=1))
    on_disk = open(path, "rb").read()
    view = _shown_view(qapp, path)
    try:
        assert _wait(qapp, lambda: view.cache.get(0, view.zoom, 0) is not None)
        before = view.cache.get(0, view.zoom, 0)

        view.doc.load_page(0).insert_text((10, 80), "EDITEDMARK", fontsize=12)
        view.invalidate_content()
        assert view.cache.get(0, view.zoom, 0) is None

        assert _wait(qapp, lambda: view.cache.get(0, view.zoom, 0) is not None)
        after = view.cache.get(0, view.zoom, 0)
        assert after != before                   # repainted from the edit
        assert open(path, "rb").read() == on_disk
    finally:
        view.shutdown()
        view.deleteLater()
        qapp.processEvents()


def test_search_sees_an_in_memory_change(qapp, tmp_path):
    """The search worker must read the session, not the file on disk."""
    from pdfar.search import SearchParams, SearchWorker

    path = str(build_sample_pdf(tmp_path / "s.pdf", pages=2))
    with make_session(path) as session:
        session.doc.load_page(1).insert_text((10, 30), "ONLYINMEMORY", fontsize=12)
        session.content_changed()
        worker = SearchWorker(session, SearchParams("ONLYINMEMORY"))
        found = []
        thread = QThread()
        worker.moveToThread(thread)
        worker.done.connect(found.append)        # mirrors MainWindow._run_search
        worker.done.connect(thread.quit)
        thread.started.connect(worker.run)
        try:
            thread.start()
            assert _wait(qapp, lambda: bool(found))
            assert found[0], "search did not see the in-memory edit"
        finally:
            worker.cancel()
            thread.quit()
            thread.wait(5000)


def test_reflow_sees_an_in_memory_change(qapp, tmp_path):
    from pdfar.reflow import ReflowWorker

    path = str(build_sample_pdf(tmp_path / "s.pdf"))
    with make_session(path) as session:
        session.doc.load_page(0).insert_text((10, 80), "REFLOWONLY", fontsize=12)
        session.content_changed()
        worker = ReflowWorker(session, width=600)
        layouts = []
        worker.page_ready.connect(layouts.append)
        try:
            worker.start()
            assert _wait(qapp, lambda: bool(layouts))
            text = " ".join(run[0] for line in layouts[0].lines for run in line.runs)
            assert "REFLOWONLY" in text
        finally:
            worker.requestInterruption()
            worker.wait(5000)


def test_main_window_passes_the_session(qapp, tmp_path):
    """Opening a document wires the session through the whole read path."""
    from pdfar.main import MainWindow

    path = str(build_sample_pdf(tmp_path / "m.pdf", pages=2))
    win = MainWindow()
    win.show()
    qapp.processEvents()
    try:
        win.open_document(path)
        qapp.processEvents()
        view = win.view
        assert view.session.doc is view.doc
        assert view.pool.session is view.session
        assert view.session.version == 0
    finally:
        win.close()
        qapp.processEvents()
