"""Font catalog and resolution for page-number text.

Page numbers were originally limited to the PDF base-14 fonts, which need no
embedding.  Arbitrary system fonts are supported now: Qt supplies the family
and style lists, fontconfig (``fc-list``) maps a chosen style to a font file,
and PyMuPDF embeds that file in the numbered copy.

Fonts must already exist on the system.  :data:`RECOMMENDED_FONTS` lists the
families shipped by common Debian/Ubuntu packages so the UI can show them
first and tell users which package to install.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple

from PyQt6.QtGui import QFont, QFontDatabase

# Base-14 font key, UI label, and the family used for the Qt preview.
BASE14_FONTS = (
    ("helv", "Helvetica (built-in)", "Sans Serif"),
    ("tiro", "Times (built-in)", "Serif"),
    ("cour", "Courier (built-in)", "Monospace"),
)
BASE14_KEYS = {key for key, _label, _family in BASE14_FONTS}
BASE14_LABELS = {key: label for key, label, _family in BASE14_FONTS}
BASE14_PREVIEW = {key: family for key, _label, family in BASE14_FONTS}
BASE14_STYLES = ("Regular", "Bold", "Italic", "Bold Italic")

# PyMuPDF's short names for the styled base-14 faces.
_BASE14_ALIASES = {
    "helv": {"regular": "helv", "bold": "hebo",
             "italic": "heit", "bold italic": "hebi"},
    "tiro": {"regular": "tiro", "bold": "tibo",
             "italic": "tiit", "bold italic": "tibi"},
    "cour": {"regular": "cour", "bold": "cobo",
             "italic": "coit", "bold italic": "cobi"},
}


@dataclass(frozen=True)
class RecommendedFont:
    """A family worth installing, and the Debian/Ubuntu package that has it."""

    family: str
    package: str = ""
    tier: str = "main"       # main | contrib | upstream


# Order matters: it is the order shown in the dialog's Recommended section.
RECOMMENDED_FONTS = (
    # Liberation: metric-compatible with Arial / Times New Roman / Courier New.
    RecommendedFont("Liberation Sans", "fonts-liberation"),
    RecommendedFont("Liberation Serif", "fonts-liberation"),
    RecommendedFont("Liberation Mono", "fonts-liberation"),
    # DejaVu: broad Unicode coverage.
    RecommendedFont("DejaVu Sans", "fonts-dejavu"),
    RecommendedFont("DejaVu Serif", "fonts-dejavu"),
    RecommendedFont("DejaVu Sans Mono", "fonts-dejavu"),
    # Noto (Google Fonts).
    RecommendedFont("Noto Sans", "fonts-noto-core"),
    RecommendedFont("Noto Serif", "fonts-noto-core"),
    RecommendedFont("Noto Sans Mono", "fonts-noto-core"),
    # Popular Google Fonts packages.
    RecommendedFont("Roboto", "fonts-roboto"),
    RecommendedFont("Open Sans", "fonts-open-sans"),
    RecommendedFont("Lato", "fonts-lato"),
    RecommendedFont("Inter", "fonts-inter"),
    RecommendedFont("Montserrat", "fonts-montserrat"),
    RecommendedFont("JetBrains Mono", "fonts-jetbrains-mono"),
    RecommendedFont("Fira Code", "fonts-firacode"),
    RecommendedFont("Ubuntu", "fonts-ubuntu"),
    RecommendedFont("Ubuntu Mono", "fonts-ubuntu"),
    RecommendedFont("Cantarell", "fonts-cantarell"),
    # Metric-compatible replacements for Calibri / Cambria.
    RecommendedFont("Carlito", "fonts-crosextra-carlito"),
    RecommendedFont("Caladea", "fonts-crosextra-caladea"),
    # PostScript base-35 clones.
    RecommendedFont("Nimbus Sans", "fonts-urw-base35"),
    RecommendedFont("Nimbus Roman", "fonts-urw-base35"),
    RecommendedFont("Nimbus Mono PS", "fonts-urw-base35"),
    # IBM Plex lives in Debian contrib, not main.
    RecommendedFont("IBM Plex Sans", "fonts-ibm-plex", "contrib"),
    RecommendedFont("IBM Plex Serif", "fonts-ibm-plex", "contrib"),
    RecommendedFont("IBM Plex Mono", "fonts-ibm-plex", "contrib"),
    # Red Hat fonts are OFL but not packaged by Debian/Ubuntu.
    RecommendedFont("Red Hat Text", "", "upstream"),
    RecommendedFont("Red Hat Display", "", "upstream"),
    RecommendedFont("Red Hat Mono", "", "upstream"),
)


def style_sort_key(style: str) -> Tuple[int, str]:
    """Regular/Book first, then the remaining styles alphabetically."""
    key = style.casefold()
    if key in {"regular", "book", "normal", "roman"}:
        return (0, key)
    return (1, key)


@lru_cache(maxsize=1)
def installed_families() -> FrozenSet[str]:
    """Case-insensitive set of families Qt can use right now."""
    return frozenset(family.casefold() for family in QFontDatabase.families())


@lru_cache(maxsize=1)
def _families_by_fold() -> dict:
    return {family.casefold(): family for family in QFontDatabase.families()}


def display_family(family: str) -> str:
    """Drop Qt's foundry suffix (``Nimbus Sans [urw]`` → ``Nimbus Sans``)."""
    if family in BASE14_KEYS:
        return family
    base = family.split(" [", 1)[0].strip()
    return base or family


def qt_family(family: str) -> str:
    """Resolve a canonical family to the name Qt actually exposes.

    Fontconfig/JT sometimes append a foundry (``Nimbus Sans [urw]``); the
    catalog keeps the friendly name and resolves it here.
    """
    if family in BASE14_KEYS:
        return family
    lookup = _families_by_fold()
    exact = lookup.get(family.casefold())
    if exact:
        return exact
    prefix = family.casefold() + " ["
    matches = [name for key, name in lookup.items()
               if key.startswith(prefix)]
    if not matches:
        return family
    matches.sort(key=lambda name: (
        not name.casefold().endswith("[urw]"), len(name), name.casefold()))
    return matches[0]


def family_installed(family: str) -> bool:
    return qt_family(family).casefold() in installed_families()


def recommended_installable() -> Tuple[Tuple[RecommendedFont, bool], ...]:
    """Catalog order plus whether each family is currently installed."""
    return tuple((font, family_installed(font.family))
                 for font in RECOMMENDED_FONTS)


def missing_recommended() -> Tuple[RecommendedFont, ...]:
    return tuple(font for font, installed in recommended_installable()
                 if not installed)


def install_hint(fonts) -> str:
    """Shell command for the missing apt packages (empty when none)."""
    packages = []
    for font in fonts:
        if font.tier == "upstream" or not font.package:
            continue
        if font.package not in packages:
            packages.append(font.package)
    if not packages:
        return ""
    return "sudo apt install " + " ".join(sorted(packages))


def all_families() -> Tuple[str, ...]:
    """Installed families without duplicate foundry variants."""
    chosen = {}
    for name in sorted(QFontDatabase.families(), key=str.casefold):
        base = display_family(name).casefold()
        current = chosen.get(base)
        if current is None:
            chosen[base] = name
        elif (name.casefold().endswith("[urw]")
              and not current.casefold().endswith("[urw]")):
            chosen[base] = name
    return tuple(sorted(chosen.values(), key=lambda name: display_family(
        name).casefold()))


def styles_for(family: str) -> Tuple[str, ...]:
    """Styles offered for *family* (base-14 keys use their four faces)."""
    if family in BASE14_KEYS:
        return BASE14_STYLES
    styles = QFontDatabase.styles(family)
    if not styles:
        return ("Regular",)
    unique = {style for style in styles if style}
    return tuple(sorted(unique, key=style_sort_key)) or ("Regular",)


def default_style(family: str, preferred: str = "") -> str:
    """Pick *preferred* when available, otherwise the family's regular face."""
    styles = styles_for(family)
    if preferred:
        # Some families call their italic face "Oblique"; treat the names as
        # equivalent when selecting a saved style.
        wanted = preferred.casefold().replace("italic", "oblique")
        for style in styles:
            if style.casefold().replace("italic", "oblique") == wanted:
                return style
        if wanted in {"regular", "normal", "roman"}:
            for style in styles:
                if style.casefold() in {"regular", "normal", "roman", "book"}:
                    return style
    return min(styles, key=style_sort_key)


def base14_alias(family: str, style: str = "Regular") -> Optional[str]:
    """PyMuPDF font name for a base-14 family/style pair, else ``None``."""
    aliases = _BASE14_ALIASES.get(family)
    if aliases is None:
        return None
    key = style.casefold()
    if "bold" in key or "black" in key or "heavy" in key:
        return aliases["bold italic"] if ("italic" in key or "oblique" in key) \
            else aliases["bold"]
    if "italic" in key or "oblique" in key:
        return aliases["italic"]
    return aliases["regular"]


def resolve_family_label(family: str) -> str:
    """User-facing label; base-14 keys keep their descriptive label."""
    return BASE14_LABELS.get(family, family)


