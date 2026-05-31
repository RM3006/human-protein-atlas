-- Open Targets drug-target associations (v26.03 clinical_target).
-- `disease_ids` is a list of EFO IDs; fact_drug_target_disease unnests it.
-- Drug display names join via stg_ot_drug_molecules on chembl_id.
-- Source: r2://atlas-raw/opentargets/v{version}/ot_drugs.parquet

-- maxClinicalStage in OT v26.03 is a string category ("APPROVAL", "PHASE3", etc.)
-- Pass through as-is; dim_drug maps to numeric max_phase.
SELECT
    drugId          AS chembl_id,
    targetId        AS ensembl_gene_id,
    diseases        AS disease_ids,
    maxClinicalStage AS max_clinical_stage_raw
FROM read_parquet(
    's3://{{ var("r2_bucket") }}/opentargets/v{{ var("ot_version") }}/ot_drugs.parquet'
)
