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

### Next steps

- Push branch + open PR so CI runs on a PR (checkpoint for Part 1).
- Part 2: STRING (ENSP→UniProt mapping is the #1 gotcha — write/test
  `resolve_string_ids` first), HPA, Open Targets (4 datasets). Reuse the R2
  resource; one ingest module per source under `pipelines/atlas/assets/ingest/`.
