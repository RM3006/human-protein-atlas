"""Correctness tests for the ML embedding layer.

Tests only pure helper functions — no Modal GPU or MotherDuck connections
required. Each test checks values, not just "runs without error" (CLAUDE.md rule 7).
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from numpy.typing import NDArray

from atlas.assets.ml.embeddings import (
    EMBEDDING_DIM,
    MODEL_VERSION,
    accession_to_id,
    build_embedding_df,
    chunk,
)
from atlas.assets.ml.modal_esm2 import (
    MAX_SEQ_LEN,
    truncate_sequence,
)

# --- truncate_sequence ---


def test_truncate_short_sequence_unchanged() -> None:
    seq, flag = truncate_sequence("MKLLVV", max_len=10)
    assert seq == "MKLLVV"
    assert flag is False


def test_truncate_sequence_at_exact_limit_unchanged() -> None:
    seq, flag = truncate_sequence("M" * 10, max_len=10)
    assert seq == "M" * 10
    assert flag is False


def test_truncate_long_sequence_clips_and_flags() -> None:
    long_seq = "ACDEF" * 300  # 1500 residues
    seq, flag = truncate_sequence(long_seq, max_len=MAX_SEQ_LEN)
    assert len(seq) == MAX_SEQ_LEN
    assert seq == long_seq[:MAX_SEQ_LEN]
    assert flag is True


def test_truncate_uses_default_max_seq_len() -> None:
    # Verify the default matches the ESM-2 constant.
    short = "MKTII"
    seq, flag = truncate_sequence(short)
    assert seq == short
    assert flag is False


# --- chunk ---


def test_chunk_splits_evenly() -> None:
    assert chunk(list(range(6)), 2) == [[0, 1], [2, 3], [4, 5]]


def test_chunk_handles_remainder() -> None:
    chunks = chunk(list(range(10)), 3)
    assert chunks == [[0, 1, 2], [3, 4, 5], [6, 7, 8], [9]]


def test_chunk_smaller_than_size_returns_one_chunk() -> None:
    assert chunk([1, 2], 100) == [[1, 2]]


def test_chunk_empty_list() -> None:
    assert chunk([], 10) == []


# --- accession_to_id ---


def test_accession_to_id_is_deterministic() -> None:
    assert accession_to_id("P00533") == accession_to_id("P00533")


def test_accession_to_id_is_positive() -> None:
    for acc in ["P00533", "P01308", "P04637", "Q9Y463"]:
        assert accession_to_id(acc) > 0


def test_accession_to_id_distinct_accessions_differ() -> None:
    egfr = accession_to_id("P00533")
    insulin = accession_to_id("P01308")
    tp53 = accession_to_id("P04637")
    assert len({egfr, insulin, tp53}) == 3


# --- build_embedding_df ---


def _make_embedding_inputs(
    n: int = 3,
) -> tuple[list[str], NDArray[np.float32], NDArray[np.float32], list[bool], datetime]:
    accessions = [f"P{i:05d}" for i in range(n)]
    embeddings: NDArray[np.float32] = np.zeros((n, EMBEDDING_DIM), dtype=np.float32)
    coords: NDArray[np.float32] = np.tile(
        np.arange(n, dtype=np.float32).reshape(-1, 1), (1, 2)
    )
    was_truncated = [i % 2 == 0 for i in range(n)]
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return accessions, embeddings, coords, was_truncated, now


def test_build_embedding_df_row_count() -> None:
    accessions, embeddings, coords, flags, now = _make_embedding_inputs(5)
    df = build_embedding_df(accessions, embeddings, coords, flags, now)
    assert df.height == 5


def test_build_embedding_df_columns_present() -> None:
    accessions, embeddings, coords, flags, now = _make_embedding_inputs()
    df = build_embedding_df(accessions, embeddings, coords, flags, now)
    assert set(df.columns) == {
        "uniprot_accession",
        "embedding",
        "umap_x",
        "umap_y",
        "model_version",
        "was_truncated",
        "computed_at",
    }


def test_build_embedding_df_embedding_length() -> None:
    accessions, embeddings, coords, flags, now = _make_embedding_inputs()
    df = build_embedding_df(accessions, embeddings, coords, flags, now)
    for row_emb in df["embedding"].to_list():
        assert len(row_emb) == EMBEDDING_DIM


def test_build_embedding_df_values() -> None:
    accessions, embeddings, coords, flags, now = _make_embedding_inputs(2)
    embeddings[1, 0] = 0.5
    df = build_embedding_df(accessions, embeddings, coords, flags, now)

    assert df.item(0, "uniprot_accession") == "P00000"
    assert df.item(1, "uniprot_accession") == "P00001"
    assert df.item(0, "model_version") == MODEL_VERSION
    assert df.item(0, "was_truncated") is True
    assert df.item(1, "was_truncated") is False
    assert df.item(1, "embedding")[0] == pytest.approx(  # pyright: ignore[reportUnknownMemberType]
        0.5, abs=1e-6
    )
    assert df.item(0, "umap_x") == pytest.approx(0.0, abs=1e-6)  # pyright: ignore[reportUnknownMemberType]
    assert df.item(1, "umap_x") == pytest.approx(1.0, abs=1e-6)  # pyright: ignore[reportUnknownMemberType]
