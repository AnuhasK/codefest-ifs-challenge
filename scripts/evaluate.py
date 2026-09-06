import sys
import logging
import argparse
from pathlib import Path

# Quiet down third-party SDK informational logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import BASE_DIR
from src.evaluation.baseline_eval import run_baseline_evaluation
from src.evaluation.hybrid_eval import run_hybrid_evaluation


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Ashen Era Retrieval & RAG Performance."
    )
    parser.add_argument(
        "--mode",
        choices=["hybrid", "baseline", "all"],
        default="hybrid",
        help="Evaluation mode: 'hybrid' (5-way Phase 3 benchmark), 'baseline' (Phase 2), or 'all'.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of sample questions to evaluate (default: all).",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(BASE_DIR / "evaluation_results"),
        help="Directory to save JSON benchmark reports.",
    )
    parser.add_argument(
        "--experiments",
        nargs="+",
        default=None,
        help="Optional specific experiment names to run (e.g. baseline_a exp_5_full_hybrid_rerank).",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Run retrieval only (computes Recall@K, MRR without calling LLM for answer generation).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show generated answers and retrieved sources in terminal output.",
    )
    parser.add_argument(
        "--qids",
        nargs="+",
        default=None,
        help="Filter evaluation to specific question IDs (e.g. 1b_007 1b_006 1b_022).",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)

    if args.mode in ("baseline", "all"):
        print("\n================ RUNNING PHASE 2 BASELINE EVALUATION ================")
        baseline_report = run_baseline_evaluation(
            output_dir=out_dir,
            limit=args.limit,
        )
        print("\n================ BASELINE EVALUATION SUMMARY ================")
        print(f"Total Questions Evaluated: {baseline_report.total_questions}")
        print(f"Standard Dense Avg Retrieval Time:   {baseline_report.standard_avg_retrieval_sec}s")
        print(f"Contextual Dense Avg Retrieval Time: {baseline_report.contextual_avg_retrieval_sec}s")
        print(f"Standard Dense Avg Tokens:           {baseline_report.standard_avg_tokens}")
        print(f"Contextual Dense Avg Tokens:         {baseline_report.contextual_avg_tokens}")
        print("============================================================\n")

    if args.mode in ("hybrid", "all"):
        hybrid_report = run_hybrid_evaluation(
            output_dir=out_dir,
            limit=args.limit,
            experiments_to_run=args.experiments,
            retrieval_only=args.retrieval_only,
            verbose=args.verbose,
            qids=args.qids,
        )

        print("\n" + "=" * 92)
        print(f"{'PHASE 3 HYBRID RETRIEVAL BENCHMARK SUMMARY':^92}")
        print("=" * 92)
        print(
            f"{'Experiment':<30} | {'R@1':<5} | {'R@3':<5} | {'R@5':<5} | {'R@10':<5} | {'MRR':<5} | {'Grounded':<8} | {'Lat(s)':<6}"
        )
        print("-" * 92)

        for name, m in hybrid_report.experiment_summaries.items():
            print(
                f"{name:<30} | {m.avg_recall_at_1:<5.2f} | {m.avg_recall_at_3:<5.2f} | "
                f"{m.avg_recall_at_5:<5.2f} | {m.avg_recall_at_10:<5.2f} | {m.avg_mrr:<5.2f} | "
                f"{f'{m.citation_grounded_pct:.0f}%':<8} | {m.avg_retrieval_time_sec:<6.3f}"
            )
        print("=" * 92 + "\n")


if __name__ == "__main__":
    main()
