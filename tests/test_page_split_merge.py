"""F10-4: Insert document pages, merge PDFs, and split PDF."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf as fitz
import pytest
from conftest import build_sample_pdf, make_session

from pdfar.edits import EditError, Scope
from pdfar.pages import (
    InsertDocumentPages, merge_documents, split_document,
)


@pytest.fixture
def doc_a(tmp_path):
    path = str(tmp_path / "doc_a.pdf")
    build_sample_pdf(path, pages=4)
    return path


@pytest.fixture
def doc_b(tmp_path):
    path = str(tmp_path / "doc_b.pdf")
    doc = fitz.open()
    for i in range(3):
        page = doc.new_page(width=200, height=100)
        page.insert_text(fitz.Point(10, 30), f"DocB Page {i + 1}", fontsize=12)
    doc.save(path)
    doc.close()
    return path


def test_insert_document_pages_into_session(doc_a, doc_b):
    """Insert doc_b (3 pages) into doc_a (4 pages) at gap 2."""
    session = make_session(doc_a)
    try:
        assert session.doc.page_count == 4
        edit = InsertDocumentPages(source_path=doc_b, at=2)
        version, scope = session.apply(edit)

        assert scope is Scope.STRUCTURE
        assert session.doc.page_count == 7
        lines = [session.doc[i].get_text().strip().splitlines()[0] for i in range(7)]
        assert lines == [
            "Hello PDFAR world",
            "Page 2 content",
            "DocB Page 1",
            "DocB Page 2",
            "DocB Page 3",
            "Page 3 content",
            "Page 4 content",
        ]

        # Verify undo
        session.undo()
        assert session.doc.page_count == 4
        undo_lines = [session.doc[i].get_text().strip().splitlines()[0] for i in range(4)]
        assert undo_lines == ["Hello PDFAR world", "Page 2 content", "Page 3 content", "Page 4 content"]
    finally:
        session.close()


def test_insert_document_subset_of_pages(doc_a, doc_b):
    """Insert only page 1 of doc_b into doc_a at gap 0 (at the front)."""
    session = make_session(doc_a)
    try:
        edit = InsertDocumentPages(source_path=doc_b, at=0, pages=(1,))
        session.apply(edit)
        assert session.doc.page_count == 5
        assert "DocB Page 2" in session.doc[0].get_text()
        assert "Hello PDFAR world" in session.doc[1].get_text()
    finally:
        session.close()


def test_insert_document_refusals(doc_a, tmp_path):
    """Invalid path or out-of-range gap raises EditError."""
    session = make_session(doc_a)
    try:
        missing_path = str(tmp_path / "nonexistent.pdf")
        with pytest.raises(EditError, match="Could not open source PDF"):
            session.apply(InsertDocumentPages(source_path=missing_path, at=0))

        with pytest.raises(EditError, match="out of range|gaps run from"):
            session.apply(InsertDocumentPages(source_path=doc_a, at=100))
    finally:
        session.close()


def test_merge_documents_combines_files(doc_a, doc_b, tmp_path):
    """Merge doc_a (4 pages) and doc_b (3 pages) into target PDF."""
    target_path = str(tmp_path / "merged.pdf")
    merge_documents([doc_a, doc_b], target_path)

    assert os.path.exists(target_path)
    merged = fitz.open(target_path)
    try:
        assert merged.page_count == 7
        assert "Hello PDFAR world" in merged[0].get_text()
        assert "DocB Page 1" in merged[4].get_text()
    finally:
        merged.close()


def test_merge_documents_refusals(doc_a, tmp_path):
    """Empty source list or missing file raises EditError."""
    target_path = str(tmp_path / "merged_fail.pdf")
    with pytest.raises(EditError, match="No source documents"):
        merge_documents([], target_path)

    missing = str(tmp_path / "missing.pdf")
    with pytest.raises(EditError, match="does not exist"):
        merge_documents([doc_a, missing], target_path)


def test_split_document_by_chunk_size(doc_a, tmp_path):
    """Split doc_a (4 pages) into single pages and 2-page chunks."""
    doc = fitz.open(doc_a)
    output_dir = str(tmp_path / "split_output")
    os.makedirs(output_dir, exist_ok=True)
    try:
        # Single page chunks
        created_1 = split_document(doc, output_dir, chunk_size=1)
        assert len(created_1) == 4
        for p in created_1:
            assert os.path.exists(p)
            with fitz.open(p) as f:
                assert f.page_count == 1

        # 2-page chunks
        output_dir_2 = str(tmp_path / "split_output_2")
        os.makedirs(output_dir_2, exist_ok=True)
        created_2 = split_document(doc, output_dir_2, chunk_size=2)
        assert len(created_2) == 2
        for p in created_2:
            assert os.path.exists(p)
            with fitz.open(p) as f:
                assert f.page_count == 2
    finally:
        doc.close()


def test_split_document_refusals(doc_a, tmp_path):
    """Chunk size < 1 raises EditError."""
    doc = fitz.open(doc_a)
    try:
        with pytest.raises(EditError, match="Chunk size must be at least 1"):
            split_document(doc, str(tmp_path), chunk_size=0)
    finally:
        doc.close()
