from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.models.search import SearchResult
from src.models.entity import Entity


class HopResult(BaseModel):
    """Result of a single traversal hop across the knowledge graph."""
    hop_number: int
    source_entity: str
    target_entity: str
    relationship_type: str
    direction: str = "forward"  # "forward" or "reverse"
    evidence_chunk_ids: List[str] = Field(default_factory=list)
    path_description: str = ""


class SufficiencyScore(BaseModel):
    """Evaluation of whether gathered evidence is sufficient to reliably answer a query."""
    level: str  # "HIGH", "MEDIUM", "LOW", "INSUFFICIENT"
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    source_count: int = 0
    unique_documents: int = 0
    missing: List[str] = Field(default_factory=list)
    reasoning: str = ""


class QueryState(BaseModel):
    """Explicit state tracker for single-hop and multi-hop retrieval."""
    original_query: str
    query_type: str = "simple"  # "simple", "multi_hop", "multi_entity", "comparison"
    sub_questions: List[str] = Field(default_factory=list)

    # Discovery tracking
    identified_entities: List[str] = Field(default_factory=list)
    discovered_entities: List[str] = Field(default_factory=list)
    discovered_relationships: List[Dict[str, Any]] = Field(default_factory=list)
    traversal_hops: List[HopResult] = Field(default_factory=list)

    # Evidence tracking
    retrieved_evidence: List[SearchResult] = Field(default_factory=list)
    evidence_per_hop: Dict[int, List[SearchResult]] = Field(default_factory=dict)

    # Quality and termination tracking
    evidence_coverage: float = 0.0
    missing_information: List[str] = Field(default_factory=list)
    contradictions: List[Dict[str, Any]] = Field(default_factory=list)
    sufficiency: Optional[SufficiencyScore] = None

    # Iteration bounds
    iteration_count: int = 0
    max_iterations: int = 3
