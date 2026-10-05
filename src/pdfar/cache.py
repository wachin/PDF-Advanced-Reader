"""Byte-budget cache for rendered pages (GUI thread only).

Unlike the 1.x cache this one is actually used, is bounded by *memory*
rather than an arbitrary page count, and stores ``QImage`` (implicitly shared,
cheap to copy) instead of raw PNG buffers.

Eviction is *viewport-aware* when the cache is told which page the viewport is
centred on (``set_center``): the entry whose page is farthest from the viewport
is dropped first, mirroring Okular's ``DocumentPrivate::searchLowestPriorityPixmap``
(see ``docs/okular-architecture.md``).  Without a center it falls back to a
plain LRU, so the class stays usable on its own.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple

from PyQt6.QtGui import QImage


class PageCache:
    """Byte-budget cache keyed by (page, zoom, rot, tag)."""

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self.max_bytes = int(max_bytes)
        self._cache: "OrderedDict[Tuple[int, float, int, str], QImage]" = OrderedDict()
        self._bytes = 0
        self._center: Optional[int] = None

    def set_center(self, page: int) -> None:
        """Tell the cache which page the viewport is centred on.

        With a center set, eviction drops the farthest page first (Okular's
        distance-priority cache) instead of the least-recently-used one.
        """
        self._center = int(page)

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
            self._evict_one()

    def _evict_one(self) -> None:
        """Drop a single entry to free memory.

        With a viewport center set, the page farthest from it goes first
        (Okular-style distance priority); ties break to the least-recently-used
        entry.  Without a center, plain LRU (the oldest entry).
        """
        if not self._cache:
            return
        victim: Optional[Tuple[int, float, int, str]] = None
        if self._center is None:
            victim = next(iter(self._cache))
        else:
            center = self._center
            best = -1
            for key in self._cache:            # OrderedDict: oldest first
                dist = abs(key[0] - center)
                if dist > best:                # strict > keeps LRU on ties
                    best = dist
                    victim = key
            if victim is None:
                victim = next(iter(self._cache))
        img = self._cache.pop(victim)
        self._bytes -= self._size_of(img)

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
