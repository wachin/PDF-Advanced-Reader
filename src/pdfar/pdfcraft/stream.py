"""Content-stream tokenizer, parser and byte-exact splicer.

This is an independent Python implementation of the parts of
``pdfcraft-content`` that the text engine needs (see
``external/pdfcraft/crates/content/src/lib.rs``).  The important property is
that every operator records the byte span it occupies in the *joined* content
stream, so a rewrite can replace one operator and leave every other byte
exactly as the producer wrote it.

Adapted concepts (not code) from PdfCraft, MIT OR Apache-2.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Sequence, Tuple


# ---------------------------------------------------------------------------
# Operand types
# ---------------------------------------------------------------------------


class Name(str):
    """A PDF name object (written ``/Foo``)."""

    __slots__ = ()


@dataclass(frozen=True)
class Ref:
    """An indirect reference (``12 0 R``)."""

    num: int
    gen: int = 0


@dataclass
class PdfString:
    """A PDF string: decoded bytes plus the exact source bytes (without delimiters)."""

    data: bytes
    raw: bytes

    def text(self, encoding: str = "latin-1") -> str:
        return self.data.decode(encoding, errors="replace")


@dataclass
class Op:
    """One operator with its operands and its byte span in the joined stream."""

    op: str
    operands: List[Any] = field(default_factory=list)
    # Start of the first operand (or of the keyword when there are none).
    start: int = 0
    # Start/end of the keyword itself.
    kw_start: int = 0
    end: int = 0
    # BI ... ID ... EI is opaque binary; it is kept but never interpreted.
    inline_image: bool = False

    def nums(self) -> List[float]:
        out = []
        for value in self.operands:
            if isinstance(value, bool):
                return []
            if isinstance(value, (int, float)):
                out.append(float(value))
            else:
                return []
        return out

    def _operand(self, index: int):
        """The operand at *index*, or ``None``; a negative index counts from the end."""
        if -len(self.operands) <= index < len(self.operands):
            return self.operands[index]
        return None

    def name(self, index: int = 0) -> str | None:
        value = self._operand(index)
        return str(value) if isinstance(value, Name) else None

    def string(self, index: int = 0) -> PdfString | None:
        value = self._operand(index)
        return value if isinstance(value, PdfString) else None

    def array(self, index: int = 0) -> list | None:
        value = self._operand(index)
        return value if isinstance(value, list) else None


# ---------------------------------------------------------------------------
# Lexical helpers
# ---------------------------------------------------------------------------

WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"
_WS_SET = set(WHITESPACE)
_DELIM_SET = set(DELIMITERS)
_DIGITS = set(b"0123456789")


def skip_whitespace(data: bytes, i: int) -> int:
    n = len(data)
    while i < n:
        c = data[i]
        if c in _WS_SET:
            i += 1
        elif c == 0x25:  # '%' comment runs to end of line
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
        else:
            break
    return i


def _is_number_start(c: int) -> bool:
    return c in _DIGITS or c in b"+-."


def _parse_number(data: bytes, i: int) -> Tuple[float | int, int]:
    n = len(data)
    j = i
    if j < n and data[j] in b"+-":
        j += 1
    seen_dot = False
    while j < n:
        c = data[j]
        if c in _DIGITS:
            j += 1
        elif c == 0x2E and not seen_dot:  # '.'
            seen_dot = True
            j += 1
        else:
            break
    token = data[i:j]
    if not token or token in (b"+", b"-", b".", b"+.", b"-."):
        return 0, max(j, i + 1)
    try:
        if seen_dot:
            return float(token), j
        return int(token), j
    except ValueError:
        return 0, j


def _decode_literal(raw: bytes) -> bytes:
    """Decode the body of a literal string (between the parentheses)."""
    out = bytearray()
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c != 0x5C:  # backslash
            out.append(c)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        e = raw[i]
        if e == 0x6E:  # n
            out.append(0x0A)
            i += 1
        elif e == 0x72:  # r
            out.append(0x0D)
            i += 1
        elif e == 0x74:  # t
            out.append(0x09)
            i += 1
        elif e == 0x62:  # b
            out.append(0x08)
            i += 1
        elif e == 0x66:  # f
            out.append(0x0C)
            i += 1
        elif e in b"()\\":
            out.append(e)
            i += 1
        elif e in b"\r\n":
            # Line continuation: backslash + EOL produces nothing.
            if e == 0x0D and i + 1 < n and raw[i + 1] == 0x0A:
                i += 1
            i += 1
        elif 0x30 <= e <= 0x37:  # octal, up to three digits
            value = 0
            count = 0
            while i < n and count < 3 and 0x30 <= raw[i] <= 0x37:
                value = value * 8 + (raw[i] - 0x30)
                i += 1
                count += 1
            out.append(value & 0xFF)
        else:
            out.append(e)
            i += 1
    return bytes(out)


def _parse_literal(data: bytes, i: int) -> Tuple[PdfString, int]:
    n = len(data)
    assert data[i] == 0x28  # '('
    depth = 1
    j = i + 1
    body_start = j
    while j < n:
        c = data[j]
        if c == 0x5C:  # backslash: skip the escaped byte
            j += 2
            continue
        if c == 0x28:
            depth += 1
        elif c == 0x29:
            depth -= 1
            if depth == 0:
                raw = data[body_start:j]
                return PdfString(_decode_literal(raw), raw), j + 1
        j += 1
    raw = data[body_start:n]
    return PdfString(_decode_literal(raw), raw), n


def _parse_hex_string(data: bytes, i: int) -> Tuple[PdfString, int]:
    n = len(data)
    assert data[i] == 0x3C  # '<'
    j = data.find(b">", i + 1)
    if j < 0:
        j = n
    raw = bytes(c for c in data[i + 1 : j] if c not in _WS_SET)
    if len(raw) % 2:
        raw += b"0"
    try:
        value = bytes.fromhex(raw.decode("ascii"))
    except ValueError:
        value = b""
    return PdfString(value, raw), min(j + 1, n)


def _parse_name(data: bytes, i: int) -> Tuple[Name, int]:
    n = len(data)
    assert data[i] == 0x2F  # '/'
    j = i + 1
    out = bytearray()
    while j < n and data[j] not in _WS_SET and data[j] not in _DELIM_SET:
        if data[j] == 0x23 and j + 2 < n:  # '#xx'
            try:
                out.append(int(data[j + 1 : j + 3], 16))
                j += 3
                continue
            except ValueError:
                pass
        out.append(data[j])
        j += 1
    return Name(out.decode("latin-1")), j


def _parse_array(data: bytes, i: int) -> Tuple[list, int]:
    n = len(data)
    assert data[i] == 0x5B  # '['
    out: list = []
    i += 1
    while True:
        i = skip_whitespace(data, i)
        if i >= n:
            break
        if data[i] == 0x5D:  # ']'
            i += 1
            break
        value, i = _parse_value(data, i)
        if value is _MISSING:
            i += 1
            continue
        out.append(value)
    return out, i


def _parse_dict(data: bytes, i: int) -> Tuple[dict, int]:
    n = len(data)
    assert data[i : i + 2] == b"<<"
    out: dict = {}
    i += 2
    while True:
        i = skip_whitespace(data, i)
        if i >= n:
            break
        if data[i : i + 2] == b">>":
            i += 2
            break
        key, i = _parse_value(data, i)
        if not isinstance(key, Name):
            i += 1
            continue
        i = skip_whitespace(data, i)
        value, i = _parse_value(data, i)
        if value is _MISSING:
            break
        out[str(key)] = value
    return out, i


class _Missing:
    pass


_MISSING = _Missing()


def _parse_value(data: bytes, i: int) -> Tuple[Any, int]:
    """Parse one object.  Returns ``(_MISSING, i)`` when nothing usable is there."""
    n = len(data)
    i = skip_whitespace(data, i)
    if i >= n:
        return _MISSING, i
    c = data[i]
    if c == 0x28:  # '('
        return _parse_literal(data, i)
    if c == 0x3C:  # '<'
        if data[i : i + 2] == b"<<":
            return _parse_dict(data, i)
        return _parse_hex_string(data, i)
    if c == 0x5B:  # '['
        return _parse_array(data, i)
    if c == 0x2F:  # '/'
        return _parse_name(data, i)
    if _is_number_start(c):
        value, j = _parse_number(data, i)
        # Indirect reference: "12 0 R"
        if isinstance(value, int):
            k = skip_whitespace(data, j)
            if k < n and data[k] in _DIGITS:
                gen, k2 = _parse_number(data, k)
                k3 = skip_whitespace(data, k2)
                if (
                    isinstance(gen, int)
                    and k3 < n
                    and data[k3 : k3 + 1] == b"R"
                    and (k3 + 1 >= n or data[k3 + 1] in _WS_SET or data[k3 + 1] in _DELIM_SET)
                ):
                    return Ref(value, gen), k3 + 1
        return value, j
    # Keywords true / false / null
    for word, value in ((b"true", True), (b"false", False), (b"null", None)):
        if data.startswith(word, i):
            j = i + len(word)
            if j >= n or data[j] in _WS_SET or data[j] in _DELIM_SET:
                return value, j
    return _MISSING, i


def _inline_image_end(data: bytes, i: int) -> int:
    """Return the index just past ``EI`` for an inline image starting at ``i``."""
    n = len(data)
    id_pos = data.find(b"ID", i)
    if id_pos < 0:
        return n
    j = id_pos + 2
    if j < n and data[j] in _WS_SET:
        j += 1
    while True:
        j = data.find(b"EI", j)
        if j < 0:
            return n
        before_ok = j > 0 and data[j - 1] in _WS_SET
        after_ok = j + 2 >= n or data[j + 2] in _WS_SET or data[j + 2] in _DELIM_SET
        if before_ok and after_ok:
            return j + 2
        j += 2


# ---------------------------------------------------------------------------
# Operator parsing
# ---------------------------------------------------------------------------


def parse_ops(data: bytes) -> List[Op]:
    """Parse a content stream into operators carrying their byte spans."""
    n = len(data)
    ops: List[Op] = []
    operands: List[Any] = []
    first_operand = -1
    i = 0
    while True:
        i = skip_whitespace(data, i)
        if i >= n:
            break
        c = data[i]
        if c in b"([</" or _is_number_start(c):
            if first_operand < 0:
                first_operand = i
            value, j = _parse_value(data, i)
            if value is _MISSING or j <= i:
                i += 1
                continue
            operands.append(value)
            i = j
            continue
        # Keyword or literal keyword value.
        j = i
        while j < n and data[j] not in _WS_SET and data[j] not in _DELIM_SET:
            j += 1
        if j == i:
            i += 1
            continue
        word = data[i:j]
        if word in (b"true", b"false", b"null"):
            if first_operand < 0:
                first_operand = i
            operands.append({b"true": True, b"false": False, b"null": None}[word])
            i = j
            continue
        keyword = word.decode("latin-1")
        if keyword == "BI":
            end = _inline_image_end(data, j)
            start = first_operand if first_operand >= 0 else i
            ops.append(Op("BI", [], start, i, end, inline_image=True))
            operands = []
            first_operand = -1
            i = end
            continue
        start = first_operand if first_operand >= 0 else i
        ops.append(Op(keyword, operands, start, i, j))
        operands = []
        first_operand = -1
        i = j
    return ops


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def fmt_number(value: float | int) -> bytes:
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if value == int(value) and abs(value) < 1e15:
        return str(int(value)).encode("ascii")
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    if not text or text == "-0":
        return b"0"
    return text.encode("ascii")


def _escape_name(name: str) -> bytes:
    out = bytearray()
    for byte in name.encode("latin-1", errors="replace"):
        if byte in _WS_SET or byte in _DELIM_SET or byte < 0x21 or byte > 0x7E:
            out.extend(f"#{byte:02X}".encode("ascii"))
        else:
            out.append(byte)
    return bytes(out)


def _escape_literal(data: bytes) -> bytes:
    out = bytearray()
    for byte in data:
        if byte in (0x28, 0x29, 0x5C):
            out.append(0x5C)
            out.append(byte)
        elif byte == 0x0D:
            out.extend(b"\\r")
        elif byte == 0x0A:
            out.extend(b"\\n")
        else:
            out.append(byte)
    return bytes(out)


def serialize_operand(value: Any) -> bytes:
    if isinstance(value, Name):
        return b"/" + _escape_name(str(value))
    if isinstance(value, PdfString):
        # Only synthesized strings reach the serializer; untouched operators are
        # spliced from the source bytes, so a producer's hex/literal choice and
        # escapes never have to be reproduced here.
        return b"(" + _escape_literal(value.data) + b")"
    if isinstance(value, Ref):
        return f"{value.num} {value.gen} R".encode("ascii")
    if isinstance(value, bool):
        return b"true" if value else b"false"
    if value is None:
        return b"null"
    if isinstance(value, (int, float)):
        return fmt_number(value)
    if isinstance(value, (list, tuple)):
        return b"[" + b" ".join(serialize_operand(v) for v in value) + b"]"
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(b"/" + _escape_name(str(key)) + b" " + serialize_operand(item))
        return b"<<" + b" ".join(parts) + b">>"
    return str(value).encode("latin-1", errors="replace")


def serialize_op(op: Op) -> bytes:
    parts = [serialize_operand(v) for v in op.operands]
    parts.append(op.op.encode("latin-1"))
    return b" ".join(parts)


def serialize_ops(ops: Sequence[Op]) -> bytes:
    return b"\n".join(serialize_op(op) for op in ops)


# ---------------------------------------------------------------------------
# Byte-exact splicing
# ---------------------------------------------------------------------------


def apply_chunks(data: bytes, chunks: Sequence[Tuple[int, int, bytes]]) -> bytes:
    """Replace ``[start, end)`` ranges of ``data``; ranges must not overlap."""
    out = []
    cursor = 0
    for start, end, replacement in sorted(chunks, key=lambda c: (c[0], c[1])):
        if start < cursor:
            raise ValueError("overlapping splices")
        out.append(data[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(data[cursor:])
    return b"".join(out)


def splice(data: bytes, ops: Sequence[Op], edits: dict[int, Tuple[bytes, bool]]) -> bytes:
    """Apply per-operator edits to ``data``.

    ``edits[i] = (insert, keep)``: insert ``insert`` before operator ``i`` and
    keep (``True``) or remove (``False``) its own bytes.  Operators without an
    entry are untouched, byte for byte.
    """
    chunks: List[Tuple[int, int, bytes]] = []
    for i, op in enumerate(ops):
        insert, keep = edits.get(i, (b"", True))
        if not insert and keep:
            continue
        body = bytearray()
        if insert:
            if op.start > 0 and data[op.start - 1] not in _WS_SET:
                body.append(0x0A)
            body.extend(insert)
        end = op.start if keep else op.end
        chunks.append((op.start, end, bytes(body)))
    return apply_chunks(data, chunks)


# ---------------------------------------------------------------------------
# Joined pieces (a page's /Contents is one stream in pieces, ISO 32000-2 §7.8.2)
# ---------------------------------------------------------------------------


@dataclass
class Pieces:
    """A page's content streams joined, with the boundary of each piece."""

    data: bytes
    # (start, end) of each piece in ``data``.
    bounds: List[Tuple[int, int]]

    @classmethod
    def join(cls, streams: Sequence[bytes]) -> "Pieces":
        data = b"\n".join(streams)
        bounds = []
        at = 0
        for stream in streams:
            bounds.append((at, at + len(stream)))
            at += len(stream) + 1
        return cls(data, bounds)

    def piece_of(self, offset: int) -> int:
        for index, (start, end) in enumerate(self.bounds):
            if start <= offset < end:
                return index
        return max(0, len(self.bounds) - 1)

    def split(self, new_data: bytes) -> List[bytes]:
        """Split rewritten joined data back into pieces.

        Edits never cross a piece boundary in this engine (an operator that
        spans two pieces is refused), so the original boundaries still hold.
        """
        out = []
        for start, end in self.bounds:
            out.append(new_data[start:end])
        # A splice can change lengths; recompute from the tail instead.
        if len(new_data) != len(self.data):
            return _split_by_offsets(new_data, self.bounds)
        return out


