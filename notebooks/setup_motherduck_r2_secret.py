"""One-shot admin task: register a persistent R2 secret inside MotherDuck.

Background: dbt's `profiles.yml` configures R2 access as session-level DuckDB
`SET s3_*` settings (via `env_var()`). Those only apply to the local dbt-duckdb
session — `duckdb_secrets()` on the live connection returns empty. Any query run
from a *different* session (e.g. pasted into the MotherDuck web UI) re-executes
the staging views' `read_parquet('s3://atlas-raw/...')` with no R2 credentials,
falls back to default AWS S3 resolution (region eu-central-1), and 404s because
the bucket lives in Cloudflare R2, not AWS S3.

`CREATE SECRET IN MOTHERDUCK` persists the secret server-side so MotherDuck's
cloud execution engine can resolve `r2://` paths from any session. R2 is
regionless, so no REGION parameter is needed (unlike the s3_region setting).

This is a one-time registration — run once, then delete (same pattern as
fix_bucket2_rewrites.py). Companion change: staging models switch their source
paths from `s3://...` to `r2://...` so they resolve via this secret.

Run:
    uv run python notebooks/setup_motherduck_r2_secret.py
"""

from __future__ import annotations

import os
from pathlib import Path

# --- load .env.local (same manual-parse pattern as fix_bucket2_rewrites.py;
#     no python-dotenv dependency per CLAUDE.md rule 6) ---
_env = Path(__file__).resolve().parents[1] / ".env.local"
if _env.exists():
    for _line in _env.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

import duckdb  # noqa: E402

token = os.environ["MOTHERDUCK_TOKEN"]
account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
key_id = os.environ["CLOUDFLARE_R2_ACCESS_KEY_ID"]
secret = os.environ["CLOUDFLARE_R2_SECRET_ACCESS_KEY"]

con = duckdb.connect(f"md:atlas?motherduck_token={token}")

print("Creating persistent R2 secret 'atlas_r2' in MotherDuck …")
# NOTE: MotherDuck docs say R2 is regionless and REGION can be omitted, but in
# practice the cloud execution engine then defaults to 'eu-central-1' (its own
# account region), which R2 rejects (valid values: wnam/enam/weur/eeur/apac/oc/
# auto) -> HTTP 400 InvalidRegionName. Pinning REGION 'auto' here fixed it,
# verified against fresh bare connections (no local SET statements at all).
con.execute(f"""
    CREATE OR REPLACE SECRET atlas_r2 IN MOTHERDUCK (
        TYPE R2,
        KEY_ID '{key_id}',
        SECRET '{secret}',
        ACCOUNT_ID '{account_id}',
        REGION 'auto'
    )
""")

rows = con.sql(
    "SELECT name, type, provider, persistent, storage FROM duckdb_secrets() WHERE name = 'atlas_r2'"
).fetchall()
print(f"Done. duckdb_secrets() now shows: {rows}")
print("(Credential values were not printed or logged.)")
