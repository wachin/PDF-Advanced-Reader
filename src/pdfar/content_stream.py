"""F10-7: PDF content stream parsing and splicing for inline text editing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple, Optional, Any, Union

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz


@dataclass
class PDFOperator:
    """A parsed PDF operator with its operands."""
    operator: str                    # e.g., "Tj", "TJ", "Tf", "BT", "ET"
    operands: List[Any]              # Operands as Python objects
    start_pos: int                   # Start position in stream
    end_pos: int                     # End position in stream


@dataclass 
class TextShowOp:
    """A text-showing operator (Tj, TJ, ', \")."""
    operator: str                    # Tj, TJ, ', "
    text: str                        # The text shown
    operands: List[Any]              # Original operands
    start_pos: int                   # Start in stream
    end_pos: int                     # End in stream
    font_name: str                   # Current font
    font_size: float                 # Current font size
    color: Tuple[float, float, float]  # RGB color


class ContentStreamParser:
    """Parse PDF content streams into operators."""
    
    # Text-showing operators
    TEXT_SHOW_OPS = {"Tj", "TJ", "'", '"'}
    
    # All known PDF operators
    PDF_OPERATORS = {
        # Graphics state
        'b', 'B', 'b*', 'B*', 'c', 'cm', 'd', 'Do', 'DP', 'f', 'F', 'f*', 'G', 'g', 'gs',
        'h', 'i', 'j', 'J', 'K', 'k', 'l', 'm', 'M', 'n', 'q', 'Q', 're', 'rg', 'RG',
        'ri', 's', 'S', 'sc', 'SC', 'scn', 'SCN', 'sh', 'T*', 'Tc', 'Td', 'TD', 'Tf',
        'Tj', 'TJ', 'TL', 'Tm', 'Tr', 'Ts', 'Tw', 'Tz', 'v', 'w', 'W', 'W*',
        # Text showing
        'Tj', 'TJ', "'", '"',
        # Inline images
        'BI', 'ID', 'EI',
        # Marked content
        'BMC', 'BDC', 'EMC', 'MP',
        # Compatibility
        'BX', 'EX',
    }
    
    @classmethod
    def _parse_operand(cls, data: bytes, i: int) -> Tuple[Any, int]:
        """Parse a single operand from content stream starting at position i.
        
        Returns (operand, new_position).
        """
        data_len = len(data)
        
        # Skip whitespace
        while i < data_len and data[i:i+1] in b' \t\r\n\f':
            i += 1
        if i >= data_len:
            return None, i
        
        c = data[i:i+1]
        
        # Literal string
        if c == b'(':
            end = cls._find_matching_paren(data, i)
            if end > i:
                literal = data[i+1:end].decode('utf-8', errors='ignore')
                literal = literal.replace('\\(', '(').replace('\\)', ')')
                return literal, end + 1
        
        # Hex string or dictionary
        elif c == b'<':
            if i + 1 < len(data) and data[i+1:i+2] == b'<':
                # Dictionary << ... >>
                end = data.find(b'>>', i)
                if end > i:
                    return data[i:end+2], end + 2
            else:
                # Hex string
                end = data.find(b'>', i)
                if end > i:
                    hex_str = data[i+1:end].decode('ascii', errors='ignore')
                    try:
                        return bytes.fromhex(hex_str), end + 1
                    except ValueError:
                        return data[i:end+1], end + 1
        
        # Array
        elif c == b'[':
            # Array - parse into a list
            end = cls._find_matching_bracket(data, i)
            if end > i:
                array_items = cls._parse_array(data, i, end)
                return array_items, end + 1
            else:
                i += 1
        
        # Dictionary or hex string
        elif c == b'<':
            if i + 1 < len(data) and data[i+1:i+2] == b'<':
                # Dictionary
                end = data.find(b'>>', i)
                if end > i:
                    return data[i:end+2], end + 2
            else:
                # Hex string
                end = data.find(b'>', i)
                if end > i:
                    hex_str = data[i+1:end].decode('ascii', errors='ignore')
                    try:
                        return bytes.fromhex(hex_str), end + 1
                    except ValueError:
                        return data[i:end+1], end + 1
        
        # Number
        elif c in b'0123456789.-':
            j = i
            while j < len(data) and data[j:j+1] in b'0123456789.-':
                j += 1
            num_str = data[i:j].decode('ascii')
            try:
                if b'.' in data[i:j]:
                    return float(data[i:j].decode('ascii')), j
                else:
                    return int(data[i:j].decode('ascii')), j
            except ValueError:
                return data[i:j].decode('ascii', errors='ignore'), j
        
        # Name
        elif c == b'/':
            j = i + 1
            while j < len(data) and data[j:j+1] not in b' \t\r\n\f()[]<>':
                j += 1
            name = data[i+1:j].decode('ascii', errors='ignore')
            return name, j
        
        # Boolean/null
        elif data[i:i+4] == b'true':
            return True, i + 4
        elif data[i:i+5] == b'false':
            return False, i + 5
        elif data[i:i+4] == b'null':
            return None, i + 4
        
        # Unknown - skip
        return None, i + 1
    
    @classmethod
    def _parse_all_operands(cls, data: bytes, start_pos: int) -> Tuple[List[Any], int]:
        """Parse all operands from start_pos until an operator or end of stream."""
        operands = []
        i = start_pos
        data_len = len(data)
        
        while i < data_len:
            # Skip whitespace
            while i < data_len and data[i:i+1] in b' \t\r\n\f':
                i += 1
            if i >= data_len:
                break
            
            c = data[i:i+1]
            
            # Check if this looks like an operator (letters followed by whitespace, or single-char operators ' and ")
            if c in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                j = i + 1
                while j < len(data) and data[j:j+1] in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                if j >= len(data) or data[j:j+1] in b' \t\r\n\f':
                    # This is an operator, stop parsing operands
                    break
            elif c in b"'\"":
                # Single-character operators ' and "
                if i + 1 >= len(data) or data[i+1:i+2] in b' \t\r\n\f':
                    # This is an operator, stop parsing operands
                    break
            
            # Parse operand
            operand, i = cls._parse_operand(data, i)
            if operand is not None:
                operands.append(operand)
        
        return operands, i
    
    @classmethod
    def _parse_operand(cls, data: bytes, i: int) -> Tuple[Any, int]:
        """Parse a single operand from content stream starting at position i.
        
        Returns (operand, new_position).
        """
        data_len = len(data)
        
        # Skip whitespace
        while i < data_len and data[i:i+1] in b' \t\r\n\f':
            i += 1
        if i >= data_len:
            return None, i
        
        c = data[i:i+1]
        
        # Literal string
        if c == b'(':
            end = cls._find_matching_paren(data, i)
            if end > i:
                literal = data[i+1:end].decode('utf-8', errors='ignore')
                literal = literal.replace('\\(', '(').replace('\\)', ')')
                return literal, end + 1
        
        # Hex string or dictionary
        elif c == b'<':
            if i + 1 < len(data) and data[i+1:i+2] == b'<':
                # Dictionary << ... >>
                end = data.find(b'>>', i)
                if end > i:
                    return data[i:end+2], end + 2
            else:
                # Hex string
                end = data.find(b'>', i)
                if end > i:
                    hex_str = data[i+1:end].decode('ascii', errors='ignore')
                    try:
                        return bytes.fromhex(hex_str), end + 1
                    except ValueError:
                        return data[i:end+1], end + 1
        
        # Array
        elif c == b'[':
            # Array - parse into a list
            end = cls._find_matching_bracket(data, i)
            if end > i:
                array_items = cls._parse_array(data, i, end)
                return array_items, end + 1
            else:
                i += 1
        
        # Dictionary or hex string
        elif c == b'<':
            if i + 1 < len(data) and data[i+1:i+2] == b'<':
                # Dictionary
                end = data.find(b'>>', i)
                if end > i:
                    return data[i:end+2], end + 2
            else:
                # Hex string
                end = data.find(b'>', i)
                if end > i:
                    hex_str = data[i+1:end].decode('ascii', errors='ignore')
                    try:
                        return bytes.fromhex(hex_str), end + 1
                    except ValueError:
                        return data[i:end+1], end + 1
        
        # Number
        elif c in b'0123456789.-':
            j = i
            while j < len(data) and data[j:j+1] in b'0123456789.-':
                j += 1
            num_str = data[i:j].decode('ascii')
            try:
                if b'.' in data[i:j]:
                    return float(data[i:j].decode('ascii')), j
                else:
                    return int(data[i:j].decode('ascii')), j
            except ValueError:
                return data[i:j].decode('ascii', errors='ignore'), j
        
        # Name
        elif c == b'/':
            j = i + 1
            while j < len(data) and data[j:j+1] not in b' \t\r\n\f()[]<>':
                j += 1
            name = data[i+1:j].decode('ascii', errors='ignore')
            return name, j
        
        # Boolean/null
        elif data[i:i+4] == b'true':
            return True, i + 4
        elif data[i:i+5] == b'false':
            return False, i + 5
        elif data[i:i+4] == b'null':
            return None, i + 4
        
        # Unknown - skip
        return None, i + 1
    
    @staticmethod
    def _find_matching_paren(data: bytes, start: int, end: Optional[int] = None) -> int:
        """Find matching closing parenthesis, handling nested parens and escapes."""
        depth = 1
        i = start + 1
        search_end = end if end is not None else len(data)
        while i < search_end and depth > 0:
            if data[i:i+1] == b'\\' and i + 1 < search_end:
                i += 2  # Skip escaped char
            elif data[i:i+1] == b'(':
                depth += 1
                i += 1
            elif data[i:i+1] == b')':
                depth -= 1
                i += 1
            else:
                i += 1
        return i - 1 if depth == 0 else search_end - 1
    
    @staticmethod
    def _find_matching_bracket(data: bytes, start: int) -> int:
        """Find matching closing bracket, handling nested brackets."""
        depth = 1
        i = start + 1
        while i < len(data) and depth > 0:
            if data[i:i+1] == b'\\' and i + 1 < len(data):
                i += 2  # Skip escaped char
            elif data[i:i+1] == b'[':
                depth += 1
                i += 1
            elif data[i:i+1] == b']':
                depth -= 1
                i += 1
            else:
                i += 1
        return i - 1 if depth == 0 else len(data) - 1
    
    @classmethod
    def _parse_array(cls, data: bytes, start: int, end: int) -> List[Any]:
        """Parse array contents into a list of Python objects."""
        items = []
        i = start + 1  # Skip opening '['
        end_pos = end
        while i < end:
            # Skip whitespace
            while i < end and data[i:i+1] in b' \t\r\n\f':
                i += 1
            if i >= end:
                break
            
            c = data[i:i+1]
            
            # String literal
            if c == b'(':
                paren_end = cls._find_matching_paren(data, i, end_pos)
                if paren_end > i:
                    literal = data[i+1:paren_end].decode('utf-8', errors='ignore')
                    literal = literal.replace('\\(', '(').replace('\\)', ')')
                    items.append(literal)
                    i = paren_end + 1
                    continue
            
            # Number
            elif data[i:i+1] in b'0123456789.-':
                j = i
                while j < end and data[j:j+1] in b'0123456789.-':
                    j += 1
                num_str = data[i:j].decode('ascii')
                try:
                    if b'.' in data[i:j]:
                        items.append(float(data[i:j].decode('ascii')))
                    else:
                        items.append(int(data[i:j].decode('ascii')))
                except ValueError:
                    items.append(data[i:j].decode('ascii', errors='ignore'))
                i = j
                continue
            
            # Hex string
            elif c == b'<':
                if i + 1 < end and data[i+1:i+2] == b'<':
                    # Dictionary << ... >>
                    end_dict = data.find(b'>>', i)
                    if end_dict > i and end_dict < end:
                        items.append(data[i:end_dict+2])
                        i = end_dict + 2
                        continue
                else:
                    # Hex string
                    end_hex = data.find(b'>', i)
                    if end_hex > i and end_hex < end:
                        hex_str = data[i+1:end_hex].decode('ascii', errors='ignore')
                        try:
                            items.append(bytes.fromhex(hex_str))
                        except ValueError:
                            items.append(data[i:end_hex+1])
                        i = end_hex + 1
                        continue
                    else:
                        i += 1
                        continue
            
            # Name
            elif data[i:i+1] == b'/':
                j = i + 1
                while j < end and data[j:j+1] not in b' \t\r\n\f()[]<>':
                    j += 1
                name = data[i+1:j].decode('ascii', errors='ignore')
                items.append(name)
                i = j
                continue
            
            # Skip whitespace
            elif data[i:i+1] in b' \t\r\n\f':
                i += 1
                continue
            
            # Unknown - skip
            else:
                i += 1
        
        return items
    
    @classmethod
    def parse(cls, data: bytes) -> List[PDFOperator]:
        """Parse content stream into operators with positions.

        PDF content streams use postfix notation: operands followed by operator.
        This parser handles multiple operators in sequence by parsing
        one operator-operand group at a time.
        """
        ops = []
        i = 0
        data_len = len(data)
        
        while i < data_len:
            # Skip whitespace
            while i < data_len and data[i:i+1] in b' \t\r\n\f':
                i += 1
            if i >= data_len:
                break
            
            start_pos = i
            
            # Parse operands for the next operator
            operands, i = cls._parse_all_operands(data, i)
            
            # Skip whitespace
            while i < data_len and data[i:i+1] in b' \t\r\n\f':
                i += 1
            if i >= data_len:
                break
            
            # Read operator (handle both letter operators and single-char operators ' and ")
            op_start = i
            while i < data_len and data[i:i+1] in b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                i += 1
            
            # Also handle single-character operators ' and "
            if i == op_start and i < data_len and data[i:i+1] in b"'\"":
                i += 1
            
            if i > op_start:
                operator = data[op_start:i].decode('ascii', errors='ignore')
                end_pos = i
                ops.append(PDFOperator(
                    operator=operator,
                    operands=operands,
                    start_pos=start_pos,
                    end_pos=end_pos
                ))
                # Continue to next operator
                continue
            else:
                i += 1
        
        return ops


class ContentStreamSerializer:
    """Serialize operators back to content stream bytes."""
    
    @staticmethod
    def serialize_operand(operand: Any, operator: str = "") -> bytes:
        """Serialize a single operand to bytes.
        
        For Tf operator, the first operand (font name) should be a name object (prefixed with /).
        For text-showing operators (Tj, TJ, ', "), string operands should be literal strings (in parentheses).
        """
        if isinstance(operand, str):
            # Check if this is a font name for Tf operator
            if operator == "Tf":
                # Font names are name objects (prefixed with /)
                # Escape special characters in name
                escaped = operand.replace('#', '#20').replace('/', '#2F')
                return b'/' + escaped.encode('ascii')
            else:
                # String literal (for Tj, TJ, ', ")
                escaped = operand.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
                return b'(' + escaped.encode('utf-8') + b')'
        elif isinstance(operand, bytes):
            return b'<' + operand.hex().upper().encode('ascii') + b'>'
        elif isinstance(operand, (int, float)):
            return str(operand).encode('ascii')
        elif isinstance(operand, bool):
            return b'true' if operand else b'false'
        elif operand is None:
            return b'null'
        elif isinstance(operand, list):
            # Array
            inner = b' '.join(ContentStreamSerializer.serialize_operand(x, operator) for x in operand)
            return b'[' + inner + b']'
        elif isinstance(operand, dict):
            # Dictionary - simplified
            return b'<< >>'
        else:
            return str(operand).encode('ascii')
    
    @classmethod
    def serialize_ops(cls, ops: List[PDFOperator]) -> bytes:
        """Serialize operators back to content stream bytes."""
        parts = []
        for op in ops:
            # Serialize operands with operator context
            for operand in op.operands:
                parts.append(cls.serialize_operand(operand, op.operator))
            # Operator
            parts.append(op.operator.encode('ascii'))
        return b' '.join(parts) + b'\n'


def _extract_text_from_op(op: PDFOperator) -> str:
    """Extract text from a text-showing operator."""
    if op.operator == "Tj":
        if op.operands and isinstance(op.operands[0], str):
            return op.operands[0]
        elif op.operands and isinstance(op.operands[0], bytes):
            # Handle hex string - try latin-1 first (common for PDF)
            try:
                return op.operands[0].decode('latin-1', errors='ignore')
            except Exception:
                return op.operands[0].decode('utf-8', errors='ignore')
    elif op.operator == "TJ":
        text = ""
        for operand in op.operands:
            if isinstance(operand, str):
                text += operand
            elif isinstance(operand, bytes):
                # Hex string - try latin-1 first (common for PDF)
                try:
                    text += operand.decode('latin-1', errors='ignore')
                except Exception:
                    text += operand.decode('utf-8', errors='ignore')
            elif isinstance(operand, list):
                for item in operand:
                    if isinstance(item, str):
                        text += item
                    elif isinstance(item, bytes):
                        try:
                            text += item.decode('latin-1', errors='ignore')
                        except Exception:
                            text += item.decode('utf-8', errors='ignore')
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
        elif isinstance(operand, bytes):
            # Handle hex string - PDF content streams typically use latin-1
            decoded = operand.decode('latin-1', errors='ignore')
            if old_text in decoded:
                new_operands.append(decoded.replace(old_text, new_text).encode('latin-1'))
            else:
                new_operands.append(operand)
        elif isinstance(operand, list):
            new_list = []
            for item in operand:
                if isinstance(item, str) and old_text in item:
                    new_list.append(item.replace(old_text, new_text))
                elif isinstance(item, bytes):
                    decoded = item.decode('latin-1', errors='ignore')
                    if old_text in decoded:
                        new_list.append(decoded.replace(old_text, new_text).encode('latin-1'))
                    else:
                        new_list.append(item)
                else:
                    new_list.append(item)
            new_operands.append(new_list)
        else:
            new_operands.append(operand)
    return new_operands


def replace_text_in_stream(stream_data: bytes, op_index: int, new_operands: List[Any]) -> bytes:
    """Replace operands of a specific operator in a content stream."""
    ops = ContentStreamParser.parse(stream_data)
    if 0 <= op_index < len(ops):
        ops[op_index].operands = new_operands
    return ContentStreamSerializer.serialize_ops(ops)