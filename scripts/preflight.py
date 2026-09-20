#!/usr/bin/env python
"""Check every external dependency this deployment claims to have.

    python scripts/preflight.py
    python scripts/preflight.py --json

Each check answers one question: **is this thing actually working right now?**
Not "is a variable set" — a variable is not a connection, and the gap between
the two is where a deployment that looked fine stops sending messages on a
Tuesday.

So nothing here is inferred. Redis is checked by counting in it, because that
is what the rate limiter does. Ollama is checked by asking which models it
has loaded, because an endpoint that answers while the model was never pulled
is the most common way extraction fails. WhatsApp is checked against Meta,
because a token that has expired looks exactly like a token that works until
someone needs a message sent.

A check that cannot run says so (``skipped``). It never reports ``ok``.

Exit codes: 0 — everything required is working. 1 — something required is
broken. Optional pieces that are simply not configured yet never fail the
run, because "voice is off" is a legitimate state for a firm to be in.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import ssl
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402

Status = Literal["ok", "fail", "warn", "skipped"]

TIMEOUT = 10.0


@dataclass
class Check:
    name: str
    status: Status
    detail: str
    #: What to do about it. Only ever set when there is something to do.
    remedy: str = ""
    required: bool = True
    facts: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail" and self.required


# --- Postgres -----------------------------------------------------------


async def check_database(settings: Settings) -> Check:
    from sqlalchemy import text

    from app.db.session import dispose_engine, get_engine

    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        return Check(
            "PostgreSQL",
            "fail",
            f"could not connect ({type(exc).__name__})",
            remedy="Check DATABASE_URL. It must use the async driver: postgresql+asyncpg://",
        )
    finally:
        await dispose_engine()

    if revision is None:
        return Check(
            "PostgreSQL",
            "fail",
            "connected, but no migrations have been applied",
            remedy="Run: alembic upgrade head",
        )
    return Check("PostgreSQL", "ok", f"connected, schema at {revision}")


# --- Redis --------------------------------------------------------------


async def check_redis(settings: Settings) -> Check:
    if not settings.redis_url:
        if settings.is_production and not settings.allow_in_memory_rate_limit:
            return Check(
                "Redis",
                "fail",
                "not configured, and production refuses to start without it",
                remedy=(
                    "Set REDIS_URL. Without it the rate limiter counts inside one "
                    "process, so N API workers grant every firm N times its limit."
                ),
            )
        return Check(
            "Redis",
            "warn",
            "not configured — rate limiting is per-process",
            remedy="Fine for one worker. Set REDIS_URL before running more than one.",
            required=False,
        )

    try:
        import redis.asyncio as redis
    except ImportError:
        return Check("Redis", "fail", "the redis package is not installed",
                     remedy="pip install redis")

    client = redis.from_url(settings.redis_url, decode_responses=True)
    key = "docuparse:preflight"
    try:
        # Exercised the way the rate limiter uses it — INCR then EXPIRE —
        # rather than a PING, which a read-only replica would also answer.
        await client.delete(key)
        first = await client.incr(key)
        await client.expire(key, 10)
        await client.delete(key)
    except Exception as exc:  # noqa: BLE001
        return Check(
            "Redis",
            "fail",
            f"unreachable ({type(exc).__name__})",
            remedy="Check REDIS_URL and that the server is running and accepts writes.",
        )
    finally:
        await client.aclose()

    if first != 1:
        return Check("Redis", "fail", "counter did not start at 1",
                     remedy="Something else is writing to this key space.")
    return Check("Redis", "ok", "reachable, and counts")


# --- the model ----------------------------------------------------------


async def check_model(settings: Settings) -> Check:
    if not settings.model_tier_enabled:
        return Check(
            "Model (Ollama)",
            "skipped",
            "the model tier is switched off in EXTRACTION_TIERS",
            remedy="",
            required=False,
        )
    if not settings.provider_configured:
        missing = " and ".join(
            name
            for name, value in (
                ("AI_API_KEY", settings.ai_api_key),
                ("AI_MODEL", settings.ai_model),
            )
            if not value
        )
        return Check(
            "Model (Ollama)",
            "fail",
            f"not configured: {missing} unset",
            remedy=(
                "Extraction returns 503 until this is set. For Ollama: "
                "AI_BASE_URL=http://ollama:11434/v1, AI_API_KEY=local (unchecked "
                "by Ollama but required by the client), AI_MODEL=qwen2.5vl:7b"
            ),
        )

    url = f"{settings.ai_base_url.rstrip('/')}/models"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {settings.ai_api_key}"}
            )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "Model (Ollama)",
            "fail",
            f"{settings.ai_base_url} is unreachable ({type(exc).__name__})",
            remedy=(
                "Is Ollama running? From the app server: "
                "curl http://ollama:11434/v1/models"
            ),
        )

    if response.status_code == 404:
        # Ollama and vLLM both list models; some OpenAI-compatible gateways
        # do not. Reporting a working deployment as broken would be worse
        # than admitting the check could not run.
        return Check(
            "Model (Ollama)",
            "skipped",
            f"{settings.ai_base_url} answers, but does not list its models",
            remedy=(
                f"Could not confirm {settings.ai_model!r} is loaded. Run one "
                "document through /v1/invoices/extract to be sure."
            ),
            required=False,
        )

    if response.status_code != 200:
        return Check(
            "Model (Ollama)",
            "fail",
            f"the endpoint answered {response.status_code}",
            remedy="A 401 means AI_API_KEY is wrong for this endpoint.",
        )

    try:
        available = [m["id"] for m in response.json().get("data", []) if "id" in m]
    except Exception:  # noqa: BLE001
        return Check(
            "Model (Ollama)",
            "fail",
            "the endpoint answered, but not with a model list",
            remedy=f"Is {url} an OpenAI-compatible endpoint? Note the /v1.",
        )

    wanted = settings.ai_model or ""
    # Ollama tags models "name:tag" and will answer to the bare name, so an
    # exact-match-only check would report a working setup as broken.
    if wanted in available or any(m.split(":")[0] == wanted.split(":")[0] for m in available):
        return Check(
            "Model (Ollama)",
            "ok",
            f"{wanted} is loaded",
            facts={"available": ", ".join(available[:8]) or "none"},
        )

    return Check(
        "Model (Ollama)",
        "fail",
        f"the endpoint is up but {wanted!r} is not among its models",
        remedy=(
            f"Pull it: docker compose -f docker-compose.prod.yml exec ollama "
            f"ollama pull {wanted}"
        ),
        facts={"available": ", ".join(available[:8]) or "none"},
    )


# --- WhatsApp -----------------------------------------------------------


async def check_whatsapp(settings: Settings) -> list[Check]:
    if settings.whatsapp_provider == "mock":
        status: Status = "fail" if settings.is_production else "skipped"
        return [
            Check(
                "WhatsApp",
                status,
                "the mock provider is configured — it records messages and sends nothing",
                remedy=(
                    "Set WHATSAPP_PROVIDER=whatsapp_cloud with real credentials. "
                    "Production refuses to start on the mock."
                ),
                required=settings.is_production,
            )
        ]

    missing = [
        name
        for name, value in (
            ("WHATSAPP_PHONE_NUMBER_ID", settings.whatsapp_phone_number_id),
            ("WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token),
        )
        if not value
    ]
    if missing:
        return [
            Check(
                "WhatsApp",
                "fail",
                f"{' and '.join(missing)} unset",
                remedy="Both come from the WhatsApp Business account in Meta's dashboard.",
            )
        ]

    base = settings.whatsapp_api_base.rstrip("/")
    version = settings.whatsapp_api_version
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    checks: list[Check] = []

    # The number, which also proves the token is live and scoped to it.
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                f"{base}/{version}/{settings.whatsapp_phone_number_id}",
                params={"fields": "display_phone_number,verified_name,quality_rating"},
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        return [
            Check(
                "WhatsApp",
                "fail",
                f"could not reach Meta ({type(exc).__name__})",
                remedy="Check outbound network access to graph.facebook.com.",
            )
        ]

    body = _json_body(response)
    # Meta answers a dead token with 401, but a gateway in front of it may
    # pass the error through with a 200. Either way, an `error` key means no.
    number = body.get("display_phone_number")
    if response.status_code == 200 and "error" not in body and number:
        checks.append(
            Check(
                "WhatsApp",
                "ok",
                f"{number} ({body.get('verified_name') or 'unnamed'}) is live",
                facts={"quality": str(body.get("quality_rating") or "unknown")},
            )
        )
    elif response.status_code == 200 and "error" not in body:
        # Answered, but without the field that would prove which number this
        # is. Saying "live" here would be inventing the reassurance.
        checks.append(
            Check(
                "WhatsApp",
                "fail",
                "Meta answered, but did not say which number this is",
                remedy=(
                    "WHATSAPP_PHONE_NUMBER_ID may point at something that is not "
                    "a phone number node. It is the number's id, not the number."
                ),
            )
        )
        return checks
    else:
        # Meta's own message is the useful part; the token is never echoed.
        detail = _meta_error(response)
        checks.append(
            Check(
                "WhatsApp",
                "fail",
                f"Meta refused: {detail}",
                remedy=(
                    "A 190 means the token has expired — a temporary token lasts "
                    "24 hours; you need a permanent system-user token. A 100 "
                    "usually means WHATSAPP_PHONE_NUMBER_ID is the phone number "
                    "rather than its id."
                ),
            )
        )
        return checks

    checks.append(await _check_template(settings, headers=headers, base=base, version=version))
    return checks


async def _check_template(
    settings: Settings, *, headers: dict[str, str], base: str, version: str
) -> Check:
    """Whether the template exists and Meta has approved it.

    This is the difference between chasing a silent client and not: outside
    the 24-hour window the only thing Meta will deliver is an approved
    template, and approval is a separate review from everything else.
    """
    name = settings.whatsapp_template_name
    if not name:
        return Check(
            "WhatsApp template",
            "fail",
            "no template configured",
            remedy=(
                "Set WHATSAPP_TEMPLATE_NAME. Without it, the first chase to a "
                "client who has not messaged you in 24 hours cannot be sent at "
                "all. The body takes four variables: firm, client, period, "
                "what is outstanding."
            ),
        )

    waba = None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            owner = await client.get(
                f"{base}/{version}/{settings.whatsapp_phone_number_id}",
                params={"fields": "whatsapp_business_account"},
                headers=headers,
            )
            if owner.status_code == 200:
                waba = (owner.json().get("whatsapp_business_account") or {}).get("id")
            if not waba:
                return Check(
                    "WhatsApp template",
                    "skipped",
                    "could not find which business account owns this number",
                    remedy=(
                        f"Check {name!r} is Approved by hand in Meta's Message "
                        "Templates page."
                    ),
                    required=False,
                )

            listing = await client.get(
                f"{base}/{version}/{waba}/message_templates",
                params={"name": name, "limit": 10},
                headers=headers,
            )
    except Exception as exc:  # noqa: BLE001
        return Check(
            "WhatsApp template",
            "skipped",
            f"could not be checked ({type(exc).__name__})",
            remedy=f"Confirm {name!r} is Approved in Meta's dashboard.",
            required=False,
        )

    if listing.status_code != 200:
        return Check(
            "WhatsApp template",
            "skipped",
            f"could not be listed: {_meta_error(listing)}",
            remedy=(
                "The token may lack whatsapp_business_management. Confirm "
                f"{name!r} is Approved by hand."
            ),
            required=False,
        )

    matches = [t for t in listing.json().get("data", []) if t.get("name") == name]
    if not matches:
        return Check(
            "WhatsApp template",
            "fail",
            f"no template named {name!r} exists in this business account",
            remedy="Create and submit it in Meta's Message Templates page.",
        )

    approved = [t for t in matches if str(t.get("status", "")).upper() == "APPROVED"]
    if not approved:
        states = ", ".join(sorted({str(t.get("status")) for t in matches}))
        return Check(
            "WhatsApp template",
            "fail",
            f"{name!r} exists but is {states}, not APPROVED",
            remedy="Meta has not approved it yet. Until they do, it cannot be sent.",
        )

    # Positional variables: a count mismatch is rejected at send time (132000),
    # which would mean discovering it on a real client's chase.
    body = next(
        (c for t in approved for c in t.get("components", []) if c.get("type") == "BODY"),
        None,
    )
    if body:
        import re

        count = len(set(re.findall(r"\{\{(\d+)\}\}", body.get("text", ""))))
        if count != 4:
            return Check(
                "WhatsApp template",
                "fail",
                f"{name!r} is approved but takes {count} variables, not 4",
                remedy=(
                    "WhatsApp fills them positionally: {{1}} firm, {{2}} client, "
                    "{{3}} period, {{4}} what is outstanding. A mismatch is "
                    "rejected at send time with error 132000."
                ),
            )

    return Check("WhatsApp template", "ok", f"{name!r} is approved and takes 4 variables")


def _json_body(response: httpx.Response) -> dict:
    """Meta's body, or an empty dict. Never raises — a check that crashes on
    an unexpected shape tells the operator nothing."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


