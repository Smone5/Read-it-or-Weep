"""File storage behind one small interface.

The app stores uploaded reports, the images extracted from them and the company
logo. Where those bytes live is a deployment choice, selected with
``STORAGE_BACKEND``:

    local  – a directory (a mounted volume in production). Default; works anywhere.
    s3     – Amazon S3 or any S3-compatible store (MinIO, Cloudflare R2, ...).
    azure  – Azure Blob Storage.
    gcs    – Google Cloud Storage.

Files are always served to browsers *through the backend*, never by handing out
storage URLs, so no provider-specific signed-URL logic is needed.
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path

from config import BACKEND_DIR, config


class Storage(ABC):
    """Blocking implementations; the async wrappers below run them in a worker thread."""

    @abstractmethod
    def put_sync(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def get_sync(self, key: str) -> bytes: ...

    @abstractmethod
    def delete_prefix_sync(self, prefix: str) -> None: ...

    async def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        await asyncio.to_thread(self.put_sync, key, data, content_type)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get_sync, key)

    async def delete_prefix(self, prefix: str) -> None:
        await asyncio.to_thread(self.delete_prefix_sync, prefix)


def _clean(key: str) -> str:
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("Invalid storage key.")
    return "/".join(parts)


class LocalStorage(Storage):
    def __init__(self, directory: str):
        root = Path(directory)
        self.root = (root if root.is_absolute() else BACKEND_DIR / root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / _clean(key)).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Invalid storage key.")
        return path

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get_sync(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def delete_prefix_sync(self, prefix: str) -> None:
        import shutil

        path = self._path(prefix)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.is_file():
            path.unlink(missing_ok=True)


class _Prefixed:
    def __init__(self, prefix: str):
        self._prefix = prefix.strip("/")

    def _key(self, key: str) -> str:
        key = _clean(key)
        return f"{self._prefix}/{key}" if self._prefix else key


class S3Storage(_Prefixed, Storage):
    def __init__(self, bucket: str, prefix: str = "", endpoint_url: str = "", region: str = ""):
        import boto3  # imported lazily so the dependency is optional

        _Prefixed.__init__(self, prefix)
        if not bucket:
            raise RuntimeError("STORAGE_BUCKET is required for STORAGE_BACKEND=s3")
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None, region_name=region or None)

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._key(key), Body=data, ContentType=content_type)

    def get_sync(self, key: str) -> bytes:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()
        except self.client.exceptions.NoSuchKey as exc:
            raise FileNotFoundError(key) from exc

    def delete_prefix_sync(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=self._key(prefix)):
            keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


class AzureBlobStorage(_Prefixed, Storage):
    def __init__(self, container: str, prefix: str = "", connection_string: str = "", account_url: str = ""):
        from azure.storage.blob import BlobServiceClient

        _Prefixed.__init__(self, prefix)
        if not container:
            raise RuntimeError("STORAGE_BUCKET (the container name) is required for STORAGE_BACKEND=azure")
        if connection_string:
            service = BlobServiceClient.from_connection_string(connection_string)
        elif account_url:
            from azure.identity import DefaultAzureCredential  # managed identity / workload identity

            service = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        else:
            raise RuntimeError("Set AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_URL")
        self.container = service.get_container_client(container)

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings

        self.container.upload_blob(self._key(key), data, overwrite=True,
                                   content_settings=ContentSettings(content_type=content_type))

    def get_sync(self, key: str) -> bytes:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self.container.download_blob(self._key(key)).readall()
        except ResourceNotFoundError as exc:
            raise FileNotFoundError(key) from exc

    def delete_prefix_sync(self, prefix: str) -> None:
        for blob in self.container.list_blobs(name_starts_with=self._key(prefix)):
            self.container.delete_blob(blob.name)


class GCSStorage(_Prefixed, Storage):
    def __init__(self, bucket: str, prefix: str = ""):
        from google.cloud import storage as gcs

        _Prefixed.__init__(self, prefix)
        if not bucket:
            raise RuntimeError("STORAGE_BUCKET is required for STORAGE_BACKEND=gcs")
        self.bucket = gcs.Client().bucket(bucket)

    def put_sync(self, key: str, data: bytes, content_type: str) -> None:
        self.bucket.blob(self._key(key)).upload_from_string(data, content_type=content_type)

    def get_sync(self, key: str) -> bytes:
        from google.api_core.exceptions import NotFound

        try:
            return self.bucket.blob(self._key(key)).download_as_bytes()
        except NotFound as exc:
            raise FileNotFoundError(key) from exc

    def delete_prefix_sync(self, prefix: str) -> None:
        for blob in self.bucket.list_blobs(prefix=self._key(prefix)):
            blob.delete()


def build_storage() -> Storage:
    backend = config.storage_backend
    if backend == "local":
        return LocalStorage(config.storage_local_dir)
    if backend == "s3":
        return S3Storage(config.storage_bucket, config.storage_prefix, config.s3_endpoint_url, config.s3_region)
    if backend == "azure":
        return AzureBlobStorage(config.storage_bucket, config.storage_prefix,
                                config.azure_connection_string, config.azure_account_url)
    if backend == "gcs":
        return GCSStorage(config.storage_bucket, config.storage_prefix)
    raise RuntimeError(f"Unknown STORAGE_BACKEND '{backend}' (use local, s3, azure or gcs)")


_storage: Storage | None = None


def storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = build_storage()
    return _storage
