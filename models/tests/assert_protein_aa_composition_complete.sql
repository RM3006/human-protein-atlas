-- fact_protein_aa_composition must be complete and consistent for every protein:
--   1. exactly 20 rows per protein (one per standard amino acid, zeros included),
--   2. the summed residue counts equal the protein's standard-residue count
--      (sequence length minus any non-standard residues such as selenocysteine),
--   3. summed percentages never exceed 100% (allowing rounding slack).
-- Recomputing standard_residues from the sequence makes this exact: selenoproteins
-- (e.g. SELENOP, 10x 'U') legitimately sum to < 100% and must NOT fail here.
-- Fails (returns rows) if any protein violates an invariant.

WITH per_protein AS (
    SELECT
        uniprot_accession,
        COUNT(*)              AS n_rows,
        SUM("count")          AS total_counted,
        SUM(pct_of_sequence)  AS total_pct
    FROM {{ ref('fact_protein_aa_composition') }}
    GROUP BY uniprot_accession
),
expected AS (
    SELECT
        uniprot_accession,
        length(regexp_replace(sequence, '[^ACDEFGHIKLMNPQRSTVWY]', '', 'g')) AS standard_residues
    FROM {{ ref('dim_protein') }}
    WHERE sequence IS NOT NULL AND length(sequence) > 0
)

SELECT
    p.uniprot_accession,
    p.n_rows,
    p.total_counted,
    e.standard_residues,
    p.total_pct
FROM per_protein p
JOIN expected e USING (uniprot_accession)
WHERE p.n_rows <> 20
   OR p.total_counted <> e.standard_residues
   OR p.total_pct > 100.5
