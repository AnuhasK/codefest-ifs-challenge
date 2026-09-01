from pathlib import Path
from uuid import uuid4
import pytest

from src.ingestion.discovery import discover_corpus
from src.ingestion.extraction import (
    extract_pdf,
    extract_docx,
    extract_markdown,
    extract_text,
)


def test_markdown_extraction(corpus_root: Path):
    wiki_files = list(corpus_root.glob("wiki/*.md"))
    assert len(wiki_files) > 0, "No markdown wiki files found"

    sample = wiki_files[0]
    result = extract_markdown(str(sample), uuid4(), uuid4())

    assert result.raw_text, "Markdown text should not be empty"
    assert len(result.sections) > 0, "Markdown sections should be parsed"
    assert result.format == "md"


def test_docx_extraction(corpus_root: Path):
    docx_files = list(corpus_root.glob("chronicles/*.docx"))
    assert len(docx_files) > 0, "No chronicle docx files found"

    sample = docx_files[0]
    result = extract_docx(str(sample), uuid4(), uuid4())

    assert result.raw_text, "DOCX text should not be empty"
    assert len(result.sections) > 0, "DOCX headings/sections should be detected"
    assert result.format == "docx"


def test_pdf_extraction(corpus_root: Path):
    pdf_files = list(corpus_root.glob("chronicles/*.pdf"))
    assert len(pdf_files) > 0, "No chronicle pdf files found"

    sample = pdf_files[0]
    result = extract_pdf(str(sample), uuid4(), uuid4())

    assert result.raw_text, "PDF text should not be empty"
    assert len(result.pages) > 0, "PDF pages should be counted"
    assert result.format == "pdf"


def test_text_extraction(corpus_root: Path):
    txt_files = list(corpus_root.glob("ephemera/*.txt"))
    if txt_files:
        sample = txt_files[0]
        result = extract_text(str(sample), uuid4(), uuid4())
        assert result.raw_text, "TXT content should not be empty"
        assert result.format == "txt"
