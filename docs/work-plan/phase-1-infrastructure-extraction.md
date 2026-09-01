# Phase 1 — Infrastructure + Content Extraction

**Timeline: Days 1–2**  
**Goal:** All documents extracted and chunked, databases running, project scaffold complete.

---

## Prerequisites

- Python 3.11+ installed
- Docker + Docker Compose installed
- Git repository initialized with meaningful commits
- API keys for chosen LLM and embedding providers (not needed yet, but should be confirmed)

---

## Step-by-Step Implementation

### 1.1 — Project Scaffold

Create the full project directory structure:

```
project/
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── discovery.py
│   │   ├── extraction.py
│   │   ├── ocr.py
│   │   ├── chunking.py
│   │   └── pipeline.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── document.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── postgres.py
│   │   ├── neo4j_db.py
│   │   └── migrations/
│   │       └── 001_initial_schema.sql
│   └── providers/
│       ├── __init__.py
│       └── embeddings.py
├── tests/
│   ├── __init__.py
│   ├── test_discovery.py
│   ├── test_extraction.py
│   ├── test_chunking.py
│   └── conftest.py
├── scripts/
│   ├── ingest.py
│   └── setup_db.py
├── docker-compose.yml
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md
```

**Deliverable:** All empty files created, project importable as a Python package.

---

### 1.2 — Docker Compose

Create `docker-compose.yml` with:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    ports: ["5432:5432"]
    environment:
      POSTGRES_DB: ashen_era
      POSTGRES_USER: ashen
      POSTGRES_PASSWORD: <from .env>
    volumes:
      - postgres_data:/var/lib/postgresql/data

  neo4j:
    image: neo4j:5-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/<from .env>
    volumes:
      - neo4j_data:/data
```

**Test:** `docker-compose up -d` → both services start and are accessible.

---

### 1.3 — Database Schemas

#### PostgreSQL schema (`migrations/001_initial_schema.sql`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id UUID PRIMARY KEY,
    title TEXT NOT NULL,
    source_category TEXT NOT NULL,  -- chronicles, wiki, codex, ephemera
    source_path TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE document_representations (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    file_path TEXT NOT NULL,
    format TEXT NOT NULL,  -- pdf, docx, md, txt, scan_pdf
    file_size_bytes INTEGER,
    page_count INTEGER
);

CREATE TABLE sections (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    title TEXT,
    level INTEGER,  -- heading level
    position INTEGER,  -- order within document
    parent_section_id UUID REFERENCES sections(id)
);

CREATE TABLE chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    section_id UUID REFERENCES sections(id),
    representation_id UUID REFERENCES document_representations(id),
    content TEXT NOT NULL,
    contextualized_content TEXT,  -- filled in Phase 2
    page_start INTEGER,
    page_end INTEGER,
    chapter TEXT,
    section_title TEXT,
    position INTEGER,
    token_count INTEGER,
    embedding vector(1024),  -- filled in Phase 2 (Voyage AI voyage-3-large = 1024 dims)
    contextual_embedding vector(1024),  -- filled in Phase 2
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE assets (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),
    file_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,  -- figure_plate, portrait, heraldry, landscape, battle_painting, creature, relic
    entity_name TEXT,
    description TEXT,  -- Gemini Vision generated description
    extracted_data JSONB,  -- structured data from figure plates
    embedding vector(1024),  -- filled in Phase 2
    metadata JSONB DEFAULT '{}'
);

CREATE TABLE provenance (
    id UUID PRIMARY KEY,
    chunk_id UUID REFERENCES chunks(id),
    document_id UUID REFERENCES documents(id),
    representation_id UUID REFERENCES document_representations(id),
    source_file TEXT NOT NULL,
    page_number INTEGER,
    extraction_method TEXT,  -- pymupdf, python-docx, markdown, txt, ocr
    extraction_timestamp TIMESTAMPTZ DEFAULT NOW()
);
```

#### Neo4j constraints (run via neo4j driver)

```cypher
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;
CREATE CONSTRAINT document_node_id IF NOT EXISTS FOR (d:Document) REQUIRE d.id IS UNIQUE;
CREATE CONSTRAINT chunk_node_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE;
CREATE CONSTRAINT asset_node_id IF NOT EXISTS FOR (a:Asset) REQUIRE a.id IS UNIQUE;
```

**Test:** Connect to PostgreSQL and Neo4j programmatically. Run schema creation. Verify tables and constraints exist.

---

### 1.4 — Configuration (`src/config.py`)

Central configuration using environment variables + `.env` file:

