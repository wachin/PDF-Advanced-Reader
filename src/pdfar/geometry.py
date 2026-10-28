"""Pure geometry helpers: page rotation and coordinate mapping.

The numbers these helpers move are the ones the file states: an annotation's
``/Rect``, a highlight's quad points, a word's box.  All of them are counted on the
sheet, which is ``(w0, h0)`` here — ``page.cropbox``, not ``page.rect``, because
MuPDF has already turned the page by its own ``/Rotate`` before it reports a rect.
``rotation`` is therefore every turn between the sheet and the screen: the page's
own plus the viewer's, whose sum is the same place as the two turns in sequence.
All mapping helpers here convert between:

  * sheet space : points, origin top-left, as PyMuPDF reports them
  * view space  : that sheet turned, zoom-independent, origin top-left

Zoom is a plain scalar multiplication applied by the caller on top of this.

The functions are intentionally pure (no Qt / fitz objects) so they can be
unit-tested cheaply.
"""

from __future__ import annotations

from typing import Tuple

Rect = Tuple[float, float, float, float]  # x0, y0, x1, y1

VALID_ROTATIONS = (0, 90, 180, 270)


def normalize_rotation(rotation: int) -> int:
    """Clamp/round a rotation to one of 0/90/180/270."""
    r = int(round((rotation or 0) / 90.0)) * 90 % 360
    return r if r in VALID_ROTATIONS else 0


def display_size(w0: float, h0: float, rotation: int = 0) -> Tuple[float, float]:
    """Page size in view space after the given clockwise rotation."""
    r = normalize_rotation(rotation)
    if r in (90, 270):
        return (h0, w0)
    return (w0, h0)


def map_point(x: float, y: float, w0: float, h0: float, rotation: int = 0) -> Tuple[float, float]:
    """Map a point from page space to view space (clockwise rotation)."""
    r = normalize_rotation(rotation)
    if r == 90:
        return (h0 - y, x)
    if r == 180:
        return (w0 - x, h0 - y)
    if r == 270:
        return (y, w0 - x)
    return (x, y)


def unmap_point(u: float, v: float, w0: float, h0: float, rotation: int = 0) -> Tuple[float, float]:
    """Map a point from view space back to page space."""
    r = normalize_rotation(rotation)
    if r == 90:
        return (v, h0 - u)
    if r == 180:
        return (w0 - u, h0 - v)
    if r == 270:
        return (w0 - v, u)
    return (u, v)


def unmap_delta(du: float, dv: float, rotation: int = 0) -> Tuple[float, float]:
    """Map a displacement from view space back to page space."""
    r = normalize_rotation(rotation)
    if r == 90:
        return (dv, -du)
    if r == 180:
        return (-du, -dv)
    if r == 270:
        return (-dv, du)
    return (du, dv)


def map_rect(rect: Rect, w0: float, h0: float, rotation: int = 0) -> Rect:
    """Map a rectangle from page space to view space.

    The result is always a normalised rect (x0 <= x1, y0 <= y1).
    """
    x0, y0, x1, y1 = rect
    corners = [
        map_point(x0, y0, w0, h0, rotation),
        map_point(x1, y0, w0, h0, rotation),
        map_point(x0, y1, w0, h0, rotation),
        map_point(x1, y1, w0, h0, rotation),
    ]
    us = [c[0] for c in corners]
    vs = [c[1] for c in corners]
    return (min(us), min(vs), max(us), max(vs))


def unmap_rect(rect: Rect, w0: float, h0: float, rotation: int = 0) -> Rect:
    """Map a rectangle from view space back to page space."""
    x0, y0, x1, y1 = rect
    corners = [
        unmap_point(x0, y0, w0, h0, rotation),
        unmap_point(x1, y0, w0, h0, rotation),
        unmap_point(x0, y1, w0, h0, rotation),
        unmap_point(x1, y1, w0, h0, rotation),
    ]
    us = [c[0] for c in corners]
    vs = [c[1] for c in corners]
    return (min(us), min(vs), max(us), max(vs))
