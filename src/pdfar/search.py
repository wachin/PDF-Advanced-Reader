"""Estructuras y utilidades para búsqueda."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPen


# Paleta para resaltados
HIGHLIGHT_COLOR = QColor(255, 235, 59, 110)  # amarillo
HIGHLIGHT_PEN = QPen(Qt.PenStyle.NoPen)


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class SearchParams:
    query: str
    mode: str  # 'AND', 'OR', 'PHRASE', 'PROX'
    case_sensitive: bool
    whole_words: bool
    proximity: int = 5


@dataclass
class SearchHit:
    page: int
    snippet: str
