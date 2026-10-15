from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response

from app.auth.routes import CurrentUser
from app.responses.models import QuestionStatisticDetail, ResponseList, SurveyStatistics
from app.responses.repository import ResponseRepository
from app.responses.service import InvalidSubmission, ResponseConflict, ResponseService
from app.surveys.models import AnswerBatch, NavigationResult, SubmissionInput, SubmissionReceipt
from app.surveys.routes import get_survey_service


router = APIRouter(tags=["responses and statistics"])


def get_response_service(request: Request) -> ResponseService:
    return ResponseService(get_survey_service(request), ResponseRepository(request.app.state.db))


Service = Annotated[ResponseService, Depends(get_response_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.post("/survey/{share_id}/navigate", response_model=NavigationResult)
def navigate(share_id: str, data: AnswerBatch, user: CurrentUser, service: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.navigate(share_id, data.survey_revision, data.answers)


@router.post("/survey/{share_id}/responses", status_code=201, response_model=SubmissionReceipt)
def submit(share_id: str, data: SubmissionInput, user: CurrentUser, service: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    result, created = service.submit(share_id, user["id"], data.model_dump())
    response.status_code = 201 if created else 200
    return result


@router.get("/api/surveys/{survey_id}/responses", response_model=ResponseList)
def responses(survey_id: str, user: CurrentUser, service: Service, response: Response,
              offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    return service.responses(survey_id, user["id"], offset, limit)


@router.get("/api/surveys/{survey_id}/statistics", response_model=SurveyStatistics)
def statistics(survey_id: str, user: CurrentUser, service: Service, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.statistics(survey_id, user["id"])


@router.get("/api/surveys/{survey_id}/statistics/{question_id}", response_model=QuestionStatisticDetail)
def question_statistics(survey_id: str, question_id: str, user: CurrentUser, service: Service,
                        response: Response, offset: Offset = 0, limit: Limit = 20):
    response.headers["Cache-Control"] = "no-store"
    return service.question_statistics(survey_id, user["id"], question_id, offset, limit)


def register_error_handlers(application) -> None:
    from fastapi.responses import JSONResponse

    @application.exception_handler(ResponseConflict)
    async def conflict(request: Request, exc: ResponseConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})

    @application.exception_handler(InvalidSubmission)
    async def invalid(request: Request, exc: InvalidSubmission):
        return JSONResponse(status_code=422, content={"detail": str(exc)}, headers={"Cache-Control": "no-store"})
