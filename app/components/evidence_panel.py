from typing import List, Dict, Any
import streamlit as st


def format_evidence_location(ev: Dict[str, Any]) -> str:
    """Format exact page, line numbers, or plate identifier for an evidence record."""
    meta = ev.get("metadata") or {}
    ref_loc = meta.get("reference_location")
    if ref_loc:
        return ref_loc
    page = ev.get("page") or meta.get("page")
    line_start = meta.get("line_start")
    line_end = meta.get("line_end")
    para_start = meta.get("paragraph_start")

    if line_start == "Plate" or meta.get("is_asset_chunk"):
        return "Plate / Visual Record"
    if page:
        if line_start and line_end:
            return f"p. {page} (Lines {line_start}-{line_end})"
        if para_start:
            return f"p. {page} (Para {para_start})"
        return f"p. {page}"
    if line_start:
        return f"Line {line_start}"
    return "p. 1"


def render_evidence_panel(
    evidence: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    evidence_status: str = "HIGH",
):
    """
    Render evidence quality indicator, contradiction alerts, and expandable evidence passages.
    """
    st.subheader("📊 Grounded Evidence")

    # 1. Evidence Status Badge
    status_colors = {
        "HIGH": ("#10b981", "#064e3b", "Verified High Confidence"),
        "MEDIUM": ("#f59e0b", "#78350f", "Moderate Confidence"),
        "LOW": ("#ef4444", "#7f1d1d", "Low Confidence"),
        "INSUFFICIENT": ("#ef4444", "#7f1d1d", "Insufficient Archive Evidence"),
    }
    badge_color, badge_bg, status_desc = status_colors.get(
        evidence_status.upper(), ("#6b7280", "#1f2937", "Unknown Status")
    )

    st.markdown(
        f"""
        <div style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 10px 14px;
            background-color: {badge_bg};
            border: 1px solid {badge_color};
            border-radius: 8px;
            margin-bottom: 14px;
        ">
            <span style="font-weight: 700; color: #f9fafb; font-size: 14px;">
                Evidence Status: <span style="color: {badge_color};">{evidence_status.upper()}</span>
            </span>
            <span style="font-size: 12px; color: #d1d5db;">{status_desc}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 2. Key Metrics Row
    m1, m2 = st.columns(2)
    with m1:
        st.metric("Sources Used", len(evidence))
    with m2:
        st.metric("Conflicts Found", len(conflicts))

    # 3. Conflict Alerts (if any)
    if conflicts:
        st.warning(f"⚠️ {len(conflicts)} contradiction(s) or divergent accounts detected between sources")
        for idx, conflict in enumerate(conflicts, 1):
            claim = conflict.get("claim_summary", "Contradiction detected")
            with st.expander(f"⚠️ Conflict #{idx}: {claim[:60]}..."):
                st.markdown(f"**Disputed Claim:** {claim}")
                st.markdown(f"**Conflict Type:** `{conflict.get('conflict_type', 'contradiction')}`")

                c_col1, c_col2 = st.columns(2)
                with c_col1:
                    st.markdown("**Supporting Records:**")
                    for sid in conflict.get("supporting", []):
                        st.markdown(f"- `{sid}`")
                with c_col2:
                    st.markdown("**Opposing Records:**")
                    for oid in conflict.get("opposing", []):
                        st.markdown(f"- `{oid}`")

    # 4. Evidence Passages List
    if evidence:
        st.markdown("##### 📄 Cited Sources & Excerpts")
        for ev in evidence:
            ev_id = ev.get("id", "EVIDENCE")
            doc_title = ev.get("document_title", "Archive Document")
            loc_str = format_evidence_location(ev)
            cat = ev.get("source_category", "archive")
            subtype = ev.get("source_subtype", "record")
            content = ev.get("content", "")

            with st.expander(f"[{ev_id}] {doc_title} ({loc_str})"):
                st.caption(f"**Provenance:** Category: `{cat}` | Subtype: `{subtype}` | **Location:** `{loc_str}`")
                if ev.get("section_title"):
                    st.caption(f"**Section:** {ev['section_title']}")
                st.markdown(f"> {content}")
    else:
        st.info("No evidence records gathered for this response.")
