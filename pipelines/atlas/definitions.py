"""Dagster code location: wires assets to their resources.

Loads ``.env.local`` into the process environment so the ``dagster`` CLI and the
webserver pick up R2 credentials without a third-party dotenv dependency.
"""

from __future__ import annotations

from pathlib import Path

from dagster import (
    Definitions,
    EnvVar,
    # has untyped **kwargs in dagster's stubs:
    load_assets_from_package_module,  # pyright: ignore[reportUnknownVariableType]
)

from atlas.assets import ingest
from atlas.resources.r2 import R2Resource


def _load_env_local() -> None:
    """Populate os.environ from the repo-root ``.env.local`` (no overwrite)."""
    import os

    # pipelines/atlas/definitions.py -> repo root is three levels up.
    env_path = Path(__file__).resolve().parents[2] / ".env.local"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env_local()

defs = Definitions(
    assets=load_assets_from_package_module(ingest),
    resources={
        "r2": R2Resource(
            account_id=EnvVar("CLOUDFLARE_ACCOUNT_ID"),
            access_key_id=EnvVar("CLOUDFLARE_R2_ACCESS_KEY_ID"),
            secret_access_key=EnvVar("CLOUDFLARE_R2_SECRET_ACCESS_KEY"),
            bucket="atlas-raw",
        ),
    },
)
