from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class RelationshipType(str, Enum):
    """Canonical relationship types between entities in the Ashen Era knowledge graph."""
    MEMBER_OF = "MEMBER_OF"
    LED = "LED"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    OCCURRED_AT = "OCCURRED_AT"
    LOCATED_IN = "LOCATED_IN"
    WON = "WON"
    LOST = "LOST"
    CONTROLS = "CONTROLS"
    HOLDS = "HOLDS"
    ALLIED_WITH = "ALLIED_WITH"
    OPPOSED = "OPPOSED"
    CREATED = "CREATED"
    FOUNDED = "FOUNDED"
    DESTROYED = "DESTROYED"
    RELATED_TO = "RELATED_TO"


class Entity(BaseModel):
    """Canonical knowledge graph entity representation for Neo4j."""
    id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    entity_type: str
    source: str = "unknown"
    confidence: float = 1.0
    source_documents: List[str] = Field(default_factory=list)
    source_chunks: List[str] = Field(default_factory=list)
    mention_count: int = 1


class RelationshipOutput(BaseModel):
    """Single relationship extracted by LLM with structured output schema enforcement."""
    source: str = Field(..., description="The source entity name that is the subject")
    target: str = Field(..., description="The target entity name that is the object")
    type: RelationshipType = Field(..., description="Canonical relationship type")
    evidence: str = Field(..., description="Exact supporting text span from the chunk")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0, description="Extraction confidence score")


class RelationshipExtractionResult(BaseModel):
    """Schema enforced on Gemini structured output for chunk relationship extraction."""
    relationships: List[RelationshipOutput] = Field(default_factory=list)


class ExtractedRelationship(BaseModel):
    """Fully resolved relationship ready for Neo4j persistence and provenance linking."""
    source_entity: str
    target_entity: str
    relationship_type: str  # e.g. MEMBER_OF
    evidence_text: str
    chunk_id: Optional[str] = None
    document_id: Optional[str] = None
    confidence: float = 1.0
    source: str = "llm"  # "infobox", "llm", or "rules"

