# Corpus-bound transformer retrieval evaluation

Status: `complete`
Cases: `440/440`; unique queries: `130/130`; query errors: `0`

> These are AI corpus-verified chunk bindings, not human-approved retrieval gold. All source report cases remain report_judgment_scoreable=false.

| K | Strict case recall | Evidence group recall | Any-group case recall |
|---:|---:|---:|---:|
| 1 | 33.4% (147/440) | 33.4% (147/440) | 33.4% (147/440) |
| 3 | 47.7% (210/440) | 47.7% (210/440) | 47.7% (210/440) |
| 5 | 67.7% (298/440) | 67.7% (298/440) | 67.7% (298/440) |
| 8 | 73.6% (324/440) | 73.6% (324/440) | 73.6% (324/440) |
| 10 | 78.0% (343/440) | 78.0% (343/440) | 78.0% (343/440) |
| 15 | 88.4% (389/440) | 88.4% (389/440) | 88.4% (389/440) |
| 20 | 90.7% (399/440) | 90.7% (399/440) | 90.7% (399/440) |
| 30 | 92.3% (406/440) | 92.3% (406/440) | 92.3% (406/440) |

All 440 eligible cases currently have exactly one required evidence group, so the three recall columns are expected to be identical.

Retrieval mode: `production hybrid_search with query planner and reranker`; reranker: `qwen3-rerank`; maximum K: `30`; candidates/type: dense `30`, lexical `30`.
Query rewrite fallback: `96/130` unique queries, affecting `292/440` cases; reasons: `{'query_rewrite_failed': 96}`. All queries were still reranked.
Average rerank candidates: `95.4`; average rerank stage: `389.3 ms`.
