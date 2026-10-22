"""Owner-only exact-version or family-wide survey usage queries."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.auth.routes import CurrentUser
from app.questions.usage import QuestionUsageService
from app.questions.usage_models import QuestionUsage


usage_router = APIRouter(prefix="/api/questions", tags=["question usage"])


def get_usage_service(request: Request) -> QuestionUsageService:
    return QuestionUsageService(request.app.state.db)


Service = Annotated[QuestionUsageService, Depends(get_usage_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]


@usage_router.get("/{version_id}/usage", response_model=QuestionUsage)
def usage(
    version_id: str, user: CurrentUser, service: Service,
    scope: Literal["version", "family"] = "version", offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.usage(version_id, user["id"], scope, offset, limit)
