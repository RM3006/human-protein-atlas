{{ config(tags=['real_data']) }}
-- family_group is the atlas map color dimension, derived in dim_protein by joining
-- exploded HPA protein_class tokens to the family_group_map seed and taking the
-- lowest-priority match (COALESCE to 'Unclassified' otherwise). Two invariants:
--   1. Every row has a non-NULL value drawn from the known bucket set. An unexpected
--      value means the seed gained an unmapped family_group without this test (and the
--      legend) being updated; a NULL means the COALESCE was removed.
--   2. At least 9 distinct buckets are present. A broken token-explode or seed join
--      would silently collapse most proteins to 'Unclassified' while still passing
--      not_null — the exact "wrong-but-present" failure mode this suite guards against.
-- Fails (returns rows) on either violation.

WITH invalid_values AS (
    SELECT
        uniprot_accession,
        family_group,
        'unexpected_or_null_family_group' AS violation
    FROM {{ ref('dim_protein') }}
    WHERE family_group IS NULL
       OR family_group NOT IN (
            'Receptors',
            'Ion channels',
            'Transporters',
            'Transcription factors',
            'Immune',
            'Ribosomal / translation',
            'Enzymes',
            'Secreted',
            'Membrane',
            'Intracellular',
            'Unclassified'
       )
),
too_few_groups AS (
    SELECT
        CAST(NULL AS VARCHAR) AS uniprot_accession,
        CAST(NULL AS VARCHAR) AS family_group,
        'fewer_than_9_distinct_family_groups' AS violation
    FROM {{ ref('dim_protein') }}
    HAVING COUNT(DISTINCT family_group) < 9
)

SELECT * FROM invalid_values
UNION ALL
SELECT * FROM too_few_groups
