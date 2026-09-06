from src.models.evidence import EvidenceRecord, SourceCharacteristics
from src.ingestion.claims import extract_claims_from_chunk


def test_claim_extraction_accused_not_committed():
    text = "In 312 AS, Ser Vael was accused of betrayal at the summit."
    claims = extract_claims_from_chunk(text)

    assert len(claims) >= 1
    c = claims[0]
    assert c.subject == "Ser Vael"
    assert c.predicate == "accused_of"
    assert c.predicate != "committed"
    assert "betrayal" in c.object
    assert c.claim_strength == "allegation"
    assert c.temporal_context.lower() == "in 312 as"



def test_claim_extraction_founded_order():
    text = "Ser Vael founded the Ashen Vanguard during the Ashen War."
    claims = extract_claims_from_chunk(text)

    assert len(claims) >= 1
    c = claims[0]
    assert c.subject == "Ser Vael"
    assert c.predicate == "founded"
    assert "Ashen Vanguard" in c.object
    assert c.claim_strength == "assertion"
    assert c.temporal_context == "during the Ashen War"


def test_claim_extraction_rumored():
    text = "Lord Drovenath was rumored to have fled to the southern wastes."
    claims = extract_claims_from_chunk(text)

    assert len(claims) >= 1
    c = claims[0]
    assert c.subject == "Lord Drovenath"
    assert c.predicate == "rumored_to_have"
    assert c.predicate != "did"
    assert c.claim_strength == "rumor"


def test_claim_extraction_from_evidence_record():
    rec = EvidenceRecord(
        id="EVIDENCE_001",
        chunk_id="c1",
        document_id="d1",
        content="Ser Vael established the Ashen Vanguard.",
        source_category="codex",
        source_subtype="codex_entry",
        document_title="Codex Vaeloria I",
        source_characteristics=SourceCharacteristics(
            source_type="codex",
            document_subtype="codex_entry",
            claim_strength="assertion",
            narrative_voice="official",
            temporal_reliability="retrospective",
        ),
    )

    claims = extract_claims_from_chunk(rec)
    assert len(claims) >= 1
    assert claims[0].source_evidence_id == "EVIDENCE_001"
    assert claims[0].source_type == "codex"
    assert claims[0].claim_strength == "assertion"
