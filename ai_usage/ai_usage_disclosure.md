# AI Usage Disclosure & Human-AI Collaboration Record

**Competition:** SLIIT Codefest 2026 AI Competition — Powered by IFS  
**Track:** 1B (Primary: Multi-Document Multi-Hop Reasoning) · 1A (Rich Answers) · 1C (Iterative Search)  
**Date:** September 2026  
**Status:** Official Submission Disclosure  

---

## Table of Contents

1. [Collaboration Philosophy & Academic Integrity](#1-collaboration-philosophy--academic-integrity)
2. [Catalog of AI Tools & Models Used](#2-catalog-of-ai-tools--models-used)
3. [Human Direction: Where Humans Steered, Questioned & Corrected AI](#3-human-direction-where-humans-steered-questioned--corrected-ai)
4. [Prompt Engineering & Verification Techniques](#4-prompt-engineering--verification-techniques)
5. [Exported Chat Log Catalog](#5-exported-chat-log-catalog)
6. [Summary of Independent Human Contributions](#6-summary-of-independent-human-contributions)

---

## 1. Collaboration Philosophy & Academic Integrity

In accordance with the Codefest 2026 competition guidelines:

> *"You are free — and expected — to use AI tools in your work. What earns marks is not the polish of your output alone, but the quality of your thinking: the approach you chose, why you chose it, what you tried, what failed, and how well your solution actually works."*

AI was used as an **accelerator, pair programmer, and sounding board**, never as an unmonitored generator. The system's architectural principles, data engineering, evaluation methodology, and critical design pivots were conceived, evaluated, and directed by the human engineering team.

### Independent Research-Driven Architecture (Not Blind AI Generation)
Crucially, the system architecture was **not blindly taken or generated from AI**. Instead, the foundation of the system was developed through extensive independent technical research by the team across industry engineering blogs, ML community discussions (such as practitioner findings on Reddit's `r/GraphRAG` and `r/LanguageTechnology`), and published technical literature (including Anthropic's Contextual Retrieval methodologies and hybrid search evaluations).

The team gathered proven, state-of-the-art patterns—such as dual-storage topologies (PostgreSQL pgvector + Neo4j), corpus-aware gazettes to overcome fictional domain hallucination, local ONNX cross-encoders, and reciprocal rank fusion—and actively injected these researched concepts into the AI dialogues. AI tools (ChatGPT and Antigravity) were directed to help compare trade-offs, formulate, and synthesize these diverse research inputs into a structured, unified design document (`docs/architecture-v1.md`) tailored to the constraints of the Ashen Era fantasy corpus. Every module, phase, and acceptance gate was rigorously evaluated, challenged, and verified against empirical failure modes.

This document discloses all tools used, the exact scope of their usage, key moments of human intervention where AI-suggested solutions were rejected or corrected, and the index of accompanying chat transcripts.

---

## 2. Catalog of AI Tools & Models Used

| Tool / Model | Provider / Mode | Functional Area | Specific Purpose in Project |
|---|---|---|---|
| **Google Gemini 3.6 / 3.8 Flash** | Google Cloud / API | Application Pipeline | (1) Batch semantic entity extraction (50 chunks/call); (2) Tier-2 contextual prefix generation for pronoun-heavy chunks; (3) Grounded answer generation constrained to retrieved evidence; (4) Source conflict detection. |
| **Google Gemini Vision** | Google Cloud / API | Ingestion Pipeline | Visual scene description of 55 atmospheric art images (portraits, heraldry banners, landscapes, battle scenes, relics). |
| **Voyage AI (`voyage-3-large`)** | Voyage AI / API | Retrieval Engine | Asymmetric 1024-dimensional dense vector embeddings for documents (`input_type="document"`) and queries (`input_type="query"`). |
| **FlashRank Cross-Encoder** | Local ONNX (`ms-marco-TinyBERT-L-2-v2`) | Retrieval Engine | Local CPU reranking of fused candidate chunks (<20ms latency, zero API calls). |
| **RapidOCR** | Local ONNX (`rapidocr-onnxruntime`) | Ingestion Pipeline | Deterministic, offline optical character recognition for extracting tabular data (threat ratings, garrison strengths) from 15 figure plates on CPU. |
| **Antigravity IDE / Claude 3.7 Sonnet** | Anthropic / IDE Assistant | Development & Engineering | Code architecture review, refactoring, test creation, debugging rate limits and SQL queries, documentation authoring. |
| **ChatGPT (GPT-4o)** | OpenAI / Web Interface | Ideation & Research Synthesis | Formulating independent research (blogs, Reddit posts, technical articles) into the initial architecture design; structuring the 8-phase implementation roadmap; early brainstorming on Cypher schemas and evaluation edge cases. |

---

## 3. Human Direction: Where Humans Steered, Questioned & Corrected AI

The 15% rubric criterion for *Human-AI Collaboration Quality* emphasizes **human oversight, iterative refinement, and questioning AI output**. Below are the major instances during development where AI proposals were evaluated, challenged, and corrected by the team:

### Case 1: Rejection of Pretrained Transformer NER (`en_core_web_trf`)
- **Initial AI Recommendation:** The AI coding assistant initially proposed using spaCy's transformer pipeline (`en_core_web_trf`) for automated Named Entity Recognition.
- **Human Evaluation & Failure Analysis:** The team tested the model on sample text and discovered two critical flaws:
  1. The 1.3GB model consumed prohibitive memory and slowed down CPU ingestion.
  2. Because the model was trained on OntoNotes (news/Wikipedia), it repeatedly misclassified fictional fantasy entities—categorizing feudal factions as corporations (`ORG`), fantasy castles as people (`PERSON`), and completely failing to detect `CREATURE`, `ARTIFACT`, or `DYNASTY`.
- **Human Decision:** The team rejected the transformer approach and designed a **Corpus-Aware Gazette-First NER** strategy: parsing the archive’s own file names (`wiki_person_*.md`, `codex_*.md`) to extract canonical entities deterministically with 100% precision at zero model cost, supplemented by batched Gemini Flash calls.

---

### Case 2: Rejecting Single-Database (PostgreSQL-Only) Multi-Hop Joins
- **Initial AI Recommendation:** The AI initially drafted a single-database design using PostgreSQL with self-joins and recursive CTEs to connect entities across tables.
- **Human Evaluation & Failure Analysis:** The team analyzed sample question 1B_007 (*"Which accord was won by the faction Ederon Fellgard belongs to?"*). In SQL, variable-depth 3-hop joins (`Person` $\rightarrow$ `Faction` $\rightarrow$ `War` $\rightarrow$ `Accord`) required hardcoding the exact sequence of tables in advance. If a question required traversing an unexpected intermediate link, SQL queries broke or produced Cartesian explosions.
- **Human Decision:** The team mandated a **Dual-Storage Topology**, introducing Neo4j Community alongside PostgreSQL. The team directed the implementation of dynamic Cypher path traversals (`MATCH (p:Person {name: $name})-[*1..3]->(target)`), resolving the multi-hop requirement natively.

---

### Case 3: Diagnosing the "Hit@K Ceiling Effect" and Redesigning Benchmark Metrics
- **Initial AI Behavior:** The automated evaluation script produced `Hit@K = 1.0 (100%)` across all Phase 3 and Phase 4 experiments. The AI reported that multi-hop retrieval was "solved."
- **Human Evaluation & Discovery:** The team audited the evaluation code and recognized a severe flaw: `Hit@K` checked if *any single keyword* from the target list was present in top-K chunks. Because the FlashRank cross-encoder placed the primary chunk at Rank 1, the metric returned 1.0 even when the essential second-hop document was completely missing from context!
- **Human Decision:** The team intervened, rejected the AI's conclusion, and designed **Joint Multi-Target Recall@K** (§5.0). This metric requires chunks matching **both Hop 1 AND Hop 2** to be present in top-K simultaneously, exposing genuine retrieval gaps and forcing the development of iterative sub-query expansion.

---

### Case 4: Rejecting Autonomous ReAct Agents in Favor of a Bounded State Machine
- **Initial AI Proposal:** The AI suggested deploying an autonomous ReAct agent loop (similar to LangChain / AutoGPT) to freely decide when to stop searching and which tools to invoke.
- **Human Evaluation & Failure Analysis:** Testing revealed that autonomous agents frequently drifted into rabbit holes on ambiguous fantasy names, took over 180 seconds per query, and occasionally entered infinite retry loops.
- **Human Decision:** The team rejected open-ended agents and designed a **Bounded Deterministic State Machine (`QueryState`)**:
  - Hard cap of `max_hops = 3`.
  - Deterministic tracking of discovered entities vs missing gaps.
  - Strict evidence sufficiency threshold ($\ge 0.85$) for early exit.
  This guaranteed bounded latency, termination, and reproducible audit trails.

---

### Case 5: Eliminating Hallucinated Citations via Deterministic Citation Tokens
- **Initial AI Proposal:** The AI prompted the LLM to format markdown citations directly (e.g., `[Source: Royal Annals, p. 84]`).
- **Human Evaluation & Discovery:** On zero-shot fantasy documents, the LLM consistently fabricated believable but incorrect page numbers, conflated similar-sounding book titles, and generated non-existent chapter names.
- **Human Decision:** The team designed the **Deterministic Citation Token Architecture (`EVIDENCE_xxx`)**:
  - The LLM receives numbered evidence blocks and is strictly prohibited from typing document titles or page numbers.
  - The LLM outputs only `[EVIDENCE_001]`.
  - A deterministic post-processing resolver maps the token back to verified PostgreSQL chunk locators (`[Document Title, p. 84 (Lines 12-28)]`). Citation hallucinations were reduced to exactly **0%**.

---

### Case 6: Overcoming Free-Tier Quota Exhaustion (Batching & Key Rotation)
- **Initial Implementation Flaw:** Early ingestion scripts called Gemini individually for every chunk's NER and contextual prefix, resulting in over 2,100 API calls that immediately exhausted the 5 RPM free-tier limit and crashed.
- **Human Decision & Engineering:** The team restructured the ingestion pipeline:
  1. Implemented `GeminiKeyRotator` to cycle across a pool of 12 API keys with graceful 15-second backoff on HTTP 429.
  2. Implemented batching: 50 chunks per LLM call, reducing 2,143 calls to **43 calls**.
  3. Implemented local SQLite disk caching (`embeddings_cache_voyage.sqlite`), ensuring restarts resume with zero redundant API calls.

---

## 4. Prompt Engineering & Verification Techniques

To ensure the LLM behaved as an evidence-grounded inference engine, the team developed strict system prompt contracts:

1. **Evidence Boundary Prompting:** The LLM is instructed:
   > *"You are an evidence-grounded document assistant for the Ashen Era Archive. You possess ZERO external knowledge of this universe. You must base your answer EXCLUSIVELY on the provided numbered evidence passages. If the evidence does not contain sufficient facts to answer the question, you must state: 'The archive records do not contain sufficient evidence to confirm this.' Do not extrapolate."*

2. **Source Conflict Classification:** When divergent accounts are detected, the prompt instructs:
   > *"If two or more sources present conflicting accounts, DO NOT attempt to reconcile them into a single truth or pick a winner. Explicitly report the contradiction: 'Sources disagree: Document A states X, whereas Document B records Y.'"*

3. **Structured JSON Output Enforcement:** For entity extraction and relationship identification, prompts use Pydantic models with strict JSON schemas, preventing unstructured or hallucinated relationship labels.

---

## 5. Exported Chat Log Catalog

All full, raw chat transcripts exported from development sessions are stored in [`ai_usage/chats/`](chats/) for judge inspection, alongside direct links to web-hosted sessions:

### Local Chat Transcripts (`ai_usage/chats/`)

| File Name | Development Focus | Key Human Steers & Inventions |
|---|---|---|
| [`chats/Project Plan Corpus Analysis.md`](chats/Project%20Plan%20Corpus%20Analysis.md) | Architectural Scoping & Feasibility | Audited 52-section plan against 10-day timeline; pruned React for Streamlit; selected Neo4j graph; authored `docs/architecture-v1.md`. |
| [`chats/ingestion-development.md`](chats/ingestion-development.md) | Ingestion & Contextual Retrieval | Discarded generic spaCy `en_core_web_trf`; designed corpus-aware Gazette + rule NER; built SQLite disk caches for idempotency. |
| [`chats/Entity Extraction Methodology Alternatives.md`](chats/Entity%20Extraction%20Methodology%20Alternatives.md) | Offline NER & Local OCR | Introduced external ML community research (Reddit / spaCy NLP); replaced LLM image calls with CPU RapidOCR for figure plates. |
| [`chats/Architecture And Implementation Audit.md`](chats/Architecture%20And%20Implementation%20Audit.md) | Hybrid Retrieval Audit | Verified Phase 1 & 2 gates; built PostgreSQL BM25 (`ts_rank_cd`), dense embeddings, RRF ($k=60$), and FlashRank reranking; added Recall@K & MRR. |
| [`chats/Architecture Implementation Gap Analysis.md`](chats/Architecture%20Implementation%20Gap%20Analysis.md) | Gap Analysis & Metric Reform | Standardized on `gemini-3.8-flash`; synced 2,011 entities to Neo4j; identified `Hit@K` ceiling effect, triggering Joint Multi-Target Recall. |
| [`chats/Switching Embeddings To Voyage.md`](chats/Switching%20Embeddings%20To%20Voyage.md) | Voyage AI Vector Migration | Upgraded dense embeddings to `voyage-3-large` (1024-dim); caught and removed hardcoded entity relationships in favor of dynamic extraction. |
| [`chats/UI API Integration Plan.md`](chats/UI%20API%20Integration%20Plan.md) | Middleware & Trace UI | Prioritized FastAPI backend before frontend; designed dark archive UI; demanded interactive `show_trace` toggle for Track 1C judging. |
| [`chats/Generate LLM Evaluation Dataset.md`](chats/Generate%20LLM%20Evaluation%20Dataset.md) | Benchmark Evaluation & UX | Executed 20-question benchmark with `gemini-3.8-flash`; overhauled bracket-heavy citation UX with hover previews; resolved AFC warnings. |

### Web-Hosted Sessions (ChatGPT Live Shares)

*(Note: Because ChatGPT shared web sessions render dynamically via client-side JavaScript, raw text exports could not be extracted without markdown formatting loss; live share links are provided below for evaluation auditability)*:

| Session Link | Focus | Key Human Steers & Inventions |
|---|---|---|
| [ChatGPT: Confirm Architecture Role](https://chatgpt.com/share/6aa1805b-9b40-83ee-9c72-42204f49579e) | Architecture Scoping & Role Definition | Synthesizing external technical research (engineering blogs, Reddit discussions, articles) into architectural roles, boundary definitions, and feasibility scoping. |
| [ChatGPT: Implementation Plan Summary](https://chatgpt.com/share/6aa1806d-2090-83ee-970f-e41427278343) | Phased Implementation Roadmap | Formulating the 8-phase implementation roadmap, acceptance criteria, and gatekeeper tests from vetted best practices. |

---

## 6. Summary of Independent Human Contributions

1. **System Architecture:** Conceived the dual-storage model, the 4-stream hybrid retrieval topology, and the bounded state-machine traversal loop.
2. **Corpus Analysis & Insight:** Recognized that figure plates contained exclusive numerical data and designed the local RapidOCR extraction strategy.
3. **Algorithm Design:** Conceived the Gazette-First entity grounding approach, the two-tier contextual prefixing formula, and the deterministic citation resolver.
4. **Evaluation Rigor:** Identified the metric ceiling defect in Hit@K, implemented Joint Recall@K, and conducted honest error analysis across all 20 benchmark questions.
5. **Quality Assurance & Verification:** Verified that every cited document, page, and line number traced back to physical files in `Ashen_Era_Archive/`.
