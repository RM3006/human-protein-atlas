{{ config(materialized='table') }}

-- Drug-target-disease triples from Open Targets clinical_target (v26.03).
-- The `diseases` list in clinical_target is unnested here; each row is one
-- (drug, protein, disease) triple. mechanism_of_action is not in clinical_target
-- and is left NULL for v1 (available from drug_mechanism_of_action in v2).

WITH targets AS (
    SELECT ensembl_gene_id, uniprot_accession
    FROM {{ ref('stg_ot_targets') }}
    WHERE uniprot_accession IS NOT NULL
),
proteins AS (
    SELECT uniprot_accession FROM {{ ref('dim_protein') }}
),
drugs_known AS (
    SELECT chembl_id FROM {{ ref('dim_drug') }}
),
drugs_exploded AS (
    SELECT
        chembl_id,
        ensembl_gene_id,
        UNNEST(disease_ids) AS efo_id
    FROM {{ ref('stg_ot_drugs') }}
    WHERE disease_ids IS NOT NULL
)

SELECT DISTINCT
    de.chembl_id,
    t.uniprot_accession,
    de.efo_id,
    CAST(NULL AS VARCHAR) AS mechanism_of_action
FROM drugs_exploded de
INNER JOIN targets    t  ON de.ensembl_gene_id = t.ensembl_gene_id
INNER JOIN proteins   p  ON t.uniprot_accession = p.uniprot_accession
INNER JOIN drugs_known dk ON de.chembl_id = dk.chembl_id
