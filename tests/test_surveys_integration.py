"""Draft APIs against real MongoDB; auth, storage and edits are not mocked."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from threading import Barrier

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest
from pymongo.errors import WriteError

from app.database.connection import initialize_database
from app.main import create_app


pytestmark = pytest.mark.integration
PASSWORD = "correct horse 42"


def actor(api, username="Survey_Alice"):
    created = api.post("/api/auth/register", json={"username": username, "password": PASSWORD})
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def owner(api):
    return actor(api)


def create_survey(api, headers, **fields):
    response = api.post("/api/surveys", headers=headers, json={"title": "First survey", **fields})
    assert response.status_code == 201, response.text
    return response.json()


def add_question(api, headers, survey, payload):
    response = api.post(
        f"/api/surveys/{survey['id']}/questions",
        headers=headers, params={"expected_revision": survey["revision"]}, json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_persists_owner_and_utc_dates_with_public_defaults(api, database, owner):
    user, headers = owner
    deadline = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=7)
    survey = create_survey(api, headers, title="  Feedback  ", deadline=deadline.isoformat())
    assert survey["title"] == "Feedback"
    assert survey["description"] == ""
    assert survey["owner_id"] == user["id"]
    assert survey["status"] == "draft"
    assert survey["revision"] == 1
    assert survey["questions"] == []
    assert survey["question_count"] == 0
    assert survey["allow_anonymous"] is False
    assert survey["allow_repeat"] is False
    assert "schema_version" not in survey
    document = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    assert document["owner_id"] == ObjectId(user["id"])
    assert document["schema_version"] == 3
    assert document["created_at"] == document["updated_at"]
    assert document["created_at"].utcoffset() == timedelta(0)
    assert document["deadline"].utcoffset() == timedelta(0)
    assert abs((document["deadline"] - deadline).total_seconds()) < 0.001
    assert api.get(f"/api/surveys/{survey['id']}", headers=headers).json() == survey


def test_list_is_owner_scoped_paginated_and_stably_sorted(api, database, owner):
    _, headers = owner
    surveys = [create_survey(api, headers, title=f"Survey {index}") for index in range(3)]
    _, bob = actor(api, "Survey_Bob")
    foreign = create_survey(api, bob, title="Private Bob survey")
    # Equal timestamps make the ObjectId tie breaker deterministic and necessary.
    same_date = datetime(2026, 1, 1, tzinfo=timezone.utc)
    database.surveys.update_many({}, {"$set": {"created_at": same_date}})
    expected = sorted((survey["id"] for survey in surveys), reverse=True)
    listed = api.get("/api/surveys", headers=headers).json()
    assert listed["total"] == 3
    assert listed["offset"] == 0 and listed["limit"] == 20
    assert [item["id"] for item in listed["items"]] == expected
    assert all("questions" not in item and item["question_count"] == 0 for item in listed["items"])
    assert foreign["id"] not in str(listed)
    page = api.get("/api/surveys", headers=headers, params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 3 and page["offset"] == 1 and page["limit"] == 1
    assert [item["id"] for item in page["items"]] == expected[1:2]
    assert api.get("/api/surveys?offset=99", headers=headers).json()["items"] == []
    for query in (
        "offset=-1", "offset=2147483648", "offset=9223372036854775808", "limit=0", "limit=101"
    ):
        assert api.get("/api/surveys?" + query, headers=headers).status_code == 422


def test_patch_metadata_advances_revision_and_clears_deadline(api, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    response = api.patch(f"/api/surveys/{survey['id']}", headers=headers, json={
        "expected_revision": 1, "title": " New title ", "description": "Details",
        "allow_anonymous": True, "allow_repeat": True, "deadline": deadline,
    })
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["revision"] == 2
    assert updated["title"] == "New title" and updated["description"] == "Details"
    assert updated["allow_anonymous"] is True and updated["allow_repeat"] is True
    assert updated["created_at"] == survey["created_at"]
    assert updated["updated_at"] >= survey["updated_at"]
    cleared = api.patch(f"/api/surveys/{survey['id']}", headers=headers,
                        json={"expected_revision": 2, "deadline": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["deadline"] is None and cleared.json()["revision"] == 3


def test_invalid_survey_requests_do_not_write_or_change_draft(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    original = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    for payload in ({"title": " "}, {"title": "T", "owner_id": survey["owner_id"]},
                    {"title": "T", "status": "published"}, {"title": "T", "allow_repeat": "false"}):
        response = api.post("/api/surveys", headers=headers, json=payload)
        assert response.status_code == 422, (payload, response.text)
    for payload in ({"title": "Changed"}, {"expected_revision": 1},
                    {"expected_revision": 1, "title": None}, {"expected_revision": 1, "questions": []}):
        response = api.patch(f"/api/surveys/{survey['id']}", headers=headers, json=payload)
        assert response.status_code == 422, (payload, response.text)
    assert database.surveys.count_documents({}) == 1
    assert database.surveys.find_one({"_id": original["_id"]}) == original


def test_every_draft_endpoint_requires_authentication(api, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    base = "/api/surveys/" + survey["id"]
    qid = "q_" + "a" * 32
    requests = [
        ("POST", "/api/surveys", {"title": "T"}), ("GET", "/api/surveys", None),
        ("GET", base, None), ("PATCH", base, {"expected_revision": 1, "title": "T"}),
        ("POST", base + "/questions?expected_revision=1", {"type": "text", "title": "T"}),
        ("PUT", base + f"/questions/{qid}?expected_revision=1", {"type": "text", "title": "T"}),
        ("DELETE", base + f"/questions/{qid}?expected_revision=1", None),
        ("PUT", base + "/question-order?expected_revision=1", {"question_ids": []}),
    ]
    for method, path, payload in requests:
        response = api.request(method, path, json=payload)
        assert response.status_code == 401, (method, path, response.text)
        assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_foreign_and_unknown_surveys_are_indistinguishable_on_all_operations(api, database, owner, target):
    _, alice = owner
    survey = create_survey(api, alice)
    _, bob = actor(api, "Survey_Bob")
    survey_id = {"foreign": survey["id"], "missing": str(ObjectId()), "malformed": "invalid-id"}[target]
    base = "/api/surveys/" + survey_id
    qid = "q_" + "a" * 32
    original = database.surveys.find_one({})
    requests = [
        ("GET", base, None), ("PATCH", base, {"expected_revision": 1, "title": "stolen"}),
        ("POST", base + "/questions?expected_revision=1", {"type": "text", "title": "T"}),
        ("PUT", base + f"/questions/{qid}?expected_revision=1", {"type": "text", "title": "T"}),
        ("DELETE", base + f"/questions/{qid}?expected_revision=1", None),
        ("PUT", base + "/question-order?expected_revision=1", {"question_ids": []}),
    ]
    details = []
    for method, path, payload in requests:
        response = api.request(method, path, headers=bob, json=payload)
        assert response.status_code == 404, (method, path, response.text)
        details.append(response.json())
    assert all(detail == details[0] for detail in details)
    assert database.surveys.find_one({}) == original


def test_adds_four_question_types_in_order_with_generated_stable_ids(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    definitions = [
        {"type": "single_choice", "title": "Single", "required": True, "options": [{"label": "A"}, {"label": "B"}]},
        {"type": "multiple_choice", "title": "Two", "options": [{"label": "A"}, {"label": "B"}], "min_selections": 2, "max_selections": 2},
        {"type": "text", "title": "Text", "min_length": 1, "max_length": 20},
        {"type": "number", "title": "Number", "min_value": -3.5, "max_value": 9, "integer_only": True},
    ]
    observed_ids = []
    for index, definition in enumerate(definitions, 1):
        survey = add_question(api, headers, survey, definition)
        assert survey["revision"] == index + 1
        assert survey["question_count"] == index
        assert [question["type"] for question in survey["questions"]] == [item["type"] for item in definitions[:index]]
        question = survey["questions"][-1]
        assert re.fullmatch(r"q_[a-f0-9]{32}", question["id"])
        assert question["id"] not in observed_ids
        observed_ids.append(question["id"])
        for option in question.get("options", []):
            assert re.fullmatch(r"opt_[a-f0-9]{32}", option["id"])
        assert [item["id"] for item in survey["questions"]] == observed_ids
    stored = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    assert stored["questions"] == survey["questions"]
    assert survey["questions"][0]["required"] is True
    assert survey["questions"][1]["min_selections"] == survey["questions"][1]["max_selections"] == 2


def test_edit_keeps_question_and_reused_option_ids_then_deletes_question(api, owner):
    _, headers = owner
    survey = add_question(api, headers, create_survey(api, headers), {
        "type": "single_choice", "title": "Original", "options": [{"label": "A"}, {"label": "B"}],
    })
    question = survey["questions"][0]
    qid = question["id"]
    reused_id, discarded_id = [option["id"] for option in question["options"]]
    response = api.put(f"/api/surveys/{survey['id']}/questions/{qid}?expected_revision=2", headers=headers,
                      json={"type": "multiple_choice", "title": "Changed", "options": [
                          {"id": reused_id, "label": "Edited A"}, {"label": "New C"}], "max_selections": 1})
    assert response.status_code == 200, response.text
    changed = response.json()
    assert changed["revision"] == 3 and changed["question_count"] == 1
    assert changed["questions"][0]["id"] == qid
    assert changed["questions"][0]["options"][0]["id"] == reused_id
    assert changed["questions"][0]["options"][1]["id"] not in {reused_id, discarded_id}
    deleted = api.delete(f"/api/surveys/{survey['id']}/questions/{qid}?expected_revision=3", headers=headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["revision"] == 4 and deleted.json()["questions"] == []
    assert deleted.json()["question_count"] == 0


def test_foreign_option_ids_and_invalid_questions_never_mutate_draft(api, database, owner):
    _, headers = owner
    survey = add_question(api, headers, create_survey(api, headers), {
        "type": "single_choice", "title": "Original", "options": [{"label": "A"}, {"label": "B"}],
    })
    original = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    foreign_id = "opt_" + "f" * 32
    payload = {"type": "single_choice", "title": "T", "options": [
        {"id": foreign_id, "label": "A"}, {"label": "B"}],
    }
    base = f"/api/surveys/{survey['id']}/questions"
    for method, path in [("POST", base), ("PUT", base + "/" + survey["questions"][0]["id"])]:
        response = api.request(method, path, params={"expected_revision": 2}, headers=headers, json=payload)
        assert response.status_code == 422, response.text
    for invalid in (
        {"type": "text", "title": "T", "min_length": 5, "max_length": 2},
        {"type": "single_choice", "title": "T", "options": [{"label": "Ａ"}, {"label": "a"}]},
        {"type": "number", "title": "T", "min_value": "1"},
    ):
        response = api.post(base, params={"expected_revision": 2}, headers=headers, json=invalid)
        assert response.status_code == 422, response.text
    assert database.surveys.find_one({"_id": original["_id"]}) == original


def test_reorder_requires_exact_permutation_and_preserves_question_content(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    for title in ["One", "Two", "Three"]:
        survey = add_question(api, headers, survey, {"type": "text", "title": title})
    ids = [question["id"] for question in survey["questions"]]
    original = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    base = f"/api/surveys/{survey['id']}/question-order?expected_revision=4"
    for invalid in [ids[:-1], [ids[0], ids[0], ids[2]], [ids[0], ids[1], "q_" + "f" * 32]]:
        response = api.put(base, headers=headers, json={"question_ids": invalid})
        assert response.status_code == 422, response.text
        assert database.surveys.find_one({"_id": original["_id"]}) == original
    response = api.put(base, headers=headers, json={"question_ids": ids[::-1]})
    assert response.status_code == 200, response.text
    assert response.json()["questions"] == survey["questions"][::-1]
    assert response.json()["revision"] == 5


def test_missing_revision_and_unknown_questions_do_not_write(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    base = f"/api/surveys/{survey['id']}"
    qid = "q_" + "a" * 32
    original = database.surveys.find_one({})
    requests = [
        ("POST", "/questions", {"type": "text", "title": "T"}),
        ("PUT", f"/questions/{qid}", {"type": "text", "title": "T"}),
        ("DELETE", f"/questions/{qid}", None),
        ("PUT", "/question-order", {"question_ids": []}),
    ]
    for method, path, payload in requests:
        response = api.request(method, base + path, headers=headers, json=payload)
        assert response.status_code == 422, response.text
    for method, payload in [("PUT", {"type": "text", "title": "T"}), ("DELETE", None)]:
        response = api.request(method, base + f"/questions/{qid}?expected_revision=1", headers=headers, json=payload)
        assert response.status_code == 404, response.text
    assert database.surveys.find_one({"_id": original["_id"]}) == original


def test_stale_revision_conflicts_for_all_edits_without_overwriting(api, database, owner):
    _, headers = owner
    survey = add_question(api, headers, create_survey(api, headers), {"type": "text", "title": "Saved"})
    base = f"/api/surveys/{survey['id']}"
    qid = survey["questions"][0]["id"]
    original = database.surveys.find_one({})
    requests = [
        ("PATCH", base, {"expected_revision": 1, "title": "Stale"}),
        ("POST", base + "/questions?expected_revision=1", {"type": "text", "title": "Stale"}),
        ("PUT", base + f"/questions/{qid}?expected_revision=1", {"type": "text", "title": "Stale"}),
        ("DELETE", base + f"/questions/{qid}?expected_revision=1", None),
        ("PUT", base + "/question-order?expected_revision=1", {"question_ids": [qid]}),
    ]
    for method, path, payload in requests:
        response = api.request(method, path, headers=headers, json=payload)
        assert response.status_code == 409, (method, path, response.text)
    assert database.surveys.find_one({"_id": original["_id"]}) == original


def test_concurrent_same_revision_edits_have_exactly_one_winner(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    gate = Barrier(2)

    def attempt(title):
        gate.wait(timeout=10)
        response = api.post(f"/api/surveys/{survey['id']}/questions?expected_revision=1", headers=headers,
                            json={"type": "text", "title": title})
        return title, response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["Concurrent A", "Concurrent B"]))
    assert sorted(status for _, status in results) == [201, 409]
    winner = next(title for title, status in results if status == 201)
    saved = database.surveys.find_one({"_id": ObjectId(survey["id"])})
    assert saved["revision"] == 2
    assert len(saved["questions"]) == 1 and saved["questions"][0]["title"] == winner


def test_restart_retains_existing_users_sessions_and_survey_questions(mongo_settings, database):
    with TestClient(create_app(mongo_settings)) as first:
        user, headers = actor(first)
        survey = add_question(first, headers, create_survey(first, headers), {
            "type": "number", "title": "Age", "min_value": 0, "integer_only": True,
        })
    persisted = {name: list(database[name].find({})) for name in ("users", "sessions", "surveys")}
    with TestClient(create_app(mongo_settings)) as restarted:
        assert restarted.get("/api/auth/me", headers=headers).json() == user
        response = restarted.get(f"/api/surveys/{survey['id']}", headers=headers)
        assert response.status_code == 200 and response.json() == survey
        assert restarted.get("/api/surveys", headers=headers).json()["items"][0]["id"] == survey["id"]
    assert {name: list(database[name].find({})) for name in persisted} == persisted


def test_survey_schema_and_owner_list_index_are_created_idempotently(api, database, owner):
    _, headers = owner
    survey = create_survey(api, headers)
    original = database.surveys.find_one({})
    initialize_database(database)
    initialize_database(database)
    options = database.surveys.options()
    assert options["validationLevel"] == "strict" and options["validationAction"] == "error"
    schema = options["validator"]["$jsonSchema"]
    assert schema["bsonType"] == "object" and schema["additionalProperties"] is False
    assert {"owner_id", "questions", "revision", "schema_version", "status"} <= set(schema["required"])
    indexes = database.surveys.index_information()
    assert any(info["key"] == [("owner_id", 1), ("created_at", -1), ("_id", -1)] for info in indexes.values())
    assert database.surveys.find_one({}) == original
    assert api.get(f"/api/surveys/{survey['id']}", headers=headers).status_code == 200


def test_mongodb_rejects_invalid_survey_documents(api, database, owner):
    _, headers = owner
    create_survey(api, headers)
    original = database.surveys.find_one({})
    invalid_documents = []
    missing = deepcopy(original)
    del missing["owner_id"]
    invalid_documents.append(missing)
    invalid_documents.append({**original, "owner_id": str(original["owner_id"])})
    invalid_documents.append({**original, "schema_version": "1"})
    invalid_documents.append({**original, "unexpected": "not allowed"})
    invalid_documents.append({**original, "questions": [{"id": "q_" + "a" * 32, "type": "unknown", "title": "T", "required": False}]})
    for document in invalid_documents:
        document["_id"] = ObjectId()
        with pytest.raises(WriteError) as error:
            database.surveys.insert_one(document)
        assert error.value.code == 121
    assert database.surveys.count_documents({}) == 1
    assert database.surveys.find_one({"_id": original["_id"]}) == original
