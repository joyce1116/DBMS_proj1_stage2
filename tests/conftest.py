"""Integration fixtures own exactly one fresh, disposable database per test."""

import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from app.core.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def mongo_server():
    uri = os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27018")
    client = MongoClient(
        uri,
        tz_aware=True,
        serverSelectionTimeoutMS=3000,
        connectTimeoutMS=3000,
        socketTimeoutMS=3000,
    )
    try:
        client.admin.command("ping")
    except PyMongoError as exc:
        client.close()
        pytest.fail(
            "Integration tests require a real MongoDB server. Start MongoDB or "
            "set TEST_MONGODB_URI. Connection failed: " + type(exc).__name__,
            pytrace=False,
        )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def mongo_settings(mongo_server):
    # Verify nonexistence before allowing teardown to delete this database.
    name = "survey_test_" + uuid4().hex
    assert name not in mongo_server.list_database_names()
    settings = Settings(
        mongo_uri=os.environ.get("TEST_MONGODB_URI", "mongodb://127.0.0.1:27018"),
        mongo_db=name,
        mongo_timeout_ms=3000,
        session_ttl_seconds=86400,
        _env_file=None,
    )
    try:
        yield settings
    finally:
        assert name.startswith("survey_test_") and len(name) == 44
        mongo_server.drop_database(name)


@pytest.fixture
def database(mongo_server, mongo_settings):
    return mongo_server[mongo_settings.mongo_db]


@pytest.fixture
def api(mongo_settings):
    with TestClient(create_app(mongo_settings)) as client:
        yield client
