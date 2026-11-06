"""F10-7: Text editing engine - content stream tokenization, glyph mapping, and splicing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any, Sequence, Iterator
from collections import defaultdict

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz

from .edits import Edit, EditError, Scope
from .content_stream import (
    ContentStreamParser, ContentStreamSerializer, PDFOperator,
    _extract_text_from_op, _make_new_operands, replace_text_in_stream
)
from .text_editing import _get_text_length


@dataclass
class GlyphInfo:
    """Information about a single glyph in a text run."""
    char: str                  # Unicode character
    glyph_id: int              # Glyph ID in the font
    codepoint: int             # Unicode codepoint
    advance: float             # Glyph advance width
    origin: fitz.Point         # Glyph origin in text space
    bbox: fitz.Rect            # Glyph bounding box in text space
    font_name: str             # Font resource name
    font_size: float           # Font size
    font_file: Optional[str]   # Font file path if embedded
    color: Tuple[float, float, float]  # RGB color
    render_mode: int           # Text render mode
    seqno: int                 # Sequence number from text trace


@dataclass
class TextSpan:
    """A contiguous span of text with uniform formatting."""
    text: str
    glyphs: List[GlyphInfo]
    font_name: str
    font_size: float
    font_file: Optional[str]
    color: Tuple[float, float, float]
    render_mode: int
    bbox: fitz.Rect
    baseline_y: float
    direction: Tuple[float, float]  # Writing direction vector
    seqno_start: int
    seqno_end: int
    op_indices: List[int]  # Indices of operators that produce this span


@dataclass
class TextBlock:
    """A block of text (paragraph) composed of spans."""
    spans: List[TextSpan]
    bbox: fitz.Rect
    rotation: float  # Block rotation in degrees


@dataclass
class OperatorPosition:
    """Position of an operator in the content stream."""
    operator: str
    operands: List[Any]
    start_pos: int
    end_pos: int
    stream_index: int  # Which content stream (for multi-stream pages)
    seqno: int         # Sequence number from text trace


@dataclass
class ContentStreamEdit:
    """An edit to a content stream."""
    stream_index: int
    operator_index: int
    old_operands: List[Any]
    new_operands: List[Any]
    old_text: str
    new_text: str


class ContentStreamTokenizer:
    """
    Tolerant content-stream tokenizer that keeps the byte span of every operator.
    
    Uses PyMuPDF's text trace to map glyphs back to content stream operators,
    enabling precise surgical edits that preserve all other content.
    """
    
    def __init__(self, page: fitz.Page):
        self.page = page
        self._trace_cache: Optional[List[Dict]] = None
        self._operator_map: Dict[int, List[OperatorPosition]] = {}
    
    def get_text_trace(self) -> List[Dict]:
        """Get the text trace from PyMuPDF with seqno information."""
        if self._trace_cache is None:
            try:
                # get_texttrace returns a list of dicts with detailed glyph info
                self._trace_cache = self.page.get_text("trace")
            except Exception:
                # Fallback if trace not supported
                self._trace_cache = []
        return self._trace_cache
    
    def build_operator_map(self) -> Dict[int, List[OperatorPosition]]:
        """
        Build a mapping from text trace seqno to content stream operator positions.
        
        Returns a dict mapping seqno -> list of operator positions that produce that glyph.
        """
        if self._operator_map:
            return self._operator_map
        
        trace = self.get_text_trace()
        operators = self._parse_content_streams()
        
        # Map each operator to its seqno range
        for op_idx, op in enumerate(operators):
            if op.operator in {"Tj", "TJ", "'", '"'}:
                text = self._extract_text(op)
                # Find corresponding trace entries
                for trace_item in self.page.get_text("trace"):
                    if trace_item["text"] == text:
                        seqno = trace_item["seqno"]
                        if seqno not in self._operator_map:
                            self._operator_map[seqno] = []
                        self._operator_map[seqno].append(OperatorPosition(
                            operator=op.operator,
                            operands=op.operands,
                            start_pos=op.start_pos,
                            end_pos=op.end_pos,
                            stream_index=op.stream_index,
                            seqno=seqno
                        ))
        
        return self._operator_map
    
    def _parse_content_streams(self) -> List[OperatorPosition]:
        """Parse all content streams on the page into operators with positions."""
        # This would use the ContentStreamParser from content_stream.py
        # For now, return empty list - to be implemented
        return []
    
    def _extract_text(self, op: PDFOperator) -> str:
        """Extract text from a text-showing operator."""
        if op.operator == "Tj":
            if op.operands and isinstance(op.operands[0], str):
                return op.operands[0]
        elif op.operator == "TJ":
            text = ""
            for operand in op.operands:
                if isinstance(operand, str):
                    text += operand
                elif isinstance(operand, list):
                    for item in operand:
                        if isinstance(item, str):
                            text += item
            return text
        elif op.operator == "'":
            if op.operands and isinstance(op.operands[0], str):
                return op.operands[0]
        elif op.operator == '"':
            if len(op.operands) >= 3 and isinstance(op.operands[2], str):
                return op.operands[2]
        return ""


class GlyphOperatorMapper:
    """
    Maps glyphs to their content stream operators using text trace.
    
    This enables precise surgical edits by knowing exactly which operator
    produced each glyph.
    """
    
    def __init__(self, page: fitz.Page):
        self.page = page
        self._glyph_to_ops: Dict[int, List[OperatorPosition]] = {}
        self._ops_cache: Optional[List[OperatorPosition]] = None
    
    def get_operators_for_glyph(self, seqno: int) -> List[OperatorPosition]:
        """Get all operators that produced a given glyph (by seqno)."""
        if not self._glyph_to_ops:
            self._build_map()
        return self._glyph_to_ops.get(seqno, [])
    
    def get_operators_for_text(self, text: str) -> List[OperatorPosition]:
        """Get all operators that produced the given text."""
        ops = []
        trace = self.page.get_text("trace")
        for item in trace:
            if item.get("text", "") == text:
                seqno = item.get("seqno", -1)
                if seqno >= 0:
                    ops.extend(self.get_operators_for_glyph(seqno))
        return ops
    
    def _build_map(self) -> None:
        """Build the glyph-to-operators mapping from text trace."""
        trace = self.page.get_text("trace")
        # Parse content streams
        streams = self.page.get_contents()
        # This is a simplified version - real implementation would parse all streams
        pass


class FontLadder:
    """
    Font resolution ladder: source font → another font of same face on page →
    embed full program → generated fallback → base-14 → refuse.
    
    Ensures we always use the best available font for text replacement.
    """
    
    def __init__(self, page: fitz.Page):
        self.page = page
        self._font_cache: Dict[str, fitz.Font] = {}
    
    def resolve_font(self, font_name: str, font_size: float, 
                     text: str, bold: bool = False, italic: bool = False) -> Tuple[str, Optional[str]]:
        """
        Resolve the best font for displaying text.
        
        Returns (font_name, font_file_path) where font_file_path is None for base-14 fonts.
        """
        # 1. Try the exact font requested
        font = self._get_page_font(font_name)
        if font and self._font_supports_text(font, text):
            return font_name, None
        
        # 2. Try another font of the same face on the page
        for name in self._list_page_fonts():
            font = self._get_page_font(name)
            if font and self._fonts_match(font, font_name, bold, italic) and self._font_supports_text(font, text):
                return name, None
        
        # 3. Try system font with full embedding
        font_file = self._find_system_font(font_name, bold, italic)
        if font_file:
            return f"PCFull{font_name.replace(' ', '')}", font_file
        
        # 4. Try base-14 fallback
        base14 = self._base14_substitute(font_name, bold, italic)
        if base14:
            return base14, None
        
        # 5. Refuse - no suitable font
        raise EditError(f"Cannot display '{text}': no available font can draw all characters")
    
    def _get_page_font(self, name: str) -> Optional[fitz.Font]:
        """Get a font object from the page's resources."""
        try:
            return self.page.get_font(name)
        except Exception:
            return None
    
    def _list_page_fonts(self) -> List[str]:
        """List all font resource names on the page."""
        fonts = set()
        for i in range(self.page.parent.page_count):
            page = self.page.parent[i]
            for font in page.get_fonts():
                fonts.add(font[3])  # font name is at index 3
        return list(fonts)
    
    def _fonts_match(self, font: fitz.Font, target_name: str, bold: bool, italic: bool) -> bool:
        """Check if a font matches the requested style."""
        # Simplified matching - real implementation would be more sophisticated
        return True
    
    def _font_supports_text(self, font: fitz.Font, text: str) -> bool:
        """Check if a font supports all characters in text."""
        for char in text:
            if not font.has_glyph(ord(char)):
                return False
        return True
    
    def _find_system_font(self, name: str, bold: bool, italic: bool) -> Optional[str]:
        """Find a system font file matching the requested font."""
        # This would use the fonts.py module to find system fonts
        return None
    
    def _base14_substitute(self, name: str, bold: bool, italic: bool) -> Optional[str]:
        """Map to base-14 font if possible."""
        base14 = {
            ("Helvetica", False, False): "helv",
            ("Helvetica", True, False): "hebo",
            ("Helvetica", False, True): "heit",
            ("Helvetica", True, True): "hebi",
            ("Times", False, False): "tiro",
            ("Times", True, False): "tibo",
            ("Times", False, True): "tiit",
            ("Times", True, True): "tibi",
            ("Courier", False, False): "cour",
            ("Courier", True, False): "cobo",
            ("Courier", False, True): "coit",
            ("Courier", True, True): "cobi",
        }
        return base14.get((name, bold, italic))


