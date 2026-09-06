import os
import sys
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Any

# Silence verbose SDK logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.generation.answer import answer_question
from src.retrieval.orchestrator import RetrievalConfig
from src.providers.embeddings import get_embedding_provider
from src.providers.reranker_provider import get_reranker_provider
from src.providers.llm_provider import get_llm_provider

logger = logging.getLogger(__name__)

GENERATION_EVALUATION_QUESTIONS = [
    {
        "id": "q1_multihop_ederon",
        "question": "Which accord was ultimately won by the faction of which Ederon Fellgard is a member?",
        "expected_type": "multi_hop",
        "category": "multi_hop_track1b",
    },
    {
        "id": "q2_multihop_isolde",
        "question": "Which war did the faction containing Isolde Fellgard win?",
        "expected_type": "multi_hop",
        "category": "multi_hop_track1b",
    },
    {
        "id": "q3_conflict_fenspire",
        "question": "What do the archive records and ballad state regarding Fenspire in the Gloaming Reach?",
        "expected_type": "conflict",
        "category": "contradiction_nuance",
    },
    {
        "id": "q4_insufficient_lore",
        "question": "Where is the lost sapphire crown of the ancient star emperor hidden?",
        "expected_type": "insufficient",
        "category": "insufficient_evidence",
    },
]


