from pathlib import Path
from typing import List, Dict, Any
import streamlit as st


def render_image_evidence(asset_references: List[Dict[str, Any]], api_url: str = "http://localhost:8000"):
    """
    Track 1A: Multimodal Visual Evidence Component.
    Embeds actual archive image files (figure plates, atmospheric illustrations, portraits)
    alongside RapidOCR structured data tables or Gemini Vision descriptions.
    """
    if not asset_references:
        return

    st.subheader("📷 Visual Evidence (Track 1A)")

    for asset in asset_references:
        asset_type = asset.get("asset_type", "image").replace("_", " ").title()
        entity_name = asset.get("entity_name") or "Archive Asset"
        file_path = asset.get("file_path", "")
        img_url = asset.get("image_url")
        extracted_data = asset.get("extracted_data") or {}
        description = asset.get("description", "")

        with st.expander(f"🖼️ {asset_type}: {entity_name}", expanded=True):
            # 1. Determine image source (local path or API streaming URL)
            p = Path(file_path) if file_path else None
            image_source = None
            if p and p.exists() and p.is_file():
                image_source = str(p)
            elif img_url:
                image_source = f"{api_url.rstrip('/')}{img_url}"
            elif file_path:
                image_source = file_path

            if image_source:
                try:
                    st.image(image_source, caption=f"{entity_name} ({asset_type})", use_container_width=True)
                except Exception as exc:
                    st.warning(f"Could not render image: {exc}")

            # 2. Render RapidOCR Structured Data for Figure Plates
            if asset.get("asset_type") == "figure_plate" and extracted_data:
                st.markdown("**📊 Structured In-World Data (RapidOCR):**")

                # Metrics table or display
                all_metrics = extracted_data.get("all_metrics", {})
                num_val = extracted_data.get("numerical_value")
                scale = extracted_data.get("scale_or_unit", "")

                if all_metrics and isinstance(all_metrics, dict):
                    metric_cols = st.columns(min(len(all_metrics), 3))
                    for idx, (m_key, m_val) in enumerate(all_metrics.items()):
                        with metric_cols[idx % len(metric_cols)]:
                            st.metric(label=m_key, value=f"{m_val:,}" if isinstance(m_val, (int, float)) else str(m_val))

                if num_val is not None:
                    st.markdown(f"- **Primary Recorded Metric:** `{num_val:,}` {scale}")

                if extracted_data.get("provenance_note"):
                    st.caption(f"**Verification Note:** {extracted_data['provenance_note']}")

            # 3. Vision Description (for atmospheric art / portraits)
            if description:
                st.markdown("**📝 Archive Description:**")
                st.info(description)
