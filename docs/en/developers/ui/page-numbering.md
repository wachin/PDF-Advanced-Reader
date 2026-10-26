# Page numbering panel

Page numbering is embedded in the right-hand **Tools** dock next to Search.
Both dock tabs are icon-only; their labels appear on hover.

## Behaviour

- Two sub-tabs: **Position** (default) and **Mask**. Only one is visible at a
  time, which keeps the narrow right dock short; the numbering range, range
  summary and `Save Copy…` stay below both.
- Positions: top/bottom × left/centre/right.
- Offsets can be entered in **pt, mm, cm or in**. The panel keeps the
  canonical offset in PDF points, so changing the display unit never moves
  the number.
- **Start page number** and **End page number** restrict the range.
  `end_page == 0` means “last page” and keeps following the document until
  the user picks an explicit end.
- Formats: `1`, `1/5`, `Page 1`, `Page 1 of 5`, `- 1 -`. Totals count only
  the pages selected for numbering.
- The first printed number is independent of the first page, so a cover can
  be skipped while numbering still starts at 1.
- The mask grows independently on each side (`mask_left`, `mask_right`,
  `mask_top`, `mask_bottom`), so it can cover an old number that is wider or
  taller than the new one.
- The mask colour is any `#rrggbb` value. The panel offers preset swatches, a
  `QColorDialog`, and an on-screen eyedropper (`ScreenColorPicker`) that grabs
  the screen before showing a full-screen overlay, so a click samples the
  actual page background.
- The preview is drawn on every selected page: a mask in the chosen colour, a
  dashed red outline and the red number. Dragging the number updates the
  offsets live.
- Fonts: the built-in Helvetica, Times and Courier faces come first, followed
  by installed families from the recommended catalog, then the current one.
  The **Style** combo lists every style Qt reports for the family. **Choose…**
  opens `FontDialog`, a searchable tree split into **Recommended** (built-ins,
  installed recommended families, and greyed-out missing ones with their apt
  hint) and **All installed fonts**, with a style list, size field and live
  sample. Saved copies embed the selected system font file.
- `Save Copy…` writes a new `name-numbered.pdf`; the open file is never
  overwritten.

## Modules

### `page_numbers.py`

| Piece | Responsibility |
|---|---|
| `PageNumberSettings` | Dataclass persisted in `QSettings` (`pageNumbers/*`). All lengths are PDF points; `mask_*` pads and `mask_color` are stored per side/colour. Legacy `maskPadding` seeds every side. |
| `UNITS`, `unit_to_points`, `points_to_unit` | UI ↔ point conversion. |
| `page_range` | Inclusive 0-based page range, clamped to the document. |
| `layout_number` / `number_box` | Text baseline and glyph/mask geometry in display space. |
| `box_to_settings` | Drag result → new offsets; clamps inside the page and re-anchors a centred number to the nearest left/right edge. |
| `stamp_copy` | Non-destructive export. |
| `mask_rgb` | Hex mask colour → `draw_rect(fill=…)` floats in 0–1. |
| `sample_screen_color` | Maps an overlay point back to the captured screen pixel, rejecting out-of-bounds positions. |
| `ScreenColorPicker` | Full-screen eyedropper; captures first, emits `color_picked(hex)`, closes on click or `Esc`. |
| `PageNumbersPanel` | Embedded controls, `settings_changed` and `save_requested` signals. |
| `FontDialog` | Searchable recommended/installed font tree, style list, size and live sample. |
| `preview_font` | Qt preview font for the chosen family and style. |
| `pdf_number_font`, `number_font_source` | PyMuPDF font or embedded-file source for stamping. |

### `fonts.py`

