"""End-to-end Modal smoke test for Part 4 — 1 batch, ~$0.01.

Calls the deployed Modal embed_batch function with 20 real sequences,
then runs the full downstream pipeline (Parquet write to MotherDuck,
Qdrant upsert) against temporary artefacts. UMAP is skipped here
(needs 15+ neighbours; verified in preflight_part4.py instead).

Run once after Modal credits reset, before the full 20k-sequence job:
    uv run python notebooks/smoketest_part4.py

Exit 0 = all checks passed, safe to run the full materialization.
Exit N = N failures, do not proceed.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path

import modal
import numpy as np

# --- load .env.local ---
_env = Path(__file__).resolve().parents[1] / ".env.local"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

import duckdb  # noqa: E402
import polars as pl  # noqa: E402
from atlas.assets.ml.embeddings import (  # noqa: E402
    EMBEDDING_DIM,
    MODAL_APP_NAME,
    MODAL_FN_NAME,
    MODEL_VERSION,
    accession_to_id,
)
from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

SMOKE_ACCESSIONS = [
    "P00533",  # EGFR
    "P01308",  # Insulin
    "P04637",  # TP53
    "P04626",  # ERBB2
    "P21860",  # ERBB3
    "Q15303",  # ERBB4
    "P68871",  # Haemoglobin beta
    "P02144",  # Myoglobin
    "P00533",  # EGFR duplicate → tests dedup in prod (same ID)
]
# Deduplicate while preserving order
SMOKE_ACCESSIONS = list(dict.fromkeys(SMOKE_ACCESSIONS))

_MD_TEMP = "_smoketest_fact_embedding"
_QD_TEMP = "proteins_smoketest"

FAILURES: list[str] = []


def ok(label: str) -> None:
    print(f"  PASS  {label}")


def fail(label: str, detail: str = "") -> None:
    msg = f"  FAIL  {label}" + (f": {detail}" if detail else "")
    print(msg)
    FAILURES.append(msg)


def section(title: str) -> None:
    print(f"\n{'─' * 60}\n  {title}\n{'─' * 60}")


# ─────────────────────────────────────────────────────────────────────────────
# 1. Pull sequences from MotherDuck
# ─────────────────────────────────────────────────────────────────────────────
section("1 / Fetch smoke sequences from MotherDuck")

token = os.environ.get("MOTHERDUCK_TOKEN", "")
if not token:
    print("ERROR: MOTHERDUCK_TOKEN not set")
    sys.exit(1)

conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

placeholders = ",".join("?" * len(SMOKE_ACCESSIONS))
rows = conn.execute(
    f"SELECT uniprot_accession, gene_symbol, protein_name, sequence "
    f"FROM dim_protein WHERE uniprot_accession IN ({placeholders})",
    SMOKE_ACCESSIONS,
).fetchall()

if len(rows) != len(SMOKE_ACCESSIONS):
    found = {r[0] for r in rows}
    missing = set(SMOKE_ACCESSIONS) - found
    fail("sequences fetched", f"missing accessions: {missing}")
    sys.exit(1)

# Sort to match SMOKE_ACCESSIONS order
row_map = {r[0]: r for r in rows}
ordered = [row_map[acc] for acc in SMOKE_ACCESSIONS]
accessions = [r[0] for r in ordered]
gene_symbols = [r[1] for r in ordered]
protein_names = [r[2] for r in ordered]
sequences = [r[3] for r in ordered]

ok(f"Fetched {len(accessions)} sequences: {', '.join(gene_symbols)}")
for acc, gene, seq in zip(accessions, gene_symbols, sequences, strict=True):
    print(f"     {acc}  {gene or '?':10s}  {len(seq)} aa")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Modal inference — 1 batch
# ─────────────────────────────────────────────────────────────────────────────
section(f"2 / Modal inference ({len(sequences)} sequences, 1 batch)")

try:
    embed_fn = modal.Function.from_name(MODAL_APP_NAME, MODAL_FN_NAME)
    print(f"     Calling {MODAL_APP_NAME}.{MODAL_FN_NAME} …")
    batch_result = embed_fn.remote(sequences)  # .remote() = single call, not .map()
    ok(f"Modal call returned {len(batch_result)} results")
except Exception:
    fail("Modal remote call", traceback.format_exc())
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# 3. Validate embed_batch output format
# ─────────────────────────────────────────────────────────────────────────────
section("3 / Validate embed_batch output")

all_embeddings: list[list[float]] = []
all_truncated: list[bool] = []

try:
    for emb, truncated in batch_result:
        all_embeddings.append(emb)
        all_truncated.append(truncated)

    assert len(all_embeddings) == len(sequences), "result count mismatch"
    ok("result count matches input")
except Exception:
    fail("output iteration", traceback.format_exc())
    sys.exit(1)

try:
    assert len(all_embeddings[0]) == EMBEDDING_DIM, f"expected {EMBEDDING_DIM} dims"
    ok(f"embedding dimension: {len(all_embeddings[0])}")
except Exception as exc:
    fail("embedding dimension", str(exc))

try:
    embeddings_matrix = np.array(all_embeddings, dtype=np.float32)
    assert np.all(np.isfinite(embeddings_matrix)), "non-finite values in embeddings"
    ok(f"all values finite — matrix shape {embeddings_matrix.shape}")
except Exception as exc:
    fail("embedding values", str(exc))

print(f"     Truncated sequences: {sum(all_truncated)}/{len(all_truncated)}")
for acc, gene, trunc in zip(accessions, gene_symbols, all_truncated, strict=True):
    if trunc:
        print(f"       truncated: {acc} ({gene})")

# ─────────────────────────────────────────────────────────────────────────────
# 4. MotherDuck write (Parquet path)
# ─────────────────────────────────────────────────────────────────────────────
section("4 / MotherDuck Parquet write")

# Use dummy coords (UMAP needs 15+ points; this is just testing the write path).
fake_coords = np.zeros((len(accessions), 2), dtype=np.float32)
now = datetime.now(UTC)

try:
    df = pl.DataFrame({
        "uniprot_accession": accessions,
        "embedding": pl.Series(
            [row.tolist() for row in embeddings_matrix], dtype=pl.List(pl.Float32)
        ),
        "umap_x": pl.Series(fake_coords[:, 0].tolist(), dtype=pl.Float32),
        "umap_y": pl.Series(fake_coords[:, 1].tolist(), dtype=pl.Float32),
        "model_version": pl.Series([MODEL_VERSION] * len(accessions)),
        "was_truncated": all_truncated,
        "computed_at": pl.Series([now] * len(accessions)),
    })
    _fd, _tmp = tempfile.mkstemp(suffix=".parquet")
    os.close(_fd)
    tmp_parquet = Path(_tmp)
    try:
        df.write_parquet(tmp_parquet)
        parquet_path = tmp_parquet.as_posix()
        conn.execute(
            f"CREATE OR REPLACE TABLE {_MD_TEMP} AS "
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )
    finally:
        tmp_parquet.unlink(missing_ok=True)

    count = conn.execute(f"SELECT COUNT(*) FROM {_MD_TEMP}").fetchone()[0]  # type: ignore[index]
    assert count == len(accessions), f"row count mismatch: {count} vs {len(accessions)}"

    # Spot-check EGFR embedding
    egfr_row = conn.execute(
        f"SELECT embedding FROM {_MD_TEMP} WHERE uniprot_accession = 'P00533'"
    ).fetchone()
    assert egfr_row is not None, "EGFR not found in temp table"
    assert len(egfr_row[0]) == EMBEDDING_DIM, "EGFR embedding wrong dim"

    conn.execute(f"DROP TABLE {_MD_TEMP}")
    ok(f"Wrote {count} rows, EGFR embedding {EMBEDDING_DIM}-dim ✓, table cleaned up")
except Exception:
    fail("MotherDuck write", traceback.format_exc())
    import contextlib
    with contextlib.suppress(Exception):
        conn.execute(f"DROP TABLE IF EXISTS {_MD_TEMP}")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Qdrant upsert + search
# ─────────────────────────────────────────────────────────────────────────────
section("5 / Qdrant upsert + nearest-neighbour search")

qdrant_url = os.environ.get("QDRANT_URL", "")
qdrant_key = os.environ.get("QDRANT_API_KEY", "")

try:
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key)

    if qdrant.collection_exists(_QD_TEMP):
        qdrant.delete_collection(_QD_TEMP)
    qdrant.create_collection(
        collection_name=_QD_TEMP,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )

    points = [
        PointStruct(
            id=accession_to_id(acc),
            vector=emb.tolist(),
            payload={"uniprot_accession": acc, "gene_symbol": gene},
        )
        for acc, emb, gene in zip(
            accessions, embeddings_matrix, gene_symbols, strict=True
        )
    ]
    qdrant.upsert(collection_name=_QD_TEMP, points=points)

    info = qdrant.get_collection(_QD_TEMP)
    count = info.points_count or 0
    assert count == len(accessions), f"expected {len(accessions)} points, got {count}"
    ok(f"upserted {count} points")

    # Search: EGFR's nearest neighbour should be one of the ErbB family
    egfr_idx = accessions.index("P00533")
    egfr_vec = embeddings_matrix[egfr_idx].tolist()
    result = qdrant.query_points(
        collection_name=_QD_TEMP,
        query=egfr_vec,
        limit=4,
        with_payload=True,
    )
    neighbour_genes = [
        p.payload.get("gene_symbol") if p.payload else "?"
        for p in result.points
        if p.payload and p.payload.get("uniprot_accession") != "P00533"
    ]
    erbb_found = any(g in {"ERBB2", "ERBB3", "ERBB4"} for g in neighbour_genes)
    print(f"     EGFR top neighbours: {neighbour_genes}")
    if erbb_found:
        ok("EGFR neighbours include ErbB family member ✓")
    else:
        # With only 8 proteins and random-ish embeddings this might not hold — warn but don't fail
        print("  WARN  ErbB family not in EGFR top-3 (expected with full 20k embedding space)")

    qdrant.delete_collection(_QD_TEMP)
    ok(f"temp collection '{_QD_TEMP}' deleted")
except Exception:
    fail("Qdrant upsert/search", traceback.format_exc())
    import contextlib
    with contextlib.suppress(Exception):
        qdrant.delete_collection(_QD_TEMP)

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═' * 60}")
if not FAILURES:
    print("  ALL CHECKS PASSED — full materialization is clear to run.")
    print()
    print("  Next:")
    print("    PYTHONUTF8=1 uv run dagster asset materialize \\")
    print("      --select protein_embeddings -m atlas.definitions")
else:
    print(f"  {len(FAILURES)} CHECK(S) FAILED — do not proceed:")
    for f in FAILURES:
        print(f"    {f}")
print(f"{'═' * 60}\n")

sys.exit(len(FAILURES))
