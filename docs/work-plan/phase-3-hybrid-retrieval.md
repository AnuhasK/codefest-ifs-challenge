# Phase 3 — Hybrid Retrieval

**Timeline: Days 3–4**  
**Goal:** BM25 lexical search + RRF fusion + cross-encoder reranking. Measurable improvement over Phase 2 baseline.

---

## Prerequisites

- Phase 2 complete — all acceptance criteria met
- Baseline evaluation results documented (standard + contextual dense retrieval)
- All chunks in PostgreSQL with both embedding columns populated

---

## Step-by-Step Implementation

### 3.1 — PostgreSQL Full-Text Search Setup

Add a `tsvector` column for BM25-equivalent search:

```sql
-- Add tsvector column
ALTER TABLE chunks ADD COLUMN search_vector tsvector;

-- Populate tsvector from content
UPDATE chunks SET search_vector = to_tsvector('english', content);

-- Create GIN index for fast text search
CREATE INDEX chunks_search_idx ON chunks USING gin(search_vector);

-- Create trigger to auto-update on content change
CREATE TRIGGER chunks_search_update
    BEFORE INSERT OR UPDATE ON chunks
    FOR EACH ROW
    EXECUTE FUNCTION tsvector_update_trigger(search_vector, 'pg_catalog.english', content);
```

**Test:**
- `tsvector` column is populated for all chunks
- GIN index is created successfully
- A simple text search query returns results

---

### 3.2 — BM25 Search (`src/retrieval/bm25_search.py`)

Implement lexical search using PostgreSQL FTS:

```python
def bm25_search(
    query: str,
    db: PostgresConnection,
    top_k: int = 50
) -> list[SearchResult]:
    """
    Search chunks using PostgreSQL full-text search with ts_rank scoring.
    
    Converts query to tsquery, searches against search_vector column,
    ranks by ts_rank (BM25-equivalent), returns top K results.
    """
```

**SQL query pattern:**
```sql
SELECT id, content, ts_rank(search_vector, plainto_tsquery('english', %s)) AS score
FROM chunks
WHERE search_vector @@ plainto_tsquery('english', %s)
ORDER BY score DESC
LIMIT %s;
```

**Important considerations:**
- Use `plainto_tsquery` for natural language queries (handles stop words, stemming)
- Consider `websearch_to_tsquery` for more advanced query parsing
- Fantasy names like "Gravemaw Wyrm" may not stem well — test and adjust
- Return `SearchResult` objects with consistent interface as dense search

**Test (`tests/test_bm25_search.py`):**
- Search for "Ederon Fellgard" → verify results contain chunks mentioning this name
- Search for "Gravemaw Wyrm" → verify results contain relevant chunks
- Search for a nonsense query → verify empty results
- Verify results are sorted by score descending
- Verify BM25 finds exact name matches that dense search might miss

---

### 3.3 — Query Analyzer (`src/retrieval/query_analyzer.py`)

Analyze incoming queries to extract useful signals:

```python
class QueryAnalysis:
    original_query: str
    query_type: str  # "simple", "multi_entity", "multi_hop", "comparison"
    entities_mentioned: list[str]  # names/places/factions detected in query
    expanded_queries: list[str]  # alternative phrasings for dense search
    bm25_query: str  # optimized query for BM25

def analyze_query(query: str, llm: LLMProvider | None = None) -> QueryAnalysis:
    """
    Analyze query to extract entities, classify type, and generate expanded queries.
    
    Can use simple heuristics or an LLM for richer analysis.
    """
```

**Two approaches (implement both, use LLM if available):**

1. **Heuristic (fast, no API cost):**
   - Detect capitalized multi-word phrases as potential entity names
   - Classify by question patterns ("which war" → likely multi-hop, "what is" → likely simple)
   - Generate BM25 query by extracting key terms

2. **LLM-based (richer, costs API calls):**
   - Ask LLM to extract entities, classify query type, suggest expanded queries
   - More reliable for complex queries

**Test (`tests/test_query_analyzer.py`):**
- "Which war did the faction containing Isolde Mournvale win?" → extracts "Isolde Mournvale", classifies as "multi_hop"
- "What is the central emblem on the banner of House Morvain?" → extracts "House Morvain", classifies as "simple"
- "Whose dominion encompasses the lair of the Gravemaw Wyrm?" → extracts "Gravemaw Wyrm"

---

### 3.4 — RRF Fusion (`src/retrieval/fusion.py`)

Combine ranked lists from multiple retrieval methods:

```python
def rrf_fusion(
    ranked_lists: list[list[SearchResult]],
    k: int = 60,
    top_n: int = 100
) -> list[SearchResult]:
    """
    Reciprocal Rank Fusion.
    
    For each document d appearing in any ranked list:
        RRF_score(d) = sum over all lists of: 1 / (k + rank_i(d))
    
    Args:
        ranked_lists: List of ranked result lists from different retrieval methods
        k: RRF constant (default 60, standard value)
        top_n: Number of results to return
    
    Returns:
        Fused ranked list sorted by RRF score descending
    """
```

