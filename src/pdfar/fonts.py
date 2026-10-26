"""Font catalog and resolution for page-number text.

Page numbers were originally limited to the PDF base-14 fonts, which need no
embedding.  Arbitrary system fonts are supported now: Qt supplies the family
and style lists, the platform's font index maps a chosen style to a font file
(fontconfig on Linux, the registry on Windows, the Fonts folders on macOS),
and PyMuPDF embeds that file in the numbered copy.

Fonts must already exist on the system.  :data:`RECOMMENDED_FONTS` lists the
open families shipped by common Debian/Ubuntu packages, and the platform
catalogs add the families that Windows and macOS install by default, so the
UI shows fitting suggestions and install hints on every system.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import FrozenSet, Iterable, Optional, Tuple

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
    """A family worth offering, and what has to be done to get it."""

    family: str
    package: str = ""
    tier: str = "main"       # main | contrib | upstream | builtin


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

# Families a normal Windows installation provides; no install step is needed.
# They are proprietary, so they are offered for local output only and never
# bundled with the application.
WINDOWS_FONTS = (
    RecommendedFont("Arial", "", "builtin"),
    RecommendedFont("Calibri", "", "builtin"),
    RecommendedFont("Cambria", "", "builtin"),
    RecommendedFont("Candara", "", "builtin"),
    RecommendedFont("Consolas", "", "builtin"),
    RecommendedFont("Constantia", "", "builtin"),
    RecommendedFont("Corbel", "", "builtin"),
    RecommendedFont("Courier New", "", "builtin"),
    RecommendedFont("Georgia", "", "builtin"),
    RecommendedFont("Segoe UI", "", "builtin"),
    RecommendedFont("Tahoma", "", "builtin"),
    RecommendedFont("Times New Roman", "", "builtin"),
    RecommendedFont("Trebuchet MS", "", "builtin"),
    RecommendedFont("Verdana", "", "builtin"),
)

# The equivalent shortlist of families shipped with macOS.
MACOS_FONTS = (
    RecommendedFont("Avenir", "", "builtin"),
    RecommendedFont("Avenir Next", "", "builtin"),
    RecommendedFont("Courier New", "", "builtin"),
    RecommendedFont("Futura", "", "builtin"),
    RecommendedFont("Georgia", "", "builtin"),
    RecommendedFont("Helvetica Neue", "", "builtin"),
    RecommendedFont("Menlo", "", "builtin"),
    RecommendedFont("Monaco", "", "builtin"),
    RecommendedFont("Optima", "", "builtin"),
    RecommendedFont("Palatino", "", "builtin"),
    RecommendedFont("Times New Roman", "", "builtin"),
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


@lru_cache(maxsize=1)
def _families_by_normalized() -> dict:
    return {_normalized_family(family): family
            for family in QFontDatabase.families()}


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
        normalized = _families_by_normalized().get(
            _normalized_family(family))
        return normalized or family
    matches.sort(key=lambda name: (
        not name.casefold().endswith("[urw]"), len(name), name.casefold()))
    return matches[0]


def family_installed(family: str) -> bool:
    return qt_family(family).casefold() in installed_families()


def platform_key() -> str:
    """``linux``, ``windows``, ``macos`` or ``other`` for this interpreter."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"


def platform_label() -> str:
    return {"linux": "Debian/Ubuntu", "windows": "Windows",
            "macos": "macOS"}.get(platform_key(), "this system")


def recommended_fonts() -> Tuple[RecommendedFont, ...]:
    """Recommended catalog for the current platform, built-ins first."""
    key = platform_key()
    if key == "windows":
        return WINDOWS_FONTS + RECOMMENDED_FONTS
    if key == "macos":
        return MACOS_FONTS + RECOMMENDED_FONTS
    return RECOMMENDED_FONTS


def recommended_installable() -> Tuple[Tuple[RecommendedFont, bool], ...]:
    """Catalog order plus whether each family is currently installed."""
    return tuple((font, family_installed(font.family))
                 for font in recommended_fonts())


def missing_recommended() -> Tuple[RecommendedFont, ...]:
    return tuple(font for font, installed in recommended_installable()
                 if not installed)


def recommended_tooltip(font: RecommendedFont) -> str:
    """The tree tooltip for a recommended entry."""
    if font.tier == "builtin":
        return f"Included with {platform_label()}."
    if platform_key() in {"linux", "other"} and font.package:
        return f"Package: {font.package}"
    if font.package:
        return "Recommended open-source font"
    return "Upstream OFL font"


def recommended_hint(font: RecommendedFont) -> str:
    """How to obtain a recommended family that is not installed."""
    if font.tier == "builtin":
        return (f"Not installed, although {platform_label()} normally "
                "includes it.")
    key = platform_key()
    if key == "windows":
        return ("Not installed. Download it from Google Fonts or the "
                "upstream release and install it via Settings → "
                "Personalization → Fonts.")
    if key == "macos":
        return ("Not installed. Use the upstream OFL release, or search "
                "Homebrew font casks with `brew search font-<family>`.")
    if font.tier == "contrib":
        return (f"sudo apt install {font.package} "
                "(the contrib section must be enabled)")
    if font.tier == "upstream" or not font.package:
        return ("Not packaged by Debian/Ubuntu; install the upstream "
                "OFL release.")
    return f"sudo apt install {font.package}"


def install_hint(fonts: Iterable[RecommendedFont]) -> str:
    """Shell command for the missing Debian/Ubuntu packages, else ``""``."""
    if platform_key() not in {"linux", "other"}:
        return ""
    packages = []
    for font in fonts:
        if font.tier != "main" and font.tier != "contrib":
            continue
        if not font.package:
            continue
        if font.package not in packages:
            packages.append(font.package)
    if not packages:
        return ""
    return "sudo apt install " + " ".join(sorted(packages))


def missing_note(fonts: Iterable[RecommendedFont]) -> str:
    """Multi-line install guidance shown under the font dialog."""
    fonts = tuple(font for font in fonts if font.tier != "builtin")
    if not fonts:
        return ""
    key = platform_key()
    names = ", ".join(font.family for font in fonts)
    if key == "windows":
        return ("Missing recommended fonts: " + names + "\n"
                "Download them from Google Fonts (or the upstream release) "
                "and install them via Settings → Personalization → Fonts.")
    if key == "macos":
        return ("Missing recommended fonts: " + names + "\n"
                "Install them from the upstream release, or find a Homebrew "
                "font cask with `brew search font-<family>`.")
    lines = []
    command = install_hint(fonts)
    if command:
        lines.append("Missing recommended packages — " + command)
    contrib = sorted({font.family for font in fonts
                      if font.tier == "contrib"}, key=str.casefold)
    if contrib:
        lines.append(
            "In Debian/Ubuntu contrib: " + ", ".join(contrib)
            + " (enable the contrib section first).")
    upstream = sorted({font.family for font in fonts
                       if font.tier == "upstream"}, key=str.casefold)
    if upstream:
        lines.append(
            "Not packaged by Debian/Ubuntu: " + ", ".join(upstream)
            + " (install the upstream OFL release).")
    return "\n".join(lines)


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


def _normalized_family(name: str) -> str:
    """Family key without spaces/punctuation (``Helvetica Neue`` → ``helveticaneue``)."""
    return "".join(char for char in name.casefold() if char.isalnum())


def _aliases(name: str) -> FrozenSet[str]:
    """Raw and normalized keys, so ``HelveticaNeue`` matches ``Helvetica Neue``."""
    name = name.strip()
    if not name:
        return frozenset()
    return frozenset({name.casefold(), _normalized_family(name)})


_FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".ttc"})
_FONT_STYLE_WORDS = frozenset({
    "thin", "extralight", "ultralight", "light", "regular", "book", "normal",
    "medium", "semibold", "demibold", "bold", "extrabold", "ultrabold",
    "black", "heavy", "italic", "oblique",
})
_REGULAR_STYLES = frozenset({"regular", "normal", "roman", "book"})
_FACE_TYPE_RE = re.compile(
    r"\s*\((?:TrueType|OpenType|All res|PostScript|Type\s?1)\)\s*$",
    re.IGNORECASE)


