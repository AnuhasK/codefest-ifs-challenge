import time
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.config import BASE_DIR, CORPUS_PATH
from src.retrieval.orchestrator import retrieve, RetrievalConfig
from src.retrieval.query_analyzer import analyze_query
from src.generation.answer import generate_grounded_answer, GroundedAnswer
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider
from src.providers.reranker_provider import RerankerProvider, get_reranker_provider
from src.providers.llm_provider import LLMProvider, get_llm_provider
from src.evaluation.metrics import compute_retrieval_metrics
from src.evaluation.validation import validate_citation_grounding, validate_evidence_support


class ExperimentMetrics(BaseModel):
    experiment_name: str
    description: str
    avg_recall_at_1: float
    avg_recall_at_3: float
    avg_recall_at_5: float
    avg_recall_at_10: float
    avg_mrr: float
    avg_retrieval_time_sec: float
    avg_generation_time_sec: float
    avg_tokens: float
    citation_grounded_pct: float
    evidence_supported_pct: float


class HybridQuestionResult(BaseModel):
    qid: str
    track: str
    question: str
    experiment: str
    retrieval_time_sec: float
    generation_time_sec: float
    total_time_sec: float
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    recall_at_10: float
    mrr: float
    citation_grounded: bool
    evidence_supported: bool
    retrieved_chunk_ids: List[str]
    retrieved_scores: List[float]
    retrieved_titles: List[str]
    answer: str
    citation_count: int
    citations: List[Dict[str, Any]]
    tokens_used: int


class HybridEvaluationReport(BaseModel):
    timestamp: str
    total_questions: int
    experiment_summaries: Dict[str, ExperimentMetrics]
    results: List[HybridQuestionResult]


