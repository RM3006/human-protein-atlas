-- Human Protein Atlas proteome summary (v24).
-- Drops rows with no UniProt accession (rare unmapped genes).
-- Source: r2://atlas-raw/hpa/{version}/hpa_proteome.parquet

SELECT
    uniprot_accession,
    gene_symbol,
    protein_class,
    rna_tissue_specificity,
    rna_tissue_distribution,
    subcellular_location,
    disease_involvement
FROM read_parquet(
    'r2://{{ var("r2_bucket") }}/hpa/{{ var("hpa_version") }}/hpa_proteome.parquet'
)
WHERE uniprot_accession IS NOT NULL
