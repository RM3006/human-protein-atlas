"""Correctness tests for the STRING-DB ingest.

Tests check values, not just that the code runs (CLAUDE.md rule 7).
`resolve_string_ids` is tested as a pure function first; streaming helpers are
tested with a mocked HTTP layer so no network I/O occurs.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import httpx
import polars as pl

from atlas.assets.ingest.string import (
    RAW_SCHEMA,
    fetch_aliases,
    resolve_string_ids,
    stream_interactions,
)


def test_resolve_string_ids_keeps_only_ensembl_uniprot() -> None:
    alias_rows = [
        ("9606.ENSP1", "P01308", "Ensembl_UniProt"),
        ("9606.ENSP1", "INS", "HGNC"),  # different source -> ignored
        ("9606.ENSP2", "P06213", "Ensembl_UniProt"),
    ]
    result = resolve_string_ids(alias_rows)
    assert result == {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}


def test_resolve_string_ids_first_mapping_wins_on_duplicate_ensp() -> None:
    alias_rows = [
        ("9606.ENSP1", "P01308", "Ensembl_UniProt"),
        ("9606.ENSP1", "P99999", "Ensembl_UniProt"),  # duplicate ENSP, later -> ignored
    ]
    result = resolve_string_ids(alias_rows)
    assert result["9606.ENSP1"] == "P01308"


def test_resolve_string_ids_empty_input_returns_empty_dict() -> None:
    assert resolve_string_ids([]) == {}


def _gzip_text(text: str) -> bytes:
    return gzip.compress(text.encode())


def testfetch_aliases_builds_correct_mapping() -> None:
    aliases_gz = _gzip_text(
        "#string_protein_id\talias\tsource\n"
        "9606.ENSP1\tP01308\tEnsembl_UniProt\n"
        "9606.ENSP1\tINS\tHGNC\n"
        "9606.ENSP2\tP06213\tEnsembl_UniProt\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=aliases_gz)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        mapping = fetch_aliases(client)

    assert mapping == {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}


def teststream_interactions_filters_score_resolves_ids_drops_unmapped() -> None:
    links_gz = _gzip_text(
        "protein1 protein2 combined_score\n"
        "9606.ENSP1 9606.ENSP2 800\n"  # score >= 700, both mapped -> keep
        "9606.ENSP1 9606.ENSP2 600\n"  # score < 700 -> filter
        "9606.ENSP1 9606.ENSP3 900\n"  # ENSP3 not in id_map -> drop
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=links_gz)

    id_map = {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = stream_interactions(client, id_map)

    assert df.height == 1
    assert df.item(0, "uniprot_a") == "P01308"
    assert df.item(0, "uniprot_b") == "P06213"
    assert df.item(0, "combined_score") == 800


def teststream_interactions_returns_correct_schema() -> None:
    links_gz = _gzip_text(
        "protein1 protein2 combined_score\n"
        "9606.ENSP1 9606.ENSP2 750\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=links_gz)

    id_map = {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = stream_interactions(client, id_map)

    assert dict(df.schema) == RAW_SCHEMA


def teststream_interactions_empty_when_all_scores_below_threshold() -> None:
    links_gz = _gzip_text(
        "protein1 protein2 combined_score\n"
        "9606.ENSP1 9606.ENSP2 500\n"
        "9606.ENSP1 9606.ENSP2 300\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=links_gz)

    id_map = {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = stream_interactions(client, id_map)

    assert df.height == 0
    assert dict(df.schema) == RAW_SCHEMA


def teststream_interactions_round_trips_parquet(tmp_path: Path) -> None:
    # Verify the DataFrame can be serialised to Parquet and read back with
    # the same schema (CLAUDE.md rule 3: Parquet, not CSV, in pipelines).
    links_gz = _gzip_text(
        "protein1 protein2 combined_score\n"
        "9606.ENSP1 9606.ENSP2 850\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=links_gz)

    id_map = {"9606.ENSP1": "P01308", "9606.ENSP2": "P06213"}
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = stream_interactions(client, id_map)

    path = tmp_path / "string.parquet"
    df.write_parquet(path)
    back = pl.read_parquet(path)

    assert dict(back.schema) == RAW_SCHEMA
    assert back.item(0, "combined_score") == 850
