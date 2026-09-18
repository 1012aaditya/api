"""Object storage for uploaded documents (§23, §24).

Two drivers behind one interface: the local filesystem for development, and
any S3-compatible bucket for everything else. Documents are customer business
data, so nothing here ever makes an object public — reads go through a
short-lived signed URL or through the API itself.
"""

from __future__ import annotations

import datetime as dt
import posixpath
import re
from pathlib import Path
from typing import Protocol

import anyio

from app.core.config import Settings, get_settings
from app.core.errors import DocuParseError
from app.core.logging import get_logger

logger = get_logger("docuparse.storage")

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,499}$")


class StorageError(DocuParseError):
    code = "storage_error"
    status_code = 500
    message = "The document could not be stored."


def build_storage_key(
    *, organization_id: str, document_id: str, content_type: str, now: dt.datetime
) -> str:
    extension = {
        "application/pdf": "pdf",
        "image/png": "png",
        "image/jpeg": "jpg",
    }.get(content_type, "bin")
    return f"{organization_id}/{now:%Y/%m}/{document_id}.{extension}"


def _assert_safe_key(key: str) -> None:
    # Keys are generated internally, but a traversal here would let one
    # tenant's document id reach another tenant's bytes. Cheap to assert.
    if not _SAFE_KEY.match(key) or ".." in key.split("/"):
        raise StorageError("Refusing to use an unsafe storage key.")


class ObjectStore(Protocol):
    name: str

    async def put(self, key: str, data: bytes, *, content_type: str) -> None: ...
    async def get(self, key: str) -> bytes: ...
    async def delete(self, key: str) -> None: ...
    async def signed_url(self, key: str, *, expires_in: int) -> str | None: ...


class LocalObjectStore:
    """Development driver. Not for production — no encryption, no lifecycle."""

    name = "local"

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        _assert_safe_key(key)
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            raise StorageError("Refusing to use an unsafe storage key.")
        return path

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        path = self._path(key)
        await anyio.to_thread.run_sync(lambda: path.parent.mkdir(parents=True, exist_ok=True))
        await anyio.to_thread.run_sync(lambda: path.write_bytes(data))

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await anyio.to_thread.run_sync(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError("The stored document is no longer available.") from exc

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await anyio.to_thread.run_sync(lambda: path.unlink(missing_ok=True))

    async def signed_url(self, key: str, *, expires_in: int) -> str | None:
        # There is no safe way to hand out a filesystem path as a URL.
        return None


class S3ObjectStore:
    """S3-compatible driver (AWS S3, MinIO, Cloudflare R2, ...)."""

    name = "s3"

    def __init__(self, settings: Settings) -> None:
        import boto3

        self._bucket = settings.storage_bucket
        self._expiry = settings.s3_signed_url_expiry_seconds
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        _assert_safe_key(key)
        await anyio.to_thread.run_sync(
            lambda: self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )
        )

    async def get(self, key: str) -> bytes:
        _assert_safe_key(key)

        def _read() -> bytes:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()

        try:
            return await anyio.to_thread.run_sync(_read)
        except Exception as exc:  # noqa: BLE001 — botocore raises many shapes
            logger.warning("storage.get_failed", error=type(exc).__name__)
            raise StorageError("The stored document is no longer available.") from exc

    async def delete(self, key: str) -> None:
        _assert_safe_key(key)
        await anyio.to_thread.run_sync(
            lambda: self._client.delete_object(Bucket=self._bucket, Key=key)
        )

    async def signed_url(self, key: str, *, expires_in: int | None = None) -> str | None:
        _assert_safe_key(key)
        return await anyio.to_thread.run_sync(
            lambda: self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in or self._expiry,
            )
        )


_store: ObjectStore | None = None


def build_object_store(settings: Settings | None = None) -> ObjectStore:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        if not settings.storage_bucket:
            raise StorageError("STORAGE_BACKEND=s3 requires STORAGE_BUCKET to be set.")
        return S3ObjectStore(settings)
    if settings.is_production:
        logger.warning("storage.local_backend_in_production")
    return LocalObjectStore(settings.storage_local_path)


def get_object_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = build_object_store()
    return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Test seam."""
    global _store
    _store = store


def posix_join(*parts: str) -> str:
    return posixpath.join(*parts)
