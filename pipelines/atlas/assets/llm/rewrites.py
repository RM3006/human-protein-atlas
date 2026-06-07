"""Dagster asset: Claude Haiku batch rewrites of UniProt function text.

Reads every protein from MotherDuck dim_protein, submits function_raw to the
Anthropic Messages Batch API (Claude Haiku), and writes plain-English rewrites
as Parquet to R2. dbt's stg_llm_rewrites model then reads this Parquet;
dim_protein picks up function_friendly and tagline via COALESCE, giving the
hand-curated editorial seed priority over these LLM outputs.

Produces: atlas-raw/llm/v2026_06/protein_rewrites.parquet in R2.
Depends on: dim_protein in MotherDuck (uniprot_accession, gene_symbol, protein_name, function_raw).
Lands at: R2 bucket atlas-raw, key llm/v2026_06/protein_rewrites.parquet.

Workflow: run this asset BEFORE running `dbt run`, so stg_llm_rewrites has data to read.
"""

# Omit 'from __future__ import annotations' — same Dagster 1.13.7 annotation
# bug prevention as embeddings.py: PEP 563 lazy strings break inspect.signature
# when context is the only parameter.

import json
import os
import time
from datetime import UTC, datetime
from typing import Any, cast

import anthropic
import duckdb
import polars as pl
from dagster import AssetExecutionContext, Config, MaterializeResult, MetadataValue, asset

from atlas.logging import logger
from atlas.resources.r2 import R2Resource

MODEL_ID = "claude-haiku-4-5-20251001"
LLM_VERSION = "2026_06"
R2_KEY = f"llm/v{LLM_VERSION}/protein_rewrites.parquet"
MAX_BATCH_SIZE = 10_000
MAX_TOKENS_PER_REQUEST = 600
POLL_INTERVAL_SECONDS = 60

R2_KEY_TEST = f"llm/v{LLM_VERSION}/protein_rewrites_test.parquet"
R2_CHECKPOINT_KEY = f"llm/v{LLM_VERSION}/batch_checkpoint.json"
R2_CHECKPOINT_KEY_TEST = f"llm/v{LLM_VERSION}/batch_checkpoint_test.json"


class RewritesConfig(Config):
    limit: int | None = None  # if set, process only the first N proteins (smoke test)
    force: bool = False  # bypass idempotency check and regenerate even if R2 key exists


SYSTEM_PROMPT = (
    "You rewrite UniProt function annotations into accessible English for a protein atlas.\n\n"
    "Rules — follow every one without exception:\n"
    "1. Do not invent any biological claim absent from the source text.\n"
    "2. If the source text is missing or empty, "
    'return {"function_friendly": null, "tagline": null}.\n'
    "3. function_friendly: 2–3 plain-English sentences.\n"
    "   Explain what the protein does in the body.\n"
    "   Gloss any jargon immediately. Assume the reader has high-school biology.\n"
    "4. tagline: one punchy sentence, ≤20 words, capturing the single most memorable role.\n"
    "5. Never use double quotation marks inside the function_friendly or tagline text"
    " — they break JSON parsing. For emphasis or naming (e.g. a state, mode, or nickname),"
    " use single quotes ('like this') or rephrase without quotation marks entirely.\n"
    '6. Return ONLY valid JSON with exactly two keys: "function_friendly" and "tagline".'
    " No markdown, no preamble."
)


def build_prompt(
    gene_symbol: str | None,
    protein_name: str | None,
    function_raw: str | None,
) -> str:
    """Assemble the user message for a single protein rewrite request."""
    parts: list[str] = []
    if gene_symbol:
        parts.append(f"Gene: {gene_symbol}")
    if protein_name:
        parts.append(f"Protein: {protein_name}")
    parts.append(
        f"UniProt function text:\n{function_raw}"
        if function_raw
        else "UniProt function text: (none available)"
    )
    return "\n".join(parts)


