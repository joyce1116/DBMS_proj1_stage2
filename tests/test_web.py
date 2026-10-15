"""Offline HTML/assets and content-negotiation contracts."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.web.routes import prefers_html


@pytest.mark.parametrize("accept,html", [
    ("", False), ("*/*", False), ("application/json", False), ("text/html", True),
    ("text/html,application/xhtml+xml,*/*;q=0.8", True), ("text/html;q=0", False),
    ("text/html;q=0.5,application/json;q=1", False), ("text/html;q=bad", False),
])
def test_accept_negotiation_preserves_json_default(accept, html):
    assert prefers_html(accept) is html


def test_public_shell_contains_no_business_data_or_remote_assets():
    # Without a lifespan context, no MongoDB connection is opened for these
    # public shell checks. Authentication still protects the subsequent API.
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'lang="zh-CN"' in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "https://" not in response.text
    assert "password_hash" not in response.text and "access_token" not in response.text
    for name in ("styles.css", "app.js", "api.js", "editor.js", "fill.js", "results.js", "icon.svg"):
        asset = client.get("/assets/" + name)
        assert asset.status_code == 200, name
    assert client.get("/assets/does-not-exist.js").status_code == 404
    assert client.get("/assets/%2e%2e/main.py").status_code == 404


def test_browser_share_gets_only_shell_before_login():
    client = TestClient(create_app())
    response = client.get("/survey/" + "x" * 32, headers={"Accept": "text/html"})
    assert response.status_code == 200
    assert 'id="main"' in response.text
    assert response.headers["vary"] == "Accept"
    head = client.head("/survey/" + "x" * 32, headers={"Accept":"text/html"})
    assert head.status_code == 200 and not head.content


@pytest.mark.integration
def test_json_share_remains_authenticated_and_unchanged(api):
    response = api.get("/survey/" + "x" * 32, headers={"Accept":"application/json"})
    assert response.status_code == 401
    assert response.headers["vary"] == "Accept"
    assert response.headers["content-type"].startswith("application/json")
