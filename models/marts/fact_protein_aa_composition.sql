{{ config(materialized='table') }}

-- Amino-acid composition of every protein, in long format.
-- Produces: one row per (uniprot_accession, amino_acid_code) for the 20 standard
--   amino acids, with `count` (residues of that type) and `pct_of_sequence`.
-- Depends on: dim_protein (sequence) and the seed_amino_acids lookup.
-- Lands in: MotherDuck `main.fact_protein_aa_composition`.
--
-- Per-letter counts use length(sequence) - length(replace(sequence, letter, '')):
-- the drop in length after stripping a letter is how many times it occurred.
-- pct_of_sequence divides by sequence_length, so non-standard residues (e.g.
-- selenocysteine 'U', or 'X' unknown) are simply not counted and the 20 standard
-- percentages sum to just under 100% for those few proteins -- honest, not coerced
-- (CLAUDE.md rule 5). The INNER JOIN to the seed keeps only the 20 standard codes.

WITH counts AS (
    SELECT
        uniprot_accession,
        sequence_length,
        length(sequence) - length(replace(sequence, 'A', '')) AS A,
        length(sequence) - length(replace(sequence, 'R', '')) AS R,
        length(sequence) - length(replace(sequence, 'N', '')) AS N,
        length(sequence) - length(replace(sequence, 'D', '')) AS D,
        length(sequence) - length(replace(sequence, 'C', '')) AS C,
        length(sequence) - length(replace(sequence, 'E', '')) AS E,
        length(sequence) - length(replace(sequence, 'Q', '')) AS Q,
        length(sequence) - length(replace(sequence, 'G', '')) AS G,
        length(sequence) - length(replace(sequence, 'H', '')) AS H,
        length(sequence) - length(replace(sequence, 'I', '')) AS I,
        length(sequence) - length(replace(sequence, 'L', '')) AS L,
        length(sequence) - length(replace(sequence, 'K', '')) AS K,
        length(sequence) - length(replace(sequence, 'M', '')) AS M,
        length(sequence) - length(replace(sequence, 'F', '')) AS F,
        length(sequence) - length(replace(sequence, 'P', '')) AS P,
        length(sequence) - length(replace(sequence, 'S', '')) AS S,
        length(sequence) - length(replace(sequence, 'T', '')) AS T,
        length(sequence) - length(replace(sequence, 'W', '')) AS W,
        length(sequence) - length(replace(sequence, 'Y', '')) AS Y,
        length(sequence) - length(replace(sequence, 'V', '')) AS V
    FROM {{ ref('dim_protein') }}
    WHERE sequence IS NOT NULL AND length(sequence) > 0
),
long AS (
    UNPIVOT counts
    ON A, R, N, D, C, E, Q, G, H, I, L, K, M, F, P, S, T, W, Y, V
    INTO NAME amino_acid_code VALUE "count"
)

SELECT
    l.uniprot_accession,
    l.amino_acid_code,
    l."count",
    ROUND(100.0 * l."count" / l.sequence_length, 2) AS pct_of_sequence
FROM long l
INNER JOIN {{ ref('seed_amino_acids') }} aa
    ON l.amino_acid_code = aa.amino_acid_code