def parse_rewrite(text: str) -> tuple[str | None, str | None]:
    """Parse the JSON response from Haiku into (function_friendly, tagline).

    Handles markdown-fenced JSON (```json...```) by extracting the {…} object.
    Returns (None, None) on any parse failure — never raises.
    """
    cleaned = text.strip()
    # Extract the JSON object directly — handles plain JSON and ```json fences.
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None, None
    try:
        raw: Any = json.loads(cleaned[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None, None
    if not isinstance(raw, dict):
        return None, None
    data = cast(dict[str, object], raw)
    ff = data.get("function_friendly")
    tl = data.get("tagline")
    return (
        ff if isinstance(ff, str) else None,
        tl if isinstance(tl, str) else None,
    )


def build_rewrite_df(
    accessions: list[str],
    function_friendlies: list[str | None],
    taglines: list[str | None],
    generated_at: datetime,
) -> pl.DataFrame:
    """Assemble the rewrite output DataFrame from collected arrays."""
    return pl.DataFrame(
        {
            "uniprot_accession": accessions,
            "function_friendly": pl.Series(function_friendlies, dtype=pl.String),
            "tagline": pl.Series(taglines, dtype=pl.String),
            "model_id": pl.Series([MODEL_ID] * len(accessions), dtype=pl.String),
            "generated_at": pl.Series(
                [generated_at] * len(accessions), dtype=pl.Datetime(time_unit="us")
            ),
        }
    )


def _submit_batch(
    client: anthropic.Anthropic,
    proteins: list[tuple[str, str | None, str | None, str | None]],
) -> str:
    """Submit one Anthropic batch (≤10 k requests). Returns the batch ID."""
    requests: list[Any] = [
        {
            "custom_id": accession,
            "params": {
                "model": MODEL_ID,
                "max_tokens": MAX_TOKENS_PER_REQUEST,
                "system": SYSTEM_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": build_prompt(gene_symbol, protein_name, function_raw),
                    }
                ],
            },
        }
        for accession, gene_symbol, protein_name, function_raw in proteins
    ]
    batch = client.beta.messages.batches.create(  # pyright: ignore[reportUnknownMemberType]
        requests=requests
    )
    return str(batch.id)  # pyright: ignore[reportUnknownMemberType]


def _poll_until_done(
    client: anthropic.Anthropic,
    batch_id: str,
    context: AssetExecutionContext,
) -> None:
    """Block, polling every POLL_INTERVAL_SECONDS until the batch reaches 'ended'."""
    while True:
        batch: Any = client.beta.messages.batches.retrieve(batch_id)  # pyright: ignore[reportUnknownMemberType]
        status: str = batch.processing_status
        counts: Any = batch.request_counts
        context.log.info(
            "Batch %s: status=%s  processing=%d  succeeded=%d  errored=%d",
            batch_id,
            status,
            counts.processing,
            counts.succeeded,
            counts.errored,
        )
        if status == "ended":
            return
        time.sleep(POLL_INTERVAL_SECONDS)


def _collect_results(
    client: anthropic.Anthropic,
    batch_id: str,
) -> dict[str, tuple[str | None, str | None]]:
    """Stream completed batch results. Returns {accession: (function_friendly, tagline)}."""
    results: dict[str, tuple[str | None, str | None]] = {}
    for item in client.beta.messages.batches.results(batch_id):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        result: Any = item.result  # pyright: ignore[reportUnknownMemberType]
        accession: str = item.custom_id  # pyright: ignore[reportUnknownMemberType]
        if result.type == "succeeded":
            content: Any = result.message.content
            text: str = content[0].text if content else ""
            results[accession] = parse_rewrite(text)
        else:
            logger.warning("Batch item %s result type=%s", accession, result.type)
            results[accession] = (None, None)
    return results


