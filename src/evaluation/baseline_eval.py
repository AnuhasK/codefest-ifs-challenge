import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.config import BASE_DIR, CORPUS_PATH
from src.retrieval.dense_search import dense_search
from src.generation.answer import generate_grounded_answer, GroundedAnswer
from src.providers.embeddings import EmbeddingProvider, get_embedding_provider
from src.providers.llm_provider import LLMProvider, get_llm_provider


class QuestionEvaluationResult(BaseModel):
    qid: str
    track: str
    question: str
    experiment: str  # 'standard' or 'contextual'
    retrieval_time_sec: float
    generation_time_sec: float
    total_time_sec: float
    retrieved_chunk_ids: List[str]
    retrieved_scores: List[float]
    retrieved_titles: List[str]
    answer: str
    citation_count: int
    citations: List[Dict[str, Any]]
    tokens_used: int


class BaselineExperimentReport(BaseModel):
    timestamp: str
    total_questions: int
    standard_avg_retrieval_sec: float
    contextual_avg_retrieval_sec: float
    standard_avg_tokens: float
    contextual_avg_tokens: float
    results: List[QuestionEvaluationResult]


def load_sample_questions(custom_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load sample questions from archive."""
    path = Path(custom_path) if custom_path else CORPUS_PATH / "sample_questions.json"
    if not path.exists():
        path = BASE_DIR / "Ashen_Era_Archive" / "sample_questions.json"
    if not path.exists():
        raise FileNotFoundError(f"Could not find sample_questions.json at {path}")

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def evaluate_question(
    q: Dict[str, Any],
    use_contextual: bool,
    top_k: int = 10,
    provider: Optional[EmbeddingProvider] = None,
    llm: Optional[LLMProvider] = None,
) -> QuestionEvaluationResult:
    """Evaluate a single question using standard or contextual retrieval."""
    qid = q.get("qid", "unknown")
    track = q.get("track", "")
    question_text = q.get("question", "")
    exp_name = "contextual" if use_contextual else "standard"

    t0 = time.time()
    search_results = dense_search(
        query=question_text,
        provider=provider,
        top_k=top_k,
        use_contextual=use_contextual,
    )
    t_search = time.time() - t0

    t1 = time.time()
    grounded_ans: GroundedAnswer = generate_grounded_answer(
        question=question_text,
        evidence=search_results,
        llm=llm,
    )
    t_gen = time.time() - t1
    t_total = time.time() - t0

    return QuestionEvaluationResult(
        qid=qid,
        track=track,
        question=question_text,
        experiment=exp_name,
        retrieval_time_sec=round(t_search, 4),
        generation_time_sec=round(t_gen, 4),
        total_time_sec=round(t_total, 4),
        retrieved_chunk_ids=[r.chunk_id for r in search_results],
        retrieved_scores=[round(r.score, 4) for r in search_results],
        retrieved_titles=[r.document_title or "Unknown" for r in search_results],
        answer=grounded_ans.answer_text,
        citation_count=len(grounded_ans.citations),
        citations=[c.model_dump() for c in grounded_ans.citations],
        tokens_used=grounded_ans.tokens_used,
    )


def run_baseline_evaluation(
    questions: Optional[List[Dict[str, Any]]] = None,
    top_k: int = 10,
    output_dir: Optional[Path] = None,
) -> BaselineExperimentReport:
    """
    Run Experiment A (Standard Dense) and Experiment B (Contextual Dense) across sample questions.
    """
    if questions is None:
        questions = load_sample_questions()

    provider = get_embedding_provider()
    llm = get_llm_provider()

    all_results: List[QuestionEvaluationResult] = []

    print(f"Starting baseline evaluation on {len(questions)} questions...")

    for i, q in enumerate(questions, start=1):
        print(f"[{i}/{len(questions)}] Evaluating QID: {q.get('qid')} - '{q.get('question')[:50]}...'")
        # Experiment A: Standard
        res_a = evaluate_question(q, use_contextual=False, top_k=top_k, provider=provider, llm=llm)
        all_results.append(res_a)

        # Experiment B: Contextual
        res_b = evaluate_question(q, use_contextual=True, top_k=top_k, provider=provider, llm=llm)
        all_results.append(res_b)

    std_res = [r for r in all_results if r.experiment == "standard"]
    ctx_res = [r for r in all_results if r.experiment == "contextual"]

    std_avg_ret = sum(r.retrieval_time_sec for r in std_res) / len(std_res) if std_res else 0.0
    ctx_avg_ret = sum(r.retrieval_time_sec for r in ctx_res) / len(ctx_res) if ctx_res else 0.0
    std_avg_tok = sum(r.tokens_used for r in std_res) / len(std_res) if std_res else 0.0
    ctx_avg_tok = sum(r.tokens_used for r in ctx_res) / len(ctx_res) if ctx_res else 0.0

    report = BaselineExperimentReport(
        timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
        total_questions=len(questions),
        standard_avg_retrieval_sec=round(std_avg_ret, 4),
        contextual_avg_retrieval_sec=round(ctx_avg_ret, 4),
        standard_avg_tokens=round(std_avg_tok, 2),
        contextual_avg_tokens=round(ctx_avg_tok, 2),
        results=all_results,
    )

    if output_dir:
        out_path = Path(output_dir) / "baseline_evaluation_report.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report.model_dump_json(indent=2))
        print(f"Evaluation report written to {out_path}")

    return report
