"""Tile geometry for large pages (Okular-style viewport tiling).

Why tiles
---------
Adapted from Okular's ``document.cpp`` (~line 1404): when a rendered page
would be much larger than the screen, rendering the whole page wastes memory
and time — especially for high-resolution scanned PDFs.  Okular switches to a
``TilesManager`` that renders *only the visible portions* of the page at the
requested size.

Credits / Creditos / 致谢
------------------------
This tiled-rendering technique is directly inspired by **Okular** — the KDE
PDF/document viewer (https://apps.kde.org/okular/). The tiling strategy was
designed by the KDE Okular developers ("Okular" by KDE, GPL-2.0+ / GPL-3.0+,
https://invent.kde.org/graphics/okular). PDF-Advanced-Reader (PDFAR,
GPL-3.0+, https://github.com/wachin/PDF-Advanced-Reader) implements the same
*ideas* (never render a huge whole-page bitmap on large pages; render only
the visible tiles) on top of PyMuPDF. Ideas and algorithms are not
copyrighted; this original Python implementation is PDFAR's own. Okular —
copyright by its respective authors (c) KDE.

This module is pure geometry (no Qt, no PyMuPDF), so it is trivially
testable.  It answers:

* should a page of a given display size be tiled?
* into how many rows/columns is it split?
* which tiles cover a visible region (in page display coordinates)?
* what offset does a tile occupy within the page, and what clip does it
  map to in page points at a given zoom?

Tiles are square, at most ``TILE_SIZE`` px per side.  The implementation in
``viewer.py`` renders each tile as its own ``RenderPool`` job with a ``clip``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

#: Okular switches to tiles when the page bitmap would exceed this many
#: times the viewport area (see Okular document.cpp "4*screenSize").
THRESHOLD_TIMES_SCREEN = 4.0

#: Maximum side of a single tile in display pixels.
TILE_SIZE = 1024


@dataclass(frozen=True)
class Tile:
    """Coordinates of one tile within a page grid (0-based)."""

    row: int
    col: int


class TileGrid:
    """Layout of a single page into tiles, defined in display pixels."""

    def __init__(
        self,
        disp_w: float,
        disp_h: float,
        view_w: int,
        view_h: int,
        *,
        threshold: float = THRESHOLD_TIMES_SCREEN,
        tile_size: int = TILE_SIZE,
    ) -> None:
        self.disp_w = float(disp_w)
        self.disp_h = float(disp_h)
        self.view_w = max(1, int(view_w))
        self.view_h = max(1, int(view_h))
        self.threshold = float(threshold)
        self.tile_size = max(64, int(tile_size))

    # ------------------------------------------------------------- queries
    @property
    def active(self) -> bool:
        """Whether this page should be rendered as tiles.

        Mirrors Okular: the full-page bitmap would exceed ``threshold`` times
        the viewport area.  A degenerate page (no area) is never tiled.
        """
        page_area = self.disp_w * self.disp_h
        view_area = self.view_w * self.view_h
        return page_area > self.threshold * view_area

    @property
    def cols(self) -> int:
        return max(1, int((self.disp_w + self.tile_size - 1) // self.tile_size))

    @property
    def rows(self) -> int:
        return max(1, int((self.disp_h + self.tile_size - 1) // self.tile_size))

    def tile_count(self) -> int:
        return self.rows * self.cols

    def tile_rect(self, tile: Tile) -> Tuple[float, float, float, float]:
        """(dx, dy, tw, th) offset and size of *tile* within the page display."""
        dx = tile.col * self.tile_size
        dy = tile.row * self.tile_size
        tw = min(self.tile_size, self.disp_w - dx)
        th = min(self.tile_size, self.disp_h - dy)
        return (dx, dy, tw, th)

    def tiles_for_region(
        self, x0: float, y0: float, x1: float, y1: float
    ) -> List[Tile]:
        """Tiles overlapping the page-display region ``(x0, y0, x1, y1)``."""
        if self.disp_w <= 0 or self.disp_h <= 0:
            return []
        # clamp into the page
        x0 = max(0.0, min(x0, self.disp_w))
        y0 = max(0.0, min(y0, self.disp_h))
        x1 = max(0.0, min(x1, self.disp_w))
        y1 = max(0.0, min(y1, self.disp_h))
        if x1 <= x0 or y1 <= y0:
            return []
        c0 = int(x0 // self.tile_size)
        c1 = min(self.cols - 1, int((x1 - 1e-6) // self.tile_size))
        r0 = int(y0 // self.tile_size)
        r1 = min(self.rows - 1, int((y1 - 1e-6) // self.tile_size))
        return [Tile(row, col) for row in range(r0, r1 + 1) for col in range(c0, c1 + 1)]

    # ------------------------------------------------------------- clipping
    def clip_points(self, tile: Tile, zoom: float) -> Tuple[float, float, float, float]:
        """Map a tile to page-point coordinates for a render ``clip``.

        Valid only when the render matrix has no rotation (the viewer only
        tiles pages whose view rotation is 0).  Returns ``(x0, y0, x1, y1)``.
        """
        z = max(1e-6, float(zoom))
        dx, dy, tw, th = self.tile_rect(tile)
        return (dx / z, dy / z, (dx + tw) / z, (dy + th) / z)


def visible_tiles(
    page_top_left: Sequence[float],
    page_size: Sequence[float],
    view_x0: float,
    view_y0: float,
    view_x1: float,
    view_y1: float,
    margin: float = 0.0,
) -> List[Tile]:
    """Build a grid for a page and return the tiles covering its visible part.

    Convenience wrapper combining ``TileGrid`` construction with the visible
    region (viewport ∩ page, optionally expanded by *margin*).

    Parameters
    ----------
    page_top_left, page_size:
        Page position and size in *view coordinates* (already offset by zoom
        and scrollbar).
    view_x0..view_y1:
        Visible viewport rectangle in view coordinates.
    margin:
        Extra pixels to expand the intersection (Okular preloads 512 px).
    """
    px, py = page_top_left
    pw, ph = page_size
    grid = TileGrid(pw, ph, max(1, int(view_x1 - view_x0)),
                    max(1, int(view_y1 - view_y0)))
    if not grid.active:
        return []
    # region of the page that is visible, in page-display coordinates
    rx0 = max(0.0, view_x0 - px - margin)
    ry0 = max(0.0, view_y0 - py - margin)
    rx1 = min(pw, view_x1 - px + margin)
    ry1 = min(ph, view_y1 - py + margin)
    return grid.tiles_for_region(rx0, ry0, rx1, ry1)
