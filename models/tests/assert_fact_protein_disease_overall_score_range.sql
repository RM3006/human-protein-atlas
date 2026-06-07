-- overall_score is an Open Targets association score on a [0, 1] scale.
-- A value outside this range would signal a schema/scale change upstream.
-- Fails if any row falls outside [0, 1].

SELECT uniprot_accession, efo_id, overall_score
FROM {{ ref('fact_protein_disease') }}
WHERE overall_score < 0 OR overall_score > 1
