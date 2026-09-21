"""Shapes for the board."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field


class ZoneOut(BaseModel):
    key: str
    label: str
    note: str
    count: int


class CardOut(BaseModel):
    client_id: str
    name: str
    #: Derived from state on every read, never from where anyone dragged
    #: the card. A position that can disagree with the truth is a lie.
    zone: str
    #: One line saying why the card is where it is, so a person can tell at
    #: a glance without opening it.
    reason: str

    case_id: str | None = None
    period: str | None = None
    deadline: dt.date | None = None
    days_left: int | None = None

    contact_state: str
    last_contacted_at: dt.datetime | None = None
    last_response_at: dt.datetime | None = None
    outstanding: list[str] = Field(default_factory=list)

    exceptions: int = 0
    tasks_overdue: int = 0
    #: False when automation is off for them. A card idle for that reason
    #: is not the same as one nobody has got to.
    automated: bool = True


class BoardOut(BaseModel):
    generated_at: dt.datetime | None
    zones: list[ZoneOut]
    cards: list[CardOut]
