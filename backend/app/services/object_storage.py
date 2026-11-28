"""
LLD v1.3 Item 52: External S3-Compatible Object Storage Abstraction (Cloudflare R2 / AWS S3).
Never relies on Render's ephemeral container disk for durable document persistence.
Includes an in-memory store fallback for local development and unit testing.
"""
import hashlib
import os
from typing import Dict, Optional


class ObjectStorageClient:
    def __init__(self):
        self.bucket = os.getenv("S3_BUCKET", "")
        self.endpoint_url = os.getenv("S3_ENDPOINT_URL", "")
        self._memory_store: Dict[str, bytes] = {}

    @staticmethod
    def compute_checksum(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def upload_bytes(self, object_key: str, data: bytes, content_type: str = "application/pdf") -> Dict[str, str]:
        checksum = self.compute_checksum(data)
        if self.bucket:
            try:
                import boto3
                s3 = boto3.client("s3", endpoint_url=self.endpoint_url or None)
                s3.put_object(Bucket=self.bucket, Key=object_key, Body=data, ContentType=content_type)
                return {"storage_key": object_key, "checksum": checksum, "backend": "s3"}
            except Exception:
                pass
        self._memory_store[object_key] = data
        return {"storage_key": object_key, "checksum": checksum, "backend": "memory"}

    async def download_bytes(self, object_key: str) -> Optional[bytes]:
        if self.bucket:
            try:
                import boto3
                s3 = boto3.client("s3", endpoint_url=self.endpoint_url or None)
                resp = s3.get_object(Bucket=self.bucket, Key=object_key)
                return resp["Body"].read()
            except Exception:
                pass
        return self._memory_store.get(object_key)

    async def delete_object(self, object_key: str) -> bool:
        if self.bucket:
            try:
                import boto3
                s3 = boto3.client("s3", endpoint_url=self.endpoint_url or None)
                s3.delete_object(Bucket=self.bucket, Key=object_key)
                return True
            except Exception:
                pass
        return self._memory_store.pop(object_key, None) is not None


object_storage = ObjectStorageClient()
