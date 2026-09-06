import re
import json
import logging
from typing import List, Optional, Dict, Any

from src.models.evidence import (
    VerificationResult,
    ClaimCheck,
    CitationIssue,
    Conflict,
    EvidenceRecord,
)
from src.knowledge.evidence import EvidenceManager
from src.generation.citations import CitationResolver
from src.providers.llm_provider import LLMProvider

logger = logging.getLogger(__name__)

VERIFY_PROMPT_TEMPLATE = """Given this answer and the evidence passages it was based on, verify each factual claim in the answer.

Answer:
{answer}

Evidence provided:
{evidence_texts}

For each factual claim in the answer:
1. State the claim
2. Verdict: is it supported by the provided evidence? ("supported", "unsupported", or "partially_supported")
3. Which evidence IDs support it? (e.g. ["EVIDENCE_001"])

Respond strictly in valid JSON format:
{{
  "claim_checks": [
    {{
      "claim": "...",
      "verdict": "supported",
      "evidence_ids": ["EVIDENCE_001"]
    }}
  ],
  "unsupported_claims": []
}}
"""

CONFLICT_ACKNOWLEDGEMENT_KEYWORDS = [
    "conflict", "disagree", "contradict", "allegation", "alleged", "claimed",
    "however", "whereas", "on the other hand", "alternatively", "disputed",
    "contrasting", "unclear", "divergent", "differing", "while", "accused",
]


COMMON_CONNECTORS = {
    "the", "and", "but", "in", "at", "on", "ser", "lord", "lady", "while",
    "however", "although", "whereas", "according", "furthermore", "moreover",
    "as", "this", "that", "these", "those", "when", "where", "if", "there",
    "based", "some", "both", "all", "each", "one", "two", "three", "with",
    "from", "into", "over", "after", "before", "ballad", "ballads", "record",
    "records", "trial", "trials", "codex", "annals", "archive"
}

STOP_WORDS_SET = {
    "the", "and", "a", "an", "in", "on", "at", "to", "for", "of", "with",
    "from", "by", "as", "is", "was", "were", "be", "been", "are", "that",
    "this", "these", "those", "it", "its", "or", "which", "who", "whom"
}


def _heuristic_verify(
    answer: str,
    evidence_manager: EvidenceManager,
) -> List[ClaimCheck]:
    """
    Deterministic rule-based claim validation checking entity and token grounding against evidence.
    """
    sentences = [s.strip() for s in re.split(r"[.\n]+", answer) if len(s.strip()) > 10]
    checks: List[ClaimCheck] = []

    for sent in sentences:
        # Check citations in this sentence
        cited_nums = re.findall(r"EVIDENCE_(\d+)", sent, flags=re.IGNORECASE)
        matching_ids = [f"EVIDENCE_{int(num):03d}" for num in cited_nums]

        # Gather supporting texts from cited records or all records
        matching_records: List[EvidenceRecord] = []
        for eid in matching_ids:
            rec = evidence_manager.get_evidence_by_id(eid)
            if rec:
                matching_records.append(rec)

        if not matching_records:
            matching_records = evidence_manager.evidence

        combined_text = " ".join(r.content for r in matching_records).lower()

        # Extract content words (non-stopwords > 3 chars)
        content_words = [
            w.lower() for w in re.findall(r"\b[a-zA-Z]{4,}\b", sent)
            if w.lower() not in STOP_WORDS_SET and not w.lower().startswith("evidence")
        ]
        matching_content_words = [w for w in content_words if w in combined_text]

        # Extract capitalized potential proper nouns
        nouns = [
            w for w in re.findall(r"\b[A-Z][a-z]+\b", sent)
            if w.lower() not in COMMON_CONNECTORS
        ]
        missing_nouns = [w for w in nouns if w.lower() not in combined_text]

        if content_words and len(matching_content_words) == 0 and len(missing_nouns) > 0:
            # Zero content overlap with evidence
            verdict = "unsupported"
            has_ev = False
        elif len(missing_nouns) > 0 and len(nouns) > 0 and len(matching_content_words) < 2:
            verdict = "partially_supported"
            has_ev = True
        else:
            verdict = "supported"
            has_ev = True

        checks.append(
            ClaimCheck(
                claim_text=sent,
                has_evidence=has_ev,
                evidence_ids=matching_ids or [r.id for r in matching_records[:2]],
                verdict=verdict,
            )
        )

    return checks



