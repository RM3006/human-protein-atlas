-- fact_protein_disease must have exactly one row per (protein, disease) pair.
-- ~70 UniProt accessions are the canonical target of multiple Ensembl genes
-- (paralog families: histones, HLA, ...), which fans out into duplicate rows
-- for the same protein-disease pair unless aggregated. Fails if any pair
-- appears more than once.

SELECT uniprot_accession, efo_id, COUNT(*) AS row_count
FROM {{ ref('fact_protein_disease') }}
GROUP BY uniprot_accession, efo_id
HAVING COUNT(*) > 1
