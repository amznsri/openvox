"""AWS S3 / MinIO storage backend (S3-compatible)."""

from __future__ import annotations

import asyncio


from openvox.storage.base import StorageBackend


class S3Storage(StorageBackend):
    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint: str = "",
        access_key: str = "",
        secret_key: str = "",
    ) -> None:
        self._bucket = bucket
        self._region = region
        self._endpoint = endpoint or None
        self._ak = access_key
        self._sk = secret_key

    def _client(self):
        import boto3

        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint,
            aws_access_key_id=self._ak or None,
            aws_secret_access_key=self._sk or None,
        )

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        c = self._client()
        await asyncio.to_thread(
            c.put_object, Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )
        return f"s3://{self._bucket}/{key}"

    async def download(self, key: str) -> bytes:
        c = self._client()
        resp = await asyncio.to_thread(c.get_object, Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    async def delete(self, key: str) -> None:
        c = self._client()
        await asyncio.to_thread(c.delete_object, Bucket=self._bucket, Key=key)

    async def presign(self, key: str, expires: int = 3600) -> str:
        c = self._client()
        url = await asyncio.to_thread(
            c.generate_presigned_url,
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires,
        )
        return url

    async def exists(self, key: str) -> bool:
        c = self._client()
        try:
            await asyncio.to_thread(c.head_object, Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False
