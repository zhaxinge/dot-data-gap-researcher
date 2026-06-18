# Demo

**One-liner:** QC validated operational network data in production; this repo shows the same pattern on transportation open data — Python trust layer first, LLM for synthesis second.

## 2-Minute Script

### Verbal (60 sec) — QC browser tool

> "In production I built a QC trust layer for operational network data — 1,522 intersections, 93% time reduction. The LLM never touched raw geometry; Python validated first, then the agent drafted actions. Same pattern here."

### Screen-share (90 sec) — this repo

```powershell
cd backend
.venv\Scripts\activate
python ..\cli\interview_demo.py --cached
```

Or live run (if portal API is up):

```powershell
python ..\cli\interview_demo.py
```

**Show on screen:**

1. Terminal output — headline stats + top 5 true gaps by `priority_score`
2. `backend/workspace/data/gaps.csv` — filter `gap_class=true_gap`, sort `priority_score` desc
3. `backend/workspace/reports/gap_analysis_report.md` — sections:
   - **True Gaps** vs **Intentionally External**
   - **Issues by Provider**
4. One CSV row — `evidence` + `recommended_action` columns

**Say out loud:**

> "The LLM never invents gaps — Python diff with fuzzy match threshold 85, every row has evidence and a recommended action. Same pattern I'd use before an iQ agent flags a lane rate exception."

## Screen-Share Checklist

- [ ] `python cli/interview_demo.py` runs in under 3 minutes (or `--cached` for rehearsal)
- [ ] `gaps.csv` has single `primary_category` per row (not 7 semicolon tags)
- [ ] True gaps sorted by `priority_score` descending
- [ ] Report shows provider rollup
- [ ] One expanded row: `evidence` JSON + `recommended_action`

## DAT iQ Parallel

| This repo | DAT iQ / agentic BI |
|-----------|---------------------|
| `fetch_dot_catalog` | Ingest lane / benchmark feeds |
| `analyze_catalog_gaps` (deterministic diff) | Rule-based exception detection |
| `gap_class` + `priority_score` | Severity / priority ranking |
| `evidence` + `recommended_action` | Draft action + audit trail |
| Human reviews `gaps.csv` | Analyst approves before execution |

## Follow-Up Answers

**Why not LLM-first?**
Hallucinated gaps are worse than missed gaps in compliance contexts. Deterministic matching with audit evidence scales to agent workflows.

**How do you handle external sources?**
`gap_class=intentional_external` for registries (GBFS, Transitland, RITIS). `recommended_action=link_only` — visibility without forcing portal mirror.

**What about stale data?**
`stale_metadata` flags datasets with no update in 2+ years; scored and surfaced in provider rollup.

**Duplicate datasets?**
`dedup.py` fuzzy-clusters portal titles ≥90% similarity; `duplicate_of` on quality gaps.

**Link rot?**
Optional `--check-links` HEAD-checks top 50 true-gap URLs; `link_status` column in CSV.

## Key Files

| File | Purpose |
|------|---------|
| `cli/interview_demo.py` | One command for demo |
| `cli/smoke_test_tools.py` | Full pipeline; `--check-links` optional |
| `backend/tools/gap_analysis.py` | Core diff, evidence, provider rollup |
| `backend/tools/gap_scoring.py` | `priority_score` sorting |
| `data/reference/provider_aliases.yaml` | Provider normalization |

## Rehearsal Target

- Total spoken time: **2 minutes**
- Live command: **under 3 minutes** (cached OK if portal 503)
- Filter CSV: `gap_class=true_gap` → top 10 sensible federal datasets
