"""Search data structures and the background search worker."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz
from PyQt6.QtCore import QObject, pyqtSignal

Rect = Tuple[float, float, float, float]

MAX_HITS = 5000


@dataclass
class SearchParams:
    query: str
    mode: str = "AND"          # 'AND' | 'OR' | 'PHRASE'
    case_sensitive: bool = False
    whole_words: bool = False


@dataclass
class SearchHit:
    page: int
    snippet: str
    rects: List[Rect] = field(default_factory=list)


def normalize_space(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _terms_for(params: SearchParams) -> List[str]:
    if params.mode == "PHRASE":
        return [params.query]
    return [t for t in re.split(r"\s+", params.query) if t]


def _word_pattern(term: str) -> re.Pattern:
    """Whole-word regex for *term*, tolerant of punctuation ("(x)", "C++")."""
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")


def page_matches(text: str, params: SearchParams, terms: List[str]) -> bool:
    """Return True when *text* satisfies the search parameters."""
    if not terms:
        return False
    cmp_text = text if params.case_sensitive else text.lower()
    t = terms if params.case_sensitive else [x.lower() for x in terms]

    if params.mode == "PHRASE":
        if params.whole_words:
            return _word_pattern(t[0]).search(cmp_text) is not None
        return t[0] in cmp_text

    if params.whole_words:
        preds = [_word_pattern(x).search(cmp_text) is not None for x in t]
    else:
        preds = [x in cmp_text for x in t]
    return all(preds) if params.mode == "AND" else any(preds)


def make_snippet(text: str, terms: List[str], case_sensitive: bool, width: int = 170) -> str:
    src = text if case_sensitive else text.lower()
    needle = terms[0] if terms else ""
    q = needle if case_sensitive else needle.lower()
    m = src.find(q) if q else -1
    if m == -1:
        m = 0
    start = max(0, m - width // 3)
    end = min(len(text), start + width)
    return " ".join(text[start:end].split())


class SearchWorker(QObject):
    """Runs a search in a worker thread, with its own document handle.

    Signals are emitted from the worker thread; connecting them to methods of a
    QObject living in the GUI thread gives us automatic queued delivery.
    """

    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)  # List[SearchHit]

    def __init__(self, doc_path: str, params: SearchParams):
        super().__init__()
        self.doc_path = doc_path
        self.params = params

    def run(self) -> None:
        params = self.params
        terms = _terms_for(params)
        hits: List[SearchHit] = []
        try:
            doc = fitz.open(self.doc_path)
        except Exception:
            self.done.emit([])
            return

        try:
            total = doc.page_count
            for i in range(total):
                if i % 5 == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)
                page = doc.load_page(i)
                text = page.get_text("text") or ""
                if not page_matches(text, params, terms):
                    continue

                rects: List[Rect] = []
                for term in terms:
                    try:
                        found = page.search_for(term, match_case=params.case_sensitive) or []
                    except TypeError:  # older PyMuPDF
                        found = page.search_for(term) or []
                    for r in found:
                        rects.append((r.x0, r.y0, r.x1, r.y1))
                hits.append(SearchHit(page=i, snippet=make_snippet(text, terms, params.case_sensitive),
                                      rects=rects))
                if len(hits) >= MAX_HITS:
                    break
        finally:
            doc.close()

        self.done.emit(hits)
