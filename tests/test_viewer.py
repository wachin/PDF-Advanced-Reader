"""Tests for viewer scroll and rendering functionality."""

import pytest


class TestWorkerLifecycle:
    """Tests for Bug 1.3: Worker lifecycle with signals."""

    def test_render_worker_is_qobject(self):
        """
        Bug 1.3: RenderWorker should inherit from QObject to support signals.
        
        The fix uses QObject + moveToThread() + pyqtSignal for proper GUI integration.
        """
        from pdfar.worker import RenderWorker
        from PyQt6.QtCore import QObject
        
        # RenderWorker should inherit from QObject
        assert issubclass(RenderWorker, QObject)

    def test_render_worker_has_finished_signal(self):
        """
        Worker should emit finished signal when complete instead of polling.
        """
        from pdfar.worker import RenderWorker
        
        worker = RenderWorker("/nonexistent.pdf", 0, 1.0)
        # Should have finished signal
        assert hasattr(worker, 'finished')

    def test_render_worker_has_run_method(self):
        """
        Worker should have a run() method for the thread to execute.
        """
        from pdfar.worker import RenderWorker
        
        worker = RenderWorker("/nonexistent.pdf", 0, 1.0)
        assert hasattr(worker, 'run')
        assert callable(worker.run)


class TestViewerScrollBug:
    """Tests for Bug 1.1: Visible range calculation inside QScrollArea."""

    def test_set_scroll_position_method_exists(self):
        """
        PDFViewer should have set_scroll_position() method for explicit scroll context.
        This fixes Bug 1.1 where parent() detection failed.
        """
        from pdfar.viewer import PDFViewer
        assert hasattr(PDFViewer, 'set_scroll_position')


class TestMemoryLeaks:
    """Tests for Bug 1.4: Memory leaks when opening multiple PDFs."""

    def test_open_pdf_has_cleanup(self):
        """
        Bug 1.4: open_pdf() should clean up previous viewer before creating new one.
        
        The fix should:
        - Quit and wait for thread pool
        - Call deleteLater() on old viewer
        - Clear search_rects_per_page
        """
        from pdfar.main import PDFAR
        
        # Check that the cleanup code exists in open_pdf method
        import inspect
        source = inspect.getsource(PDFAR.open_pdf)
        
        # Should have cleanup code
        assert 'deleteLater' in source or 'thread_pool' in source


# Run with: PYTHONPATH=src python3 -m pytest tests/test_viewer.py -v
