"""Search data structures and the background search worker."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from .session import DocumentSession

Rect = Tuple[float, float, float, float]
Span = Tuple[int, int]

MAX_HITS = 5000
MAX_REGEX_HITS = 10000
REGEX_TIMEOUT_MS = 5000


@dataclass
class SearchParams:
    query: str
    mode: str = "AND"          # 'AND' | 'OR' | 'PHRASE' | 'REGEX'
    case_sensitive: bool = False
    whole_words: bool = False
    # Regex-specific options
    regex_timeout_ms: int = 5000
    max_regex_hits: int = 10000


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
    if params.mode == "REGEX":
        return [params.query]  # Return the full query as a single regex pattern
    return [t for t in re.split(r"\s+", params.query) if t]


class SearchTimeout(Exception):
    """Raised when a regex search exceeds its per-page time budget."""


def _now() -> float:
    return time.monotonic()


def compile_regex(params: SearchParams) -> re.Pattern:
    """Compile the regex query, raising ValueError for invalid patterns."""
    flags = 0 if params.case_sensitive else re.IGNORECASE
    try:
        return re.compile(params.query, flags)
    except re.error as e:
        raise ValueError(f"Invalid regex: {e}") from e


def _word_pattern(term: str) -> re.Pattern:
    """Whole-word regex for *term*, tolerant of punctuation ("(x)", "C++")."""
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)")


def page_matches(text: str, params: SearchParams, terms: List[str]) -> bool:
    """Return True when *text* satisfies the search parameters."""
    if not terms:
        return False
    
    if params.mode == "REGEX":
        try:
            pattern = compile_regex(params)
            return pattern.search(text) is not None
        except ValueError:
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


def highlight_spans(text: str, params: SearchParams) -> List[Span]:
    """Character spans in *text* that the current search would match."""
    if not text:
        return []
    flags = 0 if params.case_sensitive else re.IGNORECASE
    spans: List[Span] = []

    if params.mode == "REGEX":
        try:
            pattern = compile_regex(params)
        except ValueError:
            return []
        spans = [match.span() for match in pattern.finditer(text)
                 if match.end() > match.start()]
    else:
        for term in _terms_for(params):
            if not term:
                continue
            if params.whole_words:
                pattern = re.compile(
                    r"(?<!\w)" + re.escape(term) + r"(?!\w)", flags)
            else:
                pattern = re.compile(re.escape(term), flags)
            spans.extend(match.span() for match in pattern.finditer(text))

    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for start, end in spans[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


class SearchWorker(QObject):
    """Runs a search in a worker thread, with its own document handle.

    Signals are emitted from the worker thread; connecting them to methods of a
    QObject living in the GUI thread gives us automatic queued delivery.
    """

    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)  # List[SearchHit]
    failed = pyqtSignal(str)

    def __init__(self, session: DocumentSession, params: SearchParams):
        super().__init__()
        self.session = session
        self.params = params
        self._cancel = threading.Event()
        # Constructed on the GUI thread: publish the bytes the worker thread is
        # about to read, so it never touches the document the GUI is using.
        self.version = session.read_version()

    def cancel(self) -> None:
        """Ask the running search to stop at the next page or match."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def _deadline(self) -> Optional[float]:
        timeout_ms = self.params.regex_timeout_ms
        if timeout_ms <= 0:
            return None
        return _now() + timeout_ms / 1000.0

    def _check_deadline(self, deadline: Optional[float]) -> None:
        if deadline is not None and _now() > deadline:
            raise SearchTimeout(
                f"Regex search timed out after {self.params.regex_timeout_ms} ms")

    def _search_for(self, page, text: str) -> list:
        try:
            return page.search_for(text, match_case=self.params.case_sensitive) or []
        except TypeError:  # older PyMuPDF
            try:
                return page.search_for(text) or []
            except Exception:
                return []
        except Exception:
            return []

    def _term_rects(self, page, terms: List[str]) -> List[Rect]:
        rects: List[Rect] = []
        for term in terms:
            for r in self._search_for(page, term):
                rects.append((r.x0, r.y0, r.x1, r.y1))
        return rects

    def _regex_rects(self, page, pattern: re.Pattern, text: str,
                     deadline: Optional[float]) -> List[Rect]:
        rects: List[Rect] = []
        for match in pattern.finditer(text):
            if self.cancelled:
                return rects
            match_text = match.group()
            if match_text:
                for r in self._search_for(page, match_text):
                    rects.append((r.x0, r.y0, r.x1, r.y1))
            self._check_deadline(deadline)
        self._check_deadline(deadline)
        return rects

    def run(self) -> None:
        params = self.params
        terms = _terms_for(params)
        hits: List[SearchHit] = []

        pattern: Optional[re.Pattern] = None
        if params.mode == "REGEX":
            try:
                pattern = compile_regex(params)
            except ValueError as exc:
                self.failed.emit(str(exc))
                return

        try:
            doc = self.session.open_copy(self.version)
        except Exception:
            self.done.emit([])
            return

        try:
            total = doc.page_count
            hit_count = 0
            max_hits = params.max_regex_hits if params.mode == "REGEX" else MAX_HITS

            for i in range(total):
                if self.cancelled:
                    break
                if i % 5 == 0 or i == total - 1:
                    self.progress.emit(i + 1, total)

                if hit_count >= max_hits:
                    break

                page = doc.load_page(i)
                text = page.get_text("text") or ""

                if pattern is not None:
                    deadline = self._deadline()
                    if pattern.search(text) is None:
                        self._check_deadline(deadline)
                        continue
                    rects = self._regex_rects(page, pattern, text, deadline)
                else:
                    if not page_matches(text, params, terms):
                        continue
                    rects = self._term_rects(page, terms)

                hits.append(SearchHit(page=i, snippet=make_snippet(text, terms, params.case_sensitive),
                                      rects=rects))
                hit_count += 1

                if hit_count >= max_hits:
                    break
        except SearchTimeout as exc:
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"Search failed: {exc}")
            return
        finally:
            doc.close()

        # A cancelled search must never publish partial results.  The done
        # signal still quits the worker thread, and the GUI ignores stale
        # generations anyway.
        self.done.emit([] if self.cancelled else hits)
