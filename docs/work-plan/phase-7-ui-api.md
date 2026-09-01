# Phase 7 — UI + API

**Timeline: Days 7–8**  
**Goal:** Working demo application with Streamlit UI and FastAPI backend. Users can ask questions, see evidence, and browse sources.

---

## Prerequisites

- Phase 6 complete — all acceptance criteria met
- End-to-end answer pipeline functional (query → answer with citations)
- All data ingested and indexed (chunks, embeddings, entities, relationships)
- Docker Compose running (PostgreSQL + Neo4j)

---

## Step-by-Step Implementation

### 7.1 — FastAPI Application (`src/api/main.py`)

Set up the FastAPI application:

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Ashen Era Archive Intelligence",
    description="Evidence-grounded document intelligence for the Ashen Era Archive",
    version="1.0.0"
)

# CORS for Streamlit if running on different port
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])

# Health check
@app.get("/health")
def health():
    return {"status": "healthy", "databases": check_database_connections()}
```

**Test:**
- `GET /health` returns 200 with database status
- API documentation accessible at `/docs`

---

### 7.2 — Query Endpoint (`src/api/routes/query.py`)

Main question-answering endpoint:

```python
class QueryRequest(BaseModel):
    question: str
    max_hops: int = 3
    top_k: int = 20
    include_trace: bool = False

class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    evidence: list[EvidenceSummary]
    conflicts: list[ConflictSummary]
    evidence_status: str  # HIGH, MEDIUM, LOW, INSUFFICIENT
    trace: dict | None = None  # query trace if requested

@router.post("/query")
async def query(request: QueryRequest) -> QueryResponse:
    """
    Answer a question about the Ashen Era Archive.
    Returns a grounded answer with citations, evidence, and conflict information.
    """
```

**Test:**
- POST a simple question → receive answer with citations
- POST a multi-hop question → receive answer with multi-document evidence
- POST with `include_trace=True` → trace included in response
- POST with nonsense question → receive INSUFFICIENT status

---

### 7.3 — Search Endpoint (`src/api/routes/search.py`)

Direct search without answer generation (useful for exploration):

```python
class SearchRequest(BaseModel):
    query: str
    search_type: str = "hybrid"  # hybrid, bm25, dense, entity
    top_k: int = 20

class SearchResponse(BaseModel):
    results: list[SearchResultResponse]
    total: int
    search_type: str

@router.post("/search")
async def search(request: SearchRequest) -> SearchResponse:
    """Search the archive without generating an answer."""
```

**Test:**
- Search with different types → correct search method used
- Results include chunk content, document info, score

---

### 7.4 — Document Browse Endpoint (`src/api/routes/documents.py`)

Browse documents and their chunks:

```python
@router.get("/documents")
async def list_documents(category: str = None) -> list[DocumentSummary]:
    """List all documents, optionally filtered by category."""

@router.get("/documents/{document_id}")
async def get_document(document_id: str) -> DocumentDetail:
    """Get full document details including sections and chunks."""

@router.get("/documents/{document_id}/chunks")
async def get_document_chunks(document_id: str) -> list[ChunkDetail]:
    """Get all chunks for a document."""

@router.get("/entities")
async def list_entities(entity_type: str = None) -> list[EntitySummary]:
    """List all entities, optionally filtered by type."""

@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str) -> EntityDetail:
    """Get entity details including relationships and mentions."""
```

**Test:**
- List documents → returns all documents with correct counts
- Filter by category → only matching documents returned
- Get document chunks → returns chunks with content and metadata
- List entities → returns entities with types
- Get entity → returns relationships and mention locations

---

### 7.5 — Streamlit Chat Interface (`app/streamlit_app.py`)

Build the main Streamlit application:

```python
import streamlit as st
import requests

st.set_page_config(
    page_title="Ashen Era Archive Intelligence",
    page_icon="📚",
    layout="wide"
)

st.title("📚 Ashen Era Archive Intelligence")
st.subtitle("Evidence-grounded answers from the Ashen Era Archive")

# Sidebar: settings
with st.sidebar:
    st.header("Settings")
    max_hops = st.slider("Max retrieval hops", 1, 5, 3)
    top_k = st.slider("Evidence candidates", 5, 50, 20)
    show_trace = st.checkbox("Show query trace", False)

# Chat interface
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# User input
if prompt := st.chat_input("Ask about the Ashen Era Archive..."):
    # Add user message
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # Query the API
    response = requests.post("http://localhost:8000/query", json={
        "question": prompt,
        "max_hops": max_hops,
        "top_k": top_k,
        "include_trace": show_trace
    })
    
    result = response.json()
    
    # Display answer with evidence
    # ... (see components below)
```

**Features:**
- Chat history with message persistence
- Settings sidebar (max hops, top K, trace toggle)
- Responsive layout

---

### 7.6 — Evidence Panel (`app/components/evidence_panel.py`)

Display evidence alongside the answer:

```python
def render_evidence_panel(evidence: list[dict], conflicts: list[dict], 
                          evidence_status: str):
    """
    Render the evidence panel with:
    1. Evidence status badge (HIGH/MEDIUM/LOW/INSUFFICIENT)
    2. List of evidence records with source info
    3. Conflict alerts
    4. Expandable evidence details
    """
    
    # Evidence status badge
    status_colors = {"HIGH": "green", "MEDIUM": "orange", "LOW": "red", "INSUFFICIENT": "red"}
    st.markdown(f"**Evidence Status:** :{status_colors[evidence_status]}[{evidence_status}]")
    
    # Evidence count
    st.metric("Sources Used", len(evidence))
    
    # Evidence list
    for ev in evidence:
        with st.expander(f"📄 {ev['document_title']} — p.{ev['page']}"):
            st.markdown(f"**Source type:** {ev['source_category']} ({ev['source_subtype']})")
            st.text(ev['content'])
    
    # Conflicts
    if conflicts:
        st.warning(f"⚠️ {len(conflicts)} conflict(s) detected between sources")
        for conflict in conflicts:
            with st.expander(f"Conflict: {conflict['claim_summary']}"):
                st.markdown("**Supporting:**")
                for eid in conflict['supporting']:
                    st.markdown(f"- {eid}")
                st.markdown("**Opposing:**")
                for eid in conflict['opposing']:
                    st.markdown(f"- {eid}")
