"""Authenticated bank APIs; grants provide read access without management."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.auth.routes import CurrentUser
from app.banks.models import (
    BankCreate, BankEntry, BankEntryCreate, BankEntryList, BankEntryReplace,
    BankList, BankPatch, BankSummary,
)
from app.banks.repository import BankRepository
from app.banks.service import BankConflict, BankNotFound, BankService, InvalidBankChange


router = APIRouter(prefix="/api/question-banks", tags=["question banks"])


def get_bank_service(request: Request) -> BankService:
    return BankService(BankRepository(request.app.state.db))


Service = Annotated[BankService, Depends(get_bank_service)]
Offset = Annotated[int, Query(ge=0, le=2147483647)]
Limit = Annotated[int, Query(ge=1, le=100)]
ExpectedRevision = Annotated[int, Query(ge=1, le=2147483647)]


@router.post("", status_code=201, response_model=BankSummary)
def create(data: BankCreate, user: CurrentUser, service: Service) -> dict:
    return service.create(user["id"], data.model_dump())


@router.get("", response_model=BankList)
def list_banks(
    user: CurrentUser, service: Service, view: Literal["owned", "shared"] = "owned",
    offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.list_banks(user["id"], view, offset, limit)


@router.get("/{bank_id}", response_model=BankSummary)
def detail(bank_id: str, user: CurrentUser, service: Service) -> dict:
    return service.detail(bank_id, user["id"])


@router.patch("/{bank_id}", response_model=BankSummary)
def patch(bank_id: str, data: BankPatch, user: CurrentUser, service: Service) -> dict:
    return service.patch(bank_id, user["id"], data.model_dump(exclude_unset=True))


@router.delete("/{bank_id}", status_code=204)
def archive(bank_id: str, expected_revision: ExpectedRevision, user: CurrentUser, service: Service):
    service.archive(bank_id, user["id"], expected_revision)
    return Response(status_code=204)


@router.get("/{bank_id}/entries", response_model=BankEntryList)
def list_entries(
    bank_id: str, user: CurrentUser, service: Service, offset: Offset = 0, limit: Limit = 20,
) -> dict:
    return service.list_entries(bank_id, user["id"], offset, limit)


@router.post("/{bank_id}/entries", status_code=201, response_model=BankEntry)
def add_entry(bank_id: str, data: BankEntryCreate, user: CurrentUser, service: Service) -> dict:
    return service.add_entry(bank_id, user["id"], data.version_id)


@router.put("/{bank_id}/entries/{family_id}", response_model=BankEntry)
def replace_entry(
    bank_id: str, family_id: str, data: BankEntryReplace, user: CurrentUser, service: Service,
) -> dict:
    return service.replace_entry(
        bank_id, family_id, user["id"], data.version_id, data.expected_version_id,
    )


@router.delete("/{bank_id}/entries/{family_id}", status_code=204)
def remove_entry(
    bank_id: str, family_id: str, expected_version_id: str, user: CurrentUser, service: Service,
):
    service.remove_entry(bank_id, family_id, user["id"], expected_version_id)
    return Response(status_code=204)


def register_error_handlers(application) -> None:
    @application.middleware("http")
    async def private_bank_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == "/api/question-banks" or request.url.path.startswith("/api/question-banks/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(BankNotFound)
    async def not_found(request: Request, exc: BankNotFound):
        return JSONResponse(status_code=404, content={"detail": "Question bank, entry or version not found"})

    @application.exception_handler(BankConflict)
    async def conflict(request: Request, exc: BankConflict):
        return JSONResponse(
            status_code=409,
            content={"detail": "Question bank metadata or entry changed, or this family is already in the bank"},
        )

    @application.exception_handler(InvalidBankChange)
    async def invalid_change(request: Request, exc: InvalidBankChange):
        return JSONResponse(status_code=422, content={"detail": str(exc)})
