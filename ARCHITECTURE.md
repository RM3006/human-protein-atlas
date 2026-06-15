# ARCHITECTURE.md — Protein Atlas

This document explains **how** the atlas is built and **why** it's built that way. For
**what** it does, see [README.md](./README.md); for **when** each piece was built, see
[ROADMAP.md](./ROADMAP.md); for the rationale behind individual decisions (including ones
later reversed), see `MEMORY.md`.

---

## 1. System topology — end-to-end data lifecycle

The atlas is a batch pipeline feeding a read-only serving layer. There is no live
request path from a public source to the UI — every hop below runs offline, on a
schedule the operator controls, and the UI only ever reads pre-computed tables.

```
Public sources                Bronze (R2)              Silver/Gold (MotherDuck)
───────────────                ──────────              ────────────────────────
UniProt REST API   ─HTTPS/REST→  r2://atlas-raw/        ─dbt-duckdb (httpfs,─→  staging views
STRING bulk .tsv.gz─HTTPS GET→   {source}/v{version}/    R2 secret on MD)        (1:1 per source)
HPA proteinatlas.tsv─HTTPS GET→  *.parquet                                       ──dbt SQL──→
Open Targets Parquet─HTTPS GET→                                                  Gold marts
                                                                                  (dim_*, fact_*)

ML branch (parallel, depends on dim_protein)
─────────────────────────────────────────────
dim_protein.sequence ─DuckDB read→ embeddings asset
  ─modal.Function.from_name(...).map()→  Modal A10G containers (ESM-2 t33_650M)
  ←(embedding[1280], was_truncated)──────┘
  ─local UMAP (CPU)→ umap_x, umap_y
  ─Parquet→CREATE OR REPLACE TABLE→ MotherDuck fact_embedding
  ─QdrantClient upsert→ Qdrant Cloud "proteins" collection (cosine, 1280-dim)

Serving
───────
Browser ─Streamlit's own protocol→ Streamlit Community Cloud
  apps/ui/data.py ─duckdb.connect("md:atlas?motherduck_token=...")→ MotherDuck
  apps/ui/data.py ─QdrantClient (REST)→ Qdrant Cloud (nearest-neighbor search)
```

**Protocols at each boundary**:

