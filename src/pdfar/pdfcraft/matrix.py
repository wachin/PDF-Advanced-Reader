"""2-D affine matrices in PDF convention ``[a b c d e f]``.

x' = a*x + c*y + e
y' = b*x + d*y + f

``then(m, n)`` applies ``m`` first and ``n`` second, matching PdfCraft's
``Matrix::then`` (``crates/content``), so ``then(tm, ctm)`` is the text
rendering matrix.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

Matrix = Tuple[float, float, float, float, float, float]

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def matrix(values: Sequence[float]) -> Matrix:
    a, b, c, d, e, f = (float(v) for v in values)
    return (a, b, c, d, e, f)


def translation(x: float, y: float) -> Matrix:
    return (1.0, 0.0, 0.0, 1.0, float(x), float(y))


def then(m: Matrix, n: Matrix) -> Matrix:
    """Apply ``m`` then ``n`` (``n`` is the outer transform)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m: Matrix, x: float, y: float) -> Tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def scale_of(m: Matrix) -> Tuple[float, float]:
    """Lengths of the transformed unit x and y axes."""
    a, b, c, d, _e, _f = m
    return (math.hypot(a, b), math.hypot(c, d))


def direction(m: Matrix) -> Tuple[float, float]:
    """Unit vector of the transformed x axis (the baseline direction)."""
    a, b, _c, _d, _e, _f = m
    length = math.hypot(a, b)
    if not math.isfinite(length) or length <= 1e-9:
        return (1.0, 0.0)
    return (a / length, b / length)


def to_frame(direction_xy: Tuple[float, float], point: Tuple[float, float]) -> Tuple[float, float]:
    """Point as ``(along, across)`` a baseline running in ``direction_xy``."""
    dx, dy = direction_xy
    x, y = point
    return (x * dx + y * dy, y * dx - x * dy)


def invert(m: Matrix) -> Matrix | None:
    a, b, c, d, e, f = m
    det = a * d - b * c
    if not math.isfinite(det) or abs(det) < 1e-12:
        return None
    ia = d / det
    ib = -b / det
    ic = -c / det
    id_ = a / det
    ie = -(e * ia + f * ic)
    if_ = -(e * ib + f * id_)
    return (ia, ib, ic, id_, ie, if_)


def rotation_degrees(m: Matrix) -> float:
    a, b, _c, _d, _e, _f = m
    return math.degrees(math.atan2(b, a))
