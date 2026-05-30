"""Correctness tests for the Open Targets ingest.

Tests check values, not just that the code runs (CLAUDE.md rule 7). The HTTP
layer is fully mocked; fixture Parquet bytes are built in-memory from small
representative DataFrames.

Column names reflect Open Targets v26.03 schema:
  - associations: ``associationScore`` (not ``score``)
  - drugs: ``clinical_target`` layout (``drugId``, ``targetId``, ``diseases``, ``maxClinicalStage``)
  - disease: ``id``, ``name`` only (``therapeuticAreas`` removed in v26.03)
"""

from __future__ import annotations

import io

import httpx
import polars as pl
import pytest

from atlas.assets.ingest.opentargets import (
    OT_ASSOCIATIONS_COLUMNS,
    OT_DRUGS_COLUMNS,
    OT_TARGETS_COLUMNS,
    fetch_dataset,
    list_parts,
)


def _parquet_bytes(df: pl.DataFrame) -> bytes:
    buf = io.BytesIO()
    df.write_parquet(buf)
    return buf.getvalue()


def _dir_html(filenames: list[str]) -> bytes:
    links = "".join(f'<a href="{n}">{n}</a>' for n in filenames)
    return f"<html><body>{links}</body></html>".encode()


# ---------------------------------------------------------------------------
# list_parts
# ---------------------------------------------------------------------------


def test_list_parts_extracts_partitioned_filenames() -> None:
    html = _dir_html(["part-00000.parquet", "part-00001.snappy.parquet"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        names = list_parts(client, "https://ftp.example.com/dataset/")

    assert names == ["part-00000.parquet", "part-00001.snappy.parquet"]


def test_list_parts_extracts_single_file_datasets() -> None:
    # v26.03 datasets like disease/ and clinical_target/ are single files.
    html = _dir_html(["disease.parquet"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        names = list_parts(client, "https://ftp.example.com/disease/")

    assert names == ["disease.parquet"]


def test_list_parts_returns_empty_when_no_parquet_files() -> None:
    html = b'<html><body><a href="readme.txt">readme.txt</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        names = list_parts(client, "https://ftp.example.com/dataset/")

    assert names == []


# ---------------------------------------------------------------------------
# fetch_dataset
# ---------------------------------------------------------------------------


def test_fetch_dataset_concatenates_two_parts() -> None:
    part0 = _parquet_bytes(
        pl.DataFrame({"diseaseId": ["EFO_1"], "targetId": ["ENSG1"], "associationScore": [0.9]})
    )
    part1 = _parquet_bytes(
        pl.DataFrame({"diseaseId": ["EFO_2"], "targetId": ["ENSG2"], "associationScore": [0.5]})
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/"):
            parts = ["part-00000.parquet", "part-00001.parquet"]
            return httpx.Response(200, content=_dir_html(parts))
        if "part-00000" in url:
            return httpx.Response(200, content=part0)
        if "part-00001" in url:
            return httpx.Response(200, content=part1)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = fetch_dataset(client, "association_overall_direct", OT_ASSOCIATIONS_COLUMNS)

    assert df.height == 2
    assert list(df["targetId"]) == ["ENSG1", "ENSG2"]
    assert list(df["associationScore"]) == [0.9, 0.5]


def test_fetch_dataset_handles_single_file_dataset() -> None:
    # Mirrors the disease/ and clinical_target/ layout in v26.03.
    single_file = _parquet_bytes(
        pl.DataFrame({"id": ["EFO_0001359"], "name": ["type 1 diabetes mellitus"]})
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/"):
            return httpx.Response(200, content=_dir_html(["disease.parquet"]))
        return httpx.Response(200, content=single_file)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = fetch_dataset(client, "disease", ["id", "name"])

    assert df.height == 1
    assert df.item(0, "id") == "EFO_0001359"


def test_fetch_dataset_raises_when_no_files_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html><body></body></html>")

    transport = httpx.MockTransport(handler)
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(RuntimeError, match="No Parquet files found"),
    ):
        fetch_dataset(client, "bogusDataset", OT_ASSOCIATIONS_COLUMNS)


def test_fetch_dataset_selects_only_requested_columns() -> None:
    part = _parquet_bytes(
        pl.DataFrame(
            {
                "diseaseId": ["EFO_1"],
                "targetId": ["ENSG1"],
                "associationScore": [0.8],
                "extra_column": ["should_be_dropped"],
            }
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/"):
            return httpx.Response(200, content=_dir_html(["part-00000.parquet"]))
        return httpx.Response(200, content=part)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = fetch_dataset(client, "association_overall_direct", OT_ASSOCIATIONS_COLUMNS)

    assert "extra_column" not in df.columns
    assert set(df.columns) == set(OT_ASSOCIATIONS_COLUMNS)


# ---------------------------------------------------------------------------
# Dataset-specific column correctness
# ---------------------------------------------------------------------------


def test_targets_dataset_columns_are_present() -> None:
    part = _parquet_bytes(
        pl.DataFrame(
            {
                "id": ["ENSG00000254647"],
                "approvedSymbol": ["INS"],
                "approvedName": ["insulin"],
                "proteinIds": [None],
                "extra": ["drop_me"],
            }
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/"):
            return httpx.Response(200, content=_dir_html(["part-00000.parquet"]))
        return httpx.Response(200, content=part)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = fetch_dataset(client, "target", OT_TARGETS_COLUMNS)

    assert df.item(0, "id") == "ENSG00000254647"
    assert df.item(0, "approvedSymbol") == "INS"
    assert "extra" not in df.columns


def test_drugs_dataset_columns_are_present() -> None:
    # v26.03 clinical_target layout: drugId, targetId, diseases (list), maxClinicalStage.
    part = _parquet_bytes(
        pl.DataFrame(
            {
                "drugId": ["CHEMBL1201631"],
                "targetId": ["ENSG00000254647"],
                "diseases": [["EFO_0001359"]],
                "maxClinicalStage": [4],
            }
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/"):
            return httpx.Response(200, content=_dir_html(["clinical_target.parquet"]))
        return httpx.Response(200, content=part)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        df = fetch_dataset(client, "clinical_target", OT_DRUGS_COLUMNS)

    assert df.item(0, "drugId") == "CHEMBL1201631"
    assert df.item(0, "maxClinicalStage") == 4
    assert set(df.columns) == set(OT_DRUGS_COLUMNS)
