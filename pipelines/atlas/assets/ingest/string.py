"""STRING-DB interactions -> Bronze Parquet in R2.

Downloads the human protein-protein interaction network (v12.0), maps the native
ENSP IDs to UniProt accessions via the aliases file (the #1 gotcha in this
project per ROADMAP.md Part 2 risks), filters to combined_score >= 700, and
lands (uniprot_a, uniprot_b, combined_score) triplets in R2.

`resolve_string_ids` is a pure function tested independently before integration.
The links file (~190 MB compressed, ~1.6 GB uncompressed) is streamed line-by-line
so it is never fully decompressed in memory.
"""

import gzip
import io
from collections.abc import Iterator
from typing import Any

import httpx
import polars as pl
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset

from atlas.logging import logger
from atlas.resources.r2 import R2Resource

STRING_VERSION = "12.0"
_SPECIES = "9606"
_DL_BASE = "https://stringdb-downloads.org/download"
ALIASES_URL = (
    f"{_DL_BASE}/protein.aliases.v{STRING_VERSION}/"
    f"{_SPECIES}.protein.aliases.v{STRING_VERSION}.txt.gz"
)
LINKS_URL = (
    f"{_DL_BASE}/protein.links.v{STRING_VERSION}/{_SPECIES}.protein.links.v{STRING_VERSION}.txt.gz"
)
SCORE_THRESHOLD = 700
R2_KEY = f"string/v{STRING_VERSION}/string_interactions.parquet"

RAW_SCHEMA: dict[str, pl.DataType] = {
    "uniprot_a": pl.String(),
    "uniprot_b": pl.String(),
    "combined_score": pl.Int32(),
}


def resolve_string_ids(alias_rows: list[tuple[str, str, str]]) -> dict[str, str]:
    """Map STRING ENSP IDs to UniProt accessions.

    Filters to rows where ``source == "Ensembl_UniProt"``, keeping only the
    first mapping seen per ENSP ID (canonical form).

    Args:
        alias_rows: list of (string_protein_id, alias, source) tuples from the
            STRING aliases file.

    Returns:
        dict mapping ENSP ID (e.g. ``9606.ENSP00000250971``) to UniProt
        accession (e.g. ``P01308``).
    """
    mapping: dict[str, str] = {}
    for ensp, alias, source in alias_rows:
        if source == "Ensembl_UniProt" and ensp not in mapping:
            mapping[ensp] = alias
    return mapping


def fetch_aliases(client: httpx.Client) -> dict[str, str]:
    """Download the STRING aliases file and return the ENSP -> UniProt mapping."""
    response = client.get(
        ALIASES_URL, follow_redirects=True, headers={"Accept-Encoding": "identity"}
    )
    response.raise_for_status()
    rows: list[tuple[str, str, str]] = []
    with gzip.open(io.BytesIO(response.content)) as gz:
        next(gz)  # skip header: "#string_protein_id\talias\tsource"
        for line in gz:
            parts = line.decode().rstrip("\n").split("\t")
            if len(parts) >= 3:
                rows.append((parts[0], parts[1], parts[2]))
    return resolve_string_ids(rows)


class _IterBytesIO(io.RawIOBase):
    """Wrap httpx's ``iter_bytes()`` as a ``RawIOBase`` so ``gzip.open`` can read it.

    This enables streaming gzip decompression from an HTTP response without
    buffering the entire compressed body in memory.
    """

    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks
        self._buf = b""

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray) -> int:  # pyright: ignore[reportIncompatibleMethodOverride]
        while not self._buf:
            try:
                self._buf = next(self._chunks)
            except StopIteration:
                return 0
        n = min(len(b), len(self._buf))
        b[:n] = self._buf[:n]
        self._buf = self._buf[n:]
        return n


def stream_interactions(client: httpx.Client, id_map: dict[str, str]) -> pl.DataFrame:
    """Stream the STRING links file and return resolved, filtered interactions.

    Resolves both ENSP endpoints to UniProt accessions via ``id_map``. Rows
    where either endpoint has no mapping are silently dropped (unmapped proteins
    are not in Swiss-Prot and are out of scope for the atlas).
    """
    rows: list[tuple[str, str, int]] = []
    unmapped = 0
    with client.stream(
        "GET",
        LINKS_URL,
        follow_redirects=True,
        timeout=300.0,
        headers={"Accept-Encoding": "identity"},
    ) as response:
        response.raise_for_status()
        raw = _IterBytesIO(response.iter_bytes(chunk_size=65536))
        with gzip.open(io.BufferedReader(raw)) as gz:  # pyright: ignore[reportArgumentType]
            next(gz)  # skip header: "protein1 protein2 combined_score"
            for line in gz:
                parts = line.decode().split()
                if len(parts) < 3:
                    continue
                score = int(parts[2])
                if score < SCORE_THRESHOLD:
                    continue
                ua = id_map.get(parts[0])
                ub = id_map.get(parts[1])
                if ua and ub:
                    rows.append((ua, ub, score))
                else:
                    unmapped += 1

    if unmapped:
        logger.warning("STRING: %d high-confidence edges had no UniProt mapping", unmapped)

    return pl.DataFrame(
        {
            "uniprot_a": [r[0] for r in rows],
            "uniprot_b": [r[1] for r in rows],
            "combined_score": [r[2] for r in rows],
        },
        schema=RAW_SCHEMA,
    )


@asset(group_name="ingest", compute_kind="python")
def string_interactions_raw(
    context: AssetExecutionContext, r2: R2Resource
) -> MaterializeResult[Any]:
    """Human protein-protein interactions from STRING-DB, resolved to UniProt.

    Produces: Parquet of (uniprot_a, uniprot_b, combined_score) triplets with
              combined_score >= 700 and both endpoints mapped to UniProt accessions.
    Depends on: STRING-DB bulk download files v12.0 and the R2 resource.
    Lands at: r2://atlas-raw/string/v12.0/string_interactions.parquet.
    """
    with httpx.Client(timeout=120.0) as client:
        logger.info("Fetching STRING aliases (%s)...", ALIASES_URL)
        id_map = fetch_aliases(client)
        context.log.info("STRING aliases loaded: %d ENSP -> UniProt mappings", len(id_map))

        logger.info("Streaming STRING interactions (%s)...", LINKS_URL)
        df = stream_interactions(client, id_map)

    r2.write_parquet(df, R2_KEY)
    context.log.info("Wrote %d interactions to r2://%s/%s", df.height, r2.bucket, R2_KEY)
    return MaterializeResult(
        metadata={
            "num_records": MetadataValue.int(df.height),
            "string_version": MetadataValue.text(STRING_VERSION),
            "r2_key": MetadataValue.text(R2_KEY),
            "preview": MetadataValue.md(f"```\n{df.head(5)}\n```"),
        }
    )