def _fontconfig_index() -> Tuple[
        Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
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
        family_set = family_set | {_normalized_family(name)
                                   for name in family_set}
        style_set = _split_field(styles)
        style_set = style_set | {_normalized_family(name)
                                 for name in style_set}
        if family_set and path.strip():
            entries.append((family_set, style_set, path.strip()))
    return tuple(entries)


def _strip_face_type(value_name: str) -> str:
    """``Arial Bold (TrueType)`` → ``Arial Bold``."""
    return _FACE_TYPE_RE.sub("", value_name).strip()


def _split_face_name(name: str) -> Tuple[str, str]:
    """Split a face name into ``(family, style)`` using trailing style words."""
    words = name.replace("_", " ").replace("-", " ").split()
    style = []
    while len(words) > 1 and words[-1].casefold() in _FONT_STYLE_WORDS:
        style.insert(0, words.pop())
    family = " ".join(words).strip()
    return family, " ".join(style) or "Regular"


def _add_face(entries, family: str, style: str, path: str) -> None:
    """Register a face under both its family and its full face name."""
    if not family:
        return
    entries.append((_aliases(family), _aliases(style), path))
    if style.casefold() not in _REGULAR_STYLES:
        entries.append((_aliases(f"{family} {style}"),
                        _aliases(style), path))


def _directory_entries(directories: Iterable[str]) -> list:
    """Faces discovered by scanning font folders (Windows/macOS fallback)."""
    entries = []
    for directory in directories:
        try:
            names = os.listdir(os.path.expanduser(directory))
        except OSError:
            continue
        for filename in names:
            stem, extension = os.path.splitext(filename)
            if extension.casefold() not in _FONT_EXTENSIONS:
                continue
            family, style = _split_face_name(stem)
            _add_face(entries, family, style,
                      os.path.join(os.path.expanduser(directory), filename))
    return entries


def _windows_font_dirs() -> Tuple[str, ...]:
    directories = []
    windows = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windows:
        directories.append(os.path.join(windows, "Fonts"))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        directories.append(
            os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return tuple(directories)


def _windows_registry_names() -> Tuple[Tuple[str, str], ...]:
    """``(value name, file)`` pairs from the machine and user font keys."""
    try:
        import winreg
    except ImportError:  # pragma: no cover - only reachable off Windows
        return ()
    subkey = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    pairs = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    pairs.append((str(name), str(value)))
                    index += 1
        except OSError:
            continue
    return tuple(pairs)


def _resolve_font_path(value: str, directories: Iterable[str]) -> Optional[str]:
    value = os.path.expandvars(str(value).strip())
    if not value:
        return None
    candidates = [value] if os.path.isabs(value) else [
        os.path.join(os.path.expanduser(directory), value)
        for directory in directories]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _windows_entries(pairs: Iterable[Tuple[str, str]],
                     directories: Iterable[str]) -> list:
    entries = []
    for value_name, value in pairs:
        path = _resolve_font_path(value, directories)
        if not path:
            continue
        name = _strip_face_type(value_name)
        family, style = _split_face_name(name)
        _add_face(entries, family, style, path)
        # A multi-word face such as "Arial Black" is also a family of its own.
        if family != name:
            _add_face(entries, name, style, path)
    return entries


def _windows_font_index() -> Tuple[
        Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
    directories = _windows_font_dirs()
    entries = _windows_entries(_windows_registry_names(), directories)
    entries.extend(_directory_entries(directories))
    return tuple(entries)


def _macos_font_dirs() -> Tuple[str, ...]:
    return ("~/Library/Fonts", "/Library/Fonts",
            "/System/Library/Fonts",
            "/System/Library/Fonts/Supplemental",
            "/Network/Library/Fonts")


def _macos_font_index(directories: Optional[Iterable[str]] = None) -> Tuple[
        Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
    return tuple(_directory_entries(
        _macos_font_dirs() if directories is None else directories))


@lru_cache(maxsize=4)
def _font_index_for(platform: str) -> Tuple[
        Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
    if platform == "windows":
        return _windows_font_index()
    if platform == "macos":
        return _macos_font_index()
    return _fontconfig_index()


def _font_index() -> Tuple[
        Tuple[FrozenSet[str], FrozenSet[str], str], ...]:
    return _font_index_for(platform_key())


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
    """Path to the best platform font match for *family*/*style*, if any."""
    wanted_keys = _aliases(display_family(family))
    candidates = [entry for entry in _font_index()
                  if entry[0] & wanted_keys]
    if not candidates:
        return None
    wanted = (style or "Regular").casefold()
    exact = [entry for entry in candidates if wanted in entry[1]]
    if exact:
        return _prefer_uncondensed(exact, wanted)
    if wanted in _REGULAR_STYLES:
        regular = [entry for entry in candidates
                   if entry[1] & _REGULAR_STYLES]
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
    _families_by_normalized.cache_clear()
    _font_index_for.cache_clear()


__all__ = [
    "BASE14_FONTS", "BASE14_KEYS", "BASE14_LABELS", "BASE14_PREVIEW",
    "BASE14_STYLES", "MACOS_FONTS", "RECOMMENDED_FONTS", "RecommendedFont",
    "WINDOWS_FONTS", "all_families", "apply_style", "base14_alias",
    "clear_caches", "default_style", "family_installed", "font_file",
    "install_hint", "installed_families", "missing_note",
    "missing_recommended", "platform_key", "platform_label",
    "qt_preview_font", "recommended_fonts", "recommended_hint",
    "recommended_installable", "recommended_tooltip", "resolve_family_label",
    "style_sort_key", "styles_for",
]
