"""F10-7: Image placement editing - fold transforms into existing graphics state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Any, List, Dict
import math

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24
except ImportError:
    import fitz

from .edits import Edit, EditError, Scope
from .content_stream import ContentStreamParser, ContentStreamSerializer, PDFOperator


@dataclass
class ImagePlacement:
    """Represents an image placement in a content stream."""
    stream_obj: int          # Content stream object number
    op_index: int            # Index of the 'cm' operator
    op_index_do: int         # Index of the 'Do' operator
    matrix: Tuple[float, float, float, float, float, float]  # a, b, c, d, e, f
    image_name: str          # Image resource name (e.g., 'fzImg0')
    bbox: fitz.Rect          # Image bounding box in user space
    stream_obj: int          # Content stream object number


def decompose_matrix(matrix: Tuple[float, float, float, float, float, float]) -> Dict[str, float]:
    """Decompose a transformation matrix into components.

    Matrix: [a, b, c, d, e, f] representing:
    [a  c  e]
    [b  d  f]
    [0  0  1]

    Returns dict with: scale_x, scale_y, rotation, skew_x, skew_y, tx, ty
    """
    a, b, c, d, e, f = matrix
    
    # Scale factors
    scale_x = math.hypot(a, b)
    scale_y = math.hypot(c, d)
    
    # Rotation (in radians)
    if scale_x != 0:
        rotation = math.atan2(b, a)
    else:
        rotation = 0.0
    
    # Skew
    if scale_y != 0:
        skew_x = math.atan2(-c, d)
    else:
        skew_x = 0.0
    
    return {
        'scale_x': scale_x,
        'scale_y': scale_y,
        'rotation': rotation,
        'skew_x': skew_x,
        'skew_y': 0.0,  # Not commonly used in PDF
        'tx': e,
        'ty': f,
    }


def compose_matrix(components: Dict[str, float]) -> Tuple[float, float, float, float, float, float]:
    """Compose a transformation matrix from components."""
    # Extract components
    scale_x = components.get('scale_x', 1.0)
    scale_y = components.get('scale_y', 1.0)
    rotation = components.get('rotation', 0.0)
    skew_x = components.get('skew_x', 0.0)
    skew_y = components.get('skew_y', 0.0)
    tx = components.get('tx', 0.0)
    ty = components.get('ty', 0.0)
    
    # Build matrix: translate * rotate * skew * scale
    cos_r = math.cos(rotation)
    sin_r = math.sin(rotation)
    skew_x_rad = math.tan(skew_x) if skew_x != 0 else 0
    
    # Scale matrix
    a = scale_x * cos_r
    b = scale_x * sin_r
    c = scale_y * (-sin_r + skew_x_rad * cos_r)  # Approximate
    d = scale_y * (cos_r + skew_x_rad * sin_r)
    e = tx
    f = ty
    
    # Simplified: assume no skew for now
    a = scale_x * cos_r
    b = scale_x * sin_r
    c = -scale_y * sin_r
    d = scale_y * cos_r
    e = tx
    f = ty
    
    return (a, b, c, d, e, f)


def multiply_matrices(m1: Tuple[float, ...], m2: Tuple[float, ...]) -> Tuple[float, ...]:
    """Multiply two 2D transformation matrices (column-major, PDF convention).
    
    Both matrices are [a, b, c, d, e, f] representing:
    [a  c  e]
    [b  d  f]
    [0  0  1]
    
    Returns m1 * m2 (apply m2 then m1)
    """
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def invert_matrix(m: Tuple[float, ...]) -> Tuple[float, ...]:
    """Invert a 2D transformation matrix."""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if abs(det) < 1e-10:
        raise ValueError("Matrix is not invertible")
    
    inv_det = 1.0 / det
    return (
        d * inv_det,
        -b * inv_det,
        -c * inv_det,
        a * inv_det,
        (c * f - d * e) * inv_det,
        (b * e - a * f) * inv_det,
    )


@dataclass
class ImageTransform:
    """Image transform components for editing."""
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0  # radians
    tx: float = 0.0
    ty: float = 0.0
    absolute_matrix: Optional[Tuple[float, float, float, float, float, float]] = None
    
    def to_matrix(self) -> Tuple[float, float, float, float, float, float]:
        """Convert to PDF transformation matrix."""
        cos_r = math.cos(self.rotation)
        sin_r = math.sin(self.rotation)
        
        a = self.scale_x * math.cos(self.rotation)
        b = self.scale_x * math.sin(self.rotation)
        c = -self.scale_y * math.sin(self.rotation)
        d = self.scale_y * math.cos(self.rotation)
        e = self.tx
        f = self.ty
        return (a, b, c, d, e, f)
    
    @classmethod
    def from_matrix(cls, matrix: Tuple[float, ...]) -> 'ImageTransform':
        """Create ImageTransform from PDF matrix."""
        a, b, c, d, e, f = matrix
        scale_x = math.hypot(a, b)
        scale_y = math.hypot(c, d)
        
        if scale_x != 0:
            rotation = math.atan2(b, a)
        else:
            rotation = 0.0
        
        return cls(
            scale_x=scale_x,
            scale_y=scale_y,
            rotation=rotation,
            tx=e,
            ty=f
        )


@dataclass
class ImagePlacementSettings:
    """Settings for editing an image placement."""
    image_name: str  # Image resource name (e.g., 'fzImg0')
    page_num: int    # Page number (0-based)
    # Transform components (applied relative to current)
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0  # radians
    tx: float = 0.0
    ty: float = 0.0
    # Absolute positioning (overrides relative if set)
    absolute_matrix: Optional[Tuple[float, float, float, float, float, float]] = None
    absolute_bbox: Optional[fitz.Rect] = None


def find_image_placements(doc: fitz.Document, page_num: int) -> List[ImagePlacement]:
    """Find all image placements on a page."""
    page = doc[page_num]
    placements = []
    
    # Get image info from page
    images = page.get_image_info()
    if not images:
        return []
    
    # Parse content streams
    contents = page.get_contents()
    if not isinstance(contents, list):
        contents = [contents] if contents else []
    
    for stream_obj in contents:
        stream_data = doc.xref_stream(stream_obj)
        if not stream_data:
            continue
        ops = ContentStreamParser.parse(stream_data)
        
        # Find cm/Do pairs
        for i, op in enumerate(ops):
            if op.operator == "cm":
                # Next non-whitespace operator should be Do
                for j in range(i + 1, min(i + 5, len(ops))):
                    if ops[j].operator == "Do" and ops[j].operands:
                        img_name = str(ops[j].operands[0])
                        matrix = ops[i].operands
                        if len(matrix) >= 6:
                            # Get image bbox from page
                            bbox = fitz.Rect(0, 0, 0, 0)
                            # Try to get bbox from image info
                            images = page.get_image_info()
                            for img_info in page.get_image_info():
                                if img_info.get('bbox'):
                                    bbox = fitz.Rect(*img_info['bbox'])
                                    break
                            
                            placements.append(ImagePlacement(
                                stream_obj=0,  # Will be set properly if needed
                                op_index=i,
                                op_index_do=i+1,
                                matrix=tuple(matrix[:6]),
                                image_name=img_name,
                                bbox=bbox,
                            ))
                        break
    return placements


def apply_image_transform(
    doc: fitz.Document,
    page_num: int,
    image_name: str,
    transform: ImageTransform,
    mode: str = "relative"  # "relative" or "absolute"
) -> bool:
    """Apply a transform to an image placement by folding into existing cm operator."""
    page = doc[page_num]
    contents = page.get_contents()
    if not isinstance(contents, list):
        contents = [contents] if contents else []
    
    for stream_obj in contents:
        stream_data = doc.xref_stream(stream_obj)
        if not stream_data:
            continue
        
        ops = ContentStreamParser.parse(stream_data)
        modified = False
        
        # Find the image's cm/Do pair
        for i, op in enumerate(ops):
            if op.operator == "cm":
                # Check if next few ops include Do with our image
                for j in range(i + 1, min(i + 5, len(ops))):
                    if ops[j].operator == "Do" and ops[j].operands:
                        img_name = str(ops[j].operands[0])
                        if img_name == image_name:
                            # Found the image's cm operator
                            current_matrix = tuple(op.operands[:6])
                            
                            if transform.absolute_matrix:
                                new_matrix = transform.absolute_matrix
                            else:
                                # Compose: new = transform * current (relative)
                                current_transform = ImageTransform.from_matrix(current_matrix)
                                new_transform = ImageTransform(
                                    scale_x=current_transform.scale_x * transform.scale_x,
                                    scale_y=current_transform.scale_y * transform.scale_y,
                                    rotation=current_transform.rotation + transform.rotation,
                                    tx=current_transform.tx + transform.tx,
                                    ty=current_transform.ty + transform.ty
                                )
                                new_matrix = new_transform.to_matrix()
                            
                            # Update the cm operator's operands
                            ops[i].operands = list(new_matrix[:6])
                            modified = True
                            break
        
        if modified:
            new_stream = ContentStreamSerializer.serialize_ops(ops)
            page.parent.update_stream(stream_obj, new_stream)
            return True
    
    return False


@dataclass(frozen=True)
class EditImagePlacement(Edit):
    """Edit an image placement by modifying its transform."""
    
    page_num: int
    image_name: str
    transform: ImageTransform
    mode: str = "relative"  # "relative" or "absolute"
    
    scope = Scope.CONTENT
    requires_assembly = True
    
    @property
    def label(self) -> str:
        return f"Edit image placement: {self.image_name}"
    
    def page_order(self, before_count: int) -> Tuple[int, ...]:
        return tuple(range(before_count))
    
    def apply(self, doc: fitz.Document) -> None:
        if not apply_image_transform(doc, self.page_num, self.image_name, self.transform, self.mode):
            raise EditError(f"Image '{self.image_name}' not found on page {self.page_num + 1}")