```python
# Key configuration items:
# - POSTGRES_URL
# - NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
# - CORPUS_PATH (path to Ashen_Era_Archive)
# - VOYAGE_API_KEY (for Voyage AI embeddings)
# - GEMINI_API_KEY (for Google Gemini LLM + Vision)
# - EMBEDDING_MODEL = "voyage-3-large"  # 1024 dims
# - LLM_MODEL = "gemini-2.5-flash"  # for batch processing
# - LLM_MODEL_STRONG = "gemini-2.5-pro"  # for answer generation
# - CHUNK_MAX_TOKENS, CHUNK_OVERLAP_TOKENS
```

**Deliverable:** `config.py` that loads from `.env` with sensible defaults.

---

### 1.5 — Data Models (`src/models/document.py`)

Define Pydantic/dataclass models:

```python
# Document — logical document (one per unique content, regardless of format)
# DocumentRepresentation — specific file (PDF, DOCX, etc.)
# Section — document structural unit (chapter, heading)
# Chunk — text chunk with metadata
# Asset — image/figure plate with description and extracted data
# Provenance — source tracing record
# CorpusInventory — complete corpus summary
```

**Test:** Models can be instantiated and serialized.

---

### 1.6 — File Discovery (`src/ingestion/discovery.py`)

Scan the `Ashen_Era_Archive/` directory and classify every file:

**Input:** Path to corpus root  
**Output:** List of discovered files with classification

```python
class DiscoveredFile:
    path: str
    filename: str
    extension: str  # pdf, docx, md, txt
    category: str   # chronicles, wiki, codex, ephemera, images
    is_scan: bool   # True if .scan.pdf
    file_size: int

def discover_corpus(corpus_path: str) -> list[DiscoveredFile]:
    """Recursively scan corpus directory and classify all files."""
```

**Logic:**
- Walk `chronicles/`, `wiki/`, `codex/`, `ephemera/`, `images/` directories
- Classify by parent directory
- Detect `.scan.pdf` suffix for scanned documents
- Skip non-document files (e.g., `README.txt`, `sample_questions.json`, image directories within wiki/codex)

**Expected output:**
| Category | Files | Formats |
|---|---|---|
| chronicles | 8 | 4×PDF + 4×DOCX |
| wiki | 95 | Markdown |
| codex | 6 | 3×PDF + 3×DOCX |
| ephemera | 145 | Mixed (PDF, DOCX, TXT, scan.pdf) |
| images | 15 | PNG |

**Test (`tests/test_discovery.py`):**
- Discovery finds all expected files (verify total count)
- Each file has correct category assignment
- Scanned PDFs are correctly identified
- No files outside the corpus are included

---

### 1.7 — Document Bundling

Group format variants into logical documents:

```python
def bundle_documents(files: list[DiscoveredFile]) -> list[LogicalDocument]:
    """Group files by filename stem into logical documents."""
```

**Logic:**
- Strip extension from filename
- Remove `.scan` suffix before grouping
- Files with the same stem → same logical document
- Example: `the_ashen_chronicles_volume_i_the_kindling_years.pdf` and `.docx` → one `LogicalDocument` with two representations

**Test:**
- Chronicles: 4 logical documents (each with PDF + DOCX)
- Codex: 3 logical documents (each with PDF + DOCX)
- Ephemera: verify documents with multiple formats are correctly bundled
- Wiki: each `.md` file is its own logical document

---

### 1.8 — Content Extraction (`src/ingestion/extraction.py`)

Extract text content from each file format:

```python
def extract_pdf(file_path: str) -> ExtractionResult:
    """Extract text from PDF using PyMuPDF. Returns text per page."""

def extract_docx(file_path: str) -> ExtractionResult:
    """Extract text from DOCX using python-docx. Returns text per paragraph."""

def extract_markdown(file_path: str) -> ExtractionResult:
    """Parse markdown, preserving heading structure."""

def extract_text(file_path: str) -> ExtractionResult:
    """Read plain text file."""

class ExtractionResult:
    document_id: str
    representation_id: str
    pages: list[PageContent]  # for PDFs
    sections: list[SectionContent]  # for structured documents
    raw_text: str
    metadata: dict
```

**For each format:**

| Format | Library | Strategy |
|---|---|---|
| PDF | PyMuPDF (`fitz`) | Extract per-page text, preserve page numbers |
| DOCX | `python-docx` | Extract paragraphs, detect headings by style |
| Markdown | `markdown-it-py` or regex | Parse heading structure (`#`, `##`, etc.) |
| TXT | Built-in `open()` | Read entire content |

**Important:** For logical documents with both PDF and DOCX, prefer **DOCX extraction** (cleaner text, better structure detection) and use **PDF extraction** as fallback or for page number mapping.

