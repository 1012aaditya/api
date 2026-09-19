"""The agent's planner: what to do next about one blocked case.

Everything else in this product is deterministic on purpose. This file is the
one place a model is allowed to *decide*, and it is fenced in accordingly
(§G, §17, §20):

* **It sees one case.** No ids go in, no ids come out. The executor already
  holds the client and case it is acting on, so a model cannot name a
  different firm's client however it is prompted.
* **It picks one action from five.** Anything outside that vocabulary is a
  rejected plan, not an improvisation.
* **Its message is checked before it is sent.** A draft that claims a
  document arrived, invents a document the case never asked for, or carries a
  link is thrown away — those are the specific ways a helpful model does
  damage to a CA's relationship with their client (§28).
* **It cannot get past the policy.** Sending still goes through the same gate
  as everything else: opt-out, daily cap, quiet hours, channel switches.
* **A rejected or impossible plan falls back to the ladder**, which is what
  ran before this file existed. There is no path where a model failing means
  a client is not chased.

What the model is genuinely better at than a rule: knowing that a client who
replied "CA saab, thoda time do, audit chal raha hai" should be given a week
rather than another reminder in 24 hours, and writing that sentence in the
language they wrote theirs in.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.base import utcnow
from app.models import (
    Client,
    ComplianceCase,
    Message,
    RequirementStatus,
)
from app.providers.json_utils import extract_json_object
from app.repositories.clients import ClientFactRepository, RequirementRepository

logger = get_logger("docuparse.planner")

#: Everything the planner may ask for. Adding to this list is a deliberate
#: act; the model cannot widen it.
ACTIONS = ("send_message", "wait", "escalate", "create_task", "do_nothing")

#: A message longer than this is not a WhatsApp message to a busy client.
MAX_MESSAGE_CHARS = 700

#: The longest the agent may decide to wait before looking again.
MAX_WAIT_HOURS = 720

#: Phrases that would tell a client we have something we do not have. The
#: model is told not to, and then checked, because "told not to" is not a
#: control.
_CLAIMS_RECEIPT = re.compile(
    r"\b("
    r"we (have )?received|received your|got your|thanks? (you )?for (sending|the)|"
    r"mil gaya|mil gya|mil gayi|prapt|received hai"
    r")\b",
    re.IGNORECASE,
)

#: Links are how a chase becomes a phishing message. The firm's own portal
#: can be added later, deliberately, with the domain pinned.
_HAS_URL = re.compile(r"(https?://|www\.|\b[a-z0-9-]+\.(com|in|net|org|co)\b)", re.IGNORECASE)


@dataclass
class Plan:
    """One decision, with the working shown."""

    action: str
    reasoning: str = ""
    message: str | None = None
    wait_hours: int | None = None
    reason: str | None = None
    task_title: str | None = None
    task_description: str | None = None
    #: Which model produced it, for the timeline. None means the rules did.
    model: str | None = None
    #: Why a model's plan was thrown away, when one was.
    rejected: str | None = None
    usage_tokens: int = 0

    @property
    def from_model(self) -> bool:
        return self.model is not None


@dataclass
class CaseSnapshot:
    """Everything the planner is allowed to know, and nothing else."""

    firm_name: str
    client_name: str
    language: str
    contact_state: str
    case_label: str
    period: str
    days_to_deadline: int | None
    missing: list[str]
    settled: list[str]
    reminders_sent: int
    hours_since_last_message: int | None
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    max_followups: int = 3
    messages_left_today: int = 0
    default_action: str = "send_message"
    #: The date the firm is reasoning on, so a promise can be read as due.
    today: dt.date | None = None


SYSTEM_PROMPT = """You are the assistant of an Indian chartered accountancy firm. \
Your only job is to get one client to send the documents their filing needs.

You will be given the state of one case. Choose exactly ONE action:

- send_message: write a WhatsApp message asking for what is still missing.
- wait: do nothing now, look again in N hours. Use this when the client has \
said they will send something, or has just been messaged.
- escalate: hand this case to a person at the firm. Use this when the client \
is upset, confused in a way a message will not fix, has asked for a human, \
or has ignored the agreed number of reminders.
- create_task: ask a person to do something specific, without escalating the \
whole case.
- do_nothing: nothing is needed.

Rules you must not break:
- NEVER say or imply that a document has been received. You do not know that. \
Only the firm's system knows, and it will tell you in the state.
- NEVER ask for a document that is not in the missing list.
- NEVER include a link, a URL or an attachment.
- NEVER discuss tax treatment, liability, penalties or what a client should \
do about their filing. If they ask, escalate or create a task.
- Write the message in the language the client writes in. Hinglish in Latin \
script is normal and correct for many Indian clients; match them.
- Be short. Two or three sentences. A busy shopkeeper is reading this on a \
phone.
- Be polite and never threatening. You are a firm's representative.

