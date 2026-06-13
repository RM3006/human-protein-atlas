# MEMORY.md — Architectural decision log

State tracking for the Protein Atlas build. Newest entries at the bottom of each
part. See `ROADMAP.md` for the plan and `ARCHITECTURE.md` (Part 8) for the full
writeup.

## Part 1 — Foundation + UniProt ingest (complete)

### Decisions made

- **Package layout: `pipelines/atlas/`, imported as `atlas`.** CLAUDE.md rule 8
  requires `from atlas.logging import logger`, while the repo layout names the
  Dagster dir `pipelines/`. Hatchling cannot rewrite a path *prefix* in editable
  installs (only remove one), so `packages = ["pipelines/atlas"]` strips the
  `pipelines/` prefix and exposes the package as `atlas`. Pyright resolves it via
  `extraPaths = ["pipelines"]`.
- **R2 provisioned with the AWS Terraform provider, not the Cloudflare provider.**
  The `.env.local` creds are R2 **S3** access keys; the native `cloudflare`
  provider would need a separate account-level API token. R2 implements S3
  `CreateBucket`, so the `aws` provider against the R2 endpoint (path-style, all
  AWS preflight disabled) creates `atlas-raw` with the keys already on hand.
  Chosen by the user over the native provider.
- **R2 IO via boto3 (new dependency, not in the original fixed stack).** Needed an
  S3 client to read/write Parquet to R2; boto3 is the de-facto choice and pairs
  with the AWS-provider infra decision. `boto3-stubs[s3]` added for pyright.
- **UniProt ingest uses the REST API (cursor pagination), per ROADMAP**, not the
  FTP proteome dump the manifest also mentions. ~41 pages of 500, serial (cursors
  must not be parallelized), with retry/backoff on 429/5xx.
- **Bronze schema keeps cross-references as lists** (`pfam_ids`, `ensembl_gene_ids`,
  `string_ids`, `keywords`, `secondary_accessions`). dbt staging (Part 3) will
  pick the canonical single value. Missing fields → null (rule 5).
- **`from __future__ import annotations` removed from the asset module.** It
  stringizes the `context` hint and Dagster's runtime validation rejects it.
- **Pyright stays `strict`**; the only suppressions are three documented per-line
  `# pyright: ignore` at irreducible third-party stub gaps (boto3 dynamic client,
  `ConfigurableResource` generic, `load_assets_from_package_module` kwargs).

### Result

- `tofu apply` created the `atlas-raw` R2 bucket.
- `uniprot_human_reviewed_raw` materialized **20,431** rows to
  `r2://atlas-raw/uniprot/v2026_01/uniprot_human_reviewed_raw.parquet`.
- Round-trip read confirms schema + insulin (P01308) present. 148 null
  gene symbols (legitimate, kept as null).
- ruff, pyright (strict, 0 errors), pytest (6 passed) all green locally.

---

## Part 2 — Remaining data sources (complete)

### Decisions made

- **Open Targets version pinned to `26.03`** (March 2026 quarterly release).
  FTP base: `https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/26.03/output/`.
- **4 separate Dagster assets for Open Targets** (`ot_targets_raw`, `ot_diseases_raw`,
  `ot_associations_raw`, `ot_drugs_raw`) rather than one multi-output asset.
- **STRING links file streamed, not temp-filed.** `_IterBytesIO` adapts
  `httpx.iter_bytes()` to `io.RawIOBase` so `gzip.open(io.BufferedReader(...))` can
  decompress line-by-line without loading the 1.6 GB uncompressed payload into memory.
- **`Accept-Encoding: identity` header on STRING downloads** prevents httpx from
  auto-decompressing `.gz` files at the transport layer before `gzip.open` sees them.
- **HPA version hardcoded to `v24`**; bump to `v25` when HPA ships the next annual release.
- **Test helpers are public** when tests need to import them directly (pyright strict
  `reportPrivateUsage`). Functions tested only indirectly keep the underscore prefix.

### Schema discoveries during materialization (breaking changes in actual data)

- **HPA v24 dropped `Tissue expression` column.** `rna_tissue_specificity` is the
  replacement. Bronze schema has no `tissue_expression` field; manifest updated.
- **OT v26.03 path layout changed**: `output/etl/parquet/` → `output/` directly.
- **OT dataset renames**: `targets→target`, `diseases→disease/disease.parquet` (single
  file), `associationByOverallDirect→association_overall_direct`.
- **OT column rename**: `score→associationScore` in the associations dataset.
- **`knownDrugsAggregated` removed** in OT v26.03; replaced by
  `clinical_target/clinical_target.parquet`. Drug display names now require a join to
  `drug_molecule/` in the dbt Silver layer (Part 3).
- **`therapeuticAreas` removed** from OT disease table; derivable from `parents` column.
- **`list_parts` regex updated** to match both partitioned (`part-*`) and single-file
  (`disease.parquet`, `clinical_target.parquet`) dataset layouts.

### Materialized row counts (confirmed in R2)

| Asset | R2 key | Rows |
|---|---|---|
| `uniprot_human_reviewed_raw` | `uniprot/v2026_01/` | 20,431 |
| `hpa_proteome_raw` | `hpa/v24/` | 19,180 |
| `string_interactions_raw` | `string/v12.0/` | 472,588 |
| `ot_targets_raw` | `opentargets/v26.03/` | 78,691 (all species; filter to human in dbt) |
| `ot_diseases_raw` | `opentargets/v26.03/` | 47,030 |
| `ot_associations_raw` | `opentargets/v26.03/` | 4,508,002 (all species; filter in dbt) |
| `ot_drugs_raw` | `opentargets/v26.03/` | 13,407 |

### Part 2 is safe to build on