**Test (`tests/test_extraction.py`):**
- Extract a sample PDF → verify text is non-empty, page count matches
- Extract a sample DOCX → verify text is non-empty, paragraphs extracted
- Extract a sample Markdown → verify headings are detected correctly
- Extract a sample TXT → verify full content captured
- Verify no extraction returns empty content for a valid file

---

### 1.9 — OCR Processing (`src/ingestion/ocr.py`)

Process the ~16 `.scan.pdf` files:

```python
def ocr_scanned_pdf(file_path: str) -> ExtractionResult:
    """OCR a scanned PDF. Returns extracted text per page."""
```

**Options (choose one):**
- Tesseract via `pytesseract` — well-tested, free
- EasyOCR — better accuracy on some documents
- Docling — IBM's document processing library

**Test:**
- OCR a sample scanned PDF → verify text is extracted and non-empty
- Verify page count matches expected number

---

### 1.10 — Semantic Chunking (`src/ingestion/chunking.py`)

Format-aware chunking:

```python
def chunk_document(extraction: ExtractionResult, category: str) -> list[Chunk]:
    """Chunk extracted content using format-appropriate strategy."""

def chunk_wiki_article(extraction: ExtractionResult) -> list[Chunk]:
    """Split markdown wiki article on ## headings."""

def chunk_novel(extraction: ExtractionResult) -> list[Chunk]:
    """Split novel by chapter boundaries, then paragraph groups with overlap."""

def chunk_codex(extraction: ExtractionResult) -> list[Chunk]:
    """Keep tables intact. Split prose by heading/paragraph."""

def chunk_ephemera(extraction: ExtractionResult) -> list[Chunk]:
    """1-2 chunks per document (they are short)."""
```

**Chunking parameters (configurable):**
```
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 100
CHUNK_MIN_TOKENS = 50  # don't create tiny chunks
```

**Rules:**
- Never split mid-sentence
- Tables are always kept as single chunks
- Each chunk retains: `document_id`, `section_id`, `page`, `chapter`, `section_title`, `position`
- Overlap only applies to novel/prose chunking, not wiki sections or ephemera

**Test (`tests/test_chunking.py`):**
- Chunk a wiki article → verify each section becomes a chunk
- Chunk a novel chapter → verify chunks respect paragraph boundaries
- Chunk an ephemera document → verify 1-2 chunks
- Verify no chunk exceeds `CHUNK_MAX_TOKENS`
- Verify no chunk is below `CHUNK_MIN_TOKENS` (unless the source is shorter)
- Verify every chunk has correct metadata (document_id, page, section)

---

### 1.10 — Image Processing (`src/ingestion/images.py`)

Process all images in the corpus using Gemini Vision:

```python
def process_images(corpus_path: str, llm: LLMProvider) -> list[Asset]:
    """
    1. Discover all images (images/, wiki/images/, codex/images/)
    2. Classify image type from filename pattern
    3. Deduplicate (images/ and codex/images/ contain the same plates)
    4. Process figure plates → structured data extraction via Gemini Vision
    5. Process atmospheric art → detailed description via Gemini Vision
    6. Generate synthetic text chunks from descriptions
    7. Link images to wiki articles (parse markdown image references)
    8. Store asset records in PostgreSQL
    """

def process_figure_plate(image_path: str, llm: LLMProvider) -> Asset:
    """Extract structured data from a figure plate using Gemini Vision."""

def process_atmospheric_art(image_path: str, entity_name: str, llm: LLMProvider) -> Asset:
    """Generate detailed description of atmospheric art using Gemini Vision."""

def create_image_chunk(asset: Asset) -> Chunk:
    """Create a synthetic text chunk from an image asset's description/data."""
```

**Image classification from filename:**
```
plate_*                          → figure_plate (data extraction)
atmo_portrait_character_*        → portrait (description)
atmo_heraldry_faction_*          → heraldry (description — focus on emblems/motifs)
atmo_landscape_location_*        → landscape (description)
atmo_battle_painting_conflict_*  → battle_painting (description)
atmo_creature_creature_*         → creature (description)
atmo_relic_artifact_*            → relic (description — focus on visual details)
```

**Deduplication:** `images/` and `codex/images/` contain identical plates (same filenames). Process once, link to both locations.

**Wiki-image linking:** Parse the first line of wiki markdown files:
```markdown
![House Morvain](images/atmo_heraldry_faction_house_morvain.png)
```
Extract the image path and link the asset to the wiki document.

