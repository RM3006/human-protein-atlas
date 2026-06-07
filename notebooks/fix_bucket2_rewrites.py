"""One-shot patch: re-run LLM rewrites for the ~51 proteins lost to a JSON-parsing bug.

Background: the original protein_llm_rewrites batch (2026-06-06) produced good,
substantive rewrites for these proteins, but Claude Haiku wrote them using
"scare quotes" for emphasis (e.g. `the "on" position`, `"leak mode"`) — unescaped
double quotes inside JSON string values, which broke `parse_rewrite` and silently
discarded the content as (None, None). `SYSTEM_PROMPT` now has an explicit rule
against this (rewrites.py rule 5: use single quotes instead).

This script re-derives the exact affected accession list from the original batch
results (still inside Anthropic's 29-day retention window), re-submits ONLY those
to a fresh small batch with the corrected prompt, and patches just those rows into
the existing protein_rewrites.parquet in R2 — leaving the ~17k already-good
rewrites untouched. Not a recurring asset: run once, then delete.

Run:
    uv run python notebooks/fix_bucket2_rewrites.py
"""

from __future__ import annotations

import io
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# --- load .env.local ---
_env = Path(__file__).resolve().parents[1] / ".env.local"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

import anthropic  # noqa: E402
import duckdb  # noqa: E402
import polars as pl  # noqa: E402
from atlas.assets.llm.rewrites import (  # noqa: E402
    R2_KEY,
    _collect_results,  # pyright: ignore[reportPrivateUsage] -- one-shot script, reusing the asset's batch helpers
    _poll_until_done,  # pyright: ignore[reportPrivateUsage]
    _submit_batch,  # pyright: ignore[reportPrivateUsage]
    build_rewrite_df,
    parse_rewrite,
)
from atlas.resources.r2 import R2Resource  # noqa: E402

r2 = R2Resource(
    account_id=os.environ["CLOUDFLARE_ACCOUNT_ID"],
    access_key_id=os.environ["CLOUDFLARE_R2_ACCESS_KEY_ID"],
    secret_access_key=os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"],
    bucket="atlas-raw",
)
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ─────────────────────────────────────────────────────────────────────────────
# 1. Re-derive the affected accession list from the ORIGINAL batch results.
#    (Same classification used to diagnose the bug: explicit null/null from the
#    LLM is a deliberate "too terse to rewrite" call and is left alone — those
#    proteins now fall back to function_raw via dim_protein's COALESCE. Only the
#    JSON-parse-failure cases — good content lost to the quoting bug — get redone.)
# ─────────────────────────────────────────────────────────────────────────────
print("Step 1/5: identifying affected accessions from the original batch run …")

token = os.environ["MOTHERDUCK_TOKEN"]
conn = duckdb.connect(f"md:atlas?motherduck_token={token}")

# NOTE: dim_protein.function_friendly can no longer be used to find the 87 —
# the Bucket 1 fix (COALESCE ... u.function_raw ...) now back-fills ALL of them
# from function_raw, masking which ones came from a null LLM rewrite. The only
# remaining signal is the LLM rewrites parquet itself: function_friendly IS NULL
# there, cross-referenced with dim_protein for a substantive function_raw.
rewrites = r2.read_parquet(R2_KEY)
null_rewrite_accessions = (
    rewrites.filter(pl.col("function_friendly").is_null())
    .get_column("uniprot_accession")
    .to_list()
)
placeholders_c = ",".join("?" * len(null_rewrite_accessions))
candidates = {
    acc
    for (acc,) in conn.execute(
        f"SELECT uniprot_accession FROM dim_protein "
        f"WHERE function_raw != 'No information available' "
        f"AND uniprot_accession IN ({placeholders_c})",
        null_rewrite_accessions,
    ).fetchall()
}

checkpoint_obj = io.BytesIO()
checkpoint_obj.write(
    r2._client()  # noqa: SLF001 -- one-shot script, reusing the resource's authenticated client  # pyright: ignore[reportPrivateUsage]
    .get_object(Bucket="atlas-raw", Key="llm/v2026_06/batch_checkpoint.json")["Body"]
    .read()
)
checkpoint = json.loads(checkpoint_obj.getvalue())