All Bronze assets are idempotent (safe to rerun), typed Parquet in R2, and verified
against the source. Part 3 (dbt) reads from these keys. Key join notes for dbt:
- Filter `ot_targets_raw` and `ot_associations_raw` to human proteins via join on
  `uniprot_accession` from the UniProt Bronze asset.
- Drug names: join `ot_drugs_raw.drugId` → `drug_molecule/` OT dataset.
- STRING: `uniprot_a` and `uniprot_b` are already resolved UniProt accessions.
- HPA: `uniprot_accession` joins directly to `dim_protein`.

### Next steps (Part 3)

- `dbt init` with `dbt-duckdb` pointing at MotherDuck `atlas` database.
- `models/sources.yml` pointing at the 7 Bronze Parquet files in R2.
- Staging models (one per source), then seven mart tables per the manifest schema.
- Exit criterion: `protein_story_card.sql` returns a complete row for insulin (P01308).

---

## Part 3 — dbt modeling: Bronze → Silver → Gold (complete)

### Decisions made

- **dbt model-paths**: `["staging", "marts", "queries"]` — using each folder as its
  own model-path root avoids dbt parsing `dbt_project.yml` as a schema file (which
  happens with `["."]`). Mart materialization is set via inline
  `{{ config(materialized='table') }}` blocks since folder-level config keys don't
  work with this layout.
- **`duckdb==1.5.2` pinned**: MotherDuck's extension only supported up to DuckDB
  1.5.2 as of 2026-05-31; `dbt-duckdb` 1.10.1 would otherwise pull 1.5.3, which
  fails on connect.
- **Staging models use `read_parquet('s3://...')` directly** rather than dbt
  `{{ source() }}` external sources — simpler, reliable, configurable via dbt vars.
  S3 credentials set in `profiles.yml` `settings:` via `env_var()`.
- **`ot_drug_molecules_raw` added as a 5th OT Bronze asset** (22,230 rows):
  `clinical_target` has drug IDs but not display names; `drug_molecule/` carries
  `id`, `name`, `drugType`.
- **OT v26.03 `maxClinicalStage` is a STRING** (`"APPROVAL"`, `"PHASE3"`, …), not a
  float. `stg_ot_drugs` stores it raw as `max_clinical_stage_raw`; `dim_drug` maps
  it to `SMALLINT` via `CASE`.
- **`fact_protein_tissue` uses INNER JOIN to `dim_protein`**: 38 HPA rows had
  accessions not present in reviewed Swiss-Prot UniProt (TrEMBL or unmapped
  entries). Inner join enforces referential integrity for the relationships test.
- **`therapeutic_area` in `dim_disease` is NULL for v1**: `therapeuticAreas` was
  removed in OT v26.03; only `parents` (hierarchical EFO IDs) remains, and deriving
  it needs a recursive self-join — deferred to v2.
- **`function_friendly`, `tagline`, `is_curated` left NULL/FALSE in `dim_protein`**
  at this stage — populated in Part 5 (LLM rewrites + manual curation).
- **MotherDuck `atlas` database created automatically** on first connect via
  `md:atlas?motherduck_token=...`; no manual provisioning step needed.

### Result

- 16 dbt models (8 staging views + 7 Gold tables + `protein_story_card` view),
  45 data tests (unique, not_null, relationships) — all green (`dbt run` 16/16,
  `dbt test` 45/45).
- `protein_story_card` verified as a fully-populated row for insulin (P01308) and
  a row with the expected nulls for a long-tail protein (Q9Y478).

| Table | Rows |
|---|---|
| `dim_protein` | 20,431 |
| `dim_disease` | 47,030 |
| `dim_drug` | 13,407 |
| `fact_protein_tissue` | 19,142 |
| `fact_interaction` | 472,588 |
| `fact_protein_disease` | ~1.2M (human proteins only) |
| `fact_drug_target_disease` | ~250k distinct triples |

### Next steps (Part 4)

- Modal function running ESM-2 `t33_650M` on A10G GPU.
- Dagster asset batching all ~20k sequences via Modal.
- UMAP projection over the full embedding matrix.
- Qdrant collection + `fact_embedding` table in MotherDuck.

---

## Part 4 — ESM-2 embeddings, UMAP projection, Qdrant indexing (complete)

### Decisions made

- **Modal `max_containers=5`**: `concurrency_limit` was renamed to `max_containers`
  in Modal ≥ 2025-02-24; always use the new name.
- **Batch size 128, fp16**: fits comfortably on an A10G (24 GB VRAM) with ESM-2
  650M. ~20k proteins ÷ 128/batch ≈ 160 Modal calls; with `max_containers=5`,
  ~32 serial rounds.
- **Mean-pool all non-padding tokens** (including `<cls>`/`<eos>`): simple and
  effective for cosine similarity and UMAP clustering.
- **Truncation at 1022 residues** (`MAX_SEQ_LEN`): ESM-2's context window is 1024
  tokens minus 2 for `<cls>`/`<eos>`. `was_truncated` records which proteins were
  clipped (2,302 of them).
- **UMAP runs in the Dagster process** (local CPU, not Modal): 20k × 1280 float32
  takes ~5–10 min on CPU, free, and outside the Modal cost budget.
  `n_neighbors=15, min_dist=0.1, metric=cosine, random_state=42`.
- **Direct MotherDuck write from Dagster** (not via dbt): embeddings are ML
  outputs, not SQL transforms of raw source data. Written via Arrow registration —
  `conn.register("embedding_df", df.to_arrow())` then
  `CREATE OR REPLACE TABLE fact_embedding AS SELECT * FROM embedding_df`. Parquet
  round-trip (polars → local tmp → `read_parquet`) avoided a `pyarrow` dependency
  and was ~300× faster than `executemany` at this scale.