def verify_answer(
    answer: str,
    evidence_manager: EvidenceManager,
    llm: Optional[LLMProvider] = None,
    conflicts: Optional[List[Conflict]] = None,
    force_llm_verification: bool = False,
) -> VerificationResult:
    """
    Post-generation verification pipeline:
    1. Validate deterministic citation identifiers.
    2. Extract and verify factual claims against evidence (deterministic fast-path + LLM fallback).
    3. Check if detected conflicts are acknowledged when conflicts exist.
    4. Flag unsupported claims and compute overall verification status.
    """
    resolver = CitationResolver(evidence_manager)
    citation_issues = resolver.validate_citations(answer)

    claim_checks: List[ClaimCheck] = []
    unsupported_claims: List[str] = []

    # Run deterministic heuristic check first (< 0.01s)
    heuristic_checks = _heuristic_verify(answer, evidence_manager)
    heuristic_unsup = [c.claim_text for c in heuristic_checks if c.verdict == "unsupported"]

    # If heuristic cleanly verified all claims and citations are valid, use fast-path (bypasses 15-20s LLM call)
    if not force_llm_verification and len(heuristic_unsup) == 0:
        claim_checks = heuristic_checks
        unsupported_claims = []
    elif llm is not None:
        # Format evidence blocks for deep LLM verification
        evidence_lines: List[str] = []
        for rec in evidence_manager.evidence:
            evidence_lines.append(f"[{rec.id}] {rec.document_title}: \"{rec.content.strip()}\"")

        prompt = VERIFY_PROMPT_TEMPLATE.format(
            answer=answer,
            evidence_texts="\n".join(evidence_lines),
        )

        try:
            response = llm.generate(
                prompt=prompt,
                system_prompt="You are an archive verification evaluator. Verify each factual statement against provided evidence.",
            )
            content = response.content.strip()
            json_match = re.search(r"(\{.*\})", content, re.DOTALL)
            data = json.loads(json_match.group(1)) if json_match else json.loads(content)

            for c_raw in data.get("claim_checks", []):
                verdict = c_raw.get("verdict", "supported")
                if verdict not in ("supported", "unsupported", "partially_supported"):
                    verdict = "supported" if c_raw.get("has_evidence", True) else "unsupported"

                ev_ids = c_raw.get("evidence_ids", [])
                claim_text = c_raw.get("claim", "")
                has_ev = verdict in ("supported", "partially_supported")

                check = ClaimCheck(
                    claim_text=claim_text,
                    has_evidence=has_ev,
                    evidence_ids=ev_ids,
                    verdict=verdict,
                )
                claim_checks.append(check)
                if verdict == "unsupported":
                    unsupported_claims.append(claim_text)

            for unsup in data.get("unsupported_claims", []):
                if unsup not in unsupported_claims:
                    unsupported_claims.append(unsup)

        except Exception as e:
            logger.warning("LLM answer verification failed, using heuristic: %s", e)
            claim_checks = _heuristic_verify(answer, evidence_manager)
            unsupported_claims = [c.claim_text for c in claim_checks if c.verdict == "unsupported"]
    else:
        claim_checks = _heuristic_verify(answer, evidence_manager)
        unsupported_claims = [c.claim_text for c in claim_checks if c.verdict == "unsupported"]

    # If the answer is an explicit honest refusal to fabricate, it passes verification
    if "insufficient information to answer" in answer.lower():
        return VerificationResult(
            is_verified=True,
            claim_checks=claim_checks,
            citation_issues=citation_issues,
            conflict_acknowledgements=[],
            unsupported_claims=[],
        )

    # Conflict acknowledgement check
    conflict_acknowledgements: List[str] = []
    conflicts_acknowledged = True

    if conflicts:
        answer_lower = answer.lower()
        has_ack_terms = any(kw in answer_lower for kw in CONFLICT_ACKNOWLEDGEMENT_KEYWORDS)
        if has_ack_terms:
            for c in conflicts:
                conflict_acknowledgements.append(f"Acknowledged dispute: {c.claim_summary}")
        else:
            conflicts_acknowledged = False
            conflict_acknowledgements.append("WARNING: Detected archive conflict was not acknowledged in answer.")

    # Verification passes if no citation errors, no unsupported claims, and conflicts are acknowledged
    is_verified = (
        len(citation_issues) == 0
        and len(unsupported_claims) == 0
        and conflicts_acknowledged
    )


    return VerificationResult(
        is_verified=is_verified,
        claim_checks=claim_checks,
        citation_issues=citation_issues,
        conflict_acknowledgements=conflict_acknowledgements,
        unsupported_claims=unsupported_claims,
    )