def _split_by_offsets(data: bytes, bounds: Sequence[Tuple[int, int]]) -> List[bytes]:
    """Best-effort split when lengths changed: keep the inter-piece newlines."""
    out = []
    cursor = 0
    for index, (start, _end) in enumerate(bounds):
        if index + 1 < len(bounds):
            next_start = bounds[index + 1][0]
            # The separator byte was inserted by join().
            segment_end = start + (next_start - start) - 1
        else:
            segment_end = len(data)
        segment_end = max(cursor, min(segment_end, len(data)))
        out.append(data[cursor:segment_end])
        cursor = segment_end + 1
    return out


def local_edits(
    pieces: Pieces, joined_edits: Sequence[Tuple[int, int, bytes]]
) -> List[List[Tuple[int, int, bytes]]]:
    """Convert joined-coordinate edits into per-piece local edits.

    An edit that crosses a piece boundary puts its replacement in the first
    piece it touches and removes the overlapped bytes from the others.
    """
    per_piece: List[List[Tuple[int, int, bytes]]] = [[] for _ in pieces.bounds]
    for start, end, replacement in joined_edits:
        placed = False
        for index, (p_start, p_end) in enumerate(pieces.bounds):
            lo = max(start, p_start)
            hi = min(end, p_end)
            if lo >= hi:
                continue
            local = (lo - p_start, hi - p_start, replacement if not placed else b"")
            per_piece[index].append(local)
            placed = True
    return per_piece
