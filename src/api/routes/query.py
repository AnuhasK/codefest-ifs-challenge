import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
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
    """Resolve an asset file path to exist on the current filesystem (cross-platform Linux/Windows)."""
    if not raw_path:
        return ""

    # Normalize backslashes from Windows DB paths to forward slashes
    norm_path = raw_path.replace("\\", "/")
    p = Path(norm_path)
    if p.exists() and p.is_file():
        return str(p.resolve())

    # Try resolving relative to CORPUS_PATH
    if "Ashen_Era_Archive/" in norm_path:
        rel_sub = norm_path.split("Ashen_Era_Archive/", 1)[-1]
        candidate = CORPUS_PATH / rel_sub
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())

    # Try matching filename inside CORPUS_PATH
    filename = norm_path.split("/")[-1]
    if filename:
        found = list(CORPUS_PATH.glob(f"**/{filename}"))
        if found and found[0].is_file():
            return str(found[0].resolve())

    return raw_path



def fetch_assets_for_evidence(
    evidence_list: list,
    cited_evidence_ids: Optional[Set[str]] = None,
    question: str = "",
) -> List[AssetReference]:
    """
    Extract and fetch full asset details (Track 1A).
    Strictly prioritizes assets that are ACTUALLY CITED in the answer so that irrelevant
    candidate chunks retrieved during broad candidate search do not leak images into the UI.
    """
    # If cited_evidence_ids are provided, prioritize cited evidence records
    target_evidence = evidence_list
    if cited_evidence_ids:
        cited_chunks = [ev for ev in evidence_list if ev.id in cited_evidence_ids]
        # If any cited chunk directly references an asset, restrict strictly to cited chunks
        if any(getattr(ev, "metadata", {}).get("asset_id") for ev in cited_chunks):
            target_evidence = cited_chunks
        elif cited_chunks:
            target_evidence = cited_chunks

    asset_ids: List[str] = []
    metadata_assets: Dict[str, Dict[str, Any]] = {}
    candidate_entity_names: List[str] = []

    for ev in target_evidence:
        meta = getattr(ev, "metadata", {}) or {}
        aid = meta.get("asset_id")
        if aid:
            aid_str = str(aid)
            if aid_str not in asset_ids:
                asset_ids.append(aid_str)
                metadata_assets[aid_str] = meta

        # Collect candidate entity names from metadata and section titles
        e_name = meta.get("entity_name")
        if e_name and isinstance(e_name, str):
            candidate_entity_names.append(e_name.strip())
        sec = getattr(ev, "section_title", "") or ""
        if " - " in sec:
            candidate_entity_names.append(sec.split(" - ", 1)[-1].strip())

    assets: List[AssetReference] = []
    seen_ids = set()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                # 1. Query by explicit asset UUIDs from target evidence
                if asset_ids:
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

                # 2. If no assets were found from explicit asset_ids, check matching entity names
                if not assets:
                    clean_names = list({n for n in candidate_entity_names if len(n) > 2 and n.lower() != "archive asset"})
                    if clean_names:
                        cur.execute(
                            """
                            SELECT id, asset_type, entity_name, file_path, description, extracted_data
                            FROM assets
                            WHERE entity_name = ANY(%s)
                            LIMIT 3
                            """,
                            [clean_names],
                        )
                        rows_ent = cur.fetchall()
                        for row in rows_ent:
                            aid_str = str(row["id"])
                            if aid_str not in seen_ids:
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
def query_endpoint(request: QueryRequest) -> QueryResponse:
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

        # 1. Map Citations with document_id and file_url for PDF reference viewing
        citations: List[CitationItem] = []
        for c in final_answer.citations:
            meta = c.get("metadata") or {}
            page_val = c.get("page") or meta.get("page") or 1
            ref_loc = c.get("reference_location") or meta.get("reference_location")
            if not ref_loc:
                l_start = c.get("line_start") or meta.get("line_start")
                l_end = c.get("line_end") or meta.get("line_end")
                p_start = meta.get("paragraph_start")
                if l_start == "Plate" or meta.get("is_asset_chunk"):
                    ref_loc = "Plate / Visual Record"
                elif l_start and l_end:
                    ref_loc = f"p. {page_val} (Lines {l_start}-{l_end})"
                elif p_start:
                    ref_loc = f"p. {page_val} (Para {p_start})"
                else:
                    ref_loc = f"p. {page_val}"

            doc_id = c.get("document_id")
            if not doc_id:
                for ev in final_answer.evidence:
                    if ev.id == c.get("evidence_id"):
                        doc_id = ev.document_id
                        break

            file_url = f"/documents/{doc_id}/file" if doc_id else None

            citations.append(
                CitationItem(
                    evidence_id=c.get("evidence_id", ""),
                    document_title=c.get("document_title", "Archive Document"),
                    source_path=c.get("source_path") or c.get("source_file"),
                    page=page_val,
                    excerpt=c.get("excerpt", "") or c.get("original_text", "")[:200],
                    reference_location=ref_loc,
                    line_start=c.get("line_start") or meta.get("line_start"),
                    line_end=c.get("line_end") or meta.get("line_end"),
                    document_id=doc_id,
                    file_url=file_url,
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

        # Collect cited evidence IDs from citations
        cited_evidence_ids = {c.evidence_id for c in citations if c.evidence_id}

        # 3. Fetch Multimodal Asset References (Track 1A) strictly for cited evidence
        asset_refs = fetch_assets_for_evidence(
            final_answer.evidence,
            cited_evidence_ids=cited_evidence_ids,
            question=request.question,
        )


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
            raw_answer=final_answer.raw_answer_text,
            citations=citations,
            evidence=evidence_summaries,
            asset_references=asset_refs,
            conflicts=conflicts,
            evidence_status=final_answer.evidence_status,
            trace=trace_data,
            warning=final_answer.warning,
        )


    except Exception as exc:
        logger.exception(f"Error executing query: {exc}")
        raise HTTPException(
            status_code=500,
            detail=f"An error occurred while processing the query: {str(exc)}",
        )
