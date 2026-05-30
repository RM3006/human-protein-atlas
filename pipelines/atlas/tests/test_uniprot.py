"""Correctness tests for the UniProt ingest.

These check values, not just that the code runs (CLAUDE.md rule 7): the insulin
fixture asserts every flattened field, and the pagination test confirms the
cursor walk stitches multiple pages together using a mocked HTTP layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from atlas.assets.ingest.uniprot import (
    RAW_SCHEMA,
    build_dataframe,
    fetch_all,
    parse_entry,
)

FIXTURE = Path(__file__).parent / "fixtures" / "uniprot_insulin.json"


def _insulin() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_entry_flattens_every_field() -> None:
    row = parse_entry(_insulin())

    assert row["primary_accession"] == "P01308"
    assert row["secondary_accessions"] == ["Q5EEX2"]
    assert row["gene_symbol"] == "INS"
    assert row["protein_name"] == "Insulin"
    assert row["sequence_length"] == 110
    assert row["sequence"].startswith("MALWMRLLPL")
    assert len(row["sequence"]) == 110
    assert "blood glucose" in row["function_raw"]
    assert row["keywords"] == ["Diabetes mellitus", "Hormone"]
    assert row["pfam_ids"] == ["PF00049"]
    assert row["ensembl_gene_ids"] == ["ENSG00000254647"]
    assert row["string_ids"] == ["9606.ENSP00000250971"]


def test_parse_entry_missing_fields_become_null() -> None:
    # An entry with no genes / function / xrefs must yield nulls and empty lists,
    # never invented data (CLAUDE.md rule 5).
    row = parse_entry({"primaryAccession": "X00001", "sequence": {"value": "MK", "length": 2}})

    assert row["primary_accession"] == "X00001"
    assert row["gene_symbol"] is None
    assert row["protein_name"] is None
    assert row["function_raw"] is None
    assert row["secondary_accessions"] == []
    assert row["keywords"] == []
    assert row["pfam_ids"] == []
    assert row["ensembl_gene_ids"] == []
    assert row["string_ids"] == []


def test_build_dataframe_has_expected_schema() -> None:
    df = build_dataframe([_insulin()])

    assert df.height == 1
    assert dict(df.schema) == RAW_SCHEMA
    assert df.item(0, "primary_accession") == "P01308"


def test_fetch_all_walks_every_cursor_page() -> None:
    page_two_url = "https://rest.uniprot.org/uniprotkb/search?cursor=abc&format=json&size=2"

    def handler(request: httpx.Request) -> httpx.Response:
        if "cursor" not in request.url.query.decode():
            # First page: two results + a Link header pointing at page two.
            return httpx.Response(
                200,
                headers={
                    "x-uniprot-release": "2025_01",
                    "Link": f'<{page_two_url}>; rel="next"',
                },
                json={"results": [{"primaryAccession": "P1"}, {"primaryAccession": "P2"}]},
            )
        # Second (final) page: one result, no Link header.
        return httpx.Response(200, json={"results": [{"primaryAccession": "P3"}]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        entries, release = fetch_all(client, page_size=2)

    assert [e["primaryAccession"] for e in entries] == ["P1", "P2", "P3"]
    assert release == "2025_01"


def test_fetch_all_retries_then_succeeds(monkeypatch: Any) -> None:
    import atlas.assets.ingest.uniprot as mod

    def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(mod.time, "sleep", _no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [{"primaryAccession": "P1"}]})

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        entries, _ = fetch_all(client, page_size=2)

    assert calls["n"] == 2
    assert [e["primaryAccession"] for e in entries] == ["P1"]


def test_dataframe_can_round_trip_parquet(tmp_path: Path) -> None:
    df = build_dataframe([_insulin()])
    path = tmp_path / "uniprot.parquet"
    df.write_parquet(path)

    back = pl.read_parquet(path)
    assert dict(back.schema) == RAW_SCHEMA
    assert back.item(0, "ensembl_gene_ids").to_list() == ["ENSG00000254647"]
