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

## Part 2 — Remaining data sources (code complete; materialization pending)

### Decisions made

- **Open Targets version pinned to `26.03`** (March 2026 quarterly release).
  FTP base: `https://ftp.ebi.ac.uk/pub/databases/opentargets/platform/26.03/output/etl/parquet/`.
- **4 separate Dagster assets for Open Targets** (`ot_targets_raw`, `ot_diseases_raw`,
  `ot_associations_raw`, `ot_drugs_raw`) rather than one multi-output asset.
  Chosen for idempotency and surgical reruns per the discussion at session start.
- **STRING links file streamed, not temp-filed.** `_IterBytesIO` adapts
  `httpx.iter_bytes()` to `io.RawIOBase` so `gzip.open(io.BufferedReader(...))` can
  decompress line-by-line without loading the 1.6 GB uncompressed payload into memory.
  One `# pyright: ignore[reportArgumentType]` suppression at the `BufferedReader` call
  (RawIOBase subclass parameter narrowing — irreducible stub gap).
- **`Accept-Encoding: identity` header on STRING downloads** to prevent httpx from
  auto-decompressing the `.gz` file at the transport layer before our `gzip.open` sees it.
- **HPA version hardcoded to `v24`** (no machine-readable version in the download URL).
  Bump to `v25` when HPA ships the next annual release.
- **Test helpers are public, not private**, when tests need to import them directly
  (pyright strict `reportPrivateUsage` would fail otherwise). Pattern established:
  functions that need direct unit tests are named without a leading underscore;
  pure implementation helpers tested only indirectly keep the underscore.
- **`.unique()` and `.cast()` in hpa.py, `.select()` in opentargets.py** each need
  one `# pyright: ignore` for polars stub gaps (same pattern as Part 1).

### Files added

- `pipelines/atlas/assets/ingest/string.py` — STRING interactions asset
- `pipelines/atlas/assets/ingest/hpa.py` — HPA proteome asset
- `pipelines/atlas/assets/ingest/opentargets.py` — 4 OT assets
- `pipelines/atlas/tests/test_string.py` — 8 tests (pure function + mocked HTTP)
- `pipelines/atlas/tests/test_hpa.py` — 7 tests (parse + dedup + null handling)
- `pipelines/atlas/tests/test_opentargets.py` — 7 tests (directory listing + concat)

### Status

- ruff, pyright (strict, 0 errors), pytest (28 passed) all green locally.
- **Assets not yet materialized** — run `dagster asset materialize` for each source
  and verify row counts against the manifest before marking Part 2 complete.

### Next steps

- Materialize all 5 ingest assets; verify row counts against the manifest.
- Check Dagster UI for dependency edges between the 5 assets.
- If counts look correct, open PR and confirm CI is green (Part 2 checkpoint).
- Part 3: `dbt init` with `dbt-duckdb`, pointing at MotherDuck. Sources, staging,
  seven mart tables. Story-card SQL for insulin (P01308) as the exit criterion.
