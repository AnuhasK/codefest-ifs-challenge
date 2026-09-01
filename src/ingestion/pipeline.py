from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4
import json
import re
import psycopg

from src.config import CORPUS_PATH
from src.database.postgres import get_db_connection
from src.models.document import (
    LogicalDocument,
    Chunk,
    Asset,
    Provenance,
    IngestionReport,
    ValidationReport,
)
from src.ingestion.discovery import discover_corpus, bundle_documents
from src.ingestion.extraction import extract_document_representation
from src.ingestion.ocr import ocr_scanned_pdf
from src.ingestion.images import process_corpus_images
from src.ingestion.chunking import chunk_document, create_image_chunk


def validate_ingested_corpus(
    documents: List[LogicalDocument],
    chunks: List[Chunk],
    assets: List[Asset],
) -> ValidationReport:
    """Validate corpus completeness, detecting empty documents and duplicate chunks."""
    empty_docs: List[str] = []
    chunk_hashes = set()
    duplicate_chunks = 0
    warnings: List[str] = []

    # Map chunks to document IDs
    doc_chunk_counts: Dict[UUID, int] = {doc.id: 0 for doc in documents}
    for c in chunks:
        if c.document_id in doc_chunk_counts:
            doc_chunk_counts[c.document_id] += 1
        
        # Check text uniqueness
        content_snippet = c.content.strip()[:100]
        if content_snippet in chunk_hashes:
            duplicate_chunks += 1
        else:
            chunk_hashes.add(content_snippet)

    for doc in documents:
        if doc.source_category != "images" and doc_chunk_counts.get(doc.id, 0) == 0:
            empty_docs.append(f"{doc.title} ({doc.source_path})")

    if empty_docs:
        warnings.append(f"Found {len(empty_docs)} documents without extracted chunks.")

    if len(assets) == 0:
        warnings.append("No visual assets were ingested.")

    is_valid = len(empty_docs) == 0 and len(chunks) > 0

    return ValidationReport(
        is_valid=is_valid,
        total_documents=len(documents),
        total_chunks=len(chunks),
        total_assets=len(assets),
        empty_documents=empty_docs,
        duplicate_chunks=duplicate_chunks,
        warnings=warnings,
    )


def save_to_database(
    documents: List[LogicalDocument],
    chunks: List[Chunk],
    assets: List[Asset],
    provenance_records: List[Provenance],
) -> None:
    """Persist all ingested objects into PostgreSQL within a single managed transaction."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # 1. Insert documents
            for doc in documents:
                cur.execute(
                    """
                    INSERT INTO documents (id, title, source_category, source_path, metadata)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title,
                        source_category = EXCLUDED.source_category,
                        source_path = EXCLUDED.source_path,
                        metadata = EXCLUDED.metadata;
                    """,
                    (
                        doc.id,
                        doc.title,
                        doc.source_category,
                        doc.source_path,
                        json.dumps(doc.metadata),
                    ),
                )

                # Insert representations
                for rep in doc.representations:
                    cur.execute(
                        """
                        INSERT INTO document_representations (id, document_id, file_path, format, file_size_bytes, page_count)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING;
                        """,
                        (
                            rep.id,
                            rep.document_id,
                            rep.file_path,
                            rep.format,
                            rep.file_size_bytes,
                            rep.page_count,
                        ),
                    )

            # 2. Insert chunks
            for chunk in chunks:
                cur.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, section_id, representation_id, content,
                        contextualized_content, page_start, page_end, chapter,
                        section_title, position, token_count, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        section_title = EXCLUDED.section_title,
                        token_count = EXCLUDED.token_count,
                        metadata = EXCLUDED.metadata;
                    """,
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.section_id,
                        chunk.representation_id,
                        chunk.content,
                        chunk.contextualized_content,
                        chunk.page_start,
                        chunk.page_end,
                        chunk.chapter,
                        chunk.section_title,
                        chunk.position,
                        chunk.token_count,
                        json.dumps(chunk.metadata),
                    ),
                )

            # 3. Insert assets
            for asset in assets:
                cur.execute(
                    """
                    INSERT INTO assets (id, document_id, file_path, asset_type, entity_name, description, extracted_data, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        description = EXCLUDED.description,
                        extracted_data = EXCLUDED.extracted_data,
                        metadata = EXCLUDED.metadata;
                    """,
                    (
                        asset.id,
                        asset.document_id,
                        asset.file_path,
                        asset.asset_type,
                        asset.entity_name,
                        asset.description,
                        json.dumps(asset.extracted_data),
                        json.dumps(asset.metadata),
                    ),
                )

            # 4. Insert provenance
            for prov in provenance_records:
                cur.execute(
                    """
                    INSERT INTO provenance (id, chunk_id, document_id, representation_id, source_file, page_number, extraction_method)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING;
                    """,
                    (
                        prov.id,
                        prov.chunk_id,
                        prov.document_id,
                        prov.representation_id,
                        prov.source_file,
                        prov.page_number,
                        prov.extraction_method,
                    ),
                )


