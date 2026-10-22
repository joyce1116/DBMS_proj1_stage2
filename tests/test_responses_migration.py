"""New schema upgrades keep old documents usable and backups untouched."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure, WriteError
import pytest

from app.database.connection import initialize_database
from app.database.migrations import migrate_surveys_v2, migrate_surveys_v3, migrate_surveys_v4, V3_PLAN
from app.main import create_app
from app.surveys.schema import LEGACY_SURVEY_SCHEMA, SURVEY_SCHEMA_V2, SURVEY_SCHEMA_V3


pytestmark = pytest.mark.integration


def original(status="draft", version=2, owner=None):
    now = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    doc = {
        "_id": ObjectId(), "owner_id": owner or ObjectId(), "title": "旧问卷",
        "description": "保留说明", "allow_anonymous": True, "allow_repeat": False,
        "deadline": None, "status": status, "revision": 7, "schema_version": version,
        "created_at": now, "updated_at": now,
        "questions": [{"id": "q_" + "a" * 32, "title": "原题目", "type": "text",
                       "required": True, "min_length": 1, "max_length": 20}],
    }
    if version == 2:
        doc.update(share_id=None if status == "draft" else str(doc["_id"]) + "a" * 8,
                   published_at=None if status == "draft" else now,
                   closed_at=now if status == "closed" else None)
    return doc


def setup_v2(database):
    database.create_collection("surveys", validator={"$jsonSchema": SURVEY_SCHEMA_V2})
    docs = [original(state) for state in ("draft", "published", "closed")]
    database.surveys.insert_many(deepcopy(docs))
    return docs


def test_v3_preflight_migration_preserves_all_lifecycle_states_and_reruns(database):
    assert migrate_surveys_v3(database, dry_run=True)["state"] == "absent"
    assert database.list_collection_names() == []
    docs = setup_v2(database)
    names = database.list_collection_names()
    assert migrate_surveys_v3(database, dry_run=True)["v2_count"] == 3
    assert database.list_collection_names() == names
    assert list(database.surveys.find()) == docs
    result = migrate_surveys_v3(database)
    assert result["converted_count"] == 3 and result["state"] == "completed"
    assert database.surveys.options()["validator"] == {"$jsonSchema": SURVEY_SCHEMA_V3}
    for doc in docs:
        assert database[result["backup_collection"]].find_one({"_id": doc["_id"]}) == doc
        assert database.surveys.find_one({"_id": doc["_id"]}) == V3_PLAN.upgraded(doc)
    database.surveys.update_one({"_id": docs[0]["_id"]}, {"$set": {"title": "迁移后编辑"}})
    assert migrate_surveys_v3(database)["state"] == "already_v3"
    assert database[result["backup_collection"]].find_one({"_id": docs[0]["_id"]}) == docs[0]


def test_v3_resumes_partial_conversion_with_original_backup(database):
    docs = setup_v2(database)
    real_update = type(database.surveys).update_many

    def interrupt(collection, *args, **kwargs):
        if collection.name == "surveys":
            collection.update_one({"_id": docs[1]["_id"]}, args[1])
            raise OperationFailure("simulated interruption")
        return real_update(collection, *args, **kwargs)

    with patch("pymongo.synchronous.collection.Collection.update_many", interrupt):
        with pytest.raises(OperationFailure):
            migrate_surveys_v3(database)
    record = database.schema_migrations.find_one({"_id": "surveys_v2_to_v3"})
    assert record["state"] == "in_progress"
    assert database.surveys.count_documents({"schema_version": 3}) == 1
    assert migrate_surveys_v3(database, dry_run=True)["state"] == "dry_run"
    assert migrate_surveys_v3(database)["state"] == "completed"
    for doc in docs:
        assert database[record["backup_collection"]].find_one({"_id": doc["_id"]}) == doc


@pytest.mark.parametrize("problem", ["unknown_schema", "invalid_doc", "bad_audit"])
def test_v3_rejects_unrecognized_or_damaged_input_before_writes(database, problem):
    setup_v2(database)
    if problem == "unknown_schema":
        database.command({"collMod": "surveys", "validator": {"title": {"$exists": True}}})
    elif problem == "invalid_doc":
        database.surveys.update_one({}, {"$set": {"revision": 0}}, bypass_document_validation=True)
    else:
        database.schema_migrations.insert_one({"_id": "surveys_v2_to_v3", "state": "unknown"})
    docs = list(database.surveys.find())
    names = database.list_collection_names()
    options = database.surveys.options()
    with pytest.raises(RuntimeError):
        migrate_surveys_v3(database)
    assert list(database.surveys.find()) == docs
    assert database.list_collection_names() == names
    assert database.surveys.options() == options


def test_v1_to_v3_keeps_existing_user_session_question_and_allows_submit(database, mongo_settings):
    # Register normally, then install a legacy survey collection while the app
    # is stopped. The test never drops production or development collections.
    with TestClient(create_app(mongo_settings)) as client:
        creds = {"username": "Legacy_User", "password": "Legacy_password_123"}
        user = client.post("/api/auth/register", json=creds).json()
        login = client.post("/api/auth/login", json=creds).json()
    assert database.surveys.count_documents({}) == 0
    # An empty test-owned collection is the sole object replaced to create the
    # historical schema; no original document is removed.
    database.command({"collMod": "surveys", "validator": {"$jsonSchema": LEGACY_SURVEY_SCHEMA}})
    doc = original(version=1, owner=ObjectId(user["id"]))
    database.surveys.insert_one(deepcopy(doc))
    users, sessions = list(database.users.find()), list(database.sessions.find())
    first = migrate_surveys_v2(database)
    second = migrate_surveys_v3(database)
    assert list(database.users.find()) == users and list(database.sessions.find()) == sessions
    assert database[first["backup_collection"]].find_one() == doc
    assert database[second["backup_collection"]].find_one()["questions"] == doc["questions"]
    # The historical migration still targets v3; only the current app needs v4.
    assert database.surveys.options()["validator"] == {"$jsonSchema": SURVEY_SCHEMA_V3}
    migrate_surveys_v4(database)
    with TestClient(create_app(mongo_settings)) as client:
        headers = {"Authorization": "Bearer " + login["access_token"]}
        base = "/api/surveys/" + str(doc["_id"])
        detail = client.get(base, headers=headers)
        assert detail.status_code == 200
        assert detail.json()["questions"] == doc["questions"]
        opened = client.post(base + "/publish", headers=headers, json={"expected_revision": 7}).json()
        response = client.post(opened["share_url"] + "/responses", headers=headers, json={
            "survey_revision": opened["revision"], "request_id": "legacy_request_123",
            "answers": {doc["questions"][0]["id"]: "保留原题并作答"},
        })
        assert response.status_code == 201, response.text
        assert client.get(base + "/statistics", headers=headers).json()["total_responses"] == 1


def test_new_submission_validator_and_indexes_survive_restart(api, database, mongo_settings):
    indexes = database.submissions.index_information()
    assert indexes["uq_submissions_request"]["unique"]
    assert indexes["uq_submissions_once"]["unique"]
    assert indexes["uq_submissions_once"]["partialFilterExpression"] == {"single_response": True}
    with pytest.raises(WriteError) as error:
        database.submissions.insert_one({"survey_id": ObjectId(), "anonymous": True})
    assert error.value.code == 121
    before = database.submissions.options()
    initialize_database(database)
    with TestClient(create_app(mongo_settings)) as restarted:
        assert restarted.get("/health/ready").status_code == 200
    assert database.submissions.options() == before
    assert database.submissions.index_information() == indexes
