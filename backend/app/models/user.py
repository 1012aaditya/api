from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, String, text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ID, Base, UTCDateTime, utcnow
from app.utils.ids import prefixed_id, user_id


class Role:
    """Who may do what inside one firm.

    Deliberately three. A CA firm of five people does not want a permissions
    matrix; it wants "the partner", "whoever else runs the place", and "the
    juniors who do the work". Anything finer would be ignored or worked
    around.
    """

    #: The person who signed the firm up. There is always at least one.
    OWNER = "owner"
    #: May do everything an owner may, except be the last one standing.
    ADMIN = "admin"
    #: The daily work: clients, cases, documents, exceptions, tasks.
    STAFF = "staff"

    ALL = frozenset({OWNER, ADMIN, STAFF})

    #: May invite colleagues, change what the agent is allowed to do, and
    #: mint API keys. A junior's login being phished should not hand over the
    #: firm's automation or its integration credentials.
    PRIVILEGED = frozenset({OWNER, ADMIN})

    LABELS = {
        OWNER: "Owner",
        ADMIN: "Administrator",
        STAFF: "Staff",
    }


class Invitation(Base):
    """An open invitation for someone to join a firm.

    There is no mail server here, so the invitation is a link the firm sends
    however they already talk to each other. That means the token is a
    credential: it is stored hashed, shown once, expires, and can only be
    used one time (§23).
    """

    __tablename__ = "invitations"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=lambda: prefixed_id("inv"))
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default=Role.STAFF)
    #: SHA-256 of the token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    invited_by_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(
        ID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    def is_open(self, *, now: dt.datetime | None = None) -> bool:
        moment = now or utcnow()
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > moment
        )


class User(Base):
    """A dashboard login. API traffic authenticates with API keys, not users."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(ID, primary_key=True, default=user_id)
    organization_id: Mapped[str] = mapped_column(
        ID, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(
        String(40), nullable=False, default="owner", server_default=text("'owner'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )

    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
