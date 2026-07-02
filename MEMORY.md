# MEMORY.md — Architectural decision log

Compressed build history. Full design rationale is in `ARCHITECTURE.md`; implementation details are in the code.

---

## Part 1 — Foundation + UniProt ingest

Package `atlas` exposed via hatchling `packages = ["pipelines/atlas"]` (strips `pipelines/` prefix); pyright resolves it via `extraPaths = ["pipelines"]`. R2 provisioned with the AWS Terraform provider — R2 is S3-compatible, no separate Cloudflare provider needed. UniProt ingested via REST + cursor pagination; `from __future__ import annotations` removed from asset modules (Dagster's runtime validator rejects stringized hints).

## Part 2 — Remaining data sources

OT pinned to v26.03, 4 separate Dagster assets. STRING links streamed line-by-line via `_IterBytesIO` (avoids 1.6 GB RAM spike); `Accept-Encoding: identity` prevents httpx from pre-decompressing `.gz` before `gzip.open`. Key OT v26.03 schema surprises: path layout changed to `output/` directly, `score→associationScore` rename, `knownDrugsAggregated` replaced by `clinical_target/` (drug names in a separate `drug_molecule/` dataset).

## Part 3 — dbt modeling

`duckdb==1.5.2` pinned to MotherDuck's extension cap — re-evaluate when MotherDuck upgrades. Staging uses inline `read_parquet('{{ var("source_root") }}/...')` rather than dbt `source()` — simpler and configurable (`source_root` defaults to `r2://atlas-raw` in prod, overridden to local fixtures in CI). `maxClinicalStage` in OT v26.03 is a string (`"PHASE_3"`) — mapped to SMALLINT in `dim_drug` via explicit CASE with no ELSE (unknown stages become NULL and fail the `accepted_values` test loudly).

## Part 4 — ESM-2 embeddings, UMAP, Qdrant

Modal: `max_containers=5`, batches of 128 at fp16 on A10G. UMAP runs in the Dagster process on CPU (~5–10 min, no GPU needed). MotherDuck write via local temp Parquet + `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet(...)` (~300× faster than `executemany`). Qdrant point IDs: `sha256(accession)[:8] >> 1` — same function in `embeddings.py` and `data.py`, no ID-mapping table needed.

## Part 5 — LLM rewrites + editorial seed

Two-tier COALESCE: editorial seed → LLM rewrite → `function_raw` verbatim → `'No information available'`. The `function_raw` rung was added after diagnosing two batch-bug buckets: ~41 proteins where Haiku legitimately returned null (terse source text), and ~51 where valid rewrites were discarded because Haiku used unescaped double-quotes inside JSON values — fixed by adding SYSTEM_PROMPT rule 5 and re-running only those via `notebooks/fix_bucket2_rewrites.py` (one-shot, already run). Batch IDs persisted to R2 before polling — Anthropic retains results 29 days.

## STRING ENSP→UniProt resolution

Naive "first alias seen" yielded 10.95% accuracy. Replaced with `_pick_canonical_accession`: 4-tier fallback preferring `Ensembl_HGNC_uniprot_ids` corroborated by the `Ensembl_UniProt ∩ UniProt_AC` intersection, then `UniProt_AC` alone, then `Ensembl_UniProt` as last resort. Validated at **99.78%** (18,800/18,842); remaining 0.2% needs sequence-alignment disambiguation — not pursued.

## dbt P0 bugs + tests

Three P0s fixed: (1) `UNNEST(disease_ids)` was extracting the whole STRUCT as a string — fix: `.diseaseId`; (2) `fact_interaction` had symmetric A↔B duplicates and self-loops — fix: `LEAST/GREATEST` + `MAX(combined_score)` → 226,539 clean pairs; (3) `fact_protein_disease` had paralog-fan duplicate (protein, disease) rows — same `MAX()` fix. Thirteen singular tests added, all named `assert_*` in `models/tests/`.

## MotherDuck R2 persistent secret

Staging-view 404s in non-dbt sessions traced to `SET s3_*` being session-scoped (dbt-local only). Fix: `CREATE OR REPLACE SECRET atlas_r2 IN MOTHERDUCK (TYPE R2, ..., REGION 'auto')` — registered once via `notebooks/setup_motherduck_r2_secret.py` (idempotent, keep). **`REGION 'auto'` is required** — MotherDuck docs say it's optional but the engine defaults to `eu-central-1`, which R2 rejects with HTTP 400. Staging paths use `r2://` prefix to route through this secret.

## fact_protein_disease score floor

`HAVING MAX(overall_score) >= 0.1` applied to `fact_protein_disease`. OT's raw distribution is heavily right-skewed (median ~0.02); unfiltered, EGFR reads as "~2,600 diseases." Floor reduces 4.35M → 697k rows; only 5% of proteins lose all associations. Lossy-in-the-mart by deliberate choice — sub-0.1 rows are never actionable for display.

## Ligand → receptor → drug routing

Drugs attach to their molecular target, never to a ligand. Insulin (P01308) has zero drugs on its card; INSR (P06213) lists the insulin analogs. Two derivation shortcuts verified and rejected: STRING partners are undirected (IGF1R appears at the same confidence as INSR with no field to distinguish them), and shared-disease joins yield the entire disease pharmacopeia.

## protein_story_card: VARCHAR → LIST(STRUCT)

`top_interaction_partners`, `top_diseases`, `approved_drugs` reshaped from baked display strings to typed `LIST(STRUCT(...))`. Reason: 8,119 disease names contain parentheses (string-splitting breaks), and interaction partners needed both accession (link) and gene_symbol (label) without a second query. `approved_drugs` capped at top-5 by phase — DRD2 has 97 phase-3+ drugs.

## Part 6 — Streamlit UI, no API tier

FastAPI/Modal API tier dropped — no consumer other than the Streamlit app would ever call it. `apps/ui/data.py` connects to MotherDuck and Qdrant directly; one DuckDB connection cached via `st.cache_resource` behind a `threading.Lock`. App tracks `main` on Streamlit Community Cloud; URL in README.md is the source of truth.

## Part 7 — Polish

og:image dropped: Streamlit Community Cloud serves a generic shell HTML that crawlers read before JS executes; no Python hook can reach the initial response. 5-stop guided tour added in `apps/ui/tour.py`.

## Part 8 — Amino acid composition

Cross-protein "richest in X" ranking and glossary cards dropped — breaks the "atlas is by protein" principle, and EGFR doesn't rank top-5 for cysteine against the curated set anyway. Per-protein composition tab kept: full sequence + 20 amino acids ranked by `pct_of_sequence` + side-chain-category rollup. `seed_amino_acids` and `fact_protein_aa_composition` dbt layer unchanged.

## CSS `!important` on stVerticalBlockBorderWrapper blocks per-instance overrides

Global CSS that sets `border: ... !important` on `[data-testid="stVerticalBlockBorderWrapper"]` (or on a `.st-key-{key}` rule, see "Container border overrides" above) wins over any per-card override attempted via a CSS variable or a more specific selector — `!important` beats non-`!important` regardless of specificity, and the failure is silent (no error, the color/border just doesn't change). Only matters for cards built with `st.container(border=True, key=...)`; a raw HTML `<div>` injected via `st.markdown(unsafe_allow_html=True)` isn't affected since it never touches that wrapper. If a future card needs a per-instance dynamic border (e.g. a status color), the override must carry matching `!important` on an equal-or-higher-specificity selector, not a plain declaration.

## Part 9 — Deploy

Hero screenshot dropped — live demo link is the primary visual entry point; a static screenshot goes stale on every UI change. Default landing protein changed from insulin (P01308) to **COL1A1 (P02452)**: insulin has zero drugs under the ligand-routing rule, which reads as a bug on first load; COL1A1 has populated partners, diseases (osteogenesis imperfecta), and drugs.

## CI dbt gate

`source_root` var added to all staging models (default `r2://atlas-raw`; CI overrides to `models/fixtures/bronze`). Empty Bronze Parquet stubs committed; `dbt build --exclude tag:real_data` runs on every PR against in-memory DuckDB. Unit tests: `test_fact_interaction_dedup` (LEAST/GREATEST + MAX + self-loop drop) and `test_fact_protein_disease_floor` (0.09 excluded, 0.10 included). Two volume/cardinality guards tagged `real_data` — run manually against the live warehouse, not in CI.

## Qdrant dropped for a precomputed fact_protein_neighbor table

Qdrant Cloud's free-tier cluster auto-pauses after inactivity; a query against a paused cluster surfaced to users as "Sequence-similarity search is temporarily unavailable" on `apps/ui`'s Sequence neighborhood tab. Rather than babysit cluster uptime, the neighbor lookup was moved into the warehouse: a new Dagster asset (`pipelines/atlas/assets/ml/neighbors.py`, `protein_neighbors`) reads `fact_embedding`, computes exact top-20 cosine-similarity neighbors per protein with one numpy matmul (a few seconds at 20,431 rows — brute-force beats an ANN index at this scale, and is exact besides), and writes `fact_protein_neighbor` to MotherDuck. `apps/ui/data.py`'s `find_neighbors` is now a plain SQL join against that table; `qdrant-client` is removed from the project entirely (`embeddings.py`'s Qdrant upsert block and `accession_to_id` deleted from both `embeddings.py` and `data.py`). Full write-up in `ARCHITECTURE.md` under "Nearest-neighbor lookup: precomputed table, not a live vector-search service."
