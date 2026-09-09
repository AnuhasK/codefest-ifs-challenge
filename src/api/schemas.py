from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field


# ==========================================
# Health Schemas
# ==========================================

class DatabaseStatus(BaseModel):
    postgres: str = "disconnected"
    neo4j: str = "disconnected"
    details: Optional[Dict[str, Any]] = None


class HealthResponse(BaseModel):
    status: str = "healthy"  # healthy, degraded, unhealthy
    timestamp: str
    databases: DatabaseStatus


# ==========================================
# Query & Multimodal (Track 1A/1B/1C) Schemas
# ==========================================

class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Question about the Ashen Era Archive")
    max_hops: int = Field(default=3, ge=1, le=5, description="Maximum graph traversal hops for multi-hop queries")
    top_k: int = Field(default=20, ge=1, le=50, description="Evidence candidate pool size")
    include_trace: bool = Field(default=False, description="Whether to include full retrieval and verification trace")


class AssetReference(BaseModel):
    """Multimodal asset reference (Track 1A) for figure plates, heraldry, portraits, and atmospheric art."""
    asset_id: str
    asset_type: str  # figure_plate, portrait, heraldry, landscape, creature, etc.
    file_path: str
    image_url: Optional[str] = None
    entity_name: Optional[str] = None
    description: str
    extracted_data: Optional[Dict[str, Any]] = None


class EvidenceSummary(BaseModel):
    id: str  # e.g., EVIDENCE_001
    chunk_id: str
    document_id: str
    document_title: str
    source_category: str
    source_subtype: str
    page: Optional[int] = None
    section_title: Optional[str] = None
    content: str
    score: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ConflictSummary(BaseModel):
    claim_summary: str
    supporting: List[str] = Field(default_factory=list)
    opposing: List[str] = Field(default_factory=list)
    conflict_type: str = "contradiction"


class CitationItem(BaseModel):
    evidence_id: str
    document_title: str
    source_path: Optional[str] = None
    page: Optional[int] = None
    excerpt: str
    reference_location: Optional[str] = None
    line_start: Optional[Any] = None
    line_end: Optional[Any] = None


class QueryResponse(BaseModel):
    question: str
    answer: str
    citations: List[CitationItem] = Field(default_factory=list)
    evidence: List[EvidenceSummary] = Field(default_factory=list)
    asset_references: List[AssetReference] = Field(default_factory=list)
    conflicts: List[ConflictSummary] = Field(default_factory=list)
    evidence_status: str = "HIGH"  # HIGH, MEDIUM, LOW, INSUFFICIENT, API_QUOTA_EXHAUSTED
    trace: Optional[Dict[str, Any]] = None
    warning: Optional[str] = None


# ==========================================
# Direct Search Schemas
# ==========================================

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    search_type: str = Field(default="hybrid", description="Search algorithm: hybrid, bm25, dense, entity")
    top_k: int = Field(default=20, ge=1, le=100)


class SearchResultItem(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    source_category: str
    content: str
    score: float
    page: Optional[int] = None
    section_title: Optional[str] = None
    source_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    results: List[SearchResultItem]
    total: int
    search_type: str
    warning: Optional[str] = None


# ==========================================
# Document & Chunk Browsing Schemas
# ==========================================

class DocumentSummary(BaseModel):
    id: str
    title: str
    source_category: str
    source_path: str
    chunk_count: int = 0


class DocumentSection(BaseModel):
    id: str
    title: Optional[str] = None
    level: int = 1
    position: int = 0


class DocumentDetail(BaseModel):
    id: str
    title: str
    source_category: str
    source_path: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    chunk_count: int = 0
    sections: List[DocumentSection] = Field(default_factory=list)


class ChunkDetail(BaseModel):
    id: str
    document_id: str
    content: str
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_title: Optional[str] = None
    position: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ==========================================
# Entity & Graph Browsing Schemas
# ==========================================

class EntitySummary(BaseModel):
    id: str
    name: str
    entity_type: str
    aliases: List[str] = Field(default_factory=list)
    mention_count: int = 0
    confidence: float = 1.0


class EntityRelationship(BaseModel):
    rel_type: str
    target_id: str
    target_name: str
    target_type: str


class EntityDetail(BaseModel):
    id: str
    name: str
    entity_type: str
    aliases: List[str] = Field(default_factory=list)
    mention_count: int = 0
    confidence: float = 1.0
    relationships: List[EntityRelationship] = Field(default_factory=list)
    sample_chunk_ids: List[str] = Field(default_factory=list)
