"""HTTP contracts for owner-managed read/use sharing."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.auth.security import normalize_username
from app.surveys.models import StrictModel


class GrantRequest(StrictModel):
    username: Annotated[str, Field(strict=True, max_length=256)]

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)


class QuestionGrant(StrictModel):
    id: str
    resource_type: Literal["version", "bank"]
    resource_id: str
    owner_id: str
    grantee_id: str
    username: str
    created_at: datetime


class QuestionGrantList(StrictModel):
    items: list[QuestionGrant]
    total: int
    offset: int
    limit: int
