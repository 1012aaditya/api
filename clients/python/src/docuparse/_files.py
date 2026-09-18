"""Turning "a file" into something httpx can post.

Callers hand us paths, open handles, raw bytes or a directory. All of it has
to end up as ``(filename, bytes_or_handle, content_type)`` triples, with a
filename the server can echo back in an error — a rejected upload that says
``<unknown>`` is useless for working out which of 200 files to re-send.
"""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import IO, Any, Iterable, List, Sequence, Tuple, Union

FileInput = Union[str, "os.PathLike[str]", bytes, bytearray, IO[bytes], Tuple[str, Any]]

# What the API accepts. Anything else is refused locally rather than spending a
# round trip to be told the same thing.
SUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg"}

_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def _content_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in _CONTENT_TYPES:
        return _CONTENT_TYPES[suffix]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def prepare(
    file: FileInput, *, default_name: str = "document.pdf"
) -> Tuple[str, Any, str]:
    """Coerce one input into an httpx multipart tuple."""
    if isinstance(file, tuple):
        name, payload = file[0], file[1]
        return str(name), payload, _content_type(str(name))

    if isinstance(file, (bytes, bytearray)):
        # Raw bytes carry no name, so the caller gets the default and should
        # pass a (name, bytes) tuple if the name matters to them.
        return default_name, bytes(file), _content_type(default_name)

    if isinstance(file, (str, os.PathLike)):
        path = Path(file)
        return path.name, path.read_bytes(), _content_type(path.name)

    # An open binary handle. `.name` is usually the path it was opened from.
    name = getattr(file, "name", None)
    filename = Path(str(name)).name if name else default_name
    return filename, file, _content_type(filename)


def prepare_many(
    files: Union[Iterable[FileInput], str, "os.PathLike[str]"],
    *,
    field: str = "files",
) -> List[Tuple[str, Tuple[str, Any, str]]]:
    """Coerce a batch: an iterable of inputs, or a directory to scan.

    A directory is walked non-recursively and sorted by name, so the batch goes
    up in the order a person would see in their file browser.
    """
    if isinstance(files, (str, os.PathLike)) and Path(files).is_dir():
        candidates: Sequence[Path] = sorted(
            entry
            for entry in Path(files).iterdir()
            if entry.is_file() and entry.suffix.lower() in SUPPORTED_SUFFIXES
        )
        if not candidates:
            raise ValueError(
                f"No PDF, PNG, JPG or JPEG files found in {os.fspath(files)!r}."
            )
        items: Iterable[FileInput] = list(candidates)
    elif isinstance(files, (str, os.PathLike, bytes, bytearray)) or hasattr(
        files, "read"
    ):
        items = [files]  # type: ignore[list-item]
    else:
        items = list(files)

    prepared = [(field, prepare(item)) for item in items]
    if not prepared:
        raise ValueError("No files to upload.")
    return prepared
