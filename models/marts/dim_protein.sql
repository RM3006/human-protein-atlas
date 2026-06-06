{{ config(materialized='table') }}

-- Protein dimension: one row per reviewed human protein.
-- function_raw is kept verbatim from UniProt — never overwritten.
-- function_friendly and tagline: hand-curated editorial seed wins via COALESCE;
-- LLM output (stg_llm_rewrites) fills the remaining ~20k proteins.
-- is_curated = TRUE for the 100 proteins in the dim_protein_editorial seed.

WITH uniprot AS (
    SELECT * FROM {{ ref('stg_uniprot') }}
),
hpa AS (
    SELECT * FROM {{ ref('stg_hpa') }}
),
editorial AS (
    SELECT * FROM {{ ref('dim_protein_editorial') }}
),
llm AS (
    SELECT * FROM {{ ref('stg_llm_rewrites') }}
)

SELECT
    u.uniprot_accession,
    u.gene_symbol,
    u.protein_name,
    u.sequence_length,
    u.sequence,
    u.pfam_id,
    u.function_raw,
    COALESCE(editorial.function_friendly, llm.function_friendly) AS function_friendly,
    COALESCE(editorial.tagline, llm.tagline)                    AS tagline,
    editorial.uniprot_accession IS NOT NULL                     AS is_curated,
    u.ensembl_gene_id,
    u.string_protein_id,
    CAST(NULL AS VARCHAR)                                        AS chembl_target_id,
    h.protein_class,
    h.subcellular_location,
    CURRENT_TIMESTAMP                                            AS updated_at
FROM uniprot u
LEFT JOIN hpa h         ON u.uniprot_accession = h.uniprot_accession
LEFT JOIN editorial     ON u.uniprot_accession = editorial.uniprot_accession
LEFT JOIN llm           ON u.uniprot_accession = llm.uniprot_accession