- **Qdrant collection `proteins`**: deleted and recreated on each run for
  idempotency. Point IDs are stable: `sha256(accession)[:8]` shifted right by 1
  to guarantee a positive int64.
- **`qdrant-client` 1.18 API**: `query_points()` replaces `search()`,
  `points_count` replaces `vectors_count`.
- **`modal_esm2.py` excluded from pyright**: torch/transformers only exist in the
  Modal GPU container image, not locally — exclusion avoids ~40 spurious import
  errors. `torch>=2.4` pinned to match the `transformers` 5.9.0 requirement.
- **Python 3.13 + Dagster 1.13.7 annotation bug**: `from __future__ import
  annotations` (PEP 563) makes `inspect.signature()` return raw strings; Dagster's
  `_validate_context_type_hint` then compares a string against a class object and
  fails. Assets *with* resource parameters dodge this (resource binding calls
  `typing.get_type_hints()` first, resolving annotations as a side effect); assets
  with no resource parameters don't. **Fix**: `embeddings.py` omits the future
  import so annotations evaluate eagerly to real class objects.
- **Test helper functions public** (no `_` prefix) when tests import them
  directly — same pattern as the ingest modules' `parse_entry`/`build_dataframe`.

### Result

- All 20,431 reviewed human proteins embedded and written to `fact_embedding`
  (MotherDuck) and the Qdrant `proteins` collection.
