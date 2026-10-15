"""Opt-in real Edge/Chromium tests against a disposable MongoDB and live HTTP."""

import os
import socket
from threading import Thread
import time

import pytest
import uvicorn

from app.main import create_app


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        channel = os.environ.get("PLAYWRIGHT_CHANNEL", "msedge")
        instance = driver.chromium.launch(headless=True, **({"channel":channel} if channel != "chromium" else {}))
        yield instance
        instance.close()


@pytest.fixture
def live_url(mongo_settings):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(mongo_settings), log_level="error", access_log=False))
    thread = Thread(target=server.run, kwargs={"sockets":[listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    try:
        assert server.started, "The test HTTP server did not start"
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "The test HTTP server did not stop"


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={"width":1440,"height":1000}, locale="zh-CN", timezone_id="Asia/Shanghai")
    errors = []

    def observe(tab):
        tab.on("pageerror", lambda error: errors.append(str(error)))
        tab.on("console", lambda message: errors.append(message.text) if message.type == "error" and "Content Security Policy" in message.text else None)
        tab.on("dialog", lambda dialog: dialog.accept())

    context.on("page",observe)
    tab = context.new_page()
    tab.set_default_timeout(10000)
    try:
        yield tab
    finally:
        context.close()
        assert not errors, "Browser errors: " + "\n".join(errors)
