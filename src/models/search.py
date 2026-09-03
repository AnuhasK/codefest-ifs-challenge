from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    """Container for a single retrieved chunk with relevance score and metadata."""

    chunk_id: str
    document_id: str
    content: str
    score: float = Field(..., description="Cosine similarity score (0.0 to 1.0)")
    distance: Optional[float] = Field(None, description="Raw vector distance")
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    chapter: Optional[str] = None
    section_title: Optional[str] = None
    source_category: Optional[str] = None
    source_path: Optional[str] = None
    document_title: Optional[str] = None
    rank: int = 1
    metadata: Dict[str, Any] = Field(default_factory=dict)
