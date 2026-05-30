"""Open Targets ingest: four datasets -> Bronze Parquet in R2.

Open Targets releases quarterly Parquet dumps at EBI FTP. This module ingests
the four datasets needed for the atlas story card:
  - targets         -> Ensembl gene metadata + UniProt join key
  - diseases        -> EFO disease ontology
  - associations    -> gene-disease evidence scores (associationByOverallDirect)
  - drugs           -> approved + clinical-trial drug-target-disease triples

Each dataset is a directory of Parquet part files on EBI FTP. Parts are
downloaded sequentially, the required columns selected, and the result written
to R2 as a single Parquet file.
"""

import io
import re
from typing import Any

import httpx
import polars as pl
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from atlas.logging import logger
from atlas.resources.r2 import R2Resource

OT_VERSION = "26.03"
_OT_FTP_BASE = "https://ftp.ebi.ac.uk/pub/databases/opentargets/platform"
_ETL_PATH = f"{_OT_FTP_BASE}/{OT_VERSION}/output/etl/parquet"

# Columns to land in Bronze (original OT names; dbt staging renames to snake_case).
OT_TARGETS_COLUMNS = ["id", "approvedSymbol", "approvedName", "proteinIds"]
OT_DISEASES_COLUMNS = ["id", "name", "therapeuticAreas"]
OT_ASSOCIATIONS_COLUMNS = ["targetId", "diseaseId", "score"]
OT_DRUGS_COLUMNS = [
    "drugId",
    "prefName",
    "targetId",
    "diseaseId",
    "phase",
    "mechanismOfAction",
]


def list_parts(client: httpx.Client, dataset_url: str) -> list[str]:
    """Return the list of Parquet part-file names in an OT dataset directory.

    Parses the EBI FTP HTTP index for ``href`` links matching the OT part-file
    naming pattern (``part-NNNNN-*.parquet``).
    """
    resp = client.get(dataset_url, follow_redirects=True, timeout=60.0)
    resp.raise_for_status()
    return re.findall(r'href="(part-[^"]+\.parquet)"', resp.text)


def fetch_dataset(
    client: httpx.Client, dataset: str, columns: list[str]
) -> pl.DataFrame:
    """Download all Parquet parts for one OT dataset, select columns, concatenate.

    Raises ``RuntimeError`` if no part files are found (wrong version or dataset
    name); raises ``polars.exceptions.ColumnNotFoundError`` if a requested column
    is absent (schema changed upstream) — both fail loudly per CLAUDE.md.
    """
    dataset_url = f"{_ETL_PATH}/{dataset}/"
    part_names = list_parts(client, dataset_url)
    if not part_names:
        raise RuntimeError(
            f"No Parquet parts found for Open Targets dataset '{dataset}' "
            f"at {dataset_url}. Verify OT_VERSION='{OT_VERSION}' and dataset name."
        )

    frames: list[pl.DataFrame] = []
    for name in part_names:
        url = f"{dataset_url}{name}"
        resp = client.get(url, follow_redirects=True, timeout=300.0)
        resp.raise_for_status()
        part_df = pl.read_parquet(io.BytesIO(resp.content)).select(columns)  # pyright: ignore[reportUnknownMemberType]
        frames.append(part_df)
        logger.info("OT %s: loaded %s (%d rows)", dataset, name, part_df.height)

    return pl.concat(frames)


@asset(group_name="ingest", compute_kind="python")
def ot_targets_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Open Targets target metadata -> Bronze Parquet.

    Produces: Parquet with Ensembl gene IDs, approved symbol/name, and proteinIds
              (list of id+source structs used to resolve the UniProt join key).
    Depends on: Open Targets EBI FTP v26.03 ``targets/`` dataset and the R2 resource.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_targets.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_targets.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "targets", OT_TARGETS_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info("Wrote %d OT target rows to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )


@asset(group_name="ingest", compute_kind="python")
def ot_diseases_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Open Targets disease ontology -> Bronze Parquet.

    Produces: Parquet with EFO disease IDs, display names, and therapeutic areas.
    Depends on: Open Targets EBI FTP v26.03 ``diseases/`` dataset and the R2 resource.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_diseases.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_diseases.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "diseases", OT_DISEASES_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info("Wrote %d OT disease rows to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )


@asset(group_name="ingest", compute_kind="python")
def ot_associations_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Open Targets gene-disease associations -> Bronze Parquet.

    Produces: Parquet of (targetId, diseaseId, score) triples from
              ``associationByOverallDirect`` — the aggregated, cross-source
              confidence scores used in the story-card "When broken" slot.
    Depends on: Open Targets EBI FTP v26.03 ``associationByOverallDirect/`` and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_associations.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_associations.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "associationByOverallDirect", OT_ASSOCIATIONS_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info(
        "Wrote %d OT association rows to r2://%s/%s", df.height, r2.bucket, key
    )
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )


@asset(group_name="ingest", compute_kind="python")
def ot_drugs_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Open Targets known drugs -> Bronze Parquet.

    Produces: Parquet of drug-target-disease triples from ``knownDrugsAggregated``
              covering approved (phase 4) and clinical-trial drugs. Used for the
              "Drugs" slot on the story card.
    Depends on: Open Targets EBI FTP v26.03 ``knownDrugsAggregated/`` and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_drugs.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_drugs.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "knownDrugsAggregated", OT_DRUGS_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info("Wrote %d OT drug rows to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )
