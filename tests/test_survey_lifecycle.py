"""Publication, sharing and closure against real MongoDB, without fake storage."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from threading import Barrier

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest
from pymongo.errors import DuplicateKeyError

from app.database.connection import initialize_database
from app.main import create_app


pytestmark = pytest.mark.integration
PASSWORD = "correct horse 42"


def actor(api, username="Lifecycle_Alice"):
    created = api.post("/api/auth/register", json={"username": username, "password": PASSWORD})
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def owner(api):
    return actor(api)


def draft(api, headers, *, with_question=True, **fields):
    response = api.post("/api/surveys", headers=headers, json={"title": "Feedback", **fields})
    assert response.status_code == 201, response.text
    survey = response.json()
    if with_question:
        response = api.post(
            f"/api/surveys/{survey['id']}/questions", headers=headers,
            params={"expected_revision": survey["revision"]},
            json={"type": "single_choice", "title": "Choose", "required": True,
                  "options": [{"label": "A"}, {"label": "B"}]},
        )
        assert response.status_code == 201, response.text
        survey = response.json()
    return survey


def transition(api, headers, survey, operation):
    return api.post(
        f"/api/surveys/{survey['id']}/{operation}", headers=headers,
        json={"expected_revision": survey["revision"]},
    )


def published(api, headers, **fields):
    survey = draft(api, headers, **fields)
    response = transition(api, headers, survey, "publish")
    assert response.status_code == 200, response.text
    return response.json()


def stored(database, survey):
    return database.surveys.find_one({"_id": ObjectId(survey["id"])})


def snapshot(database):
    # Include auth collections: opening a share must reuse the old session.
    return {
        name: list(database[name].find({}).sort("_id", 1))
        for name in database.list_collection_names()
    }


@pytest.mark.parametrize("already_published", [False, True])
def test_historical_fractional_integer_bounds_remain_readable(api, database, owner, already_published):
    _, headers = owner
    survey = draft(api, headers, with_question=False)
    base = f"/api/surveys/{survey['id']}"
    valid = {"type": "number", "title": "旧整数题", "required": True,
             "integer_only": True, "min_value": 1, "max_value": 5}
    response = api.post(base + "/questions", headers=headers,
                        params={"expected_revision": survey["revision"]}, json=valid)
    assert response.status_code == 201
    survey = response.json()
    if already_published:
        response = transition(api, headers, survey, "publish")
        assert response.status_code == 200
        survey = response.json()
    # Model an accepted historical document without rewriting real user data.
    database.surveys.update_one({"_id": ObjectId(survey["id"])},
                                {"$set": {"questions.0.min_value": 1.2, "questions.0.max_value": 5.8}})
    original = stored(database, survey)
    detail = api.get(base, headers=headers)
    assert detail.status_code == 200 and detail.json()["questions"][0]["min_value"] == 1.2
    qid = survey["questions"][0]["id"]
    if already_published:
        assert api.get(survey["share_url"], headers=headers).status_code == 200
        checked = api.post(survey["share_url"] + "/validate", headers=headers,
                           json={"question_id": qid, "value": 2})
        assert checked.status_code == 200
        assert api.post(survey["share_url"] + "/validate", headers=headers,
                         json={"question_id": qid, "value": 2.5}).status_code == 422
        assert stored(database, survey) == original
    else:
        assert transition(api, headers, survey, "publish").status_code == 422
        assert stored(database, survey) == original
        repaired = api.put(base + "/questions/" + qid, headers=headers,
                            params={"expected_revision": survey["revision"]}, json=valid)
        assert repaired.status_code == 200 and repaired.json()["questions"][0]["id"] == qid
        assert transition(api, headers, repaired.json(), "publish").status_code == 200


def test_draft_has_no_share_and_owner_summary_exposes_availability(api, database, owner):
    _, headers = owner
    survey = draft(api, headers)
    for key in ("share_id", "share_url", "published_at", "closed_at"):
        assert survey[key] is None
    assert survey["availability"] == "draft" and survey["is_open"] is False
    summary = api.get("/api/surveys", headers=headers).json()["items"][0]
    assert "questions" not in summary
    assert summary == {key: value for key, value in survey.items()
                       if key not in ("questions", "jump_rules", "question_sources")}
    assert stored(database, survey)["schema_version"] == 4
    assert stored(database, survey)["question_sources"] == []
    assert api.get("/survey/" + "a" * 32, headers=headers).status_code == 404


def test_publish_preserves_draft_content_and_records_utc_millisecond_dates(api, database, owner):
    _, headers = owner
    survey = draft(api, headers, description="Original details", allow_repeat=True)
    before = stored(database, survey)
    response = transition(api, headers, survey, "publish")
    assert response.status_code == 200, response.text
    opened = response.json()
    assert opened["status"] == "published" and opened["revision"] == survey["revision"] + 1
    assert opened["availability"] == "open" and opened["is_open"] is True
    assert re.fullmatch(r"[A-Za-z0-9_-]{32}", opened["share_id"])
    assert opened["share_url"] == "/survey/" + opened["share_id"]
    assert opened["closed_at"] is None
    after = stored(database, survey)
    for key in ("owner_id", "title", "description", "questions", "allow_anonymous",
                "allow_repeat", "deadline", "created_at", "schema_version"):
        assert after[key] == before[key], key
    assert after["published_at"] == after["updated_at"]
    assert after["published_at"] >= before["updated_at"]
    assert after["published_at"].utcoffset() == timedelta(0)
    assert after["published_at"].microsecond % 1000 == 0
    assert api.get(f"/api/surveys/{survey['id']}", headers=headers).json() == opened
    summary = api.get("/api/surveys", headers=headers).json()["items"][0]
    assert summary == {key: value for key, value in opened.items()
                       if key not in ("questions", "jump_rules", "question_sources")}


def test_empty_survey_cannot_publish_and_is_unchanged(api, database, owner):
    _, headers = owner
    survey = draft(api, headers, with_question=False)
    original = stored(database, survey)
    response = transition(api, headers, survey, "publish")
    assert response.status_code == 422, response.text
    assert stored(database, survey) == original


@pytest.mark.parametrize("after_deadline_ms", [0, 1], ids=["exact-deadline", "past-deadline"])
def test_publish_rechecks_deadline_at_transition_without_writing(
    api, database, owner, monkeypatch, after_deadline_ms,
):
    _, headers = owner
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    survey = draft(api, headers, deadline=deadline.isoformat())
    original = stored(database, survey)
    monkeypatch.setattr("app.surveys.service.mongo_utc_now", lambda: deadline + timedelta(milliseconds=after_deadline_ms))
    response = transition(api, headers, survey, "publish")
    assert response.status_code == 422, response.text
    assert stored(database, survey) == original


@pytest.mark.parametrize("invalid_case", [
    "duplicate-question-id", "duplicate-option-id", "duplicate-normalized-label", "reversed-text-range",
])
def test_publish_revalidates_persisted_question_configuration(api, database, owner, invalid_case):
    _, headers = owner
    survey = draft(api, headers)
    questions = deepcopy(survey["questions"])
    if invalid_case == "duplicate-question-id":
        questions.append(deepcopy(questions[0]))
    elif invalid_case == "duplicate-option-id":
        questions[0]["options"][1]["id"] = questions[0]["options"][0]["id"]
    elif invalid_case == "duplicate-normalized-label":
        questions[0]["options"][1]["label"] = " a "
    else:
        questions = [{"id": questions[0]["id"], "type": "text", "title": "Invalid range",
                      "required": False, "min_length": 20, "max_length": 2}]
    # The storage schema enforces types; publication must also check cross-field
    # business rules even for a legacy import or a direct database modification.
    database.surveys.update_one({"_id": ObjectId(survey["id"])}, {"$set": {"questions": questions}})
    original = stored(database, survey)
    response = transition(api, headers, survey, "publish")
    assert response.status_code == 422, (invalid_case, response.text)
    assert stored(database, survey) == original


@pytest.mark.parametrize("payload", [
    {}, {"expected_revision": 0}, {"expected_revision": True}, {"expected_revision": "2"},
    {"expected_revision": 2.0}, {"expected_revision": 2, "status": "published"},
], ids=["missing", "zero", "boolean", "string", "float", "extra-field"])
def test_transition_request_validation_does_not_write(api, database, owner, payload):
    _, headers = owner
    survey = draft(api, headers)
    original = stored(database, survey)
    for operation in ("publish", "close"):
        response = api.post(f"/api/surveys/{survey['id']}/{operation}", headers=headers, json=payload)
        assert response.status_code == 422, (operation, response.text)
        assert stored(database, survey) == original


def test_stale_publish_and_close_revisions_never_overwrite_current_state(api, database, owner):
    _, headers = owner
    survey = draft(api, headers)
    original = stored(database, survey)
    stale = {**survey, "revision": survey["revision"] - 1}
    assert transition(api, headers, stale, "publish").status_code == 409
    assert stored(database, survey) == original
    opened = transition(api, headers, survey, "publish").json()
    original = stored(database, survey)
    assert transition(api, headers, survey, "close").status_code == 409
    assert stored(database, survey) == original
    assert transition(api, headers, opened, "close").status_code == 200


def test_lifecycle_rejects_draft_close_republication_and_repeated_close(api, database, owner):
    _, headers = owner
    survey = draft(api, headers)
    original = stored(database, survey)
    assert transition(api, headers, survey, "close").status_code == 409
    assert stored(database, survey) == original
    opened = transition(api, headers, survey, "publish").json()
    original = stored(database, survey)
    assert transition(api, headers, opened, "publish").status_code == 409
    assert stored(database, survey) == original
    closed_response = transition(api, headers, opened, "close")
    assert closed_response.status_code == 200, closed_response.text
    closed = closed_response.json()
    assert closed["status"] == "closed" and closed["revision"] == opened["revision"] + 1
    assert closed["availability"] == "closed" and closed["is_open"] is False
    assert closed["share_id"] == opened["share_id"] and closed["share_url"] == opened["share_url"]
    assert closed["published_at"] == opened["published_at"]
    after = stored(database, survey)
    assert after["closed_at"] == after["updated_at"]
    assert after["closed_at"] >= after["published_at"]
    assert after["closed_at"].microsecond % 1000 == 0
    assert after["questions"] == original["questions"]
    for operation in ("publish", "close"):
        assert transition(api, headers, closed, operation).status_code == 409
        assert stored(database, survey) == after
    response = api.get(closed["share_url"], headers=headers)
    assert response.status_code == 410
    assert stored(database, survey) == after


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_transition_ownership_hides_foreign_and_unknown_surveys(api, database, owner, target):
    _, alice = owner
    survey = draft(api, alice)
    _, bob = actor(api, "Lifecycle_Bob")
    survey_id = {"foreign": survey["id"], "missing": str(ObjectId()), "malformed": "invalid-id"}[target]
    original = snapshot(database)
    details = []
    for operation in ("publish", "close"):
        response = api.post(f"/api/surveys/{survey_id}/{operation}", headers=bob,
                            json={"expected_revision": survey["revision"]})
        assert response.status_code == 404, response.text
        details.append(response.json())
    assert details[0] == details[1]
    assert snapshot(database) == original


def test_lifecycle_and_share_require_existing_bearer_authentication(api, database, owner):
    _, headers = owner
    survey = published(api, headers)
    original = snapshot(database)
    for bad_headers in ({}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer invalid-token"}):
        for operation in ("publish", "close"):
            response = api.post(f"/api/surveys/{survey['id']}/{operation}", headers=bad_headers,
                                json={"expected_revision": survey["revision"]})
            assert response.status_code == 401, response.text
            assert response.headers["WWW-Authenticate"] == "Bearer"
        response = api.get(survey["share_url"], headers=bad_headers)
        assert response.status_code == 401, response.text
        assert response.headers["WWW-Authenticate"] == "Bearer"
    assert snapshot(database) == original


@pytest.mark.parametrize("status", ["published", "closed"])
def test_published_and_closed_surveys_freeze_every_draft_edit(api, database, owner, status):
    _, headers = owner
    survey = published(api, headers)
    if status == "closed":
        response = transition(api, headers, survey, "close")
        assert response.status_code == 200, response.text
        survey = response.json()
    original = stored(database, survey)
    base = f"/api/surveys/{survey['id']}"
    qid, revision = survey["questions"][0]["id"], survey["revision"]
    requests = [
        ("PATCH", base, {"expected_revision": revision, "title": "Changed"}),
        ("POST", base + f"/questions?expected_revision={revision}", {"type": "text", "title": "Added"}),
        ("PUT", base + f"/questions/{qid}?expected_revision={revision}", {"type": "text", "title": "Edited"}),
        ("DELETE", base + f"/questions/{qid}?expected_revision={revision}", None),
        ("PUT", base + f"/question-order?expected_revision={revision}", {"question_ids": [qid]}),
    ]
    for method, path, payload in requests:
        response = api.request(method, path, headers=headers, json=payload)
        assert response.status_code == 409, (method, path, response.text)
        assert stored(database, survey) == original


@pytest.mark.parametrize("allow_anonymous", [False, True], ids=["named", "anonymous"])
def test_share_is_minimal_authenticated_read_only_json_for_owner_and_other_users(
    api, database, owner, allow_anonymous,
):
    _, alice = owner
    survey = published(api, alice, description="Public instructions", allow_anonymous=allow_anonymous,
                       allow_repeat=True)
    _, bob = actor(api, "Lifecycle_Bob")
    original = snapshot(database)
    expected = {key: survey[key] for key in (
        "id", "title", "description", "allow_anonymous", "allow_repeat", "deadline", "revision", "questions", "jump_rules",
    )}
    for headers in (alice, bob):
        response = api.get(survey["share_url"], headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Type"].startswith("application/json")
        assert response.json() == expected
    assert api.get(survey["share_url"]).status_code == 401
    assert api.get(f"/api/surveys/{survey['id']}", headers=bob).status_code == 404
    assert api.get("/api/surveys", headers=bob).json()["items"] == []
    assert snapshot(database) == original
    assert "answers" not in database.list_collection_names()


def test_unknown_and_malformed_share_tokens_are_not_found(api, owner):
    _, headers = owner
    for token in ("short", "a" * 31, "a" * 33, "." + "a" * 31, "a" * 32):
        response = api.get("/survey/" + token, headers=headers)
        assert response.status_code == 404, (token, response.text)


@pytest.mark.parametrize("offset_ms,availability,share_status", [
    (-1, "open", 200), (0, "expired", 410), (1, "expired", 410),
], ids=["before", "equal", "after"])
def test_deadline_boundary_is_computed_without_mutating_or_deleting_survey(
    api, database, owner, monkeypatch, offset_ms, availability, share_status,
):
    _, headers = owner
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    survey = published(api, headers, deadline=deadline.isoformat())
    original = snapshot(database)
    monkeypatch.setattr("app.surveys.service.mongo_utc_now", lambda: deadline + timedelta(milliseconds=offset_ms))
    detail = api.get(f"/api/surveys/{survey['id']}", headers=headers).json()
    summary = api.get("/api/surveys", headers=headers).json()["items"][0]
    for result in (detail, summary):
        assert result["status"] == "published"
        assert result["availability"] == availability
        assert result["is_open"] is (share_status == 200)
        assert result["revision"] == survey["revision"]
    assert api.get(survey["share_url"], headers=headers).status_code == share_status
    assert snapshot(database) == original


def test_expired_published_survey_can_be_explicitly_closed(api, database, owner, monkeypatch):
    _, headers = owner
    deadline = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    survey = published(api, headers, deadline=deadline.isoformat())
    monkeypatch.setattr("app.surveys.service.mongo_utc_now", lambda: deadline)
    assert api.get(survey["share_url"], headers=headers).status_code == 410
    response = transition(api, headers, survey, "close")
    assert response.status_code == 200, response.text
    closed = response.json()
    assert closed["availability"] == "closed" and closed["status"] == "closed"
    assert closed["share_id"] == survey["share_id"]
    assert stored(database, survey)["closed_at"] == deadline
    assert api.get(survey["share_url"], headers=headers).status_code == 410


@pytest.mark.parametrize("operation", ["publish", "close"])
def test_concurrent_transitions_at_same_revision_have_one_winner(api, database, owner, operation):
    _, headers = owner
    survey = draft(api, headers) if operation == "publish" else published(api, headers)
    gate = Barrier(2)

    def attempt(_):
        gate.wait(timeout=10)
        return transition(api, headers, survey, operation)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 409]
    saved = stored(database, survey)
    winner = next(response.json() for response in responses if response.status_code == 200)
    assert saved["revision"] == survey["revision"] + 1
    assert saved["status"] == {"publish": "published", "close": "closed"}[operation]
    assert saved["share_id"] == winner["share_id"]
    assert saved["questions"] == survey["questions"]


def test_publish_racing_with_draft_edit_cannot_publish_or_erase_unsaved_content(api, database, owner):
    _, headers = owner
    survey = draft(api, headers)
    gate = Barrier(2)

    def attempt(operation):
        gate.wait(timeout=10)
        if operation == "publish":
            return operation, transition(api, headers, survey, operation)
        return operation, api.patch(
            f"/api/surveys/{survey['id']}", headers=headers,
            json={"expected_revision": survey["revision"], "title": "Concurrent edit"},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, ["publish", "edit"]))
    assert sorted(response.status_code for _, response in responses) == [200, 409]
    winner = next(operation for operation, response in responses if response.status_code == 200)
    saved = stored(database, survey)
    assert saved["revision"] == survey["revision"] + 1
    assert saved["questions"] == survey["questions"]
    if winner == "publish":
        assert saved["status"] == "published" and saved["title"] == survey["title"]
        assert saved["share_id"] is not None
    else:
        assert saved["status"] == "draft" and saved["title"] == "Concurrent edit"
        assert saved["share_id"] is None and saved["published_at"] is None


def test_share_token_collision_retries_without_mutating_the_existing_share(api, database, owner, monkeypatch):
    _, headers = owner
    first = published(api, headers)
    first_document = stored(database, first)
    second = draft(api, headers, title="Another questionnaire")
    tokens = iter([first["share_id"], "Z" * 32])
    # Only random generation is controlled; MongoDB must actually reject the
    # duplicate token before the service can allocate the second one.
    monkeypatch.setattr("app.surveys.service.secrets.token_urlsafe", lambda length: next(tokens))
    response = transition(api, headers, second, "publish")
    assert response.status_code == 200, response.text
    assert response.json()["share_id"] == "Z" * 32
    assert response.json()["revision"] == second["revision"] + 1
    assert stored(database, first) == first_document
    assert api.get(first["share_url"], headers=headers).json()["id"] == first["id"]
    assert api.get(response.json()["share_url"], headers=headers).json()["id"] == second["id"]


def test_restart_keeps_share_link_existing_users_sessions_and_question_ids(mongo_settings, database):
    with TestClient(create_app(mongo_settings)) as first:
        user, headers = actor(first)
        survey = published(first, headers)
        shared = first.get(survey["share_url"], headers=headers).json()
    original = snapshot(database)
    with TestClient(create_app(mongo_settings)) as restarted:
        assert restarted.get("/api/auth/me", headers=headers).json() == user
        response = restarted.get(survey["share_url"], headers=headers)
        assert response.status_code == 200 and response.json() == shared
        assert restarted.get(f"/api/surveys/{survey['id']}", headers=headers).json() == survey
    assert snapshot(database) == original


def test_share_index_is_partial_unique_and_surveys_have_no_ttl(api, database, owner):
    _, headers = owner
    opened = published(api, headers)
    # Multiple unpublished surveys have null tokens and must coexist.
    draft(api, headers, with_question=False)
    draft(api, headers, with_question=False)
    original = snapshot(database)
    initialize_database(database)
    initialize_database(database)
    indexes = database.surveys.index_information()
    share_index = indexes["uq_surveys_share_id"]
    assert share_index["key"] == [("share_id", 1)] and share_index["unique"] is True
    assert share_index["partialFilterExpression"] == {"share_id": {"$type": "string"}}
    assert all("expireAfterSeconds" not in index for index in indexes.values())
    schema = database.surveys.options()["validator"]["$jsonSchema"]
    assert set(schema["properties"]["status"]["enum"]) == {"draft", "published", "closed"}
    duplicate = deepcopy(stored(database, opened))
    duplicate["_id"] = ObjectId()
    with pytest.raises(DuplicateKeyError):
        database.surveys.insert_one(duplicate)
    assert snapshot(database) == original
