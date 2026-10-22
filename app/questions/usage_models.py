"""Privacy-limited usage summaries; no response data or foreign survey content."""

from typing import Literal

from app.surveys.models import QuestionId, Revision, StrictModel


class QuestionUsageOccurrence(StrictModel):
    question_id: QuestionId
    version_id: str
    version: Revision


class QuestionUsageSurvey(StrictModel):
    survey_id: str
    title: str | None
    status: Literal["draft", "published", "closed"] | None
    owned: bool
    questions: list[QuestionUsageOccurrence]


class QuestionUsage(StrictModel):
    version_id: str
    family_id: str
    scope: Literal["version", "family"]
    items: list[QuestionUsageSurvey]
    total_surveys: int
    total_occurrences: int
    offset: int
    limit: int
