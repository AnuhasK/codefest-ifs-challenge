Here is the complete summary of the issues encountered (and how they were resolved), along with a detailed catalog of all scripts, modules, and functions created in **Phase 1**.

---

## Part 1: Issues Encountered & Resolutions

| # | Issue | Root Cause | Resolution |
|---|---|---|---|
| **1** | **Scanned PDFs had 0 text (`.scan.pdf`)** | The 15 `.scan.pdf` files in `ephemera/` are pure scanned bitmap images with no embedded text layer, and native Tesseract OCR was not installed in Windows PATH. | Implemented a multi-tier OCR pipeline in `src/ingestion/ocr.py`: tries PyMuPDF text layer $\rightarrow$ PyMuPDF OCR $\rightarrow$ **Gemini 2.5 Flash / Vision multimodal transcription**. |
| **2** | **Image categorization in document bundling** | Standalone `.png` files in subdirectories (`wiki/images/`, `codex/images/`) were initially picked up as text documents by `bundle_documents()`, leading to 15 unchunked document validation warnings. | Updated `discovery.py` to route all `.png`/`.jpg` extensions to the `images` asset category and restricted `bundle_documents()` strictly to text formats (`.pdf`, `.docx`, `.md`, `.txt`). |
| **3** | **PyMuPDF deprecation warning** | `import fitz` triggered a deprecation notice from PyMuPDF v1.25+. | Replaced with `import pymupdf as fitz` across `extraction.py` and `ocr.py`. |
| **4** | **Missing dependencies / imports during initial run** | `NameError: name 're' is not defined` in `pipeline.py` and missing `UUID`, `List` in `ocr.py`. | Added missing standard library imports (`re`, `UUID`, `List`, `BytesIO`). |
| **5** | **Console output buffering on Windows** | When subprocesses were spawned as background tasks, Python buffered stdout output, making logs appear empty until process completion. | Added `flush=True` to all `print()` statements in `pipeline.py` and `ingest.py`. |

---

## Part 2: Functions and Scripts Created

### 1. Configuration & Infrastructure
* **`src/config.py`**:
  * Loads environment variables from `.env`.
  * Exports database connection URLs, API keys (`GEMINI_API_KEY`, `VOYAGE_API_KEY`), model configurations (`voyage-3-large`, `gemini-2.5-flash`, `gemini-2.5-pro`), and chunk sizing parameters (`CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS`).
* **`docker-compose.yml`**:
  * Defines container services for **PostgreSQL 16 + pgvector** (`5432`) and **Neo4j 5 Community** (`7474`, `7687`).
* **`src/database/migrations/001_initial_schema.sql`**:
  * Creates `vector` extension and 5 core tables: `documents`, `document_representations`, `sections`, `chunks` (with 1024-dim vector columns for Voyage AI), `assets`, and `provenance`.

---

### 2. Database Modules
* **`src/database/postgres.py`**:
  * `get_connection()`: Returns a `psycopg3` connection with `pgvector` registered.
  * `get_db_connection()`: Context manager for transactional DB operations with automatic commit/rollback.
  * `init_postgres()`: Executes `001_initial_schema.sql` to initialize database tables and indices.
* **`src/database/neo4j_db.py`**:
  * `Neo4jConnection`: Class wrapping the Neo4j Python driver.
    * `execute_query(cypher, parameters)`: Executes Cypher read queries.
    * `execute_write(cypher, parameters)`: Executes transactional write queries.
    * `init_schema()`: Creates uniqueness constraints on `Entity(id)`, `Document(id)`, `Chunk(id)`, and `Asset(id)`.
  * `get_neo4j_connection()`: Singleton connection provider.
  * `init_neo4j()`: Convenience initializer for graph constraints.

---

### 3. Data Models (`src/models/document.py`)
Defines Pydantic models for type safety:
* `DiscoveredFile`: Raw corpus file with format and category.
* `DocumentRepresentation`: Physical file record (PDF, DOCX, scan, etc.).
* `Section`: Structural heading/chapter representation.
* `PageContent` / `SectionContent`: Extracted text containers.
* `ExtractionResult`: Standardized output across all extractor types.
* `Chunk`: Text chunk with metadata, positions, and vector placeholder.
* `Asset`: Visual asset record (figure plate data, atmospheric art description).
* `Provenance`: Traceability link tying a chunk/asset back to its source file and page.
* `LogicalDocument`: Canonical bundled document containing multiple representations.
* `ValidationReport` & `IngestionReport`: Pipeline verification statistics.

