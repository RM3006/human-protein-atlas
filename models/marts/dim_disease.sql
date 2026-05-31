{{ config(materialized='table') }}

-- Disease dimension: one row per EFO disease term from Open Targets.
-- therapeutic_area is derivable from the `parents` column hierarchy in OT but
-- requires a recursive self-join; left as NULL for v1 (CLAUDE.md rule 5).

SELECT
    efo_id,
    disease_name,
    CAST(NULL AS VARCHAR) AS therapeutic_area
FROM {{ ref('stg_ot_diseases') }}
