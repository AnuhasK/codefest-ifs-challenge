import re
import time
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.models.search import SearchResult
from src.models.evidence import FinalAnswer, EvidenceRecord, Conflict, VerificationResult
from src.knowledge.evidence import EvidenceManager, assess_evidence_sufficiency
from src.knowledge.conflict import detect_conflicts
from src.generation.citations import CitationResolver
from src.generation.verification import verify_answer
from src.generation.context_builder import build_evidence_context
from src.generation.prompts import (
    SYSTEM_PROMPT_BASELINE_RAG,
    QA_USER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT_PHASE6,
    QA_USER_PROMPT_PHASE6_TEMPLATE,
)
from src.providers.llm_provider import LLMProvider, get_llm_provider
from src.providers.key_rotator import GeminiQuotaExhaustedError
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider
from src.providers.reranker_provider import RerankerProvider
from src.retrieval.orchestrator import retrieve, retrieve_with_multihop, RetrievalConfig
from src.retrieval.query_analyzer import analyze_query, QueryAnalysis
from src.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class Citation(BaseModel):
    """Resolved citation referencing original document and page."""
    evidence_id: str
    document_title: str
    source_path: Optional[str] = None
    page: Optional[int] = None
    excerpt: str


class GroundedAnswer(BaseModel):
    """Complete grounded answer object with resolved citations and evidence traceability."""
    question: str
    answer_text: str
    citations: List[Citation] = Field(default_factory=list)
    evidence_used: List[str] = Field(default_factory=list)
    tokens_used: int = 0
    model_used: str = ""
    evidence_count: int = 0


def generate_grounded_answer(
    question: str,
    evidence: List[SearchResult],
    llm: Optional[LLMProvider] = None,
    model: Optional[str] = None,
) -> GroundedAnswer:
    """
    Generate an evidence-grounded answer with deterministic citation resolution (legacy baseline).
    """
    if not evidence:
        return GroundedAnswer(
            question=question,
            answer_text="Based on the provided archive evidence, there is insufficient information to answer this question.",
            citations=[],
            evidence_used=[],
            tokens_used=0,
            model_used=model or "",
            evidence_count=0,
        )

    if llm is None:
        llm = get_llm_provider()

    formatted_context, evidence_map = build_evidence_context(evidence)

    user_prompt = QA_USER_PROMPT_TEMPLATE.format(
        question=question,
        context=formatted_context,
    )

    try:
        response = llm.generate(
            prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT_BASELINE_RAG,
            model=model,
        )
        answer_text = response.content.strip()
        tokens_used = response.tokens_used
        model_used = response.model
    except GeminiQuotaExhaustedError as eq:
        logger.error("Gemini API quota exhausted in generate_grounded_answer: %s", eq)
        return GroundedAnswer(
            question=question,
            answer_text="⚠️ Gemini API quota is exhausted across all configured keys. Please check or refresh GEMINI_API_KEYS in .env.",
            citations=[],
            evidence_used=[],
            tokens_used=0,
            model_used=model or "",
            evidence_count=len(evidence),
        )

    # Extract all cited [EVIDENCE_X] or EVIDENCE_X mentions
    cited_ids = set(re.findall(r"EVIDENCE_(\d+)", answer_text))
    citations: List[Citation] = []
    used_ids: List[str] = []

    for cid in sorted(cited_ids, key=int):
        ev_key = f"EVIDENCE_{cid}"
        if ev_key in evidence_map:
            res = evidence_map[ev_key]
            citations.append(
                Citation(
                    evidence_id=ev_key,
                    document_title=res.document_title or "Ashen Era Archive Document",
                    source_path=res.source_path,
                    page=res.page_start,
                    excerpt=res.content[:200] + "..." if len(res.content) > 200 else res.content,
                )
            )
            used_ids.append(ev_key)

    return GroundedAnswer(
        question=question,
        answer_text=answer_text,
        citations=citations,
        evidence_used=used_ids,
        tokens_used=tokens_used,
        model_used=model_used,
        evidence_count=len(evidence),
    )


