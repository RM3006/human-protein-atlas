-- fact_interaction must contain no "X interacts with X" rows.
-- These were artifacts of paralog canonicalization (many STRING gene-level
-- entries collapsing onto one UniProt accession), not real self-interactions.
-- Fails (returns rows) if any self-loop survives the grain rebuild.

SELECT uniprot_a, uniprot_b, combined_score
FROM {{ ref('fact_interaction') }}
WHERE uniprot_a = uniprot_b