def run_generation_evaluation() -> Dict[str, Any]:
    print("=" * 80)
    print("GENERATION & GROUNDING BENCHMARK EVALUATION: EVIDENCE QUALITY, CITATIONS & VERIFICATION")
    print("=" * 80)

    cfg = RetrievalConfig(
        enable_bm25=True,
        enable_dense=True,
        enable_contextual=True,
        enable_diversity=True,
        enable_reranker=True,
        enable_multihop=True,
        bm25_top_k=50,
        dense_top_k=50,
        contextual_top_k=50,
        rrf_top_n=25,
        reranker_top_k=6,
    )

    embed_provider = get_embedding_provider()
    reranker = get_reranker_provider()
    llm = get_llm_provider()

    results: List[Dict[str, Any]] = []
    latencies: List[float] = []

    total_citations_count = 0
    valid_citations_count = 0
    total_conflicts_detected = 0
    total_answers_evaluated = 0
    verified_answers_count = 0
    total_claims_count = 0
    unsupported_claims_count = 0

    for item in GENERATION_EVALUATION_QUESTIONS:
        qid = item["id"]
        q_text = item["question"]
        print(f"\nEvaluating [{qid}]: \"{q_text}\"")

        start_t = time.time()
        final_ans = answer_question(
            query=q_text,
            config=cfg,
            embedding_provider=embed_provider,
            reranker_provider=reranker,
            llm=llm,
        )
        elapsed = round(time.time() - start_t, 2)
        latencies.append(elapsed)

        total_answers_evaluated += 1

        # Check citations
        cit_issues = final_ans.verification_result.citation_issues if final_ans.verification_result else []
        cits = final_ans.citations
        total_citations_count += len(cits)
        valid_citations_count += max(0, len(cits) - len(cit_issues))

        # Check conflicts
        conflicts = final_ans.conflicts
        total_conflicts_detected += len(conflicts)

        # Check verification
        vr = final_ans.verification_result
        if vr:
            if vr.is_verified:
                verified_answers_count += 1
            total_claims_count += len(vr.claim_checks)
            unsupported_claims_count += len(vr.unsupported_claims)

        print(f"  -> Latency: {elapsed}s (Target: <45s)")
        print(f"  -> Evidence Status: {final_ans.evidence_status}")
        print(f"  -> Citations Resolved: {len(final_ans.citations)}")
        print(f"  -> Conflicts Detected: {len(conflicts)}")
        print(f"  -> Verified: {'YES' if (vr and vr.is_verified) else 'NO'}")

        results.append({
            "id": qid,
            "question": q_text,
            "category": item["category"],
            "latency_s": elapsed,
            "evidence_status": final_ans.evidence_status,
            "citation_count": len(final_ans.citations),
            "conflict_count": len(conflicts),
            "is_verified": vr.is_verified if vr else False,
            "unsupported_claim_count": len(vr.unsupported_claims) if vr else 0,
            "resolved_answer_snippet": final_ans.answer_text[:250] + "..." if len(final_ans.answer_text) > 250 else final_ans.answer_text,
        })

    # Compute key metrics
    citation_accuracy = (
        round(valid_citations_count / total_citations_count * 100, 2)
        if total_citations_count > 0
        else 100.0
    )
    verification_pass_rate = (
        round(verified_answers_count / total_answers_evaluated * 100, 2)
        if total_answers_evaluated > 0
        else 0.0
    )
    unsupported_claim_rate = (
        round(unsupported_claims_count / total_claims_count * 100, 2)
        if total_claims_count > 0
        else 0.0
    )
    avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0.0
    max_latency = round(max(latencies), 2) if latencies else 0.0

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_questions": len(GENERATION_EVALUATION_QUESTIONS),
        "metrics": {
            "citation_accuracy_pct": citation_accuracy,
            "total_conflicts_detected": total_conflicts_detected,
            "verification_pass_rate_pct": verification_pass_rate,
            "unsupported_claim_rate_pct": unsupported_claim_rate,
            "average_latency_s": avg_latency,
            "max_latency_s": max_latency,
            "latency_under_45s_pass": (max_latency < 45.0),
        },
        "question_results": results,
    }

    # Save outputs
    out_dir = Path("evaluation_results")
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "generation_evaluation_report.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    summary_path = out_dir / "generation_evaluation_summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("# Generation & Grounding Evaluation Summary: Evidence Quality + Claims\n\n")
        f.write(f"- **Generated**: {report['timestamp']}\n")
        f.write(f"- **Total Questions Evaluated**: {report['total_questions']}\n\n")
        f.write("## Key Performance Metrics\n\n")
        f.write(f"| Metric | Result | Target / Standard |\n")
        f.write(f"|---|---|---|\n")
        f.write(f"| **Citation Accuracy** | `{citation_accuracy}%` | 100% of citations map to retrieved documents |\n")
        f.write(f"| **Conflicts Detected** | `{total_conflicts_detected}` | Disagreements surfaced with opposing sources |\n")
        f.write(f"| **Verification Pass Rate** | `{verification_pass_rate}%` | Factually supported claims + conflict acknowledged |\n")
        f.write(f"| **Unsupported Claim Rate** | `{unsupported_claim_rate}%` | < 10% unsupported assertions |\n")
        f.write(f"| **Average Latency** | `{avg_latency}s` | < 45s for 1B queries |\n")
        f.write(f"| **Max Latency** | `{max_latency}s` | < 45s threshold |\n\n")
        f.write("## Question Breakdown\n\n")
        f.write("| ID | Category | Evidence Status | Latency | Verified | Citations | Conflicts |\n")
        f.write("|---|---|---|---|---|---|---|\n")
        for r in results:
            ver = "YES" if r["is_verified"] else "NO"
            f.write(f"| `{r['id']}` | {r['category']} | {r['evidence_status']} | {r['latency_s']}s | {ver} | {r['citation_count']} | {r['conflict_count']} |\n")

    print("\n" + "=" * 80)
    print("GENERATION & GROUNDING EVALUATION COMPLETE")
    print(f"Citation Accuracy          : {citation_accuracy}%")
    print(f"Conflicts Detected         : {total_conflicts_detected}")
    print(f"Verification Pass Rate     : {verification_pass_rate}%")
    print(f"Unsupported Claim Rate     : {unsupported_claim_rate}%")
    print(f"Average Latency            : {avg_latency}s (Max: {max_latency}s - Target < 45s: {'MET' if max_latency < 45.0 else 'FAILED'})")
    print(f"Report saved to            : {json_path}")
    print(f"Summary saved to           : {summary_path}")
    print("=" * 80 + "\n")

    return report


if __name__ == "__main__":
    run_generation_evaluation()
