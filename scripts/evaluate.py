import sys
import argparse
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import BASE_DIR
from src.evaluation.baseline_eval import run_baseline_evaluation


def main():
    parser = argparse.ArgumentParser(description="Evaluate Baseline RAG (Standard vs Contextual Retrieval).")
    parser.add_argument("--top-k", type=int, default=10, help="Top-K evidence passages to retrieve.")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of sample questions to evaluate.")
    parser.add_argument("--output-dir", type=str, default=str(BASE_DIR / "evaluation_results"), help="Directory to save JSON report.")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    report = run_baseline_evaluation(
        top_k=args.top_k,
        output_dir=out_dir,
    )

    print("\n================ BASELINE EVALUATION SUMMARY ================")
    print(f"Total Questions Evaluated: {report.total_questions}")
    print(f"Standard Dense Avg Retrieval Time:   {report.standard_avg_retrieval_sec}s")
    print(f"Contextual Dense Avg Retrieval Time: {report.contextual_avg_retrieval_sec}s")
    print(f"Standard Dense Avg Tokens:           {report.standard_avg_tokens}")
    print(f"Contextual Dense Avg Tokens:         {report.contextual_avg_tokens}")
    print("============================================================\n")


if __name__ == "__main__":
    main()
