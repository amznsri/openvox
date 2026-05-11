"""BytePlus TOS (Torch Object Storage) backend."""

from __future__ import annotations

import asyncio

from openvox.storage.base import StorageBackend


class BytePlusTOSStorage(StorageBackend):
    def __init__(
        self,
        *,
        access_key: str,
        secret_key: str,
        endpoint: str,
        region: str,
        bucket: str,
    ) -> None:
        self._ak = access_key
        self._sk = secret_key
        self._endpoint = endpoint
        self._region = region
        self._bucket = bucket

    def _client(self):
        import tos  # local import — heavy dep

        return tos.TosClientV2(self._ak, self._sk, self._endpoint, self._region)

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        c = self._client()
        await asyncio.to_thread(
            c.put_object, self._bucket, key, content=data, content_type=content_type
        )
        return f"tos://{self._bucket}/{key}"

    async def download(self, key: str) -> bytes:
        c = self._client()
        resp = await asyncio.to_thread(c.get_object, self._bucket, key)
        return resp.read()

    async def delete(self, key: str) -> None:
        c = self._client()
        await asyncio.to_thread(c.delete_object, self._bucket, key)

    async def presign(self, key: str, expires: int = 3600) -> str:
        c = self._client()
        resp = await asyncio.to_thread(c.pre_signed_url, "GET", self._bucket, key, expires=expires)
        return resp.signed_url

    async def exists(self, key: str) -> bool:
        c = self._client()
        try:
            await asyncio.to_thread(c.head_object, self._bucket, key)
            return True
        except Exception:
            return False
