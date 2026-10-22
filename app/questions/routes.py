"""Authenticated APIs for immutable reusable-question versions."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.auth.routes import CurrentUser
from app.questions.access import QuestionAccessService
from app.questions.models import QuestionVersion, QuestionVersionList
from app.questions.repository import QuestionRepository
from app.questions.service import (
    InvalidQuestionChange, QuestionNotFound, QuestionService, VersionConflict,
)
from app.surveys.models import QuestionInput


router = APIRouter(prefix="/api/questions", tags=["questions"])


def get_question_service(request: Request) -> QuestionService:
    db = request.app.state.db
    return QuestionService(QuestionRepository(db), QuestionAccessService(db))


Service = Annotated[QuestionService, Depends(get_question_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("", status_code=201, response_model=QuestionVersion)
def create(data: QuestionInput, user: CurrentUser, service: Service) -> dict:
    return service.create(user["id"], data.model_dump())


@router.get("", response_model=QuestionVersionList)
def list_owned(
    user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20,
) -> dict:
    """List every saved version belonging to the current user."""
    return service.list_owned(user["id"], offset, limit)


@router.get("/{version_id}", response_model=QuestionVersion)
def detail(version_id: str, user: CurrentUser, service: Service) -> dict:
    return service.detail(version_id, user["id"])


@router.get("/{version_id}/history", response_model=QuestionVersionList)
def history(
    version_id: str, user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.history(version_id, user["id"], offset, limit)


@router.post("/{version_id}/versions", status_code=201, response_model=QuestionVersion)
def revise(version_id: str, data: QuestionInput, user: CurrentUser, service: Service) -> dict:
    return service.revise(version_id, user["id"], data.model_dump())


@router.post("/{version_id}/restore", status_code=201, response_model=QuestionVersion)
def restore(version_id: str, user: CurrentUser, service: Service) -> dict:
    return service.restore(version_id, user["id"])


def register_error_handlers(application) -> None:
    @application.middleware("http")
    async def private_question_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/questions" or request.url.path.startswith("/api/questions/"):
            # Cover successes, authentication/validation failures and MongoDB
            # outages as well as domain errors; every version remains private.
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(QuestionNotFound)
    async def not_found(request: Request, exc: QuestionNotFound):
        return JSONResponse(status_code=404, content={"detail": "Question version not found"})

    @application.exception_handler(InvalidQuestionChange)
    async def invalid_change(request: Request, exc: InvalidQuestionChange):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(VersionConflict)
    async def version_conflict(request: Request, exc: VersionConflict):
        return JSONResponse(
            status_code=409,
            content={"detail": "Question version allocation conflicted or exhausted; retry later"},
        )