---

### 4. Ingestion Modules
* **`src/ingestion/discovery.py`**:
  * `discover_corpus(corpus_path)`: Recursively scans the archive, classifies categories (`chronicles`, `wiki`, `codex`, `ephemera`, `images`), and flags `.scan.pdf` files.
  * `bundle_documents(files)`: Bundles format duplicates (e.g. PDF and DOCX of the same novel) into a single `LogicalDocument`.

* **`src/ingestion/extraction.py`**:
  * `extract_pdf(file_path, doc_id, rep_id)`: Extracts page-by-page text using PyMuPDF.
  * `extract_docx(file_path, doc_id, rep_id)`: Extracts heading hierarchy, prose paragraphs, and data tables using `python-docx`.
  * `extract_markdown(file_path, doc_id, rep_id)`: Extracts section headers, content, and image links (`![alt](path)`).
  * `extract_text(file_path, doc_id, rep_id)`: Reads raw plain-text ephemera.
  * `extract_document_representation(...)`: Dispatcher routing extraction to the proper format handler.

* **`src/ingestion/ocr.py`**:
  * `ocr_page_with_gemini(pil_img)`: Sends rendered scanned page pixmaps to Gemini Flash for visual transcription.
  * `ocr_scanned_pdf(file_path, doc_id, rep_id)`: Fallback OCR pipeline orchestrating text extraction $\rightarrow$ PyMuPDF OCR $\rightarrow$ Gemini Vision transcription.

* **`src/ingestion/images.py`**:
  * `classify_image_type(filename)`: Classifies image filename patterns into 7 categories (`figure_plate`, `portrait`, `heraldry`, `landscape`, `battle_painting`, `creature`, `relic`) and parses entity names.
  * `process_image_with_gemini(image_path, asset_type, entity_name)`: Calls Gemini Vision to extract numerical data from figure plates (Threat Ratings, Garrison Strengths) or generate descriptions for atmospheric art.
  * `process_corpus_images(corpus_path, use_vision)`: Discovers all images, deduplicates duplicate plates across folders, and creates `Asset` records.

* **`src/ingestion/chunking.py`**:
  * `approximate_token_count(text)`: Word-to-token estimator (~1.3 tokens/word).
  * `chunk_paragraphs_with_overlap(paragraphs, max_tokens, overlap_tokens)`: Sliding-window chunker preserving paragraph integrity and token overlap.
  * `chunk_document(extraction, doc)`: Format-aware chunker (splits wiki by `##` headings, novels by chapter paragraphs with overlap, keeps codex tables intact).
  * `create_image_chunk(asset)`: Generates searchable synthetic text chunks from image metadata and descriptions.

* **`src/ingestion/pipeline.py`**:
  * `validate_ingested_corpus(documents, chunks, assets)`: Validates that all documents produced chunks, detects duplicates, and checks asset counts.
  * `save_to_database(documents, chunks, assets, provenance_records)`: Batch persists all data models into PostgreSQL in a single transaction.
  * `run_ingestion(corpus_path, use_vision, persist_db)`: Orchestrates the entire offline pipeline end-to-end and returns an `IngestionReport`.

---

### 5. CLI Scripts
* **`scripts/setup_db.py`**: Runs PostgreSQL schema migration and Neo4j constraint creation.
* **`scripts/ingest.py`**: CLI tool with flags (`--corpus-path`, `--no-vision`, `--dry-run`) to run corpus ingestion and display summary statistics.

---

### 6. Test Suite (`tests/`)
* `tests/conftest.py`: Shared pytest fixtures (`corpus_root`).
* `tests/test_discovery.py`: Tests file discovery, scan detection, and PDF+DOCX document bundling.
* `tests/test_extraction.py`: Tests content extraction across PDF, DOCX, Markdown, and TXT formats.
* `tests/test_chunking.py`: Tests semantic chunking and paragraph overlap.
* `tests/test_image_processing.py`: Tests image classification, plate detection, and synthetic chunk creation.
*(All 11 unit tests passing).*