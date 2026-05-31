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
