{{ config(materialized='table') }}

-- Protein dimension: one row per reviewed human protein.
-- function_raw is the UniProt source text; NULL rows get 'No information available'.
-- function_friendly and tagline: hand-curated editorial seed wins via COALESCE;
-- LLM output (stg_llm_rewrites) fills the remaining ~20k proteins; when the LLM
-- judged function_raw too terse to rewrite (e.g. "Orphan receptor") and returned
-- null, function_friendly falls back to the raw UniProt text itself -- still more
-- informative to a reader than a placeholder.
-- is_curated = TRUE for the 100 proteins in the dim_protein_editorial seed.

WITH uniprot AS (
    SELECT * FROM {{ ref('stg_uniprot') }}
),
hpa AS (
    SELECT * FROM {{ ref('stg_hpa') }}
),
editorial AS (
    -- chr(65533) = U+FFFD replacement character introduced by dbt-duckdb CSV seed loading.
    -- Replace with proper em-dash (chr(8212) = U+2014) so the UI renders correctly.
    SELECT
        uniprot_accession,
        REPLACE(tagline,           chr(65533), chr(8212)) AS tagline,
        REPLACE(function_friendly, chr(65533), chr(8212)) AS function_friendly,
        is_curated
    FROM {{ ref('dim_protein_editorial') }}
),
llm AS (
    SELECT * FROM {{ ref('stg_llm_rewrites') }}
),
class_tokens AS (
    -- HPA protein_class is a comma-separated multi-label string; explode to one
    -- row per (protein, class token) so each token can be matched to a family group.
    SELECT
        h.uniprot_accession,
        TRIM(UNNEST(string_split(h.protein_class, ','))) AS tok
    FROM hpa h
    WHERE h.protein_class IS NOT NULL AND h.protein_class <> ''
),
family AS (
    -- Pick the single highest-priority (lowest number) matching family group per
    -- protein, so a specific functional family (e.g. Enzymes, Receptors) wins over
    -- the generic localization buckets (Predicted intracellular/membrane/secreted).
    -- ARG_MIN returns the family_group of the row with the minimum priority.
    SELECT
        ct.uniprot_accession,
        ARG_MIN(m.family_group, m.priority) AS family_group
    FROM class_tokens ct
    JOIN {{ ref('family_group_map') }} m ON ct.tok = m.protein_class_token
    GROUP BY ct.uniprot_accession
)

SELECT
    u.uniprot_accession,
    u.gene_symbol,
    u.protein_name,
    u.sequence_length,
    u.sequence,
    u.pfam_id,
    COALESCE(u.function_raw, 'No information available')                                    AS function_raw,
    COALESCE(editorial.function_friendly, llm.function_friendly, u.function_raw, 'No information available') AS function_friendly,
    COALESCE(editorial.tagline, llm.tagline, 'No information available')                      AS tagline,
    editorial.uniprot_accession IS NOT NULL                     AS is_curated,
    u.ensembl_gene_id,
    u.string_protein_id,
    CAST(NULL AS VARCHAR)                                        AS chembl_target_id,
    h.protein_class,
    h.subcellular_location,
    -- Coarse family bucket for the atlas map color dimension; 'Unclassified' when
    -- the protein has no HPA class or only annotation-flag tokens (see family_group_map seed).
    COALESCE(fam.family_group, 'Unclassified')                  AS family_group,
    CURRENT_TIMESTAMP                                            AS updated_at
FROM uniprot u
LEFT JOIN hpa h         ON u.uniprot_accession = h.uniprot_accession
LEFT JOIN editorial     ON u.uniprot_accession = editorial.uniprot_accession
LEFT JOIN llm           ON u.uniprot_accession = llm.uniprot_accession
LEFT JOIN family fam    ON u.uniprot_accession = fam.uniprot_accession
