-- Open Targets gene-disease associations (v26.03 association_overall_direct).
-- Contains all species; the mart layer filters to human via join on stg_ot_targets.
-- Source: r2://atlas-raw/opentargets/v{version}/ot_associations.parquet

SELECT
    targetId                                    AS ensembl_gene_id,
    diseaseId                                   AS efo_id,
    CAST(associationScore AS DECIMAL(10, 6))    AS overall_score
FROM read_parquet(
    'r2://{{ var("r2_bucket") }}/opentargets/v{{ var("ot_version") }}/ot_associations.parquet'
)
