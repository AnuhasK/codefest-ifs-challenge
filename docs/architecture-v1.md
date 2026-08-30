# Architecture Document v1 — Ashen Era Archive Intelligence System

**Track: 1B — Connecting Facts Across Thousands of Pages**  
**Date: 30 August 2026**  
**Status: Approved**

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Core Architectural Principle](#2-core-architectural-principle)
3. [High-Level Architecture](#3-high-level-architecture)
4. [Technology Stack & Rationale](#4-technology-stack--rationale)
5. [Dual Storage Architecture](#5-dual-storage-architecture)
6. [Ingestion Pipeline](#6-ingestion-pipeline)
7. [Contextual Retrieval — Core Design](#7-contextual-retrieval--core-design)
8. [Hybrid Retrieval Architecture](#8-hybrid-retrieval-architecture)
9. [Entity & Knowledge Graph Architecture](#9-entity--knowledge-graph-architecture)
10. [Entity & Claim Extraction — Phased Strategy](#10-entity--claim-extraction--phased-strategy)
11. [Multi-Hop Retrieval](#11-multi-hop-retrieval)
12. [Evidence Management](#12-evidence-management)
13. [Answer Generation & Verification](#13-answer-generation--verification)
14. [Deterministic Citation Architecture](#14-deterministic-citation-architecture)
15. [Immutable Corpus Principle](#15-immutable-corpus-principle)
16. [Logical Document Model](#16-logical-document-model)
17. [Chunk Architecture](#17-chunk-architecture)
18. [Query-Time Data Flow](#18-query-time-data-flow)
19. [Evaluation Architecture](#19-evaluation-architecture)
20. [Observability](#20-observability)
21. [Project Structure](#21-project-structure)
22. [What We Are Deliberately NOT Building](#22-what-we-are-deliberately-not-building)

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

### Embedding Model: Provider Abstraction

**Decision:** Abstract the embedding model behind a provider interface so we can switch between Voyage, OpenAI, BGE-M3, or Gemini without code changes.

**Rationale:**
- Different embedding models have different strengths (multilingual, code, retrieval-optimized)
- We want to experimentally compare models and pick the best for this corpus
- API-based models (Voyage, OpenAI) are easy to start with; local models (BGE-M3) avoid API costs
- The competition rewards experimental methodology — showing we tested multiple embeddings is valuable

---

### LLM: Provider Abstraction

**Decision:** Abstract the LLM behind a provider interface.

**Rationale:** Same as embeddings — we want to be able to swap between GPT-4o, Claude, Gemini Flash, etc. without code changes. Cheaper models can handle entity extraction; stronger models handle answer generation.

---

### Document Processing

| Library | Purpose |
|---|---|
| PyMuPDF (fitz) | PDF text extraction, page-level content |
| python-docx | DOCX text extraction |
| markdown-it-py or similar | Markdown parsing with heading structure |
| Built-in file I/O | Plain text files |
| Tesseract / EasyOCR / Docling | OCR for scanned PDFs (`.scan.pdf`) |

**Rationale:** These are the standard, well-maintained Python libraries for each format. No need for heavier solutions (Apache Tika, Unstructured.io) given the corpus size.

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
  Structure Detection
  (identify chapters, sections, headings, tables, paragraphs)
        │
        ▼
  Semantic Chunking
  (format-aware splitting — see §17)
        │
        ▼
  Contextualization
  (generate contextual prefixes for each chunk — see §7)
        │
        ▼
  Embedding Generation
  (standard embeddings + contextual embeddings → pgvector)
        │
        ▼
  Entity Extraction (Layer 1)
  (NER via LLM → Neo4j entity nodes)
        │
        ▼
  Relationship Extraction (Layer 2)
  (entity co-occurrence + LLM → Neo4j edges)
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

## 7. Contextual Retrieval — Core Design

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
│  This passage describes Ser Vael's actions  │
│  following the signing of the Leaden Accord.│
│                                             │
│  He then broke the accord and fled to the   │
│  eastern marshes."                          │
└─────────────────────────────────────────────┘
```

### How we generate contextual prefixes

For each chunk, an LLM receives:
1. The full document title
2. The chapter/section heading
3. Surrounding chunks (previous + next) for local context
4. The chunk itself

The LLM generates a 1-3 sentence prefix that:
- Names the document and section
- Identifies key entities mentioned
- Resolves pronouns where possible
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
- The cost is manageable: one LLM call per chunk during ingestion (offline, not at query time)
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

## 10. Entity & Claim Extraction — Phased Strategy

### Layer 1: Named Entity Recognition (Core)

Extract entities from every chunk using LLM:
```
Input:  "Ser Vael of the Ashen Vanguard rode to Red Vale..."
Output: [Person: "Ser Vael", Faction: "Ashen Vanguard", Place: "Red Vale"]
```

- Store as Neo4j nodes
- Link each entity to the chunks/documents where it appears
- **This alone enables:** entity-based retrieval, basic cross-document linking

### Layer 2: Relationship Extraction (Core)

For chunks containing multiple entities, extract relationships:
```
Input:  chunk containing [Ser Vael] and [Ashen Vanguard]
Output: (Ser Vael)-[:MEMBER_OF]->(Ashen Vanguard)
```

- Store as Neo4j edges with evidence references
- **This enables:** 2-3 hop graph traversal for multi-document questions

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

### Why phased?

1. Each layer adds value independently — if time runs out, Layer 1 + Layer 2 still works
2. Layer 1 is fast, reliable, and has the highest ROI
3. Layer 3 is the most LLM-intensive and error-prone
4. A working system with Layer 1+2 + good hybrid retrieval answers most 1B questions

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
