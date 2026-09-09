import logging
from typing import List, Optional, Any
from pydantic import BaseModel, Field

from src.models.search import SearchResult
from src.models.query import QueryState
from src.retrieval.query_analyzer import QueryAnalysis, analyze_query, extract_entities_heuristic
from src.retrieval.bm25_search import bm25_search
from src.retrieval.dense_search import dense_search
from src.retrieval.fusion import rrf_fusion
from src.retrieval.diversity import apply_document_diversity
from src.retrieval.reranker import rerank_candidates
from src.knowledge.graph import KnowledgeGraph
from src.knowledge.multihop import multi_hop_search, hydrate_evidence_chunks
from src.knowledge.evidence import assess_evidence_sufficiency
from src.providers.embeddings import EmbeddingProvider
from src.providers.reranker_provider import RerankerProvider
from src.config import (
    BM25_TOP_K,
    DENSE_TOP_K,
    RERANKER_TOP_K,
    RRF_K,
    RETRIEVAL_MAX_CHUNKS_PER_DOC,
)

logger = logging.getLogger(__name__)


class RetrievalConfig(BaseModel):
    """Configuration options for the hybrid retrieval orchestrator."""
    bm25_top_k: int = BM25_TOP_K
    dense_top_k: int = DENSE_TOP_K
    contextual_top_k: int = DENSE_TOP_K
    rrf_k: int = RRF_K
    rrf_top_n: int = 100
    reranker_top_k: int = RERANKER_TOP_K
    max_chunks_per_doc: int = RETRIEVAL_MAX_CHUNKS_PER_DOC

    enable_bm25: bool = True
    enable_dense: bool = True
    enable_contextual: bool = True
    enable_entity_search: bool = True
    enable_diversity: bool = True
    enable_reranker: bool = True
    enable_multihop: bool = False
    multihop_max_hops: int = 3
    multihop_max_iterations: int = 2
    entity_top_k: int = 50


def retrieve(
    query: str,
    query_analysis: Optional[QueryAnalysis] = None,
    config: Optional[RetrievalConfig] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    reranker_provider: Optional[RerankerProvider] = None,
    source_category: Optional[str] = None,
) -> List[SearchResult]:
    """
    Execute the full hybrid retrieval pipeline:
    
    1. Query Analysis (heuristic or LLM-assisted)
    2. BM25-style lexical search (PostgreSQL FTS with ts_rank_cd)
    3. Dense semantic search (raw chunk embeddings)
    4. Contextual dense search (contextualized chunk embeddings)
    5. RRF Fusion across all active streams
    6. Controlled Document Diversity filter
    7. Cross-Encoder Reranking
    
    Returns:
        Ranked list of top candidate SearchResults ready for context construction.
    """
    if not query or not query.strip():
        return []

    if config is None:
        config = RetrievalConfig()

    # Step 1: Query Analysis
    if query_analysis is None:
        query_analysis = analyze_query(query)

    if config.enable_multihop and (query_analysis.query_type == "multi_hop" or query_analysis.sub_questions):
        multi_state = retrieve_with_multihop(
            query=query,
            query_analysis=query_analysis,
            config=config,
            embedding_provider=embedding_provider,
            reranker_provider=reranker_provider,
            source_category=source_category,
        )
        return multi_state.retrieved_evidence

    search_streams: List[List[SearchResult]] = []

    # Step 2: PostgreSQL FTS / BM25 Search
    if config.enable_bm25:
        # Search using optimized bm25 query or original query
        lexical_query = query_analysis.bm25_query or query
        bm25_results = bm25_search(
            query=lexical_query,
            top_k=config.bm25_top_k,
            source_category=source_category,
        )
        if bm25_results:
            search_streams.append(bm25_results)

    # Step 3: Standard Dense Search
    if config.enable_dense:
        std_dense_results = dense_search(
            query=query,
            provider=embedding_provider,
            top_k=config.dense_top_k,
            use_contextual=False,
            source_category=source_category,
        )
        if std_dense_results:
            search_streams.append(std_dense_results)

    # Step 4: Contextual Dense Search
    if config.enable_contextual:
        ctx_dense_results = dense_search(
            query=query,
            provider=embedding_provider,
            top_k=config.contextual_top_k,
            use_contextual=True,
            source_category=source_category,
        )
        if ctx_dense_results:
            search_streams.append(ctx_dense_results)

    # Step 4b: Entity Search Stream (Phase 4 Knowledge Graph Hook)
    if config.enable_entity_search:
        try:
            from src.retrieval.entity_search import entity_search
            entity_results = entity_search(
                query=query,
                query_analysis=query_analysis,
                top_k=config.entity_top_k,
                source_category=source_category,
            )
            if entity_results:
                search_streams.append(entity_results)
        except (ImportError, Exception) as e:
            logger.debug("Entity search stream bypassed or not yet implemented: %s", e)

    if not search_streams:
        return []

    # Step 5: Reciprocal Rank Fusion
    if len(search_streams) == 1:
        fused = search_streams[0][:config.rrf_top_n]
    else:
        fused = rrf_fusion(
            ranked_lists=search_streams,
            k=config.rrf_k,
            top_n=config.rrf_top_n,
        )

    # Step 6: Controlled Document Diversity Filter
    if config.enable_diversity and len(fused) > config.reranker_top_k:
        diverse_candidates = apply_document_diversity(
            candidates=fused,
            max_per_doc=config.max_chunks_per_doc,
            preserve_top_k=3,
        )
    else:
        diverse_candidates = fused

    # Step 7: Cross-Encoder Reranker
    if config.enable_reranker and diverse_candidates:
        # Score top 30 diverse candidates with cross-encoder to maintain low latency on CPU
        rerank_pool = diverse_candidates[:max(config.reranker_top_k, 30)]
        final_results = rerank_candidates(
            query=query,
            candidates=rerank_pool,
            reranker=reranker_provider,
            top_k=config.reranker_top_k,
        )
    else:
        final_results = diverse_candidates[:config.reranker_top_k]

    return final_results


