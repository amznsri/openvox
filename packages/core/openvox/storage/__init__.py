"""Pluggable object storage — local FS, BytePlus TOS, S3, GCS, Alibaba OSS."""

from openvox.storage.base import StorageBackend
from openvox.storage.factory import get_storage

__all__ = ["StorageBackend", "get_storage"]
