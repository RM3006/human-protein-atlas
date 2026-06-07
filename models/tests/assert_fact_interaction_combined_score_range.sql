-- combined_score is a STRING-DB confidence score on a 0-1000 scale, and the
-- atlas only ingests high-confidence interactions (>= 700, see stg_string).
-- A value outside [700, 1000] would signal a unit/scale change upstream or a
-- broken filter. Fails if any row falls outside this range.

SELECT uniprot_a, uniprot_b, combined_score
FROM {{ ref('fact_interaction') }}
WHERE combined_score < 700 OR combined_score > 1000
