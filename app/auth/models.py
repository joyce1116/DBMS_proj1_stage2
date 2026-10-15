from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth.security import normalize_username


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(strict=True, max_length=256)]
    password: Annotated[str, Field(strict=True, min_length=1, max_length=128)]

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return normalize_username(value)


class RegisterRequest(Credentials):
    password: Annotated[str, Field(strict=True, min_length=8, max_length=128)]

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Password must contain a non-whitespace character")
        return value


class UserPublic(BaseModel):
    id: str
    username: str
    created_at: datetime


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserPublic
