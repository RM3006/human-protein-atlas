{{ config(materialized='table') }}

-- Drug dimension: one row per ChEMBL molecule that appears in clinical_target.
-- drug_name and modality come from ot_drug_molecules; max_phase is the highest
-- clinical stage across all drug-target relationships in clinical_target.

-- Map OT v26.03 string phase categories to numeric max_phase.
-- "APPROVAL" = 4 (approved); PHASE3 = 3; PHASE2 = 2; PHASE1 = 1; PHASE0 = 0.
WITH drugs_agg AS (
    SELECT
        chembl_id,
        MAX(
            CASE max_clinical_stage_raw
                WHEN 'APPROVAL' THEN 4
                WHEN 'PHASE4'   THEN 4
                WHEN 'PHASE3'   THEN 3
                WHEN 'PHASE2'   THEN 2
                WHEN 'PHASE1'   THEN 1
                WHEN 'PHASE0'   THEN 0
                ELSE TRY_CAST(max_clinical_stage_raw AS SMALLINT)
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
