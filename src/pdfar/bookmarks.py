"""Bookmark management for PDFAR.

Handles both:
- PDF internal bookmarks (Table of Contents / outline) - read-only
- User bookmarks - persistent per-document, editable
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QSettings


@dataclass
class Bookmark:
    """A single bookmark entry."""
    title: str
    page: int              # 0-based page index
    level: int = 1         # hierarchy level (1 = top-level)
    is_user: bool = False  # True for user-created, False for PDF outline
    # For user bookmarks only
    position: Optional[tuple] = None  # (x, y) in PDF points, optional


class BookmarkManager:
    """Manages bookmarks for a single document.

    - PDF outline bookmarks: read from the PDF, read-only
    - User bookmarks: stored in QSettings, persisted per document path
    """

    def __init__(self, doc_path: str, doc: fitz.Document):
        self.doc_path = doc_path
        self.doc = doc
        self.settings = QSettings("PDFAR", "PDFAR")
        self._user_bookmarks: List[Bookmark] = []
        self._outline_bookmarks: List[Bookmark] = []
        self._load_outline()
        self._load_user_bookmarks()

    # ------------------------------------------------------------------ outline (PDF internal)
    def _load_outline(self) -> None:
        """Load the PDF's table of contents as bookmarks."""
        try:
            toc = self.doc.get_toc() or []
        except Exception:
            toc = []
        self._outline_bookmarks = []
        for level, title, page in toc:
            level = max(1, int(level))
            # PyMuPDF pages are 1-based in TOC
            page_idx = max(0, int(page) - 1)
            self._outline_bookmarks.append(Bookmark(
                title=str(title),
                page=page_idx,
                level=level,
                is_user=False
            ))

    def get_outline_bookmarks(self) -> List[Bookmark]:
        return list(self._outline_bookmarks)

    # ------------------------------------------------------------------ user bookmarks (persisted)
    def _settings_key(self) -> str:
        """Unique key for this document's user bookmarks."""
        return f"bookmarks/{os.path.abspath(self.doc_path)}"

    def _load_user_bookmarks(self) -> None:
        data = self.settings.value(self._settings_key(), [])
        self._user_bookmarks = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    bm = Bookmark(
                        title=item.get("title", ""),
                        page=item.get("page", 0),
                        level=item.get("level", 1),
                        is_user=True,
                        position=tuple(item["position"]) if item.get("position") else None
                    )
                    self._user_bookmarks.append(bm)

    def _save_user_bookmarks(self) -> None:
        data = []
        for bm in self._user_bookmarks:
            data.append({
                "title": bm.title,
                "page": bm.page,
                "level": bm.level,
                "position": list(bm.position) if bm.position else None
            })
        self.settings.setValue(self._settings_key(), data)

    def get_user_bookmarks(self) -> List[Bookmark]:
        return list(self._user_bookmarks)

    def get_all_bookmarks(self) -> List[Bookmark]:
        """All bookmarks: user first, then outline."""
        return self._user_bookmarks + self._outline_bookmarks

    def add_user_bookmark(self, title: str, page: int, level: int = 1,
                          position: Optional[tuple] = None) -> Bookmark:
        """Add a user bookmark."""
        bm = Bookmark(title=title, page=page, level=level, is_user=True, position=position)
        # Insert maintaining order by page, then by level
        insert_idx = 0
        for i, existing in enumerate(self._user_bookmarks):
            if existing.page > page or (existing.page == page and existing.level >= level):
                insert_idx = i
                break
        else:
            insert_idx = len(self._user_bookmarks)
        self._user_bookmarks.insert(insert_idx, bm)
        self._save_user_bookmarks()
        return bm

    def remove_user_bookmark(self, index: int) -> bool:
        """Remove a user bookmark by index in the user bookmarks list."""
        if 0 <= index < len(self._user_bookmarks):
            self._user_bookmarks.pop(index)
            self._save_user_bookmarks()
            return True
        return False

    def rename_user_bookmark(self, index: int, new_title: str) -> bool:
        """Rename a user bookmark."""
        if 0 <= index < len(self._user_bookmarks) and new_title.strip():
            self._user_bookmarks[index].title = new_title.strip()
            self._save_user_bookmarks()
            return True
        return False

    def move_user_bookmark(self, index: int, new_level: int) -> bool:
        """Change the hierarchy level of a user bookmark."""
        if 0 <= index < len(self._user_bookmarks):
            new_level = max(1, new_level)
            self._user_bookmarks[index].level = new_level
            self._save_user_bookmarks()
            return True
        return False