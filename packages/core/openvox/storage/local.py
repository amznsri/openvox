"""Local-filesystem storage — the default for local-first installs."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiofiles

from openvox.storage.base import StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Forbid `..` escapes.
        clean = Path(key).as_posix().lstrip("/")
        if ".." in clean.split("/"):
            raise ValueError("invalid key")
        return self._root / clean

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(p, "wb") as f:
            await f.write(data)
        return f"file://{p.absolute()}"

    async def download(self, key: str) -> bytes:
        async with aiofiles.open(self._path(key), "rb") as f:
            return await f.read()

    async def delete(self, key: str) -> None:
        p = self._path(key)
        await asyncio.to_thread(p.unlink, missing_ok=True)

    async def presign(self, key: str, expires: int = 3600) -> str:
        # Local backend doesn't presign — return a path the API can stream from.
        return f"/storage/{key.lstrip('/')}"

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()