def retrieve_with_multihop(
    query: str,
    query_analysis: Optional[QueryAnalysis] = None,
    config: Optional[RetrievalConfig] = None,
    embedding_provider: Optional[EmbeddingProvider] = None,
    reranker_provider: Optional[RerankerProvider] = None,
    graph: Optional[KnowledgeGraph] = None,
    source_category: Optional[str] = None,
    llm: Optional[Any] = None,
) -> QueryState:
    """
    Execute an iterative multi-hop retrieval pipeline connecting facts across documents:

    1. Initial hybrid retrieval (Hop 1).
    2. Knowledge graph traversal starting from recognized query entities.
    3. Hop 2 targeted retrieval on discovered intermediate entities & sub-questions.
    4. Edge evidence hydration from PostgreSQL.
    5. Re-fusion & cross-document diversity filtering.
    6. Cross-encoder reranking.
    7. Evidence sufficiency scoring.
    """
    if config is None:
        config = RetrievalConfig()

    if query_analysis is None:
        query_analysis = analyze_query(query)

    kg = graph or KnowledgeGraph()

    state = QueryState(
        original_query=query,
        query_type=query_analysis.query_type,
        sub_questions=query_analysis.sub_questions,
        identified_entities=query_analysis.entities_mentioned,
        max_iterations=config.multihop_max_iterations,
    )

    # 1. Hop 1: Base Hybrid Retrieval without multihop recursion
    base_config = config.model_copy(update={"enable_multihop": False})
    hop1_results = retrieve(
        query=query,
        query_analysis=query_analysis,
        config=base_config,
        embedding_provider=embedding_provider,
        reranker_provider=reranker_provider,
        source_category=source_category,
    )
    state.retrieved_evidence = hop1_results
    state.evidence_per_hop[1] = hop1_results
    state.iteration_count = 1

    # If single-hop query or no entities identified, evaluate sufficiency and return
    if query_analysis.query_type != "multi_hop" and not query_analysis.sub_questions:
        state.sufficiency = assess_evidence_sufficiency(query, state, llm=llm)
        return state

    # 2. Knowledge Graph Traversal
    seed_entities = list(query_analysis.entities_mentioned)
    if query_analysis.sub_questions:
        for sq in query_analysis.sub_questions:
            for e in extract_entities_heuristic(sq):
                if e not in seed_entities:
                    seed_entities.append(e)

    traversal_hops, discovered_entities, edge_chunk_ids = multi_hop_search(
        start_entities=seed_entities,
        graph=kg,
        max_hops=config.multihop_max_hops,
        limit=20,
    )
    state.traversal_hops = traversal_hops
    state.discovered_entities = discovered_entities

    # 3. Hydrate edge evidence chunks from Neo4j paths
    graph_evidence_chunks = hydrate_evidence_chunks(
        chunk_ids=edge_chunk_ids,
        source_category=source_category,
        stream_name="graph_multihop_edge",
    )

    # 4. Hop 2: Targeted retrieval on bridge entities and sub-questions
    bridge_entities = [
        e for e in discovered_entities
        if e not in query_analysis.entities_mentioned
    ][:5]

    hop2_query = (
        " ".join(query_analysis.sub_questions[1:])
        if len(query_analysis.sub_questions) > 1
        else query
    )
    hop2_search_query = " ".join(bridge_entities) + " " + hop2_query

    hop2_candidates: List[SearchResult] = list(graph_evidence_chunks)

    # Targeted BM25 & Dense Search for Hop 2
    targeted_bm25 = bm25_search(
        query=hop2_search_query,
        top_k=config.bm25_top_k,
        source_category=source_category,
    )
    if targeted_bm25:
        hop2_candidates.extend(targeted_bm25)

    try:
        targeted_dense = dense_search(
            query=hop2_search_query,
            provider=embedding_provider,
            top_k=config.dense_top_k,
            source_category=source_category,
        )
        if targeted_dense:
            hop2_candidates.extend(targeted_dense)
    except Exception as e:
        logger.debug("Hop 2 dense search bypassed: %s", e)

    # Entity search for top bridge entities
    if bridge_entities:
        try:
            from src.retrieval.entity_search import entity_search
            ent_results = entity_search(
                query="",
                query_entities=bridge_entities,
                graph=kg,
                top_k=config.entity_top_k,
                source_category=source_category,
            )
            if ent_results:
                hop2_candidates.extend(ent_results)
        except Exception as e:
            logger.debug("Hop 2 entity search bypassed: %s", e)

    # Deduplicate Hop 2 candidates
    seen_cids = set()
    deduped_hop2: List[SearchResult] = []
    for c in hop2_candidates:
        if c.chunk_id not in seen_cids:
            seen_cids.add(c.chunk_id)
            deduped_hop2.append(c)

    # 5. Rerank Hop 2 candidates against the Hop 2 specific query
    if config.enable_reranker and deduped_hop2:
        hop2_pool = deduped_hop2[:max(config.reranker_top_k, 30)]
        hop2_reranked = rerank_candidates(
            query=hop2_search_query,
            candidates=hop2_pool,
            reranker=reranker_provider,
            top_k=config.reranker_top_k,
        )
    else:
        hop2_reranked = deduped_hop2[:config.reranker_top_k]

    state.evidence_per_hop[2] = hop2_reranked

    # 6. Interleave Hop 1 and Hop 2 candidates to ensure multi-document representation
    final_interleaved: List[SearchResult] = []
    seen_final = set()
    h1_idx, h2_idx = 0, 0
    max_target = config.reranker_top_k * 2

    while (h1_idx < len(hop1_results) or h2_idx < len(hop2_reranked)) and len(final_interleaved) < max_target:
        if h1_idx < len(hop1_results):
            c1 = hop1_results[h1_idx]
            h1_idx += 1
            if c1.chunk_id not in seen_final:
                seen_final.add(c1.chunk_id)
                final_interleaved.append(c1)
        if h2_idx < len(hop2_reranked) and len(final_interleaved) < max_target:
            c2 = hop2_reranked[h2_idx]
            h2_idx += 1
            if c2.chunk_id not in seen_final:
                seen_final.add(c2.chunk_id)
                final_interleaved.append(c2)

    # 7. Controlled Document Diversity Filter
    if config.enable_diversity and len(final_interleaved) > config.reranker_top_k:
        final_results = apply_document_diversity(
            candidates=final_interleaved,
            max_per_doc=config.max_chunks_per_doc,
            preserve_top_k=2,
        )[:config.reranker_top_k]
    else:
        final_results = final_interleaved[:config.reranker_top_k]

    state.retrieved_evidence = final_results

    # 8. Assess evidence sufficiency
    state.sufficiency = assess_evidence_sufficiency(query, state, llm=llm)

    return state

