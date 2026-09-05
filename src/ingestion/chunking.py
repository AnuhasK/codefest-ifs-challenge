from typing import List, Optional
from uuid import UUID, uuid4
import re

from src.models.document import (
    Chunk,
    ExtractionResult,
    Asset,
    LogicalDocument,
)
from src.config import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_MIN_TOKENS,
)


def approximate_token_count(text: str) -> int:
    """Rough estimation of tokens based on whitespace words (avg 1.3 tokens/word)."""
    words = len(text.split())
    return max(1, int(words * 1.3))


def chunk_paragraphs_with_overlap(
    paragraphs: List[str],
    max_tokens: int = CHUNK_MAX_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
) -> List[str]:
    """Group paragraphs into chunks respecting token limits with paragraph-level overlap."""
    if not paragraphs:
        return []

    chunks: List[str] = []
    current_batch: List[str] = []
    current_tokens = 0

    for p in paragraphs:
        p_clean = p.strip()
        if not p_clean:
            continue
        p_tokens = approximate_token_count(p_clean)

        if current_tokens + p_tokens > max_tokens and current_batch:
            # Emit current chunk
            chunks.append("\n\n".join(current_batch))

            # Calculate overlap from end of current_batch
            overlap_batch: List[str] = []
            overlap_count = 0
            for prev_p in reversed(current_batch):
                prev_tokens = approximate_token_count(prev_p)
                if overlap_count + prev_tokens <= overlap_tokens:
                    overlap_batch.insert(0, prev_p)
                    overlap_count += prev_tokens
                else:
                    break

            current_batch = overlap_batch + [p_clean]
            current_tokens = overlap_count + p_tokens
        else:
            current_batch.append(p_clean)
            current_tokens += p_tokens

    if current_batch:
        chunks.append("\n\n".join(current_batch))

    return chunks


