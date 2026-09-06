from typing import Dict, Any, Optional
import streamlit as st


def render_query_trace(trace: Optional[Dict[str, Any]]):
    """
    Track 1C: Observability & Iterative Retrieval Trace Component.
    Visualizes the step-by-step reasoning and retrieval loop:
    1. Query Analysis & Entity Extraction
    2. Multi-Hop Graph Traversal (sub-questions, hops)
    3. Multi-Stream Retrieval & RRF Fusion
    4. Sufficiency Evaluation & Bounded Loop Decisions
    5. Post-Generation Claim Verification
    6. Performance Latency Waterfall
    """
    if not trace:
        return

    st.subheader("🔍 Query Trace (Track 1C)")

    with st.expander("🔬 Agentic Retrieval & Reasoning Trace", expanded=True):
        # 1. Pipeline Overview Banner
        q_type = trace.get("query_type", "simple").upper()
        hops = trace.get("retrieval_hops", 1)
        suff_level = trace.get("sufficiency_level", "N/A")
        verified = trace.get("is_verified", True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("Query Type", q_type)
        with c2:
            st.metric("Graph Hops", hops)
        with c3:
            st.metric("Sufficiency", suff_level)
        with c4:
            st.metric("Verification", "PASS" if verified else "FLAGGED")

        st.divider()

        # 2. Step-by-Step Retrieval Execution
        st.markdown("#### 1. Query Analysis & Entities")
        entities = trace.get("entities", [])
        if entities:
            st.write("**Identified Target Entities:**", ", ".join(f"`{e}`" for e in entities))
        else:
            st.write("No distinct named entities detected; broad semantic search applied.")

        st.markdown("#### 2. Multi-Hop Knowledge Graph Traversal")
        if hops > 1:
            st.success(
                f"Iterative Multi-Hop active: executed {hops} hops across Neo4j graph relationships "
                "to connect distributed evidence across multiple archive documents (Track 1B/1C)."
            )
        else:
            st.info("Single-hop retrieval sufficient for this query.")

        st.markdown("#### 3. Evidence Candidate Pool")
        cand_count = trace.get("candidate_count", 0)
        ev_count = trace.get("evidence_count", 0)
        st.write(f"- Candidates retrieved across hybrid streams (BM25 + Dense + Entity): **{cand_count}**")
        st.write(f"- Unique evidence passages selected after deduplication & reranking: **{ev_count}**")

        st.markdown("#### 4. Latency Breakdown")
        t_ret = trace.get("retrieval_time_s", 0.0)
        t_conf = trace.get("conflict_detection_time_s", 0.0)
        t_gen = trace.get("generation_time_s", 0.0)
        t_ver = trace.get("verification_time_s", 0.0)
        t_tot = trace.get("elapsed_time_s", 0.0)

        lat_cols = st.columns(5)
        with lat_cols[0]:
            st.caption("Retrieval")
            st.write(f"{t_ret:.2f}s")
        with lat_cols[1]:
            st.caption("Conflicts")
            st.write(f"{t_conf:.2f}s")
        with lat_cols[2]:
            st.caption("Generation")
            st.write(f"{t_gen:.2f}s")
        with lat_cols[3]:
            st.caption("Verification")
            st.write(f"{t_ver:.2f}s")
        with lat_cols[4]:
            st.caption("Total Elapsed")
            st.markdown(f"**{t_tot:.2f}s**")

        # 5. Raw JSON Trace
        st.divider()
        st.markdown("##### Raw Trace Payload")
        st.json(trace)