def _meta_error(response: httpx.Response) -> str:
    error = _json_body(response).get("error") or {}
    code = error.get("code")
    message = error.get("message", "")
    if code:
        return f"{message} (code {code})"
    return message or f"HTTP {response.status_code}"


# --- Caddy / TLS --------------------------------------------------------


async def check_public_url(settings: Settings) -> list[Check]:
    """Whether the dashboard's address resolves, serves TLS, and is us.

    Only meaningful in production — in development APP_URL is localhost and
    there is no certificate to have an opinion about.
    """
    if not settings.is_production:
        return [
            Check(
                "Caddy / TLS",
                "skipped",
                "not a production deployment",
                required=False,
            )
        ]

    origin = settings.app_url.rstrip("/")
    if not origin.startswith("https://"):
        return [
            Check(
                "Caddy / TLS",
                "fail",
                f"APP_URL is {origin}, which is not https",
                remedy="A production dashboard must be served over TLS.",
            )
        ]

    host = origin.removeprefix("https://").split("/")[0].split(":")[0]
    checks: list[Check] = []

    try:
        addresses = sorted({info[4][0] for info in socket.getaddrinfo(host, 443)})
        checks.append(Check("DNS", "ok", f"{host} resolves to {', '.join(addresses)}"))
    except socket.gaierror as exc:
        return [
            Check(
                "DNS",
                "fail",
                f"{host} does not resolve ({exc.strerror or exc})",
                remedy=(
                    "Point an A record at this server. Caddy cannot get a "
                    "certificate until it does, and will serve an error instead."
                ),
            )
        ]

    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=TIMEOUT) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                certificate = tls.getpeercert()
        checks.append(
            Check(
                "Caddy / TLS",
                "ok",
                f"a valid certificate is being served for {host}",
                facts={"expires": str(certificate.get("notAfter", "unknown"))},
            )
        )
    except ssl.SSLCertVerificationError as exc:
        checks.append(
            Check(
                "Caddy / TLS",
                "fail",
                f"the certificate does not verify: {exc.verify_message or exc}",
                remedy=(
                    "Usually DNS was not pointing here when Caddy first started. "
                    "Fix the record, then: docker compose restart caddy"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(
            Check(
                "Caddy / TLS",
                "fail",
                f"nothing answered on 443 ({type(exc).__name__})",
                remedy="Is Caddy running, and is port 443 open in the firewall?",
            )
        )

    return checks


# --- secrets ------------------------------------------------------------


def check_secrets(settings: Settings) -> list[Check]:
    """Values that are fine in development and dangerous the moment they are not."""
    checks: list[Check] = []
    for name, value, consequence in (
        ("JWT_SECRET", settings.jwt_secret, "anyone could mint a session for any firm"),
        (
            "WEBHOOK_SECRET",
            settings.webhook_secret or "",
            "webhook signatures could be forged",
        ),
    ):
        if not value:
            checks.append(
                Check(
                    name,
                    "warn" if name == "WEBHOOK_SECRET" else "fail",
                    "not set",
                    remedy=(
                        "Webhooks cannot be created without it."
                        if name == "WEBHOOK_SECRET"
                        else "Generate one: openssl rand -hex 32"
                    ),
                    required=name != "WEBHOOK_SECRET",
                )
            )
        elif "change" in value.lower() or "dev" in value.lower() or "insecure" in value.lower():
            checks.append(
                Check(
                    name,
                    "fail" if settings.is_production else "warn",
                    "still a development placeholder",
                    remedy=f"Generate one: openssl rand -hex 32 — otherwise {consequence}.",
                    required=settings.is_production,
                )
            )
        elif len(value) < 32:
            checks.append(
                Check(
                    name,
                    "warn",
                    f"only {len(value)} characters",
                    remedy="32 or more: openssl rand -hex 32",
                    required=False,
                )
            )
        else:
            checks.append(Check(name, "ok", "set, and not a placeholder"))

    if settings.whatsapp_provider != "mock" and not settings.whatsapp_webhook_secret:
        checks.append(
            Check(
                "WHATSAPP_WEBHOOK_SECRET",
                "fail",
                "not set, while a real WhatsApp provider is configured",
                remedy=(
                    "Anyone who learns the webhook URL could post fake client "
                    "messages into a firm's timeline. It is your Meta app secret."
                ),
            )
        )
    return checks


# --- running them -------------------------------------------------------


async def run_checks(settings: Settings) -> list[Check]:
    checks: list[Check] = []
    checks.append(await check_database(settings))
    checks.append(await check_redis(settings))
    checks.append(await check_model(settings))
    checks.extend(await check_whatsapp(settings))
    checks.extend(await check_public_url(settings))
    checks.extend(check_secrets(settings))
    return checks


SYMBOL: dict[str, str] = {
    "ok": "  ok  ",
    "fail": " FAIL ",
    "warn": " warn ",
    "skipped": " --   ",
}


def render(checks: list[Check], settings: Settings) -> str:
    width = max(len(c.name) for c in checks) + 2
    lines = [
        "",
        f"DocuParse preflight — APP_ENV={settings.app_env}",
        "=" * 62,
    ]
    for check in checks:
        lines.append(f"[{SYMBOL[check.status]}] {check.name:<{width}} {check.detail}")
        for key, value in check.facts.items():
            lines.append(f"{'':<{width + 9}}{key}: {value}")
        if check.remedy and check.status in {"fail", "warn", "skipped"}:
            lines.append(f"{'':<{width + 9}}→ {check.remedy}")
    lines.append("=" * 62)

    failures = [c for c in checks if c.failed]
    if failures:
        lines.append(
            f"{len(failures)} required check(s) failed: "
            + ", ".join(c.name for c in failures)
        )
    else:
        warnings = [c for c in checks if c.status == "warn"]
        lines.append(
            "Everything required is working."
            + (f" {len(warnings)} warning(s)." if warnings else "")
        )
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    settings = get_settings()
    checks = await run_checks(settings)

    if args.json:
        print(
            json.dumps(
                {
                    "app_env": settings.app_env,
                    "ok": not any(c.failed for c in checks),
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "detail": c.detail,
                            "remedy": c.remedy,
                            "required": c.required,
                            **({"facts": c.facts} if c.facts else {}),
                        }
                        for c in checks
                    ],
                },
                indent=2,
            )
        )
    else:
        print(render(checks, settings))

    return 1 if any(c.failed for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
