"""Pre-flight validation for Part 4 — no Modal calls.

Validates every downstream step of the protein_embeddings asset using
synthetic embeddings in place of real Modal/GPU output. Nothing is written
to the live fact_embedding table; all writes go to temp artefacts that are
cleaned up at the end.

Run before the next Modal push:
    uv run python notebooks/preflight_part4.py

Checks (each reports PASS or FAIL):
  1.  MotherDuck connection and dim_protein schema
  2.  dim_protein row count and column types
  3.  Synthetic embed_batch output — correct shape and type contract
  4.  UMAP call signature on small matrix
  5.  coords_2d.tolist() destructuring (zip pattern used in asset)
  6.  Timezone-aware datetime in DuckDB TIMESTAMP column
  7.  MotherDuck Parquet write at full scale (~20k rows × 1280 dim)
  8.  MotherDuck read-back — row count, embedding dim, spot value
  9.  Qdrant connection
  10. Qdrant create collection
  11. Qdrant upsert 1000 points
  12. Qdrant vector count
  13. Qdrant nearest-neighbour search (smoke test)
  14. Full asset zip/unpack pattern (accessions × embeddings × coords × flags)
  15. MaterializeResult metadata dict construction
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

# --- env ---
_env = Path(__file__).resolve().parents[1] / ".env.local"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

import duckdb  # noqa: E402
import umap  # noqa: E402  # pyright: ignore[reportMissingTypeStubs]
from atlas.assets.ml.embeddings import (  # noqa: E402
    BATCH_SIZE,
    EMBEDDING_DIM,
    MODEL_VERSION,
    accession_to_id,
    chunk,
)
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

# Temporary artefact names — never touch live tables.
_MD_TEMP_TABLE = "_preflight_fact_embedding"
_QD_TEMP_COLLECTION = "proteins_preflight"

FAILURES: list[str] = []


def ok(label: str) -> None:
    print(f"  PASS  {label}")


def fail(label: str, detail: str = "") -> None:
    msg = f"  FAIL  {label}" + (f": {detail}" if detail else "")
    print(msg)
    FAILURES.append(msg)


def section(title: str) -> None:
    print(f"\n{'─' * 64}\n  {title}\n{'─' * 64}")


# ─────────────────────────────────────────────────────────────────────────────
# 1–2. MotherDuck: connection + dim_protein schema
# ─────────────────────────────────────────────────────────────────────────────
section("1–2 / MotherDuck connection + dim_protein schema")

token = os.environ.get("MOTHERDUCK_TOKEN", "")
if not token:
    fail("MOTHERDUCK_TOKEN set", "missing from .env.local")
    sys.exit(1)

try:
    conn = duckdb.connect(f"md:atlas?motherduck_token={token}")
    ok("MotherDuck connection")
except Exception as exc:
    fail("MotherDuck connection", str(exc))
    sys.exit(1)

try:
    rows = conn.execute(
        "SELECT uniprot_accession, gene_symbol, protein_name, sequence "
        "FROM dim_protein WHERE sequence IS NOT NULL ORDER BY uniprot_accession"
    ).fetchall()
    n = len(rows)
    if n < 20_000:
        fail(f"dim_protein row count ({n})", "expected ≥ 20,000")
    else:
        ok(f"dim_protein read — {n:,} rows")
except Exception as exc:
    fail("dim_protein query", str(exc))
    sys.exit(1)

accessions: list[str] = [r[0] for r in rows]
gene_symbols: list[str | None] = [r[1] for r in rows]
protein_names: list[str | None] = [r[2] for r in rows]
sequences: list[str] = [r[3] for r in rows]

# Check every accession is a non-empty string
if all(isinstance(a, str) and a for a in accessions):
    ok("accessions are non-empty strings")
else:
    fail("accession types", "some accessions are empty or not strings")

# Spot-check insulin
if "P01308" in accessions:
    ok("insulin (P01308) present in dim_protein")
else:
    fail("insulin (P01308) in dim_protein", "missing — check dbt run status")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Synthetic embed_batch output — shape and type contract
# ─────────────────────────────────────────────────────────────────────────────
section("3 / Synthetic embed_batch output shape + type contract")

# Simulate what embed_batch returns: list[tuple[list[float], bool]]
rng = np.random.default_rng(42)
_fake_embs = rng.standard_normal((n, EMBEDDING_DIM)).astype(np.float32)
# Mark 10 % as truncated to simulate real-world truncation
_trunc_flags = (rng.random(n) < 0.10).tolist()

fake_batches = chunk(list(zip(range(n), _trunc_flags, strict=True)), BATCH_SIZE)
# One fake batch result mirrors embed_batch return: list[tuple[list[float], bool]]
sample_batch_result: list[tuple[list[float], bool]] = [
    (_fake_embs[i].tolist(), bool(flag)) for i, flag in fake_batches[0]
]

# Verify the asset's iteration pattern doesn't raise
try:
    all_embeddings: list[list[float]] = []
    all_truncated: list[bool] = []
    for emb, truncated in sample_batch_result:
        all_embeddings.append(emb)
        all_truncated.append(truncated)
    assert len(all_embeddings) == len(sample_batch_result)
    assert len(all_embeddings[0]) == EMBEDDING_DIM
    assert isinstance(all_truncated[0], bool)
    ok("embed_batch output iteration pattern")
except Exception as exc:
    fail("embed_batch output iteration pattern", str(exc))

# Assemble full synthetic result the same way the asset would
all_embeddings = [_fake_embs[i].tolist() for i in range(n)]
all_truncated_full: list[bool] = [bool(f) for f in _trunc_flags]
embeddings_matrix = np.array(all_embeddings, dtype=np.float32)

if embeddings_matrix.shape == (n, EMBEDDING_DIM):
    ok(f"embeddings_matrix shape {embeddings_matrix.shape}")
else:
    fail(
        "embeddings_matrix shape",
        f"got {embeddings_matrix.shape}, expected ({n}, {EMBEDDING_DIM})",
    )

# ─────────────────────────────────────────────────────────────────────────────
# 4. UMAP call signature on small matrix
# ─────────────────────────────────────────────────────────────────────────────
section("4 / UMAP call signature (50-row smoke test)")

try:
    reducer = umap.UMAP(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        n_neighbors=15, min_dist=0.1, metric="cosine", random_state=42
    )
    small_matrix = embeddings_matrix[:50]
    raw = reducer.fit_transform(small_matrix)  # pyright: ignore[reportUnknownMemberType]
    coords_small = np.asarray(raw, dtype=np.float32)  # pyright: ignore[reportUnknownArgumentType]
    if coords_small.shape == (50, 2):
        ok(f"UMAP fit_transform shape {coords_small.shape}")
    else:
        fail("UMAP output shape", f"got {coords_small.shape}")
except Exception:
    fail("UMAP call", traceback.format_exc())

# Use pre-cooked coords for the rest of the preflight (avoid 20k-row UMAP time)
# Real coords: uniform grid stands in for actual UMAP output.
fake_coords = rng.standard_normal((n, 2)).astype(np.float32)

# ─────────────────────────────────────────────────────────────────────────────
# 5. coords_2d.tolist() destructuring
# ─────────────────────────────────────────────────────────────────────────────
section("5 / coords_2d.tolist() destructuring in zip")

try:
    coords_2d_list = fake_coords.tolist()
    # Verify the (x, y) destructuring the asset uses
    sample_acc = accessions[0]
    sample_emb = embeddings_matrix[0]
    (x, y) = coords_2d_list[0]
    assert isinstance(x, float) and isinstance(y, float), "x, y must be Python floats"
    # Full zip pattern used in md_rows comprehension
    first_row = next(
        (
            acc,
            list(map(float, emb)),
            float(x),
            float(y),
            MODEL_VERSION,
            bool(trunc),
            datetime.now(UTC),
        )  # noqa: E501
        for acc, emb, (x, y), trunc in zip(
            accessions[:1],
            embeddings_matrix[:1],
            coords_2d_list[:1],
            all_truncated_full[:1],
            strict=True,
        )
    )
    assert first_row[0] == sample_acc
    assert len(first_row[1]) == EMBEDDING_DIM
    ok("coords_2d.tolist() destructuring and md_rows tuple construction")
except Exception:
    fail("coords_2d destructuring", traceback.format_exc())

# ─────────────────────────────────────────────────────────────────────────────
# 6. Timezone-aware datetime in DuckDB TIMESTAMP
# ─────────────────────────────────────────────────────────────────────────────
section("6 / Timezone-aware datetime → DuckDB TIMESTAMP")

try:
    conn.execute("CREATE OR REPLACE TABLE _ts_test (ts TIMESTAMP)")
    now_utc = datetime.now(UTC)
    conn.executemany("INSERT INTO _ts_test VALUES (?)", [(now_utc,)])
    result = conn.execute("SELECT ts FROM _ts_test").fetchone()
    conn.execute("DROP TABLE _ts_test")
    if result is not None:
        ok(f"datetime.now(UTC) accepted by DuckDB TIMESTAMP → stored as {result[0]}")
    else:
        fail("timezone-aware datetime", "no rows returned after insert")
except Exception as exc:
    fail("timezone-aware datetime in DuckDB TIMESTAMP", str(exc))

# ─────────────────────────────────────────────────────────────────────────────
# 7–8. MotherDuck write + read-back at full scale
# ─────────────────────────────────────────────────────────────────────────────
section(f"7–8 / MotherDuck Parquet write + read-back ({n:,} rows × {EMBEDDING_DIM} dim)")

now = datetime.now(UTC)

try:
    import tempfile
    from pathlib import Path

    import polars as pl

    df_pf = pl.DataFrame(
        {
            "uniprot_accession": accessions,
            "embedding": pl.Series(
                [row.tolist() for row in embeddings_matrix], dtype=pl.List(pl.Float32)
            ),
            "umap_x": pl.Series(fake_coords[:, 0].tolist(), dtype=pl.Float32),
            "umap_y": pl.Series(fake_coords[:, 1].tolist(), dtype=pl.Float32),
            "model_version": pl.Series([MODEL_VERSION] * n),
            "was_truncated": all_truncated_full,
            "computed_at": pl.Series([now] * n),
        }
    )
    tmp_pq = Path(tempfile.mktemp(suffix=".parquet"))
    df_pf.write_parquet(tmp_pq)
    parquet_path = tmp_pq.as_posix()
    conn.execute(
        f"CREATE OR REPLACE TABLE {_MD_TEMP_TABLE} AS SELECT * FROM read_parquet('{parquet_path}')"
    )
    tmp_pq.unlink(missing_ok=True)
    ok(f"Parquet write + CREATE TABLE ({n:,} rows via read_parquet)")
except Exception:
    fail("Parquet write + CREATE TABLE", traceback.format_exc())

try:
    count = conn.execute(f"SELECT COUNT(*) FROM {_MD_TEMP_TABLE}").fetchone()[0]  # type: ignore[index]
    if count == n:
        ok(f"read-back row count matches: {count:,}")
    else:
        fail("read-back row count", f"got {count}, expected {n}")
except Exception as exc:
    fail("read-back count query", str(exc))

try:
    emb_type = conn.execute(f"SELECT typeof(embedding) FROM {_MD_TEMP_TABLE} LIMIT 1").fetchone()[0]  # type: ignore[index]
    if "FLOAT" in emb_type.upper():
        ok(f"embedding column type: {emb_type}")
    else:
        fail("embedding column type", f"unexpected: {emb_type}")
except Exception as exc:
    fail("embedding type check", str(exc))

# Spot-check insulin embedding: all values should be finite
try:
    ins_row = conn.execute(
        f"SELECT embedding FROM {_MD_TEMP_TABLE} WHERE uniprot_accession = 'P01308'"
    ).fetchone()
    if ins_row is None:
        fail("insulin embedding spot-check", "P01308 not found in temp table")
    else:
        emb_vals = ins_row[0]
        if len(emb_vals) == EMBEDDING_DIM and all(
            isinstance(v, float) and np.isfinite(v) for v in emb_vals
        ):
            ok(f"insulin embedding spot-check: {EMBEDDING_DIM} finite floats ✓")
        else:
            fail("insulin embedding spot-check", f"dim={len(emb_vals)}, has non-finite values")
except Exception as exc:
    fail("insulin embedding spot-check", str(exc))

# Cleanup
try:
    conn.execute(f"DROP TABLE {_MD_TEMP_TABLE}")
    ok("temp table cleaned up")
except Exception as exc:
    fail("temp table cleanup", str(exc))

# ─────────────────────────────────────────────────────────────────────────────
# 9–13. Qdrant: connect, create, upsert, count, search, cleanup
# ─────────────────────────────────────────────────────────────────────────────
section("9–13 / Qdrant operations")

qdrant_url = os.environ.get("QDRANT_URL", "")
qdrant_key = os.environ.get("QDRANT_API_KEY", "")
if not qdrant_url:
    fail("QDRANT_URL set", "missing from .env.local")
else:
    try:
        qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key)
        # Cheap ping: list collections
        qdrant.get_collections()
        ok("Qdrant connection")
    except Exception as exc:
        fail("Qdrant connection", str(exc))
        qdrant = None  # type: ignore[assignment]

    if qdrant is not None:
        # Create test collection
        try:
            if qdrant.collection_exists(_QD_TEMP_COLLECTION):
                qdrant.delete_collection(_QD_TEMP_COLLECTION)
            qdrant.create_collection(
                collection_name=_QD_TEMP_COLLECTION,
                vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
            )
            ok(f"create collection '{_QD_TEMP_COLLECTION}'")
        except Exception as exc:
            fail("Qdrant create_collection", str(exc))

        # Upsert 1000 points using real accessions + synthetic embeddings
        UPSERT_N = 1_000
        try:
            batch_accs = accessions[:UPSERT_N]
            batch_embs = embeddings_matrix[:UPSERT_N]
            points = [
                PointStruct(
                    id=accession_to_id(acc),
                    vector=emb.tolist(),
                    payload={
                        "uniprot_accession": acc,
                        "gene_symbol": gene_symbols[j],
                        "protein_name": protein_names[j],
                    },
                )
                for j, (acc, emb) in enumerate(zip(batch_accs, batch_embs, strict=True))
            ]
            qdrant.upsert(collection_name=_QD_TEMP_COLLECTION, points=points)
            ok(f"upsert {UPSERT_N} points")
        except Exception:
            fail("Qdrant upsert", traceback.format_exc())

        # Verify count
        try:
            info = qdrant.get_collection(_QD_TEMP_COLLECTION)
            count = info.points_count or 0
            if count == UPSERT_N:
                ok(f"point count: {count}")
            else:
                fail("point count", f"expected {UPSERT_N}, got {count}")
        except Exception as exc:
            fail("Qdrant get_collection", str(exc))

        # Search: nearest neighbours of the first point
        try:
            query_vec = embeddings_matrix[0].tolist()
            result = qdrant.query_points(
                collection_name=_QD_TEMP_COLLECTION,
                query=query_vec,
                limit=5,
                with_payload=True,
            )
            if len(result.points) >= 1:
                top = result.points[0]
                assert top.payload is not None
                assert "uniprot_accession" in top.payload
                top_acc = top.payload.get("uniprot_accession")
                ok(f"nearest-neighbour search returned {len(result.points)} hits; top={top_acc}")
            else:
                fail("nearest-neighbour search", "returned 0 hits")
        except Exception:
            fail("Qdrant search", traceback.format_exc())

        # Cleanup
        try:
            qdrant.delete_collection(_QD_TEMP_COLLECTION)
            ok(f"test collection '{_QD_TEMP_COLLECTION}' deleted")
        except Exception as exc:
            fail("Qdrant cleanup", str(exc))

# ─────────────────────────────────────────────────────────────────────────────
# 14. Full asset zip/unpack pattern
# ─────────────────────────────────────────────────────────────────────────────
section("14 / Full asset zip / unpack (accessions × embeddings × coords × flags)")

try:
    sample_rows = [
        (acc, list(map(float, emb)), float(x), float(y), MODEL_VERSION, bool(trunc), now)
        for acc, emb, (x, y), trunc in zip(
            accessions[:5],
            embeddings_matrix[:5],
            fake_coords.tolist()[:5],
            all_truncated_full[:5],
            strict=True,
        )
    ]
    assert len(sample_rows) == 5
    assert sample_rows[0][0] == accessions[0]  # accession
    assert len(sample_rows[0][1]) == EMBEDDING_DIM  # embedding
    assert isinstance(sample_rows[0][2], float)  # umap_x
    assert isinstance(sample_rows[0][5], bool)  # was_truncated
    ok("md_rows tuple construction (5 rows sampled)")
except Exception:
    fail("md_rows zip/unpack", traceback.format_exc())

# ─────────────────────────────────────────────────────────────────────────────
# 15. MaterializeResult metadata dict
# ─────────────────────────────────────────────────────────────────────────────
section("15 / MaterializeResult metadata dict")

try:
    from dagster import MaterializeResult, MetadataValue

    n_truncated = sum(all_truncated_full)
    meta = MaterializeResult(
        metadata={
            "num_proteins": MetadataValue.int(n),
            "num_truncated": MetadataValue.int(n_truncated),
            "embedding_dim": MetadataValue.int(EMBEDDING_DIM),
            "umap_shape": MetadataValue.text(str(fake_coords.shape)),
            "model_version": MetadataValue.text(MODEL_VERSION),
            "computed_at": MetadataValue.text(now.isoformat()),
        }
    )
    ok(f"MaterializeResult constructed (num_proteins={n}, num_truncated={n_truncated})")
except Exception as exc:
    fail("MaterializeResult construction", str(exc))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═' * 64}")
if not FAILURES:
    print("  ALL CHECKS PASSED — safe to push to Modal.")
else:
    print(f"  {len(FAILURES)} CHECK(S) FAILED:")
    for f in FAILURES:
        print(f"    {f}")
print(f"{'═' * 64}\n")

sys.exit(len(FAILURES))
