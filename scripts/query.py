import sys
import logging
import argparse
from pathlib import Path

# Silence verbose SDK HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.retrieval.orchestrator import retrieve, RetrievalConfig
from src.retrieval.query_analyzer import analyze_query
from src.generation.answer import generate_grounded_answer
from src.providers.embeddings import get_embedding_provider
from src.providers.reranker_provider import get_reranker_provider
from src.providers.llm_provider import get_llm_provider
from src.evaluation.validation import validate_citation_grounding, validate_evidence_support


def execute_single_query(
    query_text: str,
    cfg: RetrievalConfig,
    config_name: str,
    top_k: int,
    retrieval_only: bool,
    embed_provider,
    reranker,
    llm,
):
    print("\n" + "=" * 80)
    print(f"QUERY: {query_text}")
    print("=" * 80)

    # 1. Query Analysis
    q_analysis = analyze_query(query_text)
    print("\n[Query Analysis]")
    print(f"  Classification : {q_analysis.query_type.upper()}")
    if q_analysis.entities_mentioned:
        print(f"  Entities       : {', '.join(q_analysis.entities_mentioned)}")
    print(f"  BM25 Query     : '{q_analysis.bm25_query}'")

    # 2. Retrieve
    print(f"\n[Retrieving Candidates (Mode: {config_name.upper()})...]")
    results = retrieve(
        query=query_text,
        query_analysis=q_analysis,
        config=cfg,
        embedding_provider=embed_provider,
        reranker_provider=reranker,
    )

    print(f"\n[Retrieved Evidence Chunks ({len(results)})]")
    print("-" * 80)
    for idx, r in enumerate(results, start=1):
        doc = r.document_title or "Unknown Document"
        sources = r.metadata.get("sources", [])
        src = f" [via {', '.join(sources)}]" if sources else ""
        print(f"\n  #{idx} | Score: {r.score:.4f} | Document: {doc}{src}")
        print(f"  Chunk ID: {r.chunk_id}")
        snippet = r.content.replace("\n", " ").strip()
        if len(snippet) > 220:
            snippet = snippet[:220] + "..."
        print(f"  Excerpt : \"{snippet}\"")
    print("-" * 80)

    if retrieval_only:
        print("\n[Retrieval-Only complete. Skipped answer generation.]\n")
        return

    # 3. Generate Grounded Answer
    print("\n[Generating Grounded Answer with Citations...]")
    ans = generate_grounded_answer(
        question=query_text,
        evidence=results,
        llm=llm,
    )

    print("\n" + "=" * 80)
    print("ANSWER:")
    print("=" * 80)
    print(ans.answer_text)
    print("\n" + "-" * 80)
    print(f"Tokens Used: {ans.tokens_used} | Model: {ans.model_used}")

    if ans.citations:
        print("\n[Resolved Citations]")
        for c in ans.citations:
            pg = f", p. {c.page}" if c.page else ""
            print(f"  [{c.evidence_id}] {c.document_title}{pg}")
            print(f"       Supporting quote: \"{c.excerpt}\"")

    # 4. Validation
    res_dicts = [
        {"chunk_id": r.chunk_id, "document_title": r.document_title, "content": r.content}
        for r in results
    ]
    cit_dicts = [c.model_dump() for c in ans.citations]
    g_val = validate_citation_grounding(cit_dicts, res_dicts)
    s_val = validate_evidence_support(ans.answer_text, cit_dicts)

    print("\n[Grounding Validation]")
    print(f"  Citation Grounded : {'YES (All citations map to retrieved passages)' if g_val['citation_grounded'] else 'NO'}")
    print(f"  Evidence Supported: {'YES (Key claim entities present in cited excerpts)' if s_val['evidence_supported'] else 'NO'}")
    print("=" * 80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Query the Ashen Era Archive using Hybrid Retrieval & RAG."
    )
    parser.add_argument(
        "query",
        type=str,
        nargs="*",
        default=[],
        help="One or more question strings to search for in the archive.",
    )
    parser.add_argument(
        "--questions",
        nargs="+",
        default=[],
        help="List of questions to execute in batch.",
    )
    parser.add_argument(
        "--config",
        choices=["hybrid", "bm25", "dense", "rrf"],
        default="hybrid",
        help="Retrieval pipeline configuration (default: 'hybrid' = BM25 + Dense + RRF + Diversity + FlashRank).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of top evidence chunks to retrieve and display (default: 5).",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip LLM answer generation and display only retrieved evidence chunks.",
    )
    args = parser.parse_args()

    # Collect questions
    questions = args.questions if args.questions else args.query
    if isinstance(questions, str):
        questions = [questions]
    if not questions:
        print("Please provide at least one question to query.")
        parser.print_help()
        return

    # Configure retrieval mode
    if args.config == "bm25":
        cfg = RetrievalConfig(
            enable_bm25=True,
            enable_dense=False,
            enable_contextual=False,
            enable_diversity=False,
            enable_reranker=False,
            bm25_top_k=args.top_k,
        )
    elif args.config == "dense":
        cfg = RetrievalConfig(
            enable_bm25=False,
            enable_dense=False,
            enable_contextual=True,
            enable_diversity=False,
            enable_reranker=False,
            contextual_top_k=args.top_k,
        )
    elif args.config == "rrf":
        cfg = RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=True,
            enable_diversity=True,
            enable_reranker=False,
            bm25_top_k=50,
            dense_top_k=50,
            contextual_top_k=50,
            rrf_top_n=args.top_k,
        )
    else:  # hybrid
        cfg = RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=True,
            enable_diversity=True,
            enable_reranker=True,
            bm25_top_k=50,
            dense_top_k=50,
            contextual_top_k=50,
            rrf_top_n=25,
            reranker_top_k=args.top_k,
        )

    embed_provider = get_embedding_provider()
    reranker = get_reranker_provider() if cfg.enable_reranker else None
    llm = get_llm_provider() if not args.retrieval_only else None

    for q in questions:
        execute_single_query(
            query_text=q,
            cfg=cfg,
            config_name=args.config,
            top_k=args.top_k,
            retrieval_only=args.retrieval_only,
            embed_provider=embed_provider,
            reranker=reranker,
            llm=llm,
        )


if __name__ == "__main__":
    main()
