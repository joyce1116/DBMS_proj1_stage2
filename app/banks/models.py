"""Strict HTTP contracts for bank metadata and separately paginated entries."""

from datetime import datetime
from typing import Annotated

from pydantic import StringConstraints, model_validator

from app.questions.models import QuestionVersion
from app.surveys.models import Description, Revision, StrictModel, SurveyTitle


VersionId = Annotated[str, StringConstraints(strict=True)]


class BankCreate(StrictModel):
    title: SurveyTitle
    description: Description = ""


class BankPatch(StrictModel):
    expected_revision: Revision
    title: SurveyTitle | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        changed = self.model_fields_set & {"title", "description"}
        if not changed:
            raise ValueError("At least one bank metadata field must be supplied")
        if any(getattr(self, name) is None for name in changed):
            raise ValueError("Bank metadata fields cannot be null")
        return self


class BankSummary(StrictModel):
    id: str
    owner_id: str
    title: str
    description: str
    revision: Revision
    created_at: datetime
    updated_at: datetime
    can_manage: bool


class BankList(StrictModel):
    items: list[BankSummary]
    total: int
    offset: int
    limit: int


class BankEntryCreate(StrictModel):
    version_id: VersionId


class BankEntryReplace(BankEntryCreate):
    expected_version_id: VersionId


class BankEntry(StrictModel):
    id: str
    bank_id: str
    family_id: str
    version_id: str
    created_at: datetime
    updated_at: datetime
    question: QuestionVersion


class BankEntryList(StrictModel):
    items: list[BankEntry]
    total: int
    offset: int
    limit: int
