-- LLM-rewritten function descriptions for all reviewed human proteins.
-- Written by the protein_llm_rewrites Dagster asset (Claude Haiku, Anthropic Batch API).
-- Run that asset before running dbt, or this model will error on a missing file.
-- Source: r2://atlas-raw/llm/v{llm_version}/protein_rewrites.parquet

SELECT
    uniprot_accession,
    function_friendly,
    tagline
FROM {{ source('bronze', 'llm_rewrites') }}
