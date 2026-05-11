"""Storage backend ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        """Store `data` at `key`. Returns the canonical URL/URI."""

    @abstractmethod
    async def download(self, key: str) -> bytes: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def presign(self, key: str, expires: int = 3600) -> str:
        """Issue a time-limited URL the client can fetch directly."""

    @abstractmethod
    async def exists(self, key: str) -> bool: ...
