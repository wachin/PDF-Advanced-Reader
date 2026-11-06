"""F10-7: Inline text editing - content stream parsing and text replacement."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any, Set

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz

from .edits import Edit, EditError, Scope


@dataclass
class TextRun:
    """A single text run within a content stream."""
    text: str                    # The text shown
    glyphs: List[int]            # Glyph IDs / character codes
    positions: List[fitz.Point]  # Position of each glyph
    font_name: str               # Font resource name
    font_size: float             # Font size
    font_file: Optional[str]     # Font file path if embedded
    color: Tuple[float, float, float]  # RGB color
    render_mode: int             # Text render mode (0=fill, 1=stroke, etc.)
    op_index: int                # Operator index in content stream
    operator: str                # Operator: Tj, TJ, ', "
    operands: List[Any]          # Operator operands


@dataclass
class TextLine:
    """A line of text composed of multiple runs."""
    runs: List[TextRun]
    bbox: fitz.Rect              # Bounding box of the line
    baseline_y: float            # Baseline Y coordinate
    font_name: str               # Primary font
    font_size: float             # Font size
    color: Tuple[float, float, float]  # RGB color


class ContentStreamParser:
    """Parse PDF content streams into operators."""
    
    @staticmethod
    def parse_stream(data: bytes) -> List[Tuple[str, List[Any]]]:
        """Parse a content stream into (operator, operands) tuples."""
        # Use PyMuPDF's built-in parsing
        # This is a simplified version - real implementation would use fitz.ContentStreamParser
        pass
    
    @staticmethod
    def serialize_ops(ops: List[Tuple[str, List[Any]]]) -> bytes:
        """Serialize operators back to content stream bytes."""
        parts = []
        for op, operands in ops:
            for operand in operands:
                if isinstance(operand, str):
                    parts.append(f"({operand})")
                elif isinstance(operand, (int, float)):
                    parts.append(str(operand))
                elif isinstance(operand, fitz.Rect):
                    parts.append(f"[{operand.x0} {operand.y0} {operand.x1} {operand.y1}]")
                # ... handle other types
            parts.append(op)
        return b" ".join(parts)


class TextExtractor:
    """Extract text lines and runs from a PDF page."""
    
    def __init__(self, doc: fitz.Document, page_num: int):
        self.doc = doc
        self.page_num = page_num
        self.page = doc[page_num]
        self.fonts = {}  # font_name -> font_info
    
    def extract_lines(self) -> List[TextLine]:
        """Extract all text lines from the page."""
        # Use PyMuPDF's text extraction with detailed info
        blocks = self.page.get_text("dict")["blocks"]
        lines = []
        
        for block in blocks:
            if block["type"] != 0:  # Only text blocks
                continue
            for line_dict in block["lines"]:
                runs = []
                for span in line_dict["spans"]:
                    run = TextRun(
                        text=span["text"],
                        glyphs=[],  # Would need deeper extraction
                        positions=[fitz.Point(span["bbox"].x0, span["bbox"].y0)],
                        font_name=span["font"],
                        font_size=span["size"],
                        font_file=None,
                        color=self._parse_color(span["color"]),
                        render_mode=0,
                        op_index=-1,
                        operator="Tj",
                        operands=[span["text"]]
                    )
                    runs.append(run)
                
                if runs:
                    bbox = line_dict["bbox"]
                    line = TextLine(
                        runs=runs,
                        bbox=fitz.Rect(bbox),
                        baseline_y=bbox[3],
                        font_name=runs[0].font_name,
                        font_size=runs[0].font_size,
                        color=runs[0].color
                    )
                    lines.append(line)
        
        return lines
    
    @staticmethod
    def _parse_color(color_int: int) -> Tuple[float, float, float]:
        """Convert PyMuPDF color int to RGB tuple."""
        r = (color_int >> 16) & 0xFF
        g = (color_int >> 8) & 0xFF
        b = color_int & 0xFF
        return (r / 255.0, g / 255.0, b / 255.0)


class TextReplacer:
    """Replace text in a PDF page by splicing content streams."""
    
    def __init__(self, doc: fitz.Document, page_num: int):
        self.doc = doc
        self.page_num = page_num
        self.page = doc[page_num]
        self.extractor = TextExtractor(doc, page_num)
    
    def find_text_at_point(self, point: fitz.Point) -> Optional[Tuple[TextLine, TextRun, int]]:
        """Find the text run at a given point."""
        lines = self.extractor.extract_lines()
        for line in lines:
            for run in line.runs:
                if line.bbox.contains(point):
                    return (line, run, 0)
        return None
    
    def replace_text(self, line: TextLine, run: TextRun, new_text: str) -> bool:
        """Replace text in a run by splicing the content stream."""
        # This is the core operation - splice the content stream
        # to replace the text operands of the text showing operator
        pass


@dataclass(frozen=True)
class ReplaceText(Edit):
    """Replace text in a specific text run on a page."""
    page_num: int
    run_text: str           # Original text to replace
    new_text: str           # New text
    font_name: str          # Font name for validation
    font_size: float        # Font size
    color: Tuple[float, float, float]  # RGB color
    
    scope = Scope.CONTENT
    requires_assembly = False  # Text replacement doesn't change structure
    
    @property
    def label(self) -> str:
        return f"Replace text: {self.run_text[:20]}..."
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        # Find and replace the text
        # This would need the content stream splicing logic
        pass


def extract_text_runs(page: fitz.Page) -> List[Dict[str, Any]]:
    """Extract all text runs with their positions and font info."""
    # Use PyMuPDF's detailed text extraction
    text_dict = page.get_text("dict")
    runs = []
    
    for block in text_dict["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                runs.append({
                    "text": span["text"],
                    "bbox": fitz.Rect(span["bbox"]),
                    "font": span["font"],
                    "size": span["size"],
                    "color": span["color"],
                    "flags": span["flags"],
                    "bbox_x0": span["bbox"][0],
                    "bbox_y0": span["bbox"][1],
                    "bbox_x1": span["bbox"][2],
                    "bbox_y1": span["bbox"][3],
                })
    
    return runs


def replace_text_in_page(page: fitz.Page, old_text: str, new_text: str, 
                         font_name: str = "", font_size: float = 0,
                         color: Tuple[float, float, float] = (0, 0, 0)) -> bool:
    """Replace text in a page by splicing the content stream.
    
    This is a simplified version - real implementation would:
    1. Parse the content stream
    2. Find the text showing operators (Tj, TJ, ', ")
    3. Match the text operands
    4. Replace the operands with new text
    5. Handle font fallback if needed
    5. Re-serialize the content stream
    """
    # This is a placeholder - real implementation would splice the content stream
    # For now, we'll use PyMuPDF's built-in text replacement which is limited
    try:
        # Find text instances
        instances = page.search_for(old_text)
        for inst in instances:
            # Add redaction and new text (not true inline editing)
            page.add_redact_annot(inst, fill=(1, 1, 1))
        page.apply_redactions()
        
        # Add new text at the same position (simplified)
        if instances:
            pos = instances[0]
            page.insert_text((pos.x0, pos.y1), new_text, 
                           fontname=font_name, fontsize=font_size, color=color)
        return True
    except Exception:
        return False


# PDF content stream parsing utilities
class ContentStreamSplicer:
    """Splice PDF content streams by replacing operator operands."""
    
    @staticmethod
    def splice_content_stream(stream_data: bytes, 
                              replacements: Dict[int, List[Any]]) -> bytes:
        """Replace operands of specific operators in a content stream.
        
        Args:
            stream_data: Original content stream bytes
            replacements: Dict mapping operator index -> new operands list
            
        Returns:
            Modified content stream bytes
        """
        # This would parse the stream, replace operands, and re-serialize
        # Using pdfcraft's approach: parse -> edit ops -> serialize
        pass


# Integration with our Edit system
@dataclass(frozen=True)
class EditTextRun(Edit):
    """Edit to replace text in a specific run."""
    page_num: int
    old_text: str
    new_text: str
    font_name: str
    font_size: float
    color: Tuple[float, float, float]
    
    scope = Scope.CONTENT
    
    @property
    def label(self) -> str:
        return f"Edit text: {self.old_text[:20]} -> {self.new_text[:20]}"
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        # Would implement content stream splicing here
        pass