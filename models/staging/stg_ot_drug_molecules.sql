-- Open Targets drug molecule metadata (v26.03).
-- Provides preferred drug names and modality for dim_drug.
-- Source: r2://atlas-raw/opentargets/v{version}/ot_drug_molecules.parquet

SELECT
    id        AS chembl_id,
    name      AS drug_name,
    drugType  AS modality
FROM {{ source('bronze', 'ot_drug_molecules') }}
WHERE id IS NOT NULL
