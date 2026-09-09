# System Limitations & Engineering Trade-Offs

**System:** Ashen Era Archive Intelligence System  
**Competition:** SLIIT Codefest 2026 AI Competition — Powered by IFS  
**Primary Track:** 1B — Connecting Facts Across Thousands of Pages  
**Also Covers:** 1A (Rich Multimodal Answers) · 1C (Iterative Agentic Search)  
**Date:** September 2026  
**Status:** Comprehensive Analysis & Post-Evaluation Review  

---

## Table of Contents

1. [Executive Summary & Engineering Assessment](#1-executive-summary--engineering-assessment)
2. [End-to-End Latency Profile (The Core UX Bottleneck)](#2-end-to-end-latency-profile-the-core-ux-bottleneck)
3. [Evaluation Metric Saturation & Benchmarking Discontinuities](#3-evaluation-metric-saturation--benchmarking-discontinuities)
4. [Knowledge Graph & Entity Resolution Boundaries](#4-knowledge-graph--entity-resolution-boundaries)
5. [Epistemological Boundaries & Incomplete Claim Graph (Layer 3)](#5-epistemological-boundaries--incomplete-claim-graph-layer-3)
6. [Ingestion, OCR & Multimodal Fragilities](#6-ingestion-ocr--multimodal-fragilities)
7. [Chunking Boundary & Context Truncation Artifacts](#7-chunking-boundary--context-truncation-artifacts)
8. [Operational, Infrastructure & Concurrency Limits](#8-operational-infrastructure--concurrency-limits)
9. [Approaches Tried That Failed (Post-Mortem Analysis)](#9-approaches-tried-that-failed-post-mortem-analysis)
10. [Production Hardening Roadmap](#10-production-hardening-roadmap)

---

## 1. Executive Summary & Engineering Assessment

The Ashen Era Archive Intelligence System successfully achieved a **100% completion rate (20/20 questions)** on the official benchmark suite, producing zero hallucinated citations, correctly resolving 3-hop cross-document relationships via Neo4j, and detecting 26 complex source contradictions across the 415-file archive.

However, an honest engineering review requires confronting what does *not* work seamlessly, where trade-offs were made, and where the boundaries of the architecture lie. In enterprise deployments, an AI system that hides its limitations is dangerous.

### Core Trade-Off Summary

```
                      ACCURACY & COMPLETENESS
                                ▲
                                │   ★ Our System
                                │   (90s latency, 100% citation grounding,
                                │    bounded 3-hop graph verification)
                                │
                                │
                                │               Naive RAG
                                │               (3s latency, 40% hallucination,
                                │                fails on 3-hop questions)
                                └────────────────────────────────►
                                                              SPEED
```

The system intentionally sacrificed instantaneous sub-second response times in favor of exhaustive cross-document evidence discovery, deterministic verification, and multi-source conflict checking.

---

## 2. End-to-End Latency Profile (The Core UX Bottleneck)

### The Quantitative Reality

Across the 20 benchmark sample questions evaluated with `gemini-3.6-flash`, the system recorded the following latency profile:

| Sub-Track | Questions | Completed | Mean Latency | Min Latency | Max Latency |
|---|:---:|:---:|:---:|:---:|:---:|
| **1A: Rich Answers (Multimodal)** | 11 | 11 / 11 (100%) | **84.73 s** | 38.2 s | 133.59 s |
| **1B: Cross-Document (Multi-Hop)** | 7 | 7 / 7 (100%) | **108.19 s** | 76.1 s | 152.40 s |
| **1C: Iterative Agentic Search** | 2 | 2 / 2 (100%) | **55.14 s** | 48.2 s | 62.08 s |
| **Entire Benchmark Suite** | **20** | **20 / 20 (100%)** | **89.98 s** | **38.2 s** | **152.40 s** |

### Root Cause Latency Breakdown

For a representative 1B multi-hop question (e.g., *1b_007: 108.2s*), execution time is spent across the following pipeline stages:

```
Total Query Latency: ~108s
├── 1. Query Analysis & Entity Extraction (Gemini Flash API)       : ~3.5s  (3%)
├── 2. Hop 1: 4-Stream Retrieval (Dense + Contextual + BM25 + Neo4j): ~2.2s  (2%)
├── 3. Hop 1: Cross-Encoder Reranking (FlashRank ONNX)            : ~0.8s  (1%)
├── 4. Hop 1: Graph Expansion & Intermediate Sufficiency Scoring    : ~1.8s  (2%)
├── 5. Hop 2: Formulate Sub-queries & 4-Stream Retrieval          : ~2.4s  (2%)
├── 6. Hop 2: Cross-Encoder Reranking                             : ~0.9s  (1%)
├── 7. Hop 3 (if required): Additional Expansion & Verification   : ~2.1s  (2%)
├── 8. Evidence Management (Deduplication, Conflict Detection)     : ~4.5s  (4%)
├── 9. LLM Grounded Answer Generation (Gemini 3.6 Flash)           : ~18.5s (17%)
└── 10. Rate Limit Throttling & Network Jitter (Gemini Free Tier)  : ~71.3s (66%)
```

### Critical Takeaways & Limitations

1. **Free-Tier Rate Limiting is the Dominant Bottleneck:** Over 60% of total elapsed time is spent waiting on per-minute RPM throttles and inter-request sleep intervals required to keep the free-tier Gemini API keys from exhausting their quotas.
2. **Algorithmic Latency is Acceptable (~15–25s):** The local Python pipeline (PostgreSQL vector search, Neo4j graph traversals, FlashRank reranker, and evidence deduplication) executes in under 12 seconds combined.
3. **UX Implication:** While acceptable for asynchronous research synthesis or deep analytical reporting, an average wait time of ~90 seconds is unsuitable for real-time conversational chat without progressive response streaming.

---

## 3. Evaluation Metric Saturation & Benchmarking Discontinuities

### The Hit@K Ceiling Effect (Phases 2–4)

In early development (Phases 2 through 4), the retrieval evaluation script used `compute_recall_at_k`:

```python
def compute_recall_at_k(retrieved_items, target_keywords, k):
    # Old logic: Returned 1.0 if ANY single target keyword appeared in ANY top-k chunk
    for item in retrieved_items[:k]:
        if any(kw.lower() in item["content"].lower() for kw in target_keywords):
            return 1.0
    return 0.0
```

#### Why This Failed:
- Because the FlashRank cross-encoder reliably placed the primary source chunk at Rank 1, `Hit@K` scored **1.0 (100%)** across all baseline and hybrid tests.
- This produced a false sense of security. On a 1B question requiring both Document A (character $\rightarrow$ faction) and Document B (faction $\rightarrow$ accord), the system was scored as 100% successful even if Document B was completely missing from the retrieved context.

### The Metric Discontinuity (Phase 5+)

In Phase 5 (§5.0), the metric was replaced with **Joint Multi-Target Recall@K**:

```python
def compute_joint_recall_at_k(retrieved_items, hop_targets, k):
    # New logic: Requires at least one chunk in top-k to match Hop-1 targets
    # AND a separate chunk in top-k to match Hop-2 targets
    for hop_keywords in hop_targets:
        if not any(item_matches(item, hop_keywords) for item in retrieved_items[:k]):
            return 0.0
    return 1.0
```

#### Limitation:
- **Historical Incomparability:** Phase 2 and Phase 3 benchmark scores cannot be directly plotted on the same graph as Phase 5–8 scores.
- **Corpus Coverage Limitation:** The benchmark consists of the 20 official competition sample questions. While 100% pass rates were demonstrated on this set, 20 questions across 415 documents (~1,277 pages) cannot statistically guarantee zero blind spots in obscure corners of the archive.

---

## 4. Knowledge Graph & Entity Resolution Boundaries

### The Alias Resolution "Epithet Trap"

The entity resolution pipeline (`src/knowledge/entity_resolution.py`) employs a three-tier heuristic:
1. Exact canonical gazette match.
2. Title and honorific stripping (`Ser`, `Lord`, `Archon`, `Lady`).
3. String distance (Levenshtein) and dense vector embedding cosine similarity ($\ge 0.92$).

#### Where It Breaks:
- **Poetic Epithets:** A character whose formal name is *Vaelith* may be referred to in a tavern ballad as *"The Pale Raven"* or *"The Thorn of the High Pass"*. If no explicit apposition appears in the same paragraph (e.g., *"Vaelith, known as the Pale Raven"*), the embedding similarity between the name and the poetic metaphor is well below 0.92 ($\approx 0.61$).
- **Heavily Abbreviated Initials:** In ephemera letters, signatures such as *"— V."* or *"L. C."* are too short for statistical distance metrics and are filtered out as noise.
- **Resulting Impact:** In such cases, the system creates two separate entity nodes in Neo4j. A graph query traversing from the epithet will not reach relationships attached to the canonical person node, forcing the system to fall back on dense contextual retrieval.

```
Graph Disconnection Example:

[Node: "Vaelith"] ──[:MEMBER_OF]──► [Node: "Iron Covenant"]
       ▲
       │ (FAILED RESOLUTION: Similarity 0.61 < 0.92)
       │
[Node: "The Pale Raven"] (Isolated node — multi-hop traversal dead-ends)
```

### In-Memory Traversal vs Graph Scale

- Neo4j traversals are executed dynamically at query time using parameterized Cypher queries.
- While instantaneous on this corpus (~1,200 entities, ~4,000 edges), Cypher traversals are not cached in an in-memory subgraph cache (e.g., RedisGraph or precomputed transitive closures). Repeated queries traversing identical paths re-execute the same graph matches.

---

## 5. Epistemological Boundaries & Incomplete Claim Graph (Layer 3)

### Designed Architecture vs Implementation Reality

In the original architectural design (`docs/architecture.md` §11), the entity layer was planned in three hierarchical tiers:

```
Layer 1: Entities & Aliases        ──► [FULLY IMPLEMENTED]
Layer 2: Relationships & Edges     ──► [FULLY IMPLEMENTED]
Layer 3: Epistemic Claim Graph     ──► [POSTPONED / PARTIALLY IMPLEMENTED]
```

### The Layer 3 Limitation

- **What Was Intended:** A formal epistemological graph where edges are not simple facts, but first-class `Claim` nodes with source reliability weights:
  $$\text{Source}_{\text{Ballad}} \xrightarrow{\text{ALLEGES}} \text{Claim} \xleftarrow{\text{REFUTES}} \text{Source}_{\text{Codex}}$$
- **What Was Actually Built:** 
  1. The Evidence Manager collects all retrieved passages.
  2. A dedicated Gemini Flash LLM prompt inspects evidence pairs and classifies semantic contradictions or qualifications.
  3. Contradictions are formatted as explicit user-facing warnings.
- **Why It Matters:**
  - The system surfaces disagreements (e.g., *"Sources disagree: The Chronicle states X, whereas the Codex states Y"*), but it does **not compute probabilistic Bayesian belief updates**.
  - It cannot quantitatively calculate: *"Based on source provenance weights (Codex=0.95, Tavern Ballad=0.30), Statement X has an 82% posterior probability of being historically canonical."*

---

## 6. Ingestion, OCR & Multimodal Fragilities

### Degraded Scanned Bitmaps (`.scan.pdf`)

The corpus contains 15 scanned PDF files in `ephemera/`. These are not digital text PDFs; they are 300-DPI grayscale bitmap scans of mock historical documents.

| Extraction Mode | Accuracy on Digital PDFs | Accuracy on Scanned PDFs | Observed Failure Mode |
|---|:---:|:---:|---|
| **PyMuPDF Native** | 100% | 0% | Returns empty string (no embedded text layer) |
| **Local Tesseract OCR** | 99.2% | ~78.4% | Character confusion on gothic fonts (`rn` $\rightarrow$ `m`, `cl` $\rightarrow$ `d`) |
| **Gemini Vision Fallback** | 99.8% | ~94.1% | Excellent transcription, but subject to API rate limits |

#### Impact on Search:
When Tesseract misrecognizes characters in low-contrast scanned letters, exact-match BM25 (`tsvector`) search fails for those specific tokens. Retrieval must rely entirely on dense Voyage embeddings, which exhibit higher noise tolerance but lower exact-match precision.

### Figure Plate Layout Variance

- The 15 data figure plates are processed via `rapidocr-onnxruntime`.
- RapidOCR extracts printed numerical tables (garrison counts, threat ratings) with high fidelity when text is arranged in rectangular grids.
- **Failure Case:** In 2 figure plates containing radial dial illustrations or non-standard visual legends, RapidOCR extracted the numeric values as an unassociated list of numbers, requiring post-hoc heuristic regex parsing to associate numbers with entity keys.

---

## 7. Chunking Boundary & Context Truncation Artifacts

### Token-Based Sliding Window Artifacts

Chunks are generated with a target size of **400 tokens** and a **50-token overlap**.

```
[Chunk N: ...the Duke declared war on the southern provinces. At the council,]
                                    ▲ Chunk Boundary Split
[Chunk N+1: council, Ser Vael withdrew his allegiance and sealed the gates...]
```

- **Pronoun Splitting:** In narrative chronicles, sentences introducing an action are frequently separated from the preceding sentence that established the grammatical subject.
- **Mitigation Implemented:** Tier 1 Contextual Prefixes prepend the chapter and document title. Tier 2 LLM prefixes resolve pronouns.
- **Residual Limitation:** In dense battle chronicles, if three entities were mentioned in paragraph 1 and referenced via *"the former"* and *"the latter"* across a 400-token boundary, the Tier 1 template prefix cannot fully resolve which entity performed which action.

### Cross-Encoder Context Truncation (512-Token Limit)

- The local FlashRank model (`ms-marco-TinyBERT-L-2-v2`) has a maximum input sequence length of **512 tokens**.
- The cross-encoder receives the concatenated string: `query + [SEP] + chunk_content`.
- When an expanded contextual chunk (380 tokens + 60 token prefix) is paired with a complex multi-hop question (50 tokens), the trailing 20–40 tokens exceed the 512-token limit and are silently truncated during cross-encoder self-attention scoring.
- If the critical link of the second hop is located in the final sentence of that chunk, its cross-encoder ranking score may be artificially penalized.

---

## 8. Operational, Infrastructure & Concurrency Limits

### Free-Tier API Rate Limits

The system relies on external API providers (Google Gemini and Voyage AI):
- **Gemini Free Tier:** 5 Requests Per Minute (RPM) and 20 Requests Per Day (RPD) per key.
- **Voyage AI:** API rate and credit limits.

#### Failure Modes:
- **Key Pool Exhaustion:** While `GeminiKeyRotator` round-robins across 12 API keys, running back-to-back benchmark suites without pause will consume the daily RPD limit across all keys, halting execution until UTC midnight.
- **Concurrent Request Thrashing:** The backend application is architected as an `async` FastAPI service, but upstream API rate limits cap concurrent throughput. Under load from multiple simultaneous users, requests queue up and average latency degrades linearly.

### Database Host Port Mapping Collision Workaround

- Docker Compose maps PostgreSQL container port `5432` to host port **`5433`** (`ports: ["5433:5432"]`).
- While this solved collisions with local host databases, any script invoked without loading `src/config.py` (e.g., standard `psql -U ashen ashen_era`) defaults to port 5432 and fails to connect.

---

## 9. Approaches Tried That Failed (Post-Mortem Analysis)

Engineering progress is defined as much by rejected paths as by successful ones. The following table catalogs major technical approaches attempted during development that failed and were formally abandoned:

| # | Failed Approach | Intended Goal | Why It Failed (Root Cause) | Replacement Solution |
|:---:|:---|:---|:---|:---|
| **1** | **spaCy Transformer NER (`en_core_web_trf`)** | High-accuracy entity recognition | 1.3GB model size strained local RAM; model trained on OntoNotes misclassified fantasy factions as `ORG` and missed `CREATURE`/`ARTIFACT`. | **Corpus-Aware Gazette-First NER** (0 model cost, 100% precision on canonical entities). |
| **2** | **PostgreSQL-Only Relational Joins for Multi-Hop** | Single database simplicity | Multi-hop paths (3+ hops) required complex self-joins and recursive CTEs that broke when relationship types varied dynamically. | **Neo4j Graph Database** with Cypher queries. |
| **3** | **Unbatched LLM NER (2,143 chunk calls)** | Granular entity coverage | Free-tier rate limits (5 RPM) caused ingestion to estimate at >2.5 hours runtime, frequently failing on 429 quota exhaustion. | **Batched LLM NER** (50 chunks per prompt), reducing total calls to ~43. |
| **4** | **Autonomous ReAct Agent Search Loop** | Dynamic human-like iterative search | Agent suffered from search drift, hallucinated irrelevant search queries, and entered infinite loops on ambiguous queries. | **Bounded State Machine (`QueryState`)** with max 3 hops and evidence sufficiency scoring. |
| **5** | **Hit@K Benchmark Metric** | Measure retrieval quality | FlashRank placed hop-1 at Rank 1, saturating metric at 100% and hiding the failure to retrieve hop-2 documents. | **Joint Multi-Target Recall@K** requiring both hop-1 and hop-2 evidence in top-K. |
| **6** | **Pure Gemini Vision for All 85 Images** | Universal multimodal processing | High latency, rapid quota exhaustion, and stochastic errors when reading tabular numbers on figure plates. | **RapidOCR local ONNX** for figure plates; Vision reserved for atmospheric art. |
| **7** | **Freeform Generative Citation Footnotes** | Readable document citations | LLM hallucinated page numbers, chapter titles, and combined non-existent documents into single references. | **Deterministic Citation Tokens (`EVIDENCE_xxx`)** resolved via regex to database locators. |

---

## 10. Production Hardening Roadmap

To transition this competition-winning system into an enterprise-grade document intelligence platform, the following engineering milestones must be executed:

```
┌────────────────────────────────────────────────────────────────────────┐
│                        ENTERPRISE ROADMAP                              │
├────────────────────────────────────────────────────────────────────────┤
│ 1. LATENCY REDUCTION                                                   │
│    • Migrate from Gemini Free Tier to Enterprise Gemini Pro / Flash    │
│    • Implement Server-Sent Events (SSE) for streaming UI responses     │
│    • Add Redis-backed semantic caching for recurring query paths        │
│                                                                        │
│ 2. KNOWLEDGE GRAPH MATURATION                                          │
│    • Implement Layer 3 Epistemic Claim Graph with Bayesian weighting   │
│    • Deploy Cross-Document Coreference Resolution for poetic epithets   │
│    • Precompute and index common graph paths in Neo4j                  │
│                                                                        │
│ 3. MULTIMODAL ROBUSTNESS                                               │
│    • Train a domain-specific fine-tuned OCR model for scanned ephemera │
│    • Integrate table-structure recognition (e.g., Table-Transformer)   │
│                                                                        │
│ 4. DISTRIBUTED ARCHITECTURE                                            │
│    • Decouple ingestion into asynchronous Celery/Redis worker queues   │
│    • Add Multi-Tenant organization isolation & RBAC                    │
└────────────────────────────────────────────────────────────────────────┘
```
