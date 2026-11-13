"""Data model for the Python port: runs, lines and editable blocks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .matrix import Matrix, IDENTITY


@dataclass
class TextState:
    font_name: Optional[str] = None
    size: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    scale: float = 1.0
    leading: float = 0.0
    rise: float = 0.0
    render_mode: int = 0
    fill: Tuple[float, float, float] = (0.0, 0.0, 0.0)

    def copy(self) -> "TextState":
        return TextState(
            self.font_name,
            self.size,
            self.char_spacing,
            self.word_spacing,
            self.scale,
            self.leading,
            self.rise,
            self.render_mode,
            self.fill,
        )


@dataclass
class Origin:
    """Where a line starts, and the state there."""

    tm: Matrix = IDENTITY
    tlm: Matrix = IDENTITY
    ctm: Matrix = IDENTITY
    k: float = 1.0
    baseline: float = 0.0
    x: float = 0.0
    state: TextState = field(default_factory=TextState)
    bt_op: int = 0
    bt_state: TextState = field(default_factory=TextState)


@dataclass
class Shown:
    """One text-showing operator's glyphs."""

    op_index: int
    text: str
    glyph_count: int
    advance: float
    tracking: float
    tm: Matrix
    tlm: Matrix
    ctm: Matrix
    state: TextState
    bt_state: TextState
    bt: int
    bt_op: int
    rect: Tuple[float, float, float, float]
    frame: Tuple[float, float, float, float]
    baseline: float
    start_x: float
    end_x: float
    end_y: float
    font_name: str
    base_font: str
    bold: bool
    italic: bool
    size: float
    color: Tuple[float, float, float]
    render_mode: int
    stream: int
    tracking_gaps: Tuple[float, ...] = ()
    #: False when at least one character code had no Unicode mapping.
    decodable: bool = True
    #: The font writes vertically (``/WMode 1``); the layout does not support it.
    vertical: bool = False


@dataclass
class TextLine:
    text: str
    rect: Tuple[float, float, float, float]
    frame: Tuple[float, float, float, float]
    font_name: str
    base_font: str
    size: float
    bold: bool
    italic: bool
    color: Tuple[float, float, float]
    stream: int
    ops: List[int] = field(default_factory=list)
    # Text of each run the line joins, in content order.
    runs: List[str] = field(default_factory=list)
    origin: Origin = field(default_factory=Origin)
    render_mode: int = 0
    invisible: bool = False
    tracking: float = 0.0
    #: False when any run in the line had a character code with no mapping.
    decodable: bool = True
    #: True when the line's font writes vertically.
    vertical: bool = False

    @property
    def rotation(self) -> float:
        from .matrix import rotation_degrees, then

        return rotation_degrees(then(self.origin.tm, self.origin.ctm))


@dataclass
class TextBlock:
    text: str
    rect: Tuple[float, float, float, float]
    base_font: str
    size: float
    bold: bool
    italic: bool
    color: Tuple[float, float, float]
    rotation: float
    lines: List[int] = field(default_factory=list)
    rows: List[List[int]] = field(default_factory=list)
    editor_text: str = ""
    invisible: bool = False

    def same_words(self, draft: str) -> bool:
        return draft.strip() == self.editor_text.strip()


@dataclass
class LineEdit:
    substituted: Optional[str] = None
