from typing import Optional, List, Dict, Any
import requests
import streamlit as st


def render_source_viewer(api_url: str = "http://localhost:8000", highlighted_doc_id: Optional[str] = None):
    """
    Source Viewer Component (§7.7).
    Allows users to inspect original archive documents, section trees,
    and individual chunks in continuous reading order.
    """
    st.subheader("📖 Archive Document Reader")

    try:
        # Fetch document list from API
        res = requests.get(f"{api_url.rstrip('/')}/documents", timeout=10)
        if res.status_code != 200:
            st.error(f"Failed to fetch document catalog from API (status {res.status_code})")
            return

        docs: List[Dict[str, Any]] = res.json()
        if not docs:
            st.info("No documents found in archive database.")
            return

        # Document selection dropdown
        doc_options = {d["title"]: d["id"] for d in docs}
        titles = list(doc_options.keys())

        default_idx = 0
        if highlighted_doc_id:
            for idx, d in enumerate(docs):
                if d["id"] == highlighted_doc_id:
                    default_idx = idx
                    break

        selected_title = st.selectbox(
            "Select an archive document to inspect:",
            options=titles,
            index=default_idx,
            key="source_viewer_select",
        )

        selected_id = doc_options[selected_title]

        # Fetch document details & chunks
        d_res = requests.get(f"{api_url.rstrip('/')}/documents/{selected_id}", timeout=10)
        c_res = requests.get(f"{api_url.rstrip('/')}/documents/{selected_id}/chunks", timeout=15)

        if d_res.status_code == 200:
            doc_detail = d_res.json()
            st.markdown(f"### {doc_detail.get('title')}")
            col1, col2 = st.columns(2)
            with col1:
                st.caption(f"**Category:** `{doc_detail.get('source_category')}`")
            with col2:
                st.caption(f"**Source File:** `{doc_detail.get('source_path')}`")

            # Sections outline if present
            sections = doc_detail.get("sections", [])
            if sections:
                with st.expander(f"📑 Section Outline ({len(sections)} sections)"):
                    for s in sections:
                        indent = "&nbsp;" * (s.get("level", 1) * 4)
                        st.markdown(f"{indent}• **{s.get('title')}**", unsafe_allow_html=True)

        if c_res.status_code == 200:
            chunks = c_res.json()
            st.markdown(f"##### Document Passages ({len(chunks)} chunks)")
            for c in chunks:
                page_info = f"p.{c['page_start']}" if c.get("page_start") else "chunk"
                sec = f" — *{c['section_title']}*" if c.get("section_title") else ""
                with st.expander(f"Passage #{c.get('position', 0) + 1} ({page_info}){sec}"):
                    st.write(c.get("content", ""))

    except Exception as exc:
        st.warning(f"Could not load archive documents: {exc}")
