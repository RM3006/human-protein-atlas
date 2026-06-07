-- sequence_length must be positive and must equal the actual length of
-- `sequence` — both come from the same UniProt record, and the story card /
-- API trust sequence_length without re-deriving it. Fails if either invariant
-- is violated (e.g. a future UniProt parsing change desyncs the two fields).

SELECT uniprot_accession, sequence_length, length(sequence) AS actual_length
FROM {{ ref('dim_protein') }}
WHERE sequence_length <= 0
   OR sequence_length != length(sequence)
