"""System font discovery and ranking for intelligent text substitution.

PDFAR edits existing PDF text.  When the document's own font cannot draw the
replacement characters, a substitute font has to be found on the system and
embedded.  This module owns the "find one" half:

* enumerate installed faces through **fontconfig** (``fc-list``), with a
  directory scan as a fallback, so it does not depend on a particular desktop
  environment;
* read per-face metadata lazily (family, style, weight, slant, outline format,
  ``fsType`` embedding permissions, character coverage) and cache it;
* rank candidates against the *original* font's family, style and metrics, so
  the replacement looks as close as possible — never by family name alone.

Nothing here writes to a PDF; embedding lives in :mod:`pdfar.font_embed`.

The platform-specific discovery is isolated in :func:`system_faces`.  Adding a
Windows or macOS backend means adding another enumerator that yields
:class:`FontFace` objects; the ranking and the rest of the pipeline are
platform neutral.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:  # pragma: no cover - older PyMuPDF
    import fitz

try:  # optional: needed for precise licensing/coverage/embedding metadata
    from fontTools.ttLib import TTFont as _TTFont
except Exception:  # pragma: no cover - fontTools is an optional extra
    _TTFont = None


FONTTOOLS_AVAILABLE = _TTFont is not None

#: Directories scanned when fontconfig is unavailable.
FONT_DIRS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.local/share/fonts"),
    os.path.expanduser("~/.fonts"),
)

_FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".otc")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class FontFace:
    """One installed font face (a file plus a face index inside it)."""

    path: str
    index: int = 0
    family: str = ""
    style: str = ""
    weight: int = 400          # CSS-style 100..900
    italic: bool = False
    monospaced: bool = False
    serif: bool = False
    #: Outline format: ``"truetype"`` (glyf), ``"cff"`` or ``"unknown"``.
    outline: str = "unknown"
    #: ``fsType`` permission nibble (0 installable, 2 restricted, 4 print,
    #: 8 editable), or ``None`` when it could not be read.
    fs_type: Optional[int] = None
    #: False when the licence forbids embedding.
    embeddable: bool = True
    #: False when ``fsType`` forbids subsetting.
    subset_allowed: bool = True
    source: str = "fontconfig"

    @property
    def display_name(self) -> str:
        family = self.family or os.path.basename(self.path)
        return f"{family} {self.style}".strip() if self.style else family

    @property
    def bold(self) -> bool:
        return self.weight >= 600


@dataclass
class FontQuery:
    """What the original text looked like, used to rank substitutes."""

    basefont: str = ""
    bold: bool = False
    italic: bool = False
    serif: Optional[bool] = None
    monospaced: Optional[bool] = None
    #: The text that must be rendered (coverage requirement).
    text: str = ""
    #: Per-character advances of the original font, in em units.
    advances: Sequence[float] = ()

    @property
    def original_width(self) -> float:
        return float(sum(self.advances))


@dataclass
class FontMatch:
    """A candidate face with the evidence used to rank it."""

    face: FontFace
    score: float
    missing: Tuple[str, ...] = ()
    width_ratio: float = 1.0
    notes: Tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.missing and self.face.embeddable


# --------------------------------------------------------------------------- #
# Metadata cache
# --------------------------------------------------------------------------- #
_FACE_CACHE: Dict[Tuple[str, int], Dict[str, object]] = {}
_FACES_CACHE: Optional[List[FontFace]] = None


def clear_cache() -> None:
    """Forget discovered faces and per-face metadata (tests, font installs)."""
    global _FACES_CACHE
    _FACES_CACHE = None
    _FACE_CACHE.clear()


def _css_weight(fc_weight: int) -> int:
    """Map fontconfig's weight scale to CSS weights."""
    table = (
        (0, 100), (40, 200), (50, 300), (75, 350), (80, 400),
        (100, 500), (180, 600), (200, 700), (205, 750), (210, 900),
    )
    best = 400
    best_delta = 10 ** 9
    for raw, css in table:
        delta = abs(fc_weight - raw)
        if delta < best_delta:
            best_delta = delta
            best = css
    return best


def _first_name(value: str) -> str:
    """fontconfig joins localized names with commas; take the primary one."""
    return value.split(",")[0].strip() if value else ""


