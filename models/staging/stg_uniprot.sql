-- UniProt Swiss-Prot reviewed human proteins.
-- Flattens list fields to their canonical first element; dbt mart layer consumes this.
-- Source: r2://atlas-raw/uniprot/v{version}/uniprot_human_reviewed_raw.parquet

SELECT
    primary_accession                    AS uniprot_accession,
    gene_symbol,
    protein_name,
    sequence_length,
    sequence,
    function_raw,
    pfam_ids[1]                          AS pfam_id,
    ensembl_gene_ids[1]                  AS ensembl_gene_id,
    string_ids[1]                        AS string_protein_id
FROM read_parquet(
    '{{ var("source_root") }}/uniprot/v{{ var("uniprot_version") }}/uniprot_human_reviewed_raw.parquet'
)