@asset(group_name="llm", compute_kind="anthropic")
def protein_llm_rewrites(
    context: AssetExecutionContext,
    config: RewritesConfig,
    r2: R2Resource,
) -> MaterializeResult[Any]:
    """Plain-English rewrites of UniProt function text for all dim_protein rows.

    Produces: protein_rewrites.parquet in R2 (atlas-raw/llm/v2026_06/).
    Depends on: dim_protein in MotherDuck (must exist before this asset runs).
    Lands at: R2 atlas-raw/llm/v2026_06/protein_rewrites.parquet; read by stg_llm_rewrites.
    Set config.limit to a small number for smoke testing; set config.force=true to regenerate.
    """
    out_key = R2_KEY if config.limit is None else R2_KEY_TEST

    if not config.force and r2.exists(out_key):
        context.log.info(
            "R2 key %s already exists — skipping API call. "
            "Set force=true in run config to regenerate.",
            out_key,
        )
        existing = r2.read_parquet(out_key)
        return MaterializeResult(
            metadata={
                "num_proteins": MetadataValue.int(existing.height),
                "skipped": MetadataValue.text("exists_in_r2"),
                "r2_key": MetadataValue.text(out_key),
            }
        )

    token = os.environ["MOTHERDUCK_TOKEN"]
    conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

    rows = conn.execute(
        "SELECT uniprot_accession, gene_symbol, protein_name, function_raw "
        "FROM dim_protein "
        "ORDER BY uniprot_accession"
    ).fetchall()
    context.log.info("Read %d proteins from dim_protein", len(rows))

    proteins: list[tuple[str, str | None, str | None, str | None]] = [
        (r[0], r[1], r[2], r[3]) for r in rows
    ]

    if config.limit is not None:
        proteins = proteins[: config.limit]
        context.log.info("Smoke-test mode: limiting to %d proteins", config.limit)

    # Skip proteins with no function_raw — the LLM would return null for them anyway.
    # Pre-populate their results so they flow through to the 'No information available'
    # fallback in dim_protein without burning API quota.
    to_submit = [p for p in proteins if p[3] is not None]
    n_skipped = len(proteins) - len(to_submit)
    if n_skipped:
        context.log.info(
            "Skipping %d proteins with no function_raw (will be NULL in output)", n_skipped
        )

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # Split into batches of MAX_BATCH_SIZE (Anthropic limit: 10 k per batch).
    chunks = [to_submit[i : i + MAX_BATCH_SIZE] for i in range(0, len(to_submit), MAX_BATCH_SIZE)]
    batch_ids: list[str] = []
    for chunk_idx, chunk in enumerate(chunks):
        batch_id = _submit_batch(client, chunk)
        context.log.info(
            "Submitted batch %d/%d: id=%s (%d requests)",
            chunk_idx + 1,
            len(chunks),
            batch_id,
            len(chunk),
        )
        batch_ids.append(batch_id)

    # Persist batch IDs to R2 before polling. If the Parquet write later fails,
    # results stay retrievable from the Anthropic API for 29 days using these IDs.
    checkpoint_key = R2_CHECKPOINT_KEY if config.limit is None else R2_CHECKPOINT_KEY_TEST
    r2.write_json(
        {"batch_ids": batch_ids, "submitted_at": datetime.now(UTC).isoformat()},
        checkpoint_key,
    )
    context.log.info("Batch checkpoint written to R2 key %s", checkpoint_key)

    # Poll all batches until done.
    for batch_id in batch_ids:
        _poll_until_done(client, batch_id, context)

    # Collect results from all batches.
    # Seed with NULLs for skipped proteins (no function_raw) — they were never submitted.
    all_results: dict[str, tuple[str | None, str | None]] = {
        p[0]: (None, None) for p in proteins if p[3] is None
    }
    for batch_id in batch_ids:
        all_results.update(_collect_results(client, batch_id))

    accessions = [p[0] for p in proteins]
    function_friendlies = [all_results.get(acc, (None, None))[0] for acc in accessions]
    taglines = [all_results.get(acc, (None, None))[1] for acc in accessions]

    n_ff = sum(1 for x in function_friendlies if x is not None)
    n_tl = sum(1 for x in taglines if x is not None)
    context.log.info(
        "Rewrites complete: %d/%d function_friendly, %d/%d taglines",
        n_ff,
        len(accessions),
        n_tl,
        len(accessions),
    )

    df = build_rewrite_df(accessions, function_friendlies, taglines, datetime.now(UTC))
    r2.write_parquet(df, out_key)
    context.log.info("Written %d rows to R2 key %s", df.height, out_key)

    return MaterializeResult(
        metadata={
            "num_proteins": MetadataValue.int(len(accessions)),
            "num_function_friendly": MetadataValue.int(n_ff),
            "num_taglines": MetadataValue.int(n_tl),
            "num_batches": MetadataValue.int(len(batch_ids)),
            "model_id": MetadataValue.text(MODEL_ID),
            "r2_key": MetadataValue.text(out_key),
        }
    )
