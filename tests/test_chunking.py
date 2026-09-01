from pathlib import Path
from uuid import uuid4
import pytest

from src.models.document import LogicalDocument, DocumentRepresentation
from src.ingestion.extraction import extract_markdown, extract_docx
from src.ingestion.chunking import chunk_document, approximate_token_count, CHUNK_MAX_TOKENS


def test_wiki_chunking(corpus_root: Path):
    sample_file = next(corpus_root.glob("wiki/*.md"))
    doc_id = uuid4()
    rep_id = uuid4()

    doc = LogicalDocument(
        id=doc_id,
        title="Test Wiki",
        source_category="wiki",
        source_path=str(sample_file),
    )
    extraction = extract_markdown(str(sample_file), doc_id, rep_id)
    chunks = chunk_document(extraction, doc)

    assert len(chunks) > 0, "Expected at least 1 chunk for wiki document"
    for c in chunks:
        assert c.document_id == doc_id
        assert c.content, "Chunk content must not be empty"


def test_chronicle_chunking(corpus_root: Path):
    sample_file = next(corpus_root.glob("chronicles/*.docx"))
    doc_id = uuid4()
    rep_id = uuid4()

    doc = LogicalDocument(
        id=doc_id,
        title="Test Chronicle",
        source_category="chronicles",
        source_path=str(sample_file),
    )
    extraction = extract_docx(str(sample_file), doc_id, rep_id)
    chunks = chunk_document(extraction, doc)

    assert len(chunks) > 5, "Expected multiple chunks for a novel volume"
    for c in chunks:
        assert c.document_id == doc_id
        assert c.content, "Chunk content must not be empty"
