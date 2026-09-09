# Exported AI Chat Logs Directory

This directory holds the raw exported chat transcripts from AI tools used during the development of the **Ashen Era Archive Intelligence System** (SLIIT Codefest 2026 AI Competition).

These chat logs serve as empirical evidence for **Evaluation Criterion 3: Human-AI Collaboration Quality (15%)**:
> *"How well the team directed and collaborated with AI tools - not one-shot generation. We look for the team's own ideas and thought process steering the AI: iterative refinement, questioning and correcting AI output, and mixing the team's thinking and innovation with the capabilities of AI coding assistants."*

---

## Catalog of Sessions

Place your exported chat files in this directory matching the following recommended filenames (or in formats such as `.md`, `.json`, `.pdf`, `.html`, or `.txt`):

1. **`01_project_scaffold_and_storage_design.md`**
   - Architectural scoping, dual storage design (PostgreSQL + Neo4j), schema design, Docker Compose port mapping isolation.
2. **`02_ingestion_ocr_and_rapidocr.md`**
   - Ingestion pipeline, scanned PDF OCR fallbacks, figure plate analysis, integration of local RapidOCR ONNX model.
3. **`03_ner_gazette_and_spacy_rejection.md`**
   - Testing and rejection of spaCy `en_core_web_trf`, creation of corpus-aware Gazette, and batched Gemini Flash NER design.
4. **`04_hybrid_retrieval_and_rrf_tuning.md`**
   - 4-stream hybrid retrieval (dense standard, dense contextual, BM25 FTS, entity search), Reciprocal Rank Fusion ($k=60$), local FlashRank cross-encoder.
5. **`05_multihop_graph_and_state_machine.md`**
   - Neo4j Cypher traversals, multi-hop reasoning, rejection of unbounded ReAct agents in favor of bounded `QueryState` state machine.
6. **`06_evaluation_metrics_and_benchmark_runs.md`**
   - Diagnosis of the `Hit@K` ceiling effect, implementation of Joint Multi-Target Recall@K, and execution of the 20-question benchmark suite.

---

## How to Export from Common AI Tools

- **Antigravity / Cursor / Claude Code / VSCode:**
  - Export chat transcript as Markdown (`.md`) or copy session text into `.md` files in this folder.
- **ChatGPT:**
  - Settings $\rightarrow$ Data Controls $\rightarrow$ Export Data, or use browser extension / copy chat to Markdown.
- **Claude Web:**
  - Copy transcript to Markdown or print to PDF.
- **Google AI Studio:**
  - Get Code / Export Prompt and responses.
