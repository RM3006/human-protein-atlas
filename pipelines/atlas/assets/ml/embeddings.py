"""Dagster asset: ESM-2 embeddings + UMAP projection.

Reads every protein sequence from MotherDuck dim_protein, batches through
Modal's GPU inference function, projects embeddings to 2D with UMAP, and
writes MotherDuck atlas.main.fact_embedding (uniprot_accession, embedding[], umap_x/y).
The protein_neighbors asset (neighbors.py) reads this table to precompute
nearest-neighbor lookups for the UI.
"""

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import modal
import numpy as np
import polars as pl
import umap  # pyright: ignore[reportMissingTypeStubs]
from dagster import AssetExecutionContext, MaterializeResult, MetadataValue, asset
from numpy.typing import NDArray

from atlas.logging import logger

MODAL_APP_NAME = "atlas-esm2"
MODAL_FN_NAME = "embed_batch"

EMBEDDING_DIM = 1280
BATCH_SIZE = 128
MODEL_VERSION = "esm2_t33_650M"


def chunk(items: list[Any], size: int) -> list[list[Any]]:
    """Split list into chunks of at most size, preserving order."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_embedding_df(
    accessions: list[str],
    embeddings: NDArray[np.float32],
    coords: NDArray[np.float32],
    was_truncated: list[bool],
    computed_at: datetime,
) -> pl.DataFrame:
    """Assemble the fact_embedding DataFrame from computed arrays."""
    return pl.DataFrame(
        {
            "uniprot_accession": accessions,
            "embedding": pl.Series(
                [row.tolist() for row in embeddings],
                dtype=pl.List(pl.Float32),
            ),
            "umap_x": pl.Series(coords[:, 0].tolist(), dtype=pl.Float32),
            "umap_y": pl.Series(coords[:, 1].tolist(), dtype=pl.Float32),
            "model_version": pl.Series([MODEL_VERSION] * len(accessions)),
            "was_truncated": was_truncated,
            "computed_at": pl.Series([computed_at] * len(accessions)),
        }
    )


@asset(group_name="ml", compute_kind="modal")
def protein_embeddings(context: AssetExecutionContext) -> MaterializeResult[Any]:
    """ESM-2 embeddings and UMAP 2D coords for all dim_protein rows.

    Produces: fact_embedding in MotherDuck atlas.main.
    Depends on: dim_protein (MotherDuck), embed_batch Modal GPU function.
    Lands at: MotherDuck atlas.main.fact_embedding.
    """
    token = os.environ["MOTHERDUCK_TOKEN"]
    conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

    rows = conn.execute(
        "SELECT uniprot_accession, sequence "
        "FROM dim_protein WHERE sequence IS NOT NULL ORDER BY uniprot_accession"
    ).fetchall()

    accessions: list[str] = [r[0] for r in rows]
    sequences: list[str] = [r[1] for r in rows]

    context.log.info("Read %d sequences from dim_protein", len(accessions))

    # --- Modal batch inference ---
    # Look up the deployed function by name so we get the hydrated remote handle,
    # not the local un-hydrated object that comes from a direct module import.
    embed_fn: Any = modal.Function.from_name(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        MODAL_APP_NAME, MODAL_FN_NAME
    )
    sequence_batches: list[list[str]] = chunk(sequences, BATCH_SIZE)
    all_embeddings: list[list[float]] = []
    all_truncated: list[bool] = []
    total_batches = len(sequence_batches)

    for batch_idx, batch_result in enumerate(  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        embed_fn.map(sequence_batches)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    ):
        for emb, truncated in batch_result:  # pyright: ignore[reportUnknownVariableType]
            all_embeddings.append(emb)  # pyright: ignore[reportUnknownArgumentType]
            all_truncated.append(truncated)  # pyright: ignore[reportUnknownArgumentType]
        logger.info("Modal batch %d/%d complete", batch_idx + 1, total_batches)

    n_truncated = sum(all_truncated)
    context.log.info("Inference done. %d/%d sequences truncated.", n_truncated, len(accessions))

    # --- UMAP projection ---
    embeddings_matrix = np.array(all_embeddings, dtype=np.float32)
    context.log.info("Running UMAP on %s matrix …", embeddings_matrix.shape)
    reducer = umap.UMAP(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        n_neighbors=15,
        min_dist=0.1,
        metric="cosine",
        random_state=42,
    )
    raw_coords = reducer.fit_transform(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        embeddings_matrix
    )
    coords_2d: NDArray[np.float32] = np.asarray(  # pyright: ignore[reportUnknownArgumentType]
        raw_coords, dtype=np.float32
    )
    context.log.info("UMAP done. Shape: %s", coords_2d.shape)

    # --- Write to MotherDuck ---
    # Write to a local temp Parquet, then CREATE TABLE AS SELECT * FROM read_parquet().
    # Polars writes Parquet natively (no pyarrow). DuckDB memory-maps the file and
    # sends it to MotherDuck in one round-trip — ~6 s vs 30+ min for executemany.
    now = datetime.now(UTC)
    df = build_embedding_df(accessions, embeddings_matrix, coords_2d, all_truncated, now)
    _fd, _tmp = tempfile.mkstemp(suffix=".parquet")
    os.close(_fd)
    tmp_parquet = Path(_tmp)
    try:
        df.write_parquet(tmp_parquet)
        parquet_path = tmp_parquet.as_posix()
        conn.execute(
            f"CREATE OR REPLACE TABLE fact_embedding AS "
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )
    finally:
        tmp_parquet.unlink(missing_ok=True)
    context.log.info("Written %d rows to MotherDuck fact_embedding", df.height)

    return MaterializeResult(
        metadata={
            "num_proteins": MetadataValue.int(len(accessions)),
            "num_truncated": MetadataValue.int(n_truncated),
            "embedding_dim": MetadataValue.int(EMBEDDING_DIM),
            "umap_shape": MetadataValue.text(str(coords_2d.shape)),
            "model_version": MetadataValue.text(MODEL_VERSION),
            "computed_at": MetadataValue.text(now.isoformat()),
        }
    )
