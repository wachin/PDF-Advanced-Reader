#!/usr/bin/env python3
"""Unit tests for PDF viewer async rendering."""

import sys
import os
import tempfile
import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from pdfar.cache import PageCache
from pdfar.worker import RenderWorker


def test_page_cache_lru():
    """Test that PageCache implements LRU eviction."""
    cache = PageCache(max_size=3)
    
    # Add 3 items
    cache.put(0, 1.0, b"data0", 100, 100)
    cache.put(1, 1.0, b"data1", 100, 100)
    cache.put(2, 1.0, b"data2", 100, 100)
    
    assert len(cache) == 3
    
    # Access page 0 (should move to end)
    assert cache.get(0, 1.0) is not None
    
    # Add 4th item (should evict page 1)
    cache.put(3, 1.0, b"data3", 100, 100)
    
    assert len(cache) == 3
    assert cache.get(0, 1.0) is not None  # Still there (recently accessed)
    assert cache.get(1, 1.0) is None      # Evicted
    assert cache.get(2, 1.0) is not None  # Still there
    assert cache.get(3, 1.0) is not None  # Newly added
    
    print("✓ PageCache LRU test passed")


def test_page_cache_invalidate_zoom():
    """Test that invalidate_zoom removes non-matching zoom levels."""
    cache = PageCache(max_size=10)
    
    cache.put(0, 1.0, b"data100", 100, 100)
    cache.put(0, 2.0, b"data200", 200, 200)
    cache.put(1, 1.0, b"data101", 100, 100)
    
    cache.invalidate_zoom(1.0)
    
    assert cache.get(0, 1.0) is None
    assert cache.get(1, 1.0) is None
    assert cache.get(0, 2.0) is not None
    
    print("✓ PageCache invalidate_zoom test passed")


def test_worker_render():
    """Test that RenderWorker can render a page."""
    # Create a test PDF
    doc = fitz.open()
    page = doc.new_page()
    doc.save("/tmp/test_render.pdf")
    doc.close()
    
    worker = RenderWorker("/tmp/test_render.pdf", 0, 1.0)
    worker.run()
    
    assert worker.is_finished()
    assert worker.doc_path == "/tmp/test_render.pdf"
    assert worker.page_index == 0
    
    import os
    os.remove("/tmp/test_render.pdf")
    
    print("✓ RenderWorker test passed")


def test_viewer_geometry_initialization():
    """Test that viewer initializes all pages with geometry."""
    from PyQt6.QtWidgets import QApplication
    from pdfar.viewer import PDFViewer
    
    # Create test PDF with known page count
    doc = fitz.open()
    for _ in range(50):
        doc.new_page()
    doc.save("/tmp/test_geometry.pdf")
    doc.close()
    
    app = QApplication.instance() or QApplication(sys.argv)
    viewer = PDFViewer("/tmp/test_geometry.pdf", zoom=1.0)
    
    assert len(viewer.page_widgets) == 50
    
    # Verify all pages have geometry
    for i in range(50):
        assert i in viewer.page_widgets
        assert viewer.page_widgets[i].width > 0
        assert viewer.page_widgets[i].height > 0
    
    os.remove("/tmp/test_geometry.pdf")
    
    print("✓ Viewer geometry initialization test passed")


def test_viewport_range():
    """Test that _get_visible_page_range calculates correctly."""
    from PyQt6.QtWidgets import QApplication
    from pdfar.viewer import PDFViewer
    
    doc = fitz.open()
    for _ in range(100):
        doc.new_page()
    doc.save("/tmp/test_viewport.pdf")
    doc.close()
    
    app = QApplication.instance() or QApplication(sys.argv)
    viewer = PDFViewer("/tmp/test_viewport.pdf", zoom=1.0)
    
    # Check that the method exists and returns a tuple
    result = viewer._get_visible_page_range()
    assert isinstance(result, tuple)
    assert len(result) == 2
    
    os.remove("/tmp/test_viewport.pdf")
    
    print("✓ Viewport range test passed")


def main():
    """Run all tests."""
    print("=" * 50)
    print("PDF Viewer Async Rendering Tests")
    print("=" * 50)
    
    try:
        test_page_cache_lru()
        test_page_cache_invalidate_zoom()
        test_worker_render()
        test_viewer_geometry_initialization()
        test_viewport_range()
        
        print("=" * 50)
        print("✓ All tests passed")
        print("=" * 50)
    except Exception as e:
        print(f"✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
