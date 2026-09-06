import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import UUID
from fastapi import APIRouter, HTTPException

from src.api.schemas import (
    QueryRequest,
    QueryResponse,
    AssetReference,
    EvidenceSummary,
    ConflictSummary,
    CitationItem,
)
from src.generation.answer import answer_question
from src.retrieval.orchestrator import RetrievalConfig
from src.database.postgres import get_db_connection
from src.config import CORPUS_PATH

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Query"])


def resolve_asset_file_path(raw_path: str) -> str:
    """Resolve an asset file path to exist on the current filesystem."""
    if not raw_path:
        return ""
    p = Path(raw_path)
    if p.exists():
        return str(p.resolve())

    # Try resolving relative to CORPUS_PATH
    parts = p.parts
    if "Ashen_Era_Archive" in parts:
        idx = parts.index("Ashen_Era_Archive")
        rel_sub = Path(*parts[idx + 1:])
        candidate = CORPUS_PATH / rel_sub
        if candidate.exists():
            return str(candidate.resolve())

    # Try matching filename inside CORPUS_PATH
    found = list(CORPUS_PATH.glob(f"**/{p.name}"))
    if found:
        return str(found[0].resolve())

    return raw_path


def fetch_assets_for_evidence(evidence_list: list) -> List[AssetReference]:
    """
    Extract and fetch full asset details (Track 1A) for any evidence records
    referencing figure plates, heraldry, portraits, or illustrations.
    """
    asset_ids: List[str] = []
    metadata_assets: Dict[str, Dict[str, Any]] = {}

    for ev in evidence_list:
        meta = getattr(ev, "metadata", {}) or {}
        aid = meta.get("asset_id")
        if aid:
            aid_str = str(aid)
            if aid_str not in asset_ids:
                asset_ids.append(aid_str)
                metadata_assets[aid_str] = meta

    if not asset_ids:
        return []

    assets: List[AssetReference] = []
    seen_ids = set()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, asset_type, entity_name, file_path, description, extracted_data
                    FROM assets
                    WHERE id = ANY(%s::uuid[])
                    """,
                    [asset_ids],
                )
                rows = cur.fetchall()
                for row in rows:
                    aid_str = str(row["id"])
                    seen_ids.add(aid_str)
                    resolved_path = resolve_asset_file_path(row["file_path"])
                    assets.append(
                        AssetReference(
                            asset_id=aid_str,
                            asset_type=row["asset_type"],
                            file_path=resolved_path,
                            image_url=f"/assets/{aid_str}/image",
                            entity_name=row.get("entity_name"),
                            description=row.get("description") or "",
                            extracted_data=row.get("extracted_data") or {},
                        )
                    )
    except Exception as exc:
        logger.warning(f"Error fetching asset records from database: {exc}")

    # Fallback to chunk metadata if DB lookup missed any
    for aid_str, meta in metadata_assets.items():
        if aid_str not in seen_ids:
            resolved_path = resolve_asset_file_path(meta.get("file_path", ""))
            assets.append(
                AssetReference(
                    asset_id=aid_str,
                    asset_type=meta.get("asset_type", "image"),
                    file_path=resolved_path,
                    image_url=f"/assets/{aid_str}/image",
                    entity_name=meta.get("entity_name"),
                    description=meta.get("description") or "",
                    extracted_data=meta.get("extracted_data") or {},
                )
            )

    return assets


@router.post("/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """
    Answer a question about the Ashen Era Archive.
    Returns an evidence-grounded answer with deterministic citations, source epistemological metadata,
    contradiction alerts, Track 1A image references, and Track 1C query traces.
    """
    try:
        config = RetrievalConfig(
            enable_multihop=(request.max_hops > 1),
            multihop_max_hops=request.max_hops,
            reranker_top_k=request.top_k,
        )

        final_answer = answer_question(
            query=request.question,
            config=config,
        )

        # 1. Map Citations
        citations: List[CitationItem] = []
        for c in final_answer.citations:
            citations.append(
                CitationItem(
                    evidence_id=c.get("evidence_id", ""),
                    document_title=c.get("document_title", "Archive Document"),
                    source_path=c.get("source_path"),
                    page=c.get("page"),
                    excerpt=c.get("excerpt", ""),
                )
            )

        # 2. Map Evidence Summaries
        evidence_summaries: List[EvidenceSummary] = []
        for ev in final_answer.evidence:
            evidence_summaries.append(
                EvidenceSummary(
                    id=ev.id,
                    chunk_id=ev.chunk_id,
                    document_id=ev.document_id,
                    document_title=ev.document_title,
                    source_category=ev.source_category,
                    source_subtype=ev.source_subtype,
                    page=ev.page,
                    section_title=ev.section_title,
                    content=ev.content,
                    score=ev.score,
                    metadata=ev.metadata,
                )
            )

        # 3. Fetch Multimodal Asset References (Track 1A)
        asset_refs = fetch_assets_for_evidence(final_answer.evidence)

        # 4. Map Conflicts
        conflicts: List[ConflictSummary] = []
        for conf in final_answer.conflicts:
            conflicts.append(
                ConflictSummary(
                    claim_summary=conf.claim_summary,
                    supporting=[e.id for e in conf.supporting_evidence],
                    opposing=[e.id for e in conf.opposing_evidence],
                    conflict_type=conf.conflict_type,
                )
            )

        # 5. Query Trace (Track 1C)
        trace_data = final_answer.query_trace if request.include_trace else None

        return QueryResponse(
            question=final_answer.question,
            answer=final_answer.answer_text,
            citations=citations,
            evidence=evidence_summaries,
            asset_references=asset_refs,
            conflicts=conflicts,
            evidence_status=final_answer.evidence_status,
            trace=trace_data,
        )

    except Exception as exc:
        logger.exception(f"Error executing query: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while processing the query: {str(exc)}",
        )
