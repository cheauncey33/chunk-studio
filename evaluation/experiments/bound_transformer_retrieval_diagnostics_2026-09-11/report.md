# Corpus-bound transformer retrieval evaluation

Status: `complete`
Cases: `440/440`; unique queries: `130/130`; query errors: `0`

> These are AI corpus-verified chunk bindings, not human-approved retrieval gold. All source report cases remain report_judgment_scoreable=false.

| K | Strict case recall | Evidence group recall | Any-group case recall |
|---:|---:|---:|---:|
| 1 | 34.3% (151/440) | 34.3% (151/440) | 34.3% (151/440) |
| 3 | 47.7% (210/440) | 47.7% (210/440) | 47.7% (210/440) |
| 5 | 67.7% (298/440) | 67.7% (298/440) | 67.7% (298/440) |
| 8 | 73.4% (323/440) | 73.4% (323/440) | 73.4% (323/440) |
| 10 | 78.0% (343/440) | 78.0% (343/440) | 78.0% (343/440) |
| 15 | 85.5% (376/440) | 85.5% (376/440) | 85.5% (376/440) |
| 20 | 87.5% (385/440) | 87.5% (385/440) | 87.5% (385/440) |
| 30 | 88.9% (391/440) | 88.9% (391/440) | 88.9% (391/440) |

All 440 eligible cases currently have exactly one required evidence group, so the three recall columns are expected to be identical.

Retrieval mode: `production hybrid_search with query planner and reranker`; reranker: `qwen3-rerank`; maximum K: `30`.
Query rewrite fallback: `99/130` unique queries, affecting `311/440` cases; reasons: `{'query_rewrite_failed': 99}`. All queries were still reranked.
