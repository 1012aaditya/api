"""Data access for clients, cases and requirements.

Every method takes ``organization_id`` first and filters on it. That is not a
convention here, it is the tenancy boundary: a CA firm's client list is the
most sensitive thing this product holds.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.models import (
    Client,
    ClientFact,
    ClientStatus,
    ComplianceCase,
    DocumentRequirement,
    RequirementStatus,
)
from app.models.requirement import DEFAULT_REQUIREMENTS
from app.services.tally.matching import normalize_gstin


def _digits(value: str | None) -> str:
    """Last ten digits of a phone number.

    Indian numbers arrive as +919876543210, 919876543210, 09876543210 and
    9876543210 depending on who typed them and which provider relayed them.
    Comparing the last ten digits is the only thing that matches all four.
    """
    if not value:
        return ""
    return "".join(ch for ch in value if ch.isdigit())[-10:]


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, client_id: str) -> Client | None:
        result = await self.session.execute(
            select(Client).where(
                Client.id == client_id, Client.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        status: str | None = None,
    ) -> list[Client]:
        query: Select = select(Client).where(Client.organization_id == organization_id)
        if status:
            query = query.where(Client.status == status)
        if search:
            needle = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(Client.name).like(needle),
                    func.lower(Client.business_name).like(needle),
                    func.lower(Client.gstin).like(needle),
                    func.lower(Client.client_code).like(needle),
                )
            )
        query = query.order_by(Client.name).limit(min(limit, 500)).offset(offset)
        return list((await self.session.execute(query)).scalars().all())

    async def count(self, organization_id: str, *, status: str | None = None) -> int:
        query = select(func.count()).select_from(Client).where(
            Client.organization_id == organization_id
        )
        if status:
            query = query.where(Client.status == status)
        return int((await self.session.execute(query)).scalar_one())

    async def find_by_phone(self, organization_id: str, phone: str) -> Client | None:
        """Resolve an inbound message to a client.

        Compared on the last ten digits rather than the raw string, because the
        same person is +91 98765 43210 in the firm's records and 919876543210
        in the webhook.
        """
        tail = _digits(phone)
        if not tail:
            return None
        candidates = await self.session.execute(
            select(Client).where(
                Client.organization_id == organization_id,
                Client.status == ClientStatus.ACTIVE,
            )
        )
        for client in candidates.scalars():
            if tail in {_digits(client.whatsapp_phone), _digits(client.phone)}:
                return client
        return None

    async def find_by_gstin(self, organization_id: str, gstin: str | None) -> Client | None:
        normalized = normalize_gstin(gstin)
        if not normalized:
            return None
        result = await self.session.execute(
            select(Client).where(
                Client.organization_id == organization_id, Client.gstin == normalized
            )
        )
        return result.scalars().first()

    async def create(self, organization_id: str, **fields: object) -> Client:
        if "gstin" in fields:
            fields["gstin"] = normalize_gstin(fields.get("gstin")) or None  # type: ignore[arg-type]
        client = Client(organization_id=organization_id, **fields)  # type: ignore[arg-type]
        self.session.add(client)
        await self.session.flush()
        return client

    async def update(self, client: Client, **fields: object) -> Client:
        for key, value in fields.items():
            if value is not None and hasattr(client, key):
                setattr(client, key, value)
        await self.session.flush()
        return client


class CaseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, case_id: str) -> ComplianceCase | None:
        result = await self.session.execute(
            select(ComplianceCase).where(
                ComplianceCase.id == case_id,
                ComplianceCase.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def find(
        self, organization_id: str, client_id: str, case_type: str, period: str
    ) -> ComplianceCase | None:
        result = await self.session.execute(
            select(ComplianceCase).where(
                ComplianceCase.organization_id == organization_id,
                ComplianceCase.client_id == client_id,
                ComplianceCase.type == case_type,
                ComplianceCase.period == period,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: str,
        *,
        client_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ComplianceCase]:
        query = select(ComplianceCase).where(
            ComplianceCase.organization_id == organization_id
        )
        if client_id:
            query = query.where(ComplianceCase.client_id == client_id)
        if status:
            query = query.where(ComplianceCase.status == status)
        query = (
            query.order_by(ComplianceCase.deadline.is_(None), ComplianceCase.deadline)
            .limit(min(limit, 500))
            .offset(offset)
        )
        return list((await self.session.execute(query)).scalars().all())

    async def create(
        self,
        *,
        organization_id: str,
        client_id: str,
        case_type: str,
        period: str,
        deadline: dt.date | None = None,
        requirements: list[tuple[str, bool]] | None = None,
    ) -> ComplianceCase:
        """Create a case and the documents it asks for.

        The requirement list comes from ``DEFAULT_REQUIREMENTS`` unless the
        caller supplies one — a starting point the CA recognises, editable
        afterwards, never a rule they cannot change.
        """
        case = ComplianceCase(
            organization_id=organization_id,
            client_id=client_id,
            type=case_type,
            period=period,
            deadline=deadline,
        )
        self.session.add(case)
        await self.session.flush()

        wanted = requirements if requirements is not None else list(
            DEFAULT_REQUIREMENTS.get(case_type, ())
        )
        for document_type, required in wanted:
            self.session.add(
                DocumentRequirement(
                    organization_id=organization_id,
                    case_id=case.id,
                    document_type=document_type,
                    required=required,
                    deadline=deadline,
                )
            )
        await self.session.flush()
        return case

    async def counts_by_status(self, organization_id: str) -> dict[str, int]:
        rows = await self.session.execute(
            select(ComplianceCase.status, func.count())
            .where(ComplianceCase.organization_id == organization_id)
            .group_by(ComplianceCase.status)
        )
        return {status: int(count) for status, count in rows.all()}


class RequirementRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(
        self, organization_id: str, requirement_id: str
    ) -> DocumentRequirement | None:
        result = await self.session.execute(
            select(DocumentRequirement).where(
                DocumentRequirement.id == requirement_id,
                DocumentRequirement.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def for_case(self, organization_id: str, case_id: str) -> list[DocumentRequirement]:
        result = await self.session.execute(
            select(DocumentRequirement)
            .where(
                DocumentRequirement.organization_id == organization_id,
                DocumentRequirement.case_id == case_id,
            )
            .order_by(DocumentRequirement.document_type)
        )
        return list(result.scalars().all())

    async def outstanding_for_case(
        self, organization_id: str, case_id: str
    ) -> list[DocumentRequirement]:
        """Required documents the client still owes."""
        result = await self.session.execute(
            select(DocumentRequirement)
            .where(
                DocumentRequirement.organization_id == organization_id,
                DocumentRequirement.case_id == case_id,
                DocumentRequirement.required.is_(True),
                DocumentRequirement.status.in_(tuple(RequirementStatus.OUTSTANDING)),
            )
            .order_by(DocumentRequirement.document_type)
        )
        return list(result.scalars().all())

    async def match_document_type(
        self, organization_id: str, case_id: str, document_type: str
    ) -> DocumentRequirement | None:
        """The requirement a document of this type would satisfy, if any."""
        result = await self.session.execute(
            select(DocumentRequirement).where(
                DocumentRequirement.organization_id == organization_id,
                DocumentRequirement.case_id == case_id,
                DocumentRequirement.document_type == document_type,
            )
        )
        return result.scalar_one_or_none()


class ClientFactRepository:
    """Structured agent memory. A human's answer outranks a model's guess."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str, client_id: str, key: str) -> ClientFact | None:
        result = await self.session.execute(
            select(ClientFact).where(
                ClientFact.organization_id == organization_id,
                ClientFact.client_id == client_id,
                ClientFact.key == key,
            )
        )
        return result.scalar_one_or_none()

    async def all_for_client(
        self, organization_id: str, client_id: str, *, now: dt.datetime | None = None
    ) -> list[ClientFact]:
        """Everything still true about this client.

        A fact with an expiry that has passed is not returned. "They promised
        to send it tomorrow" is useful on the day and misleading a fortnight
        later, and the agent reasons over whatever this gives it.
        """
        moment = now or utcnow()
        result = await self.session.execute(
            select(ClientFact).where(
                ClientFact.organization_id == organization_id,
                ClientFact.client_id == client_id,
                or_(ClientFact.expires_at.is_(None), ClientFact.expires_at > moment),
            )
        )
        return list(result.scalars().all())

    async def record(
        self,
        *,
        organization_id: str,
        client_id: str,
        key: str,
        value: dict,
        source: str = "agent",
        confidence: str | None = None,
        expires_at: dt.datetime | None = None,
    ) -> ClientFact:
        """Write a fact, refusing to let the agent overwrite a human's.

        The agent may record what it heard. It may not quietly replace what a
        person entered — that is the difference between memory and revision.
        """
        existing = await self.get(organization_id, client_id, key)
        if existing is not None:
            if existing.source == "user" and source == "agent":
                return existing
            existing.value = value
            existing.source = source
            existing.confidence = confidence
            existing.expires_at = expires_at
            await self.session.flush()
            return existing

        fact = ClientFact(
            organization_id=organization_id,
            client_id=client_id,
            key=key,
            value=value,
            source=source,
            confidence=confidence,
            expires_at=expires_at,
        )
        self.session.add(fact)
        await self.session.flush()
        return fact