- EGFR (P00533) nearest neighbours: ERBB2 (#1, cosine 0.977), ERBB3 (#2, 0.971) —
  confirms the model learned ErbB receptor family structure. ERBB4 absent from
  the top-15 is biologically expected (most divergent ErbB receptor); the sanity
  check asserts ERBB2 + ERBB3 in the top-10 rather than a stricter top-5/all-four.
- `fact_embedding` schema: `uniprot_accession` (PK), `embedding` (FLOAT[1280]),
  `umap_x`/`umap_y` (FLOAT), `model_version` (`"esm2_t33_650M"`), `was_truncated`
  (BOOLEAN), `computed_at` (TIMESTAMP).
- PR #6 merged into `main`.

### Next steps (Part 5)

- 100 hand-curated proteins (`is_curated = TRUE`) with editorial `tagline` +
  `function_friendly`.
- LLM batch rewrite pipeline (Claude Haiku via Anthropic Batch API) for the
  remaining ~20k proteins, two-tier COALESCE in `dim_protein`.

---

## Part 5 — Editorial seed + LLM rewrite pipeline (complete)

### Decisions made

- **Two-tier editorial pattern**: `dim_protein_editorial` dbt seed (CSV, 100
  hand-curated proteins) wins via `COALESCE` over `stg_llm_rewrites` (Claude Haiku
  batch output) wins over a final `'No information available'` placeholder. The
  user can't tell which proteins are hand-written vs. LLM-rewritten in the UI.
- **`generate_editorial_seed.py`** parses `docs/protein_atlas_curation_list.md`
  directly (Gene/Tagline/Function_friendly sections) and resolves gene symbols to
  `uniprot_accession` — keeping the join key canonical per CLAUDE.md rule 1.
- **Anthropic Messages Batch API (Claude Haiku) for ~20k rewrites**: async batch
  submission, 60s polling (`_poll_until_done`), streamed result collection
  (`_collect_results`). Batch IDs persisted to a `batch_checkpoint.json` in R2
  *before* polling — results stay retrievable from Anthropic for a 29-day
  retention window even if the later Parquet write fails.
- **Skip proteins with no `function_raw` before submission**: pre-seeds their
  result as `(None, None)` so they flow straight to the placeholder fallback.
  Saved ~3,172 API calls (~$1.50, ~15% of batch quota).
- **`R2Resource.exists()` / `.write_json()` added**: idempotency guard (skip
  re-submission if the R2 output Parquet already exists) and checkpoint writes.
- **`SYSTEM_PROMPT` rule against double quotes inside JSON string values**
  (added after the bug below): instructs the model to use single quotes for
  emphasis/naming instead — unescaped `"` inside `function_friendly`/`tagline`
  text breaks `json.loads()`.
- **`dim_protein.sql` em-dash fix**: `dbt-duckdb` CSV seed loading introduces
  U+FFFD replacement characters where the editorial seed has em-dashes; the
  editorial CTE now `REPLACE`s U+FFFD (`chr(65533)`) with proper U+2014
  (`chr(8212)`) so the UI renders correctly.
- **`dim_protein.function_friendly` 3-level COALESCE fallback**: editorial →
  LLM rewrite → `function_raw` verbatim → `'No information available'`. The
  `function_raw` rung was added after discovering 87 proteins had substantive
  source text but a placeholder `function_friendly` (see "Bug discovered" below).

### Bug discovered & fixed: 87 proteins with placeholder `function_friendly` despite real `function_raw`

Diagnosed by re-fetching original batch results from the Anthropic API (within
the 29-day retention window) and classifying with `parse_rewrite` + a regex for
unescaped quotes. Two distinct root causes:

1. **Bucket 1 (~41 proteins)**: the LLM correctly judged `function_raw` too terse
   to rewrite (e.g. "Orphan receptor") and legitimately returned
   `{"function_friendly": null, ...}`. **Fix**: `dim_protein` COALESCE now falls
   back to `function_raw` verbatim for these — more informative than a placeholder.
2. **Bucket 2 (48 of an estimated 51 proteins)**: the LLM generated good,
   substantive rewrites but used unescaped "scare quotes"
   (e.g. `the "on" position`, `"leak mode"`) that broke `json.loads()`, silently
   discarding good content as `(None, None)`. **Fix**: added `SYSTEM_PROMPT` rule 5
   (single quotes only) and re-ran *only* those ~51 proteins via a one-shot script
   (`notebooks/fix_bucket2_rewrites.py`) that re-derives the affected accession
   list dynamically (cross-referencing the LLM rewrites Parquet's
   `function_friendly IS NULL` rows against `dim_protein.function_raw`), submits
   one small corrected batch, and patches just those rows into the existing
   Parquet — preserving all ~20k already-good rewrites untouched.
   **Gotcha**: the candidate query *must* read from the LLM rewrites Parquet, not
   `dim_protein` — once the Bucket 1 fallback landed, `dim_protein.function_friendly`
   no longer shows the placeholder for *either* bucket (both fall through to
   `function_raw`), masking the very rows needed to find Bucket 2.

### Bug discovered & fixed: STRING ENSP→UniProt resolution (10.95% → 99.78% accuracy)

`resolve_string_ids()` originally took the *first* alias row per ENSP regardless
of its source tag — wrong far more often than right (only 10.95% accuracy against
`dim_protein.string_protein_id`, the 18,842-pair ground truth from UniProt's own
xrefs). Replaced with a 4-tier fallback (`_pick_canonical_accession`) that groups
alias rows by ENSP, filters to the three sources that actually carry UniProt
mappings (`Ensembl_HGNC_uniprot_ids`, `Ensembl_UniProt`, `UniProt_AC`), and
prefers, in order: (1) an `Ensembl_HGNC_uniprot_ids` candidate corroborated by the
`Ensembl_UniProt ∩ UniProt_AC` intersection, else the first HGNC row; (2) a
singleton intersection; (3) the first `UniProt_AC`; (4) the first `Ensembl_UniProt`.
Validated at **99.78%** (18,800/18,842) — user explicitly decided the remaining
0.2% (sequence-alignment-level disambiguation) isn't worth chasing. Rebuilding
`string_interactions_raw` → `stg_string` → `fact_interaction` with the fixed
resolver corrected the previously-wrong partner lists (e.g. insulin now correctly
shows INSR/IGF1/SLC2A4 per the data source manifest).

### `fact_interaction` coverage gap (15,879 / 20,431 proteins) — kept as-is

Only proteins with at least one STRING interaction scoring ≥ `SCORE_THRESHOLD = 700`
appear in `fact_interaction`; ~4,500 proteins have no row. **User explicitly chose
to keep the 700 threshold** ("i do not want to include noisy data") rather than
lower it to capture more proteins with noisier low-confidence edges. The frontend
(Part 6) will need a designed empty-state message for these proteins — user
approved wording along the lines of "No high-confidence partners known. STRING-DB
hasn't…" rather than a blank field (which could read as a bug).

### Result

- `dim_protein_editorial` seed: 100 rows, `is_curated = TRUE`.
- `protein_llm_rewrites` → `r2://atlas-raw/llm/v2026_06/protein_rewrites.parquet`:
  20,431 rows (3,172 skipped/no-`function_raw`, ~17k genuine rewrites, 100
  superseded by editorial via COALESCE).
- Spot-check: 20/20 sampled LLM rewrites rated 4–5/5 for accuracy and tone.
- Final placeholder count: **0** proteins with real `function_raw` but
  `function_friendly = 'No information available'`; 41 legitimately fall back to
  `function_raw` verbatim (Bucket 1).
- `string_interactions_raw` rebuilt: 473,618 raw rows; `fact_interaction`:
  472,588 high-confidence (≥700) edges across 15,879 proteins.
- README/manifest docs updated to close out Part 5.

### Next steps (Part 6)

- Data viz / frontend (Streamlit UI): protein story cards, embedding map (UMAP
  scatter + Qdrant similarity search), interaction graphs with the designed
  empty-state for the ~4,500 proteins with no high-confidence STRING partners.
- `notebooks/fix_bucket2_rewrites.py` is a one-shot patch script (its own
  docstring says "run once, then delete") — user chose to keep it for now as an
  audit trail of how the 48 proteins were re-derived and patched.

## dbt quality-gate hardening — P0 bug fixes + singular tests (2026-06-07, complete)

A senior-level review of the dbt test suite (52 generic tests, 0 singular SQL
tests) surfaced three P0/P1 correctness bugs that all 52 green tests had missed
because they only checked nullability/uniqueness/relationships — never values.
**User explicitly chose "fix P0 bugs first, then add tests"** ("tests on broken
data would just be red noise") and **"no `dbt_utils` — custom singular SQL only"**
(staying dependency-free per CLAUDE.md rule 6).

### Bugs found & fixed

1. **`fact_drug_target_disease.efo_id` STRUCT bug**: `UNNEST(disease_ids) AS efo_id`
   extracted the whole `STRUCT(diseaseFromSource, diseaseId)` as a string
   representation, not the joinable ID — silently breaking every "drugs by
   disease" query and the `relationships → dim_disease` test (which "passed" only
   because dbt's relationship test tolerates NULLs, and the malformed values never
   matched so the test never fired on real data). Fixed: `UNNEST(disease_ids).diseaseId`.
   Live verification: **100% join rate** (110,040/110,040 non-null values now
   resolve to `dim_disease.efo_id`; the struct-string version joined at ~60%).
2. **`fact_interaction` self-loops + duplicate pairs**: STRING reports interactions
   symmetrically (A↔B and B↔A), and paralog families (histones, HLA, …) collapse
   many Ensembl/STRING gene-level entries onto one UniProt accession — together
   producing "X interacts with X" rows and the same biological pair counted 2-14×.
   Fixed by canonicalizing to `LEAST/GREATEST` ordered pairs, dropping
   `uniprot_a = uniprot_b`, and `MAX(combined_score) GROUP BY 1,2` (strongest
   evidence across paralog copies — not inventing or arbitrarily picking one).
   Result: 466,248 raw rows → **226,539 clean unordered pairs, 0 self-loops, 0
   duplicates**. This incidentally fixes a latent bug in `protein_story_card`'s
   `top_interaction_partners` (which matched `uniprot_a OR uniprot_b` — symmetric
   dupes would have listed the same partner twice); spot-checked insulin (P01308):
   305 interaction rows, 0 duplicate partners, top-5 = IGF1R/INSR/IRS1/IRS2/PIK3R1.
3. **`fact_protein_disease` duplicate (protein, disease) pairs**: same paralog
   root cause as #2 — ~70 UniProt accessions are the canonical target of multiple
   Ensembl genes, so each carries its own OT association score for the same
   disease. Fixed with the same `MAX(overall_score) GROUP BY 1,2` pattern.
   Result: 4,346,458 rows, **0 duplicate pairs**. (Later reduced to 697,330 rows
   by the `overall_score >= 0.1` floor — see the score-floor section below.)
4. **`dim_drug.max_phase` degenerate mapping (P1)**: the `CASE` matched
   `'PHASE3'` etc. but OT v26.03's raw values use underscores (`'PHASE_3'`), and a
   silent `ELSE TRY_CAST(...)` swallowed every mismatch to `NULL` or `4` — yielding
   `min=max=4`, 48% NULL. **User chose "conservative floor to lower phase"** for
   combined-phase categories so `max_phase >= 3` only counts drugs that
   *definitively* cleared phase 3 (the UI claims "approved/late-stage drugs"):
   `PHASE_2_3→2, PHASE_1_2→1, EARLY_PHASE_1/IND/PREAPPROVAL/PRECLINICAL→0,
   UNKNOWN→NULL`, **no `ELSE`** — an unrecognized category now becomes NULL *and*
   fails the new `accepted_values` test on `stg_ot_drugs.max_clinical_stage_raw`
   loudly (CLAUDE.md: never silently coerce). Result: real distribution
   `{0:15, 1:432, 2:1128, 3:618, 4:2431, NULL:80}`.

All four fixes share one throughline worth remembering: **paralog/grain
mismatch** (many Ensembl/STRING gene-level rows → one UniProt protein-level
identity) is the root cause behind two of the three P0s — not two unrelated bugs.

### Tests added

- New `accepted_values` test on `stg_ot_drugs.max_clinical_stage_raw` (enumerates
  all 11 known OT clinical-stage categories) — written with arguments nested under
  `arguments:` from the start to avoid the dbt 1.11
  `MissingArgumentsPropertyInGenericTestDeprecation` warning.
- New `relationships` test: `fact_drug_target_disease.efo_id → dim_disease.efo_id`
  (only meaningful post struct-fix; passes at the verified 100% join rate).
- Six singular SQL tests in the new `models/tests/` directory (dependency-free,
  per user's explicit "no dbt_utils" choice — `test-paths: ["tests"]` was already
  configured but the directory didn't exist):
  `assert_fact_interaction_no_self_loops`,
  `assert_fact_interaction_canonical_unique_pairs` (mis-ordering + duplicates in
  one test), `assert_fact_protein_disease_unique_pairs`,
  `assert_fact_interaction_combined_score_range` ([700, 1000]),
  `assert_fact_protein_disease_overall_score_range` (later tightened [0, 1] →
  [0.1, 1] when the score floor was added — see the score-floor section below),
  `assert_dim_drug_max_phase_range` ([0, 4] or NULL).

### Result

- `dbt run --select dim_drug fact_drug_target_disease fact_interaction
  fact_protein_disease`: PASS=4, ERROR=0.
- `dbt test`: **59/59 PASS, 0 WARN, 0 ERROR** (52 original + 1 new generic +
  6 new singular — all green on the *first* run, confirming the
  fix-then-test sequencing avoided red noise as the user predicted).
- `ruff check` and `pyright` both clean.
- Considered and **rejected** a `stg_ot_targets.uniprot_accession`-extraction
  schema-drift canary test — would have required querying a flaky S3-backed
  staging view ([[project-part3-dbt]] documents the intermittent `HTTP 404` on
  `read_parquet('s3://...')`) just to validate a speculative test outside the
  agreed P0-fix scope. Skipped per "implement the simplest solution, avoid
  speculative future-proofing."

### Docs updated (same day)

Per CLAUDE.md's "Schema change → update manifest" trigger, added grain notes to
`docs/protein_atlas_data_source_manifest.md`: STRING "How it joins"
(symmetric-pair + paralog canonicalization → `LEAST/GREATEST/MAX`), Open
Targets "How it joins" (paralog dedup via `MAX(overall_score)`, plus the
`disease_ids` STRUCT→`.diseaseId` extraction), and grain comments on the
`fact_interaction`/`fact_protein_disease`/`fact_drug_target_disease` table defs
in the reference warehouse-schema block. **`ARCHITECTURE.md` does not exist yet**
— it's a Part 8 deliverable per `ROADMAP.md:251`/`README.md:186` ("written in
Part 8"), so nothing to update there.

### P2 guardrail follow-up (same day, complete)

User asked "what about the P2 fixes?" — the original review's "🟡 P2 — missing
guardrails" list had only been *incidentally* covered (as a byproduct of writing
tests that locked in the P0 fixes), not addressed as its own item. Went back
through the full P2 list and closed the actionable remainder:

- `not_null` on `dim_protein.function_friendly`/`tagline` — pins the COALESCE
  invariant (editorial > LLM > raw > `'No information available'`, never NULL)
  so a future COALESCE edit can't silently reintroduce nulls into the UI. Also
  fixed the column descriptions in `marts/_schema.yml`, which were stale
  ("NULL only if function_raw was absent…" — no longer true post-Part-5 fix).
- `assert_dim_protein_sequence_length_consistent` — `sequence_length > 0 AND
  sequence_length = length(sequence)` (verified clean: range 2–34,350, 0 mismatches).
- `assert_fact_drug_target_disease_unique_triples` — pins the
  `(chembl_id, uniprot_accession, efo_id)` grain the model's `SELECT DISTINCT`
  relies on implicitly (verified: 119,762 rows, 0 duplicate triples).
- `unique` on `fact_protein_tissue.uniprot_accession` — turned out to be a
  **plain single-column grain gap**, not a composite one: this table is an HPA
  *summary* (one row per protein, `tissue`/`expression_level` carry category
  strings, not per-tissue detail rows) — the schema's own description said "one
  row per protein" but nothing enforced it.
- `assert_dim_protein_volume_sane` — floor of 15,000 rows (current: 20,431,
  grows by low hundreds per UniProt release). Guards against the exact failure
  mode that stayed green through the STRING resolver bug this session:
  "wrong-but-present" data from a broken ingest. Floor is deliberately generous
  to tolerate years of organic drift while catching a collapse.

**Still deliberately out of scope** (named in the original review, judged not
worth doing):
- `stg_ot_targets` extraction schema-drift canary — would require querying the
  flaky S3-backed staging view just to validate a speculative test; needs a real
  design decision (what's an acceptable extraction-rate floor?) the user hasn't
  made.
- MotherDuck S3/R2 staging-view flakiness (`HTTP 404` on existing files) — an
  infrastructure fix, not a test; revisit if it starts blocking `dbt run`/CI
  rather than just ad-hoc diagnostic queries.

### Result

`dbt test`: **65/65 PASS, 0 WARN, 0 ERROR** (59 prior + 6 new — `not_null` ×2,
3 singular grain/consistency tests, 1 volume guard). `ruff`/`pyright` clean.

### Next steps

- ~~The remaining open items above (staging flakiness, OT extraction canary)~~ —
  staging flakiness root-caused and fixed same day, see below. OT extraction
  canary still open; revisit only if it starts actively blocking work.
- Part 6: Streamlit UI work (unchanged from the Part 5 next-steps note above).

## MotherDuck S3/R2 staging-view 404s — root cause + fix (2026-06-07, complete)

### Root cause

The "flaky `HTTP 404` on existing files" noted above wasn't flaky — it was
**session-scoped**. `models/profiles.yml` configured R2 access via dbt-duckdb
`settings:` (`s3_endpoint`, `s3_access_key_id`, …, templated with `env_var()`).
Those become ephemeral `SET s3_*` statements applied only to dbt's *local*
DuckDB session — confirmed via `SELECT * FROM duckdb_secrets()` returning empty
on the live connection. Any other session (MotherDuck web UI, a fresh `duckdb`
connection, CI) re-executes the staging views' `read_parquet('s3://atlas-raw/…')`
with no R2 credentials, falls back to MotherDuck's default AWS-S3 resolution
(`region eu-central-1`), and 404s — `atlas-raw` is a Cloudflare R2 bucket, not
an AWS S3 bucket. `dbt run`/`dbt test` always passed because they ran inside the
one session that had the right settings; only ad-hoc UI queries failed.

### Fix

1. **`notebooks/setup_motherduck_r2_secret.py`** (one-shot, run-once-then-keep —
   it's idempotent via `CREATE OR REPLACE`, unlike `fix_bucket2_rewrites.py`
   which was truly single-use): registers a **persistent** secret server-side —
   `CREATE OR REPLACE SECRET atlas_r2 IN MOTHERDUCK (TYPE R2, KEY_ID …, SECRET …,
   ACCOUNT_ID …, REGION 'auto')`. `storage='motherduck'`, `persistent=True` —
   available to *any* session, confirmed via fresh bare connections with zero
   local `SET` statements.
   - **MotherDuck/DuckDB docs say R2 is regionless and `REGION` can be omitted —
     that's wrong in practice.** Without it, the cloud engine defaults to
     `eu-central-1` (its own account region), which R2 rejects outright
     (`InvalidRegionName … Must be one of: wnam, enam, weur, eeur, apac, oc,
     auto` → HTTP 400). Pinning `REGION 'auto'` fixed it. Worth remembering if
     any other R2 secret gets created later.
2. **All 9 `models/staging/stg_*.sql`**: source paths switched from
   `s3://{{ var("r2_bucket") }}/…` to `r2://{{ var("r2_bucket") }}/…` so they
   route through the `atlas_r2` secret (scoped to `r2://` only).
3. **`models/profiles.yml`**: removed the now-redundant `settings:` block
   entirely (6 lines) — the persistent secret is the single source of truth for
   both local dbt runs and MotherDuck UI/cloud sessions. Re-ran `dbt run` (17/17)
   + `dbt test` (65/65) with zero local `s3_*` settings present — all green.

### Verification

Queried all 9 staging views from **fresh, bare `duckdb.connect()` sessions**
(no local `SET` statements at all — the exact MotherDuck-UI scenario that was
failing): all 9 resolved correctly (`stg_hpa` 19,179 rows … `stg_ot_associations`
4,508,002 rows … `stg_uniprot` 20,431 rows).

### Result

Staging-view 404s eliminated for *all* sessions, not just dbt's. `dbt run`
17/17, `dbt test` 65/65, `ruff`/`pyright` clean on the new script.

---

## fact_protein_disease score floor `>= 0.1` (2026-06-07, complete)

### Decision

Added `HAVING MAX(a.overall_score) >= 0.1` to `models/marts/fact_protein_disease.sql`.
Surfaced during a biological review of `atlas.main`: the table was 4.3M rows but
its OT association `overall_score` is a *weight-of-evidence* measure (not a
probability/effect size) with a brutally right-skewed distribution — median
~0.02, mean ~0.06 — because Open Targets surfaces every faint signal (single
text-mining co-mentions, lone underpowered GWAS hits). Unfiltered, a naive
`COUNT` reads EGFR as "associated with ~2,600 diseases", and **54% of proteins
had an association but not one reaching 0.5**. The score is right-skewed because
a single evidence channel tops out around ~0.5; exceeding that needs multiple
independent channels agreeing, so ~0.1 cleanly cuts the pure-noise tail.

### Impact (measured before applying)

4,346,458 → **697,330 rows (16.0% kept; ~3.65M trace rows dropped)**. Only
**992 of 19,215 proteins (5%) lose ALL associations** — 95% keep ≥1 real link.
Diseases referenced 26,235 → 21,717.

### Why lossy-in-the-mart, not document-only

User chose the mart floor over keep-raw-+-caveat. Accepted tradeoff: a future
score `< 0.1` cannot be recovered without a rebuild. Justified because nothing
below 0.1 is ever actionable for ranking/display, and **the UI shows only the
top few associations per protein by score** — so no usable data is lost. (The
companion UI decision: story card ranks by `overall_score DESC` and shows the
top few; the protein_story_card query already does `ORDER BY overall_score DESC
LIMIT 5`.)

### Test (TDD)

Tightened `assert_fact_protein_disease_overall_score_range` from `[0, 1]` to
`[0.1, 1]` — ran red first (3,649,128 violating rows = 4.35M − 0.70M, confirming
the floor wasn't yet applied), then added the `HAVING` and reran green. The test
now enforces the floor as a contract: any row `< 0.1` means the filter was
removed/bypassed. `dbt test --select fact_protein_disease`: **7/7 PASS** (floor +
unique-pairs grain + not_null ×2 + relationships ×2).

### Docs updated (same commit)

`models/marts/_schema.yml` (description), `docs/protein_atlas_data_source_manifest.md`
(grain note + DDL comment + the "ot_associations_raw is large" gotcha).

---

## Ligand → receptor → drug routing (UI design rule, 2026-06-07)

### Decision

Drugs stay on the protein they act *on* (the molecular target); a ligand's card
is **not** given a synthesized drug list. The card surfaces the receptor as a
clickable interaction partner so the reader navigates **ligand → receptor →
drugs**. Written into `ROADMAP.md` Part 6 as a "do not deviate" design rule
(deliverable + task 3 + exit criterion). Chosen by the user over two rejected
alternatives.

### Why (verified against live data, insulin P01308)

Insulin the gene has **0 drugs** in `fact_drug_target_disease`; its analogs
(Glargine, Degludec, …) attach to the receptor **INSR (P06213, 16 approved)**.
Two tempting shortcuts both fail:

- **Link drugs via top interaction partners** (the user's first idea): insulin's
  top-8 STRING partners are tied at ~999, and they include both INSR (25 drugs,
  the *right* insulin analogs) **and IGF1R (P08069, 20 drugs — MECASERMIN,
  TEPROTUMUMAB: thyroid-eye-disease / growth drugs, *wrong* for insulin)**. Same
  score, same `protein_class` ("Enzymes, FDA approved drug targets") — **no
  signal to keep INSR and drop IGF1R.** STRING is undirected/untyped (fuses
  binding with pathway co-membership), so it cannot identify the *cognate*
  receptor.
- **Link drugs via top diseases**: insulin's highest-scored diseases (neonatal
  diabetes, MODY, hyperproinsulinemia) have **0 drugs**; type 1 diabetes has
  **136 drugs across 197 targets** — the whole disease pharmacopeia, not
  insulin's drugs.

### Rejected: a real ligand→receptor table

A correct, general "drugs for this ligand" feature would need a curated, *typed*
ligand→receptor source (IUPHAR/BPS Guide to PHARMACOLOGY, OmniPath, …) =
new data source (CLAUDE.md rule 6) + scope. Parked as a possible v2 thread; for
v1 the navigation approach is correct and free, and respects rule 5 (no invented
data).

### Docs updated (same commit)

`ROADMAP.md` (Part 6 design rule + task + exit criterion),
`docs/protein_atlas_data_source_manifest.md` (corrected the wrong "insulin →
Humulin/Lispro/Glargine" worked-example + "Drugs that work with it" framing —
drugs map to INSR, not INS), `README.md` (features: clickable cross-references).

---

## protein_story_card: VARCHAR lists → LIST(STRUCT) (2026-06-07, complete)

### Decision

Reshaped `top_interaction_partners`, `top_diseases`, and `approved_drugs` in
`models/queries/protein_story_card.sql` from `LIST(VARCHAR)` of baked
`"name (score)"` display strings to `LIST(STRUCT(...))` with typed fields
(`accession`/`efo_id`/`chembl_id`, a human-readable name, and the numeric
score/phase). DuckDB serializes struct lists to JSON arrays of objects, so
FastAPI/Streamlit get parsed fields directly — no string-splitting in the UI
layer.

### Why

User asked, ahead of Part 6, whether the `LIST(VARCHAR)` shape from Part 3
("`P06213 (0.650)`", `"name (score)"`) was fit for Streamlit. Two concrete
problems found by checking live data:

1. **8,119 of `dim_disease`'s rows have parentheses in `disease_name` itself**
   (e.g. `"peroxisome biogenesis disorder 1A (Zellweger)"` →
   `"... (Zellweger) (0.234)"`). A naive `split('(')` to recover the score
   breaks; only a regex anchored on the numeric tail survives, and that's
   string-surgery for data that started out structured in SQL.
2. **`top_interaction_partners` carried only the bare accession** (`P06213`),
   not a display name — the opposite of what the new ligand→receptor→drug
   design rule needs: a clickable link (accession) *and* a friendly label
   (`gene_symbol`/`protein_name`) in the same row, without a second lookup.

While in there, also capped `approved_drugs` at top-5 by `max_phase DESC`
(previously unbounded `LIST(DISTINCT drug_name)`): checked live data and found
346 proteins have >5 phase>=3 drugs (DRD2/`P14416` has 97). The Part 6 design
rule already promises "top few by clinical phase" — the cap now lives in the
query, not left for the UI to truncate.

### How to apply

Final shapes (verified against insulin P01308 and EGFR P00533):
- `top_interaction_partners`: `{accession, gene_symbol, protein_name, combined_score}`
- `top_diseases`: `{efo_id, disease_name, overall_score}`
- `approved_drugs`: `{chembl_id, drug_name, max_phase}`, top 5 by phase

Insulin's `approved_drugs` is `NULL` (LIST aggregate over zero rows) — same as
before; the UI must treat `NULL`/empty the same way (`for x in (data or [])`).
Rebuilt (`dbt run --select protein_story_card`) and spot-checked live; no
schema/test file exists for this query (it's a parametrized `models/queries/`
view, not a mart — `_schema.yml` doesn't cover it).

---

## Part 6 — dropped the API tier; Streamlit talks to MotherDuck + Qdrant directly (2026-06-13)

### Decision

The original Part 6 plan put a FastAPI service on Modal between Streamlit and
the data stores. That tier was never built. Instead `apps/ui/app.py` calls
`apps/ui/data.py`, which opens a MotherDuck (DuckDB) connection and a Qdrant
client directly — no intermediate HTTP service. This is now the confirmed,
permanent v1 architecture, not a stopgap. `README.md` (intro prose, tech-stack
table, architecture diagram, project structure), `ROADMAP.md` (Part 6 title,
deliverables, tasks), `CLAUDE.md` (tech-stack list, repo layout, "Async FastAPI
handlers" convention), and `SETUP.md` (Modal/Qdrant setup notes) were updated to
remove FastAPI/Modal-API references.

### Why

The API tier was speculative: nothing other than this Streamlit app would ever
call it. For a single-tenant analytical dashboard, a separate FastAPI service on
Modal adds a deployment unit, a network hop, and a second place for the
story-card query logic to drift from `models/queries/protein_story_card.sql` —
with no consumer to justify the indirection.

### How to apply

- `apps/ui/data.py` is the canonical data-access layer for the UI: typed
  functions over a MotherDuck connection (`fetch_story_card`, `search_proteins`,
  `list_proteins`, `fetch_atlas`, `fetch_sequence_lengths`, ...) and a Qdrant
  client (`find_neighbors`). New UI data needs go here, not into a new API
  endpoint.
- `models/queries/protein_story_card.sql` stays the canonical spec for the
  story-card shape (per the LIST(STRUCT) entry above); `data.py` mirrors it.
- If a second consumer (a public API, a mobile client) ever needs this data,
  that's the trigger to revisit an API tier — not before.
- FastAPI/pydantic/uvicorn were never added to `pyproject.toml`; `httpx` stays
  (used by the ingest pipelines in `pipelines/atlas/assets/ingest/`, unrelated
  to this dropped tier).

---

## Part 6 — deployed to Streamlit Community Cloud (2026-06-13, complete)

### Decision

Deployed from the `feat/part6-ui` branch to Streamlit Community Cloud:
https://human-protein-atlas-wuvzvj7dohsidbm4lgndwc.streamlit.app/. Added
`apps/ui/requirements.txt` — a minimal pip list (`streamlit`, `plotly`,
`duckdb==1.5.2`, `qdrant-client`) — because Streamlit Cloud prefers a
requirements file next to the main script over the repo-root `pyproject.toml`,
which lists the whole project's dependencies (dagster, modal, umap-learn,
dbt-duckdb, ...) that the UI never imports. `pyproject.toml`/`uv.lock` remain
the source of truth for local dev.

### Why

Free-tier Cloud builds have limited time/memory; installing the full project
dependency set for a dashboard that only needs four packages risked slow or
failing builds. `MOTHERDUCK_TOKEN`, `QDRANT_URL`, `QDRANT_API_KEY` are set in
the Cloud app's Secrets UI (TOML), matching `apps/ui/.streamlit/secrets.toml.example`.

### How to apply

All ROADMAP Part 6 exit criteria verified live: incognito load works; searching
"insulin" highlights INS and renders its card; insulin shows no direct drugs
while INSR (its receptor) is a working link listing the insulin analogs;
clicking a neighbor updates the card without a page reload. Part 6 is complete
— `README.md` Status section and Part 6 checkbox updated, live-demo link added
to the hero and links sections.

If `apps/ui/requirements.txt` ever drifts from `pyproject.toml`'s version
constraints for `streamlit`/`plotly`/`duckdb`/`qdrant-client`, update both.

---

## Part 6 — Streamlit Cloud app recreated against `main`; new live URL (2026-06-13)

### Decision

Streamlit Community Cloud has no UI to repoint an existing app's source branch
(Settings only exposes "App URL" and "Python version"). The original app
(tracking `feat/part6-ui`, URL ending `wuvzvj7dohsidbm4lgndwc`) was deleted and
recreated pointed at `main`. New live URL:
https://human-protein-atlas-cqhrelt2uatfzhyt54udys.streamlit.app/. `README.md`
hero and links sections updated to the new URL; the old URL is dead.

### Why

`feat/part6-ui` was merged to `main` (PR #9, `a3e1f05`) and the user wants the
deployed app to track `main` going forward so future merges deploy automatically
without per-PR Cloud reconfiguration.

### How to apply

If the live demo link 404s in the future, check whether the app was recreated
again (new random URL) before assuming a code issue — `README.md` is the
source of truth for the current URL. Secrets (`MOTHERDUCK_TOKEN`, `QDRANT_URL`,
`QDRANT_API_KEY`) had to be re-entered in the new app's Secrets UI since they
don't carry over on recreation.