def run_ingestion(
    corpus_path: Path | str = CORPUS_PATH,
    use_vision: bool = True,
    persist_db: bool = True,
) -> IngestionReport:
    """
    Execute full offline ingestion pipeline:
    1. Discovery & Bundling
    2. Text & OCR Extraction
    3. Gemini Vision Image Processing
    4. Format-aware Chunking & Provenance
    5. Database Persistence & Validation
    """
    corpus_root = Path(corpus_path).resolve()
    print(f"--- Starting Corpus Ingestion from: {corpus_root} ---", flush=True)

    # 1. Discover and bundle
    discovered_files = discover_corpus(corpus_root)
    documents = bundle_documents(discovered_files)
    print(f"Discovered {len(discovered_files)} files bundled into {len(documents)} logical documents.", flush=True)

    all_chunks: List[Chunk] = []
    provenance_records: List[Provenance] = []
    failures: List[Dict[str, str]] = []
    ocr_count = 0

    # 2. Extract and chunk text documents
    for doc in documents:
        # If this is purely an image document, skip text extraction
        if doc.source_category == "images":
            continue

        # Find best representation to extract
        rep = doc.representations[0]
        # Prefer DOCX or MD representation if available
        for r in doc.representations:
            if r.format in ("docx", "md"):
                rep = r
                break

        try:
            if rep.format == "scan_pdf":
                extraction = ocr_scanned_pdf(rep.file_path, doc.id, rep.id)
                ocr_count += 1
            else:
                extraction = extract_document_representation(
                    rep.file_path, rep.format, doc.id, rep.id
                )

            # Update representation metadata
            if extraction.pages:
                rep.page_count = len(extraction.pages)

            # Chunk document
            chunks = chunk_document(extraction, doc)
            all_chunks.extend(chunks)

            # Generate provenance for each chunk
            for c in chunks:
                prov = Provenance(
                    id=uuid4(),
                    chunk_id=c.id,
                    document_id=doc.id,
                    representation_id=rep.id,
                    source_file=rep.file_path,
                    page_number=c.page_start or (c.metadata.get("page") if c.metadata else None),
                    extraction_method=extraction.extraction_method,
                )
                provenance_records.append(prov)

        except Exception as e:
            failures.append({"file": rep.file_path, "error": str(e)})
            print(f"Error extracting {rep.file_path}: {e}", flush=True)

    # 3. Process image assets
    print("Processing visual assets (figure plates & artwork)...", flush=True)
    assets = process_corpus_images(corpus_root, use_vision=use_vision)
    
    # Map assets to corresponding wiki documents if applicable
    doc_by_stem = {doc.metadata.get("stem"): doc for doc in documents if doc.metadata}
    
    plate_count = sum(1 for a in assets if a.asset_type == "figure_plate")
    art_count = len(assets) - plate_count

    for asset in assets:
        # Link asset to matching wiki document
        stem = Path(asset.file_path).stem
        if stem.startswith("atmo_"):
            # Extract wiki stem e.g. atmo_portrait_character_aldous_wrenfield_the_last_warden -> aldous_wrenfield_the_last_warden
            wiki_slug = re.sub(r"^atmo_[a-z]+_[a-z]+_", "", stem)
            if wiki_slug in doc_by_stem:
                asset.document_id = doc_by_stem[wiki_slug].id

        # Create synthetic chunk
        img_chunk = create_image_chunk(asset)
        all_chunks.append(img_chunk)

        # Provenance for synthetic chunk
        prov = Provenance(
            id=uuid4(),
            chunk_id=img_chunk.id,
            document_id=img_chunk.document_id,
            representation_id=None,
            source_file=asset.file_path,
            page_number=None,
            extraction_method="gemini_vision" if use_vision else "metadata_parser",
        )
        provenance_records.append(prov)

    print(f"Processed {len(assets)} visual assets ({plate_count} figure plates, {art_count} illustrations).", flush=True)
    print(f"Total chunks created: {len(all_chunks)} ({len(all_chunks) - len(assets)} text, {len(assets)} synthetic image chunks).", flush=True)

    # 4. Validate
    validation = validate_ingested_corpus(documents, all_chunks, assets)

    # 5. Persist to PostgreSQL
    if persist_db:
        print("Persisting documents, chunks, assets, and provenance to PostgreSQL...", flush=True)
        save_to_database(documents, all_chunks, assets, provenance_records)
        print("Database persistence complete.", flush=True)

    report = IngestionReport(
        documents_discovered=len(discovered_files),
        logical_documents_created=len(documents),
        files_extracted=len(documents) - len(failures),
        extraction_failures=failures,
        ocr_processed_files=ocr_count,
        images_processed=len(assets),
        figure_plates_extracted=plate_count,
        atmospheric_art_described=art_count,
        synthetic_image_chunks=len(assets),
        total_chunks_created=len(all_chunks),
        validation=validation,
    )

    print(f"--- Ingestion Complete (Valid: {validation.is_valid}) ---", flush=True)
    return report
