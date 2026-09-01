from pathlib import Path
from typing import Optional, List, Dict, Any
from uuid import UUID
import re

import pymupdf as fitz
from docx import Document as DocxDocument

from src.models.document import (
    ExtractionResult,
    PageContent,
    SectionContent,
)


def extract_pdf(file_path: str, doc_id: UUID, rep_id: UUID) -> ExtractionResult:
    """Extract page-by-page text from standard searchable PDFs."""
    doc = fitz.open(file_path)
    pages: List[PageContent] = []
    full_text_parts: List[str] = []

    for page_idx in range(len(doc)):
        page = doc[page_idx]
        text = page.get_text("text").strip()
        pages.append(PageContent(page_number=page_idx + 1, text=text))
        if text:
            full_text_parts.append(text)

    raw_text = "\n\n".join(full_text_parts)
    page_count = len(doc)
    doc.close()

    return ExtractionResult(
        document_id=doc_id,
        representation_id=rep_id,
        file_path=file_path,
        format="pdf",
        raw_text=raw_text,
        pages=pages,
        metadata={"page_count": page_count},
        extraction_method="pymupdf",
    )


def extract_docx(file_path: str, doc_id: UUID, rep_id: UUID) -> ExtractionResult:
    """Extract paragraphs, headings, and tables from DOCX files."""
    doc = DocxDocument(file_path)
    sections: List[SectionContent] = []
    current_title = "Introduction"
    current_level = 1
    current_paragraphs: List[str] = []
    position = 0

    all_text: List[str] = []

    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue

        all_text.append(text)

        # Detect heading styles
        if p.style.name.startswith("Heading"):
            # Save previous section if it has content
            if current_paragraphs:
                sections.append(
                    SectionContent(
                        title=current_title,
                        level=current_level,
                        content="\n\n".join(current_paragraphs),
                        position=position,
                    )
                )
                position += 1
                current_paragraphs = []

            current_title = text
            try:
                current_level = int(p.style.name.replace("Heading", "").strip())
            except ValueError:
                current_level = 1
        else:
            current_paragraphs.append(text)

    # Save final section
    if current_paragraphs:
        sections.append(
            SectionContent(
                title=current_title,
                level=current_level,
                content="\n\n".join(current_paragraphs),
                position=position,
            )
        )

    # Extract tables
    tables: List[Dict[str, Any]] = []
    for t_idx, table in enumerate(doc.tables):
        table_rows = []
        for row in table.rows:
            row_data = [cell.text.strip() for cell in row.cells]
            table_rows.append(row_data)
        if table_rows:
            tables.append({"table_index": t_idx, "rows": table_rows})

    raw_text = "\n\n".join(all_text)

    return ExtractionResult(
        document_id=doc_id,
        representation_id=rep_id,
        file_path=file_path,
        format="docx",
        raw_text=raw_text,
        sections=sections,
        tables=tables,
        metadata={"paragraph_count": len(all_text), "table_count": len(tables)},
        extraction_method="python-docx",
    )


def extract_markdown(file_path: str, doc_id: UUID, rep_id: UUID) -> ExtractionResult:
    """Parse Markdown wiki articles, separating sections, infoboxes, and image links."""
    content = Path(file_path).read_text(encoding="utf-8")
    lines = content.splitlines()

    sections: List[SectionContent] = []
    image_refs: List[str] = []
    current_title = Path(file_path).stem.replace("_", " ").title()
    current_level = 1
    current_lines: List[str] = []
    position = 0

    for line in lines:
        stripped = line.strip()

        # Check for image reference e.g. ![Alt](images/path.png)
        img_match = re.match(r"!\[(.*?)\]\((.*?)\)", stripped)
        if img_match:
            image_refs.append(img_match.group(2))

        # Check for markdown heading (e.g. ## Section)
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading_match:
            if current_lines:
                sec_text = "\n".join(current_lines).strip()
                if sec_text:
                    sections.append(
                        SectionContent(
                            title=current_title,
                            level=current_level,
                            content=sec_text,
                            position=position,
                        )
                    )
                    position += 1
                current_lines = []

            current_level = len(heading_match.group(1))
            current_title = heading_match.group(2).strip()
        else:
            current_lines.append(line)

    if current_lines:
        sec_text = "\n".join(current_lines).strip()
        if sec_text:
            sections.append(
                SectionContent(
                    title=current_title,
                    level=current_level,
                    content=sec_text,
                    position=position,
                )
            )

    return ExtractionResult(
        document_id=doc_id,
        representation_id=rep_id,
        file_path=file_path,
        format="md",
        raw_text=content,
        sections=sections,
        metadata={"image_references": image_refs},
        extraction_method="markdown",
    )


def extract_text(file_path: str, doc_id: UUID, rep_id: UUID) -> ExtractionResult:
    """Read plain text ephemera or transcripts."""
    raw_text = Path(file_path).read_text(encoding="utf-8", errors="replace").strip()
    return ExtractionResult(
        document_id=doc_id,
        representation_id=rep_id,
        file_path=file_path,
        format="txt",
        raw_text=raw_text,
        sections=[
            SectionContent(
                title=Path(file_path).stem.replace("_", " ").title(),
                level=1,
                content=raw_text,
                position=0,
            )
        ],
        extraction_method="text",
    )


def extract_document_representation(
    file_path: str, format_type: str, doc_id: UUID, rep_id: UUID
) -> ExtractionResult:
    """Dispatch file extraction based on extension and format."""
    ext = format_type.lower().lstrip(".")
    if ext == "pdf":
        return extract_pdf(file_path, doc_id, rep_id)
    elif ext == "docx":
        return extract_docx(file_path, doc_id, rep_id)
    elif ext == "md":
        return extract_markdown(file_path, doc_id, rep_id)
    elif ext == "txt":
        return extract_text(file_path, doc_id, rep_id)
    else:
        # Fallback to plain text reader
        return extract_text(file_path, doc_id, rep_id)
