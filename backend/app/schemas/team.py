"""Request and response shapes for the people inside one firm."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, EmailStr, Field


class TeamMemberOut(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    role_label: str
    is_active: bool
    last_login_at: dt.datetime | None
    created_at: dt.datetime
    #: True for the person reading this, so a dashboard can say "you".
    is_you: bool = False


class InviteIn(BaseModel):
    email: EmailStr
    role: str = "staff"
    full_name: str | None = Field(default=None, max_length=200)


class InvitationOut(BaseModel):
    id: str
    email: str
    role: str
    #: The same wording the people table uses, so one firm's screen does not
    #: say "Administrator" in one list and "admin" in the next.
    role_label: str
    invited_by: str | None = None
    expires_at: dt.datetime
    created_at: dt.datetime


class InvitationCreated(InvitationOut):
    """The invitation, plus the one thing never shown again."""

    token: str
    accept_url: str
    #: Said out loud because the dashboard has to say it too.
    note: str = (
        "This link is shown once. Send it to them yourself — by WhatsApp, "
        "email, however you already talk. Anyone holding it can join this "
        "firm until it is used or expires."
    )


class AcceptInviteIn(BaseModel):
    token: str = Field(..., min_length=10, max_length=200)
    password: str = Field(..., min_length=10, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class MemberPatch(BaseModel):
    role: str | None = None
    is_active: bool | None = None