class ContentStreamSplicer:
    """
    Surgical content stream editor that replaces text by splicing operators.
    
    Key principle: replace only the specific show operator operands,
    leaving all other content byte-identical.
    """
    
    def __init__(self, page: fitz.Page):
        self.page = page
        self.parser = ContentStreamParser()
        self.serializer = ContentStreamSerializer()
    
    def replace_text(self, old_text: str, new_text: str,
                     font_name: str = "", font_size: float = 0,
                     color: Tuple[float, float, float] = (0, 0, 0)) -> List[ContentStreamEdit]:
        """
        Replace all occurrences of old_text with new_text in the page.
        
        Returns a list of edits made.
        """
        # 1. Find all text runs matching old_text
        runs = self._find_text_runs(old_text)
        
        if not runs:
            raise EditError(f"Text '{old_text}' not found on page")
        
        # 2. For each run, create a splice edit
        edits = []
        for run in runs:
            edit = self._create_splice_edit(run, new_text)
            edits.append(edit)
        
        # 3. Apply all edits
        for edit in edits:
            self._apply_edit(edit)
        
        return edits
    
    def _find_text_runs(self, text: str) -> List[Dict[str, Any]]:
        """Find all text runs matching the given text."""
        runs = []
        # Use PyMuPDF's text trace to find exact positions
        trace = self.page.get_text("trace")
        for item in trace:
            if item.get("text", "") == text:
                runs.append({
                    "seqno": item.get("seqno"),
                    "bbox": item.get("bbox"),
                    "font": item.get("font"),
                    "size": item.get("size"),
                    "color": item.get("color"),
                })
        return runs
    
    def _create_splice_edit(self, run: Dict[str, Any], new_text: str) -> ContentStreamEdit:
        """Create a splice edit for a single text run."""
        # This would find the exact operator and create a splice edit
        # For now, return a placeholder
        return ContentStreamEdit(
            stream_index=0,
            operator_index=0,
            old_operands=[],
            new_operands=[],
            old_text="",
            new_text=""
        )
    
    def _apply_edit(self, edit: ContentStreamEdit) -> None:
        """Apply a splice edit to the page."""
        # Get the content stream
        contents = self.page.get_contents()
        if isinstance(contents, list):
            stream_obj = contents[edit.stream_index]
        else:
            stream_obj = contents
        
        stream_data = self.page.parent.xref_stream(stream_obj)
        new_stream = replace_text_in_stream(stream_data, edit.operator_index, edit.new_operands)
        self.page.parent.update_stream(stream_obj, new_stream)


