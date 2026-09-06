# Phase 8 — Polish + Submission

**Timeline: Days 9–10**  
**Goal:** Documentation, reproducibility, demo video, experiment writeup, submission-ready package.

---

## Prerequisites

- Phase 7 complete — all acceptance criteria met
- Working application with UI + API
- Docker Compose runs all services
- All previous experiments documented

---

## Step-by-Step Implementation

### 8.1 — README.md (Complete Setup Guide)

The README is **critical** — judges must be able to run the project from this alone (15% Engineering Best Practices).

**Structure:**

```markdown
# Ashen Era Archive Intelligence System

## About
Brief description — what this system does, which tracks it covers.

## Track Coverage
Primary: 1B (multi-document multi-hop reasoning).
Also covers: 1A (multimodal responses with embedded images) and 1C (iterative agentic search with bounded retry).

## Architecture Overview
High-level diagram (embed from docs/ or as ASCII art).
Link to full architecture doc.

## Quick Start

### Prerequisites
- Docker + Docker Compose
- API keys: [list which ones]

### Setup
1. Clone the repository
2. Copy `.env.example` to `.env` and fill in API keys
3. Run `docker-compose up -d`
4. Run ingestion: `python scripts/ingest.py`
5. Open UI: http://localhost:8501

### Running Tests
pytest tests/ -v

## Sub-track Coverage
Primary: 1B. How our unified architecture also addresses 1A and 1C.

## Technical Decisions
Link to docs/architecturev1.md for full rationale.

## Experiment Results
Summary table with links to detailed results.

## Team
Team member names and contributions.

## AI Usage
Link to ai-usage/ directory.
```

**Test:**
- A fresh clone → follow README → system runs end-to-end

---

### 8.2 — Architecture Documentation

