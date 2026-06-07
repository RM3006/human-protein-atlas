{{ config(materialized='table') }}

-- Drug dimension: one row per ChEMBL molecule that appears in clinical_target.
-- drug_name and modality come from ot_drug_molecules; max_phase is the highest
-- clinical stage across all drug-target relationships in clinical_target.

-- Map OT v26.03 string phase categories to numeric max_phase (0-4).
-- Raw categories use underscores (PHASE_3, not PHASE3) and include combined-phase
-- and pre-trial statuses the original mapping didn't anticipate. Combined-phase
-- trials (PHASE_2_3, PHASE_1_2) floor to the lower, definitively-completed phase
-- — conservative, so `max_phase >= 3` only counts drugs that DEFINITIVELY cleared
-- phase 3 (matters for the UI's "approved/late-stage drugs" claim). UNKNOWN maps
-- to NULL, not a guessed number (CLAUDE.md rule 5: no invented data). No ELSE
-- branch: an unrecognised category becomes NULL and the
-- accepted_values test on stg_ot_drugs.max_clinical_stage_raw fails loudly
-- rather than silently coercing (CLAUDE.md: never silently coerce).
WITH drugs_agg AS (
    SELECT
        chembl_id,
        MAX(
            CASE max_clinical_stage_raw
                WHEN 'APPROVAL'      THEN 4
                WHEN 'PHASE_3'       THEN 3
                WHEN 'PHASE_2_3'     THEN 2
                WHEN 'PHASE_2'       THEN 2
                WHEN 'PHASE_1_2'     THEN 1
                WHEN 'PHASE_1'       THEN 1
                WHEN 'EARLY_PHASE_1' THEN 0
                WHEN 'IND'           THEN 0
                WHEN 'PREAPPROVAL'   THEN 0
                WHEN 'PRECLINICAL'   THEN 0
                WHEN 'UNKNOWN'       THEN NULL
            END
        ) AS max_phase
    FROM {{ ref('stg_ot_drugs') }}
    GROUP BY chembl_id
),
molecules AS (
    SELECT * FROM {{ ref('stg_ot_drug_molecules') }}
)

SELECT
    d.chembl_id,
    m.drug_name,
    CAST(d.max_phase AS SMALLINT) AS max_phase,
    m.modality
FROM drugs_agg d
LEFT JOIN molecules m ON d.chembl_id = m.chembl_id