_QUOTE_PATTERN = re.compile(r'[a-zA-Z]"[a-zA-Z ]+"[a-zA-Z]|[a-zA-Z] "[a-zA-Z][a-zA-Z \-]*" ')

to_redo: list[str] = []
for batch_id in checkpoint["batch_ids"]:
    for item in client.beta.messages.batches.results(batch_id):
        if item.custom_id not in candidates:
            continue
        if item.result.type != "succeeded":
            continue
        content: Any = item.result.message.content  # pyright: ignore[reportUnknownMemberType]
        text: str = content[0].text if content else ""
        ff, tl = parse_rewrite(text)
        if ff is not None or tl is not None:
            continue  # parsed fine — not affected
        cleaned = text.strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        raw_json = cleaned[start : end + 1] if start != -1 and end != -1 else ""
        try:
            json.loads(raw_json)
            still_null = True  # valid JSON, genuinely {"function_friendly": null, ...}
        except (json.JSONDecodeError, ValueError):
            still_null = False
        if not still_null and _QUOTE_PATTERN.search(raw_json):
            to_redo.append(item.custom_id)

print(f"  -> {len(to_redo)} accessions affected by the quoting bug (re-submitting these only)")
if not to_redo:
    print("Nothing to do — exiting.")
    raise SystemExit(0)

# ─────────────────────────────────────────────────────────────────────────────
# 2. Fetch their current dim_protein fields (same inputs the original batch used).
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 2/5: fetching protein fields from dim_protein …")
placeholders = ",".join("?" * len(to_redo))
rows = conn.execute(
    f"SELECT uniprot_accession, gene_symbol, protein_name, function_raw "
    f"FROM dim_protein WHERE uniprot_accession IN ({placeholders}) "
    f"ORDER BY uniprot_accession",
    to_redo,
).fetchall()
proteins: list[tuple[str, str | None, str | None, str | None]] = [
    (r[0], r[1], r[2], r[3]) for r in rows
]
print(f"  -> {len(proteins)} proteins ready for resubmission")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Submit one small batch with the corrected SYSTEM_PROMPT (51 << 10k limit).
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 3/5: submitting batch with corrected prompt …")
batch_id = _submit_batch(client, proteins)
print(f"  -> batch id = {batch_id}")

# ─────────────────────────────────────────────────────────────────────────────
# 4. Poll until done, collect results.
# ─────────────────────────────────────────────────────────────────────────────
print("\nStep 4/5: polling until batch completes …")


class _LogShim:
    def info(self, msg: str, *args: object) -> None:
        print("  " + (msg % args if args else msg))


class _ContextShim:
    log = _LogShim()


_poll_until_done(client, batch_id, _ContextShim())  # type: ignore[arg-type]
results = _collect_results(client, batch_id)

n_recovered = sum(1 for ff, _ in results.values() if ff is not None)
print(f"  -> {n_recovered}/{len(proteins)} now have valid function_friendly")
for acc, (ff, _tl) in results.items():
    status = "OK  " if ff is not None else "NULL"
    preview = (ff[:70] + "…") if ff and len(ff) > 70 else ff
    print(f"     [{status}] {acc}: {preview!r}")

# ─────────────────────────────────────────────────────────────────────────────
# 5. Patch just these rows into the existing protein_rewrites.parquet in R2.
# ─────────────────────────────────────────────────────────────────────────────
print(f"\nStep 5/5: patching {len(proteins)} rows into r2://atlas-raw/{R2_KEY} …")
existing = r2.read_parquet(R2_KEY)
unaffected = existing.filter(~pl.col("uniprot_accession").is_in(to_redo))

accessions = [p[0] for p in proteins]
patched = build_rewrite_df(
    accessions=accessions,
    function_friendlies=[results[acc][0] for acc in accessions],
    taglines=[results[acc][1] for acc in accessions],
    generated_at=datetime.now(UTC),
)

merged = pl.concat([unaffected, patched]).sort("uniprot_accession")
assert merged.height == existing.height, (
    f"row count changed: {existing.height} -> {merged.height} (expected unchanged)"
)
r2.write_parquet(merged, R2_KEY)
print(f"  -> wrote {merged.height} rows back to r2://atlas-raw/{R2_KEY}")

print(
    "\nDone. Next: `uv run dbt run --select stg_llm_rewrites dim_protein` "
    "to pick up the patched rewrites."
)