**Implementation details:**
- Handle documents that appear in only some lists (they get 0 contribution from lists they're absent in)
- Deduplicate by `chunk_id` (same chunk may appear in BM25 and dense results)
- Preserve metadata from the first occurrence of each chunk

**Test (`tests/test_fusion.py`):**
- Two lists with overlapping results → verify fused ranking promotes documents appearing in both
- Two lists with no overlap → verify both lists' results appear in output
- Single list → verify output is identical to input (minus the RRF score transformation)
- Verify output is sorted by RRF score descending
- Verify deduplication (no duplicate chunk_ids)

---

### 3.5 — Cross-Encoder Reranker (`src/retrieval/reranker.py`, `src/providers/reranker_provider.py`)

Score (query, chunk) pairs using a cross-encoder:

```python
class RerankerProvider(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: list[str], top_k: int = 20) -> list[RerankResult]:
        """Score each (query, document) pair and return top K by relevance."""

class RerankResult:
    index: int  # original index in the input list
    score: float
    content: str

class BGERerankerProvider(RerankerProvider): ...
class CohereRerankerProvider(RerankerProvider): ...
class CrossEncoderRerankerProvider(RerankerProvider): ...

def rerank_candidates(
    query: str,
    candidates: list[SearchResult],
    reranker: RerankerProvider,
    top_k: int = 20
) -> list[SearchResult]:
    """Rerank candidates using cross-encoder and return top K."""
```

**Provider options:**
- `BAAI/bge-reranker-v2-m3` — open-source, runs locally via sentence-transformers
- Cohere Rerank API — excellent quality, API-based
- `cross-encoder/ms-marco-MiniLM-L-6-v2` — lightweight, runs locally

**Test (`tests/test_reranker.py`):**
- Rerank a list of 50 candidates → verify top 20 returned
- Verify top result has highest score
- Verify scores are between 0 and 1 (or model-specific range)
- Verify reranking changes the order compared to input

---

### 3.6 — Retrieval Orchestrator (`src/retrieval/orchestrator.py`)

Coordinate the full hybrid retrieval pipeline:

```python
def retrieve(
    query: str,
    query_analysis: QueryAnalysis,
    config: RetrievalConfig
) -> list[SearchResult]:
    """
    Full hybrid retrieval pipeline:
    
    1. BM25 search → top K results
    2. Dense search (standard embeddings) → top K results
    3. Dense search (contextual embeddings) → top K results
    4. RRF fusion → merge all ranked lists
    5. Cross-encoder reranking → top N final candidates
    
    Returns ranked, reranked evidence candidates.
    """

class RetrievalConfig:
    bm25_top_k: int = 50
    dense_top_k: int = 50
    contextual_top_k: int = 50
    rrf_k: int = 60
    rrf_top_n: int = 100
    reranker_top_k: int = 20
    enable_bm25: bool = True
    enable_contextual: bool = True
    enable_reranker: bool = True
```

**Test (`tests/test_orchestrator.py`):**
- Full pipeline: query → BM25 + dense + contextual → RRF → reranker → results
- Disable BM25 → verify only dense results
- Disable contextual → verify only standard embeddings used
- Disable reranker → verify RRF output is returned directly
- Verify all results have valid metadata (chunk_id, document_id, page, etc.)

---

### 3.7 — Hybrid Evaluation

Run evaluation comparing Phase 2 baseline vs Phase 3 hybrid:

| Experiment | Search method | Description |
|---|---|---|
| Baseline A (from Phase 2) | Standard dense | Baseline dense retrieval |
| Baseline B (from Phase 2) | Contextual dense | Contextual retrieval |
| Experiment 3 | BM25 + Standard dense + RRF | Hybrid without reranking |
| Experiment 4 | BM25 + Standard + Contextual + RRF | Full hybrid without reranking |
| Experiment 5 | Full hybrid + reranker | Full pipeline |

For each experiment, record:
- Retrieval results (top 10 chunks per question)
- Answer quality (manual assessment on 1-5 scale)
- Latency
- Whether the correct information was retrieved (for questions where we know the answer)

**Test:**
- All 19 sample questions produce results without errors
- Experiment results are saved for comparison

---

## Acceptance Criteria

> **Do NOT proceed to Phase 4 unless ALL of the following are met:**

- [ ] BM25 search returns results for entity name queries (exact name matching works)
- [ ] Query analyzer extracts entity names from queries correctly
- [ ] RRF fusion correctly merges results from multiple sources (deduplication works)
- [ ] Cross-encoder reranker produces reranked results with meaningful score differentiation
- [ ] Full hybrid pipeline runs end-to-end: query → BM25 + dense × 2 → RRF → reranker → candidates
- [ ] Hybrid retrieval shows **measurable improvement** over Phase 2 dense-only baseline on at least some questions
- [ ] BM25 finds results that dense search missed (e.g., exact fantasy name matches)
- [ ] Evaluation results for all 5 experiments are documented
- [ ] All unit tests pass: `pytest tests/test_bm25_search.py tests/test_fusion.py tests/test_reranker.py tests/test_orchestrator.py tests/test_query_analyzer.py`
- [ ] Retrieval config is adjustable (top K values, enable/disable components)

### Key Metrics to Record

```
Experiment 3 (BM25 + dense + RRF):          Recall@10 = ?, Δ vs baseline = ?
Experiment 4 (+ contextual):                Recall@10 = ?, Δ vs baseline = ?
Experiment 5 (+ reranker):                  Recall@10 = ?, Δ vs baseline = ?
```

> **Metric ceiling note:** The `Recall@K` in `src/evaluation/metrics.py` is actually **Hit@K** (binary: does any target keyword appear in top-K?). Once the cross-encoder reranker pushes the primary chunk to Rank 1, all K-variants saturate at 1.0. If Experiments 3–5 all score 1.0, this is a metric saturation ceiling effect — not equal performance. Phase 5 will replace Hit@K with **Joint Multi-Target Recall** to properly measure multi-hop retrieval improvements.

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_bm25_search.py` | BM25 search, exact name matching, empty results |
| `tests/test_query_analyzer.py` | Entity extraction, query classification |
| `tests/test_fusion.py` | RRF fusion, deduplication, edge cases |
| `tests/test_reranker.py` | Reranking, score ordering, top-K |
| `tests/test_orchestrator.py` | Full hybrid pipeline, config toggles |

Run all tests: `pytest tests/ -v`
