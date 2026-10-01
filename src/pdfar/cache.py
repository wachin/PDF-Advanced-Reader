"""Caché de imágenes renderizadas por página."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PyQt6.QtGui import QPixmap


class PageCache:
    """Cache de imágenes renderizadas por página."""
    
    def __init__(self, max_size: int = 10):
        self.max_size = max_size
        self.cache: Dict[Tuple[int, float], QPixmap] = {}
    
    def get(self, page_idx: int, zoom: float) -> Optional[QPixmap]:
        key = (page_idx, round(zoom, 2))
        return self.cache.get(key)
    
    def put(self, page_idx: int, zoom: float, pixmap: QPixmap):
        key = (page_idx, round(zoom, 2))
        self.cache[key] = pixmap
        if len(self.cache) > self.max_size:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
    
    def clear(self):
        self.cache.clear()
    
    def invalidate_zoom(self, old_zoom: float, new_zoom: float):
        keys_to_remove = [k for k in self.cache if abs(k[1] - new_zoom) > 0.01]
        for k in keys_to_remove:
            del self.cache[k]
