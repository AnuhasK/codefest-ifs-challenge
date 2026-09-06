import pytest
from uuid import uuid4
from pydantic import ValidationError

from src.models.document import Chunk
from src.models.entity import (
    RelationshipType,
    RelationshipOutput,
    RelationshipExtractionResult,
    ExtractedRelationship,
)
from src.ingestion.relationships import (
    get_entity_pairs_from_chunk,
    clean_wiki_entity_name,
    parse_entities_from_field,
)


def test_relationship_type_enum():
    """Verify canonical relationship types in enum."""
    assert RelationshipType.MEMBER_OF == "MEMBER_OF"
    assert RelationshipType.WON == "WON"
    assert RelationshipType.LOCATED_IN == "LOCATED_IN"
    assert RelationshipType.HOLDS == "HOLDS"


def test_relationship_output_validation():
    """Verify Pydantic validation for structured relationship outputs."""
    valid = RelationshipOutput(
        source="Ederon Fellgard",
        target="The Iron-Ring Cartel",
        type=RelationshipType.MEMBER_OF,
        evidence="Ederon Fellgard is a member of The Iron-Ring Cartel.",
        confidence=0.95,
    )
    assert valid.type == RelationshipType.MEMBER_OF
    assert valid.confidence == 0.95

    # Invalid relationship type
    with pytest.raises(ValidationError):
        RelationshipOutput(
            source="Ederon Fellgard",
            target="The Iron-Ring Cartel",
            type="INVALID_TYPE",
            evidence="Some text",
            confidence=0.9,
        )

    # Invalid confidence score (> 1.0)
    with pytest.raises(ValidationError):
        RelationshipOutput(
            source="Ederon Fellgard",
            target="The Iron-Ring Cartel",
            type=RelationshipType.MEMBER_OF,
            evidence="Some text",
            confidence=1.5,
        )


def test_cooccurrence_pre_filter():
    """Verify that chunks with < 2 entities are pre-filtered out."""
    c_id1 = uuid4()
    c_id2 = uuid4()
    c_id3 = uuid4()

    chunk1 = Chunk(id=c_id1, content="Only one entity mentioned.")
    chunk2 = Chunk(id=c_id2, content="Ederon Fellgard and Iron-Ring Cartel.")
    chunk3 = Chunk(id=c_id3, content="Three entities together.")

    mentions = {
        str(c_id1): ["Ser Vael"],
        str(c_id2): ["Ederon Fellgard", "Iron-Ring Cartel"],
        str(c_id3): ["Ser Vael", "Ashen Vanguard", "Red Vale"],
    }

    # 1 entity -> 0 pairs (filtered)
    assert get_entity_pairs_from_chunk(chunk1, mentions) == []

    # 2 entities -> 1 pair
    pairs2 = get_entity_pairs_from_chunk(chunk2, mentions)
    assert len(pairs2) == 1
    assert ("Ederon Fellgard", "Iron-Ring Cartel") in pairs2

    # 3 entities -> 3 pairs
    pairs3 = get_entity_pairs_from_chunk(chunk3, mentions)
    assert len(pairs3) == 3


def test_wiki_entity_cleaners():
    """Verify parsing of entity mentions and wiki links."""
    assert clean_wiki_entity_name("[[The Silent Choir]]") == "The Silent Choir"
    assert clean_wiki_entity_name("[[The Silent Choir]] (faction)") == "The Silent Choir"
    assert clean_wiki_entity_name("**Ser Vael**") == "Ser Vael"

    parsed = parse_entities_from_field("[[Ignatz Fellgard]]; [[Brannoc Palefroth]]; [[Thessaly Coldwater]]")
    assert len(parsed) == 3
    assert "Ignatz Fellgard" in parsed
    assert "Brannoc Palefroth" in parsed
    assert "Thessaly Coldwater" in parsed
