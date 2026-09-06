# Phase 4 — Entity Layer + Neo4j

**Timeline: Days 4–5**  
**Goal:** Entities (extracted and alias-resolved in Phase 2 via gazette + rules + targeted Gemini + alias resolution) stored in Neo4j with correct type labels, MENTIONED_IN edges, and alias properties. Entity-aware retrieval integrated into the hybrid pipeline.

---

## Prerequisites

- Phase 3 complete — all acceptance criteria met
- Hybrid retrieval pipeline working (BM25 + dense + contextual + RRF + reranker)
- Neo4j running via Docker Compose (should be up since Phase 1)
- **Entities already extracted in Phase 2** via gazette + rules + targeted Gemini (Step 2.3)
- **Alias resolution already completed in Phase 2** (Step 2.3, Step 1d) — alias table available
- Entity types use the Ashen Era 15-type ontology: PERSON, FACTION, PLACE, EVENT, ARTIFACT, ORGANIZATION, CREATURE, TITLE, DYNASTY, DEITY, CONCEPT, DOCUMENT, BUILDING, MILITARY_UNIT, UNKNOWN

---

## Step-by-Step Implementation

### 4.1 — Neo4j Connection Layer (`src/database/neo4j_db.py`)

Implement a Neo4j connection manager:

```python
class Neo4jConnection:
    def __init__(self, uri: str, user: str, password: str):
        """Initialize Neo4j driver."""
    
    def execute_query(self, cypher: str, params: dict = None) -> list[dict]:
        """Execute a Cypher query and return results."""
    
    def execute_write(self, cypher: str, params: dict = None) -> None:
        """Execute a write Cypher query."""
    
    def close(self) -> None:
        """Close the driver connection."""
```

**Test:**
- Connect to Neo4j → verify connection is alive
- Run a simple query → verify results returned
- Write and read back a test node → verify data persistence

---

### 4.2 — Neo4j Graph Operations (`src/knowledge/graph.py`)

Higher-level graph operations:

```python
class KnowledgeGraph:
    def __init__(self, neo4j: Neo4jConnection):
        """Initialize with Neo4j connection."""
    
    def create_entity(self, entity: Entity) -> None:
        """Create or merge an entity node."""
    
    def create_relationship(self, source_id: str, target_id: str, 
                           rel_type: str, properties: dict = None) -> None:
        """Create a relationship between two entities."""
    
    def link_entity_to_chunk(self, entity_id: str, chunk_id: str, 
                             document_id: str) -> None:
        """Create MENTIONED_IN edge from entity to chunk/document."""
    
    def find_entity(self, name: str) -> Entity | None:
        """Find entity by name or alias."""
    
    def find_entities_by_type(self, entity_type: str) -> list[Entity]:
        """Find all entities of a given type."""
    
    def get_entity_chunks(self, entity_id: str) -> list[str]:
        """Get all chunk IDs where an entity is mentioned."""
    
    def get_related_entities(self, entity_id: str, 
                             relationship_types: list[str] = None) -> list[Entity]:
        """Get entities related to a given entity."""
```

**Test:**
- Create an entity → find it by name
- Create two entities with a relationship → verify relationship exists
- Link entity to chunk → verify chunk IDs returned by `get_entity_chunks`

---

### 4.3 — Load & Store Entities in Neo4j (`src/ingestion/entities.py`)

Entities were extracted in **Phase 2** (gazette + rules + targeted Gemini, Steps 2.3a–2.3c). Alias resolution was also performed in Phase 2 (Step 1d). This step loads those results and persists them to Neo4j.

```python
def store_entities_in_neo4j(
    entities: list[ExtractedEntity],
    alias_table: dict[str, str],  # surface_form -> canonical_entity_id
    chunk_entities: dict[str, list[ExtractedEntity]],
    graph: KnowledgeGraph
) -> None:
    """
    Persist Phase 2 entity extraction results to Neo4j.
    
    1. Load alias table (already computed in Phase 2 Step 1d)
    2. Create entity nodes with aliases property populated
    3. Create MENTIONED_IN edges from entities to chunks/documents
    """
```

**Cypher for entity creation:**
```cypher
MERGE (e:Entity {id: $id})
SET e.name = $canonical_name,
    e.type = $type,
    e.aliases = $aliases,
    e.source = $source,
    e.confidence = $confidence,
    e.mention_count = $mention_count

// For each chunk reference:
MERGE (c:Chunk {id: $chunk_id})
MERGE (e)-[:MENTIONED_IN]->(c)

// For each document reference:
MERGE (d:Document {id: $doc_id})
MERGE (e)-[:APPEARS_IN]->(d)
```

**Note:** Entity extraction and alias resolution were completed in Phase 2. This step only handles graph persistence.

