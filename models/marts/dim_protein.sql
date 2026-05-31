{{ config(materialized='table') }}

-- Protein dimension: one row per reviewed human protein.
-- Anchor table for the atlas — every fact table joins back here on uniprot_accession.
-- function_friendly, tagline, is_curated are populated in Part 5 (LLM rewrites).

WITH uniprot AS (
    SELECT * FROM {{ ref('stg_uniprot') }}
),
hpa AS (
    SELECT * FROM {{ ref('stg_hpa') }}
)

SELECT
    u.uniprot_accession,
    u.gene_symbol,
    u.protein_name,
    u.sequence_length,
    u.sequence,
    u.pfam_id,
    u.function_raw,
    CAST(NULL AS VARCHAR)       AS function_friendly,
    CAST(NULL AS VARCHAR)       AS tagline,
    FALSE                       AS is_curated,
    u.ensembl_gene_id,
    u.string_protein_id,
    CAST(NULL AS VARCHAR)       AS chembl_target_id,
    h.protein_class,
    h.subcellular_location,
    CURRENT_TIMESTAMP           AS updated_at
FROM uniprot u
LEFT JOIN hpa h ON u.uniprot_accession = h.uniprot_accession
