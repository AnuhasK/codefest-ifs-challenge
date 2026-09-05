from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import BaseModel, Field


class DiscoveredFile(BaseModel):
    path: str
    filename: str
    stem: str
    extension: str  # .pdf, .docx, .md, .txt, .png
    category: str  # chronicles, wiki, codex, ephemera, images
    is_scan: bool = False
    file_size_bytes: int = 0


class DocumentRepresentation(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    file_path: str
    format: str  # pdf, docx, md, txt, scan_pdf, png
    file_size_bytes: int = 0
    page_count: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Section(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: UUID
    title: Optional[str] = None
    level: int = 1
    position: int = 0
    parent_section_id: Optional[UUID] = None


class PageContent(BaseModel):
    page_number: int
    text: str


class SectionContent(BaseModel):
    title: str
    level: int
    content: str
    position: int


class ExtractionResult(BaseModel):
    document_id: UUID
    representation_id: UUID
    file_path: str
    format: str
    raw_text: str = ""
    pages: List[PageContent] = Field(default_factory=list)
    sections: List[SectionContent] = Field(default_factory=list)
    tables: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extraction_method: str = "text"


class Chunk(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: Optional[UUID] = None
    section_id: Optional[UUID] = None
    representation_id: Optional[UUID] = None
    content: str
    contextualized_content: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    chapter: Optional[str] = None
    section_title: Optional[str] = None
    position: int = 0
    token_count: int = 0
    embedding: Optional[List[float]] = None
    contextual_embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Asset(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    document_id: Optional[UUID] = None
    file_path: str
    asset_type: str  # figure_plate, portrait, heraldry, landscape, battle_painting, creature, relic
    entity_name: Optional[str] = None
    description: Optional[str] = None
    extracted_data: Dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Provenance(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    chunk_id: UUID
    document_id: Optional[UUID] = None
    representation_id: Optional[UUID] = None
    source_file: str
    page_number: Optional[int] = None
    extraction_method: str
    extraction_timestamp: datetime = Field(default_factory=datetime.utcnow)


class LogicalDocument(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    title: str
    source_category: str  # chronicles, wiki, codex, ephemera, images
    source_path: str
    representations: List[DocumentRepresentation] = Field(default_factory=list)
    sections: List[Section] = Field(default_factory=list)
    chunks: List[Chunk] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    is_valid: bool
    total_documents: int
    total_chunks: int
    total_assets: int
    empty_documents: List[str] = Field(default_factory=list)
    duplicate_chunks: int = 0
    warnings: List[str] = Field(default_factory=list)


class IngestionReport(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    documents_discovered: int = 0
    logical_documents_created: int = 0
    files_extracted: int = 0
    extraction_failures: List[Dict[str, str]] = Field(default_factory=list)
    ocr_processed_files: int = 0
    images_processed: int = 0
    figure_plates_extracted: int = 0
    atmospheric_art_described: int = 0
    synthetic_image_chunks: int = 0
    total_chunks_created: int = 0
    validation: Optional[ValidationReport] = None