Verify and update [docs/architecturev1.md](file:///c:/Users/Anuhas/Documents/Code/codefest-ifs-challenge/project/docs/architecturev1.md):

- [ ] Diagrams match actual implementation
- [ ] Technology choices reflect what was actually used
- [ ] Any deviations from plan are documented with rationale
- [ ] Add "Lessons Learned" section

Create architecture diagrams (can be Mermaid in markdown or images):

```
docs/
├── architecturev1.md           # Full architecture document
├── diagrams/
│   ├── system_architecture.md  # System-level diagram (Mermaid)
│   ├── ingestion_pipeline.md   # Ingestion flow diagram
│   ├── query_pipeline.md       # Query-time flow diagram
│   └── data_model.md           # Data model diagram
```

---

### 8.3 — Experiment Results Writeup

Create `docs/experiment_results.md`:

```markdown
# Experiment Results

## Experimental Methodology
How we measured improvement at each phase.

## Results Summary

| Experiment | Description | Recall@10 | Answer Quality | Δ vs Previous |
|---|---|---|---|---|
| 1 | Dense (standard) | X.XX | X/5 | baseline |
| 2 | Dense (contextual) | X.XX | X/5 | +X.XX |
| 3 | + BM25 + RRF | X.XX | X/5 | +X.XX |
| 4 | + Contextual + RRF | X.XX | X/5 | +X.XX |
| 5 | + Reranker | X.XX | X/5 | +X.XX |
| 6 | + Entity search | X.XX | X/5 | +X.XX |
| 7 | + Multi-hop | X.XX | X/5 | +X.XX |
| 8 | + Claims/Conflicts | X.XX | X/5 | +X.XX |

## Track 1B Question Results
Detailed results for each 1B sample question:
- Was the correct answer found?
- How many hops were needed?
- Which documents contributed evidence?
- Were conflicts detected?

## Key Findings
- What improved results the most?
- What didn't work as expected?
- What would we do differently?

## Limitations
Honest assessment of system limitations.
```

**This is crucial for:**
- "Technical judgment & decisions" (10%) — shows you tested alternatives
- "Problem understanding & insight" (15%) — shows you understood what matters
- "Human-AI collaboration quality" (15%) — shows iterative refinement

---

### 8.4 — AI Usage Disclosure

Complete the `ai-usage/` directory:

```
ai-usage/
├── ai_usage_disclosure.md
└── chat_logs/
    └── (exported chat logs)
```

**Content of `ai_usage_disclosure.md`:**
```markdown
# AI Usage Disclosure

## Tools Used
- [Tool name]: Used for [specific purpose]
- ...

## How AI Was Used
- Code generation: describe what was generated vs hand-written
- Architecture design: describe AI's role in design decisions
- Debugging: describe AI-assisted debugging
- Documentation: describe AI-assisted documentation

## Human Decision Points
- Architecture choices were made by the team, with AI providing analysis
- All entity extraction prompts were designed and refined by the team
- Experiment methodology was team-designed
- All trade-off decisions (e.g., Neo4j vs PostgreSQL-only) were team decisions

## What We Wrote Ourselves
- Core retrieval logic
- Evaluation framework design
- Experiment interpretation
- ...
```

---

### 8.5 — Reproducibility Verification

**Critical test: Fresh machine simulation**

```bash
# 1. Clone to a new directory
git clone <repo> /tmp/test-clone

# 2. Copy .env
cp .env.example .env
# Fill in API keys

# 3. Start services
docker-compose up -d

# 4. Wait for services to be healthy
# Check postgres and neo4j are up

# 5. Run ingestion
python scripts/ingest.py

# 6. Run evaluation
python scripts/evaluate.py

# 7. Open UI
# Navigate to http://localhost:8501

# 8. Ask a question
# Verify answer appears with citations
```

**Checklist:**
- [ ] `docker-compose up` succeeds without manual intervention
- [ ] Ingestion script runs without errors
- [ ] All API keys are documented in `.env.example`
- [ ] No hardcoded paths (everything uses config/env vars)
- [ ] No missing dependencies (everything in `pyproject.toml`)
- [ ] README instructions are complete and correct

---

### 8.6 — Demo Video

Record a demo video (2-5 minutes) showing:

1. **System startup** — `docker-compose up`, services starting
2. **Simple question (1A)** — ask a question about a figure plate (e.g. "What is the threat rating of the Weeping Lurker?"), show the answer with the **actual image embedded** alongside the text
3. **1B multi-hop question** — ask one of the Track 1B questions, show:
   - The answer
   - Multi-document evidence
   - Graph traversal (how entities were connected)
   - Citation links to original documents
4. **1C iterative retrieval** — ask a complex question, show the query trace revealing the Retrieve → Assess → Reformulate → Retry loop (evidence sufficiency states, iteration count)
5. **Conflict handling** — ask a question where sources disagree, show conflict detection
6. **Evidence inspector** — show the evidence panel, source viewer, query trace
7. **Architecture overview** — brief walkthrough of the architecture diagram

**Tips:**
- Script the demo questions in advance
- Use questions from `sample_questions.json` — judges know these
- Show the query trace to demonstrate transparency
- Keep it concise — judges watch many videos

---

### 8.7 — Git History Cleanup

The rubric explicitly scores: "meaningful atomic commits, clear messages, sensible history" (part of 15%).

**Verify:**
- [ ] Commit messages follow a consistent format (e.g., `feat:`, `fix:`, `docs:`, `test:`)
- [ ] Each commit is atomic (one logical change per commit)
- [ ] No "WIP" or "asdf" commits in the history
- [ ] Branch strategy is visible (feature branches merged to main)
- [ ] `.gitignore` excludes: `__pycache__`, `.env`, `*.pyc`, `node_modules`, data directories

**If cleanup is needed:**
- Use interactive rebase to squash/rename messy commits: `git rebase -i`
- But **do NOT** rewrite history to hide the AI collaboration — judges want to see it

---

### 8.8 — Final Evaluation Run

Run the complete evaluation suite on all 19 sample questions:

```bash
python scripts/evaluate.py --output docs/final_evaluation.json
```

For each question, record:
- Question text
- Track (1A/1B/1C)
- Generated answer
- Evidence used (with citations)
- Evidence status
- Conflicts detected
- Latency
- Pass/fail assessment

**Generate a summary table:**
```
Track 1A: X/11 questions — image/visual evidence embedded where applicable
Track 1B: Y/7 questions — multi-hop graph traversal, multi-document citations
Track 1C: (demonstrated via query trace — iterative retrieval loop visible)
Overall:  W/19
```

---

### 8.9 — Final Checklist

Before submission, verify every item:

**Code Quality:**
- [ ] All Python files have docstrings
- [ ] Code is modular with clear separation of concerns
- [ ] No dead code or commented-out blocks
- [ ] Consistent code style (run `black` or `ruff format`)
- [ ] Type hints on public functions

**Testing:**
- [ ] `pytest tests/ -v` — all tests pass
- [ ] At least one test per module
- [ ] Integration test: full pipeline end-to-end

**Documentation:**
- [ ] README.md — complete setup guide
- [ ] docs/architecturev1.md — full architecture with rationale
- [ ] docs/experiment_results.md — experiment progression with metrics
- [ ] AI Usage Disclosure — honest and detailed

**Reproducibility:**
- [ ] Docker Compose starts all services
- [ ] `.env.example` documents all required environment variables
- [ ] `pyproject.toml` lists all dependencies
- [ ] Fresh clone → setup → run works

**Submission:**
- [ ] Demo video recorded
- [ ] All code committed and pushed
- [ ] No sensitive data (API keys, passwords) in the repository
- [ ] Repository is clean (no large binary files in git history)

---

## Acceptance Criteria

> **This is the final phase — submission-ready.**

- [ ] README allows a judge to set up and run the project without asking questions
- [ ] `docker-compose up` starts all services (PostgreSQL, Neo4j, API, UI)
- [ ] Ingestion runs without errors
- [ ] UI is accessible and functional
- [ ] All 19 sample questions produce answers (even if not all are correct)
- [ ] **Track 1A:** Questions about images/figure plates return the actual image embedded in the response
- [ ] **Track 1B:** Multi-hop questions show multi-document evidence with citations and graph traversal
- [ ] **Track 1C:** Query trace for complex questions shows the iterative retrieval loop (Retrieve → Assess → Retry)
- [ ] Experiment results are documented with metrics and analysis
- [ ] Architecture document explains all decisions with rationale
- [ ] AI Usage Disclosure is complete
- [ ] Demo video is recorded and clear
- [ ] Git history has meaningful commits
- [ ] All tests pass
- [ ] **Manual verification:** ask 5 questions live, verify answers are reasonable

---

## Submission Deliverables

| Deliverable | Location | Status |
|---|---|---|
| Source code | `src/`, `app/`, `scripts/` | |
| Tests | `tests/` | |
| Docker Compose | `docker-compose.yml` | |
| Architecture doc | `docs/architecturev1.md` | |
| Experiment results | `docs/experiment_results.md` | |
| README | `README.md` | |
| AI Usage Disclosure | `ai-usage/` | |
| Demo video | (separate submission) | |
| Environment template | `.env.example` | |
