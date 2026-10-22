"""Version metadata around the existing phase-one question contracts."""

from datetime import datetime
from typing import Literal

from app.surveys.models import QuestionInput, Revision, StrictModel


class QuestionVersion(StrictModel):
    id: str
    family_id: str
    owner_id: str
    version: Revision
    parent_id: str | None
    operation: Literal["create", "revise", "restore"]
    content: QuestionInput
    created_at: datetime


class QuestionVersionList(StrictModel):
    items: list[QuestionVersion]
    total: int
    offset: int
    limit: int
