"""Dagster asset: precomputed nearest-neighbor table from ESM-2 embeddings.

Replaces a live Qdrant ANN lookup with an offline exact top-k cosine-similarity
computation over fact_embedding, stored back in MotherDuck. At ~20k proteins, exact
brute-force is both fast enough to precompute once and more accurate than an
approximate index, and the UI then does a plain indexed SQL lookup instead of a
network call to a service that can go to sleep.

Produces: MotherDuck atlas.main.fact_protein_neighbor.
Depends on: fact_embedding (MotherDuck), written by the protein_embeddings asset.
"""

# Omit 'from __future__ import annotations' — same Dagster 1.13.7 annotation bug
# as embeddings.py: PEP 563 lazy strings break inspect.signature when context is
# the only parameter.

import os
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset
from numpy.typing import NDArray

NEIGHBOR_K = 20


def top_k_neighbors(
    accessions: list[str],
    embeddings: NDArray[np.float32],
    k: int = NEIGHBOR_K,
) -> pl.DataFrame:
    """Exact top-k cosine-similarity neighbors for every row, excluding itself.

    One full (n x n) similarity matrix computed in a single matmul — fine at this
    dataset's ~20k-row scale, but would need row-block chunking well beyond it.
    """
    k = min(k, len(accessions) - 1)
    normalized = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)
    similarity = normalized @ normalized.T

    src: list[str] = []
    dst: list[str] = []
    sim: list[float] = []
    rank: list[int] = []
    for i, accession in enumerate(accessions):
        row = similarity[i]
        row[i] = -np.inf  # exclude self
        top_idx = np.argpartition(row, -k)[-k:]
        top_idx = top_idx[np.argsort(row[top_idx])[::-1]]
        for r, j in enumerate(top_idx, start=1):
            src.append(accession)
            dst.append(accessions[int(j)])
            sim.append(float(row[j]))
            rank.append(r)

    return pl.DataFrame(
        {
            "uniprot_accession": src,
            "neighbor_accession": dst,
            "similarity": sim,
            "rank": rank,
        },
        schema={
            "uniprot_accession": pl.Utf8,
            "neighbor_accession": pl.Utf8,
            "similarity": pl.Float32,
            "rank": pl.Int32,
        },
    )


@asset(group_name="ml", compute_kind="duckdb")
def protein_neighbors(context: AssetExecutionContext) -> MaterializeResult[Any]:
    """Top-20 cosine-similarity neighbors for every protein in fact_embedding.

    Produces: fact_protein_neighbor in MotherDuck atlas.main.
    Depends on: fact_embedding (MotherDuck), written by protein_embeddings — run that
        asset first.
    Lands at: MotherDuck atlas.main.fact_protein_neighbor.
    """
    token = os.environ["MOTHERDUCK_TOKEN"]
    conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

    rows = conn.execute(
        "SELECT uniprot_accession, embedding FROM fact_embedding ORDER BY uniprot_accession"
    ).fetchall()
    accessions: list[str] = [r[0] for r in rows]
    embeddings = np.array([r[1] for r in rows], dtype=np.float32)

    context.log.info("Computing top-%d neighbors for %d proteins …", NEIGHBOR_K, len(accessions))
    df = top_k_neighbors(accessions, embeddings, k=NEIGHBOR_K)
    context.log.info("Computed %d neighbor rows", df.height)

    _fd, _tmp = tempfile.mkstemp(suffix=".parquet")
    os.close(_fd)
    tmp_parquet = Path(_tmp)
    try:
        df.write_parquet(tmp_parquet)
        parquet_path = tmp_parquet.as_posix()
        conn.execute(
            f"CREATE OR REPLACE TABLE fact_protein_neighbor AS "
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )
    finally:
        tmp_parquet.unlink(missing_ok=True)
    context.log.info("Written %d rows to MotherDuck fact_protein_neighbor", df.height)

    return MaterializeResult(
        metadata={
            "num_proteins": MetadataValue.int(len(accessions)),
            "k": MetadataValue.int(NEIGHBOR_K),
            "num_rows": MetadataValue.int(df.height),
        }
    )
