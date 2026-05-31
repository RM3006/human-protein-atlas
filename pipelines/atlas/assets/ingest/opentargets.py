"""Open Targets ingest: four datasets -> Bronze Parquet in R2.

Open Targets releases quarterly Parquet dumps at EBI FTP. This module ingests
the four datasets needed for the atlas story card:
  - target              -> Ensembl gene metadata + UniProt join key
  - disease             -> EFO disease ontology (single file in v26.03+)
  - association_overall_direct -> gene-disease evidence scores
  - clinical_target     -> drug-target associations with clinical stage

Schema notes for v26.03 (path layout changed from pre-25.12):
  - ETL path is now ``output/`` (not ``output/etl/parquet/``).
  - ``diseases`` → ``disease/disease.parquet`` (single file, not partitioned).
  - ``associationByOverallDirect`` → ``association_overall_direct``; ``score``
    renamed to ``associationScore``.
  - ``knownDrugsAggregated`` removed; ``clinical_target/clinical_target.parquet``
    carries drug-target-disease triples. Drug names join via ``drug_molecule``
    in the dbt Silver layer.
  - ``therapeuticAreas`` removed from the disease table in v26.03.
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
_OUTPUT_PATH = f"{_OT_FTP_BASE}/{OT_VERSION}/output"

# Columns to land in Bronze (original OT names; dbt staging renames to snake_case).
OT_TARGETS_COLUMNS = ["id", "approvedSymbol", "approvedName", "proteinIds"]
OT_DISEASES_COLUMNS = ["id", "name"]
OT_ASSOCIATIONS_COLUMNS = ["diseaseId", "targetId", "associationScore"]
OT_DRUGS_COLUMNS = ["drugId", "targetId", "diseases", "maxClinicalStage"]
OT_DRUG_MOLECULES_COLUMNS = ["id", "name", "drugType"]


def list_parts(client: httpx.Client, dataset_url: str) -> list[str]:
    """Return Parquet file names in an OT dataset directory.

    Handles both partitioned datasets (``part-NNNNN-*.parquet``) and single-file
    datasets (e.g., ``disease.parquet``, ``clinical_target.parquet``).
    """
    resp = client.get(dataset_url, follow_redirects=True, timeout=60.0)
    resp.raise_for_status()
    return re.findall(r'href="([^"?/][^"]+\.parquet)"', resp.text)


def fetch_dataset(client: httpx.Client, dataset: str, columns: list[str]) -> pl.DataFrame:
    """Download all Parquet files for one OT dataset, select columns, concatenate.

    Raises ``RuntimeError`` if no files are found (wrong version or dataset
    name); raises ``polars.exceptions.ColumnNotFoundError`` if a requested column
    is absent (schema changed upstream) — both fail loudly per CLAUDE.md.
    """
    dataset_url = f"{_OUTPUT_PATH}/{dataset}/"
    part_names = list_parts(client, dataset_url)
    if not part_names:
        raise RuntimeError(
            f"No Parquet files found for Open Targets dataset '{dataset}' "
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
def ot_targets_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Open Targets target metadata -> Bronze Parquet.

    Produces: Parquet with Ensembl gene IDs, approved symbol/name, and proteinIds
              (list of id+source structs used to resolve the UniProt join key).
    Depends on: Open Targets EBI FTP v26.03 ``target/`` dataset and the R2 resource.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_targets.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_targets.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "target", OT_TARGETS_COLUMNS)

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
def ot_diseases_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Open Targets disease ontology -> Bronze Parquet.

    Produces: Parquet with EFO disease IDs and display names.
    Depends on: Open Targets EBI FTP v26.03 ``disease/disease.parquet`` and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_diseases.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_diseases.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "disease", OT_DISEASES_COLUMNS)

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
def ot_associations_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Open Targets gene-disease associations -> Bronze Parquet.

    Produces: Parquet of (diseaseId, targetId, associationScore) triples from
              ``association_overall_direct`` — aggregated cross-source confidence
              scores used in the story-card "When broken" slot.
    Depends on: OT EBI FTP v26.03 ``association_overall_direct/`` and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_associations.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_associations.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "association_overall_direct", OT_ASSOCIATIONS_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info("Wrote %d OT association rows to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )


@asset(group_name="ingest", compute_kind="python")
def ot_drugs_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Open Targets drug-target associations -> Bronze Parquet.

    Produces: Parquet of drug-target triples from ``clinical_target`` with
              clinical stage and associated disease list. Drug display names
              join via ``drug_molecule`` in the dbt Silver layer.
    Depends on: OT EBI FTP v26.03 ``clinical_target/clinical_target.parquet`` and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_drugs.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_drugs.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "clinical_target", OT_DRUGS_COLUMNS)

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


@asset(group_name="ingest", compute_kind="python")
def ot_drug_molecules_raw(context: AssetExecutionContext, r2: R2Resource) -> MaterializeResult[Any]:
    """Open Targets drug molecule metadata -> Bronze Parquet.

    Produces: Parquet of (id, name, drugType) rows from ``drug_molecule`` —
              preferred drug names and modality used to populate ``dim_drug``
              in the dbt Silver layer (drug display names are not in
              ``clinical_target``; they require this join).
    Depends on: OT EBI FTP v26.03 ``drug_molecule/`` dataset and R2.
    Lands at: r2://atlas-raw/opentargets/v26.03/ot_drug_molecules.parquet.
    """
    key = f"opentargets/v{OT_VERSION}/ot_drug_molecules.parquet"
    with httpx.Client(timeout=120.0) as client:
        df = fetch_dataset(client, "drug_molecule", OT_DRUG_MOLECULES_COLUMNS)

    r2.write_parquet(df, key)
    context.log.info("Wrote %d OT drug molecule rows to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "ot_version": MetadataValue.text(OT_VERSION),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )
