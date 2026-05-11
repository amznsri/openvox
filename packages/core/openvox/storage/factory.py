"""Pick a storage backend from settings."""

from __future__ import annotations

from functools import lru_cache

from openvox.config import get_settings
from openvox.storage.base import StorageBackend
from openvox.storage.byteplus_tos import BytePlusTOSStorage
from openvox.storage.local import LocalStorage
from openvox.storage.s3 import S3Storage


@lru_cache(maxsize=1)
def get_storage() -> StorageBackend:
    s = get_settings()
    if s.storage_backend == "byteplus_tos":
        return BytePlusTOSStorage(
            access_key=s.byteplus_tos_access_key,
            secret_key=s.byteplus_tos_secret_key,
            endpoint=s.byteplus_tos_endpoint,
            region=s.byteplus_tos_region,
            bucket=s.byteplus_tos_bucket,
        )
    if s.storage_backend == "s3":
        return S3Storage(
            bucket=s.s3_bucket,
            region=s.s3_region,
            endpoint=s.s3_endpoint,
            access_key=s.aws_access_key_id,
            secret_key=s.aws_secret_access_key,
        )
    # GCS / Alibaba OSS — fall back to local in this build; backends ship in
    # ./gcs.py / ./alibaba_oss.py and can be enabled by extending this match.
    return LocalStorage(s.storage_local_path)
