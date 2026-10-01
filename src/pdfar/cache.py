"""Byte-budget LRU cache for rendered pages (GUI thread only).

Unlike the 1.x cache this one is actually used, is bounded by *memory*
rather than an arbitrary page count, and stores ``QImage`` (implicitly shared,
cheap to copy) instead of raw PNG buffers.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple

from PyQt6.QtGui import QImage


class PageCache:
    """LRU cache keyed by (page, zoom, tag) with a byte budget."""

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self.max_bytes = int(max_bytes)
        self._cache: "OrderedDict[Tuple[int, float, int, str], QImage]" = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _key(page: int, zoom: float, rot: int = 0, tag: str = "view") -> Tuple[int, float, int, str]:
        return (int(page), round(float(zoom), 4), int(rot) % 360, tag)

    @staticmethod
    def _size_of(img: QImage) -> int:
        return max(1, img.width() * img.height() * 4)

    def get(self, page: int, zoom: float, rot: int = 0, tag: str = "view") -> Optional[QImage]:
        key = self._key(page, zoom, rot, tag)
        img = self._cache.get(key)
        if img is not None:
            self._cache.move_to_end(key)
        return img

    def put(self, page: int, zoom: float, img: QImage, rot: int = 0, tag: str = "view") -> None:
        key = self._key(page, zoom, rot, tag)
        old = self._cache.pop(key, None)
        if old is not None:
            self._bytes -= self._size_of(old)
        self._cache[key] = img
        self._bytes += self._size_of(img)
        while self._bytes > self.max_bytes and self._cache:
            _, victim = self._cache.popitem(last=False)
            self._bytes -= self._size_of(victim)

    def invalidate_zoom(self, zoom: Optional[float] = None, tag: str = "view") -> None:
        """Drop entries for *tag*; if zoom is given, only that zoom level."""
        if zoom is None:
            victims = [k for k in self._cache if k[3] == tag]
        else:
            z = round(float(zoom), 4)
            victims = [k for k in self._cache if k[3] == tag and k[1] == z]
        for key in victims:
            img = self._cache.pop(key)
            self._bytes -= self._size_of(img)

    def clear(self) -> None:
        self._cache.clear()
        self._bytes = 0

    @property
    def byte_size(self) -> int:
        return self._bytes

    def __len__(self) -> int:
        return len(self._cache)