Reply with JSON only, in this shape:
{"reasoning": "one or two sentences on why", "action": "<one of the five>", \
"message": "<only for send_message>", "wait_hours": <only for wait>, \
"reason": "<for wait, escalate, do_nothing>", "task_title": "<for create_task>", \
"task_description": "<for create_task>"}"""


def _promise_line(snapshot: CaseSnapshot) -> str | None:
    """Say whether a promise has come due, not just that one was made.

    "They said they would send it" reads the same on the day and a week
    later, and a model told only that will wait for ever.
    """
    fact = snapshot.facts.get("document_commitment_date")
    if not isinstance(fact, dict):
        return None
    said = str(fact.get("said") or "").strip()
    raw = fact.get("date")
    if not raw:
        return f'They said: "{said}" — with no date anyone could pin down.'

    try:
        promised = dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return f'They said: "{said}".'

    today = snapshot.today or dt.date.today()
    if promised > today:
        return f'They promised to send by {promised:%d %b}, which has not come yet.'
    days = (today - promised).days
    when = "today" if days == 0 else f"{days} day{'s' if days != 1 else ''} ago"
    return (
        f'They promised to send by {promised:%d %b} ({when}) and it has not '
        f"arrived. They said: \"{said}\"."
    )


def _language_hint(language: str) -> str:
    return {
        "hinglish": "This client writes Hinglish (Hindi in Latin script). Match it.",
        "hi": "This client writes Hindi.",
        "en": "This client writes English.",
    }.get(language, f"This client's preferred language is {language!r}.")


def render_user_prompt(snapshot: CaseSnapshot) -> str:
    """The state, as plain text. Deliberately readable — a CA should be able
    to read what the model was told and judge the decision for themselves."""
    lines = [
        f"Firm: {snapshot.firm_name}",
        f"Client: {snapshot.client_name}",
        _language_hint(snapshot.language),
        f"Where the firm stands with them: {snapshot.contact_state.replace('_', ' ')}",
        f"Case: {snapshot.case_label}",
    ]
    if snapshot.days_to_deadline is not None:
        if snapshot.days_to_deadline < 0:
            lines.append(f"Deadline: {abs(snapshot.days_to_deadline)} days OVERDUE")
        else:
            lines.append(f"Deadline: in {snapshot.days_to_deadline} days")

    lines.append(
        "Still missing (you may ask only for these): "
        + (", ".join(snapshot.missing) or "nothing")
    )
    if snapshot.settled:
        lines.append(
            "Already with the firm (do NOT ask for these): " + ", ".join(snapshot.settled)
        )
    lines.append(
        f"Reminders already sent: {snapshot.reminders_sent} "
        f"(the firm allows {snapshot.max_followups} before a person takes over)"
    )
    if snapshot.hours_since_last_message is not None:
        lines.append(
            "Hours since the last message either way: "
            f"{snapshot.hours_since_last_message}"
        )
    lines.append(f"Messages the firm may still send today: {snapshot.messages_left_today}")

    promise = _promise_line(snapshot)
    if promise:
        lines.append(promise)
    other = {k: v for k, v in snapshot.facts.items() if k != "document_commitment_date"}
    if other:
        lines.append("What the client has told us before: " + json.dumps(other))

    if snapshot.recent_messages:
        lines.append("")
        lines.append("Recent conversation (oldest first):")
        for message in snapshot.recent_messages:
            who = message.get("who", "?")
            body = (message.get("body") or "").strip().replace("\n", " ")
            intent = message.get("intent")
            suffix = f"  [read as: {intent}]" if intent else ""
            lines.append(f"  {who}: {body[:300]}{suffix}")

    lines.append("")
    lines.append(f"What the firm's default rule would do now: {snapshot.default_action}")
    lines.append("Decide. JSON only.")
    return "\n".join(lines)


# --- building the snapshot ---------------------------------------------


async def build_snapshot(
    db: AsyncSession,
    *,
    case: ComplianceCase,
    client: Client,
    firm_name: str,
    policy,
    messages_left_today: int,
    default_action: str,
    reminders_sent: int = 0,
    now: dt.datetime | None = None,
) -> CaseSnapshot:
    moment = now or utcnow()
    requirements = await RequirementRepository(db).for_case(case.organization_id, case.id)

    missing = [r.label for r in requirements if r.required and r.is_outstanding]
    settled = [r.label for r in requirements if r.status in RequirementStatus.SETTLED]

    recent = (
        (
            await db.execute(
                select(Message)
                .where(Message.client_id == client.id, Message.case_id == case.id)
                .order_by(Message.created_at.desc())
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    conversation = [
        {
            "who": "firm" if message.direction == "outbound" else "client",
            "body": message.body or "(a file)",
            "intent": message.detected_intent or "",
        }
        for message in reversed(list(recent))
    ]

    last = recent[0].created_at if recent else None
    hours_since = int((moment - last).total_seconds() // 3600) if last else None

    facts = {
        fact.key: fact.value
        for fact in await ClientFactRepository(db).all_for_client(
            case.organization_id, client.id, now=moment
        )
    }

    deadline_days = (case.deadline - moment.date()).days if case.deadline else None

    return CaseSnapshot(
        firm_name=firm_name,
        client_name=client.display_name,
        language=client.preferred_language,
        contact_state=client.contact_state,
        case_label=case.label,
        period=case.period,
        days_to_deadline=deadline_days,
        missing=missing,
        settled=settled,
        reminders_sent=reminders_sent,
        hours_since_last_message=hours_since,
        recent_messages=conversation,
        facts=facts,
        max_followups=policy.max_followups_per_case,
        messages_left_today=messages_left_today,
        default_action=default_action,
        today=moment.date(),
    )


# --- asking the model ---------------------------------------------------


def _model_available(settings: Settings) -> bool:
    return bool(settings.provider_configured)


async def propose(
    snapshot: CaseSnapshot,
    *,
    settings: Settings | None = None,
    provider=None,
) -> Plan | None:
    """Ask the model what to do. Returns None when there is no model.

    Never raises: a planner that throws would stop a client being chased,
    which is worse than a planner that shrugs and lets the rules decide.
    """
    settings = settings or get_settings()
    if provider is None:
        if not _model_available(settings):
            return None
        from app.providers.registry import get_provider

        try:
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001 - configuration, not a bug
            logger.warning("planner.provider_unavailable", error=str(exc))
            return None

    if not hasattr(provider, "complete"):
        return None

    try:
        result = await provider.complete(
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": render_user_prompt(snapshot)},
            ],
            json_mode=True,
        )
    except Exception as exc:  # noqa: BLE001 - the ladder still has to run
        logger.warning("planner.call_failed", error=type(exc).__name__)
        return None

    # Returns None rather than raising when the answer is prose, an apology
    # or a refusal — all of which models do.
    raw = extract_json_object(result.text)
    if not isinstance(raw, dict):
        return Plan(
            action="",
            model=result.model,
            rejected="the model did not return JSON",
        )

    try:
        plan = Plan(
            action=(_text(raw.get("action")) or "").strip(),
            reasoning=(_text(raw.get("reasoning")) or "")[:500],
            message=_text(raw.get("message")),
            wait_hours=_as_int(raw.get("wait_hours")),
            reason=_text(raw.get("reason")),
            task_title=_text(raw.get("task_title")),
            task_description=_text(raw.get("task_description")),
            model=result.model,
            usage_tokens=(result.usage.input_tokens or 0)
            + (result.usage.output_tokens or 0),
        )
        plan.rejected = validate(plan, snapshot)
    except Exception as exc:  # noqa: BLE001 - a malformed answer is not a crash
        logger.warning("planner.unreadable_answer", error=type(exc).__name__)
        return Plan(
            action="",
            model=result.model,
            rejected="the model's answer could not be read",
        )
    return plan


def _text(value: Any) -> str | None:
    """A string, or nothing. A model may put a list or a number in any field."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def validate(plan: Plan, snapshot: CaseSnapshot) -> str | None:
    """Return why this plan may not be executed, or None if it may.

    Every check here is a thing a well-meaning model does that would embarrass
    the firm in front of their client.
    """
    if plan.action not in ACTIONS:
        return f"{plan.action!r} is not an action this agent has"

    if plan.action == "send_message":
        body = (plan.message or "").strip()
        if not body:
            return "the plan was to send a message with no message in it"
        if len(body) > MAX_MESSAGE_CHARS:
            return f"the message is {len(body)} characters; the limit is {MAX_MESSAGE_CHARS}"
        if _CLAIMS_RECEIPT.search(body):
            return "the message implies a document was received"
        if _HAS_URL.search(body):
            return "the message contains a link"
        lowered = body.lower()
        wrongly_asked = [
            label for label in snapshot.settled if label.lower() in lowered
        ]
        if wrongly_asked:
            return f"the message mentions {wrongly_asked[0]}, which the firm already has"
        if snapshot.missing and not any(
            _mentions(lowered, label) for label in snapshot.missing
        ):
            return "the message does not ask for anything that is actually missing"

    if plan.action == "wait":
        if plan.wait_hours is None or not 1 <= plan.wait_hours <= MAX_WAIT_HOURS:
            return f"a wait of {plan.wait_hours!r} hours is not usable"

    if plan.action == "create_task" and not (plan.task_title or "").strip():
        return "the plan was to create a task with no title"

    return None


def _mentions(lowered_body: str, label: str) -> bool:
    """Whether a message plausibly asks for this document.

    Loose on purpose: "bank statement" may arrive as "statement", GSTR-2B as
    "2B". The check exists to catch a message about something else entirely,
    not to police phrasing.
    """
    words = [word for word in re.split(r"[^a-z0-9]+", label.lower()) if len(word) > 2]
    return any(word in lowered_body for word in words)