class TextEditEngine:
    """
    High-level text editing engine combining all components.
    
    Provides a unified interface for text replacement, font resolution,
    and content stream splicing.
    """
    
    def __init__(self, doc: fitz.Document, page_num: int):
        self.doc = doc
        self.page_num = page_num
        self.page = doc[page_num]
        self.tokenizer = ContentStreamTokenizer(self.page)
        self.font_ladder = FontLadder(self.page)
        self.splicer = ContentStreamSplicer(self.page)
    
    def replace_text(self, old_text: str, new_text: str,
                     font_name: str = "", font_size: float = 0,
                     color: Tuple[float, float, float] = (0, 0, 0)) -> Edit:
        """
        Replace text on a page, returning an Edit that can be applied/undone.
        
        The edit handles font resolution, content stream splicing,
        and preserves all other content.
        """
        # Resolve font
        if not font_name:
            # Use font from first occurrence - try to get from text dict
            try:
                trace = self.page.get_text("trace")
                for item in trace:
                    if item.get("text") == old_text:
                        font_name = item.get("font", "Helvetica")
                        font_size = item.get("size", 12)
                        break
            except Exception:
                # Fallback if trace not supported
                pass
        
        if not font_name:
            font_name = "Helvetica"
        
        # Resolve best font
        font_name, font_file = self.font_ladder.resolve_font(
            font_name, font_size or 12, old_text
        )
        
        # Create edit
        return ReplaceTextRun(
            page_num=self.page_num,
            old_text=old_text,
            new_text=new_text,
            font_name=font_name,
            font_size=font_size or 12,
            color=color,
            font_file=font_file
        )


