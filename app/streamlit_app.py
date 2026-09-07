import os
import time
from typing import Dict, Any, List, Optional
import requests
import streamlit as st

from app.components.evidence_panel import render_evidence_panel
from app.components.image_evidence import render_image_evidence
from app.components.trace_viewer import render_query_trace
from app.components.source_viewer import render_source_viewer

# ==========================================
# 1. Page Configuration & Theme
# ==========================================

st.set_page_config(
    page_title="Ashen Era Archive Intelligence",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom High-Aesthetic Styling
st.markdown(
    """
    <style>
    /* Dark Fantasy Archive Aesthetic */
    .stApp {
        background-color: #0b0f17;
        color: #e2e8f0;
    }
    
    /* Header & Titles */
    h1, h2, h3 {
        color: #f1f5f9;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    
    /* Citation highlight */
    .citation-tag {
        background-color: #1e293b;
        color: #f59e0b;
        padding: 2px 8px;
        border-radius: 4px;
        font-size: 0.85rem;
        font-family: monospace;
        border: 1px solid #334155;
    }
    
    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #0f172a;
        border-right: 1px solid #1e293b;
    }
    
    /* Metrics */
    [data-testid="stMetricValue"] {
        color: #e5a93c;
    }
    
    /* Quick buttons */
    .demo-btn {
        margin-bottom: 8px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Configuration from Environment
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")


# ==========================================
# 2. Helper Functions & API Calls
# ==========================================

def check_backend_health() -> Dict[str, Any]:
    """Check health and connectivity of FastAPI backend and databases."""
    try:
        res = requests.get(f"{API_URL}/health", timeout=3)
        if res.status_code in (200, 503):
            return res.json()
    except Exception:
        pass
    return {"status": "offline", "databases": {"postgres": "disconnected", "neo4j": "disconnected"}}


@st.cache_data(ttl=60)
def fetch_archive_stats() -> Dict[str, Any]:
    """Fetch counts for documents and entities from API."""
    stats = {"docs": 236, "chunks": 2117, "assets": 70, "entities": 2242}
    try:
        res_docs = requests.get(f"{API_URL}/documents", timeout=5)
        if res_docs.status_code == 200:
            doc_list = res_docs.json()
            stats["docs"] = len(doc_list)
            stats["chunks"] = sum(d.get("chunk_count", 0) for d in doc_list)
    except Exception:
        pass
    return stats


def execute_query(question: str, max_hops: int, top_k: int, include_trace: bool) -> Optional[Dict[str, Any]]:
    """Send query request to FastAPI backend."""
    payload = {
        "question": question,
        "max_hops": max_hops,
        "top_k": top_k,
        "include_trace": include_trace,
    }
    try:
        response = requests.post(f"{API_URL}/query", json=payload, timeout=120)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"API Error ({response.status_code}): {response.text}")
    except requests.exceptions.ConnectionError:
        st.error(f"Could not connect to API backend at {API_URL}. Ensure FastAPI is running on port 8000.")
    except Exception as exc:
        st.error(f"Error executing query: {exc}")
    return None


def execute_search(query: str, search_type: str, top_k: int) -> Optional[Dict[str, Any]]:
    """Send direct search request to FastAPI backend."""
    payload = {
        "query": query,
        "search_type": search_type,
        "top_k": top_k,
    }
    try:
        response = requests.post(f"{API_URL}/search", json=payload, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            st.error(f"Search API Error: {response.text}")
    except Exception as exc:
        st.error(f"Search failed: {exc}")
    return None


# ==========================================
# 3. Session State Initialization
# ==========================================

if "messages" not in st.session_state:
    st.session_state.messages = []

if "current_response" not in st.session_state:
    st.session_state.current_response = None

if "preset_prompt" not in st.session_state:
    st.session_state.preset_prompt = None


# ==========================================
# 4. Sidebar: Settings & Demo Actions
# ==========================================

with st.sidebar:
    st.title("📚 Ashen Era")
    st.caption("Archive Intelligence Platform")

    # Live Backend Connectivity Indicator
    health_info = check_backend_health()
    api_status = health_info.get("status", "offline")
    if api_status == "healthy":
        st.markdown("🟢 **System Online** (PostgreSQL & Neo4j)")
    elif api_status == "degraded":
        st.markdown("🟡 **System Degraded** (Partial DB connectivity)")
    else:
        st.markdown("🔴 **Backend Offline** (`localhost:8000` unreachable)")

    st.divider()

    # Retrieval Configuration Controls
    st.subheader("⚙️ Retrieval Parameters")
    max_hops = st.slider(
        "Max Retrieval Hops (Graph)",
        min_value=1,
        max_value=5,
        value=3,
        help="Maximum hops across Neo4j entity relationships for Track 1B reasoning.",
    )
    top_k = st.slider(
        "Evidence Candidates",
        min_value=5,
        max_value=50,
        value=20,
        help="Number of candidate chunks evaluated by cross-encoder reranker.",
    )
    show_trace = st.checkbox(
        "🔍 Show Query Trace (Track 1C)",
        value=False,
        help="Wire include_trace=True to inspect sub-questions, hops, sufficiency, and verification.",
    )

    st.divider()

    # One-Click Judge Demo Questions
    st.subheader("🎯 Live Demo Questions")
    st.caption("Test key challenge sub-tracks with one click:")

    if st.button("📷 Track 1A: Figure Plate & OCR", use_container_width=True):
        st.session_state.preset_prompt = "What is the recorded garrison strength of Marrowwatch?"

    if st.button("🔗 Track 1B: Multi-Hop Reasoning", use_container_width=True):
        st.session_state.preset_prompt = "What faction did Isolde Mournvale belong to, and what was the outcome of their siege?"

    if st.button("⚔️ Track 1B: Contradictory Sources", use_container_width=True):
        st.session_state.preset_prompt = "What are the conflicting accounts regarding the Fall of Oakhaven?"

    if st.button("🚫 Track 1C: Refusal / Insufficient", use_container_width=True):
        st.session_state.preset_prompt = "What was the warp drive speed of King Roderick's starship?"

    st.divider()

    # Corpus Statistics
    stats = fetch_archive_stats()
    st.subheader("📊 Archive Corpus")
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Documents", f"{stats['docs']:,}")
        st.metric("Visual Assets", f"{stats['assets']:,}")
    with c2:
        st.metric("Chunks", f"{stats['chunks']:,}")
        st.metric("Entities", f"{stats['entities']:,}")


# ==========================================
# 5. Main Content Area (Two Columns)
# ==========================================

st.title("📚 Ashen Era Archive Intelligence")
st.markdown("Evidence-grounded cross-document intelligence for the Ashen Era Archive.")

# Split Layout: 1.75 : 1.25
col_left, col_right = st.columns([1.75, 1.25], gap="large")

# -------------------------------------------------------------
# LEFT COLUMN: Interactive Chat & Direct Search Tabs
# -------------------------------------------------------------
with col_left:
    tab_chat, tab_search = st.tabs(["💬 Archive Intelligence Chat", "🔎 Direct Archive Search"])

    # Tab 1: Grounded Chat Interface
    with tab_chat:
        # Display existing message stream
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("citations"):
                    with st.expander("📚 Citations in this response", expanded=False):
                        for cite in msg["citations"]:
                            p_num = f"p.{cite.get('page')}" if cite.get("page") else "page unlisted"
                            st.markdown(f"- **[{cite.get('document_title', 'Document')}, {p_num}]**: {cite.get('excerpt', '')}")

        # Check if preset question was clicked
        prompt = None
        if st.session_state.preset_prompt:
            prompt = st.session_state.preset_prompt
            st.session_state.preset_prompt = None
        else:
            prompt = st.chat_input("Ask a question about the Ashen Era Archive...")

        # Process user question
        if prompt:
            # Add user message
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Query API backend
            with st.chat_message("assistant"):
                with st.spinner("Analyzing archive, traversing knowledge graph, and verifying evidence..."):
                    result = execute_query(
                        question=prompt,
                        max_hops=max_hops,
                        top_k=top_k,
                        include_trace=show_trace,
                    )

                if result:
                    answer_text = result.get("answer", "")
                    st.markdown(answer_text)

                    citations = result.get("citations", [])
                    if citations:
                        with st.expander("📚 Citations in this response", expanded=False):
                            for cite in citations:
                                p_num = f"p.{cite.get('page')}" if cite.get("page") else "page unlisted"
                                st.markdown(f"- **[{cite.get('document_title', 'Document')}, {p_num}]**: {cite.get('excerpt', '')}")

                    # Store assistant message and update current response state
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer_text,
                        "citations": citations,
                    })
                    st.session_state.current_response = result
                    st.rerun()

    # Tab 2: Direct Search Interface
    with tab_search:
        st.markdown("Search archive chunks directly without LLM answer generation.")
        s_query = st.text_input("Enter search query:", placeholder="e.g., Marrowwatch, Vael, Silent Choir")
        s_col1, s_col2 = st.columns([1, 1])
        with s_col1:
            s_type = st.selectbox("Search Algorithm:", ["hybrid", "bm25", "dense", "entity"])
        with s_col2:
            s_top_k = st.slider("Results to return:", 5, 30, 10, key="search_top_k")

        if st.button("Search Archive", type="primary"):
            if s_query:
                with st.spinner(f"Running {s_type} search..."):
                    search_res = execute_search(s_query, s_type, s_top_k)
                if search_res:
                    st.success(f"Found {search_res.get('total', 0)} matches using `{s_type}` search.")
                    for item in search_res.get("results", []):
                        with st.expander(f"[{item.get('document_title')}] Score: {item.get('score')}"):
                            st.caption(f"Category: `{item.get('source_category')}` | Page: `{item.get('page')}`")
                            st.write(item.get("content"))
            else:
                st.warning("Please enter a search query.")


# -------------------------------------------------------------
# RIGHT COLUMN: Reactive Evidence, Track 1A Images, Trace, Source Reader
# -------------------------------------------------------------
with col_right:
    resp = st.session_state.current_response

    if resp:
        # 1. Evidence Panel (§7.6)
        render_evidence_panel(
            evidence=resp.get("evidence", []),
            conflicts=resp.get("conflicts", []),
            evidence_status=resp.get("evidence_status", "HIGH"),
        )

        st.divider()

        # 2. Track 1A Image Evidence (§7.6b)
        render_image_evidence(
            asset_references=resp.get("asset_references", []),
            api_url=API_URL,
        )

        st.divider()

        # 3. Track 1C Query Trace (§7.8)
        if show_trace and resp.get("trace"):
            render_query_trace(resp.get("trace"))
            st.divider()

    else:
        st.info("👈 Ask a question or click a Quick Demo Question in the sidebar to inspect grounded evidence, multimodal assets, and reasoning traces.")

    # 4. Continuous Document Reader (§7.7)
    with st.expander("📖 Browse Original Archive Documents", expanded=False):
        render_source_viewer(api_url=API_URL)
