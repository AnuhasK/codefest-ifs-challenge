import re
from typing import List, Dict, Any, Optional

from src.models.evidence import CitationIssue, EvidenceRecord
from src.knowledge.evidence import EvidenceManager


class CitationResolver:
    """
    Deterministic citation resolution and validation engine.
    Replaces internal evidence tags with authoritative archival document citations.
    """

    def __init__(self, evidence_manager: EvidenceManager):
        self.evidence_manager = evidence_manager

    def _format_single_citation(self, record: EvidenceRecord) -> str:
        """Format a single EvidenceRecord into a human-readable citation string."""
        title = record.document_title or "Archive Document"
        if record.page:
            return f"{title}, p.{record.page}"
        return title

    def resolve_citations(self, answer_text: str) -> str:
        """
        Replace [EVIDENCE_X] references in the answer with actual document/page citations.

        Examples:
        - "Ser Vael was a member [EVIDENCE_001] of the Vanguard."
          -> "Ser Vael was a member [Royal Annals, p.84] of the Vanguard."
        - "[EVIDENCE_001, EVIDENCE_002]"
          -> "[Royal Annals, p.84; Trial Transcript, p.7]"
        """
        if not answer_text:
            return ""

        # Pattern matches [EVIDENCE_001], [EVIDENCE_1], [EVIDENCE_001, EVIDENCE_002], etc.
        def replace_match(match: re.Match) -> str:
            inner = match.group(1)
            # Find all EVIDENCE_xxx tokens inside the bracket
            tokens = re.findall(r"EVIDENCE_\d+", inner, flags=re.IGNORECASE)
            if not tokens:
                return match.group(0)

            resolved_parts: List[str] = []
            for tok in tokens:
                rec = self.evidence_manager.get_evidence_by_id(tok)
                if rec:
                    resolved_parts.append(self._format_single_citation(rec))
                else:
                    # Keep unmapped reference tag visible
                    resolved_parts.append(tok)

            return f"[{'; '.join(resolved_parts)}]"

        # Match bracketed evidence tags: [EVIDENCE_...]
        pattern = re.compile(r"\[(EVIDENCE_\d+(?:[,\s;]+EVIDENCE_\d+)*)\]", flags=re.IGNORECASE)
        resolved_text = pattern.sub(replace_match, answer_text)

        # Also replace standalone unbracketed references if any exist: EVIDENCE_001 -> [Royal Annals, p.84]
        # Only if preceded by whitespace and not followed by alphanumeric
        return resolved_text

    def validate_citations(self, answer_text: str) -> List[CitationIssue]:
        """
        Check that all [EVIDENCE_X] references in the answer correspond to actual registered evidence records.

        Returns list of CitationIssue objects (missing evidence, invalid format, etc.).
        """
        issues: List[CitationIssue] = []
        if not answer_text:
            return issues

        # Find all cited evidence IDs
        cited_tokens = re.findall(r"EVIDENCE_(\d+)", answer_text, flags=re.IGNORECASE)

        for num_str in cited_tokens:
            full_token = f"EVIDENCE_{int(num_str):03d}"
            rec = self.evidence_manager.get_evidence_by_id(full_token)
            if not rec:
                issues.append(
                    CitationIssue(
                        evidence_id=f"EVIDENCE_{num_str}",
                        issue_type="missing",
                        description=f"Citation reference EVIDENCE_{num_str} does not exist in registered evidence records.",
                    )
                )
            elif not rec.content or not rec.content.strip():
                issues.append(
                    CitationIssue(
                        evidence_id=rec.id,
                        issue_type="unsupported",
                        description=f"Citation {rec.id} refers to an empty evidence record.",
                    )
                )

        return issues

    def get_citation_details(self, evidence_id: str) -> Dict[str, Any]:
        """
        Return full citation details for a given evidence ID:
        - Document title
        - Page number
        - Section title
        - Source category
        - Source subtype
        - Source file
        - Original text snippet
        """
        rec = self.evidence_manager.get_evidence_by_id(evidence_id)
        if not rec:
            return {
                "evidence_id": evidence_id,
                "found": False,
                "document_title": "Unknown",
                "page": None,
                "section_title": None,
                "source_category": "unknown",
                "source_subtype": "unknown",
                "source_file": "",
                "original_text": "",
            }

        return {
            "evidence_id": rec.id,
            "found": True,
            "document_title": rec.document_title,
            "page": rec.page,
            "section_title": rec.section_title,
            "source_category": rec.source_category,
            "source_subtype": rec.source_subtype,
            "source_file": rec.source_file,
            "original_text": rec.content,
        }
