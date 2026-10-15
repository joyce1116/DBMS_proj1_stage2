from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.repository import AccountRepository
from app.auth.models import Credentials, LoginResponse, RegisterRequest, UserPublic
from app.auth.service import AccountService, InvalidCredentials, UsernameTaken


router = APIRouter(prefix="/api/auth", tags=["accounts"])
bearer = HTTPBearer(auto_error=False)


def get_service(request: Request) -> AccountService:
    return AccountService(
        AccountRepository(request.app.state.db),
        request.app.state.settings.session_ttl_seconds,
    )


Service = Annotated[AccountService, Depends(get_service)]


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="Invalid credentials or expired session",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None or len(credentials.credentials) > 256:
        raise unauthorized()
    return credentials.credentials


Token = Annotated[str, Depends(get_token)]


def get_current_user(token: Token, service: Service) -> dict:
    try:
        return service.current_user(token)
    except InvalidCredentials:
        raise unauthorized() from None


CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.post("/register", status_code=201, response_model=UserPublic)
def register(data: RegisterRequest, service: Service) -> dict:
    try:
        return service.register(data.username, data.password)
    except UsernameTaken:
        raise HTTPException(status_code=409, detail="Username is already registered") from None


@router.post("/login", response_model=LoginResponse)
def login(data: Credentials, service: Service, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        return service.login(data.username, data.password)
    except InvalidCredentials:
        raise unauthorized() from None


@router.get("/me", response_model=UserPublic)
def me(user: CurrentUser) -> dict:
    return user


@router.post("/logout", status_code=204)
def logout(token: Token, service: Service) -> Response:
    try:
        service.logout(token)
    except InvalidCredentials:
        raise unauthorized() from None
    return Response(status_code=204)
