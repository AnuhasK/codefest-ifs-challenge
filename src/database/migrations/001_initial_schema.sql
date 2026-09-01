-- Enable vector extension for pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Logical Documents (one per unique canonical content)
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    source_category TEXT NOT NULL,  -- chronicles, wiki, codex, ephemera, images
    source_path TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- File representations (e.g. PDF and DOCX for the same chronicle)
CREATE TABLE IF NOT EXISTS document_representations (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,  -- pdf, docx, md, txt, scan_pdf, png
    file_size_bytes BIGINT,
    page_count INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Document sections / headings hierarchy
CREATE TABLE IF NOT EXISTS sections (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    title TEXT,
    level INTEGER DEFAULT 1,
    position INTEGER DEFAULT 0,
    parent_section_id UUID REFERENCES sections(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Text Chunks with dual embeddings (standard + contextual)
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    section_id UUID REFERENCES sections(id) ON DELETE SET NULL,
    representation_id UUID REFERENCES document_representations(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    contextualized_content TEXT,
    page_start INTEGER,
    page_end INTEGER,
    chapter TEXT,
    section_title TEXT,
    position INTEGER DEFAULT 0,
    token_count INTEGER DEFAULT 0,
    embedding vector(1024),
    contextual_embedding vector(1024),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Image assets (figure plates, atmospheric illustrations, etc.)
CREATE TABLE IF NOT EXISTS assets (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    file_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,  -- figure_plate, portrait, heraldry, landscape, battle_painting, creature, relic
    entity_name TEXT,
    description TEXT,
    extracted_data JSONB DEFAULT '{}'::jsonb,
    embedding vector(1024),
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Provenance records tracing every chunk/asset to source file & location
CREATE TABLE IF NOT EXISTS provenance (
    id UUID PRIMARY KEY,
    chunk_id UUID REFERENCES chunks(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    representation_id UUID REFERENCES document_representations(id) ON DELETE SET NULL,
    source_file TEXT NOT NULL,
    page_number INTEGER,
    extraction_method TEXT NOT NULL,  -- pymupdf, python-docx, markdown, txt, ocr, gemini_vision
    extraction_timestamp TIMESTAMPTZ DEFAULT NOW()
);

-- Indices for performance
CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_rep_document_id ON document_representations(document_id);
CREATE INDEX IF NOT EXISTS idx_sections_document_id ON sections(document_id);
CREATE INDEX IF NOT EXISTS idx_provenance_chunk_id ON provenance(chunk_id);
CREATE INDEX IF NOT EXISTS idx_assets_document_id ON assets(document_id);
CREATE INDEX IF NOT EXISTS idx_assets_entity_name ON assets(entity_name);
