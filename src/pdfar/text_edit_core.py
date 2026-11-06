"""F10-7: Inline text editing - core implementation using content stream splicing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional, Dict, Any, Set

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz

from .edits import Edit, EditError, Scope
from .content_stream import (
    ContentStreamParser, ContentStreamSerializer, PDFOperator,
    replace_text_in_stream, TextRunFinder, _extract_text_from_op
)


@dataclass
class TextRunInfo:
    """Information about a text run found in a page."""
    page_num: int
    stream_obj: int          # Content stream object number
    op_index: int            # Operator index in the stream
    operator: str            # Tj, TJ, ', "
    original_text: str       # Text before replacement
    font_name: str           # Current font resource name
    font_size: float         # Font size
    color: Tuple[float, float, float]  # RGB color
    bbox: fitz.Rect          # Bounding box of the text


class TextRunLocator:
    """Locate text runs in a PDF document for editing."""
    
    def __init__(self, doc: fitz.Document):
        self.doc = doc
    
    def find_all_runs(self, page_num: int) -> List[Dict[str, Any]]:
        """Find all text runs on a page with their locations."""
        finder = TextRunFinder(self.doc, page_num)
        return finder.get_text_runs()
    
    def find_text_occurrences(self, page_num: int, search_text: str) -> List[Dict[str, Any]]:
        """Find all occurrences of specific text on a page."""
        runs = self.find_all_runs(page_num)
        return [run for run in runs if search_text in run["text"]]
    
    def find_run_at_point(self, page_num: int, point: fitz.Point) -> Optional[Dict[str, Any]]:
        """Find the text run at a specific point."""
        runs = self.find_all_runs(page_num)
        for run in runs:
            if run["bbox"].contains(point):
                return run
        return None


@dataclass(frozen=True)
class ReplaceTextRun(Edit):
    """Replace text in a specific run by splicing the content stream."""
    page_num: int
    stream_obj: int           # Content stream object number
    op_index: int             # Operator index in the stream
    old_text: str             # Text to replace
    new_text: str             # Replacement text
    font_name: str            # Font name for validation
    font_size: float          # Font size
    color: Tuple[float, float, float]  # RGB color
    
    scope = Scope.CONTENT
    requires_assembly = False  # Content-only edit
    
    @property
    def label(self) -> str:
        return f"Edit text: {self.old_text[:20]}... -> {self.new_text[:20]}..."
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        
        # Get the content stream
        contents = page.get_contents()
        if isinstance(contents, list):
            if self.stream_obj >= len(contents):
                raise EditError(f"Stream object {self.stream_obj} not found")
            stream_obj = contents[self.stream_obj]
        else:
            stream_obj = contents
        
        # Get stream data
        stream_data = doc.xref_stream(stream_obj)
        if not stream_data:
            raise EditError("Could not read content stream")
        
        # Parse and modify
        ops = ContentStreamParser.parse(stream_data)
        if self.op_index >= len(ops):
            raise EditError(f"Operator index {self.op_index} out of range")
        
        op = ops[self.op_index]
        if op.operator not in {"Tj", "TJ", "'", '"'}:
            raise EditError(f"Operator {op.operator} is not a text-showing operator")
        
        # Verify the text matches
        current_text = _extract_text_from_op(op)
        if self.old_text not in current_text:
            raise EditError(f"Expected text '{self.old_text}' not found in operator")
        
        # Create new operands
        new_operands = _make_new_operands(op.operands, self.old_text, self.new_text)
        ops[self.op_index].operands = new_operands
        
        # Serialize and update
        new_stream = ContentStreamSerializer.serialize_ops(ops)
        doc.update_stream(stream_obj, new_stream)


def _extract_text_from_op(op: PDFOperator) -> str:
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


def _make_new_operands(old_operands: List[Any], old_text: str, new_text: str) -> List[Any]:
    """Create new operands with text replaced."""
    new_operands = []
    for operand in old_operands:
        if isinstance(operand, str) and old_text in operand:
            new_operands.append(operand.replace(old_text, new_text))
        elif isinstance(operand, list):
            new_list = []
            for item in operand:
                if isinstance(item, str) and old_text in item:
                    new_list.append(item.replace(old_text, new_text))
                else:
                    new_list.append(item)
            new_operands.append(new_list)
        else:
            new_operands.append(operand)
    return new_operands


@dataclass(frozen=True)
class EditTextRun(Edit):
    """High-level edit to replace text in a run."""
    page_num: int
    old_text: str
    new_text: str
    font_name: str = ""
    font_size: float = 0.0
    color: Tuple[float, float, float] = (0.0, 0.0, 0.0)
    
    scope = Scope.CONTENT
    
    @property
    def label(self) -> str:
        return f"Edit text: {self.old_text[:20]}... -> {self.new_text[:20]}..."
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        page = doc[self.page_num]
        
        # Find the text run
        locator = TextRunLocator(doc)
        occurrences = locator.find_text_occurrences(self.page_num, self.old_text)
        
        if not occurrences:
            raise EditError(f"Text '{self.old_text}' not found on page {self.page_num + 1}")
        
        if len(occurrences) > 1:
            # Multiple matches - need more context
            raise EditError(f"Multiple occurrences of '{self.old_text}' found. Please be more specific.")
        
        run = occurrences[0]
        
        # Apply the replacement
        edit = ReplaceTextRun(
            page_num=self.page_num,
            stream_obj=run["stream_obj"],
            op_index=run["op_index"],
            old_text=self.old_text,
            new_text=self.new_text,
            font_name=run["font_name"],
            font_size=run["font_size"],
            color=run["color"]
        )
        
        # Apply directly to document (bypassing session for simplicity)
        # In a real implementation, this would go through the session
        page = doc[self.page_num]
        contents = page.get_contents()
        
        if isinstance(contents, list):
            stream_obj = contents[run["stream_obj"]]
        else:
            stream_obj = contents
        
        stream_data = doc.xref_stream(stream_obj)
        if not stream_data:
            raise EditError("Could not read content stream")
        
        ops = ContentStreamParser.parse(stream_data)
        op = ops[run["op_index"]]
        new_operands = _make_new_operands(op.operands, self.old_text, self.new_text)
        op.operands = new_operands
        
        new_stream = ContentStreamSerializer.serialize_ops(ops)
        doc.update_stream(stream_obj, new_stream)


def _make_new_operands(old_operands: List[Any], old_text: str, new_text: str) -> List[Any]:
    """Create new operands with text replaced."""
    new_operands = []
    for operand in old_operands:
        if isinstance(operand, str) and old_text in operand:
            new_operands.append(operand.replace(old_text, new_text))
        elif isinstance(operand, list):
            new_list = []
            for item in operand:
                if isinstance(item, str) and old_text in item:
                    new_list.append(item.replace(old_text, new_text))
                else:
                    new_list.append(item)
            new_operands.append(new_list)
        else:
            new_operands.append(operand)
    return new_operands


# Helper function for UI integration
def find_text_at_point(doc: fitz.Document, page_num: int, point: fitz.Point) -> Optional[Dict[str, Any]]:
    """Find the text run at a specific point on a page."""
    locator = TextRunLocator(doc)
    return locator.find_run_at_point(page_num, point)


def find_text_occurrences(doc: fitz.Document, page_num: int, text: str) -> List[Dict[str, Any]]:
    """Find all occurrences of text on a page."""
    locator = TextRunLocator(doc)
    return locator.find_text_occurrences(page_num, text)