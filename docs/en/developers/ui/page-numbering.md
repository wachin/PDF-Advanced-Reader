# Page numbering panel

Page numbering is embedded in the right-hand **Tools** dock next to Search.
Both dock tabs are icon-only; their labels appear on hover.

## Behaviour

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
- The preview is drawn on every selected page: a white mask, a dashed red
  outline and the red number. Dragging the number updates the offsets live.
- `Save Copy…` writes a new `name-numbered.pdf`; the open file is never
  overwritten.

## Modules

### `page_numbers.py`

| Piece | Responsibility |
|---|---|
| `PageNumberSettings` | Dataclass persisted in `QSettings` (`pageNumbers/*`). All lengths are PDF points. |
| `UNITS`, `unit_to_points`, `points_to_unit` | UI ↔ point conversion. |
| `page_range` | Inclusive 0-based page range, clamped to the document. |
| `layout_number` / `number_box` | Text baseline and glyph/mask geometry in display space. |
| `box_to_settings` | Drag result → new offsets; clamps inside the page and re-anchors a centred number to the nearest left/right edge. |
| `stamp_copy` | Non-destructive export. |
| `PageNumbersPanel` | Embedded controls, `settings_changed` and `save_requested` signals. |
| `preview_font` | Maps base-14 families to Qt families for the preview. |

The panel stores `_offsets_points` separately from the displayed
`QDoubleSpinBox` values. This preserves exact point positions when the user
switches between mm/cm/in, whose fields are rounded for display.

### `viewer.py`

- `set_numbering_preview(settings)` enables the overlay; `None` clears it.
- `_paint_numbering_preview` maps the mask through `map_rect` and draws the
  white fill, dashed outline and red text after annotations, hits and
  selection.
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
- mask export and mask-less export;
- drag math, clamping and centre re-anchoring;
- stamping with `/Rotate` 0/90/180/270, CropBox offsets and encrypted PDFs;
- the icon-only right dock, tooltips, preview lifecycle and reflow handoff;
- the save-a-copy flow and overwrite guard.

`tests/test_geometry.py` covers `unmap_delta` alongside the existing rotation
helpers.