**Test (`tests/test_image_processing.py`):**
- Discover all images → verify counts (15 plates, 55 atmospheric, 15 codex duplicates)
- Process a figure plate → verify structured data extracted (entity_name, metric, value)
- Process an atmospheric portrait → verify description mentions the character
- Create synthetic chunk from plate → verify chunk contains extracted data values
- Wiki-image linking → verify asset linked to correct wiki document
- Deduplication → codex/images/ plates are not processed twice

---

### 1.11 — Ingestion Pipeline (`src/ingestion/pipeline.py`)

Orchestrate the full ingestion:

```python
def run_ingestion(corpus_path: str) -> IngestionReport:
    """
    1. Discover files
    2. Bundle into logical documents
    3. Extract content (per format)
    4. OCR scanned PDFs
    5. Process images (figure plates + atmospheric art via Gemini Vision)
    6. Chunk all documents (including synthetic image chunks)
    7. Store chunks + assets in PostgreSQL
    8. Register provenance
    9. Validate corpus
    10. Return report
    """
```

**The report should include:**
```
Documents discovered: X
Documents bundled: Y
Extraction successes: Z
Extraction failures: N (with details)
Chunks created: M
OCR processed: P
Images processed: I (figure plates: F, atmospheric: A)
Synthetic image chunks: S
Validation warnings: [...]
```

**Test:**
- Run pipeline on full corpus → verify report shows expected counts
- No extraction failures for known-good documents
- Every chunk has valid provenance
- Image assets stored in PostgreSQL with descriptions

---

### 1.12 — Corpus Validation

After extraction, validate:

```python
def validate_corpus(chunks: list[Chunk], documents: list[Document]) -> ValidationReport:
    """
    - Empty extraction detection (documents with no text)
    - Page count verification (PDF page count vs extracted pages)
    - Duplicate detection (identical chunks)
    - Missing document detection (expected files not found)
    - OCR quality checks (scanned PDFs with very short text)
    """
```

**Test:**
- Validation catches an intentionally empty extraction
- Validation flags a scanned PDF with suspiciously short output

---

## Acceptance Criteria

> **Do NOT proceed to Phase 2 unless ALL of the following are met:**

- [ ] `docker-compose up -d` starts PostgreSQL (pgvector) and Neo4j successfully
- [ ] PostgreSQL schema is created (documents, chunks, sections, provenance, **assets** tables)
- [ ] Neo4j constraints are created
- [ ] File discovery finds **all** corpus files with correct category classification — **including images**
- [ ] Document bundling correctly groups PDF/DOCX variants (4 chronicle pairs, 3 codex pairs)
- [ ] Content extraction succeeds for **every** non-scanned document (PDF, DOCX, MD, TXT)
- [ ] OCR extraction runs on all `.scan.pdf` files and produces non-empty text
- [ ] **Image processing:** All 15 figure plates processed with Gemini Vision → structured data extracted
- [ ] **Image processing:** All 55 wiki atmospheric images processed → descriptions generated
- [ ] **Image processing:** Synthetic text chunks created for all processed images
- [ ] **Wiki-image linking:** Image assets linked to their wiki articles
- [ ] Semantic chunking produces chunks with correct metadata (document_id, page, section)
- [ ] No chunk exceeds the configured maximum token limit
- [ ] Full ingestion pipeline runs end-to-end and produces a clean report
- [ ] Corpus validation passes with no critical errors
- [ ] All chunks (including synthetic image chunks) are stored in PostgreSQL with provenance records
- [ ] All unit tests pass: `pytest tests/test_discovery.py tests/test_extraction.py tests/test_chunking.py tests/test_image_processing.py`
- [ ] Ingestion report shows the expected document, chunk, and image counts

### Expected Numbers (approximate)

| Metric | Expected |
|---|---|
| Total files discovered | ~355 (including images) |
| Logical documents | ~200-220 |
| Chunks created (text) | ~1,500-3,000 (depends on chunking parameters) |
| Image assets processed | ~70 (15 unique plates + 55 atmospheric art) |
| Synthetic image chunks | ~70 |
| Total chunks (text + image) | ~1,570-3,070 |
| OCR files processed | ~16 |
| Extraction failures | 0 |

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_discovery.py` | File discovery, classification, scan detection, image detection |
| `tests/test_extraction.py` | PDF, DOCX, Markdown, TXT extraction |
| `tests/test_chunking.py` | Format-aware chunking, metadata, size limits |
| `tests/test_image_processing.py` | Image discovery, Gemini Vision extraction, synthetic chunks, wiki linking |
| `tests/conftest.py` | Shared fixtures (sample files, database connections) |

Run all tests: `pytest tests/ -v`