| Piece | Responsibility |
|---|---|
| `BASE14_FONTS`, `_BASE14_ALIASES` | Built-in families, preview families and PyMuPDF styled names (`hebo`, `tibi`, …). |
| `RECOMMENDED_FONTS` | Ordered catalog of Debian/Ubuntu font packages (`main`, `contrib`) plus Red Hat's unpackaged OFL families. |
| `installed_families`, `family_installed` | Availability from `QFontDatabase` — never `fc-match`, which substitutes missing families. |
| `display_family`, `qt_family`, `all_families` | Strip Qt foundry suffixes (`Nimbus Sans [urw]`) for display and resolve back for Qt. |
| `styles_for`, `default_style`, `apply_style` | Style list, regular-face fallback, and weight/italic mapping (most specific weight word wins). |
| `font_file` | `fc-list` lookup for family/style → TTF/OTF; prefers exact, uncondensed faces. |
| `install_hint`, `missing_recommended` | `apt install` command for missing recommended packages. |
| `qt_preview_font` | Qt font used by the panel preview and dialog sample. |

The panel stores `_offsets_points` separately from the displayed
`QDoubleSpinBox` values. This preserves exact point positions when the user
switches between mm/cm/in, whose fields are rounded for display.

### `viewer.py`

- `set_numbering_preview(settings)` enables the overlay; `None` clears it.
- `_paint_numbering_preview` maps `NumberBox.mask_rect` through `map_rect` and
  draws the configured fill colour, dashed outline and red text after
  annotations, hits and selection.
- `_numbering_hit_at` maps the pointer back to page display space with
  `unmap_point` and hit-tests the mask.
- While dragging, the widget delta is divided by the zoom and converted with
  `unmap_delta`; each move is computed from the press-time box, so re-anchoring
  cannot make the glyphs jump.
- `numbering_changed` carries the updated settings to the panel; `Escape`
  restores the press-time settings.
- The cursor becomes `SizeAllCursor` over the number.

### `main.py`

- `_apply_numbering_preview` shows or clears the preview depending on whether
  the Page Numbers tab is selected and a document is open.
- Selecting the tab temporarily leaves reflow; leaving the tab restores it.
- Presentation mode clears the preview and restores it on exit.
- `_save_numbered_copy` runs the save dialog, refuses to overwrite the open
  document, stamps the copy with a wait cursor and offers to open it.
- `_apply_right_tab_icons` re-tints both dock icons for light/dark themes.

## Stamping coordinates

`layout_number` and `number_box` work in the page's display space
(`page.rect`, with the PDF's own `/Rotate` applied). For stamping, the
baseline point is converted with `page.derotation_matrix` and passed to
`insert_text(rotate=page.rotation)`. The mask rectangle is converted the same
way before `draw_rect`.

The source document is serialized first:

```python
data = source.tobytes(
    garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)
```

so unsaved annotations are included and the copy is decrypted. The result is
written to a temporary file in the destination directory and moved into place
with `os.replace`.

## Tests

`tests/test_page_numbers.py` covers:

- numbered-page totals and clamped ranges;
- all formats and layout anchors;
- unit conversions and exact point persistence across unit switches;
- start/end semantics, including “end = last page”;
- per-side mask growth, legacy `maskPadding` migration, mask colour export and
  the live preview colour;
- palette/custom colour selection, screen-pixel mapping and the eyedropper;
- the Position/Mask sub-tabs and their page contents;
- mask export and mask-less export;
- drag math, clamping and centre re-anchoring;
- stamping with `/Rotate` 0/90/180/270, CropBox offsets and encrypted PDFs;
- the icon-only right dock, tooltips, preview lifecycle and reflow handoff;
- the save-a-copy flow and overwrite guard.

`tests/test_geometry.py` covers `unmap_delta` alongside the existing rotation
helpers.

`tests/test_fonts.py` covers:

- the recommended catalog, tier rules and deduplicated apt hints;
- base-14 aliases for all four faces of each built-in family;
- foundry stripping, family resolution and duplicate-free installed lists;
- style ordering, default style selection and weight/italic mapping;
- regular/bold file resolution with condensed faces avoided;
- embedded system-font stamping (`pdfar-number` as an embedded TTF);
- preview styling, the font dialog (tree, search, disabled missing entries,
  style/size/sample) and the panel font/style/Choose controls.
