from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import UUID, uuid4
import json
import re
import spacy

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
from src.ingestion.entities import extract_entities_from_corpus, ExtractedEntity
from src.ingestion.graph_storage import store_entities_in_neo4j
from src.ingestion.contextualization import contextualize_all_chunks
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider
from src.providers.llm_provider import LLMProvider, get_llm_provider


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

    doc_chunk_counts: Dict[UUID, int] = {doc.id: 0 for doc in documents}
    for c in chunks:
        if c.document_id in doc_chunk_counts:
            doc_chunk_counts[c.document_id] += 1
        
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
            # Clean previous ingestion records to ensure clean, non-duplicate tables
            cur.execute("TRUNCATE TABLE provenance, chunks, document_representations, assets, documents CASCADE;")

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

            # 2. Insert chunks with vector embeddings
            for chunk in chunks:
                emb_val = chunk.embedding if chunk.embedding is not None else None
                ctx_emb_val = chunk.contextual_embedding if chunk.contextual_embedding is not None else None

                cur.execute(
                    """
                    INSERT INTO chunks (
                        id, document_id, section_id, representation_id, content,
                        contextualized_content, page_start, page_end, chapter,
                        section_title, position, token_count, embedding, contextual_embedding, metadata
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        content = EXCLUDED.content,
                        contextualized_content = EXCLUDED.contextualized_content,
                        section_title = EXCLUDED.section_title,
                        token_count = EXCLUDED.token_count,
                        embedding = EXCLUDED.embedding,
                        contextual_embedding = EXCLUDED.contextual_embedding,
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
                        emb_val,
                        ctx_emb_val,
                        json.dumps(chunk.metadata),
                    ),
                )

            # 3. Insert assets
            for asset in assets:
                asset_emb = asset.embedding if hasattr(asset, "embedding") and asset.embedding is not None else None
                cur.execute(
                    """
                    INSERT INTO assets (id, document_id, file_path, asset_type, entity_name, description, extracted_data, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        description = EXCLUDED.description,
                        extracted_data = EXCLUDED.extracted_data,
                        embedding = EXCLUDED.embedding,
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
                        asset_emb,
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


def apply_vector_indexes() -> None:
    """Apply HNSW vector indexes to pgvector tables."""
    migration_file = Path(__file__).resolve().parent.parent / "database" / "migrations" / "002_vector_indexes.sql"
    if migration_file.exists():
        sql = migration_file.read_text(encoding="utf-8")
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
        print("HNSW vector indexes successfully applied in PostgreSQL.", flush=True)


def run_ingestion(
    corpus_path: Path | str = CORPUS_PATH,
    use_vision: bool = True,
    generate_embeddings_flag: bool = True,
    use_contextual_llm: bool = True,
    persist_db: bool = True,
    persist_neo4j: bool = True,
) -> IngestionReport:
    """
    Execute full offline ingestion pipeline:
    1. Discovery & Bundling
    2. Text & OCR Extraction
    3. Gemini Vision Image Processing
    4. Format-aware Chunking & Provenance
    5. Gazette & spaCy Entity Extraction (Phase 2)
    6. Hybrid Contextual Prefix Generation (Phase 2)
    7. Dual Vector Embeddings (Standard + Contextual) (Phase 2)
    8. Database Persistence & HNSW Indexing
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
        if doc.source_category == "images":
            continue

        rep = doc.representations[0]
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

            if extraction.pages:
                rep.page_count = len(extraction.pages)

            chunks = chunk_document(extraction, doc)
            all_chunks.extend(chunks)

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
    
    doc_by_stem = {doc.metadata.get("stem"): doc for doc in documents if doc.metadata}
    plate_count = sum(1 for a in assets if a.asset_type == "figure_plate")
    art_count = len(assets) - plate_count

    for asset in assets:
        stem = Path(asset.file_path).stem
        if stem.startswith("atmo_"):
            wiki_slug = re.sub(r"^atmo_[a-z]+_[a-z]+_", "", stem)
            if wiki_slug in doc_by_stem:
                asset.document_id = doc_by_stem[wiki_slug].id
        elif stem.startswith("plate_"):
            plate_slug = re.sub(r"^plate_\d+_", "", stem)
            if plate_slug in doc_by_stem:
                asset.document_id = doc_by_stem[plate_slug].id

        img_chunk = create_image_chunk(asset)
        all_chunks.append(img_chunk)

        # Determine extraction method accurately: RapidOCR for figure plates processed locally,
        # Gemini Vision for atmospheric art / vision-described plates, or metadata parser
        if asset.extracted_data and asset.extracted_data.get("ocr_source") == "rapidocr":
            extraction_method = "rapidocr"
        elif use_vision:
            extraction_method = "gemini_vision"
        else:
            extraction_method = "metadata_parser"

        prov = Provenance(
            id=uuid4(),
            chunk_id=img_chunk.id,
            document_id=img_chunk.document_id,
            representation_id=None,
            source_file=asset.file_path,
            page_number=None,
            extraction_method=extraction_method,
        )
        provenance_records.append(prov)

    print(f"Processed {len(assets)} visual assets ({plate_count} plates, {art_count} art).", flush=True)
    print(f"Total chunks created: {len(all_chunks)} ({len(all_chunks) - len(assets)} text, {len(assets)} image chunks).", flush=True)

    # 4. Phase 2: Corpus-Aware Entity Extraction (Gazette + Rules + Targeted Gemini)
    print("Running Corpus-Aware entity extraction (Gazette + Rules + Targeted Gemini)...", flush=True)
    nlp = spacy.load("en_core_web_sm")
    llm = get_llm_provider() if use_contextual_llm else None
    all_extracted_entities, chunk_to_entities = extract_entities_from_corpus(
        chunks=all_chunks,
        corpus_path=corpus_root,
        llm=llm,
        nlp=nlp,
    )
    print(f"Extracted {len(all_extracted_entities)} entity occurrences across chunks (aliases resolved).", flush=True)

    # 5. Phase 2: Hybrid Contextual Prefix Generation
    print("Generating hybrid contextual prefixes (Tier 1 Template + Tier 2 LLM)...", flush=True)
    docs_map = {str(d.id): d.title for d in documents}
    ctx_stats = contextualize_all_chunks(
        chunks=all_chunks,
        chunk_entities=chunk_to_entities,
        documents_map=docs_map,
        nlp=nlp,
        llm=llm,
        use_llm_tier=use_contextual_llm,
    )
    print(f"Contextualization complete: {ctx_stats['template_prefixes']} template prefixes, {ctx_stats['llm_prefixes']} LLM prefixes.", flush=True)

    # 6. Phase 2: Embedding Generation (Standard + Contextual)
    if generate_embeddings_flag:
        provider = get_embedding_provider()
        provider_name = type(provider).__name__
        embed_batch_size = 5 if "gemini" in provider_name.lower() else 16
        print(f"Generating dual embeddings via {provider_name} (batch_size={embed_batch_size})...", flush=True)
        
        # Batch raw text embeddings
        raw_texts = [c.content for c in all_chunks]
        raw_vectors = provider.embed_texts(raw_texts, batch_size=embed_batch_size)
        for c, vec in zip(all_chunks, raw_vectors):
            c.embedding = vec

        # Batch contextual text embeddings
        ctx_texts = [c.contextualized_content or c.content for c in all_chunks]
        ctx_vectors = provider.embed_texts(ctx_texts, batch_size=embed_batch_size)
        for c, vec in zip(all_chunks, ctx_vectors):
            c.contextual_embedding = vec

        # Embed visual asset descriptions
        asset_texts = [a.description or a.entity_name or "visual asset" for a in assets]
        asset_vectors = provider.embed_texts(asset_texts, batch_size=embed_batch_size)
        for a, vec in zip(assets, asset_vectors):
            a.embedding = vec

        print(f"Successfully generated {len(raw_vectors) + len(ctx_vectors) + len(asset_vectors)} vector embeddings.", flush=True)

    # 7. Validate
    validation = validate_ingested_corpus(documents, all_chunks, assets)

    # 8. Persist to PostgreSQL + HNSW Indexing
    if persist_db:
        print("Persisting documents, chunks, assets, and provenance to PostgreSQL...", flush=True)
        save_to_database(documents, all_chunks, assets, provenance_records)
        print("Database persistence complete.", flush=True)

        if generate_embeddings_flag:
            print("Applying HNSW vector indexes...", flush=True)
            apply_vector_indexes()

    # 9. Phase 4 Readiness: Persist Entities to Neo4j
    if persist_neo4j:
        print("Persisting entities and graph relationships to Neo4j...", flush=True)
        neo4j_res = store_entities_in_neo4j(
            entities=all_extracted_entities,
            chunk_to_entities=chunk_to_entities,
            documents=documents,
        )
        print(
            f"Neo4j entity persistence status: {neo4j_res.get('status')} "
            f"({neo4j_res.get('entities_saved', 0)} entities, "
            f"{neo4j_res.get('chunk_links', 0)} chunk edges, "
            f"{neo4j_res.get('doc_links', 0)} doc edges).",
            flush=True,
        )

        # Extract and persist domain relationships & co-occurrence
        print("Extracting and persisting relationships in Neo4j...", flush=True)
        from src.ingestion.relationships import extract_all_relationships
        from src.ingestion.graph_storage import store_relationships_in_neo4j, build_cooccurrence_graph

        relationships = extract_all_relationships(
            chunks=all_chunks,
            chunk_to_entities=chunk_to_entities,
            llm=llm if use_contextual_llm else None,
            corpus_path=corpus_root,
        )
        if relationships:
            rel_res = store_relationships_in_neo4j(relationships)
            print(
                f"Neo4j relationship persistence status: {rel_res.get('status')} "
                f"({rel_res.get('relationships_saved', 0)} relationships saved across types {rel_res.get('types')}).",
                flush=True,
            )

        # Build co-occurrence graph
        cooccur_res = build_cooccurrence_graph(chunk_to_entities, min_weight=2)
        print(
            f"Neo4j co-occurrence status: {cooccur_res.get('status')} "
            f"({cooccur_res.get('edges_saved', 0)} edges created).",
            flush=True,
        )

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
