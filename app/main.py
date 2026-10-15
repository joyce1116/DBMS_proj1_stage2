from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.database.connection import initialize_database, make_client
from app.auth.routes import router
from app.surveys.routes import router as survey_router, shared_router, register_error_handlers
from app.responses.routes import router as response_router, register_error_handlers as response_errors


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        client = make_client(config)
        try:
            await run_in_threadpool(client.admin.command, "ping")
            database = client[config.mongo_db]
            await run_in_threadpool(initialize_database, database)
            application.state.mongo_client = client
            application.state.db = database
            yield
        finally:
            client.close()

    application = FastAPI(
        title="Questionnaire system",
        version="0.5.0",
        lifespan=lifespan,
    )
    application.state.settings = config
    application.include_router(router)
    application.include_router(survey_router)
    application.include_router(shared_router)
    application.include_router(response_router)
    register_error_handlers(application)
    response_errors(application)

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic errors include the original input by default, potentially a
        # password or the whole request body. Return only location/type/message.
        errors = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @application.exception_handler(PyMongoError)
    async def database_error(request: Request, exc: PyMongoError):
        return JSONResponse(status_code=503, content={"detail": "Database unavailable"})

    @application.get("/health/live", tags=["health"])
    def live() -> dict:
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"])
    def ready(request: Request) -> dict:
        request.app.state.db.command("ping")
        return {"status": "ok", "database": "connected"}

    return application


app = create_app()
