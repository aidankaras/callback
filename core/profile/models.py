"""Pydantic models for the candidate's identity and facts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Identity(BaseModel):
    """Contact details the round-trip gate must be able to recover from a PDF."""

    model_config = {"extra": "forbid"}

    name: str
    email: str
    phone: str | None = None
    links: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    graduation: str | None = None
