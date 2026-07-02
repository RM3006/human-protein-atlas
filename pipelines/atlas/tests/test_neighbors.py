"""Correctness tests for the precomputed neighbor table (pipelines/atlas/assets/ml/neighbors.py).

Tests only the pure helper — no MotherDuck connection required. Each test checks
values, not just "runs without error" (CLAUDE.md rule 7).
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from numpy.typing import NDArray

from atlas.assets.ml.neighbors import top_k_neighbors


def _row(vector: list[float]) -> NDArray[np.float32]:
    return np.array(vector, dtype=np.float32)


def test_excludes_self_and_ranks_by_similarity_descending() -> None:
    accessions = ["A", "B", "C"]
    embeddings = np.array(
        [
            _row([1.0, 0.0]),
            _row([0.9, 0.1]),  # closest to A
            _row([0.0, 1.0]),  # orthogonal to A
        ]
    )
    df = top_k_neighbors(accessions, embeddings, k=2)
    a_rows = df.filter(pl.col("uniprot_accession") == "A").sort("rank")

    assert a_rows["neighbor_accession"].to_list() == ["B", "C"]
    assert a_rows["rank"].to_list() == [1, 2]
    assert a_rows["similarity"][0] > a_rows["similarity"][1]
    assert "A" not in a_rows["neighbor_accession"].to_list()


def test_clips_k_to_available_neighbors() -> None:
    accessions = ["A", "B", "C"]
    embeddings = np.array([_row([1.0, 0.0]), _row([0.0, 1.0]), _row([1.0, 1.0])])
    df = top_k_neighbors(accessions, embeddings, k=20)

    assert df.filter(pl.col("uniprot_accession") == "A").height == 2  # n - 1
    assert df.height == 3 * 2


def test_similarity_is_cosine_not_euclidean() -> None:
    accessions = ["A", "B"]
    # Same direction, different magnitude -> cosine similarity is 1.0.
    embeddings = np.array([_row([3.0, 4.0]), _row([6.0, 8.0])])
    df = top_k_neighbors(accessions, embeddings, k=1)

    row = df.filter(pl.col("uniprot_accession") == "A")
    assert row["similarity"][0] == pytest.approx(1.0, abs=1e-5)  # pyright: ignore[reportUnknownMemberType]


def test_returns_expected_columns_and_row_count() -> None:
    accessions = ["A", "B", "C", "D"]
    embeddings = np.array([_row([1.0, 0.0]), _row([0.0, 1.0]), _row([1.0, 1.0]), _row([-1.0, 0.0])])
    df = top_k_neighbors(accessions, embeddings, k=2)

    assert set(df.columns) == {"uniprot_accession", "neighbor_accession", "similarity", "rank"}
    assert df.height == len(accessions) * 2
