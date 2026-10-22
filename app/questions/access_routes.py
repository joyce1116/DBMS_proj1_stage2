"""Owner-only sharing management and authenticated shared-version discovery."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.auth.routes import CurrentUser
from app.questions.access import (
    InvalidGrant, QuestionAccessService, ShareRecipientNotFound, SharingResourceNotFound,
)
from app.questions.access_models import GrantRequest, QuestionGrant, QuestionGrantList
from app.questions.models import QuestionVersionList


access_router = APIRouter(tags=["question sharing"])


def get_access_service(request: Request) -> QuestionAccessService:
    return QuestionAccessService(request.app.state.db)


Service = Annotated[QuestionAccessService, Depends(get_access_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]


@access_router.get("/api/shared-questions", response_model=QuestionVersionList)
def shared_questions(user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20) -> dict:
    return service.shared(user["id"], offset, limit)


@access_router.post("/api/questions/{version_id}/grants", status_code=201, response_model=QuestionGrant)
def grant_version(version_id: str, data: GrantRequest, response: Response, user: CurrentUser, service: Service) -> dict:
    grant, created = service.grant("version", version_id, user["id"], data.username)
    response.status_code = 201 if created else 200
    return grant


@access_router.get("/api/questions/{version_id}/grants", response_model=QuestionGrantList)
def version_grants(
    version_id: str, user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.grants("version", version_id, user["id"], offset, limit)


@access_router.delete("/api/questions/{version_id}/grants/{user_id}", status_code=204)
def revoke_version(version_id: str, user_id: str, user: CurrentUser, service: Service) -> Response:
    service.revoke("version", version_id, user["id"], user_id)
    return Response(status_code=204)


@access_router.post("/api/question-banks/{bank_id}/grants", status_code=201, response_model=QuestionGrant)
def grant_bank(bank_id: str, data: GrantRequest, response: Response, user: CurrentUser, service: Service) -> dict:
    grant, created = service.grant("bank", bank_id, user["id"], data.username)
    response.status_code = 201 if created else 200
    return grant


@access_router.get("/api/question-banks/{bank_id}/grants", response_model=QuestionGrantList)
def bank_grants(
    bank_id: str, user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.grants("bank", bank_id, user["id"], offset, limit)


@access_router.delete("/api/question-banks/{bank_id}/grants/{user_id}", status_code=204)
def revoke_bank(bank_id: str, user_id: str, user: CurrentUser, service: Service) -> Response:
    service.revoke("bank", bank_id, user["id"], user_id)
    return Response(status_code=204)


def register_access_error_handlers(application) -> None:
    @application.middleware("http")
    async def private_sharing_cache(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if (path == "/api/shared-questions" or path == "/api/question-banks"
                or path.startswith("/api/question-banks/") or path == "/api/questions"
                or path.startswith("/api/questions/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(SharingResourceNotFound)
    async def resource_missing(request: Request, exc: SharingResourceNotFound):
        return JSONResponse(status_code=404, content={"detail": "Question bank not found"})

    @application.exception_handler(ShareRecipientNotFound)
    async def recipient_missing(request: Request, exc: ShareRecipientNotFound):
        return JSONResponse(status_code=404, content={"detail": "Share recipient not found"})

    @application.exception_handler(InvalidGrant)
    async def invalid_grant(request: Request, exc: InvalidGrant):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