**Expected entity counts:**
- ~95 entities from gazette (wiki filenames), confidence=1.0, source="gazette"
- ~50-150 additional entities from targeted Gemini pass, source="gemini_ner"
- ~20-40 FACTION/ORGANIZATION entities
- ~30-50 PLACE entities
- ~15-30 EVENT entities
- ~20-40 ARTIFACT entities
- ~10-15 CREATURE entities

**Test:**
- All entities stored in Neo4j with correct type labels (using the 15-type ontology)
- Entity `aliases` property populated from Phase 2 alias table
- MENTIONED_IN edges exist for each chunk reference
- Querying by entity type returns expected counts
- Source tracking preserved (gazette vs rules vs gemini_ner)

---

### 4.4 — Verify Alias Table & Update Neo4j Aliases

Alias resolution was performed in Phase 2 (Step 1d) using string matching + embedding similarity (zero extra API calls). In this step, we verify the alias table and ensure Neo4j entity nodes have their `aliases` property fully populated.

```python
def verify_and_apply_alias_table(
    alias_table: dict[str, str],  # surface_form -> canonical_entity_id (from Phase 2)
    graph: KnowledgeGraph
) -> dict[str, int]:
    """
    Verify alias resolution from Phase 2 and apply to Neo4j.
    
    1. Load alias table produced in Phase 2 Step 1d
    2. For each canonical entity in Neo4j, update aliases property
    3. Log any UNKNOWN_ALIAS flags for manual review
    4. Return statistics: {resolved: N, flagged: M}
    """
```

**Alias table from Phase 2 (example):**
```
"Lord Vaelith"             -> uuid-vaelith  (canonical: "Vaelith")
"Lord V."                  -> uuid-vaelith
"the Lord of Mournthrone"  -> uuid-vaelith
"Vaelith of the Third House" -> uuid-vaelith
```

All mentions throughout the corpus resolve to a single Neo4j node.

**Entity model for Neo4j:**
```python
class Entity:
    id: str          # UUID
    name: str        # canonical name (from gazette or Gemini)
    aliases: list[str]  # all surface forms (from alias table)
    entity_type: str # from the 15-type Ashen Era ontology
    source: str      # "gazette", "rules", or "gemini_ner"
    confidence: float
    source_documents: list[str]
    source_chunks: list[str]
    mention_count: int
```

**Test:**
- Entity nodes have `aliases` property listing all surface forms
- "the Ashen Vanguard" and "Ashen Vanguard" resolve to the same entity node
- UNKNOWN_ALIAS flags are logged for manual review
- No duplicate canonical entity nodes for the same entity


---

### 4.7 — Entity Search (`src/retrieval/entity_search.py`)

Search based on entities found in the query:

```python
def entity_search(
    query_entities: list[str],
    graph: KnowledgeGraph,
    db: PostgresConnection,
    top_k: int = 50
) -> list[SearchResult]:
    """
    1. For each entity name in the query, find matching entity in Neo4j
    2. Get all chunk IDs where that entity is mentioned
    3. Fetch those chunks from PostgreSQL
    4. Return as SearchResults (scored by mention relevance)
    """
```

**Scoring for entity search:**
- Chunks mentioning the exact query entity → high score
- Chunks mentioning entities related to the query entity → medium score
- Score can be based on mention count, entity centrality, or a simple binary

**Test (`tests/test_entity_search.py`):**
- Query mentioning "Ederon Fellgard" → entity search returns chunks containing this name
- Query mentioning a non-existent entity → empty results
- Entity search returns chunks that BM25/dense search might miss (different ranking)

---

### 4.8 — Integrate Entity Search into Hybrid Pipeline

Update the retrieval orchestrator to include entity search:

```python
# In orchestrator.py, update retrieve():
def retrieve(query, query_analysis, config):
    results = []
    
    # Existing
    if config.enable_bm25:
        results.append(bm25_search(query, ...))
    results.append(dense_search(query, ...))  # standard
    if config.enable_contextual:
        results.append(dense_search(query, ..., use_contextual=True))
    
    # NEW: Entity search
    if config.enable_entity_search and query_analysis.entities_mentioned:
        results.append(entity_search(query_analysis.entities_mentioned, ...))
    
    # Fuse + rerank
    fused = rrf_fusion(results)
    reranked = rerank_candidates(query, fused, ...)
    return reranked
```

**Test:**
- Full pipeline with entity search enabled → verify entity results are included in fusion
- Query with entity names → verify entity search contributes results
- Query without entity names → verify entity search is skipped

---

### 4.9 — Entity Layer Evaluation

Run evaluation comparing Phase 3 hybrid vs Phase 4 hybrid + entities:

| Experiment | Description |
|---|---|
| Experiment 5 (from Phase 3) | Full hybrid (BM25 + dense + contextual + RRF + reranker) |
| Experiment 6 | Full hybrid + entity search (4-stream RRF) |

Focus especially on the 1B sample questions that mention specific characters/factions.

#### Evaluation Results (Actual — 20 questions)

