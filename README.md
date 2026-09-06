# Ashen Era Archive Intelligence System

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PostgreSQL 16](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Neo4j 5](https://img.shields.io/badge/Neo4j-5--community-008CC1.svg)](https://neo4j.com/)
[![Tests](https://img.shields.io/badge/tests-29%20passed-brightgreen.svg)]()

**Track 1B: Connecting Facts Across Thousands of Pages**  
SLIIT Codefest 2026 AI Competition — Powered by IFS

---

## System Overview

The **Ashen Era Archive Intelligence System** is an evidence-grounded document intelligence platform designed to answer complex, multi-hop questions whose answers are distributed across multiple heterogeneous documents within the Ashen Era Archive (270+ unique documents spanning chronicles, fan wikis, official lore codexes, in-world ephemera, and figure plates).

### Core Architectural Principle
> **Retrieve evidence first, establish how the evidence connects, verify the evidence, and generate the answer last.**

The LLM is treated strictly as an inference and synthesis engine over retrieved evidence, never as the authoritative knowledge source.

---

## Key Features

- **Format-Aware Ingestion Pipeline**: Ingests PDF, DOCX, Markdown, TXT, and scanned PDFs (`.scan.pdf` via Tesseract OCR).
- **Offline Figure Plate Extraction (RapidOCR)**: Extracts printed numbers, ratings, garrison strengths, and metrics from figure plates 100% locally on CPU with zero API calls.
- **Visual Asset Description (Gemini Vision)**: Generates structured descriptions for atmospheric artwork (portraits, heraldry banners, landscapes, relics) with automatic key rotation and rate limiting.
- **Offline Entity Extraction**: Dual-pass NER combining exact-match gazette rulers from corpus metadata with transformer NER (zero LLM calls).
- **Hybrid Contextual Retrieval**: Prepends metadata-driven template prefixes to chunks, with selective LLM prefix generation for pronoun-heavy passages.
- **Dual Vector Embeddings**: Stores standard and contextual embeddings in PostgreSQL (`pgvector` with HNSW indexing) using Voyage AI (`voyage-3-large`).
- **Deterministic Citations**: Resolves citations through deterministic evidence records rather than generative guesswork.

---

## Quick Start

### 1. Prerequisites
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- Docker & Docker Compose

### 2. Setup Environment
```bash
git clone https://github.com/AnuhasK/codefest-ifs-challenge.git
cd codefest-ifs-challenge/project

# Install dependencies with uv
uv sync

# Download spaCy lightweight model required for entity extraction
uv run python -m spacy download en_core_web_sm

# Start PostgreSQL (pgvector) and Neo4j
docker-compose up -d
```

### 3. Configure `.env`
Create a `.env` file from `.env.example`:
```env
POSTGRES_DB=ashen_era
POSTGRES_USER=ashen
POSTGRES_PASSWORD=ashen_secret
POSTGRES_HOST=localhost
POSTGRES_PORT=5433

NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=ashen_secret

CORPUS_PATH=./Ashen_Era_Archive
GEMINI_API_KEYS=key1,key2,key3,key4
VOYAGE_API_KEY=your_voyage_api_key
```

### 4. Run Tests
```bash
uv run pytest tests/ -v
```

### 5. Run Ingestion Pipeline
```bash
# Full ingestion with dual embeddings and Neo4j entity persistence
uv run python scripts/ingest.py

# Or fast metadata ingestion (skipping vector embeddings)
uv run python scripts/ingest.py --skip-embeddings

# Ingestion without Neo4j persistence
uv run python scripts/ingest.py --skip-neo4j

# Standalone sync of entities to Neo4j without re-embedding
uv run python scripts/sync_entities_to_neo4j.py
```

---

## Project Structure

```
project/
├── Ashen_Era_Archive/          # Authoritative read-only document archive
├── docs/                       # Architecture and phased implementation plans
├── scripts/
│   ├── ingest.py               # Main ingestion CLI
│   └── evaluate.py             # Baseline evaluation script
├── src/
│   ├── config.py               # Central environment & model configuration
│   ├── database/               # PostgreSQL & Neo4j clients, migrations
│   ├── models/                 # Pydantic data models (Document, Chunk, Asset)
│   ├── ingestion/              # Discovery, extraction, OCR, RapidOCR, chunking, NER
│   ├── providers/              # Embeddings, LLM provider, GeminiKeyRotator
│   ├── retrieval/              # Dense search, hybrid retrieval, fusion
│   └── generation/             # Context builder, grounded answer generation
└── tests/                      # Pytest suite (29 tests)
```
