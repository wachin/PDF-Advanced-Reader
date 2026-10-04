"""Tests for the tile-geometry module (src/pdfar/tiles.py)."""

import pytest

from pdfar.tiles import Tile, TileGrid, visible_tiles


class TestTileGridActivation:
    """Mirrors Okular's 'page > 4x screen' tiling switch."""

    def test_small_page_not_tiled(self):
        # page ~ 1/2 the screen area -> not tiled
        grid = TileGrid(400, 800, view_w=800, view_h=800)
        assert grid.active is False

    def test_large_page_tiled(self):
        # page ~ 4x the screen area and slightly over -> tiled
        grid = TileGrid(2000, 2000, view_w=800, view_h=800)
        # page_area = 4_000_000, view_area = 640_000, ratio 6.25 > 4
        assert grid.active is True

    def test_exactly_threshold_not_tiled(self):
        # area == 4 * screen -> <= threshold, so not active
        grid = TileGrid(1600, 1600, view_w=800, view_h=800, threshold=4.0)
        # page_area = 2_560_000, view_area = 640_000, ratio exactly 4
        assert grid.active is False

    def test_zero_size_page_never_tiled(self):
        grid = TileGrid(0, 0, view_w=800, view_h=800)
        assert grid.active is False


class TestGridDims:
    def test_cols_rows(self):
        grid = TileGrid(2000, 1000, view_w=800, view_h=800, tile_size=1024)
        assert grid.cols == 2   # ceil(2000/1024)
        assert grid.rows == 1   # ceil(1000/1024)

    def test_exact_multiple(self):
        grid = TileGrid(1024, 1024, view_w=800, view_h=800, tile_size=1024)
        assert grid.cols == 1
        assert grid.rows == 1

    def test_tile_count(self):
        grid = TileGrid(2000, 2000, view_w=800, view_h=800, tile_size=1024)
        assert grid.tile_count() == 4


class TestTileRect:
    def test_first_tile(self):
        grid = TileGrid(1600, 1600, view_w=800, view_h=800, tile_size=1024)
        assert grid.tile_rect(Tile(0, 0)) == pytest.approx((0, 0, 1024, 1024))

    def test_last_tile_clipped(self):
        # tile at row=1 (starts at y=1024), height clipped to 1600-1024=576
        grid = TileGrid(1600, 1600, view_w=800, view_h=800, tile_size=1024)
        assert grid.tile_rect(Tile(1, 1)) == pytest.approx((1024, 1024, 576, 576))


class TestTilesForRegion:
    def test_region_crosses_tiles(self):
        # region 0..1500 crosses into the second tile (1024..2048) in both axes
        grid = TileGrid(2000, 2000, view_w=800, view_h=800, tile_size=1024)
        tiles = grid.tiles_for_region(0, 0, 1500, 1500)
        assert sorted((t.row, t.col) for t in tiles) == [(0, 0), (0, 1), (1, 0), (1, 1)]

    def test_region_within_one_tile(self):
        grid = TileGrid(2000, 2000, view_w=800, view_h=800, tile_size=1024)
        tiles = grid.tiles_for_region(0, 0, 1000, 1000)
        assert tiles == [Tile(0, 0)]

    def test_clamped_outside(self):
        grid = TileGrid(2000, 2000, view_w=800, view_h=800, tile_size=1024)
        assert grid.tiles_for_region(-50, -50, 500, 500) == [Tile(0, 0)]


class TestClipPoints:
    def test_clip_at_unit_zoom(self):
        grid = TileGrid(1600, 1600, view_w=800, view_h=800, tile_size=1024)
        c = grid.clip_points(Tile(0, 0), zoom=1.0)
        assert c == pytest.approx((0, 0, 1024, 1024))

    def test_clip_scales_with_zoom(self):
        grid = TileGrid(1600, 1600, view_w=800, view_h=800, tile_size=1024)
        c = grid.clip_points(Tile(0, 0), zoom=2.0)
        assert c == pytest.approx((0, 0, 512, 512))


class TestVisibleTilesHelpers:
    def test_returns_empty_for_small_page(self):
        # page 400x300 in view coords, screen 800x800 -> not tiled
        tiles = visible_tiles(
            (100, 50), (400, 300),
            view_x0=0, view_y0=0, view_x1=800, view_y1=800,
        )
        assert tiles == []

    def test_returns_visible_tiles_for_large_page(self):
        # page 2000x2000, tile_size 1024 -> 2x2 grid; viewport shows top-left
        tiles = visible_tiles(
            (0, 0), (2000, 2000),
            view_x0=0, view_y0=0, view_x1=800, view_y1=800,
        )
        # first row covers y 0..1024, first col x 0..1024 -> tile (0,0)
        assert tiles == [Tile(0, 0)]