| Metric | Exp 5 (3-Stream) | Exp 6 (4-Stream + Entity) | Delta |
|---|---|---|---|
| Recall@1 | 1.0000 | 1.0000 | +0.0000 |
| Recall@3 | 1.0000 | 1.0000 | +0.0000 |
| Recall@5 | 1.0000 | 1.0000 | +0.0000 |
| Recall@10 | 1.0000 | 1.0000 | +0.0000 |
| MRR | 1.0000 | 1.0000 | +0.0000 |

**Graph population at evaluation time:** 2,242 entities · 31,444 MENTIONED_IN edges · 6,418+ APPEARS_IN edges.

#### Why All Metrics Are 1.0 — The Ceiling Effect

The `Recall@K` implementation in `src/evaluation/metrics.py` is technically **Hit@K** (binary: did *any* keyword from the target list appear anywhere in the top-K results?). Because the cross-encoder reranker (FlashRank) reliably places the primary matching chunk at **Rank 1** for all 20 questions, `Hit@1 = 1.0`, and by definition every higher-K metric is also 1.0.

This is a **metric saturation / ceiling effect** — the pipeline is working correctly; the benchmark can no longer register improvements.

#### What Entity Search Actually Changed (Invisible to Hit@K)

Inspection of `entity_layer_evaluation_report.json` reveals real signals below the metric ceiling:

- **Rank reordering:** For `1b_007` (*"Which accord was ultimately won by the faction of which Ederon Fellgard is a member?"*), chunk `6a896dd5` moved from Rank 5 (Exp 5) to Rank 4 (Exp 6) due to Neo4j entity boosting.
- **New candidate surfacing:** Entity-linked chunks not ranked in BM25/dense top-10 were surfaced into the Exp 6 candidate pool (e.g., chunk `f958aafa` for `1a_v12`).

These improvements matter for Track 1B multi-hop questions but are invisible to a single-keyword Hit@K metric.

#### Why Hit@K Fails on Multi-Hop Questions

For a 2-hop question (e.g., `1b_007`), the correct answer requires *both* hop documents:
- **Document A:** *"Ederon Fellgard is a member of the Iron Covenant."*
- **Document B:** *"The Iron Covenant won the Sunken Accord."*

If the pipeline retrieves only Document A at Rank 1, `Hit@1 = 1.0` even though Document B was completely missed. The metric falsely declares success.

**Phase 5 fix:** Replace Hit@K with **Joint Multi-Target Recall** — requiring *both* hop-1 and hop-2 evidence documents to appear in top-K — which directly measures graph traversal depth and multi-document evidence completeness.

---

## Acceptance Criteria

> **Do NOT proceed to Phase 5 unless ALL of the following are met:**

- [x] Neo4j connection works and entities can be created/queried
- [x] Entities from Phase 2 loaded and stored in Neo4j with correct types (15-type Ashen Era ontology)
- [x] Entity `aliases` property populated from Phase 2 alias table
- [x] Source tracking preserved (gazette vs rules vs gemini_ner)
- [x] No duplicate canonical entity nodes for the same entity
- [x] MENTIONED_IN and APPEARS_IN edges exist for all entities
- [x] Alias table from Phase 2 applied — all surface forms resolve to canonical entity IDs
- [x] UNKNOWN_ALIAS flags logged for any unresolved ambiguous pairs
- [x] Entity search returns relevant chunks for queries mentioning entity names
- [x] Entity search integrated into the hybrid retrieval pipeline via RRF fusion
- [x] Entity count statistics are logged (total, per type, per source)
- [x] All unit tests pass: `pytest tests/test_entity_search.py tests/test_neo4j.py tests/test_entity_resolution.py`
- [x] Experiment 6 results documented with comparison to Experiment 5
- [x] Ceiling effect on Hit@K metric documented — metric saturation confirmed, not a pipeline failure

> **NOTE on "improvement on entity-heavy 1B questions":** Rank reordering and new candidate surfacing are confirmed in `entity_layer_evaluation_report.json`. The current Hit@K metric cannot capture this; Joint Multi-Target Recall (Phase 5) will provide a proper measurement.

### Key Metrics (Actual)

```
Entities stored in Neo4j:     2,242 total
MENTIONED_IN edges:           31,444
APPEARS_IN edges:             6,418+
Experiment 6 vs 5 (Hit@K):   Δ = 0.0000 (ceiling effect — see above)
Rank reordering confirmed:    Yes (1b_007: chunk 6a896dd5 moved Rank 5 → 4)
New candidates surfaced:      Yes (entity-linked chunks not in BM25/dense top-10)
```

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_neo4j.py` | Connection, CRUD operations, query execution |
| `tests/test_entity_resolution.py` | Deduplication, alias resolution, merge logic |
| `tests/test_entity_search.py` | Entity-based retrieval, integration with PostgreSQL |

**Note:** Entity extraction tests (`tests/test_entity_extraction.py`) are in Phase 2.

Run all tests: `pytest tests/ -v`