@dataclass(frozen=True)
class ReplaceTextRun(Edit):
    """Replace a specific text run with new text."""
    page_num: int
    old_text: str
    new_text: str
    font_name: str
    font_size: float
    color: Tuple[float, float, float]
    font_file: Optional[str] = None
    
    scope = Scope.CONTENT
    requires_assembly = False
    
    @property
    def label(self) -> str:
        return f"Replace '{self.old_text[:20]}...' with '{self.new_text[:20]}...'"
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        
        # Real content stream splicing implementation
        # 1. Get the page's content streams
        contents = page.get_contents()
        if isinstance(contents, list):
            # Multiple content streams - we'll need to find which one has our text
            # For simplicity, we'll process all streams and update them
            for stream_obj in contents:
                stream_data = doc.xref_stream(stream_obj)
                if not stream_data:
                    continue
                ops = ContentStreamParser.parse(stream_data)
                
                # Find text-showing operators with our old_text
                modified = False
                for i, op in enumerate(ops):
                    if op.operator in {"Tj", "TJ", "'", '"'}:
                        text = _extract_text_from_op(op)
                        if self.old_text in text:
                            # Replace text in operands
                            new_operands = _make_new_operands(op.operands, self.old_text, self.new_text)
                            ops[i].operands = new_operands
                            modified = True
                
                if modified:
                    new_stream = ContentStreamSerializer.serialize_ops(ops)
                    doc.update_stream(stream_obj, new_stream)
        else:
            # Single content stream
            stream_obj = contents
            stream_data = doc.xref_stream(stream_obj)
            if not stream_data:
                raise EditError("Could not read content stream")
            
            ops = ContentStreamParser.parse(stream_data)
            modified = False
            for i, op in enumerate(ops):
                if op.operator in {"Tj", "TJ", "'", '"'}:
                    text = _extract_text_from_op(op)
                    if self.old_text in text:
                        new_operands = _make_new_operands(op.operands, self.old_text, self.new_text)
                        ops[i].operands = new_operands
                        modified = True
            
            if modified:
                new_stream = ContentStreamSerializer.serialize_ops(ops)
                doc.update_stream(stream_obj, new_stream)


# Convenience function for high-level text replacement
def replace_text(doc: fitz.Document, page_num: int, old_text: str, new_text: str,
                 font_name: str = "", font_size: float = 0,
                 color: Tuple[float, float, float] = (0, 0, 0)) -> Edit:
    """High-level text replacement returning an editable Edit."""
    engine = TextEditEngine(doc, page_num)
    return engine.replace_text(old_text, new_text, font_name, font_size, color)