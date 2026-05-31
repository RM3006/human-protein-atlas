{{ config(materialized='table') }}

-- Protein-protein interaction pairs from STRING-DB (v12.0).
-- Filtered to only include proteins present in dim_protein (Swiss-Prot human),
-- ensuring referential integrity. combined_score is 0-1000 (divide by 1000 for
-- confidence as a fraction).

WITH proteins AS (
    SELECT uniprot_accession FROM {{ ref('dim_protein') }}
)

SELECT
    s.uniprot_a,
    s.uniprot_b,
    s.combined_score
FROM {{ ref('stg_string') }} s
INNER JOIN proteins pa ON s.uniprot_a = pa.uniprot_accession
INNER JOIN proteins pb ON s.uniprot_b = pb.uniprot_accession
