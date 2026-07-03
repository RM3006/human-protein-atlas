-- Open Targets EFO disease ontology (v26.03).
-- Source: r2://atlas-raw/opentargets/v{version}/ot_diseases.parquet

SELECT
    id    AS efo_id,
    name  AS disease_name
FROM {{ source('bronze', 'ot_diseases') }}
WHERE id IS NOT NULL
  AND name IS NOT NULL