def _run_fc_list() -> Optional[str]:
    exe = shutil.which("fc-list")
    if not exe:
        return None
    fmt = ("%{file}\t%{index}\t%{family}\t%{style}\t%{weight}\t"
           "%{slant}\t%{spacing}\t%{fontformat}\n")
    try:
        out = subprocess.run(
            [exe, "--format", fmt], capture_output=True, timeout=20,
            check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", "replace")


def _faces_from_fontconfig() -> List[FontFace]:
    text = _run_fc_list()
    if not text:
        return []
    faces: List[FontFace] = []
    seen = set()
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        path, index, family, style, weight, slant, spacing, fmt = parts[:8]
        if not path or not os.path.isfile(path):
            continue
        try:
            idx = int(index or 0)
        except ValueError:
            idx = 0
        key = (path, idx)
        if key in seen:
            continue
        seen.add(key)
        try:
            w = int(weight or 80)
        except ValueError:
            w = 80
        italic = slant.strip() not in ("", "0", "roman")
        faces.append(FontFace(
            path=path,
            index=idx,
            family=_first_name(family),
            style=_first_name(style),
            weight=_css_weight(w),
            italic=italic,
            monospaced=spacing.strip() == "100",
            outline=_outline_from_format(fmt),
            source="fontconfig",
        ))
    return faces


def _outline_from_format(fmt: str) -> str:
    f = (fmt or "").strip().lower()
    if f in ("cff", "type 1", "type1"):
        return "cff" if f == "cff" else "unknown"
    if f in ("truetype", "true type", "truetype collection",
             "true type collection"):
        return "truetype"
    return "unknown"


def _faces_from_directories() -> List[FontFace]:
    faces: List[FontFace] = []
    for root in FONT_DIRS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                if not name.lower().endswith(_FONT_SUFFIXES):
                    continue
                path = os.path.join(dirpath, name)
                meta = face_metadata(path, 0)
                faces.append(FontFace(
                    path=path,
                    family=os.path.splitext(name)[0],
                    style="",
                    weight=int(meta.get("weight", 400)),
                    italic=bool(meta.get("italic", False)),
                    serif=bool(meta.get("serif", False)),
                    monospaced=bool(meta.get("monospaced", False)),
                    outline=str(meta.get("outline", "unknown")),
                    fs_type=meta.get("fs_type"),  # type: ignore[arg-type]
                    embeddable=bool(meta.get("embeddable", True)),
                    subset_allowed=bool(meta.get("subset_allowed", True)),
                    source="scan",
                ))
    return faces


def system_faces(refresh: bool = False) -> List[FontFace]:
    """All installed faces, cached for the process.

    Uses fontconfig when available and falls back to scanning the standard
    font directories, so the result does not depend on a specific desktop
    environment.
    """
    global _FACES_CACHE
    if _FACES_CACHE is None or refresh:
        faces = _faces_from_fontconfig()
        if not faces:
            faces = _faces_from_directories()
        _FACES_CACHE = faces
    return list(_FACES_CACHE)


# --------------------------------------------------------------------------- #
# Per-face metadata
# --------------------------------------------------------------------------- #
def _read_fs_type(font) -> Optional[int]:
    try:
        os2 = font["OS/2"]
    except Exception:
        return None
    try:
        return int(os2.fsType)
    except Exception:
        return None


def _permissions(fs_type: Optional[int]) -> Tuple[bool, bool, str]:
    """Return ``(embeddable, subset_allowed, note)`` for an ``fsType`` value."""
    if fs_type is None:
        # Unknown (Type1, damaged tables): assume installable.  The embedding
        # step re-checks and refuses if the file cannot be read.
        return True, True, ""
    if fs_type & 0x0200:
        return False, False, "the font may only be embedded as a bitmap"
    perm = fs_type & 0x000F
    subset_allowed = not (fs_type & 0x0100)
    if perm == 0x0002:
        return False, False, "the font licence forbids embedding"
    if perm == 0x0004:
        # Embedding for viewing/printing is allowed; the document must stay
        # read-only, which we honour by only ever adding a subset.
        return True, subset_allowed, "the font is licensed for preview and printing only"
    # 0 (installable) and 8 (editable) are both fine for embedding.
    return True, subset_allowed, ""


def face_metadata(path: str, index: int = 0) -> Dict[str, object]:
    """Read and cache the metadata needed to rank and embed a face.

    Uses fontTools when installed (precise ``fsType``, cmap, metrics) and
    falls back to PyMuPDF otherwise.  Never raises: unknown values are left
    out so callers can decide how strict to be.
    """
    key = (path, index)
    cached = _FACE_CACHE.get(key)
    if cached is not None:
        return cached
    meta: Dict[str, object] = {}
    if _TTFont is not None:
        try:
            font = _TTFont(path, fontNumber=index, lazy=True)
            try:
                tables = set(font.keys())
                meta["outline"] = (
                    "truetype" if "glyf" in tables
                    else "cff" if "CFF " in tables else "unknown")
                fs_type = _read_fs_type(font)
                meta["fs_type"] = fs_type
                embeddable, subset_allowed, note = _permissions(fs_type)
                meta["embeddable"] = embeddable
                meta["subset_allowed"] = subset_allowed
                if note:
                    meta["licence_note"] = note
                if "head" in tables:
                    meta["upem"] = int(font["head"].unitsPerEm)
                if "OS/2" in tables:
                    os2 = font["OS/2"]
                    meta["weight"] = int(getattr(os2, "usWeightClass", 400))
                    meta["italic"] = bool(
                        getattr(os2, "fsSelection", 0) & 0x01)
                    try:
                        meta["serif"] = bool(os2.panose.bFamilyType in (2, 3, 4, 5, 6, 7))
                    except Exception:
                        pass
                if "post" in tables:
                    try:
                        meta["monospaced"] = bool(
                            font["post"].isFixedPitch)
                    except Exception:
                        pass
                try:
                    meta["family"] = font["name"].getDebugName(1) or ""
                except Exception:
                    pass
                try:
                    meta["style"] = font["name"].getDebugName(2) or ""
                except Exception:
                    pass
                if "cmap" in tables:
                    meta["codepoints"] = frozenset(
                        font.getBestCmap().keys())
            finally:
                font.close()
        except Exception:
            meta.setdefault("embeddable", False)
            meta.setdefault("outline", "unknown")
    else:  # pragma: no cover - exercised only without fontTools
        try:
            font = fitz.Font(fontfile=path)
            meta["outline"] = "truetype"
            meta["embeddable"] = bool(font.flags.get("embed", 1))
            meta["weight"] = 700 if font.is_bold else 400
            meta["italic"] = bool(font.is_italic)
            meta["serif"] = bool(font.is_serif)
            meta["monospaced"] = bool(font.is_monospaced)
            meta["codepoints"] = frozenset(font.valid_codepoints())
        except Exception:
            meta["embeddable"] = False
    _FACE_CACHE[key] = meta
    return meta


def face_from_file(path: str, index: int = 0) -> FontFace:
    """Build a :class:`FontFace` for a user-chosen font file."""
    meta = face_metadata(path, index)
    family = str(meta.get("family") or os.path.splitext(
        os.path.basename(path))[0])
    style = str(meta.get("style") or "")
    return FontFace(
        path=path,
        index=index,
        family=family,
        style=style,
        weight=int(meta.get("weight", 400)),
        italic=bool(meta.get("italic", False)),
        monospaced=bool(meta.get("monospaced", False)),
        serif=bool(meta.get("serif", False)),
        outline=str(meta.get("outline", "unknown")),
        fs_type=meta.get("fs_type"),  # type: ignore[arg-type]
        embeddable=bool(meta.get("embeddable", False)),
        subset_allowed=bool(meta.get("subset_allowed", True)),
        source="user",
    )


def face_codepoints(face: FontFace) -> Optional[frozenset]:
    """The set of Unicode code points the face can draw, or ``None``."""
    cps = face_metadata(face.path, face.index).get("codepoints")
    return cps if isinstance(cps, frozenset) else None


def supports(face: FontFace, text: str) -> bool:
    """True when every character of *text* has a glyph in *face*."""
    cps = face_codepoints(face)
    if cps is not None:
        return all(ord(ch) in cps for ch in text)
    try:
        font = fitz.Font(fontfile=face.path)
        return all(font.has_glyph(ord(ch)) for ch in text)
    except Exception:
        return False


def missing_characters(face: FontFace, text: str) -> Tuple[str, ...]:
    """Characters of *text* that *face* cannot draw, in order of appearance."""
    cps = face_codepoints(face)
    if cps is None:
        return tuple(text)
    seen: List[str] = []
    for ch in text:
        if ord(ch) not in cps and ch not in seen:
            seen.append(ch)
    return tuple(seen)


def load_face_font(face: FontFace) -> Optional[fitz.Font]:
    """Load a face through PyMuPDF for metrics/rendering checks.

    PyMuPDF always opens face 0 of a collection; the embedding path extracts
    the requested face with fontTools instead.
    """
    try:
        return fitz.Font(fontfile=face.path)
    except Exception:
        return None


def face_advances(face: FontFace, text: str) -> Optional[Tuple[float, ...]]:
    """Per-character advances of *text* in *face*, in em units."""
    cps = face_codepoints(face)
    if cps is None:
        return None
    meta = face_metadata(face.path, face.index)
    upem = int(meta.get("upem", 0) or 0)
    if not upem or _TTFont is None:
        return None
    try:
        font = _TTFont(face.path, fontNumber=face.index, lazy=True)
        try:
            cmap = font.getBestCmap()
            hmtx = font["hmtx"]
            out: List[float] = []
            for ch in text:
                name = cmap.get(ord(ch))
                if name is None:
                    return None
                out.append(hmtx[name][0] / upem)
            return tuple(out)
        finally:
            font.close()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
_FAMILY_GROUPS: Tuple[frozenset, ...] = (
    frozenset({
        "helvetica", "helvetica neue", "arial", "liberation sans",
        "nimbus sans", "arimo", "dejavu sans", "noto sans", "free sans",
        "ubuntu", "cantarell", "open sans", "lato", "roboto",
    }),
    frozenset({
        "times", "times new roman", "liberation serif", "nimbus roman",
        "tinos", "dejavu serif", "noto serif", "free serif", "georgia",
        "source serif", "source serif 4", "liberation serif",
    }),
    frozenset({
        "courier", "courier new", "liberation mono", "nimbus mono",
        "cousine", "dejavu sans mono", "noto sans mono", "free mono",
        "jetbrains mono", "source code pro", "inconsolata",
    }),
)

_STYLE_WORDS = re.compile(
    r"\b(regular|normal|book|roman|medium|light|thin|black|heavy|bold|"
    r"semibold|demibold|extrabold|ultrabold|hairline|semilight|demilight|"
    r"extra|ultra|italic|oblique|condensed|semicondensed|expanded|narrow|"
    r"mt|ps|std|pro|display|text)\b",
    re.I)


def normalize_family(name: str, style: str = "") -> str:
    """Lower-case a family name and drop style/weight suffixes.

    A trailing word is only treated as a style word when the face's style
    string agrees, so "Arial Black" (style "Regular") keeps its name while
    "Helvetica-Bold" (style "Bold") becomes "helvetica".
    """
    words = re.split(r"[\s\-]+", (name or "").strip())
    style_words = set(w.lower() for w in re.split(r"[\s\-]+", style or "") if w)
    while len(words) > 1:
        last = words[-1].lower()
        if not _STYLE_WORDS.fullmatch(last):
            break
        if style_words and last not in style_words:
            break
        words.pop()
    s = " ".join(words).lower()
    s = re.sub(r"[^0-9a-z]+", " ", s).strip()
    return re.sub(r"\s+", " ", s)


def _family_group(name: str) -> Optional[int]:
    n = normalize_family(name)
    for i, group in enumerate(_FAMILY_GROUPS):
        if n in group:
            return i
    return None


def _family_score(query: FontQuery, face: FontFace) -> float:
    original = normalize_family(query.basefont)
    candidate = normalize_family(face.family, face.style)
    if not original:
        return 0.0
    if original == candidate:
        return 100.0
    if original and (original in candidate or candidate in original):
        return 70.0
    g1, g2 = _family_group(query.basefont), _family_group(face.family)
    if g1 is not None and g1 == g2:
        return 55.0
    if query.serif is not None and face.serif == query.serif:
        return 20.0
    return 0.0


def _style_score(query: FontQuery, face: FontFace) -> float:
    score = 0.0
    if query.italic == face.italic:
        score += 25.0
    else:
        score -= 40.0
    if query.bold == face.bold:
        score += 25.0
    else:
        score -= 40.0
    # Weight proximity: a Hairline/Thin face is a poor stand-in for a regular
    # one even though both are "not bold".
    target = 700 if query.bold else 400
    delta = abs(face.weight - target)
    score += max(0.0, 15.0 - delta / 40.0)
    if query.monospaced is not None and query.monospaced == face.monospaced:
        score += 10.0
    if _width_class(query.basefont) != _width_class(
            f"{face.family} {face.style}"):
        score -= 25.0
    return score


_WIDTH_CLASS_RE = re.compile(
    r"(semi[\s-]?condensed|condensed|narrow|expanded|extended|wide)", re.I)


def _width_class(name: str) -> str:
    m = _WIDTH_CLASS_RE.search(name or "")
    if not m:
        return "normal"
    word = m.group(1).lower().replace("-", "").replace(" ", "")
    if word in ("condensed", "semicondensed", "narrow"):
        return "condensed"
    return "expanded"


def _width_score(original: Sequence[float], candidate: Sequence[float]) -> float:
    """Reward a candidate whose text width matches the original's."""
    if not original or not candidate:
        return 0.0
    a, b = sum(original), sum(candidate)
    if a <= 0 or b <= 0:
        return 0.0
    ratio = b / a
    return max(0.0, 30.0 * (1.0 - min(abs(ratio - 1.0), 1.0)))


def rank_faces(query: FontQuery, faces: Iterable[FontFace],
               limit: int = 10) -> List[FontMatch]:
    """Rank *faces* as substitutes for *query*, best first.

    Coverage and embedding permission are hard requirements: a face that
    cannot draw every character, or whose licence forbids embedding, is not
    returned.  Ranking then prefers the same family, the same style and
    similar text width — family name alone is never decisive.
    """
    prefiltered: List[Tuple[float, FontFace]] = []
    for face in faces:
        if not face.embeddable:
            continue
        if face.outline not in ("truetype",):
            # Only TrueType outlines are embeddable by this phase's writer.
            continue
        base = _family_score(query, face) + _style_score(query, face)
        if base <= 0:
            continue
        prefiltered.append((base, face))
    prefiltered.sort(key=lambda item: item[0], reverse=True)

    matches: List[FontMatch] = []
    seen: Dict[Tuple[str, str, int, bool], FontMatch] = {}
    # Coverage requires opening the font; bound how many we inspect.
    for _base, face in prefiltered[:80]:
        meta = face_metadata(face.path, face.index)
        # fontconfig's weight/slant is occasionally wrong (e.g. "Arial Black"
        # reported as regular); the font's own tables are authoritative.
        effective = replace(
            face,
            weight=int(meta.get("weight", face.weight) or face.weight),
            italic=bool(meta.get("italic", face.italic)),
            serif=bool(meta.get("serif", face.serif)),
            monospaced=bool(meta.get("monospaced", face.monospaced)),
            embeddable=bool(meta.get("embeddable", face.embeddable)),
            subset_allowed=bool(meta.get("subset_allowed", face.subset_allowed)),
        )
        if not effective.embeddable:
            continue
        missing = missing_characters(effective, query.text)
        if missing:
            continue
        advances = (face_advances(effective, query.text)
                    if query.text else None)
        notes: List[str] = []
        score = (_family_score(query, effective)
                 + _style_score(query, effective))
        ratio = 1.0
        if advances is not None and query.advances:
            score += _width_score(query.advances, advances)
            total = sum(query.advances)
            if total > 0:
                ratio = sum(advances) / total
        if not effective.subset_allowed:
            notes.append("licence forbids subsetting; the whole font is embedded")
        if effective.source == "user":
            notes.append("chosen by the user")
        match = FontMatch(
            face=effective, score=score, missing=(), width_ratio=ratio,
            notes=tuple(notes))
        # Several files often expose the same family/style (e.g. duplicate
        # installations); keep the best-scoring one.
        key = (normalize_family(effective.family, effective.style),
               effective.style.lower(),
               effective.weight, effective.italic)
        if key not in seen or seen[key].score < match.score:
            seen[key] = match

    matches = list(seen.values())
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:limit]


def find_alternatives(query: FontQuery, limit: int = 10) -> List[FontMatch]:
    """Rank all installed faces as substitutes for *query*."""
    return rank_faces(query, system_faces(), limit=limit)


def best_match(query: FontQuery) -> Optional[FontMatch]:
    """The single best substitute, or ``None`` when nothing is suitable."""
    matches = find_alternatives(query, limit=1)
    return matches[0] if matches else None
