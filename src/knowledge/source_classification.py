import os
import re
from pathlib import Path
from typing import Union, Optional, Any

from src.models.evidence import SourceCharacteristics


SUBTYPE_PATTERNS = [
    (
        r"^ballad_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="ballad",
            claim_strength="rumor",
            narrative_voice="in_character",
            temporal_reliability="mythological",
        ),
    ),
    (
        r"^trial_transcript_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="trial_transcript",
            claim_strength="allegation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^sermon_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="sermon",
            claim_strength="assertion",
            narrative_voice="in_character",
            temporal_reliability="retrospective",
        ),
    ),
    (
        r"^field_report_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="field_report",
            claim_strength="observation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^letter_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="letter",
            claim_strength="personal",
            narrative_voice="personal",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^decree_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="decree",
            claim_strength="decree",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^interrogation_record_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="interrogation_record",
            claim_strength="allegation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^petition_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="petition",
            claim_strength="assertion",
            narrative_voice="personal",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^contract_concerning_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="contract",
            claim_strength="assertion",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^auction_catalogue_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="auction_catalogue",
            claim_strength="observation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^muster_roll_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="muster_roll",
            claim_strength="assertion",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
    (
        r"^quartermaster_ledger_",
        SourceCharacteristics(
            source_type="ephemera",
            document_subtype="quartermaster_ledger",
            claim_strength="observation",
            narrative_voice="official",
            temporal_reliability="contemporary",
        ),
    ),
]


def classify_source(
    evidence_or_filename: Union[Any, str, Path],
    category: Optional[str] = None,
) -> SourceCharacteristics:
    """
    Classify evidence source based on document metadata, filenames, and subtypes.

    Maps filenames to epistemological characteristics without hardcoding binary truth.
    """
    filename = ""
    source_category = category or ""

    # Extract filename/path from various object types
    if isinstance(evidence_or_filename, (str, Path)):
        filename = str(evidence_or_filename)
    elif hasattr(evidence_or_filename, "source_file") and evidence_or_filename.source_file:
        filename = evidence_or_filename.source_file
        source_category = source_category or getattr(evidence_or_filename, "source_category", "") or ""
    elif hasattr(evidence_or_filename, "source_path") and evidence_or_filename.source_path:
        filename = evidence_or_filename.source_path
        source_category = source_category or getattr(evidence_or_filename, "source_category", "") or ""
    elif hasattr(evidence_or_filename, "document_title") and evidence_or_filename.document_title:
        filename = evidence_or_filename.document_title
        source_category = source_category or getattr(evidence_or_filename, "source_category", "") or ""
    elif hasattr(evidence_or_filename, "metadata") and isinstance(evidence_or_filename.metadata, dict):
        filename = evidence_or_filename.metadata.get("source_file", "") or evidence_or_filename.metadata.get("source_path", "")
        source_category = source_category or evidence_or_filename.metadata.get("source_category", "") or ""

    source_category = str(source_category or "").lower()

    # Clean filename to stem
    basename = os.path.basename(filename)
    stem = re.sub(r"(\.scan)?\.(pdf|docx|md|txt|png|jpe?g)$", "", basename, flags=re.IGNORECASE).lower().strip()

    # Match ephemera subtypes by prefix
    for pattern, char in SUBTYPE_PATTERNS:
        if re.search(pattern, stem):
            return char

    # Match wiki articles
    if stem.startswith("wiki_") or source_category.lower() == "wiki" or "/wiki/" in filename.replace("\\", "/"):
        return SourceCharacteristics(
            source_type="wiki",
            document_subtype="wiki_article",
            claim_strength="assertion",
            narrative_voice="third_party",
            temporal_reliability="retrospective",
        )

    # Match codex entries
    if stem.startswith("codex_") or source_category.lower() == "codex" or "/codex/" in filename.replace("\\", "/"):
        return SourceCharacteristics(
            source_type="codex",
            document_subtype="codex_entry",
            claim_strength="assertion",
            narrative_voice="official",
            temporal_reliability="retrospective",
        )

    # Match chronicles
    if "chronicle" in stem or source_category.lower() == "chronicles" or "/chronicles/" in filename.replace("\\", "/"):
        return SourceCharacteristics(
            source_type="chronicle",
            document_subtype="chronicle_entry",
            claim_strength="assertion",
            narrative_voice="narrative",
            temporal_reliability="retrospective",
        )

    # Default fallback for unknown files
    return SourceCharacteristics(
        source_type="ephemera" if source_category.lower() == "ephemera" else "unknown",
        document_subtype="unknown",
        claim_strength="assertion",
        narrative_voice="official" if source_category.lower() in ("codex", "chronicles") else "third_party",
        temporal_reliability="retrospective",
    )
