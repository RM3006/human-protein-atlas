"""Human Protein Atlas ingest -> Bronze Parquet in R2.

Downloads proteinatlas.tsv (the all-in-one bulk export from HPA) and parks the
manifest-defined columns in R2. HPA includes a direct ``Uniprot`` column, so no
ID mapping step is required -- this is the simplest ingest in Part 2.
"""

import io
import zipfile
from typing import Any

import httpx
import polars as pl
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from atlas.logging import logger
from atlas.resources.r2 import R2Resource

# HPA releases annually. Pin to the current release; bump when HPA ships v25.
HPA_VERSION = "v24"
HPA_URL = "https://www.proteinatlas.org/download/proteinatlas.tsv.zip"
R2_KEY = f"hpa/{HPA_VERSION}/hpa_proteome.parquet"

# Columns from the HPA v24 file. Note: "Tissue expression" was removed in v24;
# rna_tissue_specificity now carries the equivalent category information.
_HPA_COLUMNS = [
    "Gene",
    "Uniprot",
    "Protein class",
    "RNA tissue specificity",
    "RNA tissue distribution",
    "Subcellular location",
    "Disease involvement",
]

_COLUMN_RENAME: dict[str, str] = {
    "Gene": "gene_symbol",
    "Uniprot": "uniprot_accession",
    "Protein class": "protein_class",
    "RNA tissue specificity": "rna_tissue_specificity",
    "RNA tissue distribution": "rna_tissue_distribution",
    "Subcellular location": "subcellular_location",
    "Disease involvement": "disease_involvement",
}

RAW_SCHEMA: dict[str, pl.DataType] = {
    "gene_symbol": pl.String(),
    "uniprot_accession": pl.String(),
    "protein_class": pl.String(),
    "rna_tissue_specificity": pl.String(),
    "rna_tissue_distribution": pl.String(),
    "subcellular_location": pl.String(),
    "disease_involvement": pl.String(),
}


def parse_hpa_tsv(content: bytes) -> pl.DataFrame:
    """Parse HPA TSV bytes into a typed Bronze DataFrame.

    Selects manifest columns, renames to snake_case, deduplicates on
    ``uniprot_accession`` keeping the first row (some genes have multiple HPA
    entries per manifest gotchas section).

    Pure and side-effect free; tested independently of network I/O.
    """
    df = (
        pl.read_csv(
            io.BytesIO(content),
            separator="\t",
            columns=_HPA_COLUMNS,
            null_values=["", "NA"],
            infer_schema_length=0,  # read all as String; cast below is explicit
        )
        .rename(_COLUMN_RENAME)
        .unique(subset=["uniprot_accession"], keep="first", maintain_order=True)  # pyright: ignore[reportUnknownMemberType]
        .cast(RAW_SCHEMA)  # pyright: ignore[reportArgumentType]
    )
    return df


@asset(group_name="ingest", compute_kind="python")
def hpa_proteome_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Human Protein Atlas proteome summary -> Bronze Parquet.

    Produces: Parquet with tissue expression, subcellular location, protein class,
              and disease involvement for ~20k proteins.
    Depends on: proteinatlas.tsv bulk download and the R2 resource.
    Lands at: r2://atlas-raw/hpa/v24/hpa_proteome.parquet.
    """
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        logger.info("Downloading HPA proteome from %s...", HPA_URL)
        response = client.get(HPA_URL)
        response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        tsv_names = [n for n in zf.namelist() if n.endswith(".tsv")]
        if not tsv_names:
            raise RuntimeError(f"No .tsv file found inside HPA zip. Contents: {zf.namelist()}")
        with zf.open(tsv_names[0]) as tsv:
            content = tsv.read()

    df = parse_hpa_tsv(content)
    r2.write_parquet(df, R2_KEY)

    context.log.info("Wrote %d HPA records to r2://%s/%s", df.height, r2.bucket, R2_KEY)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "hpa_version": MetadataValue.text(HPA_VERSION),
            "r2_key": MetadataValue.text(R2_KEY),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )
