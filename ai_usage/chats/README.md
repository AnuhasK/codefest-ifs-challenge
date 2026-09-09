# Exported AI Chat Logs Directory

This directory holds the raw exported chat transcripts from AI tools used during the development of the **Ashen Era Archive Intelligence System** (SLIIT Codefest 2026 AI Competition).

These chat logs serve as empirical evidence for **Evaluation Criterion 3: Human-AI Collaboration Quality (15%)**:
> *"How well the team directed and collaborated with AI tools - not one-shot generation. We look for the team's own ideas and thought process steering the AI: iterative refinement, questioning and correcting AI output, and mixing the team's thinking and innovation with the capabilities of AI coding assistants."*

---

## Catalog of Sessions

The following exported chat sessions document key architectural milestones, technical decisions, and human-in-the-loop steering throughout the development lifecycle:

1. **[`Project Plan Corpus Analysis.md`](./Project%20Plan%20Corpus%20Analysis.md)**
   - **Focus**: Initial architectural scoping, feasibility assessment, and timeline calibration for the SLIIT Codefest 2026 AI Competition (Track 1B).
   - **Key Decisions & Steering**: Audited the initial 52-section specification against the 10-day timeline; pruned high-overhead React frontend in favor of Streamlit; chose Neo4j for the knowledge graph; prioritized Contextual Retrieval as a core architectural tier; authored `docs/architecture-v1.md` and 8 phased work-plans with strict acceptance criteria and gates.

2. **[`ingestion-development.md`](./ingestion-development.md)**
   - **Focus**: Implementation of Phase 1 (Infrastructure & Extraction) and Phase 2 (Baseline RAG & Contextual Retrieval).
   - **Key Decisions & Steering**: Evaluated and discarded generic pretrained spaCy `en_core_web_trf` due to real-world domain bias on a fictional corpus; designed a corpus-aware Gazette + rule-based NER + targeted Gemini extraction with alias resolution; implemented persistent SQLite disk caches for LLM calls and embeddings to ensure idempotency and quota resilience; established the hybrid visual extraction pipeline.

3. **[`Entity Extraction Methodology Alternatives.md`](./Entity%20Extraction%20Methodology%20Alternatives.md)**
   - **Focus**: Researching offline entity extraction methods and managing Gemini API rate limits.
   - **Key Decisions & Steering**: User steered the agent using external ML community research (Reddit / spaCy NLP discussions) to evaluate hybrid GraphRAG pipelines; questioned LLM necessity for tabular figure plates, driving the adoption of `rapidocr-onnxruntime` for zero-API local OCR; verified database idempotency (`ON CONFLICT DO UPDATE`) to prevent duplicate records during pipeline restarts.

4. **[`Architecture And Implementation Audit.md`](./Architecture%20And%20Implementation%20Audit.md)**
   - **Focus**: Technical audit of Phases 1 & 2 and complete implementation of Phase 3 (Hybrid Retrieval).
   - **Key Decisions & Steering**: Verified acceptance gates for baseline retrieval; implemented PostgreSQL BM25 lexical search (`ts_rank_cd`), dense semantic search, Reciprocal Rank Fusion ($k=60$), and FlashRank cross-encoder reranking; user required retrieval-quality metrics (Recall@1/3/5/10, MRR), evidence/citation groundedness verification, and document diversity filtering (`max_chunks_per_doc=3`).

5. **[`Architecture Implementation Gap Analysis.md`](./Architecture%20Implementation%20Gap%20Analysis.md)**
   - **Focus**: Gap analysis across Phases 1–3 prior to Phase 4 (Knowledge Graph) and model migration.
   - **Key Decisions & Steering**: Transitioned to `gemini-3.8-flash` following `gemini-2.5` deprecation; populated Neo4j with 2,011 unique entities and 22,000+ graph edges; user directed orchestrator integration (`enable_entity_search=True`); critically questioned 100% retrieval metrics, uncovering the `Hit@K` ceiling effect and prompting a shift to joint multi-target retrieval metrics.

6. **[`Switching Embeddings To Voyage.md`](./Switching%20Embeddings%20To%20Voyage.md)**
   - **Focus**: Vector embedding layer migration to Voyage AI (`voyage-3-large`) and knowledge extraction refactoring.
   - **Key Decisions & Steering**: Re-indexed PostgreSQL pgvector with 1024-dim HNSW vector indexes while maintaining separate disk caches for Gemini and Voyage; user intervened upon detecting hardcoded relations in `src/ingestion/entities.py`, forcing dynamic, schema-driven relationship extraction; synced 7,712 graph edges to Neo4j.

7. **[`UI API Integration Plan.md`](./UI%20API%20Integration%20Plan.md)**
   - **Focus**: Phase 7 FastAPI backend services and interactive Streamlit UI application.
   - **Key Decisions & Steering**: User enforced decoupling of concerns—prioritizing FastAPI middleware and endpoints (`/query`, `/search`, `/documents`, `/entities`, `/assets`) before frontend development; designed the dark archive UI with evidence cards, RapidOCR table viewers, and citation navigation; explicitly demanded an interactive query trace toggle (`show_trace`) for judges to inspect Track 1C multi-hop reasoning.

8. **[`Generate LLM Evaluation Dataset.md`](./Generate%20LLM%20Evaluation%20Dataset.md)**
   - **Focus**: End-to-end evaluation runs, benchmark comparisons, and citation user experience.
   - **Key Decisions & Steering**: Executed the 20-question benchmark suite using `gemini-3.8-flash` and compared evaluation runs (`sample_questions_evaluation.json` vs. `track_1b_results.json`); resolved document viewing endpoints for PDF, DOCX, and Markdown; resolved readability issues caused by bracket citation clutter in UI outputs with hover previews and modal views; resolved Google GenAI SDK Automatic Function Calling (AFC) warnings.

---

## How to Export from Common AI Tools

- **Antigravity / Cursor / Claude Code / VSCode:**
  - Export chat transcript as Markdown (`.md`) or copy session text into `.md` files in this folder.
- **ChatGPT:**
  - Settings $\rightarrow$ Data Controls $\rightarrow$ Export Data, or use browser extension / copy chat to Markdown.
  - **Shared Live Web Sessions (Direct Links):**
    - [ChatGPT Session 1: Confirm Architecture Role](https://chatgpt.com/share/6aa1805b-9b40-83ee-9c72-42204f49579e) — Synthesizing external technical research (engineering blogs, Reddit discussions, articles) into architectural roles, boundary definitions, and feasibility scoping.
    - [ChatGPT Session 2: Implementation Plan Summary](https://chatgpt.com/share/6aa1806d-2090-83ee-970f-e41427278343) — Formulating the 8-phase implementation roadmap, acceptance criteria, and gatekeeper tests from vetted best practices.
    *(Note: Because ChatGPT shared web sessions render dynamically via client-side JavaScript, raw text could not be exported without markdown formatting loss; live links are provided above for full evaluation auditability).*
- **Claude Web:**
  - Copy transcript to Markdown or print to PDF.
- **Google AI Studio:**
  - Get Code / Export Prompt and responses.