| Boundary | Protocol / mechanism |
|---|---|
| Public sources → ingest assets | HTTPS REST (UniProt, paginated) or plain HTTPS GET with streaming gzip decompression (STRING, HPA, Open Targets) |
| Ingest assets → R2 | `boto3` S3 client against `https://{account_id}.r2.cloudflarestorage.com` (R2 is S3-compatible; `region="auto"`) |
| R2 → MotherDuck | `dbt-duckdb` → MotherDuck (`md:atlas`), reading `r2://atlas-raw/...` via the `httpfs` extension and a **persistent** R2 secret registered once in MotherDuck (so every session — dbt, UI, ad-hoc — resolves `r2://` paths, not just the session that issued `SET s3_*`) |
| Embeddings asset ↔ Modal | `modal.Function.from_name("atlas-esm2", "embed_batch")` + `.map()` — Modal's RPC call interface; the asset process never imports `torch`/`transformers` |
| Embeddings asset → MotherDuck / Qdrant | DuckDB connection (`CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet(...)`) and `QdrantClient` upsert (delete+recreate collection each run) |
| Streamlit UI → MotherDuck | `duckdb.connect(f"md:atlas?motherduck_token={token}")`, one connection cached via `st.cache_resource` and serialized with a `threading.Lock` (DuckDB connections aren't safe for concurrent use) |
| Streamlit UI → Qdrant | `QdrantClient` REST, for the nearest-neighbor / atlas-position lookups |
| Streamlit UI → browser | Streamlit Community Cloud's own frontend protocol — outside this project's control |

**Orchestration**: Dagster OSS (self-hosted), asset-based. `pipelines/atlas/definitions.py`
loads every asset from the `ingest`, `ml`, and `llm` packages via
`load_assets_from_package_module` and binds one shared `R2Resource`. There is no
custom DAG-wiring beyond Dagster's automatic dependency inference from asset I/O.

---

## 2. Codebase directory tour

| Path | Domain |
|---|---|
| `infra/` | OpenTofu. `main.tf`/`providers.tf`/`variables.tf` provision the `atlas-raw` R2 bucket via the AWS provider (R2's S3-compatible API needs no separate Cloudflare provider). |
| `pipelines/atlas/assets/ingest/` | One Dagster asset per public source: `uniprot.py`, `string.py`, `hpa.py`, `opentargets.py`. Each fetches from its source's native format (REST JSON, bulk TSV.gz, Parquet) and writes Bronze Parquet via `R2Resource`. |
| `pipelines/atlas/assets/ml/` | `embeddings.py` — the Dagster asset: reads `dim_protein.sequence`, batches through Modal, runs UMAP locally, writes `fact_embedding` (MotherDuck) and the `proteins` collection (Qdrant). `modal_esm2.py` — the Modal `App` definition (GPU image + `embed_batch`); runs only inside Modal's container, excluded from local type-checking. |
| `pipelines/atlas/assets/llm/` | `rewrites.py` — Anthropic Batch API submission/polling for the `function_friendly` rewrites (Part 5). |
| `pipelines/atlas/resources/r2.py` | `R2Resource` — the single boto3 S3 client wrapper every ingest/ML asset goes through to read/write Bronze Parquet (CLAUDE.md rule 3). |
| `pipelines/atlas/definitions.py` | Dagster code location: wires the three asset packages + `R2Resource`, loads `.env.local`. |
| `models/sources.yml` | External-table declarations over Bronze Parquet in R2 (`read_parquet('r2://...')`). |
| `models/staging/` | 8 views, one per Bronze source (`stg_uniprot`, `stg_string`, `stg_hpa`, `stg_ot_targets`, `stg_ot_diseases`, `stg_ot_associations`, `stg_ot_drugs`, `stg_ot_drug_molecules`, `stg_llm_rewrites`) — picks canonical scalars out of source-native arrays/structs, no business logic yet. |
| `models/marts/` | The Gold star schema: `dim_protein`, `dim_disease`, `dim_drug`, `fact_protein_tissue`, `fact_interaction`, `fact_protein_disease`, `fact_drug_target_disease`, `fact_protein_aa_composition`. All cross-mart joins use `uniprot_accession`. |
| `models/seeds/` | `seed_amino_acids.csv` (20-row amino-acid glossary, joined into `fact_protein_aa_composition` on `amino_acid_code` — a lookup key, distinct from the cross-database `uniprot_accession` join), `dim_protein_editorial.csv` (the 100 hand-curated narratives), `family_group_map.csv` (protein-family grouping used to color the UMAP atlas). |
| `models/queries/protein_story_card.sql` | The canonical story-card shape — one row per protein with `LIST(STRUCT(...))` columns for interaction partners, diseases, and drugs. `apps/ui/data.py`'s `STORY_CARD_SQL` is a hand-port of this. |
| `models/tests/` | 10 singular SQL assertions (grain, range, uniqueness) beyond dbt's generic `unique`/`not_null`/`relationships`/`accepted_values` tests in `_schema.yml`. |
| `apps/ui/data.py` | Data-access layer: connection factories (`connect_motherduck`, `make_qdrant_client`) + typed query functions (story card, search, atlas, AA composition, neighbors). Framework-agnostic — testable against an in-memory DuckDB with fixtures. |
| `apps/ui/app.py` | Streamlit entry point: page config, session-state-driven view dispatch, calls into `data.py`. |
| `apps/ui/render.py` | Presentation helpers (formatting story-card sections, cross-reference links, the atlas plot). |
| `apps/ui/tour.py` | Stateful guided-tour sequence (`st.session_state`). |
| `apps/ui/requirements.txt` | Deploy-only dependency subset (4 packages) for Streamlit Community Cloud's builder — kept in sync with `pyproject.toml`'s version constraints for `streamlit`/`plotly`/`duckdb`/`qdrant-client`. |

---

## 3. Core architectural decisions — used / considered / why

### Bronze (R2 Parquet) as a mandatory landing zone
- **Used**: every ingest asset lands raw, source-shaped Parquet in `r2://atlas-raw/{source}/v{version}/` before any modeling happens.
- **Considered**: writing straight into MotherDuck from the ingest assets.
- **Why**: Bronze is the replay/audit layer — if a dbt model needs to change, it re-reads R2 instead of re-hitting rate-limited public APIs. R2 has no egress fees, which matters because MotherDuck reads it directly via `httpfs` on every `dbt run`. One asset per source (and per Open Targets dataset — 4 separate assets, not one multi-output asset) gives independent re-run granularity, satisfying the idempotent-asset rule (CLAUDE.md rule 4).

### `uniprot_accession` as the only cross-database join key
- **Used**: every mart joins on `uniprot_accession` (CLAUDE.md rule 1); `amino_acid_code` in `fact_protein_aa_composition` is a separate, intentionally-distinct lookup key into the `seed_amino_acids` glossary, not a cross-database join.
- **Considered**: gene symbol.
- **Why**: gene symbols are many-to-many with UniProt accessions and drift across releases. Accessions are stable and recoverable from every source — including STRING, whose ENSP identifiers needed a 4-tier alias-resolution fallback (Part 5) to reach ~99.78% mapping coverage onto `uniprot_accession`, up from ~11% with a naive single-step join.

### MotherDuck + dbt-duckdb as the warehouse
- **Used**: `dbt-duckdb` targets MotherDuck (`md:atlas`); staging views are 1:1 with Bronze sources, Gold marts hold the star schema; `duckdb==1.5.2` is pinned.
- **Considered**: a heavier managed warehouse (Snowflake/BigQuery) — explicitly out of scope per `SETUP.md`.
- **Why**: MotherDuck's free tier is sufficient for ~20k-row dimensions and sub-million-row facts, and DuckDB's local-first model means the same SQL runs identically in CI (in-memory DuckDB + fixtures) and production (MotherDuck). The `1.5.2` pin exists because MotherDuck's server-side extension caps at that DuckDB version (as of 2026-05-31); `dbt-duckdb>=1.10` would otherwise pull `1.5.3` and fail to connect — re-evaluate the pin once MotherDuck upgrades. A **persistent** R2 secret was registered directly in MotherDuck (not just dbt's session) because session-scoped `SET s3_*` left other connections (the UI, ad-hoc queries) defaulting to AWS S3's `eu-central-1` and 404ing against the R2 bucket; the secret requires `REGION 'auto'`.

### Embeddings precomputed once, not on demand
- **Used**: a Dagster asset batches all `dim_protein.sequence` rows (128 at a time) through a Modal-hosted ESM-2 `t33_650M` (`facebook/esm2_t33_650M_UR50D`, fp16, A10G GPU, model baked into the image at build time), then computes UMAP locally on CPU, and writes `fact_embedding` + the Qdrant `proteins` collection.
- **Considered**: computing a protein's embedding at view time if missing; running UMAP on a GPU.
- **Why**: the serving path must stay GPU-free and sub-second — computing ~20k×1280 embeddings once per UniProt release and storing them means the UI does a warehouse lookup / vector search, never an inference call. UMAP is CPU-bound and finishes in minutes over the full matrix, so giving it its own GPU container would add image-build complexity for no speed gain. Sequences longer than 1022 residues (ESM-2's 1024-token context minus `<cls>`/`<eos>`) are truncated before tokenization, with `was_truncated` recorded per protein rather than silently dropped or silently re-extended.

### Qdrant as a dedicated vector index (not DuckDB array math)
- **Used**: a single `proteins` collection (cosine distance, 1280-dim), with point IDs derived deterministically as `sha256(uniprot_accession)[:8] >> 1` — the same function lives in both `embeddings.py` and `data.py` so no ID-mapping table is needed. The collection is deleted and recreated on every materialization.
- **Considered**: brute-force nearest-neighbor via `array_distance` over `fact_embedding` in DuckDB.
- **Why**: DuckDB has no ANN index; a full 20k×1280 scan on every "show neighbors" click would be slow on Streamlit Cloud's free tier. Qdrant's ANN index makes it near-instant. Delete+recreate keeps the asset idempotent without needing diff/upsert logic for a dataset this size.

### No API tier between Streamlit and the data stores
- **Used**: `apps/ui/data.py` calls MotherDuck and Qdrant directly from the Streamlit process.
- **Considered**: the originally-planned Part 6 architecture — a FastAPI service on Modal sitting between Streamlit and the data stores.
- **Why**: nothing besides this Streamlit app would ever consume such an API. A separate tier adds a deployment unit, a network hop, and a second place for story-card logic to drift from `models/queries/protein_story_card.sql`. If a second consumer (a public API, a mobile client) appears, that's the trigger to revisit — until then it's pure speculative generality. The corollary cost: `apps/ui/requirements.txt` is a hand-maintained 4-package subset of `pyproject.toml`, because Streamlit Cloud's free-tier build can't reasonably install the full project (`dagster`, `modal`, `umap-learn`, `dbt-duckdb`, ...) for a UI that imports none of it.

### Ligand → receptor → drug navigation (no synthesized drug lists)
- **Used**: `fact_drug_target_disease` attaches a drug only to its molecular target. A ligand like insulin (`P01308`) shows zero drugs on its own card; its receptor INSR (`P06213`) is a clickable interaction partner whose card lists the insulin analogs.
- **Considered**: deriving a ligand's drug list from its STRING interaction partners, or from proteins sharing a disease association.
- **Why** (verified against live data, see `MEMORY.md`): insulin's top STRING partners include both INSR (correct — 25 drugs) and IGF1R (20 oncology-antibody drugs, biologically wrong for insulin) at similar `combined_score`, with no field to disambiguate; a shared-disease join instead pulls in the entire disease pharmacopeia. Neither derivation is reliable without a new ligand–receptor data source (parked for v2, no new dependency added now — CLAUDE.md rule 6). Rather than invent a link (CLAUDE.md rule 5), the UI relies on the real interaction edge to let the user navigate to the correct card themselves.

### Two-tier editorial content, one UI
- **Used**: `dim_protein.is_curated` is `TRUE` for exactly 100 proteins (`models/seeds/dim_protein_editorial.csv`, hand-written `tagline`/`function_friendly`); the remaining ~20,331 get Claude Haiku batch rewrites of `function_raw` into the same fields, rendered by the identical card layout. Proteins with no UniProt function text (3,258) get a literal "No information available."
- **Considered**: a visually distinct treatment (badges, separate layout) for curated vs. generated content.
- **Why**: the editorial effort lives in the *data* layer (the curation list + a rewrite prompt that's explicitly forbidden from inventing claims beyond `function_raw`, CLAUDE.md rule 5), not a UI fork — the reader shouldn't be able to tell which tier a given card came from.

### ChEMBL deferred to v2
- **Used (v1)**: Open Targets' aggregated drug-target-disease dataset covers `fact_drug_target_disease`.
- **Considered**: ingesting ChEMBL directly for quantitative bioactivity (IC50/Ki).
- **Why**: ChEMBL is a much larger static dump requiring its own `CHEMBL_ID` → `uniprot_accession` join logic, and Open Targets already supplies enough for the story card's drug section. ChEMBL's quantitative affinity data is the highest-ranked v1→v2 extension (kinase × inhibitor heatmap) precisely *because* it's additive, not a v1 blocker.

---

## 4. Technical constraints and boundaries

- **Dataset ceiling**: the UniProt ingest query is hard-filtered to `reviewed:true AND organism_id:9606` — **20,431 proteins**. Every downstream table is bounded by this set; nothing in the pipeline expands beyond reviewed human proteins.
- **Fact-table volumes after business-logic filtering**: `fact_interaction` = 226,539 rows (`combined_score >= 700`, paralog/duplicate-deduped from 472,588 raw STRING edges); `fact_protein_disease` = 697,330 rows (Open Targets `overall_score >= 0.1`, down from ~4.3M raw — ~3.65M low-signal associations dropped); `approved_drugs` capped at the top 5 by clinical phase per protein in the story card (346 proteins have more; DRD2 has 97).
- **MotherDuck**: free-tier, memory-bound — aggregations are pushed into dbt models rather than computed ad hoc by the UI. `duckdb==1.5.2` is a hard pin tied to MotherDuck's server-extension version.
- **DuckDB connection model**: not thread-safe. The Streamlit app holds one cached connection behind a process-wide `threading.Lock`; this is fine at the request volumes a single-instance Community Cloud app sees, but would need a connection pool (or per-session connections) under real concurrency.
- **Modal**: A10G GPU, `max_containers=5`, `timeout=3600`s, batches of 128 sequences in fp16 — roughly 160 calls / 32 serial rounds for the full 20,431-protein set. Total spend budget for Part 4 was <$10; re-embedding only happens on a new UniProt release or model-version bump, not on a schedule.
- **ESM-2 context window**: 1022-residue cap (1024-token context minus `<cls>`/`<eos>`); 2,302 of 20,431 proteins are truncated. `was_truncated` is stored but does not exclude a protein from embedding or display.
- **Qdrant**: single `proteins` collection, 20,431 points × 1280-dim float32 (~105 MB), cosine distance. Recreated wholesale on each embeddings run — no incremental upsert path exists, so a partial re-embed isn't currently possible without a full rebuild.
- **Streamlit Community Cloud**: single instance, no horizontal scaling, no auth. Sleeps after ~7 days with zero traffic — mitigated by an external cron-job.org ping to `/healthz` (Tuesday and Friday, 04:00 UTC; see `SETUP.md` Phase F2). `apps/ui/requirements.txt` must be manually kept in sync with `pyproject.toml`'s version constraints for the 4 packages it lists.
- **CI gate**: `ruff`, `pyright --strict`, and `pytest` (100 tests across `pipelines/atlas`, `apps/ui`, and dbt) must all pass before merge. Every Dagster asset, dbt model, and Qdrant write is idempotent — safe to rerun without manual cleanup (CLAUDE.md rule 4).
