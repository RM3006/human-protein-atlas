{{ config(materialized='table') }}

-- Gene-disease association scores from Open Targets.
-- Joins OT associations through OT targets to resolve Ensembl gene IDs to
-- UniProt accessions. Only human proteins (those in dim_protein) are retained.
--
-- Grain: ONE row per (protein, disease) pair. ~70 UniProt accessions are the
-- canonical target of MULTIPLE distinct Ensembl genes — paralogous families
-- (histones, HLA, ...) that UniProt collapses to one accession (e.g.
-- P62805/Histone H4 <- 14 Ensembl genes). Each paralog copy carries its own OT
-- association score for the same disease, so a naive join fans out into
-- duplicate (protein, disease) rows. We aggregate via MAX(overall_score) — the
-- strongest evidence across paralogs for this protein identity — to align with
-- the atlas's protein-level grain without inventing or arbitrarily picking a
-- single paralog's score.

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
    MAX(a.overall_score) AS overall_score
FROM {{ ref('stg_ot_associations') }} a
INNER JOIN targets t  ON a.ensembl_gene_id = t.ensembl_gene_id
INNER JOIN proteins p ON t.uniprot_accession = p.uniprot_accession
INNER JOIN diseases d ON a.efo_id = d.efo_id
GROUP BY 1, 2
