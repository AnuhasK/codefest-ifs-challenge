# Phase 4 — Entity Layer + Neo4j

**Timeline: Days 4–5**  
**Goal:** Entities (already extracted in Phase 2 via gazette + spaCy) stored in Neo4j, deduplicated, resolved, and entity-aware retrieval integrated into the hybrid pipeline.

---

## Prerequisites

- Phase 3 complete — all acceptance criteria met
- Hybrid retrieval pipeline working (BM25 + dense + contextual + RRF + reranker)
- Neo4j running via Docker Compose (should be up since Phase 1)
- **Entities already extracted in Phase 2** via gazette + spaCy (stored as entity list + chunk_id → entities mapping)
- spaCy pipeline already built and tested (from Phase 2)

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

Entities were already extracted in **Phase 2** (gazette + spaCy, zero LLM calls). This step loads those results and persists them to Neo4j.

```python
def store_entities_in_neo4j(
    entities: list[ExtractedEntity],
    chunk_entities: dict[str, list[ExtractedEntity]],
    graph: KnowledgeGraph
) -> None:
    """
    Load entities extracted in Phase 2 and persist to Neo4j.
    
    1. Deduplicate entities (see 4.4)
    2. Create entity nodes in Neo4j
    3. Create MENTIONED_IN edges from entities to chunks/documents
    """
```

**Cypher for entity creation:**
```cypher
MERGE (e:Entity {id: $id})
SET e.name = $name,
    e.type = $type,
    e.aliases = $aliases,
    e.source = $source,
    e.mention_count = $mention_count

// For each chunk reference:
MERGE (c:Chunk {id: $chunk_id})
MERGE (e)-[:MENTIONED_IN]->(c)

// For each document reference:
MERGE (d:Document {id: $doc_id})
MERGE (e)-[:APPEARS_IN]->(d)
```

**Note:** Entity extraction itself (gazette building, spaCy NER, chunk annotation) was completed in Phase 2 step 2.3. This step only handles graph persistence.

**Expected entity counts (from Phase 2):**
- ~95 entities from gazette (wiki filenames)
- ~100-200 unique Person entities total (gazette + spaCy NER)
- ~20-40 Faction/Organization entities
- ~30-50 Place entities
- ~15-30 Event entities
- ~20-40 Artifact entities
- ~10-15 Creature entities

**Test:**
- All entities stored in Neo4j with correct properties (name, type, source)
- MENTIONED_IN edges exist for each chunk reference
- Querying by entity type returns expected counts
- Entity source tracking preserved (gazette vs spacy_ner)

---

### 4.4 — Entity Deduplication & Merging

After extracting from all chunks, merge duplicate entities:

```python
def deduplicate_entities(entities: list[ExtractedEntity]) -> list[Entity]:
    """
    Merge entities that refer to the same thing.
    
    Strategy:
    1. Exact name match → merge
    2. Case-insensitive match → merge
    3. Substring match (e.g., "Ser Vael" and "Vael") → candidate for merge (manual review or LLM verification)
    """
```

**Entity model for Neo4j:**
```python
class Entity:
    id: str  # UUID
    name: str  # canonical name
    aliases: list[str]  # all known names/spellings
    entity_type: str
    source_documents: list[str]  # document_ids where this entity appears
    source_chunks: list[str]  # chunk_ids where this entity appears
    mention_count: int
```

**Test:**
- "Isolde Mournvale" appearing in 5 chunks → merged into 1 entity with 5 chunk references
- "the Ashen Vanguard" and "Ashen Vanguard" → merged (case-insensitive / article stripping)

---

### 4.5 — Store Entities in Neo4j

Persist all deduplicated entities:

```python
def store_entities_in_neo4j(entities: list[Entity], graph: KnowledgeGraph) -> None:
    """
    For each entity:
    1. Create entity node in Neo4j
    2. Create MENTIONED_IN edges to chunk/document nodes
    """
```

**Cypher for entity creation:**
```cypher
MERGE (e:Entity {id: $id})
SET e.name = $name,
    e.type = $type,
    e.aliases = $aliases,
    e.mention_count = $mention_count

// For each chunk reference:
MERGE (c:Chunk {id: $chunk_id})
MERGE (e)-[:MENTIONED_IN]->(c)

// For each document reference:
MERGE (d:Document {id: $doc_id})
MERGE (e)-[:APPEARS_IN]->(d)
```

**Test:**
- All entities stored in Neo4j
- Entity nodes have correct properties (name, type, aliases)
- MENTIONED_IN edges exist for each chunk reference
- Querying by entity type returns expected counts

---

### 4.6 — Basic Entity Resolution (`src/knowledge/entity_resolution.py`)

Handle alias resolution — different names for the same entity:

```python
def resolve_entity_aliases(
    entities: list[Entity],
    graph: KnowledgeGraph,
    llm: LLMProvider | None = None
) -> dict[str, str]:
    """
    Attempt to resolve aliases. Returns a mapping of alias → canonical entity ID.
    
    Strategy:
    1. Exact match after normalization (lowercase, strip articles/titles)
    2. Wiki article filename match (wiki article title often IS the canonical name)
    3. LLM-assisted resolution for ambiguous cases (optional, stretch)
    """
```

**Practical approach for this corpus:**
- Wiki filenames are the best source of canonical names (e.g., `ederon_fellgard.md` → "Ederon Fellgard" is canonical)
- Titles like "the Oathless" or "the Ashen" are epithets, not separate entities
- Create `SAME_AS` edges in Neo4j between alias entities and their canonical entity

**Test:**
- "The Ashen Vanguard" resolves to same entity as "Ashen Vanguard"
- Wiki article names match extracted entity names
- Resolution mapping is stored and queryable

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
| Experiment 6 | Full hybrid + entity search |

Focus especially on the 1B sample questions that mention specific characters/factions.

---

## Acceptance Criteria

> **Do NOT proceed to Phase 5 unless ALL of the following are met:**

- [ ] Neo4j connection works and entities can be created/queried
- [ ] Entities from Phase 2 loaded and stored in Neo4j with correct types and chunk/document references
- [ ] Entity source tracking preserved (gazette vs spaCy NER from Phase 2)
- [ ] Entity deduplication merges obvious duplicates (exact + case-insensitive matches)
- [ ] Basic entity resolution resolves common aliases
- [ ] Entity search returns relevant chunks for queries mentioning entity names
- [ ] Entity search is integrated into the hybrid retrieval pipeline via RRF fusion
- [ ] Hybrid + entity retrieval shows improvement on entity-heavy 1B questions
- [ ] Entity count statistics are logged (total entities, per type, per source, per document)
- [ ] All unit tests pass: `pytest tests/test_entity_search.py tests/test_neo4j.py tests/test_entity_resolution.py`
- [ ] Experiment 6 results documented with comparison to Experiment 5

### Key Metrics to Record

```
Entities extracted:           ~X total, Y Person, Z Faction, ...
Entity resolution merges:     N entities merged
Experiment 6 vs 5:            Recall@10 = ?, Δ = ?
1B question improvement:      X/7 questions improved
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
