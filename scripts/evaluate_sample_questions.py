import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to Python sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Silence noisy SDK and HTTP logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("google_genai").setLevel(logging.WARNING)
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from src.config import CORPUS_PATH, LLM_MODEL
from src.database.postgres import get_db_connection
from src.generation.answer import answer_question
from src.retrieval.orchestrator import RetrievalConfig
from src.api.routes.query import resolve_asset_file_path, fetch_assets_for_evidence

QUESTIONS_PATH = BASE_DIR / "Ashen_Era_Archive" / "sample_questions.json"
OUTPUT_DIR = BASE_DIR / "evaluation_results"
OUTPUT_JSON_PATH = OUTPUT_DIR / "sample_questions_evaluation.json"
OUTPUT_SUMMARY_PATH = OUTPUT_DIR / "sample_questions_evaluation_summary.md"
API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")


def find_assets_for_query(q_text: str, existing_assets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Ensure visual assets (Track 1A figure plates, heraldry, portraits) mentioned in
    the query or relevant entities are included even if chunk metadata didn't tag asset_id.
    """
    existing_ids = {a.get("asset_id") for a in existing_assets if a.get("asset_id")}
    found: List[Dict[str, Any]] = []

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asset_type, entity_name, file_path, description, extracted_data FROM assets"
                )
                rows = cur.fetchall()
                q_clean = q_text.lower().replace("'", "").replace('"', "")
                for r in rows:
                    ename = (r.get("entity_name") or "").lower().replace("'", "").replace('"', "")
                    if ename and len(ename) > 3 and ename in q_clean:
                        aid_str = str(r["id"])
                        if aid_str not in existing_ids:
                            existing_ids.add(aid_str)
                            resolved_path = resolve_asset_file_path(r["file_path"])
                            found.append({
                                "asset_id": aid_str,
                                "asset_type": r["asset_type"],
                                "file_path": resolved_path,
                                "image_url": f"/assets/{aid_str}/image",
                                "entity_name": r.get("entity_name"),
                                "description": r.get("description") or "",
                                "extracted_data": r.get("extracted_data") or {},
                            })
    except Exception as exc:
        logger.warning(f"Error looking up assets by query entity: {exc}")

    return existing_assets + found


def evaluate_direct_pipeline(qid: str, track: str, q_text: str) -> Dict[str, Any]:
    """
    Direct in-process execution using the core archival intelligence pipeline.
    Avoids Docker/HTTP timeouts and connects directly to databases and providers.
    """
    is_multihop = track.startswith("1B") or track.startswith("1C")
    config = RetrievalConfig(
        enable_multihop=is_multihop,
        multihop_max_hops=3 if is_multihop else 1,
        reranker_top_k=10,
    )

    final_answer = answer_question(
        query=q_text,
        config=config,
    )

    # 1. Map Citations with complete metadata and reference locations
    citations: List[Dict[str, Any]] = []
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

        citations.append({
            "evidence_id": c.get("evidence_id", ""),
            "document_title": c.get("document_title", "Archive Document"),
            "source_path": c.get("source_path") or c.get("source_file"),
            "page": page_val,
            "excerpt": c.get("excerpt", "") or c.get("original_text", "")[:200],
            "reference_location": ref_loc,
            "line_start": c.get("line_start") or meta.get("line_start"),
            "line_end": c.get("line_end") or meta.get("line_end"),
        })

    # 2. Map Evidence Summaries
    evidence_summaries: List[Dict[str, Any]] = []
    for ev in final_answer.evidence:
        evidence_summaries.append({
            "id": ev.id,
            "chunk_id": ev.chunk_id,
            "document_id": ev.document_id,
            "document_title": ev.document_title,
            "source_category": ev.source_category,
            "source_subtype": ev.source_subtype,
            "page": ev.page,
            "section_title": ev.section_title,
            "content": ev.content,
            "score": ev.score,
            "metadata": ev.metadata,
        })

    # 3. Fetch Multimodal Asset References (Track 1A)
    raw_assets = fetch_assets_for_evidence(final_answer.evidence)
    asset_dict_list: List[Dict[str, Any]] = []
    for a in raw_assets:
        if hasattr(a, "model_dump"):
            asset_dict_list.append(a.model_dump())
        elif isinstance(a, dict):
            asset_dict_list.append(a)
        else:
            asset_dict_list.append({
                "asset_id": getattr(a, "asset_id", ""),
                "asset_type": getattr(a, "asset_type", "image"),
                "file_path": getattr(a, "file_path", ""),
                "image_url": getattr(a, "image_url", ""),
                "entity_name": getattr(a, "entity_name", None),
                "description": getattr(a, "description", ""),
                "extracted_data": getattr(a, "extracted_data", {}),
            })

    # Enrich visual assets by query entity mentions for Track 1A
    if track.startswith("1A"):
        asset_dict_list = find_assets_for_query(q_text, asset_dict_list)

    # 4. Map Conflicts
    conflicts: List[Dict[str, Any]] = []
    for conf in final_answer.conflicts:
        conflicts.append({
            "claim_summary": conf.claim_summary,
            "conflict_type": conf.conflict_type,
            "supporting": [e.id if hasattr(e, "id") else str(e) for e in conf.supporting_evidence],
            "opposing": [e.id if hasattr(e, "id") else str(e) for e in conf.opposing_evidence],
        })

    return {
        "status": "success",
        "answer": final_answer.answer_text,
        "citations": citations,
        "evidence_status": final_answer.evidence_status,
        "evidence": evidence_summaries,
        "asset_references": asset_dict_list,
        "conflicts": conflicts,
        "trace": final_answer.query_trace,
    }


def evaluate_api_pipeline(q_text: str, api_url: str, timeout_per_query: int) -> Dict[str, Any]:
    """Execute via the FastAPI HTTP endpoint."""
    import requests

    payload = {
        "question": q_text,
        "max_hops": 3,
        "top_k": 10,
        "include_trace": True,
    }

    resp = requests.post(
        f"{api_url}/query",
        json=payload,
        timeout=timeout_per_query,
    )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "status": "success",
            "answer": data.get("answer", ""),
            "citations": data.get("citations", []),
            "evidence_status": data.get("evidence_status", "HIGH"),
            "evidence": data.get("evidence", []),
            "asset_references": data.get("asset_references", []),
            "conflicts": data.get("conflicts", []),
            "trace": data.get("trace", {}),
        }
    else:
        return {
            "status": "error",
            "error": f"HTTP {resp.status_code}: {resp.text}",
        }


def run_evaluation(
    mode: str = "direct",
    api_url: str = API_URL,
    timeout_per_query: int = 180,
    force_restart: bool = False,
):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not QUESTIONS_PATH.exists():
        logger.error(f"Questions file not found at {QUESTIONS_PATH}")
        return

    with open(QUESTIONS_PATH, "r", encoding="utf-8") as f:
        questions: List[Dict[str, Any]] = json.load(f)

    logger.info(f"Loaded {len(questions)} evaluation questions from {QUESTIONS_PATH}")
    logger.info(f"Evaluation Mode: {mode.upper()} | Model: {LLM_MODEL}")

    # Check if partial results exist to allow resumption
    results: List[Dict[str, Any]] = []
    completed_qids = set()
    if OUTPUT_JSON_PATH.exists() and not force_restart:
        try:
            with open(OUTPUT_JSON_PATH, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                if isinstance(existing_data, dict) and "evaluations" in existing_data:
                    results = existing_data["evaluations"]
                    completed_qids = {r["qid"] for r in results if r.get("status") == "success" and r.get("answer")}
                    logger.info(f"Found existing evaluation with {len(completed_qids)} completed questions.")
        except Exception as e:
            logger.warning(f"Could not load existing evaluations: {e}")

    total_start_time = time.time()

    for idx, item in enumerate(questions, 1):
        qid = item.get("qid")
        track = item.get("track")
        q_text = item.get("question")

        if qid in completed_qids:
            logger.info(f"[{idx}/{len(questions)}] Skipping already completed question [{qid}]")
            continue

        logger.info(f"\n[{idx}/{len(questions)}] Evaluating [{qid}] ({track}): '{q_text}'")
        q_start = time.time()

        eval_entry: Dict[str, Any] = {
            "qid": qid,
            "track": track,
            "question": q_text,
            "status": "pending",
            "answer": "",
            "citations": [],
            "evidence_status": "UNKNOWN",
            "evidence": [],
            "asset_references": [],
            "conflicts": [],
            "trace": {},
            "evaluation_time_s": 0.0,
        }

        try:
            if mode == "direct":
                data = evaluate_direct_pipeline(qid, track, q_text)
            else:
                data = evaluate_api_pipeline(q_text, api_url, timeout_per_query)

            elapsed = round(time.time() - q_start, 2)
            eval_entry.update(data)
            eval_entry["evaluation_time_s"] = elapsed

            if eval_entry.get("status") == "success":
                logger.info(
                    f"-> SUCCESS in {elapsed}s | Evidence: {len(eval_entry['evidence'])} | "
                    f"Citations: {len(eval_entry['citations'])} | Assets: {len(eval_entry['asset_references'])} | "
                    f"Conflicts: {len(eval_entry['conflicts'])}"
                )
                logger.info(f"-> Answer preview: {eval_entry['answer'][:140]}...")
            else:
                logger.error(f"-> FAILED in {elapsed}s: {eval_entry.get('error', 'Unknown error')}")

        except Exception as exc:
            elapsed = round(time.time() - q_start, 2)
            eval_entry["status"] = "exception"
            eval_entry["error"] = str(exc)
            eval_entry["evaluation_time_s"] = elapsed
            logger.exception(f"-> EXCEPTION after {elapsed}s: {exc}")

        # Update results list atomically
        existing_idx = next((i for i, r in enumerate(results) if r["qid"] == qid), None)
        if existing_idx is not None:
            results[existing_idx] = eval_entry
        else:
            results.append(eval_entry)

        # Save checkpoint to disk immediately after each question
        successful = [r for r in results if r.get("status") == "success"]
        checkpoint_report = {
            "metadata": {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_questions": len(questions),
                "completed_questions": len(successful),
                "success_rate_percent": round((len(successful) / len(questions)) * 100, 1),
                "llm_model": LLM_MODEL,
                "evaluation_mode": mode,
            },
            "evaluations": results,
        }
        with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(checkpoint_report, f, indent=2, ensure_ascii=False)

    total_elapsed = round(time.time() - total_start_time, 2)
    successful = [r for r in results if r.get("status") == "success"]

    # Final summary calculations
    total_citations = sum(len(r.get("citations", [])) for r in successful)
    total_evidence = sum(len(r.get("evidence", [])) for r in successful)
    total_assets = sum(len(r.get("asset_references", [])) for r in successful)
    total_conflicts = sum(len(r.get("conflicts", [])) for r in successful)
    avg_latency = round(
        sum(r.get("evaluation_time_s", 0) for r in successful) / max(1, len(successful)), 2
    )

    final_report = {
        "metadata": {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "total_questions": len(questions),
            "completed_questions": len(successful),
            "success_rate_percent": round((len(successful) / len(questions)) * 100, 1),
            "llm_model": LLM_MODEL,
            "evaluation_mode": mode,
            "total_elapsed_time_s": total_elapsed,
            "average_latency_s": avg_latency,
            "total_citations_generated": total_citations,
            "total_evidence_records_gathered": total_evidence,
            "total_asset_references": total_assets,
            "total_conflicts_detected": total_conflicts,
        },
        "evaluations": results,
    }

    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    logger.info(f"\n=======================================================")
    logger.info(f"Evaluation complete! Saved to {OUTPUT_JSON_PATH}")
    logger.info(f"Success: {len(successful)}/{len(questions)} | Avg Latency: {avg_latency}s | Total Time: {total_elapsed}s")
    logger.info(f"=======================================================\n")

    # Generate Markdown Summary
    write_summary_markdown(final_report)


def write_summary_markdown(report: Dict[str, Any]):
    meta = report["metadata"]
    evals = report["evaluations"]

    lines = [
        "# Ashen Era Archive: Sample Questions Evaluation Report",
        "",
        f"**Generated:** {meta['generated_at']}  ",
        f"**LLM Model:** `{meta.get('llm_model', 'gemini-3.6-flash')}`  ",
        f"**Total Questions Evaluated:** {meta['completed_questions']} / {meta['total_questions']} ({meta['success_rate_percent']}%)  ",
        f"**Total Time:** {meta['total_elapsed_time_s']}s (Avg: {meta['average_latency_s']}s/query)  ",
        f"**Total Citations:** {meta['total_citations_generated']} | **Evidence Gathered:** {meta['total_evidence_records_gathered']} | **Visual Assets (1A):** {meta.get('total_asset_references', 0)} | **Conflicts Detected:** {meta['total_conflicts_detected']}  ",
        "",
        "---",
        "",
        "## Summary by Track",
        "",
        "| Track | Questions | Avg Latency (s) | Citations | Visual Assets | Conflicts |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]

    tracks: Dict[str, List[Dict[str, Any]]] = {}
    for ev in evals:
        t = ev.get("track", "Unknown Track")
        tracks.setdefault(t, []).append(ev)

    for t, q_list in tracks.items():
        succ = [q for q in q_list if q.get("status") == "success"]
        t_cites = sum(len(q.get("citations", [])) for q in succ)
        t_assets = sum(len(q.get("asset_references", [])) for q in succ)
        t_confs = sum(len(q.get("conflicts", [])) for q in succ)
        t_lat = round(sum(q.get("evaluation_time_s", 0) for q in succ) / max(1, len(succ)), 2)
        lines.append(f"| **{t}** | {len(succ)}/{len(q_list)} | {t_lat}s | {t_cites} | {t_assets} | {t_confs} |")

    lines.extend([
        "",
        "---",
        "",
        "## Detailed Question Evaluations",
        "",
    ])

    for ev in evals:
        qid = ev["qid"]
        track = ev["track"]
        q = ev["question"]
        status = ev.get("status")
        ans = ev.get("answer", "")
        cites = ev.get("citations", [])
        evidence = ev.get("evidence", [])
        conflicts = ev.get("conflicts", [])
        assets = ev.get("asset_references", [])
        lat = ev.get("evaluation_time_s", 0)

        lines.append(f"### [{qid}] {q}")
        lines.append(f"- **Track:** `{track}`")
        lines.append(f"- **Status:** `{status}` ({lat}s) | **Evidence Status:** `{ev.get('evidence_status', 'UNKNOWN')}`")
        lines.append("")
        lines.append(f"**Answer:**  \n{ans}")
        lines.append("")

        if cites:
            lines.append("**Document Citations & References:**")
            for c in cites:
                doc = c.get("document_title", "Document")
                loc = c.get("reference_location") or f"p.{c.get('page')}"
                lines.append(f"- `[{doc}, {loc}]`: {c.get('excerpt', '')}")
            lines.append("")

        if assets:
            lines.append("**Multimodal Visual Assets (Track 1A):**")
            for a in assets:
                lines.append(f"- **{a.get('entity_name') or 'Asset'}** (`{a.get('asset_type')}`): `{a.get('file_path')}`")
                if a.get("extracted_data"):
                    lines.append(f"  - Extracted: `{a.get('extracted_data')}`")
            lines.append("")

        if conflicts:
            lines.append("**Archive Conflicts Detected:**")
            for conf in conflicts:
                lines.append(f"- ⚠️ **{conf.get('claim_summary')}** (`{conf.get('conflict_type')}`)")
            lines.append("")

        lines.append("---")
        lines.append("")

    with open(OUTPUT_SUMMARY_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"Summary markdown written to {OUTPUT_SUMMARY_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate sample questions against the Ashen Era Archive")
    parser.add_argument("--mode", choices=["direct", "api"], default="direct", help="Evaluation execution mode")
    parser.add_argument("--api-url", default=API_URL, help="FastAPI backend URL if mode=api")
    parser.add_argument("--timeout", type=int, default=180, help="Per-query timeout in seconds")
    parser.add_argument("--force", action="store_true", help="Force re-evaluation of all questions")
    args = parser.parse_args()

    run_evaluation(
        mode=args.mode,
        api_url=args.api_url,
        timeout_per_query=args.timeout,
        force_restart=args.force,
    )
