"""Exact-version summaries, family pages and own-survey content pages."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.auth.routes import CurrentUser
from app.questions.statistics_models import FamilyStatistics, VersionContents, VersionStatistics
from app.questions.statistics_service import QuestionStatisticsService


statistics_router = APIRouter(prefix="/api/questions", tags=["cross-survey statistics"])


def get_statistics_service(request: Request) -> QuestionStatisticsService:
    return QuestionStatisticsService(request.app.state.db)


Service = Annotated[QuestionStatisticsService, Depends(get_statistics_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]


@statistics_router.get("/{version_id}/statistics", response_model=VersionStatistics)
def statistics(
    version_id: str, user: CurrentUser, service: Service,
    survey_scope: Literal["owned"] = "owned", offset: Offset = 0, limit: Limit = 20,
) -> dict:
    """Count answers only from surveys owned by the current user."""
    return service.statistics(version_id, user["id"], offset, limit)


@statistics_router.get("/{version_id}/statistics/family", response_model=FamilyStatistics)
def family_statistics(
    version_id: str, user: CurrentUser, service: Service,
    survey_scope: Literal["owned"] = "owned", offset: Offset = 0, limit: Limit = 20,
) -> dict:
    """The original author can page through separately counted versions."""
    return service.family_statistics(version_id, user["id"], offset, limit)


@statistics_router.get("/{version_id}/statistics/contents", response_model=VersionContents)
def contents(
    version_id: str, user: CurrentUser, service: Service,
    survey_scope: Literal["owned"] = "owned", offset: Offset = 0, limit: Limit = 20,
) -> dict:
    """Page through own-survey text/number answers without respondent identity."""
    return service.contents(version_id, user["id"], offset, limit)
