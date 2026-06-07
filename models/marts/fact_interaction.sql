{{ config(materialized='table') }}

-- Protein-protein interaction pairs from STRING-DB (v12.0).
-- Filtered to only include proteins present in dim_protein (Swiss-Prot human),
-- ensuring referential integrity. combined_score is 0-1000 (divide by 1000 for
-- confidence as a fraction).
--
-- Grain: ONE row per unordered protein pair (uniprot_a < uniprot_b), no self
-- interactions. STRING operates at gene level and reports each interaction
-- symmetrically (A-B and B-A); separately, paralogous gene families (histones,
-- HLA, ...) have many distinct gene copies that UniProt canonicalizes to a
-- SINGLE accession (e.g. P62805/Histone H4 resolves from 14 distinct Ensembl
-- genes). Both effects collapse onto the atlas's protein-level identity, so the
-- raw resolved data contains symmetric duplicates, near-duplicate pairs scored
-- from different paralog copies, and "X interacts with X" self-loops that are
-- artifacts of canonicalization rather than real self-interactions. We
-- canonicalize the pair ordering and take MAX(combined_score) — the strongest
-- evidence of interaction between these two protein identities — rather than
-- inventing or arbitrarily picking one of several raw scores.

WITH proteins AS (
    SELECT uniprot_accession FROM {{ ref('dim_protein') }}
),
resolved AS (
    SELECT
        s.uniprot_a,
        s.uniprot_b,
        s.combined_score
    FROM {{ ref('stg_string') }} s
    INNER JOIN proteins pa ON s.uniprot_a = pa.uniprot_accession
    INNER JOIN proteins pb ON s.uniprot_b = pb.uniprot_accession
    WHERE s.uniprot_a != s.uniprot_b
)

SELECT
    LEAST(uniprot_a, uniprot_b)    AS uniprot_a,
    GREATEST(uniprot_a, uniprot_b) AS uniprot_b,
    MAX(combined_score)            AS combined_score
FROM resolved
GROUP BY 1, 2
