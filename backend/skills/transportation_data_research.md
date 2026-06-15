# Transportation Data Gap Research Methodology

Use this skill when analyzing public-facing datasets on https://data.transportation.gov.

## Workflow

1. **Catalog the DOT portal** — Call `fetch_dot_catalog` to download `data.json` and enrich via the portal's native Socrata catalog (`data.transportation.gov/api/views.json`). Do not rely on the global Socrata Discovery API with a domain filter for full portal coverage.
2. **Build reference set** — Call `search_data_gov` with `organization:dot`. This merges:
   - catalog.data.gov (org slug `dot`)
   - Static entries from `data/reference/external_catalogs.yaml` (portals, APIs, clearinghouses)
   - Live registry fetchers (MobilityData GBFS, OMF MDS, WZDx, Transitland Atlas US)
   - ArcGIS hubs (USDOT NTAD, BTS geodata)
   - Socrata Discovery cross-portal queries
3. **Run deterministic diff** — Call `analyze_catalog_gaps` to produce `gaps.json` with evidence.
4. **Cross-portal discovery** — Use Tavily web search for datasets on FAA, FHWA, FRA, FTA, NHTSA, FMCSA, PHMSA, MARAD, and BTS portals listed in `data/reference/modal_agencies.yaml`, and for sources documented in `docs/reference-catalog.md`.
5. **Classify gaps** using these statuses:
   - **missing_on_portal** — In reference catalog but not matched on data.transportation.gov
     - `gap_class: true_gap` — federal/cross-portal datasets to mirror
     - `gap_class: intentional_external` — registries, partner portals (link-only)
   - **category_empty** — Portal category has zero datasets
   - **stale_metadata** — No update in 2+ years
   - **no_api_endpoint** — No Socrata/API access path
   - **redirect_only** — Links out to external host without hosted data
6. **Popularity context** — Use `workspace/data/popularity_rankings.json` (from smoke test `--with-popularity`) to cite top datasets per canonical category when prioritizing gaps.
7. **Export report** — Call `export_gap_report` and summarize findings for the user.

## Matching rules

Do not claim a dataset is missing without tool evidence. The diff tool matches on:
- Identifier
- Landing page / distribution URL
- Fuzzy title (≥85% similarity)

## External sources to know

Many datasets are intentionally hosted outside the DOT Socrata portal. When reporting `missing_on_portal` gaps, distinguish:

| Topic | External home | Notes |
|-------|-----------------|-------|
| NPMRDS / probe speeds | RITIS (npmrds.ritis.org) | DSA / agreement required |
| ATSPM | Agency-local (UDOT open-source) | No national DB |
| MIRE | State DOT GIS | FHWA publishes standard only |
| Micromobility | MobilityData GBFS, OMF MDS | Registries, not portal datasets |
| Transit GTFS | Transitland Atlas | DMFR on GitHub |
| Work zones (live) | Per-jurisdiction WZDx APIs | Registry metadata may be on portal; feeds are external |
| NTAD geospatial | geodata.bts.gov, USDOT ArcGIS Hub | Parallel to some portal rows |
| Academic surveys | ICPSR, surveyarchive.org | Research archives |

Full source list: `docs/reference-catalog.md` and `data/reference/external_catalogs.yaml`.

## Report structure

Always present:
1. Executive Summary
2. Gaps by Modal / Agency
3. Gaps by Portal Category
4. Data Quality Issues
5. Recommended Additions (prioritized — note intentional external hosting where applicable)
6. Sources with URLs

## Reference files

- `data/reference/expected_topics.yaml` — 11 portal categories
- `data/reference/modal_agencies.yaml` — modal agency seed URLs
- `data/reference/external_catalogs.yaml` — portals, APIs, registry fetchers, ArcGIS hubs, Socrata queries
- `data/reference/category_taxonomy.yaml` — canonical portal categories and Socrata aliases
- `docs/category-research-protocol.md` — interview rounds for taxonomy validation
