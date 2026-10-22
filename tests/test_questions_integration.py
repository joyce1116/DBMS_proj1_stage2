"""Stage-two private question versions against real MongoDB and old survey APIs."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import re
from threading import Barrier
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from app.database.connection import initialize_database
from app.main import create_app
from app.questions.repository import QuestionRepository
from app.questions.service import QuestionService


pytestmark = pytest.mark.integration
BASE = "/api/questions"


def actor(api, username="Question_Owner"):
    credentials = {"username": username, "password": "Question_Password_123"}
    created = api.post("/api/auth/register", json=credentials)
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def owner(api):
    return actor(api)


def create(api, headers, content=None):
    result = api.post(BASE, headers=headers, json=content or {"type": "text", "title": "Original"})
    assert result.status_code == 201, result.text
    return result.json()


def derive(api, headers, parent, content):
    result = api.post(f"{BASE}/{parent['id']}/versions", headers=headers, json=content)
    assert result.status_code == 201, result.text
    return result.json()


def old_collections(database):
    names = ("users", "sessions", "surveys", "submissions")
    return {
        name: {
            "documents": list(database[name].find({}).sort("_id", 1)),
            "options": database[name].options(),
            "indexes": database[name].index_information(),
        }
        for name in names
    }


@pytest.mark.parametrize("content", [
    {"type": "single_choice", "title": "  Choose one  ", "required": True,
     "options": [{"label": "A"}, {"label": "B"}]},
    {"type": "multiple_choice", "title": "Choose two", "required": True,
     "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
     "min_selections": 2, "max_selections": 2},
    {"type": "text", "title": "Comment", "min_length": 2, "max_length": 40},
    {"type": "number", "title": "Count", "integer_only": True,
     "min_value": 0.0, "max_value": 10.0},
])
def test_create_four_types_persists_private_version_without_local_question_id(api, database, owner, content):
    user, headers = owner
    before = old_collections(database)
    version = create(api, headers, content)
    assert set(version) == {
        "id", "family_id", "owner_id", "version", "parent_id", "operation", "content", "created_at",
    }
    assert ObjectId.is_valid(version["id"]) and ObjectId.is_valid(version["family_id"])
    assert version["owner_id"] == user["id"]
    assert version["version"] == 1 and version["parent_id"] is None
    assert version["operation"] == "create"
    assert version["content"]["type"] == content["type"]
    assert version["content"]["title"] == content["title"].strip()
    assert "id" not in version["content"]
    for option in version["content"].get("options", []):
        assert re.fullmatch(r"opt_[a-f0-9]{32}", option["id"])
    stored = database.question_versions.find_one({"_id": ObjectId(version["id"])})
    assert stored["owner_id"] == ObjectId(user["id"])
    assert stored["family_id"] == ObjectId(version["family_id"])
    assert stored["content"] == version["content"]
    assert stored["created_at"].utcoffset() == timedelta(0)
    assert stored["created_at"].microsecond % 1000 == 0
    if content["type"] == "number":
        assert type(stored["content"]["min_value"]) is int
    detail = api.get(f"{BASE}/{version['id']}", headers=headers)
    assert detail.status_code == 200 and detail.json() == version
    assert old_collections(database) == before


def test_branch_history_and_restore_append_versions_without_rewriting_prior_content(api, database, owner):
    _, headers = owner
    first = create(api, headers)
    first_stored = database.question_versions.find_one({"_id": ObjectId(first["id"])})
    second = derive(api, headers, first, {"type": "text", "title": "Changed", "max_length": 50})
    branch = derive(api, headers, first, {"type": "number", "title": "Branch", "min_value": 1})
    old_versions = list(database.question_versions.find({}).sort("version", 1))
    restored_result = api.post(f"{BASE}/{first['id']}/restore", headers=headers)
    assert restored_result.status_code == 201, restored_result.text
    restored = restored_result.json()
    expected = [first, second, branch, restored]
    assert [item["version"] for item in expected] == [1, 2, 3, 4]
    assert len({item["id"] for item in expected}) == 4
    assert {item["family_id"] for item in expected} == {first["family_id"]}
    assert second["parent_id"] == branch["parent_id"] == restored["parent_id"] == first["id"]
    assert restored["operation"] == "restore" and restored["content"] == first["content"]
    assert database.question_versions.find_one({"_id": ObjectId(first["id"])}) == first_stored
    assert list(database.question_versions.find({"version": {"$lte": 3}}).sort("version", 1)) == old_versions
    for item in expected:
        history = api.get(f"{BASE}/{item['id']}/history", headers=headers)
        assert history.status_code == 200, history.text
        assert history.json()["items"] == expected
        assert history.json()["total"] == 4
        assert api.get(f"{BASE}/{item['id']}", headers=headers).json() == item


def test_option_ids_are_owned_by_selected_parent_and_restoration_preserves_exact_content(api, database, owner):
    _, headers = owner
    first = create(api, headers, {"type": "single_choice", "title": "Original",
                                 "options": [{"label": "A"}, {"label": "B"}]})
    kept, removed = first["content"]["options"]
    second = derive(api, headers, first, {
        "type": "multiple_choice", "title": "Changed", "max_selections": 1,
        "options": [{"id": kept["id"], "label": "Edited A"}, {"label": "New C"}],
    })
    assert second["content"]["options"][0]["id"] == kept["id"]
    new_id = second["content"]["options"][1]["id"]
    assert new_id not in {kept["id"], removed["id"]}
    before = list(database.question_versions.find({}).sort("_id", 1))
    for parent, disallowed in ((first, new_id), (second, removed["id"])):
        bad = {"type": "single_choice", "title": "Smuggled option",
               "options": [{"id": disallowed, "label": "A"}, {"label": "B"}]}
        rejected = api.post(f"{BASE}/{parent['id']}/versions", headers=headers, json=bad)
        assert rejected.status_code == 422, rejected.text
    assert list(database.question_versions.find({}).sort("_id", 1)) == before
    restored = api.post(f"{BASE}/{first['id']}/restore", headers=headers)
    assert restored.status_code == 201, restored.text
    assert restored.json()["content"] == first["content"]


@pytest.mark.parametrize("content", [
    {"type": "text", "title": " ", "min_length": 1},
    {"type": "text", "title": "Bad bounds", "min_length": 10, "max_length": 2},
    {"type": "number", "title": "No coercion", "min_value": "1"},
    {"type": "number", "title": "No booleans", "max_value": True},
    {"type": "number", "title": "Integer bounds", "integer_only": True, "min_value": 1.2},
    {"type": "single_choice", "title": "Equivalent labels", "options": [{"label": "Ａ"}, {"label": "a"}]},
    {"type": "multiple_choice", "title": "Impossible bounds", "options": [{"label": "A"}, {"label": "B"}],
     "min_selections": 3},
    {"type": "text", "title": "No local ID", "id": "q_" + "a" * 32},
    {"type": "text", "title": "No client owner", "owner_id": str(ObjectId())},
    {"content": {"type": "text", "title": "No wrapper"}},
])
def test_invalid_content_never_creates_a_version_or_changes_history(api, database, owner, content):
    _, headers = owner
    original = create(api, headers)
    before = list(database.question_versions.find({}))
    for endpoint in (BASE, f"{BASE}/{original['id']}/versions"):
        result = api.post(endpoint, headers=headers, json=content)
        assert result.status_code == 422, (content, result.text)
    assert list(database.question_versions.find({})) == before
    valid = derive(api, headers, original, {"type": "text", "title": "After rejection"})
    assert valid["version"] == 2


def test_creation_rejects_client_option_ids_even_if_they_belong_to_another_owned_version(api, database, owner):
    _, headers = owner
    original = create(api, headers, {"type": "single_choice", "title": "Original",
                                    "options": [{"label": "A"}, {"label": "B"}]})
    before = list(database.question_versions.find({}))
    result = api.post(BASE, headers=headers, json=original["content"])
    assert result.status_code == 422, result.text
    assert list(database.question_versions.find({})) == before


def test_every_question_endpoint_requires_authentication(api, owner):
    _, headers = owner
    question = create(api, headers)
    detail = f"{BASE}/{question['id']}"
    for method, path, data in (
        ("POST", BASE, {"type": "text", "title": "Created"}),
        ("GET", BASE, None), ("GET", detail, None), ("GET", detail + "/history", None),
        ("POST", detail + "/versions", {"type": "text", "title": "Changed"}),
        ("POST", detail + "/restore", None),
    ):
        result = api.request(method, path, json=data)
        assert result.status_code == 401, (method, path, result.text)
        assert result.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_foreign_missing_and_malformed_versions_are_not_disclosed(api, database, owner, target):
    _, alice = owner
    original = create(api, alice)
    _, bob = actor(api, "Question_Bob")
    version_id = {"foreign": original["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target]
    detail = f"{BASE}/{version_id}"
    before = list(database.question_versions.find({}))
    errors = []
    for method, path, data in (
        ("GET", detail, None), ("GET", detail + "/history", None),
        ("POST", detail + "/versions", {"type": "text", "title": "Stolen"}),
        ("POST", detail + "/restore", None),
    ):
        result = api.request(method, path, headers=bob, json=data)
        assert result.status_code == 404, (path, result.text)
        errors.append(result.json())
    assert all(error == errors[0] for error in errors)
    assert api.get(BASE, headers=bob).json()["total"] == 0
    assert list(database.question_versions.find({})) == before


def test_personal_version_list_and_family_history_have_distinct_ordered_pagination(api, owner):
    _, headers = owner
    first = create(api, headers)
    other = create(api, headers, {"type": "text", "title": "Other family"})
    second = derive(api, headers, first, {"type": "text", "title": "Second"})
    third = derive(api, headers, first, {"type": "text", "title": "Third"})
    newest_first = [third, second, other, first]
    result = api.get(BASE, headers=headers, params={"offset": 1, "limit": 2})
    assert result.status_code == 200, result.text
    assert result.json() == {"items": newest_first[1:3], "total": 4, "offset": 1, "limit": 2}
    history_path = f"{BASE}/{third['id']}/history"
    history = api.get(history_path, headers=headers, params={"offset": 1, "limit": 1})
    assert history.status_code == 200, history.text
    assert history.json() == {"items": [second], "total": 3, "offset": 1, "limit": 1}
    for endpoint, total in ((BASE, 4), (history_path, 3)):
        empty = api.get(endpoint, headers=headers, params={"offset": 100, "limit": 100})
        assert empty.status_code == 200 and empty.json()["items"] == []
        assert empty.json()["total"] == total
        for params in ({"offset": -1}, {"offset": 2147483648}, {"limit": 0}, {"limit": 101}):
            rejected = api.get(endpoint, headers=headers, params=params)
            assert rejected.status_code == 422, rejected.text


def test_concurrent_versions_share_one_family_with_unique_contiguous_numbers(api, database, owner):
    _, headers = owner
    first = create(api, headers)
    barrier = Barrier(6)

    def append(index):
        barrier.wait(timeout=10)
        if index % 2:
            return api.post(f"{BASE}/{first['id']}/restore", headers=headers)
        return api.post(f"{BASE}/{first['id']}/versions", headers=headers,
                        json={"type": "text", "title": f"Concurrent {index}"})

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(append, range(6)))
    assert [result.status_code for result in results] == [201] * 6, [result.text for result in results]
    versions = [first, *[result.json() for result in results]]
    assert sorted(version["version"] for version in versions) == list(range(1, 8))
    assert len({version["id"] for version in versions}) == 7
    assert {version["family_id"] for version in versions} == {first["family_id"]}
    assert all(version["parent_id"] == first["id"] for version in versions[1:])
    indexes = database.question_versions.index_information()
    assert any(info["key"] == [("family_id", 1), ("version", 1)] and info.get("unique")
               for info in indexes.values())
    duplicate = deepcopy(database.question_versions.find_one({"_id": ObjectId(first["id"])}))
    duplicate["_id"] = ObjectId()
    with pytest.raises(DuplicateKeyError):
        database.question_versions.insert_one(duplicate)


def test_allocation_retry_exhaustion_returns_conflict_without_partial_write(api, database, owner, monkeypatch):
    _, headers = owner
    first = create(api, headers)
    derive(api, headers, first, {"type": "text", "title": "Already allocated"})
    before = list(database.question_versions.find({}).sort("version", 1))
    attempts = []

    def stale_allocation(repository, family_id, owner_id):
        attempts.append((family_id, owner_id))
        return 2

    # A stale read makes the real unique index reject each valid attempted insert.
    with monkeypatch.context() as patch:
        patch.setattr(QuestionRepository, "next_version", stale_allocation)
        rejected = api.post(f"{BASE}/{first['id']}/versions", headers=headers,
                            json={"type": "text", "title": "Never inserted"})
    assert rejected.status_code == 409, rejected.text
    assert len(attempts) == QuestionService.ALLOCATION_ATTEMPTS
    assert list(database.question_versions.find({}).sort("version", 1)) == before
    recovered = derive(api, headers, first, {"type": "text", "title": "After contention"})
    assert recovered["version"] == 3


def test_unrelated_unique_index_failure_returns_unavailable_without_retry(api, database, owner, monkeypatch):
    _, headers = owner
    first = create(api, headers)
    before = list(database.question_versions.find({}))
    database.question_versions.create_index([("content.title", 1)], unique=True, name="test_title_conflict")
    next_version = QuestionRepository.next_version
    attempts = []

    def observed_allocation(repository, *args):
        attempts.append(args)
        return next_version(repository, *args)

    monkeypatch.setattr(QuestionRepository, "next_version", observed_allocation)
    rejected = api.post(f"{BASE}/{first['id']}/restore", headers=headers)
    assert rejected.status_code == 503, rejected.text
    assert len(attempts) == 1
    assert list(database.question_versions.find({})) == before


def test_maximum_version_cannot_overflow_or_change_historical_versions(api, database, owner):
    _, headers = owner
    first = create(api, headers)
    maximum = deepcopy(database.question_versions.find_one())
    maximum.update({"_id": ObjectId(), "version": 2147483647,
                    "parent_id": ObjectId(first["id"]), "operation": "revise"})
    database.question_versions.insert_one(maximum)
    before = list(database.question_versions.find({}).sort("version", 1))
    for endpoint, content in (
        ("versions", {"type": "text", "title": "Overflow"}), ("restore", None),
    ):
        rejected = api.post(f"{BASE}/{first['id']}/{endpoint}", headers=headers, json=content)
        assert rejected.status_code == 409, rejected.text
    assert list(database.question_versions.find({}).sort("version", 1)) == before


def test_question_validator_rejects_malformed_stored_content(api, database, owner):
    _, headers = owner
    version = create(api, headers)
    original = database.question_versions.find_one({"_id": ObjectId(version["id"])})
    invalid = []
    for field in ("owner_id", "family_id", "content", "operation"):
        missing = deepcopy(original)
        del missing[field]
        invalid.append(missing)
    invalid += [
        {**original, "owner_id": version["owner_id"]},
        {**original, "version": 0},
        {**original, "unexpected": "field"},
        {**original, "content": {**original["content"], "id": "q_" + "a" * 32}},
    ]
    for document in invalid:
        document["_id"] = ObjectId()
        if "family_id" in document:
            # Keep schema rejection independent of the family/version index.
            document["family_id"] = document["_id"]
        with pytest.raises(WriteError) as rejected:
            database.question_versions.insert_one(document)
        assert rejected.value.code == 121
    assert database.question_versions.count_documents({}) == 1


def test_startup_is_idempotent_preserves_stage_one_and_legacy_published_flow(mongo_settings, database):
    with TestClient(create_app(mongo_settings)) as api:
        _, headers = actor(api)
        _, reader = actor(api, "Question_Respondent")
        survey_result = api.post("/api/surveys", headers=headers,
                                 json={"title": "Stage one survey", "allow_repeat": False})
        assert survey_result.status_code == 201, survey_result.text
        survey = survey_result.json()
        survey_base = "/api/surveys/" + survey["id"]
        for question in (
            {"type": "single_choice", "title": "Continue?", "required": True,
             "options": [{"label": "Yes"}, {"label": "No"}]},
            {"type": "number", "title": "Count", "required": True, "min_value": 0},
        ):
            result = api.post(survey_base + "/questions", headers=headers,
                              params={"expected_revision": survey["revision"]}, json=question)
            assert result.status_code == 201, result.text
            survey = result.json()
        choice, number = survey["questions"]
        result = api.put(survey_base + "/rules", headers=headers,
                         json={"expected_revision": survey["revision"], "rules": [
                             {"kind": "choice", "source_id": choice["id"], "target_id": "end",
                              "operator": "eq", "option_ids": [choice["options"][1]["id"]]},
                         ]})
        assert result.status_code == 200, result.text
        survey = result.json()
        result = api.post(survey_base + "/publish", headers=headers,
                          json={"expected_revision": survey["revision"]})
        assert result.status_code == 200, result.text
        published = result.json()
        assert "question_version_id" not in published["questions"][0]
        before = old_collections(database)
        first = create(api, headers)
        derive(api, headers, first, {"type": "text", "title": "Second"})
        initialize_database(database)
        initialize_database(database)
        assert old_collections(database) == before
        versions_before = list(database.question_versions.find({}).sort("version", 1))

    with TestClient(create_app(mongo_settings)) as restarted:
        assert old_collections(database) == before
        assert list(database.question_versions.find({}).sort("version", 1)) == versions_before
        assert restarted.get(f"{BASE}/{first['id']}", headers=headers).json() == first
        shared = restarted.get(published["share_url"], headers=reader)
        assert shared.status_code == 200, shared.text
        assert shared.json()["questions"] == published["questions"]
        answers = {choice["id"]: choice["options"][1]["id"]}
        navigation = restarted.post(published["share_url"] + "/navigate", headers=reader,
                                    json={"survey_revision": published["revision"], "answers": answers})
        assert navigation.status_code == 200, navigation.text
        assert navigation.json()["path"] == [choice["id"]] and navigation.json()["completed"]
        data = {"survey_revision": published["revision"], "request_id": uuid4().hex,
                "answers": answers, "anonymous": False}
        submitted = restarted.post(published["share_url"] + "/responses", headers=reader, json=data)
        assert submitted.status_code == 201, submitted.text
        listed = restarted.get(survey_base + "/responses", headers=headers)
        assert listed.status_code == 200 and listed.json()["total"] == 1
        stats = restarted.get(survey_base + "/statistics", headers=headers)
        assert stats.status_code == 200, stats.text
        assert stats.json()["total_responses"] == 1
        assert stats.json()["questions"][0]["options"][1]["count"] == 1
        assert stats.json()["questions"][1]["answer_count"] == 0
        closed = restarted.post(survey_base + "/close", headers=headers,
                                json={"expected_revision": published["revision"]})
        assert closed.status_code == 200 and closed.json()["status"] == "closed"
        replay = restarted.post(published["share_url"] + "/responses", headers=reader, json=data)
        assert replay.status_code == 200 and replay.json() == submitted.json()
