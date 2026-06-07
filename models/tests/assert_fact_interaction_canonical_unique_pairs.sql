-- fact_interaction must have exactly one row per unordered protein pair, in
-- canonical order (uniprot_a < uniprot_b). STRING reports interactions
-- symmetrically (A-B and B-A), so without canonicalization + dedup the same
-- biological interaction appears as multiple rows. Fails if any pair is
-- mis-ordered (a >= b) or duplicated.

SELECT uniprot_a, uniprot_b, COUNT(*) AS row_count
FROM {{ ref('fact_interaction') }}
WHERE uniprot_a >= uniprot_b
GROUP BY uniprot_a, uniprot_b

UNION ALL

SELECT uniprot_a, uniprot_b, COUNT(*) AS row_count
FROM {{ ref('fact_interaction') }}
GROUP BY uniprot_a, uniprot_b
HAVING COUNT(*) > 1
