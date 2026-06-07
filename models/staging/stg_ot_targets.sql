-- Open Targets target metadata (v26.03).
-- Extracts the UniProt Swiss-Prot accession from the proteinIds list of structs.
-- Targets with no Swiss-Prot accession remain (uniprot_accession = NULL); the
-- mart layer filters them via INNER JOIN.
-- Source: r2://atlas-raw/opentargets/v{version}/ot_targets.parquet

SELECT
    id                                                                      AS ensembl_gene_id,
    approvedSymbol                                                          AS gene_symbol,
    approvedName                                                            AS gene_name,
    list_filter(proteinIds, x -> x.source = 'uniprot_swissprot')[1].id     AS uniprot_accession
FROM read_parquet(
    'r2://{{ var("r2_bucket") }}/opentargets/v{{ var("ot_version") }}/ot_targets.parquet'
)