def chunk_document(
    extraction: ExtractionResult,
    doc: LogicalDocument,
) -> List[Chunk]:
    """
    Format-aware chunking dispatcher.
    Extracts chunks according to category (wiki, chronicles, codex, ephemera).
    """
    category = doc.source_category.lower()
    chunks: List[Chunk] = []
    position = 0

    # 1. Wiki Articles: Split on semantic sections
    if category == "wiki" and extraction.sections:
        for sec in extraction.sections:
            sec_text = sec.content.strip()
            if not sec_text:
                continue

            # If section is excessively long, split paragraphs with overlap
            tokens = approximate_token_count(sec_text)
            if tokens > CHUNK_MAX_TOKENS:
                paras = sec_text.split("\n\n")
                sub_chunks = chunk_paragraphs_with_overlap(paras)
                for sub in sub_chunks:
                    chunks.append(
                        Chunk(
                            id=uuid4(),
                            document_id=doc.id,
                            representation_id=extraction.representation_id,
                            content=sub,
                            section_title=sec.title,
                            position=position,
                            token_count=approximate_token_count(sub),
                            metadata={"category": category, "section_level": sec.level},
                        )
                    )
                    position += 1
            else:
                chunks.append(
                    Chunk(
                        id=uuid4(),
                        document_id=doc.id,
                        representation_id=extraction.representation_id,
                        content=sec_text,
                        section_title=sec.title,
                        position=position,
                        token_count=tokens,
                        metadata={"category": category, "section_level": sec.level},
                    )
                )
                position += 1

    # 2. Chronicles / Novels: Chapter/Section paragraph grouping with overlap
    elif category == "chronicles":
        if extraction.sections:
            for sec in extraction.sections:
                paras = sec.content.split("\n\n")
                sub_chunks = chunk_paragraphs_with_overlap(paras)
                for sub in sub_chunks:
                    chunks.append(
                        Chunk(
                            id=uuid4(),
                            document_id=doc.id,
                            representation_id=extraction.representation_id,
                            content=sub,
                            chapter=sec.title,
                            section_title=sec.title,
                            position=position,
                            token_count=approximate_token_count(sub),
                            metadata={"category": category},
                        )
                    )
                    position += 1
        elif extraction.pages:
            # Fallback to page-based chunks if sections unavailable
            for page in extraction.pages:
                if page.text.strip():
                    chunks.append(
                        Chunk(
                            id=uuid4(),
                            document_id=doc.id,
                            representation_id=extraction.representation_id,
                            content=page.text,
                            page_start=page.page_number,
                            page_end=page.page_number,
                            position=position,
                            token_count=approximate_token_count(page.text),
                            metadata={"category": category, "page": page.page_number},
                        )
                    )
                    position += 1

    # 3. Codex Data Books: Tables as single chunks, prose by section/page
    elif category == "codex":
        # Add tables first
        for table in extraction.tables:
            table_lines = [" | ".join(row) for row in table.get("rows", [])]
            table_text = "\n".join(table_lines)
            if table_text.strip():
                chunks.append(
                    Chunk(
                        id=uuid4(),
                        document_id=doc.id,
                        representation_id=extraction.representation_id,
                        content=f"Table: {doc.title}\n{table_text}",
                        section_title="Data Table",
                        position=position,
                        token_count=approximate_token_count(table_text),
                        metadata={"category": category, "is_table": True},
                    )
                )
                position += 1

        # Add prose sections
        if extraction.sections:
            for sec in extraction.sections:
                paras = sec.content.split("\n\n")
                sub_chunks = chunk_paragraphs_with_overlap(paras)
                for sub in sub_chunks:
                    chunks.append(
                        Chunk(
                            id=uuid4(),
                            document_id=doc.id,
                            representation_id=extraction.representation_id,
                            content=sub,
                            section_title=sec.title,
                            position=position,
                            token_count=approximate_token_count(sub),
                            metadata={"category": category},
                        )
                    )
                    position += 1
        elif extraction.pages:
            for page in extraction.pages:
                if page.text.strip():
                    chunks.append(
                        Chunk(
                            id=uuid4(),
                            document_id=doc.id,
                            representation_id=extraction.representation_id,
                            content=page.text,
                            page_start=page.page_number,
                            page_end=page.page_number,
                            position=position,
                            token_count=approximate_token_count(page.text),
                            metadata={"category": category},
                        )
                    )
                    position += 1

    # 4. Ephemera & other documents: 1-2 chunks per document
    else:
        text = extraction.raw_text.strip()
        if text:
            paras = text.split("\n\n")
            sub_chunks = chunk_paragraphs_with_overlap(paras, max_tokens=CHUNK_MAX_TOKENS)
            for sub in sub_chunks:
                chunks.append(
                    Chunk(
                        id=uuid4(),
                        document_id=doc.id,
                        representation_id=extraction.representation_id,
                        content=sub,
                        section_title=doc.title,
                        position=position,
                        token_count=approximate_token_count(sub),
                        metadata={"category": category},
                    )
                )
                position += 1

    return chunks


def create_image_chunk(asset: Asset) -> Chunk:
    """Create a synthetic text chunk from an image asset for hybrid retrieval."""
    content_lines = [
        f"Visual Archive Record: {asset.entity_name or 'Artwork'}",
        f"Asset Type: {asset.asset_type.replace('_', ' ').title()}",
    ]
    if asset.description:
        content_lines.append(f"Description: {asset.description}")
    if asset.extracted_data:
        data_str = ", ".join(f"{k}: {v}" for k, v in asset.extracted_data.items())
        content_lines.append(f"Recorded Data: {data_str}")

    content = "\n".join(content_lines)

    return Chunk(
        id=uuid4(),
        document_id=asset.document_id,
        content=content,
        section_title=f"{asset.asset_type.title()} - {asset.entity_name}",
        position=0,
        token_count=approximate_token_count(content),
        metadata={
            "is_asset_chunk": True,
            "asset_id": str(asset.id),
            "asset_type": asset.asset_type,
            "entity_name": asset.entity_name,
            "file_path": asset.file_path,
        },
    )
