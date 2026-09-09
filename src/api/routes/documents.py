import logging
from pathlib import Path
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse

from src.api.schemas import (
    DocumentSummary,
    DocumentDetail,
    DocumentSection,
    ChunkDetail,
    EntitySummary,
    EntityDetail,
    EntityRelationship,
)
from src.database.postgres import get_db_connection
from src.database.neo4j_db import get_neo4j_connection
from src.config import CORPUS_PATH
from src.api.routes.query import resolve_asset_file_path

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Documents & Entities"])


# ==========================================
# Document Endpoints
# ==========================================

@router.get("/documents", response_model=List[DocumentSummary])
def list_documents(
    category: Optional[str] = Query(None, description="Filter by source_category (e.g., chronicles, wiki, codex, ephemera)")
) -> List[DocumentSummary]:
    """List all ingested documents with their source categories and chunk counts."""
    params = []
    where_clause = ""
    if category:
        where_clause = "WHERE LOWER(d.source_category) = LOWER(%s)"
        params.append(category)

    query = f"""
        SELECT d.id, d.title, d.source_category, d.source_path, COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON d.id = c.document_id
        {where_clause}
        GROUP BY d.id, d.title, d.source_category, d.source_path
        ORDER BY d.title ASC;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    return [
        DocumentSummary(
            id=str(r["id"]),
            title=r["title"],
            source_category=r["source_category"],
            source_path=r["source_path"],
            chunk_count=r["chunk_count"],
        )
        for r in rows
    ]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str) -> DocumentDetail:
    """Retrieve full details for a document including section hierarchy."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id format (UUID required)")

    doc_query = """
        SELECT d.id, d.title, d.source_category, d.source_path, d.metadata, COUNT(c.id) AS chunk_count
        FROM documents d
        LEFT JOIN chunks c ON d.id = c.document_id
        WHERE d.id = %s
        GROUP BY d.id, d.title, d.source_category, d.source_path, d.metadata;
    """
    sec_query = """
        SELECT id, title, level, position
        FROM sections
        WHERE document_id = %s
        ORDER BY position ASC;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(doc_query, [doc_uuid])
            doc_row = cur.fetchone()
            if not doc_row:
                raise HTTPException(status_code=404, detail=f"Document {document_id} not found")

            cur.execute(sec_query, [doc_uuid])
            sec_rows = cur.fetchall()

    sections = [
        DocumentSection(
            id=str(s["id"]),
            title=s.get("title"),
            level=s.get("level", 1),
            position=s.get("position", 0),
        )
        for s in sec_rows
    ]

    return DocumentDetail(
        id=str(doc_row["id"]),
        title=doc_row["title"],
        source_category=doc_row["source_category"],
        source_path=doc_row["source_path"],
        metadata=doc_row.get("metadata") or {},
        chunk_count=doc_row["chunk_count"],
        sections=sections,
    )


@router.get("/documents/{document_id}/chunks", response_model=List[ChunkDetail])
def get_document_chunks(document_id: str) -> List[ChunkDetail]:
    """Retrieve all chunks belonging to a document in reading order."""
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id format (UUID required)")

    query = """
        SELECT id, document_id, content, page_start, page_end, section_title, position, metadata
        FROM chunks
        WHERE document_id = %s
        ORDER BY position ASC, page_start ASC;
    """
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, [doc_uuid])
            rows = cur.fetchall()

    return [
        ChunkDetail(
            id=str(r["id"]),
            document_id=str(r["document_id"]),
            content=r["content"],
            page_start=r.get("page_start"),
            page_end=r.get("page_end"),
            section_title=r.get("section_title"),
            position=r.get("position", 0),
            metadata=r.get("metadata") or {},
        )
        for r in rows
    ]


# ==========================================
# Entity Endpoints
# ==========================================

@router.get("/entities", response_model=List[EntitySummary])
def list_entities(
    entity_type: Optional[str] = Query(None, description="Filter by entity type (e.g. PERSON, FACTION, LOCATION, EVENT)")
) -> List[EntitySummary]:
    """List entities from the Neo4j knowledge graph, sorted by archive mention frequency."""
    neo4j_conn = get_neo4j_connection()
    cypher = """
        MATCH (e:Entity)
        WHERE ($type IS NULL OR toLower(e.type) = toLower($type))
        RETURN e.id AS id,
               e.name AS name,
               e.type AS entity_type,
               e.aliases AS aliases,
               e.mention_count AS mention_count,
               e.confidence AS confidence
        ORDER BY e.mention_count DESC
        LIMIT 150;
    """
    records = neo4j_conn.execute_query(cypher, {"type": entity_type})
    return [
        EntitySummary(
            id=r["id"],
            name=r["name"],
            entity_type=r.get("entity_type") or "UNKNOWN",
            aliases=r.get("aliases") or [],
            mention_count=int(r.get("mention_count") or 1),
            confidence=float(r.get("confidence") or 1.0),
        )
        for r in records
    ]


@router.get("/entities/{entity_id}", response_model=EntityDetail)
def get_entity(entity_id: str) -> EntityDetail:
    """Retrieve details for an entity including its domain graph relationships and chunk mentions."""
    neo4j_conn = get_neo4j_connection()

    # 1. Fetch entity node
    entity_cypher = """
        MATCH (e:Entity)
        WHERE e.id = $entity_id OR toLower(e.name) = toLower($entity_id)
        RETURN e.id AS id,
               e.name AS name,
               e.type AS entity_type,
               e.aliases AS aliases,
               e.mention_count AS mention_count,
               e.confidence AS confidence
        LIMIT 1;
    """
    records = neo4j_conn.execute_query(entity_cypher, {"entity_id": entity_id})
    if not records:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    e_data = records[0]
    canonical_id = e_data["id"]

    # 2. Fetch relationships
    rel_cypher = """
        MATCH (e:Entity {id: $id})-[r]-(target:Entity)
        WHERE type(r) <> 'MENTIONED_IN' AND type(r) <> 'APPEARS_IN'
        RETURN DISTINCT type(r) AS rel_type,
                        target.id AS target_id,
                        target.name AS target_name,
                        target.type AS target_type
        LIMIT 50;
    """
    rel_records = neo4j_conn.execute_query(rel_cypher, {"id": canonical_id})
    relationships = [
        EntityRelationship(
            rel_type=r["rel_type"],
            target_id=r["target_id"],
            target_name=r["target_name"],
            target_type=r.get("target_type") or "UNKNOWN",
        )
        for r in rel_records
    ]

    # 3. Fetch linked chunk IDs
    chunk_cypher = """
        MATCH (e:Entity {id: $id})-[:MENTIONED_IN]->(c:Chunk)
        RETURN c.id AS chunk_id
        LIMIT 20;
    """
    chunk_records = neo4j_conn.execute_query(chunk_cypher, {"id": canonical_id})
    sample_chunk_ids = [r["chunk_id"] for r in chunk_records if "chunk_id" in r]

    return EntityDetail(
        id=canonical_id,
        name=e_data["name"],
        entity_type=e_data.get("entity_type") or "UNKNOWN",
        aliases=e_data.get("aliases") or [],
        mention_count=int(e_data.get("mention_count") or 1),
        confidence=float(e_data.get("confidence") or 1.0),
        relationships=relationships,
        sample_chunk_ids=sample_chunk_ids,
    )


# ==========================================
# Multimodal Asset Streaming Endpoint
# ==========================================

@router.get("/assets/{asset_id}/image")
def get_asset_image(asset_id: str):
    """Stream an archive image file directly from disk (Track 1A figure plate or illustration)."""
    try:
        asset_uuid = UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid asset_id format (UUID required)")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT file_path, asset_type FROM assets WHERE id = %s;", [asset_uuid])
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    resolved = resolve_asset_file_path(row["file_path"])
    p = Path(resolved)
    if not p.exists() or not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Asset image file not found on disk at {resolved}",
        )

    suffix = p.suffix.lower()
    media_type = "image/png"
    if suffix in (".jpg", ".jpeg"):
        media_type = "image/jpeg"
    elif suffix == ".webp":
        media_type = "image/webp"

    return FileResponse(path=str(p), media_type=media_type)


# ==========================================
# Original Archive Document File Streaming Endpoints
# ==========================================

def serve_file_response(p: Path) -> FileResponse:
    """Serve an archive file with proper media type and Content-Disposition header."""
    suffix = p.suffix.lower()
    if suffix == ".docx":
        # Word documents download directly to the browser
        media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        headers = {
            "Content-Disposition": f'attachment; filename="{p.name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    elif suffix == ".pdf":
        # PDFs open directly in the browser viewer
        media_type = "application/pdf"
        headers = {
            "Content-Disposition": f'inline; filename="{p.name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    elif suffix in (".txt", ".log", ".md"):
        # Markdown & Text open directly in browser
        media_type = "text/plain; charset=utf-8"
        headers = {
            "Content-Disposition": f'inline; filename="{p.name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        # Images open directly in browser
        media_type = f"image/{suffix.lstrip('.')}" if suffix != ".jpg" else "image/jpeg"
        headers = {
            "Content-Disposition": f'inline; filename="{p.name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    else:
        media_type = "application/octet-stream"
        headers = {
            "Content-Disposition": f'attachment; filename="{p.name}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }

    return FileResponse(path=str(p), media_type=media_type, headers=headers)


@router.get("/Ashen_Era_Archive/{archive_path:path}")
@router.get("/archive/{archive_path:path}")
def get_archive_file_by_path(archive_path: str, format: Optional[str] = Query(None)):
    """
    Serve archive documents directly using their relative path from the project root.
    Examples:
      - /Ashen_Era_Archive/codex/codex_vaeloria_i_gazetteer_of_the_sundered_realms.pdf#page=19
      - /Ashen_Era_Archive/wiki/cerys_sablewood_the_ashen.md
      - /Ashen_Era_Archive/ephemera/auction_catalogue_concerning_halvard_sablewood.docx
    """
    clean_sub = archive_path.replace("\\", "/").lstrip("/")
    if ".." in clean_sub:
        raise HTTPException(status_code=400, detail="Invalid path traversal")

    target = (CORPUS_PATH / clean_sub).resolve()
    try:
        target.relative_to(CORPUS_PATH)
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    if not target.exists() or not target.is_file():
        # Check alternative formats
        if clean_sub.lower().endswith(".pdf"):
            scan_cand = target.with_name(target.stem + ".scan.pdf")
            if scan_cand.exists() and scan_cand.is_file():
                target = scan_cand
            else:
                docx_cand = target.with_suffix(".docx")
                if docx_cand.exists() and docx_cand.is_file():
                    target = docx_cand
        elif clean_sub.lower().endswith(".docx"):
            pdf_cand = target.with_suffix(".pdf")
            if pdf_cand.exists() and pdf_cand.is_file():
                target = pdf_cand

        if not target.exists() or not target.is_file():
            resolved = resolve_asset_file_path(clean_sub)
            if resolved:
                target = Path(resolved)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Archive file not found: {clean_sub}")

    # If format='docx' requested and sibling docx exists, prefer docx
    if format == "docx" and target.suffix.lower() != ".docx":
        docx_sibling = target.with_suffix(".docx")
        if docx_sibling.exists():
            target = docx_sibling

    return serve_file_response(target)


@router.get("/documents/{document_id}/file")
def get_document_file(document_id: str, format: Optional[str] = Query(None)):
    """
    Stream or serve an archive document by document UUID, chunk UUID, or asset UUID.
    If the document has both DOCX and PDF representations:
      - Default: serves PDF inline for native browser viewing and #page=N jumping.
      - If format='docx': downloads the original Word document.
    If the document only has DOCX: downloads the Word document.
    If the document is Markdown or Text: views inline in browser.
    """
    try:
        doc_uuid = UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document_id format (UUID required)")

    raw_path = None
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # 1. Try documents table
            cur.execute("SELECT title, source_path, source_category FROM documents WHERE id = %s;", [doc_uuid])
            row = cur.fetchone()
            if row and row.get("source_path"):
                raw_path = row["source_path"]
            else:
                # 2. Try chunks table (chunk_id passed or asset chunk)
                cur.execute("""
                    SELECT d.source_path, c.metadata
                    FROM chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE c.id = %s;
                """, [doc_uuid])
                c_row = cur.fetchone()
                if c_row:
                    if c_row.get("source_path"):
                        raw_path = c_row["source_path"]
                    elif c_row.get("metadata", {}).get("file_path"):
                        raw_path = c_row["metadata"]["file_path"]
                else:
                    # 3. Try assets table
                    cur.execute("SELECT file_path FROM assets WHERE id = %s;", [doc_uuid])
                    a_row = cur.fetchone()
                    if a_row and a_row.get("file_path"):
                        raw_path = a_row["file_path"]

    if not raw_path:
        raise HTTPException(status_code=404, detail=f"Document or asset {document_id} not found")

    resolved = resolve_asset_file_path(raw_path)
    p = Path(resolved)

    # Format selection logic:
    if format == "docx" and p.suffix.lower() != ".docx":
        sibling_docx = p.with_suffix(".docx")
        if sibling_docx.exists():
            p = sibling_docx
    elif format != "docx" and p.suffix.lower() == ".docx":
        # Sibling PDF check: If a PDF exists, prefer serving PDF for inline browser viewing with page jump
        sibling_pdf = p.with_suffix(".pdf")
        scan_pdf = p.with_name(p.stem + ".scan.pdf")
        if sibling_pdf.exists() and sibling_pdf.is_file():
            p = sibling_pdf
        elif scan_pdf.exists() and scan_pdf.is_file():
            p = scan_pdf

    if not p.exists() or not p.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Document file not found on disk at {resolved}",
        )

    return serve_file_response(p)


