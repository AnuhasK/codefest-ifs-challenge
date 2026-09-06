from typing import List
from pydantic import BaseModel, Field


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