def load_sample_questions(custom_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load sample questions from archive."""
    path = Path(custom_path) if custom_path else CORPUS_PATH / "sample_questions.json"
    if not path.exists():
        path = BASE_DIR / "Ashen_Era_Archive" / "sample_questions.json"
    if not path.exists():
        raise FileNotFoundError(f"Could not find sample_questions.json at {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


EXPERIMENT_CONFIGS = {
    "baseline_a": {
        "desc": "Standard Dense Search",
        "config": RetrievalConfig(
            enable_bm25=False,
            enable_dense=True,
            enable_contextual=False,
            enable_diversity=False,
            enable_reranker=False,
            dense_top_k=10,
        ),
    },
    "baseline_b": {
        "desc": "Contextual Dense Search",
        "config": RetrievalConfig(
            enable_bm25=False,
            enable_dense=False,
            enable_contextual=True,
            enable_diversity=False,
            enable_reranker=False,
            contextual_top_k=10,
        ),
    },
    "exp_3_bm25_dense_rrf": {
        "desc": "BM25-style FTS + Standard Dense + RRF",
        "config": RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=False,
            enable_diversity=False,
            enable_reranker=False,
            bm25_top_k=50,
            dense_top_k=50,
            rrf_top_n=10,
        ),
    },
    "exp_4_full_hybrid_no_rerank": {
        "desc": "Full Hybrid (BM25 + Standard + Contextual + RRF + Diversity, No Reranker)",
        "config": RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=True,
            enable_diversity=True,
            enable_reranker=False,
            bm25_top_k=50,
            dense_top_k=50,
            contextual_top_k=50,
            rrf_top_n=10,
        ),
    },
    "exp_5_full_hybrid_rerank": {
        "desc": "Full Hybrid + Cross-Encoder Reranker (3 Streams)",
        "config": RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=True,
            enable_entity_search=False,
            enable_diversity=True,
            enable_reranker=True,
            bm25_top_k=50,
            dense_top_k=50,
            contextual_top_k=50,
            rrf_top_n=25,
            reranker_top_k=10,
        ),
    },
    "exp_6_full_hybrid_with_entities": {
        "desc": "Full Hybrid + Neo4j Entity Search (4 Streams + RRF + Reranker)",
        "config": RetrievalConfig(
            enable_bm25=True,
            enable_dense=True,
            enable_contextual=True,
            enable_entity_search=True,
            enable_diversity=True,
            enable_reranker=True,
            bm25_top_k=50,
            dense_top_k=50,
            contextual_top_k=50,
            entity_top_k=50,
            rrf_top_n=25,
            reranker_top_k=10,
        ),
    },
}

HYBRID_EXPERIMENTS = EXPERIMENT_CONFIGS


def evaluate_single_experiment(
    q: Dict[str, Any],
    exp_name: str,
    exp_info: Dict[str, Any],
    embedding_provider: EmbeddingProvider,
    reranker_provider: RerankerProvider,
    llm: Optional[LLMProvider] = None,
    retrieval_only: bool = False,
) -> HybridQuestionResult:
    """Evaluate a single question under a specific retrieval configuration."""
    qid = q.get("qid", "unknown")
    track = q.get("track", "")
    question_text = q.get("question", "")
    config = exp_info["config"]

    q_analysis = analyze_query(question_text)

    # 1. Retrieve
    t0 = time.time()
    search_results = retrieve(
        query=question_text,
        query_analysis=q_analysis,
        config=config,
        embedding_provider=embedding_provider,
        reranker_provider=reranker_provider,
    )
    t_search = time.time() - t0

    # 2. Objective Retrieval Metrics
    res_dicts = [
        {"chunk_id": r.chunk_id, "document_title": r.document_title, "content": r.content}
        for r in search_results
    ]
    ret_metrics = compute_retrieval_metrics(res_dicts, qid)

    # 3. Answer Generation & Validation
    if retrieval_only or llm is None:
        t_gen = 0.0
        grounded_ans = GroundedAnswer(
            question=question_text,
            answer_text="[Retrieval-Only Benchmark]",
            citations=[],
            tokens_used=0,
        )
        cit_dicts = []
        grounding_val = {"citation_grounded": True}
        support_val = {"evidence_supported": True}
    else:
        t1 = time.time()
        grounded_ans = generate_grounded_answer(
            question=question_text,
            evidence=search_results,
            llm=llm,
        )
        t_gen = time.time() - t1
        cit_dicts = [c.model_dump() for c in grounded_ans.citations]
        grounding_val = validate_citation_grounding(cit_dicts, res_dicts)
        support_val = validate_evidence_support(grounded_ans.answer_text, cit_dicts, qid)

    return HybridQuestionResult(
        qid=qid,
        track=track,
        question=question_text,
        experiment=exp_name,
        retrieval_time_sec=round(t_search, 4),
        generation_time_sec=round(t_gen, 4),
        total_time_sec=round(t_search + t_gen, 4),
        recall_at_1=ret_metrics["recall_at_1"],
        recall_at_3=ret_metrics["recall_at_3"],
        recall_at_5=ret_metrics["recall_at_5"],
        recall_at_10=ret_metrics["recall_at_10"],
        mrr=ret_metrics["mrr"],
        citation_grounded=grounding_val["citation_grounded"],
        evidence_supported=support_val["evidence_supported"],
        retrieved_chunk_ids=[r.chunk_id for r in search_results],
        retrieved_scores=[round(r.score, 4) for r in search_results],
        retrieved_titles=[r.document_title or "Unknown" for r in search_results],
        answer=grounded_ans.answer_text,
        citation_count=len(grounded_ans.citations),
        citations=cit_dicts,
        tokens_used=grounded_ans.tokens_used,
    )


def run_hybrid_evaluation(
    questions: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[Path] = None,
    limit: Optional[int] = None,
    experiments_to_run: Optional[List[str]] = None,
    retrieval_only: bool = False,
    verbose: bool = False,
    qids: Optional[List[str]] = None,
) -> HybridEvaluationReport:
    """
    Run 5-way comparative evaluation across all sample questions.
    """
    if questions is None:
        questions = load_sample_questions()

    if qids:
        qid_set = set(qids)
        questions = [q for q in questions if q.get("qid") in qid_set]

    if limit is not None and limit > 0:
        questions = questions[:limit]

    embedding_provider = get_embedding_provider()
    reranker_provider = get_reranker_provider()
    llm = get_llm_provider() if not retrieval_only else None

    active_experiments = experiments_to_run or list(EXPERIMENT_CONFIGS.keys())
    all_results: List[HybridQuestionResult] = []

    mode_str = "Retrieval-Only" if retrieval_only else "End-to-End (with Generation)"
    print(f"\n--- Starting 5-Way Hybrid Evaluation ({mode_str}) on {len(questions)} Questions ---")
    print(f"Configurations to evaluate: {', '.join(active_experiments)}\n")

    for q_idx, q in enumerate(questions, start=1):
        print(f"[{q_idx}/{len(questions)}] QID: {q.get('qid')} - '{q.get('question')[:60]}...'", flush=True)
        for exp_name in active_experiments:
            exp_info = EXPERIMENT_CONFIGS[exp_name]
            res = evaluate_single_experiment(
                q=q,
                exp_name=exp_name,
                exp_info=exp_info,
                embedding_provider=embedding_provider,
                reranker_provider=reranker_provider,
                llm=llm,
                retrieval_only=retrieval_only,
            )
            all_results.append(res)
            print(
                f"   -> {exp_name:<26}: R@1={res.recall_at_1:.1f}, R@10={res.recall_at_10:.1f}, "
                f"MRR={res.mrr:.2f}, Grounded={res.citation_grounded}, Support={res.evidence_supported}",
                flush=True,
            )
            if verbose and not retrieval_only:
                print(f"      [Answer] {res.answer[:160]}...", flush=True)
                if res.retrieved_titles:
                    print(f"      [Sources] {', '.join(list(dict.fromkeys(res.retrieved_titles))[:3])}", flush=True)

    # Compute aggregate summary statistics per experiment
    summaries: Dict[str, ExperimentMetrics] = {}
    for exp_name in active_experiments:
        exp_res = [r for r in all_results if r.experiment == exp_name]
        n = len(exp_res) if exp_res else 1

        summaries[exp_name] = ExperimentMetrics(
            experiment_name=exp_name,
            description=EXPERIMENT_CONFIGS[exp_name]["desc"],
            avg_recall_at_1=round(sum(r.recall_at_1 for r in exp_res) / n, 4),
            avg_recall_at_3=round(sum(r.recall_at_3 for r in exp_res) / n, 4),
            avg_recall_at_5=round(sum(r.recall_at_5 for r in exp_res) / n, 4),
            avg_recall_at_10=round(sum(r.recall_at_10 for r in exp_res) / n, 4),
            avg_mrr=round(sum(r.mrr for r in exp_res) / n, 4),
            avg_retrieval_time_sec=round(sum(r.retrieval_time_sec for r in exp_res) / n, 4),
            avg_generation_time_sec=round(sum(r.generation_time_sec for r in exp_res) / n, 4),
            avg_tokens=round(sum(r.tokens_used for r in exp_res) / n, 1),
            citation_grounded_pct=round(sum(1.0 for r in exp_res if r.citation_grounded) / n * 100.0, 1),
            evidence_supported_pct=round(sum(1.0 for r in exp_res if r.evidence_supported) / n * 100.0, 1),
        )

    report = HybridEvaluationReport(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        total_questions=len(questions),
        experiment_summaries=summaries,
        results=all_results,
    )

    if output_dir:
        out_path = Path(output_dir) / "hybrid_evaluation_report.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        print(f"\nComplete Hybrid Evaluation Report written to {out_path}")

    return report
