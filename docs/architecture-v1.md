# Architecture Document v1 — Ashen Era Archive Intelligence System

**Track: 1B — Connecting Facts Across Thousands of Pages**  
**Date: Seppt 5 A2026**  
**Status: Approved (v1.1 — image processing + provider lock-in)**

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Core Architectural Principle](#2-core-architectural-principle)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Technology Stack & Rationale](#4-technology-stack--rationale)
5. [Dual Storage Architecture](#5-dual-storage-architecture)
6. [Ingestion Pipeline](#6-ingestion-pipeline)
7. [Image & Vision Processing Architecture](#7-image--vision-processing-architecture)
8. [Contextual Retrieval — Core Design](#8-contextual-retrieval--core-design)
9. [Hybrid Retrieval Architecture](#9-hybrid-retrieval-architecture)
10. [Entity & Knowledge Graph Architecture](#10-entity--knowledge-graph-architecture)
11. [Entity & Claim Extraction — Phased Strategy](#11-entity--claim-extraction--phased-strategy)
12. [Multi-Hop Retrieval](#12-multi-hop-retrieval)
13. [Evidence Management](#13-evidence-management)
14. [Answer Generation & Verification](#14-answer-generation--verification)
15. [Deterministic Citation Architecture](#15-deterministic-citation-architecture)
16. [Immutable Corpus Principle](#16-immutable-corpus-principle)
17. [Logical Document Model](#17-logical-document-model)
18. [Chunk Architecture](#18-chunk-architecture)
19. [Query-Time Data Flow](#19-query-time-data-flow)
20. [Evaluation Architecture](#20-evaluation-architecture)
21. [Observability](#21-observability)
22. [Project Structure](#22-project-structure)
23. [What We Are Deliberately NOT Building](#23-what-we-are-deliberately-not-building)

---

## 1. System Overview

The Ashen Era Archive Intelligence System is an evidence-grounded document intelligence platform that answers questions whose answers are distributed across multiple documents within the Ashen Era Archive — a corpus of 270+ unique documents (~415 files including format variants) spanning novels, wiki articles, codex data books, in-world ephemera, and figure plates.

The system is specifically designed for **Sub-track 1B**: questions that cannot be answered from any single document, requiring the system to discover, connect, and synthesize information across the entire archive.

### What makes this problem hard

1. **No single document contains the full answer** — facts are scattered across novels, codex entries, wiki articles, and ephemera
2. **Sources differ in reliability** — a tavern ballad and an official codex entry may contradict each other
3. **The world is entirely fictional** — no pretrained model has knowledge of it; the system must genuinely work from the documents
4. **Mixed formats** — PDF, DOCX, Markdown, plain text, and scanned pages
5. **Alias complexity** — the same character may be referred to by name, title, epithet, or pronoun across different documents

---

## 2. Core Architectural Principle

> **Retrieve evidence first, establish how the evidence connects, verify the evidence, and generate the answer last.**

The LLM is **not the knowledge source**. It is a reasoning engine that operates on evidence the retrieval system has assembled. This is the single most important architectural decision.

### Why this principle?

- The corpus is fictional — the LLM has zero prior knowledge of it
- Track 1B specifically tests cross-document evidence assembly, not generation quality
- Judges will evaluate whether the system *genuinely works* on inputs the team didn't script
- Hallucination on a fictional corpus is trivially detectable and will cost marks

---

## 3. High-Level Architecture

```
                           USER
                            │
                            ▼
                     ┌─────────────┐
                     │  Streamlit  │
                     │     UI      │
                     └──────┬──────┘
                            │
                            ▼
                     ┌─────────────┐
                     │  FastAPI    │
                     │  Backend    │
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       Query Analyzer  Retrieval    Knowledge
                       Orchestrator  Graph
              │             │             │
              ▼             ▼             ▼
       ┌───────────────────────────────────────┐
       │          Evidence Management          │
       └───────────────────┬───────────────────┘
                           │
                           ▼
                    Context Builder
                           │
                           ▼
                     ┌───────────┐
                     │    LLM    │
                     └─────┬─────┘
                           │
                           ▼
                   Answer Verifier
                           │
                           ▼
                  Citation Resolver
                           │
                           ▼
                   Grounded Answer
```

### Three-tier design

| Tier | Components | Responsibility |
|---|---|---|
| **Presentation** | Streamlit UI | Chat, evidence inspector, source viewer |
| **Application** | FastAPI, Query Analyzer, Retrieval Orchestrator, Evidence Manager, Context Builder, LLM, Verifier | Query processing, retrieval, reasoning, generation |
| **Data** | PostgreSQL + pgvector, Neo4j | Chunk storage, embeddings, FTS, entity graph |

---

## 4. Technology Stack & Rationale

### Backend: Python + FastAPI

**Decision:** Python with FastAPI for the backend API.

**Rationale:**
- Python has the strongest ecosystem for ML/NLP (embedding models, cross-encoders, document processing)
- FastAPI provides async support, automatic OpenAPI docs, and type validation
- The entire team is working in Python — no context-switching cost
- Every library we need (PyMuPDF, python-docx, sentence-transformers, neo4j driver, psycopg) is Python-native

**Alternatives considered:**
- Node.js — weaker ML ecosystem, would require Python subprocess calls anyway
- Go — fast but poor ML library support

---

### Vector Database: PostgreSQL + pgvector

**Decision:** Use PostgreSQL with the pgvector extension for vector storage and similarity search, and PostgreSQL Full-Text Search for BM25/lexical retrieval.

**Rationale:**
- Single database for relational data (documents, chunks, metadata, provenance) + vector search + text search
- pgvector supports HNSW indexes for fast approximate nearest neighbor search
- PostgreSQL FTS provides built-in BM25-equivalent ranking (`ts_rank`)
- Avoids running separate infrastructure (Pinecone, Weaviate, Elasticsearch)
- The corpus is ~1,277 pages — pgvector handles this scale trivially
- Mature, well-documented, easy to set up in Docker

**Alternatives considered:**
- Pinecone / Weaviate — unnecessary infrastructure complexity for this corpus size
- Elasticsearch — strong BM25 but adds another service; PostgreSQL FTS is sufficient
- ChromaDB / FAISS — no relational capabilities, would need a separate database anyway

---

### Graph Database: Neo4j

**Decision:** Use Neo4j for the entity/relationship knowledge graph.

**Rationale:**
- Track 1B questions require multi-hop reasoning: `Person → member_of → Faction → won → War`
- Cypher queries handle variable-depth traversals naturally:
  ```cypher
  MATCH (p:Person {name: "Ederon Fellgard"})-[:MEMBER_OF]->(f:Faction)-[:WON]->(w:War)
  RETURN w.name
  ```
- The same query in SQL requires multiple self-joins and becomes unreadable at 3+ hops
- Neo4j's graph algorithms (shortest path, community detection) could help with entity resolution
- Free Community Edition runs in Docker

**Alternatives considered:**
- PostgreSQL-only with relationship tables — works for 2 hops, painful at 3+. The sample questions clearly require 3+ hop reasoning.
- NetworkX (in-memory graph) — doesn't persist, no query language, doesn't scale
- No graph database — would force the LLM to do all the reasoning, which is unreliable for multi-hop

**Why not PostgreSQL-only?** Look at this sample question:
> "Which accord was ultimately won by the faction of which Ederon Fellgard is a member?"

This requires: `Person → member_of → Faction → won → Accord`. In SQL:
```sql
SELECT a.name FROM accords a
JOIN faction_wars fw ON a.id = fw.accord_id AND fw.outcome = 'won'
JOIN faction_members fm ON fw.faction_id = fm.faction_id
JOIN persons p ON fm.person_id = p.id
WHERE p.name = 'Ederon Fellgard';
```
This assumes you know the exact table structure and join paths. In Cypher:
```cypher
MATCH (p:Person {name: "Ederon Fellgard"})-[:MEMBER_OF]->(f)-[:WON]->(a:Accord)
RETURN a.name
```
The Cypher version is readable, flexible, and doesn't require pre-defined join paths.

---

### Frontend: Streamlit

**Decision:** Streamlit for the user interface instead of React/Vite.

**Rationale:**
- The evaluation rubric gives 0% specifically to UI framework choice — "Presentation" (10%) is about video/report/demo clarity
- A Streamlit app can be built in hours, not days
- Streamlit natively supports chat interfaces, data display, and interactive components
- Saves 2-3 days of frontend development time that can be spent on retrieval quality
- Judges need to be able to run it easily — `streamlit run app.py` is simpler than `npm install && npm run build`

**Alternatives considered:**
- React/Vite — excellent for production apps, but the time investment doesn't translate to marks
- Gradio — similar to Streamlit but weaker for custom layouts
- Terminal-only — too basic for a demo

---

### Embedding Model: Voyage AI

**Decision:** Use **Voyage AI** (`voyage-3-large`) as the primary embedding model, behind a provider abstraction interface.

**Rationale:**
- Voyage AI is specifically optimized for retrieval tasks and consistently ranks at the top of MTEB benchmarks
- `voyage-3-large` produces 1024-dimensional vectors — good balance of quality and storage
- Voyage natively distinguishes between **document embeddings** and **query embeddings** (asymmetric embedding), which improves retrieval accuracy
- API-based — no local GPU required
- The provider abstraction still allows swapping to other models if needed for experimentation

**Alternatives considered:**
- OpenAI `text-embedding-3-large` — good quality, but Voyage is retrieval-specialized
- BGE-M3 — open-source/local, but API-based is simpler for team velocity
- Gemini `text-embedding-004` — good but Voyage has stronger retrieval benchmarks

---

### LLM: Google Gemini

**Decision:** Use **Google Gemini** as the primary LLM, behind a provider abstraction interface.

**Model selection:**
- **Gemini 3.8 Flash** — primary model for entity extraction, contextualization, relationship extraction (fast, cheap, high throughput for batch processing)
- **Gemini 3.8 Flash** — for answer generation and verification (stronger reasoning when needed)
- **Gemini Flash with vision** — for image description and figure plate data extraction

**Rationale:**
- Gemini has strong multimodal (vision) capabilities — critical for processing the 85 images in the corpus
- Gemini Flash is extremely cost-effective for batch processing (entity extraction across ~2,000 chunks)
- Gemini Pro provides strong reasoning for complex answer generation
- Native structured output support (JSON mode) for reliable entity/relationship extraction
- The provider abstraction still allows swapping if needed

**Alternatives considered:**
- GPT-4o — strong but more expensive for batch processing
- Claude Sonnet 4 — excellent instruction following, but Gemini's vision and cost profile wins for this project

---

### Document Processing & NLP

| Library | Purpose |
|---|---|
| PyMuPDF (fitz) | PDF text extraction, page-level content |
| python-docx | DOCX text extraction |
| markdown-it-py or similar | Markdown parsing with heading structure |
| Built-in file I/O | Plain text files |
| Tesseract / EasyOCR / Docling | OCR for scanned PDFs (`.scan.pdf`) |
| Pillow (PIL) | Image loading, format handling |
| rapidocr-onnxruntime | Local OCR for figure plates — extracts printed text/numbers from data plates offline (~50ms/image on CPU, ~15MB, zero API calls) |
| Gemini Vision | Visual description of atmospheric art (portraits, heraldry, landscapes, battle paintings, creatures, relics) |
| spaCy + `en_core_web_sm` | Corpus-aware entity extraction helper: POS tagging, pronoun detection (for contextualization), noun chunk candidate extraction, and gazette EntityRuler matching. **Not** the primary NER — the gazette is. `en_core_web_sm` only (~12MB, ~50ms/chunk on CPU). `en_core_web_trf` dropped: too heavy (~1.3GB) for RAM-constrained ingestion and unreliable on fictional vocabulary. |
| Pydantic | Structured output schema enforcement for LLM relationship extraction |

**Rationale:** These are the standard, well-maintained Python libraries for each format. Figure plates contain printed text and numbers that a lightweight local OCR library (`rapidocr-onnxruntime`) extracts with near-perfect accuracy on CPU — no API calls needed, making extraction deterministic and reproducible. Gemini Vision is reserved for atmospheric art where visual scene description genuinely requires a vision-language model. The Gazette (built from wiki/codex filenames) is the primary NER layer — it provides near-100% recall for canonical entities without relying on a pretrained model trained on real-world text. spaCy `en_core_web_sm` acts as a lightweight helper: POS tagging for pronoun detection (contextualization), noun chunk extraction for candidate entities, and the EntityRuler for gazette pattern matching. `en_core_web_trf` is deliberately excluded — at ~1.3GB it strains the available RAM, and a BERT model trained on news/Wikipedia is unreliable on invented fictional vocabulary. Pydantic enforces structured output schemas when the LLM classifies relationships, preventing hallucinated relationship types. No need for heavier solutions (Apache Tika, Unstructured.io) given the corpus size.

---

### Infrastructure: Docker Compose

**Decision:** Docker Compose for local development and judge reproducibility.

**Rationale:**
- Judges must be able to run the project from the README alone (15% "Engineering best practices")
- `docker-compose up` starts PostgreSQL (pgvector) + Neo4j + the application
- Eliminates "works on my machine" issues
- No Kubernetes — massive overkill for a competition

---

## 5. Dual Storage Architecture

```
PostgreSQL + pgvector                        Neo4j
─────────────────────────                    ──────
Structured data:                             Graph data:
├── documents (metadata)                     ├── Entity nodes
├── document_representations                 │   ├── Person
├── sections                                 │   ├── Faction
├── chunks                                   │   ├── Place
│   ├── content (text)                       │   ├── Event
│   ├── embedding (vector)                   │   ├── Artifact
│   ├── contextual_embedding (vector)        │   └── Organization
│   └── metadata (jsonb)                     │
├── provenance records                       ├── Relationship edges
├── BM25 / FTS index (tsvector)             │   ├── MEMBER_OF
└── evidence records                         │   ├── LED
                                             │   ├── PARTICIPATED_IN
                                             │   ├── OCCURRED_AT
                                             │   ├── LOCATED_IN
                                             │   └── MENTIONED_IN
                                             │
                                             ├── Claim nodes
                                             └── Evidence links
```

### Why two databases?

Each database does what it's best at:

| Operation | PostgreSQL | Neo4j |
|---|---|---|
| Store chunks + metadata | ✅ Excellent | ❌ Wrong tool |
| Vector similarity search | ✅ pgvector | ❌ Not supported |
| BM25 / full-text search | ✅ Built-in FTS | ❌ Not supported |
| Multi-hop graph traversal | ❌ Painful at 3+ joins | ✅ Cypher is built for this |
| Entity relationship queries | ❌ Requires complex JOINs | ✅ Natural graph queries |
| Provenance tracking | ✅ Relational tables | ❌ Overkill |

---

## 6. Ingestion Pipeline

Ingestion is an **offline pipeline** that runs once before query-time. It never runs during normal question-answering.

```
IMMUTABLE CORPUS (read-only)
        │
        ▼
  File Discovery
  (scan all directories, classify by type + format)
        │
        ▼
  Document Bundling
  (PDF + DOCX variants → single logical document_id)
        │
        ▼
  Content Extraction
  (PyMuPDF for PDF, python-docx for DOCX, markdown parser, file I/O for TXT)
        │
        ▼
  OCR / Vision
  (Tesseract/EasyOCR for .scan.pdf files)
        │
        ▼
  Image Processing
  (Gemini Vision for figure plates + atmospheric art → text descriptions + data extraction)
        │
        ▼
  Structure Detection
  (identify chapters, sections, headings, tables, paragraphs)
        │
        ▼
  Semantic Chunking
  (format-aware splitting — see §17)
        │
        ▼
  Entity Extraction (Layer 1)
  (gazette + spaCy NER → entity annotations per chunk, zero LLM calls)
        │
        ▼
  Contextualization
  (hybrid: template prefix using metadata + spaCy entities for most chunks,
   LLM prefix for pronoun-heavy chunks only — see §8)
        │
        ▼
  Embedding Generation
  (standard embeddings + contextual embeddings → pgvector)
        │
        ▼
  Entity Storage
  (persist extracted entities → Neo4j nodes + MENTIONED_IN edges)
        │
        ▼
  Relationship Extraction (Layer 2)
  (co-occurrence pre-filter + LLM structured output → Neo4j edges)
        │
        ▼
  Claim Extraction (Layer 3 — stretch)
  (structured assertions → Neo4j claim nodes)
        │
        ▼
  Provenance Registration
  (link every derived artifact back to source document/page)
        │
        ▼
  Corpus Validation
  (empty extraction checks, page count verification, duplicate detection)
        │
        ▼
  Index Construction
  (BM25/FTS index, HNSW vector index)
```

---

## 7. Image & Vision Processing Architecture

The corpus contains **85 images** across three locations that carry information not available in any text document. This is a critical gap that must be addressed.

### Image Inventory

| Location | Count | Type | Content | Size |
|---|---|---|---|---|
| `images/` | 15 | Figure plates | **Data plates** with numerical values (threat ratings, garrison strengths, attunement costs) | ~30-45KB each |
| `wiki/images/` | 55 | Atmospheric art | Portraits, heraldry/banners, landscapes, battle paintings, relic illustrations | ~2MB each |
| `codex/images/` | 15 | Figure plates | Same plates as `images/` (duplicates within codex context) | ~30-45KB each |

### Why Images Are Critical

Several sample questions can **only** be answered from image content:

- *"What numerical rating is assigned to the Weeping Lurker?"* → Answer is **3**, only visible in the figure plate image
- *"What is the recorded garrison strength of Greyfell Citadel?"* → Answer is **3,695**, only visible in the figure plate image  
- *"What is the central emblem on the banner of House Morvain?"* → Answer requires visual inspection of the heraldry image
- *"In the portrait of Ignatz Ashgrove the Oathless, what object are they holding?"* → Answer requires visual inspection of the portrait

Even though we target Track 1B (text-based multi-hop), the figure plates contain **factual data** (numbers, names, scales) that may be needed to connect facts across documents. Ignoring images means missing data that text retrieval can never find.

### Image Processing Strategy

#### 1. Figure Plates (Data Extraction)

The 15 figure plates are structured data visualizations. Each contains:
- A title (entity name)
- A metric type (Threat Rating, Garrison Strength, Attunement Cost, etc.)
- A numerical value
- A scale or unit
- Sometimes a provenance note ("As entered into the Codex Vaeloria")

**Processing approach — Local OCR (zero API calls):**
```
Figure Plate Image
        │
        ▼
  RapidOCR (local text extraction, ~50ms on CPU)
        │
        ▼
  Raw OCR Text:
  "Weeping Lurker\nThreat Rating\n3\nof 10, per the Vanguard scale"
        │
        ▼
  Regex / Structured Parsing
  (extract entity name, metric type, numerical value, scale)
        │
        ▼
  Structured Data:
  {
    "entity_name": "Weeping Lurker",
    "metric": "Threat Rating",
    "value": 3,
    "scale": "0-10, per the Vanguard scale",
    "provenance": null
  }
        │
        ▼
  Store as:
  1. Asset record in PostgreSQL (image path, description, extracted data)
  2. Text chunk (generated description → embeddable and searchable)
  3. Entity link in Neo4j (plate → entity it describes)
```

**Why local OCR instead of Gemini Vision for plates?**
- Figure plates contain **printed text and numbers**, not complex visual scenes
- RapidOCR extracts this text with near-perfect accuracy on CPU (~50ms/image)
- Zero API calls → deterministic, reproducible, offline-capable
- Saves ~15 Gemini Vision calls for use on atmospheric art where a VLM is genuinely needed
- If OCR fails on a stylized plate, falls back to Gemini Vision for that specific plate

#### 2. Atmospheric Art (Visual Descriptions)

The 55 wiki images are artistic illustrations. They require descriptive text generation:

**Categories and what to extract:**

| Category prefix | Count | Extract |
|---|---|---|
| `atmo_portrait_character_*` | 12 | What the character looks like, what they're holding, armor/clothing details |
| `atmo_heraldry_faction_*` | 5 | Banner/emblem design, colors, central motif, symbols |
| `atmo_landscape_location_*` | 10 | Landscape features, architectural details, environment |
| `atmo_battle_painting_conflict_*` | 6 | Battle scene details, factions visible, key elements |
| `atmo_creature_creature_*` | 10 | Creature appearance, distinguishing features, size cues |
| `atmo_relic_artifact_*` | 12 | Artifact appearance, materials, inscriptions, motifs |

**Processing approach:**
```
Atmospheric Art Image
        │
        ▼
  Gemini Vision (detailed description)
        │
        ▼
  Text Description:
  "Portrait of Ignatz Ashgrove the Oathless. The figure is shown 
   standing in dark armor, holding a notched sword in their right 
   hand. Their expression is stern, with scarred features..."
        │
        ▼
  Store as:
  1. Asset record in PostgreSQL (image path, description)
  2. Text chunk (description → embeddable and searchable via hybrid retrieval)
  3. Entity link in Neo4j (image → entity it depicts)
  4. Document link (image → wiki article that references it)
```

**Gemini Vision prompt for atmospheric art:**
```
This is an illustration from a fantasy wiki article about "{entity_name}".
Describe this image in detail, focusing on:
1. What is depicted (person, place, creature, artifact, banner, battle)
2. Visual details that someone might ask about (colors, objects held, 
   symbols, emblems, motifs, inscriptions, materials)
3. Any text visible in the image
4. Distinguishing features

Be factual and specific. Do not speculate beyond what is visible.
```

#### 3. Wiki-Image Linking

Each wiki article's first line references its image:
```markdown
![House Morvain](images/atmo_heraldry_faction_house_morvain.png)
```

During markdown extraction, parse this reference to:
1. Link the image asset to the wiki document
2. Link the image asset to the corresponding entity in Neo4j
3. Include the image description in the wiki article's chunks (so text search finds it)

### Asset Table (PostgreSQL)

```sql
CREATE TABLE assets (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id),  -- the wiki/codex article this belongs to
    file_path TEXT NOT NULL,
    asset_type TEXT NOT NULL,  -- figure_plate, portrait, heraldry, landscape, battle_painting, creature, relic
    entity_name TEXT,  -- the entity this image depicts
    description TEXT,  -- Gemini Vision generated description
    extracted_data JSONB,  -- structured data from figure plates
    embedding vector(1024),  -- text embedding of the description (for retrieval)
    metadata JSONB DEFAULT '{}'
);
```

### Image → Entity Links in Neo4j

```cypher
// Link image assets to entities
MERGE (a:Asset {id: $asset_id})
SET a.type = $asset_type, a.file_path = $file_path

MATCH (e:Entity {name: $entity_name})
MERGE (a)-[:DEPICTS]->(e)

// Link to document
MATCH (d:Document {id: $document_id})
MERGE (d)-[:CONTAINS_IMAGE]->(a)
```

### Image Data as Searchable Chunks

Each image generates a **synthetic text chunk** that enters the standard retrieval pipeline:

```
Chunk content (for a figure plate):
"Figure plate: Greyfell Citadel. Recorded Garrison Strength: 3,695 souls 
under arms. As entered into the Codex Vaeloria. Figures verified by the 
Silent Choir."

Chunk content (for a portrait):
"Portrait illustration of Ignatz Ashgrove the Oathless from the wiki article. 
The figure is depicted in dark plate armor, holding a notched longsword in 
their right hand..."
```

These synthetic chunks:
- Get standard + contextual embeddings (like any other chunk)
- Are searchable via BM25 and dense retrieval
- Have provenance pointing to the original image file
- Link to entities in Neo4j

This means when someone asks *"What is the garrison strength of Greyfell Citadel?"*, the BM25/dense search will find the synthetic chunk from the figure plate, and the answer will cite the original image as the source.

---

## 8. Contextual Retrieval — Core Design

Contextual retrieval is a **core component**, not optional.

### The Problem

A chunk from a novel might say:
> "He then broke the accord and fled to the eastern marshes."

Without context, this chunk is nearly unretrievable for a question about a specific person or event. "He" could be anyone. "The accord" could be any accord. Dense embedding search will give this chunk a mediocre similarity score against most queries.

### The Solution

Before embedding, we prepend a **contextual prefix** that situates the chunk:

```
┌─────────────────────────────────────────────┐
│ ORIGINAL CHUNK                              │
│                                             │
│ "He then broke the accord and fled to the   │
│  eastern marshes."                          │
├─────────────────────────────────────────────┤
│ CONTEXTUALIZED CHUNK                        │
│                                             │
│ "From The Ashen Chronicles Vol II,          │
│  Chapter 7: The War Council at Red Vale.    │
│  Mentions: Ser Vael, Leaden Accord,         │
│  Ashen Vanguard.                            │
│                                             │
│  He then broke the accord and fled to the   │
│  eastern marshes."                          │
└─────────────────────────────────────────────┘
```

### How we generate contextual prefixes — Hybrid Approach

We use a **two-tier** strategy that minimizes LLM calls by leveraging the entity extraction (gazette + spaCy) already performed earlier in the ingestion pipeline:

#### Tier 1: Template Prefix (majority of chunks, zero API calls)

Built from chunk metadata (available from Phase 1) + spaCy entity annotations:

```python
def build_template_prefix(chunk, document, entities_in_chunk):
    parts = [f"From {document.title}"]
    if chunk.chapter:
        parts.append(f", {chunk.chapter}")
    if chunk.section_title:
        parts.append(f": {chunk.section_title}")
    parts.append(".")
    if entities_in_chunk:
        entity_names = [e.name for e in entities_in_chunk]
        parts.append(f" Mentions: {', '.join(entity_names)}.")
    return "".join(parts)
```

**Example output:**
```
"From The Ashen Chronicles Vol II, Chapter 7: The War Council at Red Vale.
 Mentions: Ser Vael, Ashen Vanguard, Leaden Accord."
```

This captures the **biggest retrieval improvements** — the embedding now knows which document, section, and entities are involved.

#### Tier 2: LLM Prefix (pronoun-heavy chunks only, ~500-800 API calls)

Some chunks are full of pronouns ("he", "she", "they") with few or no named entities. The template prefix can't help much because spaCy finds nothing to list.

**Detection (using spaCy):**
```python
def needs_llm_prefix(chunk, entities_in_chunk):
    doc = nlp(chunk.content)
    pronouns = [t for t in doc if t.pos_ == "PRON" and t.dep_ == "nsubj"]
    return len(pronouns) >= 2 and len(entities_in_chunk) <= 1
```

For these chunks, an LLM receives:
1. The full document title
2. The chapter/section heading
3. Surrounding chunks (previous + next) for local context
4. The chunk itself

The LLM generates a 1-3 sentence prefix that:
- Names the document and section
- Identifies key entities mentioned
- Resolves pronouns where possible ("He" → "Ser Vael")
- Does NOT add information not present in the document

### Dual embeddings

Every chunk gets **two** embeddings stored in pgvector:

| Column | Source | Used for |
|---|---|---|
| `embedding` | Raw chunk text | Standard dense retrieval |
| `contextual_embedding` | Contextualized chunk (prefix + raw text) | Contextual retrieval |

At query time, both embedding columns are searched, and results are combined via RRF fusion.

### Why this is core (not optional)

- Anthropic's research showed contextual retrieval reduces retrieval failure by 49% when combined with BM25
- This corpus is heavily interconnected — isolated chunks frequently lack the context needed for retrieval
- The hybrid approach keeps costs manageable: template prefixes for most chunks (free), LLM only for pronoun-heavy chunks (~500-800 calls)
- We can experimentally measure the improvement: standard vs. contextual retrieval

### Important constraint

The contextual prefix is **never treated as evidence**. The final answer always cites the **original chunk text** from the **original document**. The prefix exists solely to improve retrieval.

---

## 8. Hybrid Retrieval Architecture

```
                     QUERY
                       │
                       ▼
                Query Analyzer
                (classify type, extract entities, expand query)
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
      BM25           Dense         Entity
     Search          Search        Search
   (PostgreSQL     (pgvector ×2)  (Neo4j)
      FTS)
         │             │             │
         └─────────────┼─────────────┘
                       ▼
                   RRF Fusion
                (combine all ranked lists)
                       │
                       ▼
                Cross-Encoder
                  Reranker
              (top 50-100 → top 10-20)
                       │
                       ▼
              Candidate Evidence
```

### Why hybrid?

Each retrieval method solves different problems:

| Method | Strength | Example |
|---|---|---|
| **BM25** | Exact names, rare terms, dates | "Gravemaw Wyrm", "356 AS", "Leaden Accord" |
| **Dense (standard)** | Semantic meaning, paraphrasing | "who betrayed the king" ↔ "treason against the crown" |
| **Dense (contextual)** | Context-aware semantic matching | Chunks that mention "He" but are about Ser Vael |
| **Entity search** | Structured entity lookups | Find all chunks mentioning a specific person/faction |

### RRF (Reciprocal Rank Fusion)

Combines ranked lists from different retrieval methods:
```
RRF_score(d) = Σ  1 / (k + rank_i(d))
```
where `k` is a constant (typically 60) and `rank_i(d)` is the rank of document `d` in retrieval method `i`.

**Why RRF?** It doesn't require score normalization across different retrieval methods. BM25 scores and cosine similarity scores are on different scales — RRF only uses ranks.

### Cross-Encoder Reranking

After RRF fusion produces ~50-100 candidates, a cross-encoder scores each (query, candidate) pair jointly:
- The cross-encoder sees both the query and the chunk text simultaneously
- Much more accurate than bi-encoder similarity, but too slow to run on all chunks
- Reduces candidates to the top 10-20 highest-quality evidence pieces

---

## 9. Entity & Knowledge Graph Architecture

### Entity types

```
Person       — named characters (Ser Vael, Ederon Fellgard)
Faction      — organizations, houses (Ashen Vanguard, House Morvain)
Place        — locations (Red Vale, Greyfell Citadel)
Event        — wars, accords, battles (War of Drowned Light, Purge of Blackport)
Artifact     — named objects (Gauntlet of Sorrowfell, Thrice-Bound Edge)
Organization — non-faction groups (Iron Ring Cartel, Silent Choir)
Creature     — named creatures/species (Gravemaw Wyrm, Weeping Lurker)
Title        — ranks, positions (Last Warden, the Oathless)
```

### Relationship types

```
Person ──MEMBER_OF──────→ Faction
Person ──LED────────────→ Faction/Organization
Person ──PARTICIPATED_IN─→ Event
Person ──HOLDS──────────→ Artifact
Person ──HAS_TITLE──────→ Title
Person ──ALLIED_WITH────→ Person/Faction
Person ──OPPOSED────────→ Person/Faction
Faction ──WON───────────→ Event
Faction ──LOST──────────→ Event
Faction ──CONTROLS──────→ Place
Event ──OCCURRED_AT─────→ Place
Artifact ──LOCATED_AT───→ Place
Creature ──LAIR_AT──────→ Place
Entity ──MENTIONED_IN───→ Document/Chunk
```

### Neo4j schema

```cypher
// Constraints
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (e:Entity) REQUIRE e.id IS UNIQUE;

// Entity nodes carry:
// - id (UUID)
// - name (canonical name)
// - aliases (list of known aliases)
// - type (Person, Faction, etc.)
// - source_documents (list of document_ids where this entity appears)

// Relationship edges carry:
// - evidence_chunk_id (which chunk supports this relationship)
// - source_document_id (which document the evidence comes from)
// - confidence (extraction confidence score)
```

---

## 10. Entity & Claim Extraction — Corpus-Aware Hybrid Strategy

### Design Rationale: Why Not Generic NER?

The Ashen Era is an **entirely fictional world**. spaCy's pretrained NER models (`en_core_web_trf`, `en_core_web_sm`) are trained on real-world text (news, Wikipedia, OntoNotes). They produce unreliable or wrong labels for invented names like *Mournthrone*, *Vaelith*, *Ashen Spire*, and invented entity types like `FACTION`, `DYNASTY`, `ARTIFACT`, and `CREATURE` are not in their ontology at all.

The original approach called for LLM-based NER on every chunk (~2,000+ calls). On the Gemini free tier, this alone would take ~2+ hours and consume budget better spent on contextual prefixes and answer generation.

The revised approach is **corpus-aware from the start**:
- **The Gazette is the primary NER layer** — built from the corpus's own file structure, it has near-100% recall for canonical entities with zero model uncertainty
- **spaCy `en_core_web_sm` is a lightweight helper** — used for POS tagging, pronoun detection, and noun chunk candidate extraction only; NOT the entity authority
- **Targeted Gemini passes** handle the specific scenarios where deterministic methods fall short
- **Alias resolution** collapses name variants before writing to Neo4j

> **Key insight:** `en_core_web_trf` is dropped entirely. At ~1.3GB it strains available RAM, and its BERT-based NER produces misclassified or missed entities on fictional vocabulary. The gazette does the job better with zero model overhead.

### Entity Ontology (Fictional-World-Appropriate)

Standard NER types (`PERSON`, `ORG`, `GPE`) are insufficient. The Ashen Era ontology:

```
PERSON         — named characters (Ser Vael, Ederon Fellgard)
FACTION        — organized groups with political/military identity (Ashen Vanguard, Pale Covenant)
PLACE          — locations (Red Vale, Greyfell Citadel, Ashen Spire)
EVENT          — wars, accords, battles, purges (War of Drowned Light, Accord of Mournthrone)
ARTIFACT       — named objects (Gauntlet of Sorrowfell, Thrice-Bound Edge)
ORGANIZATION  — non-faction groups (Iron Ring Cartel, Silent Choir)
CREATURE       — named species/individuals (Gravemaw Wyrm, Weeping Lurker)
TITLE          — ranks, epithets (Last Warden, the Oathless, High Ember)
DYNASTY        — ruling houses/lineages (House Morvain, Third House of Elar)
DEITY          — gods, divine entities
CONCEPT        — recurring fictional terms (Attunement, the Ashen Tide)
DOCUMENT       — in-world named documents (Codex Vaeloria, Leaden Accord text)
BUILDING       — named structures (Emberveil Keep, the Ossuary)
MILITARY_UNIT — named armies/regiments
UNKNOWN        — capitalized candidate not yet classified
```

---

### Layer 1: Named Entity Recognition — Gazette-First Pipeline (Core)

**Mostly zero LLM calls. Fully offline for gazette and rules. Targeted Gemini for off-gazette candidates only.**

#### Step 1a: Build Gazette from Corpus Structure

The corpus encodes entity names directly in its file structure. This is a **closed fictional corpus** — every entity that has a canonical definition has a wiki or codex file:

```
wiki/wiki_person_ser_vael.md          → Person: "Ser Vael"
wiki/wiki_faction_ashen_vanguard.md   → Faction: "Ashen Vanguard"
wiki/wiki_place_red_vale.md           → Place: "Red Vale"
wiki/wiki_creature_gravemaw_wyrm.md   → Creature: "Gravemaw Wyrm"
wiki/wiki_artifact_thrice_bound_edge.md → Artifact: "Thrice-Bound Edge"
codex/codex_data_book_*.pdf           → Entity names from codex entries
```

Parse ~95 wiki filenames + codex entries → **complete entity dictionary with canonical names and types**. This gazette has:
- Near-100% recall for any entity with a wiki/codex article
- Zero model uncertainty — it is the ground truth for canonical entities
- No GPU, no download, no model load time
- **API cost: 0 calls**

#### Step 1b: spaCy Gazette Matching + Rule-Based Candidates

Apply a two-pass offline pipeline using `en_core_web_sm` (small, fast, ~12MB):

```
Pass 1: spaCy EntityRuler (gazette matching)
  - Load gazette patterns from wiki/codex filenames
  - Exact match + case-insensitive match
  - Output: gazette entities with correct type labels
  → All gazette entities captured with high confidence

Pass 2: Capitalized Phrase Rule Extraction
  - Extract capitalized 1–3 word noun chunks NOT already in gazette
  - Apply title prefix patterns: "Ser ", "Lord ", "Lady ", "High ", "the "
  - Tag as UNKNOWN (candidate, pending classification)
  → Catches entities mentioned only in novels/ephemera
```

Note: Pass 2 deliberately does NOT use spaCy's pretrained NER classifier. The `en_core_web_sm` NER model is unreliable on fictional vocabulary and would produce wrong type labels. Only its tokenizer, POS tagger, and noun chunk extractor are used.

**Example:**
```
Input:  "Ser Vael of the Ashen Vanguard rode to Red Vale, seeking Lord Drovenath."

Pass 1 output (gazette): [
  Person: "Ser Vael"        (gazette, confidence: 1.0)
  Faction: "Ashen Vanguard" (gazette, confidence: 1.0)
  Place: "Red Vale"         (gazette, confidence: 1.0)
]

Pass 2 output (rules): [
  UNKNOWN: "Lord Drovenath" (candidate, title prefix "Lord" detected)
]
```

- Gazette entities stored as Neo4j nodes immediately
- `UNKNOWN` candidates collected for Step 1c
- **This alone enables:** entity-based retrieval, basic cross-document linking
- **API cost: 0 calls**

#### Step 1c: Targeted Gemini Entity Pass for Off-Gazette Candidates

Not all entities have wiki articles. Characters introduced only in novels, ephemera (letters, tavern ballads, announcements), or in-passing references must be discovered differently.

**Which chunks get LLM entity extraction:**

| Trigger condition | Rationale | Est. chunk count |
|---|---|---|
| Chunk has 4+ `UNKNOWN` candidate phrases | Rule extraction found many unclassified names | ~50–100 |
| Chunk is from an ephemera document | Letters/ballads introduce the most off-gazette characters | ~20–40 docs |
| Chunk mentions title prefixes ("Ser", "Lord") with names not in gazette | High signal for off-gazette persons | ~30–60 |

**Gemini prompt for targeted entity extraction:**
```
This is a chunk from a fictional fantasy corpus called "Ashen Era".
The following candidate names were detected by pattern matching:
{candidates}

For each candidate, classify it using ONLY these types:
PERSON, FACTION, PLACE, EVENT, ARTIFACT, ORGANIZATION, CREATURE, 
TITLE, DYNASTY, DEITY, CONCEPT, DOCUMENT, BUILDING, MILITARY_UNIT, UNKNOWN

Also identify any other named entities you see that were missed.

Return JSON only:
{"entities": [{"mention": "...", "canonical_name": "...", "type": "...", "confidence": 0.0-1.0}]}

Do not invent entities. Only extract what is explicitly present in the text.

Chunk text:
{chunk_text}
```

- Store classified entities as Neo4j nodes with `confidence` score
- Add `canonical_name` to alias table (see Step 1d)
- **API cost: ~80–150 calls**

#### Step 1d: Alias Resolution — Collapse Name Variants

Across 270+ documents, the same entity may appear under many surface forms:
```
"Lord Vaelith"
"Vaelith"
"Lord V."
"the Lord of Mournthrone"
"Vaelith of the Third House"
```

Without alias resolution, Neo4j will have 5 separate nodes that are actually the same person. This breaks multi-hop reasoning.

**Resolution pipeline (no extra LLM calls):**
```
1. Exact match        → same entity
2. Prefix/suffix strip ("Lord ", " the Oathless") → compare core name
3. Levenshtein distance ≤ 2 → likely same entity (typo/variant)
4. Embedding similarity ≥ 0.92 → possible alias (semantic variant)
   (uses already-computed chunk embeddings, zero extra API calls)
5. Remaining ambiguous pairs → flag for manual or LLM verification
```

Output: **alias table** mapping every surface form to a canonical entity ID.

```
Alias Table:
"Lord Vaelith"            → entity_id: uuid-vaelith  (canonical: "Vaelith")
"Lord V."                 → entity_id: uuid-vaelith
"the Lord of Mournthrone" → entity_id: uuid-vaelith
"Vaelith of the Third House" → entity_id: uuid-vaelith
```

In Neo4j, each entity node carries an `aliases` property (list of all surface forms). All mentions throughout the corpus resolve to a single node, enabling accurate graph traversal.

---

### Layer 2: Relationship Extraction — Co-occurrence + LLM (Core)

#### Step 2a: Co-occurrence Pre-filter

For each chunk, identify which entities co-occur:
```
Chunk contains: [Ser Vael, Ashen Vanguard, Red Vale]
→ 3 entity pairs: (Ser Vael, Ashen Vanguard), (Ser Vael, Red Vale), (Ashen Vanguard, Red Vale)
→ Only chunks with 2+ entities proceed to LLM classification
```

This filters out ~30-40% of chunks (those with 0-1 entities), reducing LLM calls.

#### Step 2b: LLM Relationship Classification with Structured Output

Feed pre-extracted entities + their chunk context into Gemini with a strict Pydantic/JSON schema:
```
Input:  entities=["Ser Vael", "Ashen Vanguard"], chunk_text="..."
Output: {"relationships": [{"source": "Ser Vael", "target": "Ashen Vanguard", 
         "type": "MEMBER_OF", "evidence": "...", "confidence": 0.95}]}
```

The structured output schema forces the LLM to only emit relationships from the predefined type set (`MEMBER_OF`, `LED`, `WON`, `PARTICIPATED_IN`, etc.), preventing hallucinated relationship types.

- Store as Neo4j edges with evidence chunk references
- **This enables:** 2-3 hop graph traversal for multi-document questions
- **API cost: ~500-800 calls** (only chunks with 2+ entities)

---

### Layer 3: Claim Extraction (Stretch)

Extract factual assertions as structured claims:
```
Claim: {
  subject: "Ser Vael",
  predicate: "accused_of",
  object: "betraying the accord",
  source_document: "trial_transcript_concerning_...",
  source_type: "trial_transcript"
}
```

Key constraint: `accused_of` must NOT automatically become `committed`. The distinction between allegation and fact is critical for source reliability.

- **This enables:** conflict detection, source reliability comparison, nuanced answers

---

### LLM Budget Summary (Gemini Free Tier — Revised)

| Task | Method | API Calls | With 4 Keys @ 100 RPM |
|---|---|---|---|
| Entity extraction — gazette + rules (Layer 1a/1b) | Gazette + spaCy `en_core_web_sm` | **0** | N/A |
| Entity extraction — off-gazette candidates (Layer 1c) | Targeted Gemini pass | **~80–150** | ~2–3 min |
| Alias resolution (Layer 1d) | String matching + embeddings | **0** | N/A |
| Contextual prefixes (entity-rich chunks) | Template (metadata + gazette entities) | **0** | N/A |
| Contextual prefixes (pronoun-heavy chunks) | LLM | **~3** | <1 min |
| Relationship extraction (Layer 2) | Co-occurrence + LLM | **~500–800** | ~5–8 min |
| Image processing — figure plates | RapidOCR (local, offline) | **0** | N/A |
| Image processing — atmospheric art | Gemini Vision | **~55** | ~1.5 min |
| Answer generation | LLM (irreplaceable) | **1/query** | Real-time |
| **Total ingestion** | | **~638–1,008** | **~9–13 min** |

> **Previously:** ~1,055–1,655 calls with 3 keys. The corpus-aware revision saves an additional ~40% of the LLM budget by eliminating untargeted NER calls and replacing `en_core_web_trf` with the gazette + rule approach. Alias resolution now happens using already-computed embeddings, costing 0 extra API calls.

### Why phased?

1. Each layer adds value independently — if time runs out, Layer 1a/1b + Layer 2 still produce a working system
2. The gazette (Layer 1a) has the highest ROI: near-100% recall for canonical entities, zero cost
3. Layer 1c is scoped to only the chunks/documents that genuinely need it (~150 max)
4. Alias resolution (Layer 1d) runs after ingestion before Neo4j writes — no extra API calls
5. Layer 2 is optimized: LLM only classifies relationships between already-identified entities
6. Layer 3 is the most LLM-intensive — a stretch goal if time permits

---

## 11. Multi-Hop Retrieval

The core Track 1B capability. When a question requires connecting facts from multiple documents:

```
Question: "Which war did the faction containing Isolde Mournvale win?"

Step 1: Extract entity → "Isolde Mournvale"
Step 2: Neo4j query → Isolde Mournvale -[:MEMBER_OF]-> Faction X
Step 3: Neo4j query → Faction X -[:WON]-> War Y
Step 4: Retrieve evidence chunks for each hop
Step 5: Assemble complete answer with citations from each document
```

### QueryState

Every multi-hop query maintains explicit state:

```
QueryState:
├── original_query
├── query_type (single-hop / multi-hop)
├── identified_entities
├── sub_questions (decomposed)
├── retrieved_evidence (per hop)
├── discovered_entities (found during traversal)
├── discovered_relationships
├── known_claims
├── unresolved_entities
├── missing_information
├── contradictions
├── evidence_coverage
└── iteration_count (bounded)
```

### Bounds

```
Maximum search rounds:       3
Maximum candidates per round: 100
Maximum reranked evidence:    20
Maximum final evidence:       8–12
Maximum sub-questions:        5
```

These are configurable and should be experimentally tuned.

---

## 12. Evidence Management

### Evidence lifecycle

```
Retrieved chunks
      │
      ▼
Deduplication (remove same chunk retrieved by multiple methods)
      │
      ▼
Provenance validation (verify source document exists)
      │
      ▼
Source classification (codex, wiki, ephemera, chronicle — with characteristics, NOT hard-coded reliability)
      │
      ▼
Evidence grouping (group by entity/topic)
      │
      ▼
Coverage analysis (which parts of the question are covered?)
      │
      ▼
Conflict detection (do sources disagree?)
      │
      ▼
Evidence sufficiency scoring
```

### Source classification — NOT binary reliability

We do **not** hard-code:
```
codex = reliable
ephemera = unreliable
```

Instead, we track evidence characteristics:
```
Evidence:
├── source_type (codex, chronicle, wiki, ephemera)
├── document_subtype (ballad, trial_transcript, field_report, decree, etc.)
├── claim_strength (assertion, allegation, rumor, observation)
├── corroboration (how many other sources agree?)
├── contradiction (how many other sources disagree?)
└── provenance_quality (original source? second-hand?)
```

This allows the system to say "The sources disagree" rather than incorrectly deciding "Source X is true."

### Evidence sufficiency states

```
HIGH         — multiple sources agree, good coverage
MEDIUM       — some evidence, partial coverage
LOW          — limited evidence, gaps identified
INSUFFICIENT — cannot answer from available evidence
```

---

## 13. Answer Generation & Verification

### LLM constraints

The LLM prompt explicitly requires:
1. Use only supplied evidence
2. Do not invent facts
3. Distinguish claims from established facts
4. Acknowledge contradictions between sources
5. Preserve uncertainty ("the sources suggest..." not "the answer is...")
6. Cite evidence IDs (not document names — see §14)
7. Refuse when evidence is insufficient

### Verification pipeline

```
LLM → Draft Answer → Answer Verifier
                          │
            ┌─────────────┼─────────────┐
            ▼             ▼             ▼
       Claim check   Citation       Conflict
       (every claim   check          check
        has evidence?) (citations     (conflicts
                       exist?)        noted?)
            │             │             │
            └─────────────┼─────────────┘
                          ▼
                    Final Answer
```

---

## 14. Deterministic Citation Architecture

**Critical design decision:** The LLM does NOT generate citations. It references evidence IDs that get post-resolved.

```
Evidence Manager assigns:   EVIDENCE_001, EVIDENCE_002, ...

LLM receives:              "Based on [EVIDENCE_001] and [EVIDENCE_003]..."

LLM generates:             "Ser Vael was a member of the Ashen Vanguard [EVIDENCE_001]
                            and participated in the Leaden Accord [EVIDENCE_003]."

Citation Resolver maps:    EVIDENCE_001 → Royal Annals, page 84
                           EVIDENCE_003 → Trial Transcript, page 7

Final output:              "Ser Vael was a member of the Ashen Vanguard
                            [Royal Annals, p.84] and participated in the
                            Leaden Accord [Trial Transcript, p.7]."
```

**Why?** LLMs frequently hallucinate citation details (wrong page numbers, nonexistent documents). By using deterministic IDs that map to real evidence records, we eliminate this class of error entirely.

---

## 15. Immutable Corpus Principle

The original Ashen Era Archive must remain **completely untouched**. All derived data (extracted text, OCR, chunks, embeddings, entities, relationships) is stored separately.

```
Ashen_Era_Archive/         ← READ-ONLY, never modified
    chronicles/
    wiki/
    codex/
    ephemera/
    images/

PostgreSQL + Neo4j         ← All derived data lives here
```

The original files remain the **authoritative source**. Every derived artifact traces back to an original file path and page number.

---

## 16. Logical Document Model

PDF and DOCX representations of the same document are **bundled** under a single logical document ID.

```
Logical Document: "The Ashen Chronicles Volume I"
    ├── Representation: the_ashen_chronicles_volume_i_the_kindling_years.pdf
    └── Representation: the_ashen_chronicles_volume_i_the_kindling_years.docx
```

Both receive the same `document_id`. Evidence from the PDF and DOCX versions cannot accidentally be interpreted as two independent sources.

### Bundling rules

- Same filename stem (ignoring extension) → same logical document
- `.scan.pdf` variants are separate representations (scanned version of the same document)
- Ephemera with multiple format variants (e.g., `petition_concerning_thorn_wraith.docx`, `.pdf`, `.txt`) → same logical document

---

## 17. Chunk Architecture

Chunks are NOT fixed-size. Chunk boundaries are **format-aware**:

| Source type | Chunking strategy |
|---|---|
| **Wiki (Markdown)** | Split on `##` section headings. Each section is typically a semantic unit. |
| **Novels (PDF/DOCX)** | Split by chapter, then by paragraph groups. Use overlap (~100 tokens) to preserve context across boundaries. |
| **Codex data books** | Keep tables as single chunks. Split prose sections by heading/paragraph. |
| **Ephemera** | 1-2 chunks per document (they're short — most are 2-3KB). |
| **Scanned PDFs** | OCR → treat as plain text, chunk by paragraph. |

### Chunk metadata

Every chunk retains:
```
chunk_id           UUID
document_id        logical document reference
representation_id  which file it was extracted from
page               page number(s)
chapter            chapter name/number (if applicable)
section            section heading (if applicable)
position           position within section
content            raw text
embedding          standard vector
contextual_embedding  contextualized vector
metadata           JSON (source_type, document_subtype, etc.)
```

---

## 18. Query-Time Data Flow

Complete flow from user question to grounded answer:

```
1. USER QUESTION
   "Which war was won by the faction containing Isolde Mournvale?"

2. QUERY ANALYZER
   → Type: multi-hop
   → Entities: ["Isolde Mournvale"]
   → Sub-questions: ["Which faction does Isolde Mournvale belong to?",
                      "Which war did that faction win?"]

3. RETRIEVAL ORCHESTRATOR
   ├── BM25 search: "Isolde Mournvale faction war"
   ├── Dense search (standard): embed query → pgvector
   ├── Dense search (contextual): embed query → pgvector
   └── Entity search: Neo4j → find Isolde Mournvale node → traverse edges

4. RRF FUSION
   → Combine all ranked lists → top 50-100 candidates

5. CROSS-ENCODER RERANKER
   → Score each (query, candidate) pair → top 10-20

6. EVIDENCE MANAGER
   → Deduplicate, validate provenance, classify sources, detect conflicts

7. MULTI-HOP TRAVERSAL (if needed)
   → Neo4j: Isolde Mournvale → MEMBER_OF → Faction → WON → War
   → Fetch additional evidence for each hop

8. EVIDENCE SUFFICIENCY
   → Is there enough evidence to answer? HIGH/MEDIUM/LOW/INSUFFICIENT

9. CONTEXT BUILDER
   → Assemble structured context for LLM with evidence IDs

10. LLM GENERATION
    → Generate answer using only provided evidence
    → Reference evidence IDs

11. ANSWER VERIFICATION
    → Verify claims have evidence, citations exist, conflicts noted

12. CITATION RESOLUTION
    → Map EVIDENCE_xxx → actual document name + page

13. FINAL RESPONSE
    → Answer + citations + evidence status + supporting/conflicting evidence
```

---

## 19. Evaluation Architecture

Evaluation is a first-class component, not an afterthought.

### Evaluation dataset

The 19 sample questions from `sample_questions.json` form the initial evaluation set, supplemented by manually created questions covering:
- Direct single-document questions
- Multi-document questions (1B core)
- Multi-hop questions (1B core)
- Contradiction questions
- Insufficient-evidence questions

### Metrics

| Category | Metrics |
|---|---|
| **Retrieval** | Recall@K, Precision@K, MRR, nDCG |
| **Cross-document** | Multi-hop success rate, evidence completeness |
| **Answer** | Answer correctness, completeness, hallucination rate |
| **Citation** | Citation accuracy, citation completeness |
| **System** | Latency, LLM calls, cost per query |

### Experimental progression

Each phase produces measurable metrics. We track improvement at each stage:
```
Experiment 1: Dense-only RAG (baseline)
Experiment 2: + Contextual retrieval
Experiment 3: + BM25 hybrid
Experiment 4: + RRF fusion
Experiment 5: + Cross-encoder reranking
Experiment 6: + Entity retrieval
Experiment 7: + Multi-hop traversal
Experiment 8: + Claim/conflict detection (if time allows)
```

---

## 20. Observability

Every query produces a trace:

```
Query ID: <uuid>
├── Query classification: multi-hop
├── Extracted entities: ["Isolde Mournvale"]
├── Sub-questions: [...]
├── BM25 candidates: 42
├── Dense candidates: 38
├── Contextual candidates: 45
├── RRF merged: 87
├── Reranker top-20: [...]
├── Entities discovered: ["Isolde Mournvale", "Faction X"]
├── Graph traversals: 2 hops
├── Relationships found: [MEMBER_OF, WON]
├── Claims identified: 3
├── Conflicts detected: 0
├── Evidence sufficiency: HIGH
├── Evidence selected: 8
├── LLM tokens: 2,400
├── Verification: PASS
└── Final citations: 4
```

This is critical for:
- Debugging retrieval failures
- Demonstrating to judges why the system produced a specific answer
- Identifying bottlenecks and improvement opportunities

---

## 21. Project Structure

```
project/
├── Ashen_Era_Archive/          # IMMUTABLE — read-only corpus
│
├── src/
│   ├── ingestion/              # Offline ingestion pipeline
│   │   ├── discovery.py
│   │   ├── extraction.py
│   │   ├── ocr.py
│   │   ├── chunking.py
│   │   ├── contextualization.py
│   │   ├── entities.py
│   │   ├── relationships.py
│   │   ├── claims.py
│   │   └── pipeline.py
│   │
│   ├── retrieval/              # Query-time retrieval
│   │   ├── query_analyzer.py
│   │   ├── bm25_search.py
│   │   ├── dense_search.py
│   │   ├── entity_search.py
│   │   ├── fusion.py
│   │   ├── reranker.py
│   │   └── orchestrator.py
│   │
│   ├── knowledge/              # Knowledge graph operations
│   │   ├── graph.py
│   │   ├── entity_resolution.py
│   │   ├── multihop.py
│   │   └── evidence.py
│   │
│   ├── generation/             # LLM answer generation
│   │   ├── context_builder.py
│   │   ├── prompts.py
│   │   ├── llm.py
│   │   ├── verification.py
│   │   └── citations.py
│   │
│   ├── models/                 # Data models
│   │   ├── document.py
│   │   ├── entity.py
│   │   ├── evidence.py
│   │   └── query.py
│   │
│   ├── database/               # Database connections
│   │   ├── postgres.py
│   │   ├── neo4j_db.py
│   │   └── migrations/
│   │
│   ├── providers/              # External service abstractions
│   │   ├── embeddings.py
│   │   ├── llm_provider.py
│   │   └── reranker_provider.py
│   │
│   ├── api/                    # FastAPI application
│   │   ├── main.py
│   │   └── routes/
│   │
│   ├── evaluation/             # Evaluation framework
│   │   ├── evaluator.py
│   │   ├── metrics.py
│   │   └── experiments.py
│   │
│   └── config.py               # Central configuration
│
├── app/                        # Streamlit frontend
│   ├── streamlit_app.py
│   └── components/
│
├── tests/                      # Test suite
│
├── scripts/                    # Utility scripts
│   ├── ingest.py
│   ├── evaluate.py
│   └── setup_db.py
│
├── docs/                       # Documentation
│
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 22. What We Are Deliberately NOT Building

| Not building | Why |
|---|---|
| Kubernetes | Massive overkill for a competition demo |
| Microservices | A modular monolith is simpler and faster to develop |
| Kafka/message queues | No streaming requirements — batch ingestion + request/response queries |
| Custom-trained models | Corpus is too small; pretrained models with good prompting are sufficient |
| Persistent user memory | Not in the competition scope |
| Multi-tenancy | Single-user demo application |
| Autonomous unrestricted agents | Bounded, predictable retrieval is more trustworthy |
| Complex observability infrastructure (Datadog, Grafana) | Simple structured logging + query traces are sufficient |
| React/Vite frontend | Streamlit is faster to build, judges don't score framework choice |

> **Guiding principle:** Simple infrastructure + sophisticated retrieval and evidence logic.
