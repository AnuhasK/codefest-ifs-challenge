from src.knowledge.source_classification import classify_source
from src.models.search import SearchResult
from src.models.evidence import EvidenceRecord


def test_classify_ballad():
    res = classify_source("ballad_concerning_vael.docx")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "ballad"
    assert res.claim_strength == "rumor"
    assert res.narrative_voice == "in_character"


def test_classify_trial_transcript():
    res = classify_source("trial_transcript_concerning_fenspire.pdf")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "trial_transcript"
    assert res.claim_strength == "allegation"
    assert res.narrative_voice == "official"


def test_classify_decree():
    res = classify_source("decree_concerning_house_morvain.pdf")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "decree"
    assert res.claim_strength == "decree"
    assert res.narrative_voice == "official"


def test_classify_field_report():
    res = classify_source("field_report_concerning_red_vale.docx")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "field_report"
    assert res.claim_strength == "observation"
    assert res.narrative_voice == "official"


def test_classify_letter():
    res = classify_source("letter_concerning_the_ember_tide.txt")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "letter"
    assert res.claim_strength == "personal"
    assert res.narrative_voice == "personal"


def test_classify_interrogation_record():
    res = classify_source("interrogation_record_concerning_ashreach.scan.pdf")
    assert res.source_type == "ephemera"
    assert res.document_subtype == "interrogation_record"
    assert res.claim_strength == "allegation"
    assert res.narrative_voice == "official"


def test_classify_wiki():
    res = classify_source("wiki_person_ser_vael.md", category="wiki")
    assert res.source_type == "wiki"
    assert res.document_subtype == "wiki_article"
    assert res.claim_strength == "assertion"
    assert res.narrative_voice == "third_party"


def test_classify_codex():
    res = classify_source("codex_vaeloria_i_gazetteer_of_the_sundered_realms.pdf", category="codex")
    assert res.source_type == "codex"
    assert res.document_subtype == "codex_entry"
    assert res.claim_strength == "assertion"
    assert res.narrative_voice == "official"


def test_classify_chronicles():
    res = classify_source("the_ashen_chronicles_volume_i_the_kindling_years.pdf", category="chronicles")
    assert res.source_type == "chronicle"
    assert res.document_subtype == "chronicle_entry"
    assert res.claim_strength == "assertion"
    assert res.narrative_voice == "narrative"


def test_classify_from_search_result_object():
    sr = SearchResult(
        chunk_id="chk1",
        document_id="doc1",
        content="Some text",
        score=0.9,
        source_path="Ashen_Era_Archive/ephemera/auction_catalogue_concerning_ashreach.pdf",
    )
    res = classify_source(sr)
    assert res.source_type == "ephemera"
    assert res.document_subtype == "auction_catalogue"
    assert res.claim_strength == "observation"
