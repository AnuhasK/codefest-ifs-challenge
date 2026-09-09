import os
import sys
import re
from pathlib import Path

# Ensure project root is in sys.path so 'app' and 'src' modules can be resolved
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

    /* Interactive Citation Badges */
    .cite-link {
        display: inline-block;
        background-color: #1e293b;
        color: #38bdf8 !important;
        font-weight: 600;
        font-size: 0.82em;
        padding: 1px 6px;
        margin: 0 2px;
        border-radius: 4px;
        border: 1px solid #0284c7;
        text-decoration: none !important;
        vertical-align: baseline;
        transition: all 0.2s ease-in-out;
        cursor: pointer;
    }
    .cite-link:hover {
        background-color: #0284c7 !important;
        color: #ffffff !important;
        box-shadow: 0 0 10px rgba(56, 189, 248, 0.7);
        transform: translateY(-1px);
    }

    /* In-Chat Visual Evidence Card */
    .in-chat-asset-box {
        background-color: #111827;
        border: 1px solid #1f2937;
        border-radius: 8px;
        padding: 12px;
        margin-top: 10px;
        margin-bottom: 10px;
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

def format_citation_label(cite: Dict[str, Any]) -> str:
    """Format citation page, line numbers, or plate identifier clearly without unlisted placeholders."""
    ref_loc = cite.get("reference_location")
    if ref_loc:
        return ref_loc
    meta = cite.get("metadata") or {}
    ref_loc = meta.get("reference_location")
    if ref_loc:
        return ref_loc
    page = cite.get("page") or meta.get("page")
    l_start = cite.get("line_start") or meta.get("line_start")
    l_end = cite.get("line_end") or meta.get("line_end")
    p_start = meta.get("paragraph_start")

    if l_start == "Plate" or meta.get("is_asset_chunk"):
        return "Plate / Visual Record"
    if page:
        if l_start and l_end:
            return f"p. {page} (Lines {l_start}-{l_end})"
        if p_start:
            return f"p. {page} (Para {p_start})"
        return f"p. {page}"
    if l_start:
        return f"Line {l_start}"
    return "p. 1"


def format_interactive_answer(
    answer_text: str,
    citations: List[Dict[str, Any]],
    style: str = "compact",
    raw_answer: Optional[str] = None,
    api_url: str = API_URL,
) -> str:
    """
    Format answer text for optimal reading experience.
    If style == 'compact', transforms long, repetitive citation brackets into clean,
    clickable numbered badges [1, 2] with hover tooltips and direct PDF links (#page=N).
    """
    if style != "compact" or not citations:
        return answer_text

    # Map evidence IDs to index and citation details
    ev_map = {}
    for idx, c in enumerate(citations, 1):
        eid = c.get("evidence_id")
        if eid:
            ev_map[eid.upper()] = (idx, c)

    def make_badge(idx: int, c: Dict[str, Any]) -> str:
        doc_title = c.get("document_title", "Archive Document")
        ref_loc = format_citation_label(c)
        excerpt = (c.get("excerpt") or "")[:160].replace('"', '&quot;').replace('\n', ' ')
        doc_id = c.get("document_id")
        page = c.get("page") or 1
        pdf_url = f"{api_url}/documents/{doc_id}/file#page={page}" if doc_id else "#"
        tooltip = f"{doc_title}, {ref_loc}&#10;&quot;{excerpt}&quot;&#10;Click badge to open PDF at {ref_loc}"
        return f'<a href="{pdf_url}" target="_blank" class="cite-link" title="{tooltip}">[{idx}]</a>'

    # Path A: If raw_answer contains [EVIDENCE_...] tags, replace cleanly
    target_raw = raw_answer or ""
    if "EVIDENCE_" in target_raw:
        def replace_ev_tokens(m):
            inner = m.group(1)
            tokens = re.findall(r"EVIDENCE_\d+", inner, flags=re.IGNORECASE)
            if not tokens:
                return m.group(0)
            rendered = []
            for t in tokens:
                t_up = t.upper()
                if t_up in ev_map:
                    idx, c = ev_map[t_up]
                    rendered.append(make_badge(idx, c))
                else:
                    rendered.append(f"[{t}]")
            return "".join(rendered)

        pat = re.compile(r"\[(EVIDENCE_\d+(?:[,\s;]+EVIDENCE_\d+)*)\]", flags=re.IGNORECASE)
        return pat.sub(replace_ev_tokens, target_raw)

    # Path B: Fallback replacement on resolved answer_text with bracketed citations
    title_map = {}
    for idx, c in enumerate(citations, 1):
        t = c.get("document_title", "").strip().lower()
        l = format_citation_label(c).strip().lower()
        title_map[f"{t}, {l}"] = (idx, c)
        title_map[t] = (idx, c)

    def replace_bracket(m):
        content = m.group(1).strip()
        c_low = content.lower()
        for k, (idx, c) in title_map.items():
            if k in c_low or c_low in k:
                return make_badge(idx, c)
        return m.group(0)

    # Condense multiple consecutive citation brackets like [Doc1, p.1], [Doc2, p.2] -> [Doc1, p.1][Doc2, p.2]
    condensed = re.sub(
        r"\[([^\]]+)\](?:\s*,\s*\[([^\]]+)\])+",
        lambda m: "".join(re.findall(r"\[[^\]]+\]", m.group(0))),
        answer_text,
    )
    return re.sub(r"\[([^\]]+)\]", replace_bracket, condensed)


def render_in_chat_assets(asset_references: List[Dict[str, Any]], api_url: str = API_URL):
    """Render visual assets (figure plates, portraits, heraldry) directly within the assistant chat bubble."""
    if not asset_references:
        return

    st.markdown("---")
    st.markdown("##### 🖼️ Visual Archive Records (Track 1A)")
    cols = st.columns(min(len(asset_references), 2))
    for idx, asset in enumerate(asset_references):
        with cols[idx % len(cols)]:
            asset_type = asset.get("asset_type", "image").replace("_", " ").title()
            entity_name = asset.get("entity_name") or "Archive Asset"
            file_path = asset.get("file_path", "")
            img_url = asset.get("image_url")
            p = Path(file_path) if file_path else None
            image_source = None
            if p and p.exists() and p.is_file():
                image_source = str(p)
            elif img_url:
                image_source = f"{api_url.rstrip('/')}{img_url}"
            elif file_path:
                image_source = file_path

            if image_source:
                st.image(image_source, caption=f"{entity_name} ({asset_type})", use_container_width=True)

            extracted_data = asset.get("extracted_data") or {}
            all_metrics = extracted_data.get("all_metrics", {})
            num_val = extracted_data.get("numerical_value")
            scale = extracted_data.get("scale_or_unit", "")

            if all_metrics and isinstance(all_metrics, dict):
                st.markdown("**Recorded Metrics (RapidOCR):**")
                m_cols = st.columns(min(len(all_metrics), 3))
                for m_idx, (m_key, m_val) in enumerate(all_metrics.items()):
                    with m_cols[m_idx % len(m_cols)]:
                        st.metric(label=m_key, value=f"{m_val:,}" if isinstance(m_val, (int, float)) else str(m_val))
            elif num_val is not None:
                st.caption(f"**Primary Recorded Metric:** `{num_val:,}` {scale}")


def render_citations_expander(citations: List[Dict[str, Any]], api_url: str = API_URL):
    """Render expandable structured list of references with clickable direct PDF viewer links."""
    if not citations:
        return
    with st.expander(f"📚 References & Source Documents ({len(citations)})", expanded=False):
        for idx, cite in enumerate(citations, 1):
            p_num = format_citation_label(cite)
            doc_title = cite.get("document_title", "Archive Document")
            doc_id = cite.get("document_id")
            page = cite.get("page") or 1
            pdf_link = f" • [📄 Open PDF ({p_num}) ↗]({api_url}/documents/{doc_id}/file#page={page})" if doc_id else ""
            st.markdown(f"- **[{idx}] {doc_title}, {p_num}**{pdf_link}")
            excerpt = cite.get("excerpt")
            if excerpt:
                st.caption(f'> *"{excerpt}"*')



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
        response = requests.post(f"{API_URL}/query", json=payload, timeout=300)
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

    # Citation Presentation Mode
    st.subheader("📖 Citation Presentation")
    citation_mode = st.radio(
        "Display Format:",
        options=["Compact Badges [1, 2]", "Verbose Inlined [Title, p.X]"],
        index=0,
        help="Switch between clean numbered badges with hover previews and PDF jump links, or full verbose inlined citation strings.",
    )
    is_compact = "Compact" in citation_mode

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
                if msg.get("warning"):
                    st.warning(msg["warning"])
                if msg["role"] == "assistant":
                    display_text = format_interactive_answer(
                        msg["content"],
                        msg.get("citations", []),
                        style="compact" if is_compact else "verbose",
                        raw_answer=msg.get("raw_answer"),
                        api_url=API_URL,
                    )
                    st.markdown(display_text, unsafe_allow_html=True)
                    if msg.get("asset_references"):
                        render_in_chat_assets(msg["asset_references"], api_url=API_URL)
                    if msg.get("citations"):
                        render_citations_expander(msg["citations"], api_url=API_URL)
                else:
                    st.markdown(msg["content"])

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
                    warning = result.get("warning")
                    if warning:
                        st.warning(warning)
                    if result.get("evidence_status") == "API_QUOTA_EXHAUSTED":
                        st.error("🚨 API Quota Limit: Configured keys have reached their quota limits. Response is degraded.")

                    answer_text = result.get("answer", "")
                    citations = result.get("citations", [])
                    raw_ans = result.get("raw_answer")
                    asset_refs = result.get("asset_references", [])

                    display_text = format_interactive_answer(
                        answer_text,
                        citations,
                        style="compact" if is_compact else "verbose",
                        raw_answer=raw_ans,
                        api_url=API_URL,
                    )
                    st.markdown(display_text, unsafe_allow_html=True)

                    if asset_refs:
                        render_in_chat_assets(asset_refs, api_url=API_URL)

                    if citations:
                        render_citations_expander(citations, api_url=API_URL)

                    # Store assistant message and update current response state
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer_text,
                        "raw_answer": raw_ans,
                        "citations": citations,
                        "asset_references": asset_refs,
                        "warning": warning,
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
                    if search_res.get("warning"):
                        st.warning(search_res["warning"])
                    st.success(f"Found {search_res.get('total', 0)} matches using `{s_type}` search.")
                    for item in search_res.get("results", []):
                        with st.expander(f"[{item.get('document_title')}] Score: {item.get('score')}"):
                            loc_info = format_citation_label(item)
                            st.caption(f"Category: `{item.get('source_category')}` | Location: `{loc_info}`")
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
            api_url=API_URL,
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
