from typing import List, Dict, Any, Optional
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


def resolve_document_urls(
    source_path: Optional[str] = None,
    document_id: Optional[str] = None,
    page: int = 1,
    api_url: str = "http://localhost:8000",
) -> Dict[str, Any]:
    """
    Resolve browser-accessible URLs using the actual archive location from the project root.
    Returns a dict with primary_url, is_download, action_label, docx_download_url, and filename.
    """
    clean_api = api_url.rstrip("/")
    norm = (source_path or "").replace("\\", "/").strip()
    rel = ""
    if "Ashen_Era_Archive/" in norm:
        rel = norm.split("Ashen_Era_Archive/", 1)[-1].lstrip("/")
    elif norm and not norm.startswith("http"):
        rel = norm.lstrip("/")

    # Known archive folders where every .docx has a sibling .pdf
    has_known_sibling_pdf = False
    if rel:
        low = rel.lower()
        if low.startswith("codex/") or low.startswith("chronicles/"):
            has_known_sibling_pdf = True

    if rel:
        ext = rel.split(".")[-1].lower() if "." in rel else ""
        filename = rel.split("/")[-1]

        if ext == "md":
            return {
                "primary_url": f"{clean_api}/Ashen_Era_Archive/{rel}",
                "is_download": False,
                "action_label": "📄 Open Markdown Source",
                "docx_download_url": None,
                "filename": filename,
            }
        elif ext == "pdf":
            return {
                "primary_url": f"{clean_api}/Ashen_Era_Archive/{rel}#page={page}",
                "is_download": False,
                "action_label": f"📄 Open PDF (p. {page})",
                "docx_download_url": None,
                "filename": filename,
            }
        elif ext == "docx":
            stem_rel = rel.rsplit(".", 1)[0]
            if has_known_sibling_pdf:
                pdf_url = f"{clean_api}/Ashen_Era_Archive/{stem_rel}.pdf#page={page}"
                docx_url = f"{clean_api}/Ashen_Era_Archive/{rel}"
                return {
                    "primary_url": pdf_url,
                    "is_download": False,
                    "action_label": f"📄 Open PDF (p. {page})",
                    "docx_download_url": docx_url,
                    "filename": filename,
                }
            else:
                docx_url = f"{clean_api}/Ashen_Era_Archive/{rel}"
                return {
                    "primary_url": docx_url,
                    "is_download": True,
                    "action_label": "📥 Download Word Document",
                    "docx_download_url": None,
                    "filename": filename,
                }
        elif ext in ("png", "jpg", "jpeg", "webp"):
            return {
                "primary_url": f"{clean_api}/Ashen_Era_Archive/{rel}",
                "is_download": False,
                "action_label": "🖼️ View Image Record",
                "docx_download_url": None,
                "filename": filename,
            }

    # Fallback to document_id endpoint
    if document_id:
        return {
            "primary_url": f"{clean_api}/documents/{document_id}/file#page={page}",
            "is_download": False,
            "action_label": f"📄 Open Document (p. {page})",
            "docx_download_url": f"{clean_api}/documents/{document_id}/file?format=docx",
            "filename": f"document_{document_id}",
        }

    return {
        "primary_url": "#",
        "is_download": False,
        "action_label": "📄 View Document",
        "docx_download_url": None,
        "filename": "",
    }


def render_evidence_panel(
    evidence: List[Dict[str, Any]],
    conflicts: List[Dict[str, Any]],
    evidence_status: str = "HIGH",
    api_url: str = "http://localhost:8000",
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
        "API_QUOTA_EXHAUSTED": ("#dc2626", "#450a0a", "API Quota Exhausted"),
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

                meta = ev.get("metadata") or {}
                s_file = ev.get("source_file") or meta.get("source_path") or meta.get("file_path") or ""
                doc_id = ev.get("document_id")
                page_num = ev.get("page") or 1

                res_urls = resolve_document_urls(
                    source_path=s_file,
                    document_id=doc_id,
                    page=page_num,
                    api_url=api_url,
                )
                p_url = res_urls["primary_url"]
                p_lbl = res_urls["action_label"]
                docx_url = res_urls.get("docx_download_url")

                if p_url != "#":
                    if docx_url:
                        st.markdown(
                            f"[{p_lbl} ↗]({p_url}) &nbsp;|&nbsp; [📥 Download Word Document ↗]({docx_url})",
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f"[{p_lbl} ↗]({p_url})")
    else:
        st.info("No evidence records gathered for this response.")


