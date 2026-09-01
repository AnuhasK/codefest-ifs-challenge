import pytest
from pathlib import Path
from src.ingestion.discovery import discover_corpus, bundle_documents


def test_discover_corpus_finds_files(corpus_root: Path):
    discovered = discover_corpus(corpus_root)
    assert len(discovered) > 200, f"Expected >200 files, found {len(discovered)}"

    categories = {f.category for f in discovered}
    assert "chronicles" in categories
    assert "wiki" in categories
    assert "codex" in categories
    assert "ephemera" in categories


def test_bundle_documents(corpus_root: Path):
    discovered = discover_corpus(corpus_root)
    docs = bundle_documents(discovered)

    assert len(docs) > 150, f"Expected >150 logical documents, found {len(docs)}"

    # Verify chronicle format variants bundling (PDF + DOCX)
    chronicle_docs = [d for d in docs if d.source_category == "chronicles"]
    assert len(chronicle_docs) == 4, f"Expected 4 chronicle logical docs, got {len(chronicle_docs)}"
    for c in chronicle_docs:
        assert len(c.representations) == 2, f"Chronicle {c.title} should have 2 representations (PDF + DOCX)"


def test_scanned_pdf_detection(corpus_root: Path):
    discovered = discover_corpus(corpus_root)
    scans = [f for f in discovered if f.is_scan]
    assert len(scans) > 0, "Expected scanned PDFs to be identified"
