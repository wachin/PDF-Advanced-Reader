"""Unit tests for the LRU page cache."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QImage  # noqa: E402

from pdfar.cache import PageCache  # noqa: E402


def _img(w=10, h=10):
    return QImage(w, h, QImage.Format.Format_RGB32)


def test_put_get_roundtrip():
    c = PageCache()
    c.put(0, 1.0, _img())
    assert c.get(0, 1.0) is not None
    assert c.get(0, 1.5) is None
    assert c.get(1, 1.0) is None


def test_zoom_is_keyed_independently():
    c = PageCache()
    c.put(0, 1.0, _img())
    c.put(0, 2.0, _img())
    assert len(c) == 2
    assert c.get(0, 1.0) is not None
    assert c.get(0, 2.0) is not None


def test_rotation_is_keyed_independently():
    c = PageCache()
    c.put(0, 1.0, _img(), rot=0)
    c.put(0, 1.0, _img(), rot=90)
    assert len(c) == 2
    assert c.get(0, 1.0, rot=90) is not None
    assert c.get(0, 1.0, rot=0) is not None


def test_tag_is_keyed_independently():
    c = PageCache()
    c.put(0, 1.0, _img(), tag="view")
    c.put(0, 1.0, _img(), tag="thumb")
    assert len(c) == 2


def test_byte_budget_evicts_oldest():
    c = PageCache(max_bytes=5 * 10 * 10 * 4)   # room for 5 small images
    for i in range(10):
        c.put(i, 1.0, _img())
    assert len(c) <= 5
    assert c.get(0, 1.0) is None        # evicted
    assert c.get(9, 1.0) is not None    # newest kept


def test_lru_order_moves_on_get():
    c = PageCache(max_bytes=5 * 10 * 10 * 4)
    for i in range(5):
        c.put(i, 1.0, _img())
    assert c.get(0, 1.0) is not None    # touch page 0
    c.put(5, 1.0, _img())               # evicts page 1, not page 0
    assert c.get(0, 1.0) is not None
    assert c.get(1, 1.0) is None


def test_invalidate_zoom_only_drops_that_level():
    c = PageCache()
    c.put(0, 1.0, _img())
    c.put(1, 1.0, _img())
    c.put(2, 2.0, _img())
    c.invalidate_zoom(1.0)
    assert c.get(0, 1.0) is None
    assert c.get(1, 1.0) is None
    assert c.get(2, 2.0) is not None


def test_replace_same_key_updates_bytes():
    c = PageCache(max_bytes=10 * 10 * 4 * 2)
    c.put(0, 1.0, _img(10, 10))
    before = c.byte_size
    c.put(0, 1.0, _img(10, 10))
    assert len(c) == 1
    assert c.byte_size == before
