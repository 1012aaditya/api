"""Erasing one client, and everything the firm holds about them.

A CA firm is a processor: the invoices belong to their client, not to them,
and under the DPDP Act 2023 the person those records describe can ask for
them to be removed. A privacy policy that cannot promise this is a policy
that says no — so this exists before the policy that references it.

Two decisions shape the whole file.

**What goes.** Everything that describes the client or their business: the
client row, their cases and what each case was waiting for, every document
and its stored bytes, every value extracted from those documents, every
WhatsApp message and call, the tasks and exceptions raised about them, and
the agent's record of what it did on their behalf.

**What stays, deliberately.** The firm's usage counts. A document processed
in September is the basis of the firm's own invoice for September, and a
processor is entitled — obliged, really — to keep the record of work it was
paid for. So those rows survive with their link to the document broken:
the count remains, the connection to a person does not.

The receipt says which is which, per table, and is written to the audit log
without any of the erased content in it. An erasure that could not be
described afterwards would be indistinguishable from one that did not
happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Base, Client
from app.services.storage import ObjectStore, get_object_store

logger = get_logger("docuparse.erasure")

#: Tables holding a client_id, cleared directly. Ordered children first so a
#: foreign key never blocks the row it points at.
DIRECT = (
    "agent_jobs",
    "agent_events",
    "tasks",
    "exceptions",
    "calls",
    "messages",
    "conversations",
    "client_facts",
)

#: Kept, with the link to the document broken. The firm was paid for this
#: work and the count is their billing record; the connection to a person is
#: what has to go.
UNLINKED = ("usage_events",)


@dataclass
class ErasureReceipt:
    """What an erasure actually did, in numbers rather than content."""

    client_id: str
    #: The client's name, so a person reading the audit trail knows who was
    #: erased. Everything else about them is gone.
    client_name: str
    deleted: dict[str, int] = field(default_factory=dict)
    unlinked: dict[str, int] = field(default_factory=dict)
    #: Stored document bytes removed from the object store.
    objects_deleted: int = 0
    #: Objects the store refused to delete. Named so somebody can finish the
    #: job by hand rather than believe it is done.
    objects_failed: list[str] = field(default_factory=list)

    @property
    def rows_deleted(self) -> int:
        return sum(self.deleted.values())

    @property
    def complete(self) -> bool:
        """False when a stored object survived. The database rows are gone
        either way, so this is the only thing that can be left over."""
        return not self.objects_failed

    def as_details(self) -> dict[str, object]:
        return {
            "rows_deleted": self.rows_deleted,
            "by_table": {k: v for k, v in self.deleted.items() if v},
            "unlinked": {k: v for k, v in self.unlinked.items() if v},
            "objects_deleted": self.objects_deleted,
            "objects_failed": len(self.objects_failed),
        }


async def erase_client(
    session: AsyncSession,
    *,
    organization_id: str,
    client: Client,
    object_store: ObjectStore | None = None,
) -> ErasureReceipt:
    """Remove everything this firm holds about one client. Irreversible.

    Every statement is scoped by ``organization_id`` as well as
    ``client_id``: an id is a caller-supplied value, and one firm being able
    to erase another's client by guessing one would be the worst possible
    bug in this file (§18).
    """
    store = object_store or get_object_store()
    receipt = ErasureReceipt(client_id=client.id, client_name=client.display_name)

    tables = Base.metadata.tables
    documents = tables["documents"]
    cases = tables["compliance_cases"]

    # --- the bytes, before the rows that point at them ------------------
    keys = (
        await session.execute(
            select(documents.c.storage_key).where(
                documents.c.organization_id == organization_id,
                documents.c.client_id == client.id,
                documents.c.storage_key.is_not(None),
            )
        )
    ).scalars().all()

    for key in keys:
        try:
            await store.delete(key)
            receipt.objects_deleted += 1
        except Exception:  # noqa: BLE001
            # Recorded, never swallowed: a receipt that claimed a complete
            # erasure while a file survived would be a lie told to the
            # person who asked for it.
            logger.exception("erasure.object_delete_failed", client_id=client.id)
            receipt.objects_failed.append(key)

    document_ids = (
        await session.execute(
            select(documents.c.id).where(
                documents.c.organization_id == organization_id,
                documents.c.client_id == client.id,
            )
        )
    ).scalars().all()

    # --- billing history survives, unlinked -----------------------------
    if document_ids:
        for name in UNLINKED:
            table = tables[name]
            result = await session.execute(
                update(table)
                .where(
                    table.c.organization_id == organization_id,
                    table.c.document_id.in_(document_ids),
                )
                .values(document_id=None)
            )
            receipt.unlinked[name] = result.rowcount or 0

    # --- everything hanging off a document ------------------------------
    if document_ids:
        for name in ("webhook_deliveries", "extraction_jobs", "extractions"):
            table = tables[name]
            result = await session.execute(
                delete(table).where(table.c.document_id.in_(document_ids))
            )
            receipt.deleted[name] = result.rowcount or 0

    # --- everything hanging off a case ----------------------------------
    case_ids = (
        await session.execute(
            select(cases.c.id).where(
                cases.c.organization_id == organization_id,
                cases.c.client_id == client.id,
            )
        )
    ).scalars().all()
    if case_ids:
        requirements = tables["document_requirements"]
        result = await session.execute(
            delete(requirements).where(requirements.c.case_id.in_(case_ids))
        )
        receipt.deleted["document_requirements"] = result.rowcount or 0

    # --- the rows that name the client ----------------------------------
    for name in (*DIRECT, "documents", "compliance_cases"):
        table = tables[name]
        result = await session.execute(
            delete(table).where(
                table.c.organization_id == organization_id,
                table.c.client_id == client.id,
            )
        )
        receipt.deleted[name] = result.rowcount or 0

    clients = tables["clients"]
    result = await session.execute(
        delete(clients).where(
            clients.c.organization_id == organization_id, clients.c.id == client.id
        )
    )
    receipt.deleted["clients"] = result.rowcount or 0

    logger.info(
        "erasure.completed",
        organization_id=organization_id,
        client_id=client.id,
        **{k: v for k, v in receipt.as_details().items() if not isinstance(v, dict)},
    )
    return receipt


def unreachable_tables() -> set[str]:
    """Tables carrying a client_id that ``erase_client`` does not clear.

    A table added later would otherwise survive an erasure silently, and the
    first anyone would know is a subject access request. The test suite
    calls this and fails when it is not empty, so the failure arrives with
    the migration rather than with the lawyer.
    """
    handled = set(DIRECT) | {"documents", "compliance_cases", "clients"}
    carrying = {
        name
        for name, table in Base.metadata.tables.items()
        if "client_id" in table.columns
    }
    return carrying - handled
