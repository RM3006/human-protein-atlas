"""Correctness tests for the Human Protein Atlas ingest.

Tests check values, not just that the code runs (CLAUDE.md rule 7). The
`parse_hpa_tsv` function is tested directly against fixture bytes so no network
I/O occurs in the test suite.
"""

from __future__ import annotations

import io
import zipfile

import polars as pl

from atlas.assets.ingest.hpa import RAW_SCHEMA, parse_hpa_tsv

# Minimal HPA TSV with two proteins (insulin appears twice to test deduplication).
_HPA_TSV_BYTES = (
    b"Gene\tUniprot\tProtein class\tTissue expression\t"
    b"RNA tissue specificity\tRNA tissue distribution\t"
    b"Subcellular location\tDisease involvement\n"
    b"INS\tP01308\tPredicted secreted proteins\tTissue enhanced (pancreas)\t"
    b"Tissue enhanced\tDetected in single\tVesicles, Golgi apparatus\tDiabetes mellitus\n"
    b"EGFR\tP00533\tCD markers\tLow tissue specificity\t"
    b"Low tissue specificity\tDetected in many\tPlasma membrane\tNon-small cell lung carcinoma\n"
    b"INS\tP01308\tDuplicate row\tDuplicate\tDuplicate\tDuplicate\tDuplicate\tDuplicate\n"
)

# TSV with a row missing a UniProt accession.
_HPA_TSV_NULL_UNIPROT = (
    b"Gene\tUniprot\tProtein class\tTissue expression\t"
    b"RNA tissue specificity\tRNA tissue distribution\t"
    b"Subcellular location\tDisease involvement\n"
    b"UNKN\t\tUncharacterized\tNot detected\tNot detected\tNot detected\t\t\n"
)


def test_parse_hpa_tsv_flattens_insulin_row() -> None:
    df = parse_hpa_tsv(_HPA_TSV_BYTES)

    insulin = df.filter(pl.col("uniprot_accession") == "P01308")  # pyright: ignore[reportUnknownMemberType]
    assert insulin.height == 1
    assert insulin.item(0, "gene_symbol") == "INS"
    assert insulin.item(0, "tissue_expression") == "Tissue enhanced (pancreas)"
    assert insulin.item(0, "subcellular_location") == "Vesicles, Golgi apparatus"
    assert insulin.item(0, "disease_involvement") == "Diabetes mellitus"


def test_parse_hpa_tsv_deduplicates_on_uniprot_keeping_first() -> None:
    df = parse_hpa_tsv(_HPA_TSV_BYTES)

    # INS appears twice; after dedup, only one row with the first protein_class.
    assert df.filter(pl.col("uniprot_accession") == "P01308").height == 1  # pyright: ignore[reportUnknownMemberType]
    assert (
        df.filter(pl.col("uniprot_accession") == "P01308").item(0, "protein_class")  # pyright: ignore[reportUnknownMemberType]
        == "Predicted secreted proteins"
    )


def test_parse_hpa_tsv_produces_expected_row_count() -> None:
    df = parse_hpa_tsv(_HPA_TSV_BYTES)
    # 3 raw rows, 2 unique uniprot_accessions -> 2 rows after dedup
    assert df.height == 2


def test_parse_hpa_tsv_has_expected_schema() -> None:
    df = parse_hpa_tsv(_HPA_TSV_BYTES)
    assert dict(df.schema) == RAW_SCHEMA


def test_parse_hpa_tsv_null_uniprot_becomes_null() -> None:
    df = parse_hpa_tsv(_HPA_TSV_NULL_UNIPROT)

    assert df.height == 1
    assert df.item(0, "uniprot_accession") is None
    assert df.item(0, "gene_symbol") == "UNKN"


def test_parse_hpa_tsv_null_fields_stay_null() -> None:
    tsv = (
        b"Gene\tUniprot\tProtein class\tTissue expression\t"
        b"RNA tissue specificity\tRNA tissue distribution\t"
        b"Subcellular location\tDisease involvement\n"
        b"TP53\tP04637\t\tLow tissue specificity\t\t\t\t\n"
    )
    df = parse_hpa_tsv(tsv)

    assert df.item(0, "protein_class") is None
    assert df.item(0, "rna_tissue_specificity") is None


def _make_hpa_zip(tsv_bytes: bytes) -> bytes:
    """Wrap TSV bytes in a zip archive the way HPA distributes the file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("proteinatlas.tsv", tsv_bytes)
    return buf.getvalue()


def test_hpa_asset_parse_roundtrip_through_zip() -> None:
    # Verifies the zip-extraction path used by the real asset function.
    zip_bytes = _make_hpa_zip(_HPA_TSV_BYTES)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        tsv_names = [n for n in zf.namelist() if n.endswith(".tsv")]
        with zf.open(tsv_names[0]) as tsv:
            content = tsv.read()

    df = parse_hpa_tsv(content)
    assert df.height == 2
    assert df.item(0, "uniprot_accession") == "P01308"
