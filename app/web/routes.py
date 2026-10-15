"""Serve a public HTML shell while preserving authenticated JSON share APIs."""

from pathlib import Path
import re

from fastapi import FastAPI
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles


STATIC = Path(__file__).parent / "static"
SHARE_PATH = re.compile(r"^/survey/[^/]+$")
WEB_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
                               "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                               "base-uri 'none'; frame-ancestors 'none'",
}


def prefers_html(accept: str) -> bool:
    qualities = {}
    for part in accept.lower().split(","):
        media, *parameters = part.strip().split(";")
        quality = 1.0
        for parameter in parameters:
            if parameter.strip().startswith("q="):
                try:
                    quality = float(parameter.strip()[2:])
                except ValueError:
                    quality = 0.0
        qualities[media] = quality
    return qualities.get("text/html", 0) > 0 and (
        qualities["text/html"] >= qualities.get("application/json", 0)
    )


class ShareHTMLMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not SHARE_PATH.fullmatch(scope["path"]):
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        if scope["method"] in ("GET", "HEAD") and prefers_html(headers.get(b"accept", b"").decode("latin1")):
            # No business data is embedded. JavaScript subsequently calls the
            # unchanged JSON endpoint with Bearer authentication.
            response = FileResponse(STATIC / "index.html", headers={**WEB_HEADERS, "Vary": "Accept"})
            return await response(scope, receive, send)

        async def vary(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message["headers"], (b"vary", b"Accept")]
            await send(message)

        await self.app(scope, receive, vary)


def install_web(application: FastAPI) -> None:
    application.add_middleware(ShareHTMLMiddleware)
    application.mount("/assets", StaticFiles(directory=STATIC, check_dir=True), name="web-assets")

    @application.get("/", include_in_schema=False)
    def home():
        return FileResponse(STATIC / "index.html", headers=WEB_HEADERS)
