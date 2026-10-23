"""Version-isolated statistics over the caller's own survey snapshots."""

from datetime import datetime
from typing import Literal

from app.responses.models import OptionStatistic
from app.surveys.models import QuestionId, Revision, StrictModel


class VersionStatisticsSummary(StrictModel):
    version_id: str
    family_id: str
    version: Revision
    title: str
    type: Literal["single_choice", "multiple_choice", "text", "number"]
    survey_scope: Literal["owned"] = "owned"
    survey_count: int
    question_occurrence_count: int
    total_responses: int
    visited_count: int
    answer_count: int
    options: list[OptionStatistic]
    average: float | None
    min_value: int | float | None
    max_value: int | float | None


class NumericFrequency(StrictModel):
    value: int | float
    count: int


class VersionStatistics(VersionStatisticsSummary):
    distribution: list[NumericFrequency]
    distribution_total: int
    offset: int
    limit: int


class FamilyStatistics(StrictModel):
    family_id: str
    survey_scope: Literal["owned"] = "owned"
    versions: list[VersionStatisticsSummary]
    total_versions: int
    offset: int
    limit: int


class CrossSurveyContent(StrictModel):
    survey_id: str
    question_id: QuestionId
    response_id: str
    submitted_at: datetime
    value: str | int | float


class VersionContents(StrictModel):
    version_id: str
    survey_scope: Literal["owned"] = "owned"
    items: list[CrossSurveyContent]
    total: int
    offset: int
    limit: int
