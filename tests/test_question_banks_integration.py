"""Managed banks add organization without rewriting saved versions or surveys."""

from copy import deepcopy

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest
from pymongo.errors import DuplicateKeyError, WriteError

from app.database.connection import initialize_database
from app.main import create_app


pytestmark = pytest.mark.integration
BASE = "/api/question-banks"


def actor(api, username="Bank_Owner"):
    credentials = {"username": username, "password": "Bank_Password_123"}
    created = api.post("/api/auth/register", json=credentials)
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def owner(api):
    return actor(api)


def bank(api, headers, **content):
    response = api.post(BASE, headers=headers, json={"title": "Question bank", **content})
    assert response.status_code == 201, response.text
    return response.json()


def version(api, headers, title="Reusable question", parent=None):
    path = "/api/questions" if parent is None else f"/api/questions/{parent['id']}/versions"
    response = api.post(path, headers=headers, json={"type": "text", "title": title})
    assert response.status_code == 201, response.text
    return response.json()


def entry(api, headers, target, question):
    response = api.post(f"{BASE}/{target['id']}/entries", headers=headers,
                        json={"version_id": question["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def grant(api, headers, target, username):
    response = api.post(f"{BASE}/{target['id']}/grants", headers=headers, json={"username": username})
    assert response.status_code == 201, response.text
    return response.json()


def test_bank_metadata_is_separate_from_existing_versions_and_stage_one_collections(api, database, owner):
    user, headers = owner
    original = version(api, headers)
    before = {name: list(database[name].find({})) for name in (
        "users", "sessions", "surveys", "submissions", "question_versions",
    )}
    created = bank(api, headers, title="  Research questions  ", description="  Reusable questions  ")
    assert set(created) == {"id", "owner_id", "title", "description", "revision", "created_at",
                            "updated_at", "can_manage"}
    assert created["owner_id"] == user["id"] and created["can_manage"] is True
    assert created["title"] == "Research questions" and created["description"] == "  Reusable questions  "
    assert created["revision"] == 1 and created["created_at"] == created["updated_at"]
    stored = database.question_banks.find_one({"_id": ObjectId(created["id"])})
    assert stored["owner_id"] == ObjectId(user["id"]) and stored["archived"] is False
    assert api.get(f"{BASE}/{created['id']}", headers=headers).json() == created
    assert api.get(f"{BASE}/{created['id']}/entries", headers=headers).json()["total"] == 0
    assert {name: list(database[name].find({})) for name in before} == before
    assert api.get(f"/api/questions/{original['id']}", headers=headers).json() == original


@pytest.mark.parametrize("content", [
    {"title": " "}, {"title": "x" * 201}, {"title": 3},
    {"title": "Valid", "description": None},
    {"title": "Valid", "owner_id": "forged"},
])
def test_invalid_bank_creation_has_no_partial_record(api, database, owner, content):
    _, headers = owner
    response = api.post(BASE, headers=headers, json=content)
    assert response.status_code == 422, response.text
    assert database.question_banks.count_documents({}) == 0


def test_metadata_patch_requires_current_revision_and_complete_valid_fields(api, database, owner):
    _, headers = owner
    created = bank(api, headers)
    path = f"{BASE}/{created['id']}"
    result = api.patch(path, headers=headers, json={"expected_revision": 1, "title": "  Renamed  "})
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated["title"] == "Renamed" and updated["revision"] == 2
    assert updated["created_at"] == created["created_at"]
    before = database.question_banks.find_one({"_id": ObjectId(created["id"])})
    for content, expected in (
        ({"expected_revision": 1, "description": "stale"}, 409),
        ({"expected_revision": 2}, 422),
        ({"expected_revision": 2, "title": None}, 422),
        ({"expected_revision": 2, "description": None}, 422),
        ({"expected_revision": 0, "title": "zero"}, 422),
        ({"expected_revision": True, "title": "bool"}, 422),
        ({"title": "missing revision"}, 422),
    ):
        rejected = api.patch(path, headers=headers, json=content)
        assert rejected.status_code == expected, rejected.text
        assert database.question_banks.find_one({"_id": before["_id"]}) == before


def test_entries_pin_versions_and_enforce_one_family_per_bank_without_changing_metadata_revision(api, database, owner):
    _, headers = owner
    original = version(api, headers)
    revised = version(api, headers, "Revised", parent=original)
    first, second = bank(api, headers), bank(api, headers, title="Another bank")
    saved_versions = list(database.question_versions.find({}).sort("_id", 1))
    selected = entry(api, headers, first, original)
    assert set(selected) == {"id", "bank_id", "family_id", "version_id", "created_at", "updated_at", "question"}
    assert selected["bank_id"] == first["id"]
    assert selected["family_id"] == original["family_id"] and selected["version_id"] == original["id"]
    assert selected["question"] == original
    document = database.question_bank_entries.find_one({"_id": ObjectId(selected["id"])})
    assert document["bank_id"] == ObjectId(first["id"])
    assert document["family_id"] == ObjectId(original["family_id"])
    assert document["version_id"] == ObjectId(original["id"])
    for target in (original, revised):
        duplicate = api.post(f"{BASE}/{first['id']}/entries", headers=headers, json={"version_id": target["id"]})
        assert duplicate.status_code == 409, duplicate.text
    entry(api, headers, second, revised)
    assert api.get(f"{BASE}/{first['id']}", headers=headers).json()["revision"] == 1
    assert list(database.question_versions.find({}).sort("_id", 1)) == saved_versions


def test_entry_update_uses_version_compare_and_swap_and_checks_family(api, database, owner):
    _, headers = owner
    original = version(api, headers)
    revised = version(api, headers, "Revision", parent=original)
    foreign_family = version(api, headers, "Different family")
    target = bank(api, headers)
    selected = entry(api, headers, target, original)
    path = f"{BASE}/{target['id']}/entries/{original['family_id']}"
    result = api.put(path, headers=headers,
                     json={"version_id": revised["id"], "expected_version_id": original["id"]})
    assert result.status_code == 200, result.text
    changed = result.json()
    assert changed["id"] == selected["id"] and changed["created_at"] == selected["created_at"]
    assert changed["question"] == revised and changed["version_id"] == revised["id"]
    before = list(database.question_bank_entries.find({}))
    for body, expected in (
        ({"version_id": original["id"], "expected_version_id": original["id"]}, 409),
        ({"version_id": foreign_family["id"], "expected_version_id": revised["id"]}, 422),
        ({"version_id": original["id"]}, 422),
        ({"version_id": original["id"], "expected_version_id": "bad-id"}, 404),
    ):
        rejected = api.put(path, headers=headers, json=body)
        assert rejected.status_code == expected, rejected.text
        assert list(database.question_bank_entries.find({})) == before
    assert api.get(f"{BASE}/{target['id']}", headers=headers).json()["revision"] == 1


def test_entry_removal_requires_current_version_and_preserves_library_history(api, database, owner):
    _, headers = owner
    original = version(api, headers)
    other = version(api, headers, "Other")
    target = bank(api, headers)
    entry(api, headers, target, original)
    path = f"{BASE}/{target['id']}/entries/{original['family_id']}"
    stale = api.delete(path, headers=headers, params={"expected_version_id": other["id"]})
    assert stale.status_code == 409, stale.text
    assert database.question_bank_entries.count_documents({}) == 1
    assert api.delete(path, headers=headers).status_code == 422
    removed = api.delete(path, headers=headers, params={"expected_version_id": original["id"]})
    assert removed.status_code == 204, removed.text
    assert api.delete(path, headers=headers, params={"expected_version_id": original["id"]}).status_code == 404
    assert database.question_bank_entries.count_documents({}) == 0
    assert api.get(f"/api/questions/{original['id']}", headers=headers).json() == original


def test_bank_lists_and_entries_paginate_stably_and_validate_bounds(api, owner):
    _, headers = owner
    targets = [bank(api, headers, title=f"Bank {index}") for index in range(3)]
    questions = [version(api, headers, f"Question {index}") for index in range(3)]
    entries = [entry(api, headers, targets[-1], question) for question in questions]
    listed = api.get(BASE, headers=headers, params={"offset": 1, "limit": 1})
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": [targets[1]], "total": 3, "offset": 1, "limit": 1}
    entry_path = f"{BASE}/{targets[-1]['id']}/entries"
    all_entries = api.get(entry_path, headers=headers).json()
    assert {item["id"] for item in all_entries["items"]} == {item["id"] for item in entries}
    page = api.get(entry_path, headers=headers, params={"offset": 1, "limit": 1})
    assert page.json() == {"items": all_entries["items"][1:2], "total": 3, "offset": 1, "limit": 1}
    for path in (BASE, entry_path):
        empty = api.get(path, headers=headers, params={"offset": 50})
        assert empty.status_code == 200 and empty.json()["items"] == [] and empty.json()["total"] == 3
        for params in ({"offset": -1}, {"offset": 2147483648}, {"limit": 0}, {"limit": 101}):
            assert api.get(path, headers=headers, params=params).status_code == 422
    assert api.get(BASE, headers=headers, params={"view": "all"}).status_code == 422


def test_bank_grantee_can_read_but_cannot_manage_or_repackage_someone_elses_questions(api, database, owner):
    user, headers = owner
    reader, reader_headers = actor(api, "Bank_Reader")
    target = bank(api, headers)
    question = version(api, headers)
    selected = entry(api, headers, target, question)
    grant(api, headers, target, reader["username"])
    response = api.get(f"{BASE}/{target['id']}", headers=reader_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {**target, "can_manage": False}
    assert api.get(f"{BASE}/{target['id']}/entries", headers=reader_headers).json()["items"] == [selected]
    assert api.get(BASE, headers=reader_headers).json()["total"] == 0
    shared = api.get(BASE, headers=reader_headers, params={"view": "shared"}).json()
    assert shared["items"] == [{**target, "can_manage": False}] and shared["total"] == 1
    before = list(database.question_banks.find({}))
    path = f"{BASE}/{target['id']}"
    for method, suffix, body, params in (
        ("PATCH", "", {"expected_revision": 1, "title": "Stolen"}, {}),
        ("DELETE", "", None, {"expected_revision": 1}),
        ("POST", "/entries", {"version_id": question["id"]}, {}),
        ("PUT", f"/entries/{question['family_id']}", {"version_id": question["id"], "expected_version_id": question["id"]}, {}),
        ("DELETE", f"/entries/{question['family_id']}", None, {"expected_version_id": question["id"]}),
    ):
        rejected = api.request(method, path + suffix, headers=reader_headers, json=body, params=params)
        assert rejected.status_code == 404, rejected.text
    own_bank = bank(api, reader_headers)
    repackaged = api.post(f"{BASE}/{own_bank['id']}/entries", headers=reader_headers, json={"version_id": question["id"]})
    assert repackaged.status_code == 404, repackaged.text
    assert list(database.question_banks.find({"owner_id": ObjectId(user["id"])})) == before


@pytest.mark.parametrize("target_kind", ["foreign", "missing", "malformed"])
def test_bank_access_hides_ungranted_missing_and_malformed_resources(api, database, owner, target_kind):
    _, headers = owner
    _, outsider = actor(api, "Bank_Outsider")
    target = bank(api, headers)
    question = version(api, headers)
    entry(api, headers, target, question)
    bank_id = {"foreign": target["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target_kind]
    path = f"{BASE}/{bank_id}"
    before = list(database.question_bank_entries.find({}))
    for method, suffix, body, params in (
        ("GET", "", None, {}), ("GET", "/entries", None, {}),
        ("PATCH", "", {"expected_revision": 1, "title": "Hidden"}, {}),
        ("DELETE", "", None, {"expected_revision": 1}),
        ("POST", "/entries", {"version_id": question["id"]}, {}),
    ):
        response = api.request(method, path + suffix, headers=outsider, json=body, params=params)
        assert response.status_code == 404, response.text
        assert response.headers["Cache-Control"] == "no-store"
    assert list(database.question_bank_entries.find({})) == before


def test_archive_is_revision_guarded_and_hides_bank_without_deleting_versions_or_entries(api, database, owner):
    _, headers = owner
    reader, reader_headers = actor(api, "Archive_Reader")
    target = bank(api, headers)
    question = version(api, headers)
    entry(api, headers, target, question)
    grant(api, headers, target, reader["username"])
    path = f"{BASE}/{target['id']}"
    assert api.delete(path, headers=headers, params={"expected_revision": 2}).status_code == 409
    archived = api.delete(path, headers=headers, params={"expected_revision": 1})
    assert archived.status_code == 204, archived.text
    assert database.question_banks.find_one({"_id": ObjectId(target["id"])})["archived"] is True
    assert database.question_bank_entries.count_documents({}) == 1
    for actor_headers in (headers, reader_headers):
        assert api.get(path, headers=actor_headers).status_code == 404
        assert api.get(path + "/entries", headers=actor_headers).status_code == 404
        assert api.get(BASE, headers=actor_headers).json()["items"] == []
    assert api.get(BASE, headers=reader_headers, params={"view": "shared"}).json()["items"] == []
    assert api.get(f"/api/questions/{question['id']}", headers=reader_headers).status_code == 404
    assert api.get(f"/api/questions/{question['id']}", headers=headers).json() == question


def test_bank_endpoints_require_login_and_never_cache_responses(api, owner):
    _, headers = owner
    target = bank(api, headers)
    question = version(api, headers)
    entry(api, headers, target, question)
    path = f"{BASE}/{target['id']}"
    for method, endpoint, body, params in (
        ("GET", BASE, None, {}), ("POST", BASE, {"title": "Private"}, {}),
        ("GET", path, None, {}), ("PATCH", path, {"expected_revision": 1, "title": "Private"}, {}),
        ("DELETE", path, None, {"expected_revision": 1}),
        ("GET", path + "/entries", None, {}), ("POST", path + "/entries", {"version_id": question["id"]}, {}),
        ("PUT", path + "/entries/" + question["family_id"], {"version_id": question["id"], "expected_version_id": question["id"]}, {}),
        ("DELETE", path + "/entries/" + question["family_id"], None, {"expected_version_id": question["id"]}),
    ):
        response = api.request(method, endpoint, json=body, params=params)
        assert response.status_code == 401, (method, endpoint, response.text)
        assert response.headers["WWW-Authenticate"] == "Bearer"
        assert response.headers["Cache-Control"] == "no-store"
    assert api.get(path, headers=headers).headers["Cache-Control"] == "no-store"


def test_bank_storage_rejects_malformed_documents_and_duplicate_families(api, database, owner):
    _, headers = owner
    target = bank(api, headers)
    question = version(api, headers)
    selected = entry(api, headers, target, question)
    bank_doc = database.question_banks.find_one({"_id": ObjectId(target["id"])})
    entry_doc = database.question_bank_entries.find_one({"_id": ObjectId(selected["id"])})
    duplicate = {**deepcopy(entry_doc), "_id": ObjectId()}
    with pytest.raises(DuplicateKeyError):
        database.question_bank_entries.insert_one(duplicate)
    for collection, document, changes in (
        (database.question_banks, bank_doc, {"owner_id": target["owner_id"]}),
        (database.question_banks, bank_doc, {"archived": "false"}),
        (database.question_banks, bank_doc, {"revision": 0}),
        (database.question_bank_entries, entry_doc, {"bank_id": target["id"]}),
        (database.question_bank_entries, entry_doc, {"version_id": question["id"]}),
        (database.question_bank_entries, entry_doc, {"extra": "forbidden"}),
    ):
        invalid = {**deepcopy(document), "_id": ObjectId(), **changes}
        if collection.name == "question_bank_entries" and "bank_id" not in changes:
            invalid["bank_id"] = ObjectId()
        with pytest.raises(WriteError) as rejected:
            collection.insert_one(invalid)
        assert rejected.value.code == 121
    assert database.question_banks.count_documents({}) == database.question_bank_entries.count_documents({}) == 1


def test_bank_initialization_and_restart_preserve_stored_data(mongo_settings, database):
    with TestClient(create_app(mongo_settings)) as api:
        _, headers = actor(api)
        target = bank(api, headers)
        question = version(api, headers)
        selected = entry(api, headers, target, question)
        before = {name: list(database[name].find({}).sort("_id", 1)) for name in (
            "users", "sessions", "surveys", "submissions", "question_versions", "question_banks", "question_bank_entries",
        )}
        initialize_database(database)
        initialize_database(database)
        assert {name: list(database[name].find({}).sort("_id", 1)) for name in before} == before
    with TestClient(create_app(mongo_settings)) as restarted:
        assert restarted.get(f"{BASE}/{target['id']}", headers=headers).json() == target
        assert restarted.get(f"{BASE}/{target['id']}/entries", headers=headers).json()["items"] == [selected]
        assert {name: list(database[name].find({}).sort("_id", 1)) for name in before} == before
