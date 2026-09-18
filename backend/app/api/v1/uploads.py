"""Shared multipart upload reading.

Used by both the synchronous and the asynchronous submission routes, so the
size guard cannot be enforced on one and forgotten on the other.
"""

from __future__ import annotations

from fastapi import UploadFile

from app.core.errors import FileTooLargeError

CHUNK_BYTES = 1024 * 1024


async def read_upload(upload: UploadFile, *, max_size_bytes: int) -> bytes:
    """Read the body in chunks, stopping the moment it exceeds the limit.

    Reading first and measuring afterwards would let anyone with a valid key
    push an arbitrarily large body into this process's memory.
    """
    buffer = bytearray()
    while chunk := await upload.read(CHUNK_BYTES):
        buffer.extend(chunk)
        if len(buffer) > max_size_bytes:
            raise FileTooLargeError(
                f"File exceeds the maximum size of {max_size_bytes} bytes.",
                details={"max_size_bytes": max_size_bytes},
            )
    return bytes(buffer)