def apply_style(font: QFont, style: str, named: bool = True) -> None:
    """Apply a style *name* (Bold, Light, Italic, …) to a Qt font."""
    key = style.casefold()
    weight = QFont.Weight.Normal
    for word, value in (
            ("thin", QFont.Weight.Thin),
            ("extralight", QFont.Weight.ExtraLight),
            ("ultralight", QFont.Weight.ExtraLight),
            ("semilight", QFont.Weight.Light),
            ("demilight", QFont.Weight.Light),
            ("light", QFont.Weight.Light),
            ("medium", QFont.Weight.Medium),
            ("semibold", QFont.Weight.DemiBold),
            ("demibold", QFont.Weight.DemiBold),
            ("extrabold", QFont.Weight.ExtraBold),
            ("ultrabold", QFont.Weight.ExtraBold),
            ("bold", QFont.Weight.Bold),
            ("black", QFont.Weight.Black),
            ("heavy", QFont.Weight.Black),
    ):
        if word in key:
            weight = value
            break
    if weight is not QFont.Weight.Normal:
        font.setWeight(weight)
    if "italic" in key or "oblique" in key:
        font.setItalic(True)
    if style and named:
        font.setStyleName(style)


def qt_preview_font(family: str, style: str, pixel_size: int) -> QFont:
    """Qt font used for previews and the dialog sample."""
    if family in BASE14_KEYS:
        font = QFont(BASE14_PREVIEW[family])
    else:
        font = QFont(family)
    apply_style(font, style, named=family not in BASE14_KEYS)
    font.setPixelSize(max(1, int(pixel_size)))
    return font


def _split_field(value: str) -> FrozenSet[str]:
    return frozenset(part.strip().casefold()
                     for part in value.split(",") if part.strip())


@lru_cache(maxsize=1)
def _font_index() -> Tuple[Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
    """``(family aliases, style aliases, file)`` from fontconfig."""
    try:
        result = subprocess.run(
            ["fc-list", "-f", "%{family}\t%{style}\t%{file}\n"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return ()
    entries = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        families, styles, path = parts
        family_set = _split_field(families)
        style_set = _split_field(styles)
        if family_set and path.strip():
            entries.append((family_set, style_set, path.strip()))
    return tuple(entries)


def _style_score(styles: FrozenSet[str], wanted: str) -> int:
    want_bold = any(word in wanted for word in ("bold", "black", "heavy"))
    want_italic = "italic" in wanted or "oblique" in wanted
    best = -1
    for style in styles:
        bold = any(word in style for word in ("bold", "black", "heavy"))
        italic = "italic" in style or "oblique" in style
        score = int(bold == want_bold) + int(italic == want_italic)
        best = max(best, score)
    return best


def _prefer_uncondensed(candidates, wanted: str) -> str:
    def score(entry):
        families, styles, _path = entry
        penalty = 0
        for word in ("condensed", "narrow"):
            if word not in wanted:
                if any(word in style for style in styles):
                    penalty += 4
                if any(word in family for family in families):
                    penalty += 2
        return -penalty

    return max(candidates, key=score)[2]


def font_file(family: str, style: str = "Regular") -> Optional[str]:
    """Path to the best fontconfig match for *family*/*style*, if any."""
    wanted_family = display_family(family).casefold()
    candidates = [entry for entry in _font_index()
                  if wanted_family in entry[0]]
    if not candidates:
        return None
    wanted = (style or "Regular").casefold()
    exact = [entry for entry in candidates if wanted in entry[1]]
    if exact:
        return _prefer_uncondensed(exact, wanted)
    if wanted in {"regular", "normal", "roman", "book"}:
        regular = [entry for entry in candidates
                   if entry[1] & {"regular", "normal", "roman", "book"}]
        if regular:
            return _prefer_uncondensed(regular, wanted)
    scored = [(entry, _style_score(entry[1], wanted))
              for entry in candidates]
    best = max(score for _entry, score in scored)
    return _prefer_uncondensed(
        [entry for entry, score in scored if score == best], wanted)


def clear_caches() -> None:
    """Drop cached font databases (used when fonts change on disk)."""
    installed_families.cache_clear()
    _families_by_fold.cache_clear()
    _font_index.cache_clear()


__all__ = [
    "BASE14_FONTS", "BASE14_KEYS", "BASE14_LABELS", "BASE14_PREVIEW",
    "BASE14_STYLES", "RECOMMENDED_FONTS", "RecommendedFont", "all_families",
    "apply_style", "base14_alias", "clear_caches", "default_style",
    "family_installed", "font_file", "install_hint", "installed_families",
    "missing_recommended", "qt_preview_font", "recommended_installable",
    "resolve_family_label", "style_sort_key", "styles_for",
]