def answer_question(
    query: str,
    config: Optional[RetrievalConfig] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    reranker_provider: Optional[RerankerProvider] = None,
    llm: Optional[LLMProvider] = None,
    graph: Optional[KnowledgeGraph] = None,
    model: Optional[str] = None,
    source_category: Optional[str] = None,
) -> FinalAnswer:
    """
    End-to-End Answer Pipeline (Phase 6):
    1. Query Analysis (heuristics / decomposition)
    2. Multi-Hop Hybrid Retrieval (orchestrator)
    3. Register Evidence with EvidenceManager (assign sequential deterministic IDs)
    4. Source Classification (epistemological metadata)
    5. Conflict Detection (contradiction / qualification analysis)
    6. Evidence Sufficiency Scoring (with immediate refusal fast-path if INSUFFICIENT)
    7. Enhanced Context Construction (evidence IDs, conflicts, source notes)
    8. LLM Grounded Answer Generation
    9. Automated Post-Generation Answer Verification
    10. Deterministic Citation Resolution
    11. Return FinalAnswer with complete observability trace
    """
    start_time = time.time()
    trace: Dict[str, Any] = {"query": query, "start_time": start_time}

    if config is None:
        config = RetrievalConfig(enable_multihop=True)

    if llm is None:
        llm = get_llm_provider()

    # Step 1: Query Analysis
    q_analysis = analyze_query(query)
    trace["query_type"] = q_analysis.query_type
    trace["entities"] = q_analysis.entities_mentioned

    # Step 2: Retrieval
    retrieval_start = time.time()
    if config.enable_multihop and (q_analysis.query_type == "multi_hop" or q_analysis.sub_questions):
        state = retrieve_with_multihop(
            query=query,
            query_analysis=q_analysis,
            config=config,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
            graph=graph,
            source_category=source_category,
            llm=llm,
        )
        candidates = state.retrieved_evidence
        trace["retrieval_hops"] = len(state.traversal_hops)
    else:
        candidates = retrieve(
            query=query,
            query_analysis=q_analysis,
            config=config,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
            source_category=source_category,
        )
        trace["retrieval_hops"] = 1
    trace["retrieval_time_s"] = round(time.time() - retrieval_start, 2)
    trace["candidate_count"] = len(candidates)

    # Step 3: Register in EvidenceManager & Deduplicate
    manager = EvidenceManager()
    for c in candidates:
        manager.register_evidence(c)
    manager.deduplicate()
    trace["evidence_count"] = len(manager.evidence)

    # Step 4: Conflict Detection
    conflict_start = time.time()
    conflicts = detect_conflicts(manager.evidence, llm=llm)
    trace["conflict_count"] = len(conflicts)
    trace["conflict_detection_time_s"] = round(time.time() - conflict_start, 2)

    # Step 5: Evidence Sufficiency Assessment
    sufficiency = assess_evidence_sufficiency(query, manager, llm=llm)
    trace["sufficiency_level"] = sufficiency.level
    trace["sufficiency_coverage"] = sufficiency.coverage

    # Check if Voyage AI embedding provider is degraded or exhausted
    warning_msg: Optional[str] = None
    try:
        chk_provider = embedding_provider or get_embedding_provider()
        if hasattr(chk_provider, "is_available") and not chk_provider.is_available:
            warning_msg = "⚠️ Voyage AI embedding quota is unconfigured or exhausted; semantic vector search fell back to BM25 lexical and Neo4j graph search."
    except Exception:
        pass

    # Early exit fast-path: Refuse if evidence is completely INSUFFICIENT
    if sufficiency.level == "INSUFFICIENT" or not manager.evidence:
        elapsed = round(time.time() - start_time, 2)
        trace["elapsed_time_s"] = elapsed
        trace["tokens_used"] = 0
        return FinalAnswer(
            question=query,
            answer_text="Based on the provided archive evidence, there is insufficient information to answer this question.",
            raw_answer_text="Based on the provided archive evidence, there is insufficient information to answer this question.",
            evidence=manager.evidence,
            citations=[],
            conflicts=conflicts,
            evidence_status="INSUFFICIENT",
            verification_result=VerificationResult(
                is_verified=True,
                claim_checks=[],
                citation_issues=[],
                conflict_acknowledgements=[],
                unsupported_claims=[],
            ),
            query_trace=trace,
            warning=warning_msg,
        )

    # Step 6: Build Enhanced Context
    formatted_context, _ = build_evidence_context(
        evidence=manager,
        conflicts=conflicts,
        sufficiency=sufficiency,
    )

    # Step 7: LLM Answer Generation
    gen_start = time.time()
    user_prompt = QA_USER_PROMPT_PHASE6_TEMPLATE.format(
        question=query,
        context=formatted_context,
    )

    try:
        response = llm.generate(
            prompt=user_prompt,
            system_prompt=SYSTEM_PROMPT_PHASE6,
            model=model,
        )
        raw_answer = response.content.strip()
        if not raw_answer:
            raw_answer = "The archive search retrieved relevant evidence, but the language model was unable to generate a synthesized response. Please check API key quotas or network connectivity."
        trace["generation_time_s"] = round(time.time() - gen_start, 2)
        trace["tokens_used"] = response.tokens_used
        trace["model_used"] = response.model
    except GeminiQuotaExhaustedError as eq:
        logger.error("Gemini API quota exhausted during answer generation: %s", eq)
        raw_answer = "⚠️ Gemini API quota is exhausted across all configured keys. Please check or refresh your GEMINI_API_KEYS in .env."
        trace["generation_time_s"] = round(time.time() - gen_start, 2)
        trace["tokens_used"] = 0
        trace["model_used"] = model or "unknown"
        trace["error"] = "API_QUOTA_EXHAUSTED"
        trace["elapsed_time_s"] = round(time.time() - start_time, 2)
        return FinalAnswer(
            question=query,
            answer_text=raw_answer,
            raw_answer_text=raw_answer,
            evidence=manager.evidence,
            citations=[],
            conflicts=conflicts,
            evidence_status="API_QUOTA_EXHAUSTED",
            verification_result=VerificationResult(
                is_verified=False,
                claim_checks=[],
                citation_issues=[],
                conflict_acknowledgements=[],
                unsupported_claims=["API Quota Exhausted"],
            ),
            query_trace=trace,
            warning="⚠️ Gemini API quota is exhausted across all configured keys. Please check or refresh GEMINI_API_KEYS in .env.",
        )

    # Step 8: Answer Verification
    verify_start = time.time()
    v_result = verify_answer(
        answer=raw_answer,
        evidence_manager=manager,
        llm=llm,
        conflicts=conflicts,
    )
    trace["verification_time_s"] = round(time.time() - verify_start, 2)
    trace["is_verified"] = v_result.is_verified

    # Step 9: Citation Resolution
    resolver = CitationResolver(manager)
    resolved_answer = resolver.resolve_citations(raw_answer)

    # Compile structured citation details for all cited tokens
    cited_nums = set(re.findall(r"EVIDENCE_(\d+)", raw_answer, flags=re.IGNORECASE))
    resolved_citations: List[Dict[str, Any]] = []
    for cid in sorted(cited_nums, key=int):
        full_eid = f"EVIDENCE_{int(cid):03d}"
        details = resolver.get_citation_details(full_eid)
        if details.get("found"):
            resolved_citations.append(details)

    elapsed_total = round(time.time() - start_time, 2)
    trace["elapsed_time_s"] = elapsed_total

    return FinalAnswer(
        question=query,
        answer_text=resolved_answer,
        raw_answer_text=raw_answer,
        evidence=manager.evidence,
        citations=resolved_citations,
        conflicts=conflicts,
        evidence_status=sufficiency.level,
        verification_result=v_result,
        query_trace=trace,
        warning=warning_msg,
    )
