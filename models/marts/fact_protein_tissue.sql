{{ config(materialized='table') }}

-- Tissue expression summary per protein from HPA.
-- v1 uses the proteinatlas.tsv summary columns (rna_tissue_specificity /
-- rna_tissue_distribution) rather than the per-tissue normal_tissue.tsv detail.
-- One row per protein; `tissue` carries the specificity category (e.g.
-- "Tissue enhanced (pancreas)"), `expression_level` carries the distribution
-- category (e.g. "Detected in single").

WITH proteins AS (
    SELECT uniprot_accession FROM {{ ref('dim_protein') }}
)

SELECT
    h.uniprot_accession,
    h.rna_tissue_specificity  AS tissue,
    h.rna_tissue_distribution AS expression_level
FROM {{ ref('stg_hpa') }} h
INNER JOIN proteins p ON h.uniprot_accession = p.uniprot_accession
