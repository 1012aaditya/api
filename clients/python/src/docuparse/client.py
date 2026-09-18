"""The client.

from docuparse import DocuParse

with DocuParse(api_key="dp_live_...") as client:
    result = client.extract("invoice.pdf")
    print(result.data.invoice_number, result.data.total)
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

import httpx

from . import _files
from ._files import FileInput
from ._http import Transport
from .errors import DocuParseError
from .models import Batch, BatchSubmission, Document, Extraction, Job

__all__ = ["DocuParse"]

# No hosted endpoint exists yet — the service is self-deployed. Pointing at
# localhost is the honest default; a made-up production hostname would only
# fail later and less clearly.
DEFAULT_BASE_URL = "http://localhost:8000"

# Extraction runs the document through the pipeline before answering, so it
# gets its own, longer budget than a metadata read.
DEFAULT_TIMEOUT = 30.0
DEFAULT_EXTRACT_TIMEOUT = 180.0


def _date_param(value: Union[str, dt.date, None]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


class _Resource:
    def __init__(self, transport: Transport) -> None:
        self._t = transport


class Documents(_Resource):
    """Submit documents for background extraction, and read them back."""

    def submit(self, file: FileInput, *, timeout: Optional[float] = None) -> Job:
        """Queue one document. Returns immediately with a job to poll."""
        name, payload, content_type = _files.prepare(file)
        body = self._t.request(
            "POST",
            "/v1/documents",
            files={"file": (name, payload, content_type)},
            timeout=timeout,
        )
        return Job.from_json(body)

    def list(self, *, limit: int = 50, offset: int = 0) -> List[Document]:
        body = self._t.request(
            "GET", "/v1/documents", params={"limit": limit, "offset": offset}
        )
        return [Document.from_json(d) for d in (body.get("data") or [])]

    def get(self, document_id: str) -> Document:
        body = self._t.request("GET", f"/v1/documents/{document_id}")
        return Document.from_json(body.get("data"))

    def delete(self, document_id: str) -> None:
        """Delete the stored bytes now, without waiting for retention."""
        self._t.request("DELETE", f"/v1/documents/{document_id}")

    def extraction(self, document_id: str) -> Extraction:
        """The stored result for a document."""
        body = self._t.request("GET", f"/v1/documents/{document_id}/extraction")
        return Extraction.from_stored_json(body.get("data"))


class Jobs(_Resource):
    """Background extraction jobs."""

    def get(self, job_id: str) -> Job:
        body = self._t.request("GET", f"/v1/jobs/{job_id}")
        return Job.from_json(body.get("data"))

    def list(self, *, limit: int = 50, offset: int = 0) -> List[Job]:
        body = self._t.request(
            "GET", "/v1/jobs", params={"limit": limit, "offset": offset}
        )
        return [Job.from_json(j) for j in (body.get("data") or [])]

    def wait(
        self,
        job_id: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
    ) -> Job:
        """Poll until the job finishes.

        Returns the job whether it succeeded or failed — a failed job is an
        answer, not an exception. Raises ``TimeoutError`` only if the wait runs
        out, and the job keeps running on the server regardless.
        """
        deadline = time.monotonic() + timeout
        while True:
            job = self.get(job_id)
            if job.done:
                return job
            if time.monotonic() + poll_interval > deadline:
                raise TimeoutError(
                    f"Job {job_id} was still {job.status} after {timeout:g}s. "
                    "It is still running on the server."
                )
            time.sleep(poll_interval)


class Batches(_Resource):
    """Bulk upload: many documents in one request."""

    def create(
        self,
        files: Union[Iterable[FileInput], str, "os.PathLike[str]"],
        *,
        name: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> BatchSubmission:
        """Upload a list of files, or every supported file in a directory.

        Read the ``rejected`` list on the result. A file the server could not
        accept comes back named, with its reason — it is not queued, and it is
        not silently dropped either.
        """
        prepared = _files.prepare_many(files)
        data = {"name": name} if name else None
        body = self._t.request(
            "POST",
            "/v1/batches",
            files=prepared,
            data=data,
            timeout=timeout if timeout is not None else DEFAULT_EXTRACT_TIMEOUT,
        )
        return BatchSubmission.from_json(body)

    def get(self, batch_id: str) -> Batch:
        body = self._t.request("GET", f"/v1/batches/{batch_id}")
        return Batch.from_json(body.get("data"))

    def list(self, *, limit: int = 50, offset: int = 0) -> List[Batch]:
        body = self._t.request(
            "GET", "/v1/batches", params={"limit": limit, "offset": offset}
        )
        return [Batch.from_json(b) for b in (body.get("data") or [])]

    def wait(
        self,
        batch_id: str,
        *,
        timeout: float = 1800.0,
        poll_interval: float = 3.0,
    ) -> Batch:
        """Poll until every job in the batch has finished.

        A batch with failures still counts as done — check ``batch.failed``.
        """
        deadline = time.monotonic() + timeout
        while True:
            batch = self.get(batch_id)
            if batch.done:
                return batch
            if time.monotonic() + poll_interval > deadline:
                raise TimeoutError(
                    f"Batch {batch_id} still had {batch.queued + batch.processing} "
                    f"file(s) outstanding after {timeout:g}s. It is still running "
                    "on the server."
                )
            time.sleep(poll_interval)


class Exports(_Resource):
    """CSV export, streamed."""

    def _download(
        self,
        path: str,
        destination: Union[str, "os.PathLike[str]", None],
        *,
        start: Union[str, dt.date, None],
        end: Union[str, dt.date, None],
        batch_id: Optional[str],
        timeout: Optional[float],
    ) -> Union[Path, bytes]:
        params: Dict[str, Any] = {}
        if start is not None:
            params["from"] = _date_param(start)
        if end is not None:
            params["to"] = _date_param(end)
        if batch_id:
            params["batch_id"] = batch_id

        chunks = self._t.stream_to(path, params=params, timeout=timeout)
        if destination is None:
            return b"".join(chunks)

        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Write beside the target and rename on success. A failure mid-stream
        # must not leave a half-written CSV where a whole one is expected —
        # and must not clobber last month's good export with an empty file.
        handle = tempfile.NamedTemporaryFile(
            dir=str(target.parent),
            prefix=f".{target.name}.",
            suffix=".part",
            delete=False,
        )
        partial = Path(handle.name)
        try:
            with handle:
                for chunk in chunks:
                    handle.write(chunk)
            os.replace(partial, target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        return target

    def invoices(
        self,
        destination: Union[str, "os.PathLike[str]", None] = None,
        *,
        start: Union[str, dt.date, None] = None,
        end: Union[str, dt.date, None] = None,
        batch_id: Optional[str] = None,
        timeout: Optional[float] = 300.0,
    ) -> Union[Path, bytes]:
        """One row per invoice.

        With a ``destination`` the file is streamed to disk and the path is
        returned; without one the bytes come back, which is fine for a small
        window and a bad idea for a year of invoices.

        The file starts with a UTF-8 BOM so Excel reads the rupee sign and
        Devanagari names correctly. ``csv``, ``pandas`` and Google Sheets all
        skip it; pass ``encoding="utf-8-sig"`` if you open it by hand.
        """
        return self._download(
            "/v1/exports/invoices.csv",
            destination,
            start=start,
            end=end,
            batch_id=batch_id,
            timeout=timeout,
        )

    def line_items(
        self,
        destination: Union[str, "os.PathLike[str]", None] = None,
        *,
        start: Union[str, dt.date, None] = None,
        end: Union[str, dt.date, None] = None,
        batch_id: Optional[str] = None,
        timeout: Optional[float] = 300.0,
    ) -> Union[Path, bytes]:
        """One row per line item, carrying its invoice number so the two join."""
        return self._download(
            "/v1/exports/line-items.csv",
            destination,
            start=start,
            end=end,
            batch_id=batch_id,
            timeout=timeout,
        )


class Usage(_Resource):
    """Consumption and quota."""

    def summary(self, *, days: int = 30) -> Dict[str, Any]:
        body = self._t.request("GET", "/v1/usage", params={"days": days})
        return body.get("data") or {}

    def events(self, *, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        body = self._t.request(
            "GET", "/v1/usage/events", params={"limit": limit, "offset": offset}
        )
        return list(body.get("data") or [])


class DocuParse:
    """A DocuParse API client.

    The key is read from the ``DOCUPARSE_API_KEY`` environment variable when it
    is not passed, and the base URL from ``DOCUPARSE_BASE_URL``. Keep the key in
    the environment rather than in source: it is a bearer credential, and this
    library never logs or reprs it.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        extract_timeout: float = DEFAULT_EXTRACT_TIMEOUT,
        max_retries: int = 2,
        http_client: Optional[httpx.Client] = None,
    ) -> None:
        key = api_key or os.environ.get("DOCUPARSE_API_KEY")
        if not key:
            raise DocuParseError(
                "No API key. Pass api_key= or set DOCUPARSE_API_KEY.",
                code="authentication_required",
            )
        self._api_key = key
        self.base_url = (
            base_url or os.environ.get("DOCUPARSE_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.extract_timeout = extract_timeout

        self._transport = Transport(
            key,
            self.base_url,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

        self.documents = Documents(self._transport)
        self.jobs = Jobs(self._transport)
        self.batches = Batches(self._transport)
        self.exports = Exports(self._transport)
        self.usage = Usage(self._transport)

    # -- the one call most people need ----------------------------------

    def extract(self, file: FileInput, *, timeout: Optional[float] = None) -> Extraction:
        """Extract one invoice, synchronously.

        Blocks until the document has been through the pipeline. For more than
        a handful of files use ``batches.create`` instead — it queues them and
        returns at once.
        """
        name, payload, content_type = _files.prepare(file)
        body = self._transport.request(
            "POST",
            "/v1/invoices/extract",
            files={"file": (name, payload, content_type)},
            timeout=timeout if timeout is not None else self.extract_timeout,
        )
        return Extraction.from_json(body)

    def health(self) -> Dict[str, Any]:
        """Liveness and readiness of the deployment this client points at."""
        return self._transport.request("GET", "/ready")

    # -- lifecycle ------------------------------------------------------

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> "DocuParse":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        # Never print the key. A repr ends up in logs, tracebacks and bug
        # reports, and a bearer token in any of those is a leaked credential.
        tail = self._api_key[-4:] if len(self._api_key) > 4 else "****"
        return f"DocuParse(base_url={self.base_url!r}, api_key='...{tail}')"
