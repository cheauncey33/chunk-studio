# Top-8 preview-window ablation

- Baseline: legacy metadata plus blind text[:800] preview
- Treatment: type-aware locator cards; section snippets around query hits ±100 chars; table values hidden
- Cases: 33

| Metric | Baseline | ±100 treatment | Delta |
|---|---:|---:|---:|
| Strict Top-8 recall | 27.3% (9/33) | 27.3% (9/33) | 0.0 pp |
| Query-term visibility, eligible section candidates | 91.3% | 100.0% | +8.7 pp |
| Query-term visibility, eligible long sections | 88.1% | 100.0% | +11.9 pp |
| Strict Top-8 gold cases visibly selectable | 88.9% | 55.6% | -33.3 pp |
| └ table-gold cases | 80.0% | 20.0% | -60.0 pp |
| └ long-section-gold cases | n/a | n/a | n/a |
| Average 8-card context | 8775.5 chars | 3225.4 chars | 63.2% shorter |
| P95 8-card context | 9634 chars | 3946 chars | 59.0% shorter |

## Increment versus the last commit (±250)

The ±100 branch reduces average 8-card context from 4348.7 to 3225.4 characters (25.8%), while preserving the same literal-match visibility as ±250. P95 falls from 6015 to 3946 characters (34.4%).

## Interpretation

The preview formatter is downstream of retrieval and reranking, so it cannot improve Top-8 recall or reranker ordering. Its measurable effect is evidence visibility inside already-ranked section chunks and prompt-size reduction. Table previews intentionally remain locator-only and require `read_chunk` for values.

This is an offline deterministic ablation. Query-term visibility is not equivalent to end-to-end answer accuracy; use a paired agent run to claim answer-quality gains.

## Reproduce

From `services/pi-audit-sidecar`, run:

```powershell
npm ci
npm run eval:preview -- --db <path-to-chunkstudio.db> --json <report.json> --markdown <report.md>
```

The report used 72 selected chunks; corpus digest: `a6a1f03677d7326bf57f281f43c5e75200f403e41fcc1a7f81f3d87ac772bbc5`.
