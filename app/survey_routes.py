from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.routes import CurrentUser
from app.survey_models import (
    QuestionInput, QuestionOrder, SurveyCreate, SurveyDetail, SurveyList, SurveyPatch,
)
from app.survey_repository import SurveyRepository
from app.survey_service import (
    InvalidSurveyChange, RevisionConflict, SurveyNotFound, SurveyService,
)


router = APIRouter(prefix="/api/surveys", tags=["survey drafts"])


def get_survey_service(request: Request) -> SurveyService:
    return SurveyService(SurveyRepository(request.app.state.db))


Service = Annotated[SurveyService, Depends(get_survey_service)]
Revision = Annotated[int, Query(ge=1)]


@router.post("", status_code=201, response_model=SurveyDetail)
def create(data: SurveyCreate, user: CurrentUser, service: Service) -> dict:
    return service.create(user["id"], data.model_dump())


@router.get("", response_model=SurveyList)
def list_owned(
    user: CurrentUser, service: Service,
    offset: Annotated[int, Query(ge=0, le=2147483647)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict:
    return service.list_owned(user["id"], offset, limit)


@router.get("/{survey_id}", response_model=SurveyDetail)
def detail(survey_id: str, user: CurrentUser, service: Service) -> dict:
    return service.detail(survey_id, user["id"])


@router.patch("/{survey_id}", response_model=SurveyDetail)
def patch(survey_id: str, data: SurveyPatch, user: CurrentUser, service: Service) -> dict:
    changes = data.model_dump(exclude_unset=True, exclude={"expected_revision"})
    return service.patch(survey_id, user["id"], data.expected_revision, changes)


@router.post("/{survey_id}/questions", status_code=201, response_model=SurveyDetail)
def add_question(
    survey_id: str, data: QuestionInput, expected_revision: Revision,
    user: CurrentUser, service: Service,
) -> dict:
    return service.add_question(survey_id, user["id"], expected_revision, data.model_dump())


@router.put("/{survey_id}/questions/{question_id}", response_model=SurveyDetail)
def replace_question(
    survey_id: str, question_id: str, data: QuestionInput, expected_revision: Revision,
    user: CurrentUser, service: Service,
) -> dict:
    return service.replace_question(
        survey_id, user["id"], question_id, expected_revision, data.model_dump()
    )


@router.delete("/{survey_id}/questions/{question_id}", response_model=SurveyDetail)
def delete_question(
    survey_id: str, question_id: str, expected_revision: Revision,
    user: CurrentUser, service: Service,
) -> dict:
    return service.delete_question(survey_id, user["id"], question_id, expected_revision)


@router.put("/{survey_id}/question-order", response_model=SurveyDetail)
def reorder_questions(
    survey_id: str, data: QuestionOrder, expected_revision: Revision,
    user: CurrentUser, service: Service,
) -> dict:
    return service.reorder_questions(survey_id, user["id"], expected_revision, data.question_ids)


def register_error_handlers(application) -> None:
    # Domain errors are translated once; MongoDB errors retain the existing
    # application's 503 handler and credential errors retain its 401 behavior.
    from fastapi.responses import JSONResponse

    @application.exception_handler(SurveyNotFound)
    async def not_found(request: Request, exc: SurveyNotFound):
        return JSONResponse(status_code=404, content={"detail": "Survey or question not found"})

    @application.exception_handler(RevisionConflict)
    async def conflict(request: Request, exc: RevisionConflict):
        return JSONResponse(
            status_code=409,
            content={"detail": "Draft has changed; reload it and retry with the current revision"},
        )

    @application.exception_handler(InvalidSurveyChange)
    async def invalid_change(request: Request, exc: InvalidSurveyChange):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
