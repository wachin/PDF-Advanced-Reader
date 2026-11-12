"""Phase 4: system font discovery, metadata and ranking.

The discovery layer must work without a particular desktop environment, read
real licensing/coverage data, and rank candidates by style and metrics rather
than by family name alone.  These tests use the fonts actually installed on the
machine (skipping when a needed one is absent) and synthetic ``fc-list`` output
for the parsing paths.
"""

from __future__ import annotations

import glob
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from pdfar import font_discovery as fd
from pdfar.font_discovery import (
    FONTTOOLS_AVAILABLE,
    FontFace,
    FontQuery,
    _css_weight,
    _faces_from_fontconfig,
    _outline_from_format,
    _permissions,
    clear_cache,
    face_advances,
    face_codepoints,
    face_from_file,
    face_metadata,
    find_alternatives,
    missing_characters,
    normalize_family,
    rank_faces,
    supports,
    system_faces,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _font(*names: str):
    """First installed file matching any of the given basenames."""
    for name in names:
        hits = glob.glob(f"/usr/share/fonts/**/{name}", recursive=True)
        if hits:
            return hits[0]
    return None


DEJAVU = _font("DejaVuSans.ttf")
DEJAVU_BOLD = _font("DejaVuSans-Bold.ttf")
DEJAVU_OBLIQUE = _font("DejaVuSans-Oblique.ttf")
DEJAVU_SERIF = _font("DejaVuSerif.ttf")
DEJAVU_MONO = _font("DejaVuSansMono.ttf")

needs_dejavu = pytest.mark.skipif(DEJAVU is None,
                                  reason="DejaVuSans.ttf is not installed")


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_cache()
    yield
    clear_cache()


def _face(path: str, family: str, style: str = "Regular",
          weight: int = 400, italic: bool = False) -> FontFace:
    return FontFace(path=path, family=family, style=style, weight=weight,
                    italic=italic, outline="truetype", source="test")


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_css_weight_mapping():
    assert _css_weight(80) == 400
    assert _css_weight(200) == 700
    assert _css_weight(210) == 900
    assert _css_weight(0) == 100
    assert _css_weight(9999) == 900


def test_outline_from_format():
    assert _outline_from_format("TrueType") == "truetype"
    assert _outline_from_format("True Type Collection") == "truetype"
    assert _outline_from_format("CFF") == "cff"
    assert _outline_from_format("Type 1") == "unknown"
    assert _outline_from_format("") == "unknown"


def test_permissions_fs_type_values():
    # Installable, preview/print and editable all permit embedding.
    for value in (0x0000, 0x0004, 0x0008):
        embeddable, subset, _note = _permissions(value)
        assert embeddable is True
        assert subset is True
    # Restricted: never embeddable.
    assert _permissions(0x0002)[0] is False
    # Bit 0x0100 forbids subsetting but still allows embedding.
    embeddable, subset, _ = _permissions(0x0100)
    assert embeddable is True and subset is False
    # Bit 0x0200 restricts embedding to bitmaps: unusable for text.
    assert _permissions(0x0200)[0] is False
    assert _permissions(0x0202)[0] is False
    # Unknown (no OS/2) is permissive; the writer re-checks.
    assert _permissions(None)[0] is True


def test_normalize_family_keeps_real_name_words():
    # A style word is only stripped when the face's style agrees.
    assert normalize_family("Arial Black", "Regular") == "arial black"
    assert normalize_family("Helvetica-Bold", "Bold") == "helvetica"
    assert normalize_family("Times New Roman", "Regular") == "times new roman"
    assert normalize_family("DejaVu Sans", "Book") == "dejavu sans"
    assert normalize_family("Liberation Sans", "Bold Italic") == "liberation sans"


def test_font_face_display_name_and_bold():
    face = FontFace(path="/x.ttf", family="DejaVu Sans", style="Bold",
                    weight=700)
    assert face.display_name == "DejaVu Sans Bold"
    assert face.bold is True
    assert FontFace(path="/x.ttf", family="Solo").display_name == "Solo"


# --------------------------------------------------------------------------- #
# fontconfig parsing
# --------------------------------------------------------------------------- #
def test_faces_from_fontconfig_parses_and_skips_missing(monkeypatch):
    good = DEJAVU or "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = DEJAVU_BOLD or good
    if not os.path.isfile(good):
        pytest.skip("no installed TrueType font to point at")
    text = (
        f"{good}\t0\tDejaVu Sans\tBook\t80\t0\t100\tTrueType\n"
        f"{good}\t0\tDejaVu Sans\tBook\t80\t0\t100\tTrueType\n"  # duplicate
        "/nonexistent/Nope.ttf\t0\tNope\tRegular\t80\t0\t0\tTrueType\n"
        f"{bold}\t0\tDejaVu Sans\tBold\t200\t100\t0\tCFF\n"
        "malformed line\n"
    )
    monkeypatch.setattr(fd, "_run_fc_list", lambda: text)
    faces = _faces_from_fontconfig()
    assert len(faces) == 2  # duplicate and missing file dropped
    regular, bold_face = faces
    assert regular.family == "DejaVu Sans"
    assert regular.style == "Book"
    assert regular.weight == 400
    assert regular.monospaced is True
    assert regular.italic is False
    assert regular.source == "fontconfig"
    assert bold_face.weight == 700
    assert bold_face.italic is True
    assert bold_face.monospaced is False
    assert bold_face.outline == "cff"


def test_faces_from_fontconfig_handles_no_fontconfig(monkeypatch):
    monkeypatch.setattr(fd, "_run_fc_list", lambda: None)
    assert _faces_from_fontconfig() == []


def test_system_faces_falls_back_to_directory_scan(monkeypatch):
    sentinel = [FontFace(path="/x.ttf", family="Fallback", outline="truetype")]
    monkeypatch.setattr(fd, "_faces_from_fontconfig", lambda: [])
    monkeypatch.setattr(fd, "_faces_from_directories", lambda: sentinel)
    faces = system_faces(refresh=True)
    assert [f.family for f in faces] == ["Fallback"]


def test_system_faces_is_cached_and_refreshable(monkeypatch):
    calls = {"n": 0}

    def fake_fc():
        calls["n"] += 1
        return [FontFace(path="/x.ttf", family=f"F{calls['n']}")]

    monkeypatch.setattr(fd, "_faces_from_fontconfig", fake_fc)
    first = system_faces(refresh=True)
    second = system_faces()
    assert calls["n"] == 1
    assert first[0].family == "F1"
    assert second[0].family == "F1"
    refreshed = system_faces(refresh=True)
    assert calls["n"] == 2
    assert refreshed[0].family == "F2"


@needs_dejavu
def test_system_faces_returns_real_faces():
    faces = system_faces(refresh=True)
    assert faces, "fontconfig/directory scan found no fonts"
    assert any(f.family for f in faces)


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_face_metadata_reads_licence_and_coverage():
    meta = face_metadata(DEJAVU)
    assert meta["outline"] == "truetype"
    assert meta["embeddable"] is True
    assert meta["subset_allowed"] is True
    assert meta["fs_type"] == 0
    assert meta["weight"] == 400
    cps = meta["codepoints"]
    assert isinstance(cps, frozenset)
    assert ord("ñ") in cps and ord("á") in cps


@needs_dejavu
def test_face_metadata_is_cached(monkeypatch):
    face_metadata(DEJAVU)
    calls = {"n": 0}
    real = fd._TTFont

    class Counting:
        def __init__(self, *a, **kw):
            calls["n"] += 1
            self._inner = real(*a, **kw)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(fd, "_TTFont", Counting)
    face_metadata(DEJAVU)  # served from cache, no new TTFont
    assert calls["n"] == 0


@needs_dejavu
def test_face_from_file_and_codepoints():
    face = face_from_file(DEJAVU)
    assert face.source == "user"
    assert face.family == "DejaVu Sans"
    assert face.embeddable is True
    cps = face_codepoints(face)
    assert cps is not None and ord("ú") in cps


@needs_dejavu
def test_supports_and_missing_characters():
    face = face_from_file(DEJAVU)
    assert supports(face, "áéíóú ü ñ ÁÉÍÓÚ Ü Ñ")
    assert missing_characters(face, "añb") == ()
    # A CJK character is outside DejaVu's coverage.
    assert missing_characters(face, "ab漢") == ("漢",)
    assert supports(face, "漢") is False


@needs_dejavu
def test_face_advances_are_em_units():
    face = face_from_file(DEJAVU)
    advances = face_advances(face, "Hola")
    assert advances is not None and len(advances) == 4
    assert all(0.2 < a < 1.5 for a in advances), advances
    # Lower-case o is wider than the narrow i in a proportional face.
    i_adv = face_advances(face, "i")[0]
    o_adv = face_advances(face, "o")[0]
    assert o_adv > i_adv


@needs_dejavu
@pytest.mark.skipif(DEJAVU_MONO is None, reason="DejaVuSansMono missing")
def test_monospaced_advances_are_equal():
    face = face_from_file(DEJAVU_MONO)
    advances = face_advances(face, "milw")
    assert advances is not None
    assert max(advances) - min(advances) < 1e-6


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #
@needs_dejavu
def test_rank_requires_full_coverage():
    faces = [
        _face(DEJAVU, "DejaVu Sans"),
        _face(DEJAVU_SERIF or DEJAVU, "DejaVu Serif"),
    ]
    query = FontQuery(basefont="Helvetica", text="añ", serif=False)
    matches = rank_faces(query, faces)
    assert matches
    assert all(not m.missing for m in matches)
    assert all(m.face.embeddable for m in matches)
    # Both cover the text, so both survive.
    assert {m.face.family for m in matches} == {"DejaVu Sans", "DejaVu Serif"}


@needs_dejavu
def test_rank_prefers_same_family_group():
    faces = [
        _face(DEJAVU, "DejaVu Sans"),
        _face(DEJAVU_SERIF or DEJAVU, "DejaVu Serif"),
    ]
    query = FontQuery(basefont="Helvetica", text="Hola")
    matches = rank_faces(query, faces)
    assert matches[0].face.family == "DejaVu Sans"


@needs_dejavu
def test_rank_prefers_matching_weight_and_slant():
    if not (DEJAVU_BOLD and DEJAVU_OBLIQUE):
        pytest.skip("DejaVu bold/oblique faces missing")
    faces = [
        _face(DEJAVU, "DejaVu Sans"),
        _face(DEJAVU_BOLD, "DejaVu Sans", "Bold", 700),
        _face(DEJAVU_OBLIQUE, "DejaVu Sans", "Oblique", 400, True),
    ]
    bold = rank_faces(FontQuery(basefont="DejaVu Sans", text="Hola",
                                bold=True), faces)
    assert bold[0].face.bold is True
    italic = rank_faces(FontQuery(basefont="DejaVu Sans", text="Hola",
                                  italic=True), faces)
    assert italic[0].face.italic is True


@needs_dejavu
def test_rank_reports_width_ratio():
    faces = [_face(DEJAVU, "DejaVu Sans")]
    query = FontQuery(basefont="Helvetica", text="Hola",
                      advances=(0.5, 0.5, 0.5, 0.5))
    match = rank_faces(query, faces)[0]
    assert match.width_ratio > 0
    expected = sum(face_advances(face_from_file(DEJAVU), "Hola")) / 2.0
    assert abs(match.width_ratio - expected) < 1e-6


@needs_dejavu
def test_rank_dedupes_duplicate_installations():
    face = _face(DEJAVU, "DejaVu Sans")
    matches = rank_faces(FontQuery(basefont="DejaVu Sans", text="Hola"),
                         [face, face, face])
    assert len(matches) == 1


@needs_dejavu
def test_rank_respects_limit():
    faces = [_face(DEJAVU, f"Family {i}") for i in range(6)]
    matches = rank_faces(FontQuery(basefont="Helvetica", text="Hola"),
                         faces, limit=2)
    assert len(matches) <= 2


@needs_dejavu
def test_rank_skips_unembeddable_and_non_truetype():
    blocked = FontFace(path=DEJAVU, family="Blocked", style="Regular",
                       outline="truetype", embeddable=False)
    cff = FontFace(path=DEJAVU, family="Cff", style="Regular",
                   outline="cff")
    query = FontQuery(basefont="Blocked", text="Hola")
    assert rank_faces(query, [blocked, cff]) == []


@needs_dejavu
def test_rank_skips_face_missing_characters():
    faces = [_face(DEJAVU, "DejaVu Sans")]
    matches = rank_faces(FontQuery(basefont="DejaVu Sans", text="漢"), faces)
    assert matches == []


@needs_dejavu
def test_font_match_usable_requires_coverage_and_licence():
    from pdfar.font_discovery import FontMatch

    face = face_from_file(DEJAVU)
    assert FontMatch(face=face, score=1.0).usable is True
    assert FontMatch(face=face, score=1.0, missing=("ñ",)).usable is False
    blocked = FontFace(path=DEJAVU, family="X", outline="truetype",
                       embeddable=False)
    assert FontMatch(face=blocked, score=1.0).usable is False


def test_font_query_original_width():
    query = FontQuery(text="abc", advances=(0.5, 0.25, 0.75))
    assert query.original_width == 1.5


@pytest.mark.skipif(not FONTTOOLS_AVAILABLE,
                    reason="fontTools is required for coverage checks")
@needs_dejavu
def test_find_alternatives_on_the_real_system():
    query = FontQuery(basefont="Helvetica", text="Café ñandú")
    matches = find_alternatives(query, limit=5)
    assert matches, "no installed font can draw Spanish text"
    assert all(m.usable for m in matches)
    assert all(m.face.outline == "truetype" for m in matches)
    # Scores are ordered best first.
    scores = [m.score for m in matches]
    assert scores == sorted(scores, reverse=True)
