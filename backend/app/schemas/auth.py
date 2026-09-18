from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=10, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)
    organization_name: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=256)


class OrganizationSummary(BaseModel):
    id: str
    name: str
    slug: str
    plan: str


class UserProfile(BaseModel):
    id: str
    email: str
    full_name: str | None
    role: str
    created_at: dt.datetime
    organization: OrganizationSummary


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: UserProfile
