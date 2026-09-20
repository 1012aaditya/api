"""The CA operations API: clients, cases, exceptions, tasks, and the agent.

Session-authenticated throughout. This is a dashboard a firm's staff use, not
machine traffic, and the client list is the most sensitive thing the product
holds — an API key that leaked should not be able to enumerate it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    AuthContext,
    authenticate_session,
    get_request_id,
    require_privileged,
)
from app.core.config import Settings, get_settings
from app.core.errors import InvalidRequestError, NotFoundError
from app.db.base import utcnow
from app.db.session import get_db
from app.models import (
    ActorType,
    CaseStatus,
    Client,
    ClientStatus,
    ComplianceCase,
    Conversation,
    Document,
    DocumentType,
    ExceptionStatus,
    Message,
    RequirementStatus,
    Task,
    TaskStatus,
)
from app.repositories.clients import CaseRepository, ClientRepository, RequirementRepository
from app.repositories.operations import (
    AgentEventRepository,
    AgentPolicyRepository,
    ExceptionRepository,
    TaskRepository,
)
from app.schemas.common import ErrorResponse, SuccessResponse
from app.schemas.operations import (
    AgentEventOut,
    AgentPolicyIn,
    AgentPolicyOut,
    AgentRunIn,
    AgentRunOut,
    CaseIn,
    CaseOut,
    ClientIn,
    ClientOut,
    ClientPatch,
    CommandCentre,
    ConversationOut,
    ExceptionOut,
    MessageOut,
    RequirementOut,
    ResolveExceptionIn,
    TaskIn,
    TaskOut,
)
from app.services.ingestion import refresh_case_status
from app.services.messaging import SENT_ACTION

router = APIRouter(tags=["operations"])


def _client_out(client: Client, **extra) -> ClientOut:
    return ClientOut(
        id=client.id,
        name=client.name,
        business_name=client.business_name,
        client_code=client.client_code,
        phone=client.phone,
        whatsapp_phone=client.whatsapp_phone,
        email=client.email,
        gstin=client.gstin,
        pan=client.pan,
        status=client.status,
        preferred_channel=client.preferred_channel,
        preferred_language=client.preferred_language,
        allow_automated_contact=client.allow_automated_contact,
        automation_paused_reason=client.automation_paused_reason,
        contact_state=client.contact_state,
        last_contacted_at=client.last_contacted_at,
        last_response_at=client.last_response_at,
        created_at=client.created_at,
        **extra,
    )


def _requirement_out(requirement) -> RequirementOut:
    return RequirementOut(
        id=requirement.id,
        document_type=requirement.document_type,
        label=requirement.label,
        required=requirement.required,
        status=requirement.status,
        reason=requirement.reason,
        received_document_id=requirement.received_document_id,
        requested_at=requirement.requested_at,
        received_at=requirement.received_at,
    )


async def _names(db: AsyncSession, organization_id: str) -> dict[str, str]:
    """client_id → display name, for rows that reference a client.

    One query rather than one per row: a dashboard that issues an extra query
    per line is a dashboard that gets slow exactly when a firm gets big.
    """
    rows = await db.execute(
        select(Client.id, Client.name, Client.business_name).where(
            Client.organization_id == organization_id
        )
    )
    return {row.id: (row.business_name or row.name) for row in rows}


# --- clients ------------------------------------------------------------


@router.post(
    "/clients",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[ClientOut],
    summary="Add a client",
)
async def create_client(
    payload: ClientIn,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[ClientOut]:
    client = await ClientRepository(db).create(
        auth.organization_id, **payload.model_dump()
    )
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        client_id=client.id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="client.created",
        summary=f"{client.display_name} was added",
    )
    await db.commit()
    return SuccessResponse(request_id=request_id, data=_client_out(client))


@router.get(
    "/clients",
    response_model=SuccessResponse[list[ClientOut]],
    summary="List clients",
)
async def list_clients(
    search: str | None = Query(default=None, max_length=200),
    client_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[ClientOut]]:
    clients = await ClientRepository(db).list_for_organization(
        auth.organization_id, search=search, status=client_status, limit=limit, offset=offset
    )

    counts = await db.execute(
        select(
            ComplianceCase.client_id,
            func.count().label("open"),
            func.sum(
                func.cast(ComplianceCase.status == CaseStatus.BLOCKED, Integer)
            ).label("blocked"),
        )
        .where(
            ComplianceCase.organization_id == auth.organization_id,
            ComplianceCase.status != CaseStatus.COMPLETED,
        )
        .group_by(ComplianceCase.client_id)
    )
    by_client = {row.client_id: (int(row.open or 0), int(row.blocked or 0)) for row in counts}

    return SuccessResponse(
        request_id=request_id,
        data=[
            _client_out(
                client,
                open_cases=by_client.get(client.id, (0, 0))[0],
                blocked_cases=by_client.get(client.id, (0, 0))[1],
            )
            for client in clients
        ],
    )


@router.get(
    "/clients/{client_id}",
    response_model=SuccessResponse[ClientOut],
    responses={404: {"model": ErrorResponse}},
    summary="One client",
)
async def get_client(
    client_id: str,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[ClientOut]:
    client = await ClientRepository(db).get(auth.organization_id, client_id)
    if client is None:
        raise NotFoundError("No client with that id exists in this firm.")
    return SuccessResponse(request_id=request_id, data=_client_out(client))


@router.patch(
    "/clients/{client_id}",
    response_model=SuccessResponse[ClientOut],
    responses={404: {"model": ErrorResponse}},
    summary="Update a client",
)
async def update_client(
    client_id: str,
    payload: ClientPatch,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[ClientOut]:
    repository = ClientRepository(db)
    client = await repository.get(auth.organization_id, client_id)
    if client is None:
        raise NotFoundError("No client with that id exists in this firm.")

    changes = payload.model_dump(exclude_unset=True)
    # A human turning automated contact back on is a decision worth recording,
    # because the agent turned it off for a reason.
    if changes.get("allow_automated_contact") is True and not client.allow_automated_contact:
        client.automation_paused_reason = None
        await AgentEventRepository(db).record(
            organization_id=auth.organization_id,
            client_id=client.id,
            actor_type=ActorType.USER,
            actor_id=auth.user.id if auth.user else None,
            action="client.automation_resumed",
            summary=f"Automated contact with {client.display_name} was switched back on",
        )
    if changes.get("allow_automated_contact") is False:
        client.automation_paused_reason = "Paused by the firm."

    await repository.update(client, **changes)
    await db.commit()
    return SuccessResponse(request_id=request_id, data=_client_out(client))


# --- cases --------------------------------------------------------------


@router.post(
    "/cases",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[CaseOut],
    responses={404: {"model": ErrorResponse}},
    summary="Open a compliance case",
)
async def create_case(
    payload: CaseIn,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CaseOut]:
    client = await ClientRepository(db).get(auth.organization_id, payload.client_id)
    if client is None:
        raise NotFoundError("No client with that id exists in this firm.")

    cases = CaseRepository(db)
    if await cases.find(auth.organization_id, client.id, payload.type, payload.period):
        raise InvalidRequestError(
            f"{client.display_name} already has a {payload.type.upper()} case "
            f"for {payload.period}."
        )

    requirements = None
    if payload.document_types is not None:
        unknown = set(payload.document_types) - DocumentType.ALL
        if unknown:
            raise InvalidRequestError(f"Unknown document types: {', '.join(sorted(unknown))}.")
        requirements = [(name, True) for name in payload.document_types]

    case = await cases.create(
        organization_id=auth.organization_id,
        client_id=client.id,
        case_type=payload.type,
        period=payload.period,
        deadline=payload.deadline,
        requirements=requirements,
    )
    # Derived, not asserted: a new case is blocked because its requirements
    # say so, by the same rule that will move it off blocked later (§I).
    await refresh_case_status(db, case)
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        client_id=client.id,
        case_id=case.id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="case.created",
        summary=f"{case.label} opened for {client.display_name}",
    )
    await db.commit()

    rows = await RequirementRepository(db).for_case(auth.organization_id, case.id)
    return SuccessResponse(
        request_id=request_id,
        data=CaseOut(
            id=case.id,
            client_id=client.id,
            client_name=client.display_name,
            type=case.type,
            period=case.period,
            label=case.label,
            deadline=case.deadline,
            status=case.status,
            created_at=case.created_at,
            requirements=[_requirement_out(r) for r in rows],
            outstanding=[DocumentType.label(r.document_type) for r in rows if r.is_outstanding],
        ),
    )


@router.get(
    "/cases",
    response_model=SuccessResponse[list[CaseOut]],
    summary="List cases",
)
async def list_cases(
    client_id: str | None = Query(default=None),
    case_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[CaseOut]]:
    cases = await CaseRepository(db).list_for_organization(
        auth.organization_id, client_id=client_id, status=case_status, limit=limit, offset=offset
    )
    names = await _names(db, auth.organization_id)
    requirements = RequirementRepository(db)

    out: list[CaseOut] = []
    for case in cases:
        rows = await requirements.for_case(auth.organization_id, case.id)
        out.append(
            CaseOut(
                id=case.id,
                client_id=case.client_id,
                client_name=names.get(case.client_id),
                type=case.type,
                period=case.period,
                label=case.label,
                deadline=case.deadline,
                status=case.status,
                created_at=case.created_at,
                requirements=[_requirement_out(r) for r in rows],
                outstanding=[DocumentType.label(r.document_type) for r in rows if r.is_outstanding],
            )
        )
    return SuccessResponse(request_id=request_id, data=out)


@router.get(
    "/cases/{case_id}",
    response_model=SuccessResponse[CaseOut],
    responses={404: {"model": ErrorResponse}},
    summary="One case, with its requirements",
)
async def get_case(
    case_id: str,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CaseOut]:
    case = await CaseRepository(db).get(auth.organization_id, case_id)
    if case is None:
        raise NotFoundError("No case with that id exists in this firm.")

    rows = await RequirementRepository(db).for_case(auth.organization_id, case.id)
    client = await ClientRepository(db).get(auth.organization_id, case.client_id)
    open_exceptions = await ExceptionRepository(db).open_for_case(
        auth.organization_id, case.id
    )
    return SuccessResponse(
        request_id=request_id,
        data=CaseOut(
            id=case.id,
            client_id=case.client_id,
            client_name=client.display_name if client else None,
            type=case.type,
            period=case.period,
            label=case.label,
            deadline=case.deadline,
            status=case.status,
            created_at=case.created_at,
            requirements=[_requirement_out(r) for r in rows],
            outstanding=[DocumentType.label(r.document_type) for r in rows if r.is_outstanding],
            open_exceptions=len(open_exceptions),
        ),
    )


@router.get(
    "/clients/{client_id}/missing-documents",
    response_model=SuccessResponse[list[RequirementOut]],
    summary="What this client still owes, across every open case",
)
async def missing_documents(
    client_id: str,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[RequirementOut]]:
    client = await ClientRepository(db).get(auth.organization_id, client_id)
    if client is None:
        raise NotFoundError("No client with that id exists in this firm.")

    cases = await CaseRepository(db).list_for_organization(
        auth.organization_id, client_id=client_id
    )
    requirements = RequirementRepository(db)
    out: list[RequirementOut] = []
    for case in cases:
        if case.status == CaseStatus.COMPLETED:
            continue
        for row in await requirements.outstanding_for_case(auth.organization_id, case.id):
            out.append(_requirement_out(row))
    return SuccessResponse(request_id=request_id, data=out)


# --- exceptions ---------------------------------------------------------


@router.get(
    "/exceptions",
    response_model=SuccessResponse[list[ExceptionOut]],
    summary="What needs a human",
)
async def list_exceptions(
    exception_status: str | None = Query(default=ExceptionStatus.OPEN, alias="status"),
    client_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[ExceptionOut]]:
    items = await ExceptionRepository(db).list_for_organization(
        auth.organization_id, status=exception_status, client_id=client_id, limit=limit
    )
    names = await _names(db, auth.organization_id)
    return SuccessResponse(
        request_id=request_id,
        data=[
            ExceptionOut(
                id=item.id,
                type=item.type,
                severity=item.severity,
                message=item.message,
                status=item.status,
                client_id=item.client_id,
                client_name=names.get(item.client_id or ""),
                case_id=item.case_id,
                document_id=item.document_id,
                details=item.details,
                created_at=item.created_at,
                resolved_at=item.resolved_at,
            )
            for item in items
        ],
    )


@router.post(
    "/exceptions/{exception_id}/resolve",
    response_model=SuccessResponse[ExceptionOut],
    responses={404: {"model": ErrorResponse}},
    summary="Close an exception",
)
async def resolve_exception(
    exception_id: str,
    payload: ResolveExceptionIn,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[ExceptionOut]:
    repository = ExceptionRepository(db)
    item = await repository.get(auth.organization_id, exception_id)
    if item is None:
        raise NotFoundError("No exception with that id exists in this firm.")
    if payload.status not in ExceptionStatus.ALL:
        raise InvalidRequestError(f"{payload.status!r} is not a valid status.")

    await repository.resolve(
        item,
        status=payload.status,
        note=payload.note,
        user_id=auth.user.id if auth.user else None,
    )

    # A requirement this exception was holding goes back to being owed. It is
    # not valid — the document that arrived did not satisfy it, which is why
    # somebody was asked — and leaving it in review would mean the client's
    # real document, when it comes, is turned away by a requirement that can
    # never be settled again.
    released = None
    if item.requirement_id:
        requirement = await RequirementRepository(db).get(
            auth.organization_id, item.requirement_id
        )
        if requirement is not None and requirement.status == RequirementStatus.NEEDS_REVIEW:
            requirement.move_to(
                RequirementStatus.INVALID,
                reason=payload.note or f"An exception was {payload.status}.",
            )
            requirement.received_document_id = None
            released = requirement.label
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        client_id=item.client_id,
        case_id=item.case_id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="exception.resolved",
        summary=(
            f"An exception was marked {payload.status}"
            + (f"; {released} is owed again" if released else "")
        ),
        entity_type="exception",
        entity_id=item.id,
    )
    await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=ExceptionOut(
            id=item.id,
            type=item.type,
            severity=item.severity,
            message=item.message,
            status=item.status,
            client_id=item.client_id,
            case_id=item.case_id,
            document_id=item.document_id,
            details=item.details,
            created_at=item.created_at,
            resolved_at=item.resolved_at,
        ),
    )


# --- tasks --------------------------------------------------------------


@router.get("/tasks", response_model=SuccessResponse[list[TaskOut]], summary="Open work")
async def list_tasks(
    task_status: str | None = Query(default=TaskStatus.OPEN, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[TaskOut]]:
    tasks = await TaskRepository(db).list_for_organization(
        auth.organization_id, status=task_status, limit=limit
    )
    names = await _names(db, auth.organization_id)
    return SuccessResponse(
        request_id=request_id,
        data=[
            TaskOut(
                id=task.id,
                title=task.title,
                description=task.description,
                priority=task.priority,
                status=task.status,
                client_id=task.client_id,
                client_name=names.get(task.client_id or ""),
                case_id=task.case_id,
                due_at=task.due_at,
                created_by=task.created_by,
                created_at=task.created_at,
            )
            for task in tasks
        ],
    )


@router.post(
    "/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=SuccessResponse[TaskOut],
    summary="Add a task",
)
async def create_task(
    payload: TaskIn,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TaskOut]:
    task = await TaskRepository(db).create(
        auth.organization_id, created_by="user", **payload.model_dump()
    )
    await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=TaskOut(
            id=task.id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            client_id=task.client_id,
            case_id=task.case_id,
            due_at=task.due_at,
            created_by=task.created_by,
            created_at=task.created_at,
        ),
    )


@router.post(
    "/tasks/{task_id}/complete",
    response_model=SuccessResponse[TaskOut],
    responses={404: {"model": ErrorResponse}},
    summary="Mark a task done",
)
async def complete_task(
    task_id: str,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[TaskOut]:
    task = await TaskRepository(db).get(auth.organization_id, task_id)
    if task is None:
        raise NotFoundError("No task with that id exists in this firm.")
    task.status = TaskStatus.DONE
    task.completed_at = utcnow()
    await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=TaskOut(
            id=task.id,
            title=task.title,
            description=task.description,
            priority=task.priority,
            status=task.status,
            client_id=task.client_id,
            case_id=task.case_id,
            due_at=task.due_at,
            created_by=task.created_by,
            created_at=task.created_at,
        ),
    )


# --- the agent ----------------------------------------------------------


@router.get(
    "/agent/activity",
    response_model=SuccessResponse[list[AgentEventOut]],
    summary="What the agent has been doing",
)
async def agent_activity(
    client_id: str | None = Query(default=None),
    case_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[AgentEventOut]]:
    events = await AgentEventRepository(db).timeline(
        auth.organization_id, client_id=client_id, case_id=case_id, limit=limit, offset=offset
    )
    names = await _names(db, auth.organization_id)
    return SuccessResponse(
        request_id=request_id,
        data=[
            AgentEventOut(
                id=event.id,
                actor_type=event.actor_type,
                action=event.action,
                summary=event.summary,
                client_id=event.client_id,
                client_name=names.get(event.client_id or ""),
                case_id=event.case_id,
                details=event.details,
                created_at=event.created_at,
            )
            for event in events
        ],
    )


@router.get(
    "/agent/policy",
    response_model=SuccessResponse[AgentPolicyOut],
    summary="What the agent is allowed to do",
)
async def get_policy(
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[AgentPolicyOut]:
    policy = await AgentPolicyRepository(db).get_or_create(auth.organization_id)
    await db.commit()
    return SuccessResponse(
        request_id=request_id, data=AgentPolicyOut.model_validate(policy, from_attributes=True)
    )


@router.put(
    "/agent/policy",
    response_model=SuccessResponse[AgentPolicyOut],
    summary="Change what the agent is allowed to do",
)
async def update_policy(
    payload: AgentPolicyIn,
    # What the agent may do to a firm's clients is not a junior's setting to
    # change — nor a phished junior account's.
    auth: AuthContext = Depends(require_privileged),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[AgentPolicyOut]:
    policy = await AgentPolicyRepository(db).update(
        auth.organization_id, payload.model_dump(exclude_unset=True)
    )
    await AgentEventRepository(db).record(
        organization_id=auth.organization_id,
        actor_type=ActorType.USER,
        actor_id=auth.user.id if auth.user else None,
        action="agent.policy_changed",
        summary="The agent's permissions were changed",
        details=payload.model_dump(exclude_unset=True),
    )
    await db.commit()
    return SuccessResponse(
        request_id=request_id, data=AgentPolicyOut.model_validate(policy, from_attributes=True)
    )


@router.post(
    "/agent/run",
    response_model=SuccessResponse[AgentRunOut],
    summary="Look at everything and chase what needs chasing",
)
async def run_agent(
    payload: AgentRunIn,
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[AgentRunOut]:
    """Schedules follow-ups; it does not send them.

    The sweep queues the first rung for every blocked case that has nothing
    queued. The worker sends when each is due, so pressing this button cannot
    message two hundred clients at once by accident.
    """
    from app.services.followup import FollowUpEngine, sweep_cases_needing_chasing

    if payload.case_id:
        case = await CaseRepository(db).get(auth.organization_id, payload.case_id)
        if case is None:
            raise NotFoundError("No case with that id exists in this firm.")
        job = await FollowUpEngine(db, settings=settings).start_chasing(case)
        await db.commit()
        return SuccessResponse(
            request_id=request_id,
            data=AgentRunOut(scheduled=1 if job else 0, sent=0),
        )

    result = await sweep_cases_needing_chasing(
        db, auth.organization_id, dry_run=payload.dry_run
    )
    if payload.dry_run:
        # Nothing was queued, so nothing is committed. "Show me what you would
        # do" has to leave the database exactly as it found it.
        await db.rollback()
    else:
        await db.commit()
    return SuccessResponse(
        request_id=request_id,
        data=AgentRunOut(scheduled=result.scheduled, sent=result.sent, skipped=result.skipped),
    )


# --- conversations ------------------------------------------------------


@router.get(
    "/conversations",
    response_model=SuccessResponse[list[ConversationOut]],
    summary="WhatsApp threads",
)
async def list_conversations(
    client_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[list[ConversationOut]]:
    query = select(Conversation).where(Conversation.organization_id == auth.organization_id)
    if client_id:
        query = query.where(Conversation.client_id == client_id)
    query = query.order_by(Conversation.last_message_at.desc().nullslast()).limit(limit)
    conversations = list((await db.execute(query)).scalars().all())
    names = await _names(db, auth.organization_id)

    out: list[ConversationOut] = []
    for conversation in conversations:
        messages = list(
            (
                await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation.id)
                    .order_by(Message.created_at)
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            ConversationOut(
                id=conversation.id,
                client_id=conversation.client_id,
                client_name=names.get(conversation.client_id),
                channel=conversation.channel,
                status=conversation.status,
                last_message_at=conversation.last_message_at,
                messages=[
                    MessageOut(
                        id=message.id,
                        direction=message.direction,
                        type=message.type,
                        body=message.body,
                        status=message.status,
                        detected_intent=message.detected_intent,
                        sent_by_agent=bool(message.sent_by_agent),
                        created_at=message.created_at,
                    )
                    for message in messages
                ],
            )
        )
    return SuccessResponse(request_id=request_id, data=out)


# --- the command centre -------------------------------------------------


@router.get(
    "/command-centre",
    response_model=SuccessResponse[CommandCentre],
    summary="What needs attention today",
)
async def command_centre(
    auth: AuthContext = Depends(authenticate_session),
    db: AsyncSession = Depends(get_db),
    request_id: str = Depends(get_request_id),
) -> SuccessResponse[CommandCentre]:
    organization_id = auth.organization_id
    now = utcnow()

    case_counts = await CaseRepository(db).counts_by_status(organization_id)
    clients_total = await ClientRepository(db).count(
        organization_id, status=ClientStatus.ACTIVE
    )

    blocked_clients = await db.execute(
        select(func.count(func.distinct(ComplianceCase.client_id))).where(
            ComplianceCase.organization_id == organization_id,
            ComplianceCase.status == CaseStatus.BLOCKED,
        )
    )

    awaiting_review = await db.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.organization_id == organization_id,
            Document.status == "needs_review",
        )
    )

    # Tasks the agent raised because somebody has to pick up the phone.
    calls_required = await db.execute(
        select(func.count())
        .select_from(Task)
        .where(
            Task.organization_id == organization_id,
            Task.status.notin_(tuple(TaskStatus.CLOSED)),
            Task.title.like("Call %"),
        )
    )

    policy = await AgentPolicyRepository(db).get_or_create(organization_id)
    sent_today = await AgentEventRepository(db).count_since(
        organization_id, SENT_ACTION, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
    )
    tasks = TaskRepository(db)
    await db.commit()

    return SuccessResponse(
        request_id=request_id,
        data=CommandCentre(
            clients_total=clients_total,
            clients_blocked=int(blocked_clients.scalar_one()),
            cases_blocked=case_counts.get(CaseStatus.BLOCKED, 0),
            cases_ready=case_counts.get(CaseStatus.READY, 0),
            cases_completed=case_counts.get(CaseStatus.COMPLETED, 0),
            documents_awaiting_review=int(awaiting_review.scalar_one()),
            exceptions_open=await ExceptionRepository(db).open_count(organization_id),
            tasks_open=len(await tasks.list_for_organization(organization_id, limit=500)),
            tasks_overdue=await tasks.overdue_count(organization_id, now=now),
            calls_required=int(calls_required.scalar_one()),
            messages_sent_today=sent_today,
            message_limit_per_day=policy.max_messages_per_day,
            agent_enabled=policy.enabled,
        ),
    )
