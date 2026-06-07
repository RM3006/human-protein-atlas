-- overall_score is an Open Targets association score on a [0, 1] scale.
-- Two invariants are checked here:
--   1. Upper bound: a value > 1 would signal an upstream scale change (e.g. OT
--      switching to 0-100), which must fail loudly rather than silently rescale.
--   2. Lower bound (0.1): fact_protein_disease deliberately drops the trace tail
--      below 0.1 (see the model's HAVING clause and header comment for why). The
--      mart's contract is therefore [0.1, 1], not [0, 1]; a row below 0.1 means
--      the floor was removed or bypassed and the noise is back.
-- Fails if any row falls outside [0.1, 1].

SELECT uniprot_accession, efo_id, overall_score
FROM {{ ref('fact_protein_disease') }}
WHERE overall_score < 0.1 OR overall_score > 1
