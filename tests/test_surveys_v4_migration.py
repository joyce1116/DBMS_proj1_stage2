"""Real-MongoDB evidence for preserving phase-one data through the v4 upgrade."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from bson import BSON, ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure, WriteError
import pytest

from app.database.connection import initialize_database
from app.database.migrations import (
    V2_PLAN, V3_PLAN, V4_PLAN,
    migrate_surveys_v2, migrate_surveys_v3, migrate_surveys_v4,
)
from app.main import create_app
from app.responses.schema import SUBMISSION_SCHEMA
from app.surveys.schema import LEGACY_SURVEY_SCHEMA, SURVEY_SCHEMA_V3, SURVEY_SCHEMA


pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
QIDS = ["q_" + str(index) * 32 for index in range(1, 5)]
OPTIONS = ["opt_" + letter * 32 for letter in "abcd"]


def historical_survey(status="draft", owner=None):
    identifier = ObjectId()
    return {
        "_id": identifier, "owner_id": owner or ObjectId(), "title": "第一阶段问卷",
        "description": "保留原来的四种题型、选项、跳转及时间",
        "allow_anonymous": True, "allow_repeat": True,
        "deadline": None, "status": status, "revision": 7, "schema_version": 3,
        "created_at": NOW, "updated_at": NOW,
        "questions": [
            {"id": QIDS[0], "title": "选择分支", "type": "single_choice", "required": True,
             "options": [{"id": OPTIONS[0], "label": "继续"}, {"id": OPTIONS[1], "label": "跳转"}]},
            {"id": QIDS[1], "title": "多选", "type": "multiple_choice", "required": False,
             "options": [{"id": OPTIONS[2], "label": "甲"}, {"id": OPTIONS[3], "label": "乙"}],
             "min_selections": 0, "max_selections": 2},
            {"id": QIDS[2], "title": "年龄", "type": "number", "required": True,
             "min_value": 0, "max_value": 120, "integer_only": True},
            {"id": QIDS[3], "title": "建议", "type": "text", "required": False,
             "min_length": 0, "max_length": 200},
        ],
        "share_id": None if status == "draft" else str(identifier) + "a" * 8,
        "published_at": None if status == "draft" else NOW,
        "closed_at": NOW if status == "closed" else None,
        "jump_rules": [
            {"kind": "choice", "source_id": QIDS[0], "target_id": QIDS[2],
             "operator": "eq", "option_ids": [OPTIONS[1]]},
            {"kind": "number", "source_id": QIDS[2], "target_id": "end",
             "operator": "lt", "value": 18},
        ],
        "submission_serial": 0 if status == "draft" else 1,
        "last_submission_at": None if status == "draft" else NOW,
    }


def historical_submission(survey):
    return {
        "_id": ObjectId(), "survey_id": survey["_id"], "respondent_id": ObjectId(),
        "request_id": "historical_request_123", "fingerprint": "a" * 64,
        "survey_revision": survey["revision"], "anonymous": True, "single_response": False,
        "submission_serial": 1, "submitted_at": NOW, "path": [QIDS[0], QIDS[2]],
        "answers": [{"question_id": QIDS[0], "value": OPTIONS[1]},
                    {"question_id": QIDS[2], "value": 17}],
        "schema_version": 1,
    }


def setup_v3(database, owner=None):
    database.create_collection("surveys", validator={"$jsonSchema": SURVEY_SCHEMA_V3})
    database.create_collection("submissions", validator={"$jsonSchema": SUBMISSION_SCHEMA})
    docs = [historical_survey(state, owner) for state in ("draft", "published", "closed")]
    database.surveys.insert_many(deepcopy(docs))
    database.submissions.insert_many([historical_submission(doc) for doc in docs[1:]])
    return docs


def snapshot(database):
    """Raw BSON comparisons include field order and numeric storage types."""
    return {
        name: (database[name].options(), list(database[name].find_raw_batches().sort("_id", 1)))
        for name in database.list_collection_names()
    }


def test_v4_dry_run_is_readonly_and_upgrade_retains_original_data_and_audit(database):
    assert migrate_surveys_v4(database, dry_run=True)["state"] == "absent"
    assert database.list_collection_names() == []
    docs = setup_v3(database)
    before = snapshot(database)
    preview = migrate_surveys_v4(database, dry_run=True)
    assert preview["state"] == "dry_run" and preview["v3_count"] == 3
    assert snapshot(database) == before
    result = migrate_surveys_v4(database)
    assert result["state"] == "completed" and result["converted_count"] == 3
    assert database.surveys.options()["validator"] == {"$jsonSchema": SURVEY_SCHEMA}
    assert snapshot(database)["submissions"] == before["submissions"]
    for original in docs:
        upgraded = database.surveys.find_one({"_id": original["_id"]})
        assert upgraded == V4_PLAN.upgraded(original)
        # The original fields keep their exact BSON values; only schema version changes.
        old_fields = {key: upgraded[key] for key in original}
        old_fields["schema_version"] = 3
        assert BSON.encode(old_fields) == BSON.encode(original)
        assert upgraded["question_sources"] == []
        saved = database[result["backup_collection"]].find_one({"_id": original["_id"]})
        # $setOnInsert may reorder top-level keys, while every stored value and
        # nested question/rule BSON shape must still match the original.
        assert saved == original
        assert BSON.encode({key: saved[key] for key in original}) == BSON.encode(original)
    audit = database.schema_migrations.find_one({"_id": V4_PLAN.migration_id})
    assert audit["state"] == "completed" and audit["completed_at"] >= audit["started_at"]
    assert audit["counts"] == {
        "original_v3": 3, "original_v4": 0, "total": 3, "backup": 3, "converted": 3,
    }
    database.surveys.update_one({"_id": docs[0]["_id"]}, {"$set": {"title": "迁移后正常编辑"}})
    after_edit = snapshot(database)
    assert migrate_surveys_v4(database)["state"] == "already_v4"
    assert snapshot(database) == after_edit


def test_v4_resumes_interrupted_conversion_without_replacing_original_snapshots(database):
    docs = setup_v3(database)
    real_update = type(database.surveys).update_many

    def interrupt(collection, *args, **kwargs):
        if collection.name == "surveys":
            collection.update_one({"_id": docs[1]["_id"]}, args[1])
            raise OperationFailure("simulated partial v4 conversion")
        return real_update(collection, *args, **kwargs)

    with patch("pymongo.synchronous.collection.Collection.update_many", interrupt):
        with pytest.raises(OperationFailure, match="partial"):
            migrate_surveys_v4(database)
    audit = database.schema_migrations.find_one({"_id": V4_PLAN.migration_id})
    assert audit["state"] == "in_progress"
    assert database.surveys.count_documents({"schema_version": 4}) == 1
    assert database.surveys.options()["validator"] == V4_PLAN.transition
    interrupted = snapshot(database)
    assert migrate_surveys_v4(database, dry_run=True)["state"] == "dry_run"
    assert snapshot(database) == interrupted
    assert migrate_surveys_v4(database)["state"] == "completed"
    assert set(database.list_collection_names()) == set(interrupted)
    for original in docs:
        assert database[audit["backup_collection"]].find_one({"_id": original["_id"]}) == original
        assert database.surveys.find_one({"_id": original["_id"]}) == V4_PLAN.upgraded(original)
    assert snapshot(database)["submissions"] == interrupted["submissions"]


@pytest.mark.parametrize("problem", [
    "unknown_validator", "unknown_version", "invalid_document", "bad_audit", "unowned_transition",
])
def test_v4_refuses_unknown_or_damaged_input_before_writes(database, problem):
    setup_v3(database)
    if problem == "unknown_validator":
        database.command({"collMod": "surveys", "validator": {"title": {"$exists": True}}})
    elif problem in ("unknown_version", "invalid_document"):
        value = {"schema_version": 99} if problem == "unknown_version" else {"revision": 0}
        database.surveys.update_one({}, {"$set": value}, bypass_document_validation=True)
    elif problem == "bad_audit":
        database.schema_migrations.insert_one({"_id": V4_PLAN.migration_id, "state": "unknown"})
    else:
        database.command({"collMod": "surveys", "validator": V4_PLAN.transition})
    before = snapshot(database)
    with pytest.raises(RuntimeError):
        migrate_surveys_v4(database)
    assert snapshot(database) == before


def test_v4_completed_migration_refuses_damaged_original_backup(database):
    docs = setup_v3(database)
    result = migrate_surveys_v4(database)
    database[result["backup_collection"]].update_one(
        {"_id": docs[0]["_id"]}, {"$set": {"schema_version": 99}},
    )
    before = snapshot(database)
    with pytest.raises(RuntimeError, match="damaged original backup"):
        migrate_surveys_v4(database)
    assert snapshot(database) == before


def test_new_v4_collection_needs_no_migration_metadata(database):
    initialize_database(database)
    before = snapshot(database)
    assert migrate_surveys_v4(database)["state"] == "already_v4"
    assert snapshot(database) == before


@pytest.mark.parametrize("invalid", [
    "missing_sources", "string_version_id", "extra_source_field", "extra_mapping_field",
    "invalid_local_id", "invalid_source_id", "boolean_version", "zero_version",
])
def test_v4_storage_validator_rejects_invalid_provenance(database, invalid):
    initialize_database(database)
    doc = V4_PLAN.upgraded(historical_survey())
    doc["question_sources"] = [{
        "question_id": QIDS[0], "family_id": ObjectId(), "version_id": ObjectId(), "version": 1,
        "option_map": [{"local_id": OPTIONS[0], "source_id": OPTIONS[1]}],
    }]
    source = doc["question_sources"][0]
    if invalid == "missing_sources":
        doc.pop("question_sources")
    elif invalid == "string_version_id":
        source["version_id"] = str(source["version_id"])
    elif invalid == "extra_source_field":
        source["mutable"] = True
    elif invalid == "extra_mapping_field":
        source["option_map"][0]["label"] = "forged"
    elif invalid in ("invalid_local_id", "invalid_source_id"):
        source["option_map"][0]["local_id" if invalid == "invalid_local_id" else "source_id"] = "invalid"
    else:
        source["version"] = True if invalid == "boolean_version" else 0
    with pytest.raises(WriteError) as error:
        database.surveys.insert_one(doc)
    assert error.value.code == 121
    assert database.surveys.count_documents({}) == 0


def test_v3_existing_share_rules_submissions_and_statistics_work_after_upgrade(database, mongo_settings):
    # Prepare auth using normal code, then set the empty test-owned collection to v3.
    with TestClient(create_app(mongo_settings)) as client:
        credentials = {"username": "V4_Legacy", "password": "legacy_password_123"}
        user = client.post("/api/auth/register", json=credentials).json()
        token = client.post("/api/auth/login", json=credentials).json()["access_token"]
    database.command({"collMod": "surveys", "validator": {"$jsonSchema": SURVEY_SCHEMA_V3}})
    doc = historical_survey("published", ObjectId(user["id"]))
    database.surveys.insert_one(deepcopy(doc))
    database.submissions.insert_one(historical_submission(doc))
    previous_submissions = snapshot(database)["submissions"]
    previous_auth = {name: snapshot(database)[name] for name in ("users", "sessions")}
    migrated = migrate_surveys_v4(database)
    assert snapshot(database)["submissions"] == previous_submissions
    assert {name: snapshot(database)[name] for name in previous_auth} == previous_auth
    headers = {"Authorization": "Bearer " + token}
    base = "/api/surveys/" + str(doc["_id"])
    share = "/survey/" + doc["share_id"]
    with TestClient(create_app(mongo_settings)) as client:
        opened = client.get(share, headers=headers)
        assert opened.status_code == 200, opened.text
        assert opened.json()["questions"] == doc["questions"]
        assert client.get(base + "/statistics", headers=headers).json()["total_responses"] == 1
        data = {"survey_revision": doc["revision"], "request_id": "v4_legacy_share_123",
                "answers": {QIDS[0]: OPTIONS[1], QIDS[2]: 17}}
        response = client.post(share + "/responses", headers=headers, json=data)
        assert response.status_code == 201, response.text
        assert client.get(base + "/statistics", headers=headers).json()["total_responses"] == 2
        assert client.get(base + "/responses", headers=headers).json()["total"] == 2
        assert database.submissions.find_one({"request_id": data["request_id"]})["path"] == [QIDS[0], QIDS[2]]
    assert database[migrated["backup_collection"]].find_one() == doc


def test_v1_v2_v3_v4_chain_keeps_historical_targets_and_allows_old_draft_to_publish(database, mongo_settings):
    with TestClient(create_app(mongo_settings)) as client:
        credentials = {"username": "V4_Oldest", "password": "legacy_password_123"}
        user = client.post("/api/auth/register", json=credentials).json()
        token = client.post("/api/auth/login", json=credentials).json()["access_token"]
    database.command({"collMod": "surveys", "validator": {"$jsonSchema": LEGACY_SURVEY_SCHEMA}})
    original = historical_survey(owner=ObjectId(user["id"]))
    for key in ("share_id", "published_at", "closed_at", "jump_rules", "submission_serial", "last_submission_at"):
        original.pop(key)
    original["schema_version"] = 1
    database.surveys.insert_one(deepcopy(original))
    previous_auth = {name: snapshot(database)[name] for name in ("users", "sessions")}
    intermediate = deepcopy(original)
    for migrate, plan in ((migrate_surveys_v2, V2_PLAN), (migrate_surveys_v3, V3_PLAN),
                          (migrate_surveys_v4, V4_PLAN)):
        result = migrate(database)
        assert database[result["backup_collection"]].find_one() == intermediate
        intermediate = plan.upgraded(intermediate)
        assert database.surveys.find_one() == intermediate
        assert database.surveys.options()["validator"] == plan.target_validator
    assert {name: snapshot(database)[name] for name in previous_auth} == previous_auth
    headers = {"Authorization": "Bearer " + token}
    base = "/api/surveys/" + str(original["_id"])
    with TestClient(create_app(mongo_settings)) as client:
        detail = client.get(base, headers=headers)
        assert detail.status_code == 200 and detail.json()["questions"] == original["questions"]
        published = client.post(base + "/publish", headers=headers, json={"expected_revision": 7})
        assert published.status_code == 200, published.text
        survey = published.json()
        response = client.post(survey["share_url"] + "/responses", headers=headers, json={
            "survey_revision": survey["revision"], "request_id": "v4_oldest_draft_123",
            "answers": {QIDS[0]: OPTIONS[0], QIDS[2]: 23},
        })
        assert response.status_code == 201, response.text
        assert client.get(base + "/statistics", headers=headers).json()["total_responses"] == 1
