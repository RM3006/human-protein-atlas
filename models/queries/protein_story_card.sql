-- Canonical story card query: returns one fully-populated row for any protein.
-- Default accession is P01308 (insulin) — the Part 3 exit-criteria protein.
-- Usage: dbt show --select protein_story_card --vars '{accession: P53_HUMAN}'

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
            CASE
                WHEN i.uniprot_a = p.uniprot_accession THEN i.uniprot_b
                ELSE i.uniprot_a
            END || ' (' || ROUND(i.combined_score / 1000.0, 3)::VARCHAR || ')'
        )
        FROM (
            SELECT uniprot_a, uniprot_b, combined_score
            FROM {{ ref('fact_interaction') }}
            WHERE uniprot_a = p.uniprot_accession
               OR uniprot_b = p.uniprot_accession
            ORDER BY combined_score DESC
            LIMIT 5
        ) i
    ) AS top_interaction_partners,

    -- Top 5 disease associations ordered by confidence
    (
        SELECT LIST(d.disease_name || ' (' || ROUND(pd.overall_score, 3)::VARCHAR || ')')
        FROM (
            SELECT efo_id, overall_score
            FROM {{ ref('fact_protein_disease') }}
            WHERE uniprot_accession = p.uniprot_accession
            ORDER BY overall_score DESC
            LIMIT 5
        ) pd
        JOIN {{ ref('dim_disease') }} d ON pd.efo_id = d.efo_id
    ) AS top_diseases,

    -- Drugs in phase 3 or above
    (
        SELECT LIST(DISTINCT dr.drug_name)
        FROM {{ ref('fact_drug_target_disease') }} fdt
        JOIN {{ ref('dim_drug') }} dr ON fdt.chembl_id = dr.chembl_id
        WHERE fdt.uniprot_accession = p.uniprot_accession
          AND dr.max_phase >= 3
    ) AS approved_drugs

FROM {{ ref('dim_protein') }} p
LEFT JOIN {{ ref('fact_protein_tissue') }} fpt
    ON p.uniprot_accession = fpt.uniprot_accession
WHERE p.uniprot_accession = '{{ var("accession", "P01308") }}'
