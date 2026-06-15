# Category Research Protocol

This document supports **multi-round interviews** to validate and improve portal category taxonomy for the DOT Data Gap Research Agent.

Configuration files:

- [`data/reference/category_taxonomy.yaml`](../data/reference/category_taxonomy.yaml) — canonical names and Socrata aliases
- [`data/reference/expected_topics.yaml`](../data/reference/expected_topics.yaml) — keyword fallbacks for uncategorized datasets

## Goals

1. Collapse duplicate Socrata labels (`"and"` vs `"&"`) into one canonical bucket per homepage category
2. Validate that high-download datasets sit in the category users expect
3. Identify mis-tagged or cross-modal datasets that need a secondary tag

## Interview rounds

### Round 1 — DOT data stewards (3–5 participants)

**Materials:** `workspace/reports/gap_analysis_report.md`, category audit CSV (export from popularity rankings)

**Tasks:**

- Review the 12 canonical categories in `category_taxonomy.yaml`
- Flag datasets where `raw_category` ≠ expected canonical name
- Confirm whether **Administrative** should remain separate from modal categories

**Output:** Updated aliases in `category_taxonomy.yaml`

### Round 2 — Researchers and MPO analysts (5–8 participants)

**Materials:** Top 10 popular datasets per category from `popular_datasets_by_category.md`

**Tasks:**

- Card-sort: given 50 dataset titles, assign each to one canonical category
- Compare card-sort results to automated `categorize_dataset()` output
- Note datasets that belong in multiple categories (suggest secondary tags)

**Output:** Keyword additions in `expected_topics.yaml`

### Round 3 — API developers and data engineers (3–5 participants)

**Materials:** `popularity_rankings.json`, sample Socrata API endpoints

**Tasks:**

- Confirm whether **download count** or **API call volume** better reflects "popularity"
- Identify datasets with high views but low downloads (metadata-only assets)
- Recommend separating portal links from downloadable data in reports

**Output:** Popularity sort preference documented in README; optional switch to Site Analytics (`fa6d-d2xr`) for API-weighted ranking

## Audit CSV export

After running:

```powershell
python cli/smoke_test_tools.py --with-popularity --popularity-limit 200
```

Use `workspace/data/popularity_rankings.json` and `dot_catalog_snapshot.json` to build a spreadsheet with:

| Column | Source |
|--------|--------|
| title | dot catalog |
| raw_category | `domain_category` |
| canonical_category | normalized |
| download_count | popularity enrich |
| view_count | popularity enrich |
| landing_page | dot catalog |
| tags | dot catalog |

## Updating taxonomy after interviews

1. Add new aliases under the matching `canonical` entry in `category_taxonomy.yaml`
2. Re-run `python cli/smoke_test_tools.py` and verify **Coverage by Portal Category** shows ~12 lines (not 19+)
3. Commit taxonomy changes separately from code changes for traceability

## Success metrics

- ≤12 category rows in gap report coverage section
- ≥80% agreement between card-sort and automated category for top-50 popular datasets
- Interview notes archived alongside report date in `workspace/reports/`
