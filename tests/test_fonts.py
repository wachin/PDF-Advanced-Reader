"""Font catalog, style resolution, embedding, and the font chooser dialog."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import pymupdf as fitz
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase

from conftest import build_sample_pdf
from pdfar import fonts
from pdfar.fonts import (
    BASE14_FONTS, BASE14_KEYS, BASE14_STYLES, RECOMMENDED_FONTS,
    RecommendedFont, all_families, base14_alias, default_style,
    display_family, family_installed, font_file, install_hint,
    missing_recommended, qt_family, qt_preview_font, style_sort_key,
    styles_for,
)
from pdfar.page_numbers import (
    FontDialog, PageNumbersPanel, PageNumberSettings, number_font_source,
    pdf_number_font, preview_font, stamp_copy,
)


def _system_family() -> str:
    for name in ("DejaVu Sans", "Liberation Sans", "Noto Sans"):
        if family_installed(name):
            return name
    pytest.skip("no common system font installed")


@pytest.fixture(autouse=True)
def _qt(qapp):
    """QFontDatabase requires a QGuiApplication in every font test."""
    return qapp


def test_base14_catalog_has_three_builtins():
    assert [key for key, _label, _family in BASE14_FONTS] == [
        "helv", "tiro", "cour"]
    assert BASE14_KEYS == {"helv", "tiro", "cour"}
    assert BASE14_STYLES == ("Regular", "Bold", "Italic", "Bold Italic")


@pytest.mark.parametrize("family,style,expected", [
    ("helv", "Regular", "helv"),
    ("helv", "Bold", "hebo"),
    ("helv", "Italic", "heit"),
    ("helv", "Bold Italic", "hebi"),
    ("tiro", "Regular", "tiro"),
    ("tiro", "Bold", "tibo"),
    ("tiro", "Italic", "tiit"),
    ("tiro", "Bold Italic", "tibi"),
    ("cour", "Regular", "cour"),
    ("cour", "Bold", "cobo"),
    ("cour", "Italic", "coit"),
    ("cour", "Bold Italic", "cobi"),
])
def test_base14_aliases_cover_every_face(family, style, expected):
    assert base14_alias(family, style) == expected


def test_base14_alias_is_none_for_system_fonts():
    assert base14_alias("DejaVu Sans", "Bold") is None


def test_recommended_catalog_entries_are_well_formed():
    families = [font.family for font in RECOMMENDED_FONTS]
    assert len(families) == len(set(families))
    for font in RECOMMENDED_FONTS:
        assert font.family.strip()
        assert font.tier in {"main", "contrib", "upstream"}
        if font.tier == "upstream":
            assert not font.package
        else:
            assert font.package.startswith("fonts-")


def test_install_hint_dedupes_and_sorts_packages():
    chosen = [
        RecommendedFont("Zeta", "fonts-zeta"),
        RecommendedFont("Alpha", "fonts-alpha"),
        RecommendedFont("Alpha Two", "fonts-alpha"),
        RecommendedFont("IBM", "fonts-ibm-plex", "contrib"),
        RecommendedFont("Red Hat Text", "", "upstream"),
    ]
    assert install_hint(chosen) == \
        "sudo apt install fonts-alpha fonts-ibm-plex fonts-zeta"
    assert install_hint([RecommendedFont("Red Hat Text", "", "upstream")]) == ""


def test_platform_builtin_catalogs_are_well_formed():
    for catalog in (fonts.WINDOWS_FONTS, fonts.MACOS_FONTS):
        families = [font.family for font in catalog]
        assert families
        assert len(families) == len(set(families))
        for font in catalog:
            assert font.family.strip()
            assert font.tier == "builtin"
            assert not font.package


def test_recommended_catalog_follows_the_platform(monkeypatch):
    monkeypatch.setattr(fonts, "platform_key", lambda: "linux")
    assert fonts.recommended_fonts() == fonts.RECOMMENDED_FONTS

    monkeypatch.setattr(fonts, "platform_key", lambda: "windows")
    families = [font.family for font in fonts.recommended_fonts()]
    assert families[:3] == ["Arial", "Calibri", "Cambria"]
    assert "Segoe UI" in families
    assert len(families) == len(set(families))
    assert len(fonts.recommended_installable()) == \
        len(fonts.WINDOWS_FONTS) + len(fonts.RECOMMENDED_FONTS)

    monkeypatch.setattr(fonts, "platform_key", lambda: "macos")
    families = [font.family for font in fonts.recommended_fonts()]
    assert families[:3] == ["Avenir", "Avenir Next", "Courier New"]
    assert "Helvetica Neue" in families
    assert "Menlo" in families
    assert len(families) == len(set(families))


def test_missing_guidance_is_platform_specific(monkeypatch):
    missing = [
        RecommendedFont("Roboto", "fonts-roboto"),
        RecommendedFont("Red Hat Text", "", "upstream"),
    ]

    monkeypatch.setattr(fonts, "platform_key", lambda: "linux")
    assert fonts.install_hint(missing) == "sudo apt install fonts-roboto"
    note = fonts.missing_note(missing)
    assert "sudo apt install fonts-roboto" in note
    assert "Not packaged by Debian/Ubuntu: Red Hat Text" in note

    monkeypatch.setattr(fonts, "platform_key", lambda: "windows")
    assert fonts.install_hint(missing) == ""
    note = fonts.missing_note(missing)
    assert "Personalization" in note and "Roboto" in note
    assert "apt" not in note

    monkeypatch.setattr(fonts, "platform_key", lambda: "macos")
    note = fonts.missing_note(missing)
    assert "Homebrew" in note and "Roboto" in note


def test_builtin_guidance_names_the_platform(monkeypatch):
    monkeypatch.setattr(fonts, "platform_key", lambda: "windows")
    arial = RecommendedFont("Arial", "", "builtin")
    assert "Windows" in fonts.recommended_hint(arial)
    assert fonts.recommended_tooltip(arial) == "Included with Windows."
    assert fonts.missing_note([arial]) == ""

    monkeypatch.setattr(fonts, "platform_key", lambda: "macos")
    menlo = RecommendedFont("Menlo", "", "builtin")
    assert "macOS" in fonts.recommended_hint(menlo)
    assert fonts.recommended_tooltip(menlo) == "Included with macOS."


def test_split_face_names_extract_trailing_styles():
    cases = {
        "Arial": ("Arial", "Regular"),
        "Arial Bold": ("Arial", "Bold"),
        "Times New Roman Bold Italic": ("Times New Roman", "Bold Italic"),
        "HelveticaNeue-Bold": ("HelveticaNeue", "Bold"),
        "Open_Sans_SemiBold": ("Open Sans", "SemiBold"),
        "Arial Black": ("Arial", "Black"),
        "Courier New": ("Courier New", "Regular"),
    }
    for name, expected in cases.items():
        assert fonts._split_face_name(name) == expected, name


def test_windows_registry_entries_resolve_families_and_styles(
        tmp_path, monkeypatch):
    for filename in ("arial.ttf", "arialbd.ttf", "ariblk.ttf", "hnbi.otf"):
        (tmp_path / filename).write_bytes(b"")
    pairs = (
        ("Arial (TrueType)", "arial.ttf"),
        ("Arial Bold (TrueType)", "arialbd.ttf"),
        ("Arial Black (TrueType)", "ariblk.ttf"),
        ("Helvetica Neue Bold Italic (OpenType)",
         str(tmp_path / "hnbi.otf")),
    )
    entries = fonts._windows_entries(pairs, [str(tmp_path)])
    monkeypatch.setattr(fonts, "platform_key", lambda: "windows")
    monkeypatch.setattr(fonts, "_font_index_for", lambda _platform: tuple(entries))

    assert font_file("Arial", "Regular") == str(tmp_path / "arial.ttf")
    assert font_file("Arial", "Bold") == str(tmp_path / "arialbd.ttf")
    assert font_file("Arial Black", "Regular") == str(tmp_path / "ariblk.ttf")
    assert font_file("Helvetica Neue", "Bold Italic") == \
        str(tmp_path / "hnbi.otf")
    assert font_file("Courier New", "Regular") is None


def test_macos_font_folders_resolve_families(tmp_path, monkeypatch):
    for filename in ("HelveticaNeue.ttf", "HelveticaNeue-Bold.ttf",
                     "Open Sans Bold Italic.otf", "Menlo-Regular.ttf"):
        (tmp_path / filename).write_bytes(b"")
    entries = fonts._macos_font_index([str(tmp_path)])
    monkeypatch.setattr(fonts, "platform_key", lambda: "macos")
    monkeypatch.setattr(fonts, "_font_index_for", lambda _platform: tuple(entries))

    assert font_file("Helvetica Neue", "Regular") == \
        str(tmp_path / "HelveticaNeue.ttf")
    assert font_file("Helvetica Neue", "Bold") == \
        str(tmp_path / "HelveticaNeue-Bold.ttf")
    assert font_file("Open Sans", "Bold Italic") == \
        str(tmp_path / "Open Sans Bold Italic.otf")
    assert font_file("Menlo", "Regular") == \
        str(tmp_path / "Menlo-Regular.ttf")


def test_recommended_installable_matches_missing_list():
    pairs = fonts.recommended_installable()
    assert len(pairs) == len(RECOMMENDED_FONTS)
    missing = missing_recommended()
    assert [font for font, installed in pairs if not installed] == list(missing)
    assert all(not family_installed(font.family) for font in missing)


def test_display_family_strips_qt_foundry_suffix():
    assert display_family("Nimbus Sans [urw]") == "Nimbus Sans"
    assert display_family("DejaVu Sans") == "DejaVu Sans"
    assert display_family("helv") == "helv"


def test_qt_family_resolves_to_an_installed_name():
    family = _system_family()
    resolved = qt_family(family)
    assert resolved in QFontDatabase.families()
    assert display_family(resolved) == family


def test_qt_family_matches_names_without_spaces():
    family = _system_family()
    resolved = qt_family(fonts._normalized_family(family))
    assert display_family(resolved) == family


def test_all_families_has_no_foundry_duplicates():
    names = all_families()
    assert names
    labels = [display_family(name).casefold() for name in names]
    assert len(labels) == len(set(labels))
    assert labels == sorted(labels)


def test_style_sort_key_puts_regular_faces_first():
    assert sorted(["Bold", "Regular", "Oblique"], key=style_sort_key) == [
        "Regular", "Bold", "Oblique"]
    assert style_sort_key("Book") < style_sort_key("Bold")
    assert style_sort_key("Normal") < style_sort_key("Light")


def test_base14_styles_keep_their_four_faces():
    for key in BASE14_KEYS:
        assert styles_for(key) == BASE14_STYLES


def test_system_styles_include_a_regular_face():
    family = _system_family()
    styles = styles_for(family)
    assert styles
    assert all(style.strip() for style in styles)
    assert default_style(family) in styles


def test_default_style_prefers_requested_and_falls_back():
    assert default_style("helv", "bold") == "Bold"
    assert default_style("helv", "Nonsense") == "Regular"
    assert default_style("helv") == "Regular"


def test_default_style_treats_italic_and_oblique_as_equivalents():
    family = _system_family()
    styles = styles_for(family)
    if "Oblique" in styles and "Italic" not in styles:
        assert default_style(family, "Italic") == "Oblique"
        if "Bold Oblique" in styles:
            assert default_style(family, "Bold Italic") == "Bold Oblique"
    else:
        assert default_style(family, "Italic") in {"Italic", "Oblique"}


def test_font_file_for_regular_avoids_condensed_faces():
    family = _system_family()
    path = font_file(family, "Regular")
    assert path and os.path.isfile(path)
    assert "condensed" not in os.path.basename(path).casefold()


def test_font_file_for_bold_is_a_bold_face():
    family = _system_family()
    path = font_file(family, "Bold")
    assert path and os.path.isfile(path)
    assert fitz.Font(fontfile=path).is_bold
    assert "condensed" not in os.path.basename(path).casefold()


def test_font_file_returns_none_for_unknown_family():
    assert font_file("NoSuchFontFamily", "Regular") is None


def test_apply_style_maps_weight_words_and_italics():
    family = _system_family()
    cases = [
        ("Regular", QFont.Weight.Normal, False),
        ("Book", QFont.Weight.Normal, False),
        ("Light", QFont.Weight.Light, False),
        ("SemiBold", QFont.Weight.DemiBold, False),
        ("Bold", QFont.Weight.Bold, False),
        ("Black", QFont.Weight.Black, False),
        ("Bold Italic", QFont.Weight.Bold, True),
        ("Oblique", QFont.Weight.Normal, True),
    ]
    for style, weight, italic in cases:
        font = QFont(family)
        fonts.apply_style(font, style, named=False)
        assert font.weight() == weight, style
        assert font.italic() is italic, style

    named = QFont(family)
    fonts.apply_style(named, "Bold Italic")
    assert named.styleName() == "Bold Italic"


def test_qt_preview_font_sets_size_and_style():
    family = _system_family()
    font = qt_preview_font(family, "Bold Italic", 32)
    assert font.family() == family
    assert font.pixelSize() == 32
    assert font.bold()
    assert font.italic()

    builtin = qt_preview_font("helv", "Bold", 20)
    assert builtin.family() == "Sans Serif"
    assert builtin.bold()
    assert builtin.pixelSize() == 20


def test_page_number_font_uses_styled_base14_faces():
    font = pdf_number_font(PageNumberSettings(font_style="Bold"))
    assert font.name == "Helvetica-Bold"
    assert number_font_source(PageNumberSettings(font_style="Italic")) == \
        (None, "heit")
    assert number_font_source(PageNumberSettings(font_family="tiro")) == \
        (None, "tiro")


def test_page_number_font_uses_a_system_font_file():
    family = _system_family()
    settings = PageNumberSettings(font_family=family, font_style="Bold")
    path, name = number_font_source(settings)
    assert path and os.path.isfile(path)
    assert name == "pdfar-number"
    assert pdf_number_font(settings).name.casefold().startswith(
        family.split()[0].casefold())


def test_unknown_font_falls_back_to_helvetica():
    settings = PageNumberSettings(font_family="NoSuchFontFamily")
    assert number_font_source(settings) == (None, "helv")
    assert pdf_number_font(settings).name == "Helvetica"


def test_stamp_embeds_the_chosen_system_font(tmp_path):
    family = _system_family()
    style = "Bold" if "Bold" in styles_for(family) else default_style(family)
    src = build_sample_pdf(tmp_path / "src.pdf", pages=2, width=200,
                           height=300)
    out = tmp_path / "numbered.pdf"
    settings = PageNumberSettings(
        font_family=family, font_style=style, mask=False)

    with fitz.open(src) as doc:
        assert stamp_copy(doc, str(out), settings) == 2

    with fitz.open(out) as doc:
        page = doc[0]
        embedded = [entry for entry in page.get_fonts(full=True)
                    if entry[4] == "pdfar-number"]
        assert embedded
        assert embedded[0][1] == "ttf"
        assert "1/2" in page.get_text()


def test_preview_font_follows_family_and_style(qapp):
    family = _system_family()
    bold = preview_font(
        PageNumberSettings(font_family=family, font_style="Bold"), 24)
    assert bold.family() == family
    assert bold.bold()
    assert bold.pixelSize() == 24


def test_font_dialog_lists_builtins_and_their_styles(qapp):
    dialog = FontDialog("helv", "Regular", 12)
    try:
        assert dialog.tree.topLevelItemCount() == 2
        recommended = dialog.tree.topLevelItem(0)
        assert recommended.text(0) == "Recommended"
        labels = [recommended.child(index).text(0)
                  for index in range(recommended.childCount())]
        assert labels[:3] == [label for _key, label, _family in BASE14_FONTS]

        styles = [dialog.style_list.item(index).text()
                  for index in range(dialog.style_list.count())]
        assert styles == list(BASE14_STYLES)
        assert dialog.selected_family == "helv"
        assert dialog.selected_style == "Regular"

        dialog.style_list.setCurrentRow(1)
        assert dialog.selected_style == "Bold"
        dialog.size_spin.setValue(18)
        assert dialog.selected_size == 18
        assert "Helvetica" in dialog.family_label.text()
    finally:
        dialog.deleteLater()


def test_font_dialog_selection_updates_the_preview(qapp):
    dialog = FontDialog("helv", "Regular", 12)
    try:
        dialog.style_list.setCurrentRow(2)
        assert dialog.selected_style == "Italic"
        assert dialog.sample_label.font().italic()
        assert dialog.family_label.text().endswith("· Italic")
    finally:
        dialog.deleteLater()


def test_font_dialog_search_filters_buitin_entries(qapp):
    dialog = FontDialog("helv", "Regular", 12)
    try:
        dialog.search_edit.setText("Times")
        recommended = dialog.tree.topLevelItem(0)
        labels = {recommended.child(index).text(0): recommended.child(index)
                  for index in range(recommended.childCount())}
        assert not labels["Times (built-in)"].isHidden()
        assert labels["Helvetica (built-in)"].isHidden()
    finally:
        dialog.deleteLater()


def test_font_dialog_marks_missing_recommended_fonts(qapp):
    missing = missing_recommended()
    if not missing:
        pytest.skip("every recommended font is installed")
    dialog = FontDialog("helv", "Regular", 12)
    try:
        recommended = dialog.tree.topLevelItem(0)
        disabled = [
            recommended.child(index)
            for index in range(recommended.childCount())
            if "not installed" in recommended.child(index).text(0)
        ]
        assert len(disabled) == len(missing)
        assert all(not (item.flags() & Qt.ItemFlag.ItemIsEnabled)
                   for item in disabled)
        command = install_hint(missing)
        if command:
            assert not dialog.note.isHidden()
            assert command in dialog.note.text()
        if any(font.tier == "contrib" for font in missing):
            assert "contrib" in dialog.note.text()
    finally:
        dialog.deleteLater()


def test_panel_font_combo_starts_with_builtins(qapp):
    panel = PageNumbersPanel(page_count=5)
    try:
        keys = [panel.font_combo.itemData(index)
                for index in range(panel.font_combo.count())]
        assert keys[:3] == ["helv", "tiro", "cour"]
        assert panel.settings().font_family == "helv"

        panel.font_combo.setCurrentIndex(1)
        assert panel.settings().font_family == "tiro"
        styles = [panel.font_style_combo.itemData(index)
                  for index in range(panel.font_style_combo.count())]
        assert styles == list(BASE14_STYLES)

        panel.font_style_combo.setCurrentIndex(3)
        assert panel.settings().font_style == "Bold Italic"
    finally:
        panel.deleteLater()


def test_panel_applies_a_dialog_choice(qapp):
    family = _system_family()
    styles = styles_for(family)
    style = "Bold" if "Bold" in styles else styles[0]
    panel = PageNumbersPanel(page_count=3)
    try:
        panel._apply_font_choice(family, style, 21)
        settings = panel.settings()
        assert settings.font_family == family
        assert settings.font_style == style
        assert settings.font_size == 21
        assert panel.font_combo.currentData() == family
        assert panel.font_style_combo.currentData() == style
    finally:
        panel.deleteLater()
