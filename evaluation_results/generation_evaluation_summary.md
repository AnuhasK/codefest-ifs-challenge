# Generation & Grounding Evaluation Summary: Evidence Quality + Claims

- **Generated**: 2026-09-06T23:32:23Z
- **Total Questions Evaluated**: 4

## Key Performance Metrics

| Metric | Result | Target / Standard |
|---|---|---|
| **Citation Accuracy** | `100.0%` | 100% of citations map to retrieved documents |
| **Conflicts Detected** | `2` | Disagreements surfaced with opposing sources |
| **Verification Pass Rate** | `100.0%` | Factually supported claims + conflict acknowledged |
| **Unsupported Claim Rate** | `0.0%` | < 10% unsupported assertions |
| **Average Latency** | `121.47s` | < 45s for 1B queries |
| **Max Latency** | `273.15s` | < 45s threshold |

## Question Breakdown

| ID | Category | Evidence Status | Latency | Verified | Citations | Conflicts |
|---|---|---|---|---|---|---|
| `q1_multihop_ederon` | multi_hop_track1b | MEDIUM | 57.17s | YES | 4 | 0 |
| `q2_multihop_isolde` | multi_hop_track1b | MEDIUM | 273.15s | YES | 4 | 0 |
| `q3_conflict_fenspire` | contradiction_nuance | HIGH | 22.39s | YES | 6 | 0 |
| `q4_insufficient_lore` | insufficient_evidence | HIGH | 133.18s | YES | 0 | 2 |
