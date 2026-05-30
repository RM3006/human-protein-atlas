"""UniProt ingest: reviewed human proteins -> Bronze Parquet in R2.

UniProt is the anchor of the whole atlas; its accession is the join key for every
other source (CLAUDE.md rule 1). This module pulls the ~20k reviewed human
(Swiss-Prot, taxonomy 9606) entries from the UniProt REST API, flattens the
fields listed in the data-source manifest, and lands one Parquet file in R2.
"""

import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
import polars as pl
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from atlas.logging import logger
from atlas.resources.r2 import R2Resource

SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"
QUERY = "reviewed:true AND organism_id:9606"
PAGE_SIZE = 500
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_RETRIES = 4

# Explicit Bronze schema. Lists keep every cross-reference; the dbt staging layer
# later picks the canonical single value. Missing field -> null (CLAUDE.md rule 5).
RAW_SCHEMA: dict[str, pl.DataType] = {
    "primary_accession": pl.String(),
    "secondary_accessions": pl.List(pl.String()),
    "gene_symbol": pl.String(),
    "protein_name": pl.String(),
    "sequence_length": pl.Int64(),
    "sequence": pl.String(),
    "function_raw": pl.String(),
    "keywords": pl.List(pl.String()),
    "pfam_ids": pl.List(pl.String()),
    "ensembl_gene_ids": pl.List(pl.String()),
    "string_ids": pl.List(pl.String()),
}


def _unique(values: Iterable[str]) -> list[str]:
    """De-duplicate while preserving first-seen order."""
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v, None)
    return list(seen)


def parse_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Flatten one UniProtKB JSON entry into a Bronze row.

    Pure and side-effect free so it can be unit-tested against a fixture without
    touching the network.
    """
    gene_symbol: str | None = None
    genes: list[Any] = entry.get("genes") or []
    if genes:
        gene_name: dict[str, Any] = genes[0].get("geneName") or {}
        gene_symbol = gene_name.get("value")

    protein_name: str | None = None
    description: dict[str, Any] = entry.get("proteinDescription") or {}
    recommended: dict[str, Any] = description.get("recommendedName") or {}
    full_name: dict[str, Any] = recommended.get("fullName") or {}
    protein_name = full_name.get("value")
    if protein_name is None:
        submissions: list[Any] = description.get("submissionNames") or []
        if submissions:
            sub_full_name: dict[str, Any] = submissions[0].get("fullName") or {}
            protein_name = sub_full_name.get("value")

    sequence: dict[str, Any] = entry.get("sequence") or {}

    function_raw: str | None = None
    comments: list[Any] = entry.get("comments") or []
    for comment in comments:
        if comment.get("commentType") == "FUNCTION":
            texts: list[Any] = comment.get("texts") or []
            if texts:
                function_raw = texts[0].get("value")
            break

    keywords_raw: list[Any] = entry.get("keywords") or []
    keywords: list[str] = [kw["name"] for kw in keywords_raw if kw.get("name")]

    pfam_ids: list[str] = []
    ensembl_gene_ids: list[str] = []
    string_ids: list[str] = []
    xrefs: list[Any] = entry.get("uniProtKBCrossReferences") or []
    for xref in xrefs:
        database: str | None = xref.get("database")
        xref_id: str | None = xref.get("id")
        if database == "Pfam" and xref_id:
            pfam_ids.append(xref_id)
        elif database == "STRING" and xref_id:
            string_ids.append(xref_id)
        elif database == "Ensembl":
            properties: list[Any] = xref.get("properties") or []
            for prop in properties:
                if prop.get("key") == "GeneId" and prop.get("value"):
                    ensembl_gene_ids.append(prop["value"])

    return {
        "primary_accession": entry["primaryAccession"],
        "secondary_accessions": list(entry.get("secondaryAccessions") or []),
        "gene_symbol": gene_symbol,
        "protein_name": protein_name,
        "sequence_length": sequence.get("length"),
        "sequence": sequence.get("value"),
        "function_raw": function_raw,
        "keywords": keywords,
        "pfam_ids": _unique(pfam_ids),
        "ensembl_gene_ids": _unique(ensembl_gene_ids),
        "string_ids": _unique(string_ids),
    }


def build_dataframe(entries: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse every entry and assemble the typed Bronze DataFrame."""
    rows = [parse_entry(e) for e in entries]
    return pl.DataFrame(rows, schema=RAW_SCHEMA, orient="row")


def _get_with_retry(client: httpx.Client, url: str) -> httpx.Response:
    """GET with exponential backoff on transient UniProt errors."""
    for attempt in range(1, MAX_RETRIES + 1):
        response = client.get(url)
        if response.status_code not in RETRY_STATUSES:
            response.raise_for_status()
            return response
        if attempt == MAX_RETRIES:
            response.raise_for_status()
        backoff = 2.0**attempt
        logger.warning(
            "UniProt returned %s; retrying in %.0fs (attempt %d/%d)",
            response.status_code,
            backoff,
            attempt,
            MAX_RETRIES,
        )
        time.sleep(backoff)
    raise RuntimeError("unreachable")  # pragma: no cover


def fetch_all(
    client: httpx.Client, *, page_size: int = PAGE_SIZE
) -> tuple[list[dict[str, Any]], str | None]:
    """Walk every cursor page of the reviewed-human query.

    Returns the raw entry dicts and the UniProt release string (from the
    ``x-uniprot-release`` response header). Pagination follows the ``next`` Link
    header serially -- UniProt cursors must not be parallelized.
    """
    url = httpx.URL(SEARCH_URL, params={"query": QUERY, "format": "json", "size": page_size})
    entries: list[dict[str, Any]] = []
    release: str | None = None

    while True:
        response = _get_with_retry(client, str(url))
        if release is None:
            release = response.headers.get("x-uniprot-release")
        entries.extend(response.json().get("results", []))
        next_link = response.links.get("next")
        if not next_link:
            break
        url = httpx.URL(next_link["url"])

    return entries, release


def _release_tag(release: str | None) -> str:
    """Release tag for the R2 path; fall back to the ingest month if absent."""
    return release or datetime.now(UTC).strftime("%Y_%m")


@asset(group_name="ingest", compute_kind="python")
def uniprot_human_reviewed_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Reviewed human UniProt entries, flattened to Bronze Parquet.

    Produces: ~20k-row Parquet of the manifest's UniProt fields.
    Depends on: the UniProt REST API and the R2 resource.
    Lands at: ``r2://atlas-raw/uniprot/v{release}/uniprot_human_reviewed_raw.parquet``.
    """
    with httpx.Client(timeout=60.0, headers={"Accept": "application/json"}) as client:
        entries, release = fetch_all(client)

    df = build_dataframe(entries)
    tag = _release_tag(release)
    key = f"uniprot/v{tag}/uniprot_human_reviewed_raw.parquet"
    r2.write_parquet(df, key)

    context.log.info("Wrote %d UniProt records to r2://%s/%s", df.height, r2.bucket, key)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "uniprot_release": MetadataValue.text(tag),
            "r2_key": MetadataValue.text(key),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )
