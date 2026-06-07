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
--
-- Score floor (HAVING overall_score >= 0.1): the OT association score is a
-- weight-of-evidence measure in [0, 1] (NOT a probability or effect size). Its
-- raw distribution is extremely right-skewed — across all human pairs the median
-- is ~0.02 and the mean ~0.06, because OT surfaces every faint signal including
-- single text-mining co-mentions and lone underpowered GWAS hits. Keeping that
-- full tail makes the table 4.3M rows of mostly-noise: a naive COUNT would report
-- e.g. EGFR as "associated with 2,600 diseases", and 54% of proteins have an
-- association yet not one reaching 0.1. The 0.1 floor removes that trace tail
-- (4.35M -> ~0.70M rows, ~16% kept) while only 992 of 19,215 proteins (5%) lose
-- ALL associations — i.e. 95% of proteins keep at least one real link. Anything
-- below 0.1 is never actionable for ranking or display; the UI shows the top few
-- per protein by score, so nothing usable is lost. This is a deliberate, lossy
-- quality filter (a future score < 0.1 cannot be recovered without a rebuild),
-- enforced as a contract by assert_fact_protein_disease_overall_score_range.

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
HAVING MAX(a.overall_score) >= 0.1
