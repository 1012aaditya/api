"""Data access for the Tally integration. Every method is organization-scoped."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Ledger, LedgerAlias, TallySettings
from app.services.tally.ledgers import ParsedLedger
from app.services.tally.matching import normalize_gstin, normalize_name


class LedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_organization(
        self, organization_id: str, *, limit: int = 500, offset: int = 0, search: str | None = None
    ) -> list[Ledger]:
        query = select(Ledger).where(Ledger.organization_id == organization_id)
        if search:
            needle = f"%{search.strip().lower()}%"
            query = query.where(func.lower(Ledger.name).like(needle))
        query = query.order_by(Ledger.name).limit(min(limit, 2000)).offset(offset)
        return list((await self.session.execute(query)).scalars().all())

    async def all_for_matching(self, organization_id: str) -> list[Ledger]:
        """Every active ledger, for building a matcher once per run."""
        result = await self.session.execute(
            select(Ledger).where(
                Ledger.organization_id == organization_id, Ledger.is_active.is_(True)
            )
        )
        return list(result.scalars().all())

    async def get(self, organization_id: str, ledger_id: str) -> Ledger | None:
        result = await self.session.execute(
            select(Ledger).where(
                Ledger.id == ledger_id, Ledger.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def count(self, organization_id: str) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(Ledger)
            .where(Ledger.organization_id == organization_id)
        )
        return int(result.scalar_one())

    async def replace_all(
        self, organization_id: str, parsed: list[ParsedLedger]
    ) -> tuple[int, int]:
        """Swap in a freshly exported master, carrying confirmed matches over.

        A replace rather than a merge, because the export *is* the truth: a
        ledger the customer deleted in Tally should stop being offered here.

        The aliases are snapshotted *before* the delete. They hang off
        ``ledgers`` with ``ON DELETE CASCADE``, so by the time the new rows
        exist the old aliases are already gone — and those corrections are
        precisely the thing that makes this worth using by month three.
        Re-keying them by ledger *name* is what survives the swap.

        Returns (imported, aliases_kept).
        """
        by_old_id = {
            ledger.id: ledger.name
            for ledger in await self.all_for_matching(organization_id)
        }

        snapshot = [
            (alias.ledger_id, alias.key_type, alias.match_key, alias.confirmed_by_user_id)
            for alias in (
                await self.session.execute(
                    select(LedgerAlias).where(
                        LedgerAlias.organization_id == organization_id
                    )
                )
            )
            .scalars()
            .all()
        ]

        await self.session.execute(
            delete(LedgerAlias).where(LedgerAlias.organization_id == organization_id)
        )
        await self.session.execute(
            delete(Ledger).where(Ledger.organization_id == organization_id)
        )
        await self.session.flush()

        created = [
            Ledger(
                organization_id=organization_id,
                name=entry.name,
                normalized_name=entry.normalized_name,
                gstin=normalize_gstin(entry.gstin) or None,
                parent_group=entry.parent_group,
                is_active=True,
            )
            for entry in parsed
        ]
        self.session.add_all(created)
        # The flush has to happen before the ids are read: `id` carries a
        # column default, so it is None on an un-flushed instance and every
        # alias below would silently rebind to nothing.
        await self.session.flush()
        fresh: dict[str, str] = {ledger.name: ledger.id for ledger in created}

        kept = 0
        for old_ledger_id, key_type, match_key, user_id in snapshot:
            name = by_old_id.get(old_ledger_id)
            new_id = fresh.get(name) if name else None
            if new_id is None:
                # That ledger is gone from their books, so the alias points at
                # nothing. Dropping it is right: we would otherwise resurrect a
                # mapping to an account they deliberately deleted.
                continue
            self.session.add(
                LedgerAlias(
                    organization_id=organization_id,
                    ledger_id=new_id,
                    key_type=key_type,
                    match_key=match_key,
                    confirmed_by_user_id=user_id,
                )
            )
            kept += 1
        await self.session.flush()
        return len(parsed), kept


class LedgerAliasRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_organization(self, organization_id: str) -> list[LedgerAlias]:
        result = await self.session.execute(
            select(LedgerAlias).where(LedgerAlias.organization_id == organization_id)
        )
        return list(result.scalars().all())

    async def confirm(
        self,
        *,
        organization_id: str,
        ledger_id: str,
        supplier_name: str | None,
        supplier_gstin: str | None,
        user_id: str | None,
    ) -> list[LedgerAlias]:
        """Remember a human's answer.

        Both keys are stored when both are known: the GSTIN survives the
        supplier renaming themselves, and the name survives an invoice that
        does not print a GSTIN.
        """
        written: list[LedgerAlias] = []
        keys: list[tuple[str, str]] = []
        gstin = normalize_gstin(supplier_gstin)
        if gstin:
            keys.append(("gstin", gstin))
        name = normalize_name(supplier_name)
        if name:
            keys.append(("name", name))

        for key_type, match_key in keys:
            existing = (
                await self.session.execute(
                    select(LedgerAlias).where(
                        LedgerAlias.organization_id == organization_id,
                        LedgerAlias.key_type == key_type,
                        LedgerAlias.match_key == match_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.ledger_id = ledger_id
                existing.confirmed_by_user_id = user_id
                written.append(existing)
                continue
            alias = LedgerAlias(
                organization_id=organization_id,
                ledger_id=ledger_id,
                key_type=key_type,
                match_key=match_key,
                confirmed_by_user_id=user_id,
            )
            self.session.add(alias)
            written.append(alias)
        await self.session.flush()
        return written

    async def forget(self, organization_id: str, alias_id: str) -> bool:
        alias = (
            await self.session.execute(
                select(LedgerAlias).where(
                    LedgerAlias.id == alias_id,
                    LedgerAlias.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if alias is None:
            return False
        await self.session.delete(alias)
        await self.session.flush()
        return True


class TallySettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, organization_id: str) -> TallySettings | None:
        result = await self.session.execute(
            select(TallySettings).where(
                TallySettings.organization_id == organization_id
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create(self, organization_id: str) -> TallySettings:
        settings = await self.get(organization_id)
        if settings is not None:
            return settings
        settings = TallySettings(organization_id=organization_id)
        self.session.add(settings)
        await self.session.flush()
        return settings

    async def update(self, organization_id: str, values: dict[str, object]) -> TallySettings:
        settings = await self.get_or_create(organization_id)
        for key, value in values.items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        await self.session.flush()
        return settings
