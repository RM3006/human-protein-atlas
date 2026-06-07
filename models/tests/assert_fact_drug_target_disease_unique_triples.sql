-- fact_drug_target_disease must have exactly one row per (drug, protein, disease)
-- triple. The model's SELECT DISTINCT relies on this implicitly; nothing pins
-- it. Fails if any triple appears more than once.

SELECT chembl_id, uniprot_accession, efo_id, COUNT(*) AS row_count
FROM {{ ref('fact_drug_target_disease') }}
GROUP BY chembl_id, uniprot_accession, efo_id
HAVING COUNT(*) > 1
