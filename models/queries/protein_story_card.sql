-- Canonical story card query: returns one fully-populated row for any protein.
-- Default accession is P01308 (insulin) — the Part 3 exit-criteria protein.
-- Usage: dbt show --select protein_story_card --vars '{accession: P53_HUMAN}'
--
-- top_interaction_partners, top_diseases, and approved_drugs are LIST(STRUCT),
-- not pre-formatted "name (score)" display strings. DuckDB serializes struct
-- lists to JSON arrays of objects, so the API/UI layer gets typed fields
-- (accession, gene_symbol, score, ...) with no string-parsing required. Two
-- reasons this matters for Part 6:
--   1. ~8,100 of ~45,000 EFO disease names contain parentheses themselves
--      (e.g. "peroxisome biogenesis disorder 1A (Zellweger)"), which breaks a
--      naive split on a baked "name (score)" string.
--   2. The Part 6 design rule makes interaction partners clickable
--      cross-references — that needs the bare accession *and* a human-readable
--      gene_symbol/protein_name in the same row, not a string requiring a
--      second lookup just to render a label.

SELECT
    p.uniprot_accession,
    p.gene_symbol,
    p.protein_name,
    p.sequence_length,
    p.pfam_id,
    p.function_raw,
    p.function_friendly,
    p.tagline,
    p.is_curated,
    p.protein_class,
    p.subcellular_location,
    fpt.tissue            AS tissue_specificity,
    fpt.expression_level  AS tissue_distribution,

    -- Top 5 interaction partners ordered by confidence
    (
        SELECT LIST(
            {
                'accession': dp.uniprot_accession,
                'gene_symbol': dp.gene_symbol,
                'protein_name': dp.protein_name,
                'combined_score': ROUND(partner.combined_score / 1000.0, 3)
            }
        )
        FROM (
            SELECT
                CASE
                    WHEN i.uniprot_a = p.uniprot_accession THEN i.uniprot_b
                    ELSE i.uniprot_a
                END AS partner_accession,
                i.combined_score
            FROM {{ ref('fact_interaction') }} i
            WHERE i.uniprot_a = p.uniprot_accession
               OR i.uniprot_b = p.uniprot_accession
            ORDER BY i.combined_score DESC
            LIMIT 5
        ) partner
        JOIN {{ ref('dim_protein') }} dp ON dp.uniprot_accession = partner.partner_accession
    ) AS top_interaction_partners,

    -- Top 5 disease associations ordered by confidence
    (
        SELECT LIST(
            {
                'efo_id': pd.efo_id,
                'disease_name': d.disease_name,
                'overall_score': ROUND(pd.overall_score, 3)
            }
        )
        FROM (
            SELECT efo_id, overall_score
            FROM {{ ref('fact_protein_disease') }}
            WHERE uniprot_accession = p.uniprot_accession
            ORDER BY overall_score DESC
            LIMIT 5
        ) pd
        JOIN {{ ref('dim_disease') }} d ON pd.efo_id = d.efo_id
    ) AS top_diseases,

    -- Top 5 drugs whose molecular target is this protein, ranked by clinical phase
    -- (some targets clear 90+ phase->=3 drugs — e.g. P14416/DRD2 has 97 — so the
    -- "top few by clinical phase" cap from the Part 6 design rule is enforced here,
    -- not left to the UI to truncate an unbounded list).
    (
        SELECT LIST(
            {
                'chembl_id': drug.chembl_id,
                'drug_name': drug.drug_name,
                'max_phase': drug.max_phase
            }
        )
        FROM (
            SELECT DISTINCT dr.chembl_id, dr.drug_name, dr.max_phase
            FROM {{ ref('fact_drug_target_disease') }} fdt
            JOIN {{ ref('dim_drug') }} dr ON fdt.chembl_id = dr.chembl_id
            WHERE fdt.uniprot_accession = p.uniprot_accession
              AND dr.max_phase >= 3
            ORDER BY dr.max_phase DESC
            LIMIT 5
        ) drug
    ) AS approved_drugs

FROM {{ ref('dim_protein') }} p
LEFT JOIN {{ ref('fact_protein_tissue') }} fpt
    ON p.uniprot_accession = fpt.uniprot_accession
WHERE p.uniprot_accession = '{{ var("accession", "P01308") }}'