```

---

### 7.7 — Source Viewer (`app/components/source_viewer.py`)

Allow users to view original source documents:

```python
def render_source_viewer(document_id: str):
    """
    Display the original document content for a selected evidence source.
    Shows the full context around the cited chunk.
    """
    
    # Fetch document details
    doc = requests.get(f"http://localhost:8000/documents/{document_id}").json()
    
    st.subheader(f"📖 {doc['title']}")
    st.markdown(f"**Category:** {doc['source_category']}")
    st.markdown(f"**File:** {doc['source_path']}")
    
    # Display chunks with highlighting
    for chunk in doc['chunks']:
        if chunk['id'] in highlighted_chunk_ids:
            st.markdown(f"> {chunk['content']}")
        else:
            st.text(chunk['content'])
```

---

### 7.8 — Query Trace Viewer

Show the retrieval pipeline trace for transparency:

```python
def render_query_trace(trace: dict):
    """
    Display the query trace showing how the system arrived at its answer.
    
    Shows:
    - Query analysis (entities extracted, query type)
    - Retrieval steps (BM25, dense, entity results)
    - RRF fusion ranking
    - Reranker scores
    - Graph traversals (entities → relationships → hops)
    - Evidence sufficiency assessment
    - Verification result
    """
    
    with st.expander("🔍 Query Trace"):
        st.json(trace)
```

---

### 7.9 — Streamlit Layout

Combine components into a clean layout:

```
┌─────────────────────────────────────────────────┐
│ 📚 Ashen Era Archive Intelligence               │
├──────────┬──────────────────────────────────────┤
│ Sidebar  │  Main Content Area                    │
│          │                                       │
│ Settings │  ┌─────────────┬─────────────────────┤
│ - Hops   │  │ Chat Panel  │ Evidence Panel       │
│ - Top K  │  │             │                      │
│ - Trace  │  │ User: ...   │ Evidence Status: ... │
│          │  │ AI: ...     │ Sources: ...         │
│ Corpus   │  │             │ Conflicts: ...       │
│ Stats    │  │             │                      │
│          │  │             │ Source Viewer         │
│          │  │             │ (expandable)          │
└──────────┴──┴─────────────┴─────────────────────┘
```

**Use `st.columns` for the main layout split:**
```python
col1, col2 = st.columns([2, 1])
with col1:
    # Chat interface
with col2:
    # Evidence panel + source viewer
```

---

### 7.10 — Docker Integration

Update `docker-compose.yml` to include the application:

```yaml
services:
  postgres:
    # ... (existing)
  
  neo4j:
    # ... (existing)
  
  api:
    build: .
    ports: ["8000:8000"]
    depends_on: [postgres, neo4j]
    environment:
      POSTGRES_URL: postgresql://ashen:${POSTGRES_PASSWORD}@postgres:5432/ashen_era
      NEO4J_URI: bolt://neo4j:7687
    command: uvicorn src.api.main:app --host 0.0.0.0 --port 8000
  
  ui:
    build: .
    ports: ["8501:8501"]
    depends_on: [api]
    command: streamlit run app/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
```

Add `Dockerfile`:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install -e .
COPY . .
```

**Test:**
- `docker-compose up` starts all 4 services
- API accessible at `http://localhost:8000`
- UI accessible at `http://localhost:8501`
- UI can communicate with API
- API can communicate with PostgreSQL and Neo4j

---

## Acceptance Criteria

> **Do NOT proceed to Phase 8 unless ALL of the following are met:**

- [ ] FastAPI application starts and `/health` returns healthy status with database connections
- [ ] `/query` endpoint accepts a question and returns a FinalAnswer with citations
- [ ] `/search` endpoint returns search results for different search types
- [ ] `/documents` endpoint lists and retrieves documents
- [ ] `/entities` endpoint lists and retrieves entities with relationships
- [ ] Streamlit chat interface works — user can type a question and see an answer
- [ ] Evidence panel shows evidence records with source metadata
- [ ] Conflict alerts are displayed when conflicts exist
- [ ] Query trace is viewable when enabled
- [ ] Evidence status (HIGH/MEDIUM/LOW/INSUFFICIENT) is displayed
- [ ] Citations in the answer link back to source documents
- [ ] Docker Compose starts all services (`postgres`, `neo4j`, `api`, `ui`)
- [ ] A judge could run `docker-compose up` and use the system without any code changes
- [ ] **Live demo:** Ask 3 different questions and receive grounded answers with citations

---

## Testing Summary

| Test file | What it tests |
|---|---|
| `tests/test_api_query.py` | Query endpoint, request/response format |
| `tests/test_api_search.py` | Search endpoint, search types |
| `tests/test_api_documents.py` | Document listing, retrieval |
| `tests/test_api_health.py` | Health check, database connections |

Run API tests: `pytest tests/test_api_*.py -v`

Manual testing:
- Open Streamlit UI → ask questions → verify answers display correctly
- Check evidence panel → verify sources are shown
- Click through citations → verify they reference real documents
