"""Cloudflare R2 object-storage resource.

R2 is S3-compatible, so this resource talks to it with a boto3 S3 client pointed
at the account's R2 endpoint. It is the single place every ingest asset goes
through to read and write Bronze-layer Parquet (CLAUDE.md rule 3: Parquet, never
CSV, in pipelines).
"""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import boto3
import botocore.exceptions
import polars as pl
from botocore.config import Config
from dagster import ConfigurableResource
from pydantic import PrivateAttr

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


# ConfigurableResource is generic with no default arg in dagster's stubs.
class R2Resource(ConfigurableResource):  # pyright: ignore[reportMissingTypeArgument]
    """Read/write Parquet in a Cloudflare R2 bucket.

    Produces: an authenticated S3 client bound to one R2 bucket.
    Depends on: the R2 account id + access keys from ``.env.local``.
    Used by: every ``assets/ingest`` module to land raw Parquet.
    """

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    _client_cache: S3Client | None = PrivateAttr(default=None)

    def _client(self) -> S3Client:
        if self._client_cache is None:
            # boto3.client is a giant overload; only the s3 case is typed (S3Client).
            self._client_cache = boto3.client(  # pyright: ignore[reportUnknownMemberType]
                "s3",
                endpoint_url=f"https://{self.account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                region_name="auto",
                config=Config(retries={"max_attempts": 5, "mode": "standard"}),
            )
        return self._client_cache

    def write_parquet(self, df: pl.DataFrame, key: str) -> None:
        """Serialize ``df`` to Parquet and upload to ``bucket/key`` (overwrites)."""
        buf = io.BytesIO()
        df.write_parquet(buf, compression="zstd")
        self._client().put_object(Bucket=self.bucket, Key=key, Body=buf.getvalue())

    def read_parquet(self, key: str) -> pl.DataFrame:
        """Download ``bucket/key`` and parse it back into a DataFrame."""
        obj = self._client().get_object(Bucket=self.bucket, Key=key)
        return pl.read_parquet(io.BytesIO(obj["Body"].read()))

    def count_rows(self, key: str) -> int:
        """Row count of the Parquet object at ``bucket/key``."""
        return self.read_parquet(key).height

    def write_json(self, data: object, key: str) -> None:
        """Serialize ``data`` to JSON and upload to ``bucket/key`` (overwrites)."""
        body = json.dumps(data, default=str).encode("utf-8")
        self._client().put_object(
            Bucket=self.bucket, Key=key, Body=body, ContentType="application/json"
        )

    def exists(self, key: str) -> bool:
        """Return True if ``bucket/key`` exists in R2."""
        try:
            self._client().head_object(Bucket=self.bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as e:
            code: str = e.response["Error"]["Code"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
            if code in ("404", "NoSuchKey"):
                return False
            raise
