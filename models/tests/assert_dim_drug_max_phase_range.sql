-- max_phase is mapped from OT clinical-stage categories to integers 0-4
-- (or NULL for UNKNOWN). A value outside [0, 4] would mean the CASE mapping
-- in dim_drug picked up an unrecognised category — should be caught earlier
-- by the accepted_values test on stg_ot_drugs.max_clinical_stage_raw, but this
-- locks in the output contract directly. Fails if any non-NULL value is
-- outside [0, 4].

SELECT chembl_id, max_phase
FROM {{ ref('dim_drug') }}
WHERE max_phase IS NOT NULL
  AND (max_phase < 0 OR max_phase > 4)
