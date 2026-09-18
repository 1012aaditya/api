"""Retention sweeper (§24).

Deletes the stored bytes of documents past their retention window, keeping
the metadata row so usage history and support lookups survive. Run it on a
schedule (cron, or the worker process once async jobs land).

Deliberately idempotent: a document already purged is skipped, so a retry
after a partial failure does the right thing.
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.session import get_session_factory
from app.models import DocumentStatus
from app.repositories.documents import DocumentRepository
from app.services.storage import ObjectStore, get_object_store

logger = get_logger("docuparse.retention")

DEFAULT_BATCH_SIZE = 200


async def purge_expired_documents(
    *, object_store: ObjectStore | None = None, batch_size: int = DEFAULT_BATCH_SIZE
) -> int:
    """Purge one batch. Returns how many documents had their bytes deleted."""
    store = object_store or get_object_store()
    purged = 0

    async with get_session_factory()() as session:
        documents = await DocumentRepository(session).list_expired(
            now=utcnow(), limit=batch_size
        )
        for document in documents:
            key = document.storage_key
            if key is None:
                continue
            try:
                await store.delete(key)
            except Exception:  # noqa: BLE001
                # Leave the row alone so the next run retries this document.
                logger.exception("retention.delete_failed", document_id=document.id)
                continue
            document.storage_key = None
            document.purged_at = utcnow()
            if document.status != DocumentStatus.FAILED:
                document.status = DocumentStatus.PURGED
            purged += 1
        await session.commit()

    if purged:
        logger.info("retention.purged", count=purged)
    return purged


async def purge_all_expired(*, max_batches: int = 50) -> int:
    """Sweep repeatedly until nothing is left, bounded so it always ends."""
    total = 0
    for _ in range(max_batches):
        purged = await purge_expired_documents()
        total += purged
        if purged == 0:
            break
    return total
