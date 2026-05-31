{{ config(materialized='table') }}

-- Gene-disease association scores from Open Targets.
-- Joins OT associations through OT targets to resolve Ensembl gene IDs to
-- UniProt accessions. Only human proteins (those in dim_protein) are retained.

WITH targets AS (
    SELECT ensembl_gene_id, uniprot_accession
    FROM {{ ref('stg_ot_targets') }}
    WHERE uniprot_accession IS NOT NULL
),
proteins AS (
    SELECT uniprot_accession FROM {{ ref('dim_protein') }}
),
diseases AS (
    SELECT efo_id FROM {{ ref('dim_disease') }}
)

SELECT
    t.uniprot_accession,
    a.efo_id,
    a.overall_score
FROM {{ ref('stg_ot_associations') }} a
INNER JOIN targets t  ON a.ensembl_gene_id = t.ensembl_gene_id
INNER JOIN proteins p ON t.uniprot_accession = p.uniprot_accession
INNER JOIN diseases d ON a.efo_id = d.efo_id
