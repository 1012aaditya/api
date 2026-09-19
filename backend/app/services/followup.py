"""The follow-up ladder.

    ask → wait → remind → wait → remind → call → escalate to a person

Every interval and every limit comes from the firm's ``AgentPolicy``. Nothing
about the timing is written into this module, because the right answer differs
per firm and per client and is not ours to fix (§11).

Three things make this safe to leave running:

**It stops.** ``max_followups_per_case`` is a hard ceiling counted from jobs
that actually ran, and reaching it produces a task for a human rather than a
fourth message.

**It checks again at send time.** A schedule formed yesterday is acted on
today, and by then the client may have sent everything, opted out, or been
taken over by a human. Every branch re-reads the current state before it does
anything — a queued job is an intention, not a promise.

**It cancels itself.** When a case stops needing chasing, its queued jobs are
cancelled. A reminder that arrives after the client has already sent
everything is the fastest way to lose their patience.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    AgentJob,
    AgentJobStatus,
    AgentJobType,
    CaseStatus,
    Client,
    ComplianceCase,
    ExceptionType,
    Priority,
    Severity,
)
from app.repositories.agent_jobs import AgentJobRepository
from app.repositories.clients import CaseRepository, ClientRepository, RequirementRepository
from app.repositories.operations import AgentEventRepository, AgentPolicyRepository
from app.repositories.organizations import OrganizationRepository
from app.services.agent import AgentContext, ClientCommunicationAgent, describe_missing
from app.services.ai_policy import model_allowed
from app.services.messaging import SENT_ACTION
from app.services.planner import Plan

logger = get_logger("docuparse.followup")

#: Cases worth chasing. A completed or ready case is owed nothing.
CHASEABLE = (CaseStatus.BLOCKED, CaseStatus.IN_PROGRESS, CaseStatus.NOT_STARTED)


@dataclass
class FollowUpResult:
    scheduled: int = 0
    sent: int = 0
    escalated: int = 0
    cancelled: int = 0
    skipped: list[str] = field(default_factory=list)
    #: Set when the rung did not happen for a reason that may pass — the
    #: provider was unreachable, the daily cap was already spent. The job goes
    #: back on the queue instead of being consumed, because a reminder nobody
    #: sent and nobody retried is a client who never hears from the firm
    #: again.
    retry_in_seconds: int | None = None


def _next_delay(policy, attempt_number: int) -> tuple[int, str]:
    """Hours to wait before attempt ``n``, and what that attempt should be.

    The ladder lengthens deliberately: a client who has not answered two
    messages is not going to answer a third sent an hour later.
    """
    if attempt_number <= 1:
        return policy.first_reminder_hours, AgentJobType.SEND_FOLLOWUP
    if attempt_number == 2:
        return policy.second_reminder_hours, AgentJobType.SEND_FOLLOWUP
    return policy.voice_call_after_hours, AgentJobType.PLACE_CALL


class FollowUpEngine:
    def __init__(self, db: AsyncSession, *, settings: Settings | None = None) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._jobs = AgentJobRepository(db)
        self._cases = CaseRepository(db)
        self._clients = ClientRepository(db)
        self._requirements = RequirementRepository(db)
        self._events = AgentEventRepository(db)

    # -- scheduling -----------------------------------------------------

    async def schedule_next(
        self,
        case: ComplianceCase,
        *,
        attempt_number: int,
        now: dt.datetime | None = None,
        in_hours: int | None = None,
    ) -> AgentJob | None:
        """Queue the next rung of the ladder, or stop and hand over.

        ``in_hours`` overrides the ladder's own spacing. It is how the planner
        says "they are mid-audit, leave them a week" — a judgement the rule
        cannot make, on a schedule the rule still bounds.
        """
        moment = now or utcnow()
        policy = await AgentPolicyRepository(self._db).get_or_create(
            case.organization_id
        )

        if not policy.enabled or not policy.allow_auto_followup:
            return None

        if attempt_number > policy.max_followups_per_case:
            await self._hand_over(case, policy, now=moment)
            return None

        if in_hours is not None:
            due = moment + dt.timedelta(hours=in_hours)
            return await self._jobs.schedule(
                organization_id=case.organization_id,
                client_id=case.client_id,
                case_id=case.id,
                type=AgentJobType.SEND_FOLLOWUP,
                # Keyed on when it is due, not on which rung it is: a decided
                # wait is not the ladder's next step and must not collide with
                # the rung that has just run.
                dedupe_key=f"followup:{case.id}:wait:{due.isoformat()}",
                available_at=due,
                payload={"attempt": attempt_number, "decided_wait_hours": in_hours},
                attempt_number=attempt_number,
            )

        hours, job_type = _next_delay(policy, attempt_number)
        if job_type == AgentJobType.PLACE_CALL and not policy.allow_voice_calls:
            # Voice is off for this firm. Rather than silently skipping a rung,
            # the ladder ends here and a person is asked to call.
            await self._hand_over(case, policy, now=moment)
            return None

        return await self._jobs.schedule(
            organization_id=case.organization_id,
            client_id=case.client_id,
            case_id=case.id,
            type=job_type,
            dedupe_key=f"followup:{case.id}:{attempt_number}",
            available_at=moment + dt.timedelta(hours=hours),
            payload={"attempt": attempt_number},
            attempt_number=attempt_number,
        )

    async def _hand_over(self, case: ComplianceCase, policy, *, now: dt.datetime) -> None:
        """The agent has done what it can. Ask a person to take it."""
        context = AgentContext(organization_id=case.organization_id)
        agent = ClientCommunicationAgent(self._db, context)
        client = await self._clients.get(case.organization_id, case.client_id)
        name = client.display_name if client else case.client_id
        missing = await self._requirements.outstanding_for_case(
            case.organization_id, case.id
        )

        await agent.create_exception(
            type=ExceptionType.MAX_FOLLOWUPS_REACHED,
            severity=Severity.HIGH,
            message=(
                f"{name} has not sent {describe_missing(missing)} after "
                f"{policy.max_followups_per_case} reminders. Someone should call them."
            ),
            dedupe_key=f"max_followups:{case.id}",
            client_id=case.client_id,
            case_id=case.id,
            details={"outstanding": [r.document_type for r in missing]},
        )
        await agent.create_task(
            title=f"Call {name} — {describe_missing(missing)} still missing",
            client_id=case.client_id,
            case_id=case.id,
            priority=Priority.HIGH,
            due_at=now,
        )
        case.status = CaseStatus.ESCALATED
        await self._jobs.cancel_for_case(
            case.organization_id, case.id, reason="handed to a person"
        )
        await self._db.flush()

    # -- running one job -------------------------------------------------

    async def run_job(self, job: AgentJob, *, now: dt.datetime | None = None) -> FollowUpResult:
        """Do one rung, having re-checked that it is still the right thing."""
        result = FollowUpResult()
        moment = now or utcnow()

        case = await self._cases.get(job.organization_id, job.case_id or "")
        if case is None:
            result.skipped.append("the case is gone")
            return result

        if case.status not in CHASEABLE:
            # Everything arrived, or a person took over, between scheduling
            # and now. Say nothing.
            await self._jobs.cancel_for_case(
                case.organization_id, case.id, reason=f"case is {case.status}"
            )
            result.cancelled += 1
            result.skipped.append(f"the case is {case.status}")
            return result

        outstanding = await self._requirements.outstanding_for_case(
            case.organization_id, case.id
        )
        if not outstanding:
            await self._jobs.cancel_for_case(
                case.organization_id, case.id, reason="nothing outstanding"
            )
            result.cancelled += 1
            result.skipped.append("nothing is outstanding any more")
            return result

        client = await self._clients.get(case.organization_id, case.client_id)
        if client is None:
            result.skipped.append("the client is gone")
            return result

        if not client.allow_automated_contact:
            await self._jobs.cancel_for_client(
                case.organization_id, client.id, reason="the client opted out"
            )
            result.cancelled += 1
            result.skipped.append("the client has opted out")
            return result

        organization = await OrganizationRepository(self._db).get(case.organization_id)
        firm_name = organization.name if organization else "your CA"

        if job.type == AgentJobType.PLACE_CALL:
            return await self._place_call(case, client, job, now=moment)

        context = AgentContext(organization_id=case.organization_id)
        agent = ClientCommunicationAgent(self._db, context)

        # Ask the planner what this case needs. It may decide the answer is
        # not another reminder — and when there is no model, or it answers
        # badly, the ladder below runs exactly as it always did.
        plan = await self.decide(case, client, firm_name=firm_name, job=job, now=moment)
        if plan is not None and plan.rejected is None and plan.action != "send_message":
            return await self._carry_out(
                plan, case=case, client=client, agent=agent, job=job, now=moment
            )

        outcome = await agent.request_missing_documents(
            case,
            firm_name=firm_name,
            now=moment,
            body=(
                plan.message
                if plan is not None and plan.rejected is None and plan.action == "send_message"
                else None
            ),
        )
        if outcome.messages_sent:
            result.sent += 1
            await self.schedule_next(
                case, attempt_number=job.attempt_number + 1, now=moment
            )
            result.scheduled += 1
        else:
            result.skipped.append(outcome.skipped or "the message was not sent")
            # Try this same rung again rather than consuming it. Advancing the
            # ladder would spend a reminder nobody received, and dropping the
            # job would leave a blocked client nobody is chasing.
            result.retry_in_seconds = outcome.retry_in_seconds
        return result

    # -- the planner -----------------------------------------------------

    async def decide(
        self,
        case: ComplianceCase,
        client: Client,
        *,
        firm_name: str,
        job: AgentJob | None = None,
        now: dt.datetime | None = None,
    ) -> Plan | None:
        """What does this case need? None means "let the rules decide".

        Returns a rejected plan rather than None when the model answered and
        the answer was unusable, so the caller can record why — a planner that
        quietly does nothing is one nobody can debug or trust.
        """
        from app.services import planner as planning

        moment = now or utcnow()
        policy = await AgentPolicyRepository(self._db).get_or_create(case.organization_id)
        if not policy.allow_ai_planning:
            return None

        allowed, reason = await model_allowed(
            self._db, case.organization_id, settings=self._settings
        )
        if not allowed:
            # The firm asked for local-only and the model is not local. The
            # ladder still chases them; only the reasoning is given up.
            logger.info("planner.skipped_remote_model", case_id=case.id)
            await self._events.record(
                organization_id=case.organization_id,
                client_id=client.id,
                case_id=case.id,
                action="agent.planning_skipped",
                summary="Planned by the rules: the configured model is not local",
                details={"reason": reason},
            )
            return None

        sent_today = await self._events.count_since(
            case.organization_id,
            SENT_ACTION,
            since=moment.replace(hour=0, minute=0, second=0, microsecond=0),
        )
        snapshot = await planning.build_snapshot(
            self._db,
            case=case,
            client=client,
            firm_name=firm_name,
            policy=policy,
            messages_left_today=max(0, policy.max_messages_per_day - sent_today),
            default_action="send_message",
            reminders_sent=(job.attempt_number - 1) if job else 0,
            now=moment,
        )
        plan = await planning.propose(snapshot, settings=self._settings)
        if plan is None:
            return None

        if plan.rejected is not None:
            # Kept on the timeline on purpose. The firm should be able to see
            # that the model suggested something and what was wrong with it.
            await self._events.record(
                organization_id=case.organization_id,
                client_id=client.id,
                case_id=case.id,
                action="agent.plan_rejected",
                summary=f"Ignored the model's suggestion: {plan.rejected}",
                details={
                    "model": plan.model,
                    "action": plan.action,
                    "reasoning": plan.reasoning,
                    "rejected": plan.rejected,
                },
            )
            return plan

        await self._events.record(
            organization_id=case.organization_id,
            client_id=client.id,
            case_id=case.id,
            action="agent.planned",
            summary=f"Decided to {plan.action.replace('_', ' ')}: {plan.reasoning}",
            details={
                "model": plan.model,
                "action": plan.action,
                "reasoning": plan.reasoning,
                "wait_hours": plan.wait_hours,
                "tokens": plan.usage_tokens,
            },
        )
        return plan

    async def _carry_out(
        self,
        plan: Plan,
        *,
        case: ComplianceCase,
        client: Client,
        agent: ClientCommunicationAgent,
        job: AgentJob,
        now: dt.datetime,
    ) -> FollowUpResult:
        """Do what the plan says, through the same doors as everything else."""
        result = FollowUpResult()
        policy = await AgentPolicyRepository(self._db).get_or_create(case.organization_id)

        if plan.action == "wait":
            scheduled = await self.schedule_next(
                case,
                attempt_number=job.attempt_number,
                now=now,
                in_hours=plan.wait_hours or policy.first_reminder_hours,
            )
            result.scheduled += 1 if scheduled else 0
            result.skipped.append(plan.reason or "waiting, as decided")
            return result

        if plan.action == "escalate":
            await self._hand_over(case, policy, now=now)
            result.escalated += 1
            return result

        if plan.action == "create_task":
            await agent.create_task(
                title=plan.task_title or f"Look at {client.display_name}",
                description=plan.task_description,
                client_id=client.id,
                case_id=case.id,
            )
            # A task for a person does not end the chase, so the ladder
            # continues behind it.
            await self.schedule_next(
                case, attempt_number=job.attempt_number + 1, now=now
            )
            result.scheduled += 1
            return result

        # do_nothing
        result.skipped.append(plan.reason or "nothing was needed")
        return result

    async def _place_call(
        self, case: ComplianceCase, client: Client, job: AgentJob, *, now: dt.datetime
    ) -> FollowUpResult:
        """Hand the rung to the voice engine, which owns the call itself."""
        from app.services.voice import VoiceEngine

        result = FollowUpResult()
        call = await VoiceEngine(self._db, settings=self._settings).place_call(
            case=case, client=client, now=now
        )
        if call is None:
            result.skipped.append("the call was not placed")
            # A call that could not be placed still advances the ladder, or the
            # case would sit here forever.
            await self.schedule_next(
                case, attempt_number=job.attempt_number + 1, now=now
            )
            result.scheduled += 1
            return result

        result.escalated += 1
        await self.schedule_next(case, attempt_number=job.attempt_number + 1, now=now)
        result.scheduled += 1
        return result

    # -- the sweep -------------------------------------------------------

    async def start_chasing(
        self, case: ComplianceCase, *, now: dt.datetime | None = None
    ) -> AgentJob | None:
        """Begin the ladder for a case nobody has chased yet."""
        pending = await self._jobs.pending_for_case(case.organization_id, case.id)
        if pending:
            return pending[0]
        already = await self._jobs.attempts_for_case(case.organization_id, case.id)
        return await self.schedule_next(case, attempt_number=already + 1, now=now)


async def _report_if_exhausted(db: AsyncSession, job: AgentJob) -> None:
    """Tell the firm when a rung has stopped being retried.

    A job that ran out of attempts is a client nobody is chasing any more.
    Left as a failed row in a queue table, that is invisible; the whole point
    of the product is that it is not.
    """
    if job.status != AgentJobStatus.FAILED:
        return

    from app.repositories.operations import ExceptionRepository

    case = await CaseRepository(db).get(job.organization_id, job.case_id or "")
    client = (
        await ClientRepository(db).get(job.organization_id, case.client_id)
        if case
        else None
    )
    name = client.display_name if client else "a client"
    await ExceptionRepository(db).raise_exception(
        organization_id=job.organization_id,
        client_id=case.client_id if case else None,
        case_id=job.case_id,
        type=ExceptionType.OTHER,
        severity=Severity.HIGH,
        message=(
            f"We could not reach {name} on WhatsApp after "
            f"{job.attempts} attempts: {job.last_error}. Nobody is chasing "
            "them until someone looks."
        ),
        dedupe_key=f"followup_failed:{job.id}",
        details={"job_type": job.type, "attempts": job.attempts},
    )


async def drain_agent_jobs(
    db: AsyncSession, *, settings: Settings, limit: int = 10, now: dt.datetime | None = None
) -> int:
    """Claim and run up to ``limit`` agent jobs. Returns how many ran."""
    engine = FollowUpEngine(db, settings=settings)
    jobs = AgentJobRepository(db)
    ran = 0

    for _ in range(limit):
        job = await jobs.claim_next(now=now)
        if job is None:
            break
        await db.commit()
        ran += 1
        try:
            result = await engine.run_job(job, now=now)
            if result.retry_in_seconds is not None:
                await jobs.mark_failed(
                    job,
                    error="; ".join(result.skipped) or "the rung did not happen",
                    retry_in_seconds=result.retry_in_seconds,
                    now=now,
                )
                await _report_if_exhausted(db, job)
            else:
                await jobs.mark_done(job, now=now)
            await db.commit()
            logger.info(
                "agent_job.ran",
                type=job.type,
                sent=result.sent,
                scheduled=result.scheduled,
                cancelled=result.cancelled,
            )
        except Exception as exc:  # noqa: BLE001 - the queue must not die with one job
            await db.rollback()
            await jobs.mark_failed(job, error=str(exc), retry_in_seconds=300, now=now)
            await db.commit()
            logger.warning("agent_job.failed", type=job.type, error=str(exc))
    return ran


async def sweep_cases_needing_chasing(
    db: AsyncSession,
    organization_id: str,
    *,
    now: dt.datetime | None = None,
    limit: int = 200,
    dry_run: bool = False,
) -> FollowUpResult:
    """Find blocked cases with nothing queued, and start their ladder.

    ``dry_run`` answers "who would you chase?" without queueing anything —
    the question a CA asks before letting this loose on their client list for
    the first time.
    """
    result = FollowUpResult()
    moment = now or utcnow()
    engine = FollowUpEngine(db)

    cases = (
        await db.execute(
            select(ComplianceCase)
            .where(
                ComplianceCase.organization_id == organization_id,
                ComplianceCase.status.in_(CHASEABLE),
            )
            .limit(limit)
        )
    ).scalars().all()

    for case in cases:
        outstanding = await RequirementRepository(db).outstanding_for_case(
            organization_id, case.id
        )
        if not outstanding:
            continue

        if dry_run:
            client = await ClientRepository(db).get(organization_id, case.client_id)
            pending = await AgentJobRepository(db).pending_for_case(
                organization_id, case.id
            )
            if pending:
                continue
            name = client.display_name if client else "a client"
            result.skipped.append(
                f"would chase {name} for {describe_missing(outstanding)}"
            )
            result.scheduled += 1
            continue

        job = await engine.start_chasing(case, now=moment)
        if job is not None:
            result.scheduled += 1
    if not dry_run:
        await db.flush()
    return result
