# CLAUDE.md — Protein Atlas

A living atlas of every human protein — what it does, who it talks to, what goes wrong when it breaks — navigated by ESM-2 embeddings and joined across UniProt, STRING, HPA, Open Targets, and (v2) ChEMBL. A 12-year-old should grasp it in 90 seconds; a drug-discovery engineer should respect the data work.

## Hard rules

1. **Join key**: every cross-database join uses `uniprot_accession`. Never gene symbol.
2. **No secrets in code**: env vars or `.env.local` only.
3. **No CSV in pipelines**: Parquet, warehouse, or formal clients. CSV is debug-only.
4. **Idempotent assets**: every Dagster asset is safe to rerun.
5. **No invented data**: missing field → `NULL`. Never synthesize.
6. **No new dependencies without asking**: stack is fixed below.
7. **Tests for every asset**: one fixture-based correctness test, next to the code. Tests check values, not just "runs without error."
8. **`ruff`, `pyright`, `pytest` all pass before merge.** No `print` in production paths; use `from atlas.logging import logger`.
9. **Dynamic documentation** Update the files from the 'docs' folder after major milestones or major additions/modifications to the project

## Tech stack (fixed — ask before deviating)

Python 3.11+ · `uv` · `ruff` · `pyright` strict · `pytest` · OpenTofu 1.8+ · Dagster OSS · Cloudflare R2 · Parquet · MotherDuck · dbt-core + dbt-duckdb · Modal · ESM-2 `t33_650M` · Qdrant Cloud · Streamlit · `polars` (not pandas) · Claude Haiku for batch rewrites.

## Repo layout

```
CLAUDE.md  README.md  ARCHITECTURE.md  ROADMAP.md  SETUP.md  LICENSE  pyproject.toml  .env.example
docs/        curation list, data-source manifest
infra/       OpenTofu modules
pipelines/   Dagster project; package `atlas/` (assets/ingest, assets/transform, assets/ml, resources, tests)
models/      dbt project (sources, staging, marts)
apps/ui/     Streamlit on Community Cloud; queries MotherDuck + Qdrant directly (no API tier)
notebooks/   exploratory; never imported elsewhere
```

One module per data source under `pipelines/atlas/assets/ingest/` (`uniprot.py`, `string.py`, `hpa.py`, `opentargets.py`). The package is imported as `atlas` (e.g. `from atlas.logging import logger`).

## Conventions

- Type hints on every signature.
- Docstrings only where logic is non-obvious.
- `pathlib.Path` over `os.path`.
- Every Dagster asset has a docstring naming (1) what it produces, (2) its dependencies, (3) where the output lands.

## Two-tier editorial pattern

All ~20,000 reviewed human proteins are in the atlas. The top 100 (`is_curated = TRUE`) have hand-written `tagline` + `function_friendly`; the rest get LLM-rewritten versions over the same UI. The user can't tell which is which.

## Where to find answers

| Question | File |
|---|---|
| What does this column mean? | `docs/protein_atlas_data_source_manifest.md` |
| Which proteins are hand-curated? | `docs/protein_atlas_curation_list.md` |
| What's the current task? | `ROADMAP.md` |
| Why was this designed this way? | `ARCHITECTURE.md` |

## Documentation maintenance

Project documentation is kept in sync with code. Any commit whose change matches a trigger below must include an update to the listed file(s) in the same commit. A PR that violates this rule does not pass review.

| Change trigger | Files to update |
|---|---|
| Tech stack change (add or remove a dependency, service, or version) | `README.md`, `CLAUDE.md`, `ARCHITECTURE.md` |
| Schema change (new table, new column, changed join key) | `docs/protein_atlas_data_source_manifest.md`, `ARCHITECTURE.md` |
| Scope change (feature added or removed) | `README.md`, `ROADMAP.md` |
| Status milestone (a Part is completed) | `README.md` (Status section, checkbox) |
| New data source added | `docs/protein_atlas_data_source_manifest.md`, `README.md`, `ARCHITECTURE.md` |
| Architecture change (component added, removed, or repurposed) | `README.md`, `ARCHITECTURE.md` |
| License change | `README.md`, every file with attribution |
| Repo structure change | `README.md`, `CLAUDE.md` |

Sections in `README.md` and `ARCHITECTURE.md` marked with `<!-- MAINTAINED: name -->` ... `<!-- /MAINTAINED -->` comments are the auto-updated targets — locate them by searching the marker. Edit only inside the marker pair; do not move or rename the markers.

When starting a session that touches any trigger condition above, **read the affected MAINTAINED sections first** so the doc update is part of the same edit pass as the code change, not an afterthought.

## When unsure

Stop and ask. If a source returns something unexpected, fail loudly with a clear error — never silently coerce.

