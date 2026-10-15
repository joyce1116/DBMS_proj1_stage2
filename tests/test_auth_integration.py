"""HTTP and storage behavior against actual MongoDB, with no database mocks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Barrier

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError

from app.db import initialize_database
from app.main import create_app
from app.security import hash_token, verify_password


pytestmark = pytest.mark.integration
PASSWORD = "correct horse 42"


def register(api, username="Alice_01", password=PASSWORD):
    response = api.post(
        "/api/auth/register", json={"username": username, "password": password}
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(api, username="Alice_01", password=PASSWORD):
    response = api.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token):
    return {"Authorization": "Bearer " + token}


def assert_unauthorized(response):
    assert response.status_code == 401, response.text
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_register_persists_only_hash_and_public_response(api, database):
    public = register(api, username="  Ａｌｉｃｅ＿０１  ")
    assert set(public) == {"id", "username", "created_at"}
    assert public["username"] == "Alice_01"
    document = database.users.find_one({"_id": ObjectId(public["id"])})
    assert document["username_key"] == "alice_01"
    assert document["schema_version"] == 1
    assert document["created_at"].utcoffset() == timedelta(0)
    assert verify_password(PASSWORD, document["password_hash"])
    assert document["password_hash"].startswith("$argon2id$")
    assert "password" not in document
    assert PASSWORD not in str(public)
    assert "password_hash" not in public
    assert database.sessions.count_documents({}) == 0


@pytest.mark.parametrize("duplicate", ["Alice_01", "ALICE_01", " Ａｌｉｃｅ＿０１ "])
def test_registration_rejects_normalized_duplicates(api, database, duplicate):
    original = register(api)
    response = api.post(
        "/api/auth/register", json={"username": duplicate, "password": "other-secret-77"}
    )
    assert response.status_code == 409
    assert database.users.count_documents({}) == 1
    assert database.users.find_one({})["_id"] == ObjectId(original["id"])
    assert login(api)["user"]["id"] == original["id"]


def test_concurrent_registration_has_one_winner(api, database):
    # Two independent HTTP requests must both reach the database uniqueness rule.
    gate = Barrier(2)

    def attempt(username):
        gate.wait(timeout=10)
        return api.post(
            "/api/auth/register", json={"username": username, "password": PASSWORD}
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(attempt, ["Concurrent_User", "CONCURRENT_USER"]))
    assert sorted(statuses) == [201, 409]
    assert database.users.count_documents({"username_key": "concurrent_user"}) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"username": 123, "password": "private-secret-11"},
        {"username": "valid_name", "password": 12345678},
        {"username": "valid_name", "password": "private-secret-11", "role": "admin"},
        {"username": "bad name", "password": "private-secret-11"},
        {"username": "valid_name", "password": " " * 8},
        {"username": "valid_name", "password": "s3cr!"},
        {"username": "valid_name", "password": "x" * 129},
    ],
    ids=["username-type", "password-type", "extra-field", "username-format", "blank", "short", "long"],
)
def test_invalid_registration_is_safe_and_does_not_write(api, database, payload):
    response = api.post("/api/auth/register", json=payload)
    assert response.status_code == 422
    assert all(set(error) <= {"loc", "msg", "type"} for error in response.json()["detail"])
    secret = str(payload["password"])
    if secret.strip():
        assert secret not in response.text
    assert database.users.count_documents({}) == 0
    assert database.sessions.count_documents({}) == 0


def test_login_uses_canonical_username_and_returns_persisted_session(api, database):
    user = register(api, password="  original password  ")
    before = datetime.now(timezone.utc)
    response = api.post(
        "/api/auth/login",
        json={"username": " ＡＬＩＣＥ＿０１ ", "password": "  original password  "},
    )
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"] == user
    session = database.sessions.find_one({"token_hash": hash_token(body["access_token"])})
    assert session["user_id"] == ObjectId(user["id"])
    assert session["schema_version"] == 1
    assert timedelta(seconds=86390) < session["expires_at"] - before < timedelta(seconds=86410)
    assert "token" not in session
    assert "access_token" not in session
    assert body["access_token"] not in str(session)
    assert api.get("/api/auth/me", headers=bearer(body["access_token"])).json() == user
    assert "password_hash" not in response.text


@pytest.mark.parametrize("username,password", [("Alice_01", "x"), ("unknown_user", PASSWORD)])
def test_wrong_password_and_missing_user_are_unauthorized(api, database, username, password):
    register(api)
    response = api.post("/api/auth/login", json={"username": username, "password": password})
    assert_unauthorized(response)
    assert database.sessions.count_documents({}) == 0


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer invalid-token"}]
)
def test_me_requires_valid_bearer_session(api, headers):
    assert_unauthorized(api.get("/api/auth/me", headers=headers))


def test_logout_revokes_only_supplied_session(api, database):
    user = register(api)
    first, second = login(api), login(api)
    assert first["access_token"] != second["access_token"]
    response = api.post("/api/auth/logout", headers=bearer(first["access_token"]))
    assert response.status_code == 204
    assert response.content == b""
    assert_unauthorized(api.get("/api/auth/me", headers=bearer(first["access_token"])))
    assert_unauthorized(api.post("/api/auth/logout", headers=bearer(first["access_token"])))
    assert api.get("/api/auth/me", headers=bearer(second["access_token"])).json() == user
    assert database.sessions.count_documents({}) == 1


def test_bearer_sessions_keep_different_users_separate(api):
    alice = register(api)
    bob = register(api, "Bob_02", "bob-private-33")
    alice_token = login(api)["access_token"]
    bob_token = login(api, "Bob_02", "bob-private-33")["access_token"]
    assert api.get("/api/auth/me", headers=bearer(alice_token)).json() == alice
    assert api.get("/api/auth/me", headers=bearer(bob_token)).json() == bob
    assert alice["id"] != bob["id"]


def test_expired_session_is_rejected_before_ttl_deletion(api, database):
    register(api)
    session = login(api)
    token = session["access_token"]
    digest = hash_token(token)
    # Suspend only this fresh test database's TTL index so the expired row stays
    # present deterministically. A separate test checks the production TTL index.
    database.sessions.drop_index("ttl_sessions_expires_at")
    database.sessions.update_one(
        {"token_hash": digest},
        {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    assert database.sessions.find_one({"token_hash": digest}) is not None
    assert_unauthorized(api.get("/api/auth/me", headers=bearer(token)))


def test_restart_keeps_existing_user_and_session(mongo_settings, database):
    with TestClient(create_app(mongo_settings)) as first:
        user = register(first)
        token = login(first)["access_token"]
    with TestClient(create_app(mongo_settings)) as restarted:
        response = restarted.get("/api/auth/me", headers=bearer(token))
        assert response.status_code == 200
        assert response.json() == user
        assert login(restarted)["user"] == user
    assert database.users.count_documents({}) == 1
    assert database.sessions.count_documents({}) == 2


def test_database_initialization_is_idempotent_with_strict_schema_and_indexes(api, database):
    user = register(api)
    token = login(api)["access_token"]
    initialize_database(database)
    initialize_database(database)
    for name, required in (
        ("users", {"username", "username_key", "password_hash", "created_at", "schema_version"}),
        ("sessions", {"user_id", "token_hash", "created_at", "expires_at", "schema_version"}),
    ):
        options = database[name].options()
        assert options["validationLevel"] == "strict"
        assert options["validationAction"] == "error"
        schema = options["validator"]["$jsonSchema"]
        assert schema["bsonType"] == "object"
        assert required <= set(schema["required"])
        assert schema["additionalProperties"] is False
    user_indexes = database.users.index_information()
    assert any(info["key"] == [("username_key", 1)] and info.get("unique") for info in user_indexes.values())
    session_indexes = database.sessions.index_information()
    assert any(info["key"] == [("token_hash", 1)] and info.get("unique") for info in session_indexes.values())
    assert any(info["key"] == [("expires_at", 1)] and info.get("expireAfterSeconds") == 0 for info in session_indexes.values())
    assert api.get("/api/auth/me", headers=bearer(token)).json() == user
    assert database.users.count_documents({}) == 1
    assert database.sessions.count_documents({}) == 1


@pytest.mark.parametrize(
    "invalid_case",
    ["missing-user-field", "plaintext-password", "wrong-user-type", "missing-session-field"],
)
def test_database_rejects_invalid_documents(api, database, invalid_case):
    register(api)
    login(api)
    collection = database.sessions if invalid_case == "missing-session-field" else database.users
    document = deepcopy(collection.find_one({}))
    document["_id"] = ObjectId()
    if invalid_case == "missing-user-field":
        del document["username"]
    elif invalid_case == "plaintext-password":
        document["password"] = PASSWORD
    elif invalid_case == "wrong-user-type":
        document["schema_version"] = "1"
    else:
        del document["user_id"]
    with pytest.raises(WriteError) as error:
        collection.insert_one(document)
    assert error.value.code == 121  # MongoDB document validation failure.


def test_startup_rejects_schema_conflict_without_modifying_existing_data(mongo_settings, database):
    database.create_collection("users")
    sentinel = {"_id": ObjectId(), "existing_data": "must survive"}
    database.users.insert_one(sentinel)
    with pytest.raises(RuntimeError, match="different schema"):
        with TestClient(create_app(mongo_settings)):
            pytest.fail("Startup must require an explicit schema migration")
    assert database.users.find_one({"_id": sentinel["_id"]}) == sentinel
    assert database.users.options().get("validator") is None


def test_health_reports_real_database_connection_failure(api, mongo_settings):
    assert api.get("/health/live").json() == {"status": "ok"}
    ready = api.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "connected"}
    original_db = api.app.state.db
    unreachable = MongoClient(
        "mongodb://127.0.0.1:1", serverSelectionTimeoutMS=100, connectTimeoutMS=100
    )
    try:
        api.app.state.db = unreachable[mongo_settings.mongo_db]
        response = api.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"detail": "Database unavailable"}
        assert api.get("/health/live").status_code == 200
    finally:
        api.app.state.db = original_db
        unreachable.close()
