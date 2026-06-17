-- STRING-DB protein interactions (v12.0), already resolved to UniProt accessions.
-- Both endpoints mapped during ingest; combined_score >= 700 (high confidence).
-- Source: r2://atlas-raw/string/v{version}/string_interactions.parquet

SELECT
    uniprot_a,
    uniprot_b,
    combined_score
FROM read_parquet(
    '{{ var("source_root") }}/string/v{{ var("string_version") }}/string_interactions.parquet'
)
