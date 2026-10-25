# Crisp Vector Icons

> **Status**: Implemented
> **Code**: `src/pdfar/icons.py`
> **Tests**: `tests/test_icons.py`, `tests/test_sidebar_tabs.py`, `tests/test_main.py`

---

## 1. Problem

All monochrome icons — toolbar actions and sidebar tabs — are SVG files, yet
they looked slightly blurry next to perfectly sharp text. Both the toolbar and
the tab bar show icons at **18 × 18 logical pixels**, while the SVGs were
pre-rendered into a fixed set of pixmaps:

| Icons | Pre-rendered sizes |
|---|---|
| Sidebar tabs | 16, 20, 24, 32 |
| Toolbar actions | 16, 24, 32, 48 |

None of those sets contains 18. `QIcon` therefore picked the nearest available
pixmap and **rescaled it** to 18 px, softening every stroke. The artwork uses a
24-unit `viewBox`, so an 18 px request also turns a 2-unit stroke into a
1.5 px stroke — antialiased by definition, but crisp when drawn natively at the
target size.

The alpha mask of a rescaled icon differed from a native 18 px render by
~1149 luma units across the 18 × 18 raster; the icon outlines themselves were
visibly fuzzier.

## 2. Fix: one on-demand `QIconEngine`

`src/pdfar/icons.py` adds `_TintedSvgEngine(QIconEngine)` and a cached
`tinted_icon(name, color)` factory shared by every monochrome icon in the
program:

- **Exact-size rendering** — `pixmap()` rasterizes the SVG at the requested
  `QSize`, with no intermediate fixed-size list.
- **HiDPI aware** — `scaledPixmap(size, mode, state, scale)` renders at
  `size × scale`, sets the pixmap's device pixel ratio, and lets Qt draw it at
  the logical size. A 2× screen gets a 36 × 36 render for an 18 px icon.
- **Theme tinting** — the SVG is drawn, then filled with the theme ink using
  `CompositionMode_SourceIn`, so light mode gets `#3c3c3c` and dark mode
  `#e6e6e6`.
- **Caching** — `@lru_cache` on `(asset name, color)` returns the same
  `QIcon` for every action and tab that uses it; only two colors are ever
  requested.
- **Direct painting** — `paint()` renders at the painter's device pixel ratio
  for widgets that draw the icon themselves.

Both former helpers (`MainWindow._tinted_icon` and `Sidebar._tab_icon`) were
removed; `MainWindow` and `Sidebar` now call `tinted_icon()`.

```python
from .icons import tinted_icon

action.setIcon(tinted_icon("zoom_in.svg", "#3c3c3c"))
```

`asset_path()` in the same module centralizes asset resolution (source
checkout first, then `assets/icons/` relative to the working directory).

## 3. Verification

- The engine's 18 px alpha mask is **byte-identical** to a native 18 px
  `QSvgRenderer` render; the old raster path did not match.
- Side-by-side crops at 10× magnification confirmed the engine matches a
  native render while the old rasterized icon is thicker and softer.
- Real-display screenshots of the toolbar and tab bar in light and dark mode
  confirmed sharp strokes after the change.
- Tests assert that icons render at exactly the requested size
  (`18 × 18`), that a 2× request produces a `36 × 36` pixmap with
  `devicePixelRatio == 2`, that tinting produces the expected ink color, and
  that toolbar and sidebar icons use this path.

## 4. Notes

- `availableSizes()` is intentionally empty: the engine has no finite set of
  sizes, which is exactly the point. Qt asks for the size it needs.
- Missing or invalid SVG assets still yield a null `QIcon`, preserving the
  previous graceful fallback.
- The colored application/window icon (`icon.svg`) is loaded directly by Qt's
  own SVG icon engine and already renders as a vector; only the tinted
  monochrome icons needed this engine.
