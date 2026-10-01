"""LRU cache for rendered page bitmaps."""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional, Tuple


class PageCache:
    """LRU cache for rendered PDF page data."""

    def __init__(self, max_size: int = 30):
        self.max_size = max_size
        self._cache: OrderedDict[Tuple[int, float], Tuple[bytes, int, int]] = OrderedDict()

    def get(self, page_index: int, zoom: float) -> Optional[Tuple[bytes, int, int]]:
        """Retrieve cached page data (raw_data, width, height)."""
        key = (page_index, round(zoom, 2))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, page_index: int, zoom: float, 
            raw_data: bytes, width: int, height: int):
        """Store page data in cache with LRU eviction."""
        key = (page_index, round(zoom, 2))
        if key in self._cache:
            del self._cache[key]
        while len(self._cache) >= self.max_size:
            self._cache.popitem(last=False)
        self._cache[key] = (raw_data, width, height)

    def invalidate_zoom(self, zoom: float):
        """Remove all entries that MATCH the current zoom."""
        keys_to_remove = [
            k for k in self._cache 
            if abs(k[1] - round(zoom, 2)) <= 0.01
        ]
        for key in keys_to_remove:
            del self._cache[key]

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)
