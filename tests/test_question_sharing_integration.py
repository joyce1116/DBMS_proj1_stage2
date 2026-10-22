"""Exact-version and bank grants, frozen reuse, and privacy-preserving usage."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier
from uuid import uuid4

from bson import ObjectId
import pytest
from pymongo.errors import DuplicateKeyError, WriteError


pytestmark = pytest.mark.integration
QUESTIONS = "/api/questions"
BANKS = "/api/question-banks"


def actor(api, username="Sharing_Owner"):
    credentials = {"username": username, "password": "Sharing_Password_123"}
    created = api.post("/api/auth/register", json=credentials)
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def people(api):
    return actor(api), actor(api, "Sharing_Reader"), actor(api, "Sharing_Outsider")


def version(api, headers, content=None, parent=None):
    path = QUESTIONS if parent is None else f"{QUESTIONS}/{parent['id']}/versions"
    response = api.post(path, headers=headers, json=content or {"type": "text", "title": "Reusable text", "required": True})
    assert response.status_code == 201, response.text
    return response.json()


def bank(api, headers, title="Shared bank"):
    response = api.post(BANKS, headers=headers, json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


def add_entry(api, headers, target, question):
    response = api.post(f"{BANKS}/{target['id']}/entries", headers=headers, json={"version_id": question["id"]})
    assert response.status_code == 201, response.text
    return response.json()


def grant_path(target, kind="version"):
    return f"{QUESTIONS if kind == 'version' else BANKS}/{target['id']}/grants"


def grant(api, headers, target, recipient, kind="version"):
    response = api.post(grant_path(target, kind), headers=headers, json={"username": recipient["username"]})
    assert response.status_code == 201, response.text
    return response.json()


def revoke(api, headers, target, recipient, kind="version"):
    response = api.delete(grant_path(target, kind) + "/" + recipient["id"], headers=headers)
    assert response.status_code == 204, response.text


def survey(api, headers, title="Reusable survey", **fields):
    response = api.post("/api/surveys", headers=headers, json={"title": title, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def select(api, headers, target, question, local_id=None):
    path = f"/api/surveys/{target['id']}/question-selections"
    if local_id is not None:
        path += "/" + local_id
    response = api.request("POST" if local_id is None else "PUT", path, headers=headers,
                           params={"expected_revision": target["revision"]}, json={"version_id": question["id"]})
    assert response.status_code == (201 if local_id is None else 200), response.text
    return response.json()


def usage(api, headers, question, **params):
    response = api.get(f"{QUESTIONS}/{question['id']}/usage", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_direct_grant_allows_one_version_and_never_history_mutations_or_resharing(api, database, people):
    (owner, headers), (reader, reader_headers), (outsider, outsider_headers) = people
    original = version(api, headers)
    revised = version(api, headers, {"type": "text", "title": "Later version"}, parent=original)
    granted = grant(api, headers, original, reader)
    assert set(granted) == {"id", "resource_type", "resource_id", "owner_id", "grantee_id", "username", "created_at"}
    assert granted["resource_type"] == "version" and granted["resource_id"] == original["id"]
    assert granted["owner_id"] == owner["id"] and granted["grantee_id"] == reader["id"]
    assert granted["username"] == reader["username"]
    assert api.get(f"{QUESTIONS}/{original['id']}", headers=reader_headers).json() == original
    assert api.get(f"{QUESTIONS}/{revised['id']}", headers=reader_headers).status_code == 404
    assert api.get(QUESTIONS, headers=reader_headers).json()["total"] == 0
    assert api.get(f"{QUESTIONS}/{original['id']}", headers=outsider_headers).status_code == 404
    before = list(database.question_versions.find({}).sort("_id", 1))
    for method, suffix, body in (
        ("GET", "/history", None),
        ("POST", "/versions", {"type": "text", "title": "Unauthorized branch"}),
        ("POST", "/restore", None),
        ("GET", "/grants", None),
        ("POST", "/grants", {"username": outsider["username"]}),
    ):
        denied = api.request(method, f"{QUESTIONS}/{original['id']}" + suffix,
                             headers=reader_headers, json=body)
        assert denied.status_code == 404, denied.text
    assert list(database.question_versions.find({}).sort("_id", 1)) == before
    assert database.question_grants.count_documents({}) == 1


@pytest.mark.parametrize("kind", ["version", "bank"])
def test_grant_creation_is_case_insensitive_and_idempotent_and_revoke_is_idempotent(api, database, people, kind):
    (_, headers), (reader, reader_headers), _ = people
    target = version(api, headers) if kind == "version" else bank(api, headers)
    path = grant_path(target, kind)
    first = grant(api, headers, target, reader, kind)
    repeated = api.post(path, headers=headers, json={"username": reader["username"].swapcase()})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == first and database.question_grants.count_documents({}) == 1
    stored = database.question_grants.find_one({"_id": ObjectId(first["id"])})
    assert stored["resource_id"] == ObjectId(target["id"]) and stored["grantee_id"] == ObjectId(reader["id"])
    listed = api.get(path, headers=headers, params={"offset": 0, "limit": 1})
    assert listed.json() == {"items": [first], "total": 1, "offset": 0, "limit": 1}
    revoke(api, headers, target, reader, kind)
    revoke(api, headers, target, reader, kind)
    assert database.question_grants.count_documents({}) == 0
    assert api.get(path, headers=headers).json()["total"] == 0
    if kind == "version":
        assert api.get(f"{QUESTIONS}/{target['id']}", headers=reader_headers).status_code == 404
    else:
        assert api.get(f"{BANKS}/{target['id']}", headers=reader_headers).status_code == 404


@pytest.mark.parametrize("kind", ["version", "bank"])
def test_invalid_grantee_and_injected_grant_metadata_never_write(api, database, people, kind):
    (owner, headers), (reader, _), _ = people
    target = version(api, headers) if kind == "version" else bank(api, headers)
    for body, expected in (
        ({"username": owner["username"]}, 422),
        ({"username": "Unknown_Account"}, 404),
        ({"username": reader["username"], "owner_id": owner["id"]}, 422),
        ({"username": None}, 422), ({"username": 123}, 422),
    ):
        result = api.post(grant_path(target, kind), headers=headers, json=body)
        assert result.status_code == expected, result.text
    assert database.question_grants.count_documents({}) == 0


def test_grant_management_and_new_read_endpoints_require_authentication_and_no_store(api, people):
    (_, headers), (reader, _), _ = people
    original = version(api, headers)
    target = bank(api, headers)
    for resource, kind in ((original, "version"), (target, "bank")):
        path = grant_path(resource, kind)
        for method, endpoint, body in (
            ("GET", path, None), ("POST", path, {"username": reader["username"]}),
            ("DELETE", path + "/" + reader["id"], None),
        ):
            response = api.request(method, endpoint, json=body)
            assert response.status_code == 401, response.text
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.headers["Cache-Control"] == "no-store"
    for path in ("/api/shared-questions", f"{QUESTIONS}/{original['id']}/usage"):
        assert api.get(path).status_code == 401
        assert api.get(path).headers["Cache-Control"] == "no-store"
        assert api.get(path, headers=headers).headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("target_kind", ["foreign", "missing", "malformed"])
def test_grants_and_usage_do_not_disclose_unowned_or_unknown_resource(api, database, people, target_kind):
    (_, headers), (reader, reader_headers), _ = people
    original = version(api, headers)
    target = bank(api, headers)
    grant(api, headers, original, reader)
    grant(api, headers, target, reader, "bank")
    for resource, prefix in ((original, QUESTIONS), (target, BANKS)):
        target_id = {"foreign": resource["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target_kind]
        path = f"{prefix}/{target_id}/grants"
        for method, endpoint, body in (
            ("GET", path, None), ("POST", path, {"username": reader["username"]}),
            ("DELETE", path + "/" + reader["id"], None),
        ):
            response = api.request(method, endpoint, headers=reader_headers, json=body)
            assert response.status_code == 404, response.text
    usage_id = {"foreign": original["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target_kind]
    assert api.get(f"{QUESTIONS}/{usage_id}/usage", headers=reader_headers).status_code == 404
    assert database.question_grants.count_documents({}) == 2


def test_shared_list_deduplicates_direct_and_multiple_bank_grants_and_excludes_owned_versions(api, people):
    (_, headers), (reader, reader_headers), _ = people
    first = version(api, headers)
    second = version(api, headers, {"type": "number", "title": "Second question"})
    version(api, reader_headers)
    targets = [bank(api, headers, f"Bank {index}") for index in range(2)]
    for target in targets:
        add_entry(api, headers, target, first)
        grant(api, headers, target, reader, "bank")
    add_entry(api, headers, targets[1], second)
    grant(api, headers, first, reader)
    shared = api.get("/api/shared-questions", headers=reader_headers)
    assert shared.status_code == 200, shared.text
    assert shared.json()["items"] == [second, first] and shared.json()["total"] == 2
    revoke(api, headers, first, reader)
    assert api.get(f"{QUESTIONS}/{first['id']}", headers=reader_headers).status_code == 200
    revoke(api, headers, targets[0], reader, "bank")
    assert api.get(f"{QUESTIONS}/{first['id']}", headers=reader_headers).status_code == 200
    revoke(api, headers, targets[1], reader, "bank")
    assert api.get("/api/shared-questions", headers=reader_headers).json()["items"] == []
    assert api.get(f"{QUESTIONS}/{first['id']}", headers=reader_headers).status_code == 404


def test_bank_grant_tracks_current_entries_and_direct_grant_survives_bank_update_and_archive(api, people):
    (_, headers), (reader, reader_headers), _ = people
    first = version(api, headers)
    newer = version(api, headers, {"type": "text", "title": "New bank version"}, parent=first)
    another = version(api, headers, {"type": "number", "title": "Later bank addition"})
    target = bank(api, headers)
    add_entry(api, headers, target, first)
    grant(api, headers, target, reader, "bank")
    assert api.get(f"{QUESTIONS}/{newer['id']}", headers=reader_headers).status_code == 404
    grant(api, headers, first, reader)
    path = f"{BANKS}/{target['id']}/entries/{first['family_id']}"
    updated = api.put(path, headers=headers, json={"version_id": newer["id"], "expected_version_id": first["id"]})
    assert updated.status_code == 200, updated.text
    add_entry(api, headers, target, another)
    assert api.get("/api/shared-questions", headers=reader_headers).json()["items"] == [another, newer, first]
    revoke(api, headers, first, reader)
    assert api.get(f"{QUESTIONS}/{first['id']}", headers=reader_headers).status_code == 404
    assert api.get(f"{QUESTIONS}/{newer['id']}", headers=reader_headers).json() == newer
    grant(api, headers, newer, reader)
    assert api.delete(f"{BANKS}/{target['id']}", headers=headers, params={"expected_revision": 1}).status_code == 204
    assert api.get("/api/shared-questions", headers=reader_headers).json()["items"] == [newer]
    assert api.get(f"{QUESTIONS}/{newer['id']}", headers=reader_headers).json() == newer
    assert api.get(f"{QUESTIONS}/{another['id']}", headers=reader_headers).status_code == 404


@pytest.mark.parametrize("content", [
    {"type": "single_choice", "title": "Shared single", "required": True, "options": [{"label": "A"}, {"label": "B"}]},
    {"type": "multiple_choice", "title": "Shared multiple", "options": [{"label": "A"}, {"label": "B"}], "max_selections": 1},
    {"type": "number", "title": "Shared integer", "integer_only": True, "min_value": 0, "max_value": 10},
    {"type": "text", "title": "Shared text", "required": True, "max_length": 20},
])
def test_grantee_selects_each_type_with_independent_local_identity_and_cannot_edit_source(api, database, people, content):
    (_, headers), (reader, reader_headers), _ = people
    original = version(api, headers, content)
    grant(api, headers, original, reader)
    first = select(api, reader_headers, survey(api, reader_headers), original)
    first = select(api, reader_headers, first, original)
    assert len({question["id"] for question in first["questions"]}) == 2
    assert len(first["question_sources"]) == 2
    for question, source in zip(first["questions"], first["question_sources"]):
        assert source["question_id"] == question["id"] and source["version_id"] == original["id"]
        assert source["family_id"] == original["family_id"]
        assert {option["id"] for option in question.get("options", [])}.isdisjoint(
            option["id"] for option in original["content"].get("options", []))
        assert len(source["option_map"]) == len(original["content"].get("options", []))
    assert database.question_versions.count_documents({}) == 1
    assert api.get(f"{QUESTIONS}/{original['id']}", headers=headers).json() == original


@pytest.mark.parametrize("already_published", [False, True])
def test_revocation_blocks_new_selection_but_existing_draft_and_published_snapshots_still_submit(api, database, people, already_published):
    (_, headers), (reader, reader_headers), (_, outsider_headers) = people
    original = version(api, headers, {"type": "single_choice", "title": "Frozen shared choice", "required": True,
                                      "options": [{"label": "A"}, {"label": "B"}]})
    grant(api, headers, original, reader)
    target = select(api, reader_headers, survey(api, reader_headers, allow_repeat=True), original)
    if already_published:
        result = api.post(f"/api/surveys/{target['id']}/publish", headers=reader_headers,
                          json={"expected_revision": target["revision"]})
        assert result.status_code == 200, result.text
        target = result.json()
    before = deepcopy(database.surveys.find_one({"_id": ObjectId(target["id"])}))
    revoke(api, headers, original, reader)
    assert api.get(f"{QUESTIONS}/{original['id']}", headers=reader_headers).status_code == 404
    fresh = survey(api, reader_headers)
    own_version = version(api, reader_headers, {"type": "text", "title": "Reader's own question"})
    fresh = select(api, reader_headers, fresh, own_version)
    fresh_before = deepcopy(database.surveys.find_one({"_id": ObjectId(fresh["id"])}))
    for method, suffix in (("POST", ""), ("PUT", "/" + fresh["questions"][0]["id"])):
        failed = api.request(method, f"/api/surveys/{fresh['id']}/question-selections" + suffix,
                             headers=reader_headers, params={"expected_revision": fresh["revision"]},
                             json={"version_id": original["id"]})
        assert failed.status_code == 404, failed.text
        assert database.surveys.find_one({"_id": fresh_before["_id"]}) == fresh_before
    assert database.surveys.find_one({"_id": before["_id"]}) == before
    if not already_published:
        result = api.post(f"/api/surveys/{target['id']}/publish", headers=reader_headers,
                          json={"expected_revision": target["revision"]})
        assert result.status_code == 200, result.text
        target = result.json()
    public = api.get(target["share_url"], headers=outsider_headers)
    assert public.status_code == 200 and public.json()["questions"] == target["questions"]
    assert "question_sources" not in public.json() and original["id"] not in public.text
    question = target["questions"][0]
    answers = {question["id"]: question["options"][1]["id"]}
    checked = api.post(target["share_url"] + "/validate", headers=outsider_headers,
                       json={"question_id": question["id"], "value": answers[question["id"]]})
    assert checked.status_code == 200, checked.text
    navigation = api.post(target["share_url"] + "/navigate", headers=outsider_headers,
                          json={"survey_revision": target["revision"], "answers": answers})
    assert navigation.status_code == 200 and navigation.json()["completed"], navigation.text
    submitted = api.post(target["share_url"] + "/responses", headers=outsider_headers,
                         json={"survey_revision": target["revision"], "request_id": uuid4().hex, "answers": answers})
    assert submitted.status_code == 201, submitted.text
    statistics = api.get(f"/api/surveys/{target['id']}/statistics", headers=reader_headers)
    assert statistics.status_code == 200 and statistics.json()["total_responses"] == 1
    assert statistics.json()["questions"][0]["options"][1]["count"] == 1
    assert usage(api, headers, original)["total_occurrences"] == 1


def test_shared_question_and_grant_lists_have_bounded_pagination(api, people):
    (_, headers), (reader, reader_headers), (outsider, _) = people
    questions = [version(api, headers, {"type": "text", "title": f"Shared {index}"}) for index in range(3)]
    for question in questions:
        grant(api, headers, question, reader)
    grant(api, headers, questions[0], outsider)
    page = api.get("/api/shared-questions", headers=reader_headers, params={"offset": 1, "limit": 1})
    assert page.json() == {"items": [questions[1]], "total": 3, "offset": 1, "limit": 1}
    for path, auth, total in (("/api/shared-questions", reader_headers, 3), (grant_path(questions[0]), headers, 2)):
        beyond = api.get(path, headers=auth, params={"offset": 100})
        assert beyond.json()["items"] == [] and beyond.json()["total"] == total
        for params in ({"offset": -1}, {"offset": 2147483648}, {"limit": 0}, {"limit": 101}):
            assert api.get(path, headers=auth, params=params).status_code == 422


def test_usage_groups_repeated_occurrences_and_hides_foreign_survey_metadata(api, people):
    (_, headers), (reader, reader_headers), _ = people
    first = version(api, headers)
    second = version(api, headers, {"type": "number", "title": "Same family newer"}, parent=first)
    grant(api, headers, first, reader)
    grant(api, headers, second, reader)
    own = select(api, headers, survey(api, headers, "Owner-visible survey"), first)
    foreign = select(api, reader_headers, survey(api, reader_headers, "SECRET FOREIGN TITLE"), first)
    foreign = select(api, reader_headers, foreign, first)
    foreign = select(api, reader_headers, foreign, second)
    unrelated = version(api, reader_headers, {"type": "text", "title": "SECRET UNRELATED QUESTION"})
    foreign = select(api, reader_headers, foreign, unrelated)
    result = usage(api, headers, first)
    assert set(result) == {"version_id", "family_id", "scope", "items", "total_surveys", "total_occurrences", "offset", "limit"}
    assert result["scope"] == "version" and result["version_id"] == first["id"]
    assert result["total_surveys"] == 2 and result["total_occurrences"] == 3
    by_id = {item["survey_id"]: item for item in result["items"]}
    assert by_id[own["id"]] == {
        "survey_id": own["id"], "title": own["title"], "status": "draft", "owned": True,
        "questions": [{"question_id": own["questions"][0]["id"], "version_id": first["id"], "version": 1}],
    }
    assert set(by_id[foreign["id"]]) == {"survey_id", "title", "status", "owned", "questions"}
    assert by_id[foreign["id"]]["title"] is None and by_id[foreign["id"]]["status"] is None
    assert by_id[foreign["id"]]["owned"] is False
    assert by_id[foreign["id"]]["questions"] == [
        {"question_id": question["id"], "version_id": first["id"], "version": 1}
        for question in foreign["questions"][:2]
    ]
    serialized = str(result)
    assert foreign["title"] not in serialized and unrelated["id"] not in serialized
    assert foreign["questions"][-1]["id"] not in serialized
    family = usage(api, headers, first, scope="family")
    assert family["total_surveys"] == 2 and family["total_occurrences"] == 4
    assert sorted(question["version"] for item in family["items"] for question in item["questions"]) == [1, 1, 1, 2]
    assert usage(api, headers, second)["total_surveys"] == usage(api, headers, second)["total_occurrences"] == 1
    revoke(api, headers, first, reader)
    assert usage(api, headers, first)["total_occurrences"] == 3
    assert api.get(f"{QUESTIONS}/{first['id']}/usage", headers=reader_headers).status_code == 404


def test_usage_updates_after_replacement_detach_and_delete_and_paginates_by_survey(api, people):
    (_, headers), _, _ = people
    first = version(api, headers)
    second = version(api, headers, {"type": "text", "title": "Second version"}, parent=first)
    targets = [select(api, headers, survey(api, headers, f"Survey {index}"), first) for index in range(3)]
    targets[0] = select(api, headers, targets[0], first)
    all_items = usage(api, headers, first)["items"]
    page = usage(api, headers, first, offset=1, limit=1)
    assert page["items"] == all_items[1:2] and page["total_surveys"] == 3 and page["total_occurrences"] == 4
    targets[0] = select(api, headers, targets[0], second, targets[0]["questions"][0]["id"])
    assert usage(api, headers, first)["total_occurrences"] == 3
    assert usage(api, headers, first, scope="family")["total_occurrences"] == 4
    local = targets[1]["questions"][0]
    detached = api.put(f"/api/surveys/{targets[1]['id']}/questions/{local['id']}", headers=headers,
                       params={"expected_revision": targets[1]["revision"]},
                       json={"type": "text", "title": "Independent local edit", "required": True})
    assert detached.status_code == 200, detached.text
    assert usage(api, headers, first)["total_surveys"] == 2
    removed = api.delete(f"/api/surveys/{targets[2]['id']}/questions/{targets[2]['questions'][0]['id']}",
                         headers=headers, params={"expected_revision": targets[2]["revision"]})
    assert removed.status_code == 200, removed.text
    assert usage(api, headers, first)["total_surveys"] == usage(api, headers, first)["total_occurrences"] == 1
    assert usage(api, headers, first, scope="family")["total_occurrences"] == 2
    beyond = usage(api, headers, first, offset=100)
    assert beyond["items"] == [] and beyond["total_surveys"] == 1 and beyond["total_occurrences"] == 1
    for params in ({"scope": "all"}, {"offset": -1}, {"offset": 2147483648}, {"limit": 0}, {"limit": 101}):
        assert api.get(f"{QUESTIONS}/{first['id']}/usage", headers=headers, params=params).status_code == 422


def test_concurrent_grant_requests_create_one_record_and_return_one_creation(api, database, people):
    (_, headers), (reader, _), _ = people
    original = version(api, headers)
    barrier = Barrier(4)

    def request(_):
        barrier.wait(timeout=10)
        return api.post(grant_path(original), headers=headers, json={"username": reader["username"]})

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(request, range(4)))
    assert sorted(result.status_code for result in results) == [200, 200, 200, 201], [result.text for result in results]
    assert len({result.json()["id"] for result in results}) == 1
    assert database.question_grants.count_documents({}) == 1


def test_grant_storage_rejects_invalid_resources_and_duplicate_authorizations(api, database, people):
    (_, headers), (reader, _), _ = people
    original = version(api, headers)
    granted = grant(api, headers, original, reader)
    stored = database.question_grants.find_one({"_id": ObjectId(granted["id"])})
    with pytest.raises(DuplicateKeyError):
        database.question_grants.insert_one({**deepcopy(stored), "_id": ObjectId()})
    for change in (
        {"resource_type": "family"}, {"resource_id": original["id"]},
        {"grantee_id": reader["id"]}, {"owner_id": granted["owner_id"]},
        {"unexpected": "metadata"}, {"schema_version": 2},
    ):
        invalid = {**deepcopy(stored), "_id": ObjectId(), "resource_id": ObjectId(), **change}
        with pytest.raises(WriteError) as rejected:
            database.question_grants.insert_one(invalid)
        assert rejected.value.code == 121
    assert database.question_grants.count_documents({}) == 1


@pytest.mark.parametrize("kind", ["version", "bank"])
def test_grant_with_mismatched_resource_owner_never_confers_read_or_selection(api, database, people, kind):
    (_, headers), (reader, reader_headers), (outsider, _) = people
    original = version(api, headers)
    target = original if kind == "version" else bank(api, headers)
    if kind == "bank":
        add_entry(api, headers, target, original)
    granted = grant(api, headers, target, reader, kind)
    # Simulate a structurally valid but inconsistent stored reference. Neither
    # an ObjectId nor a valid grant schema proves ownership across collections.
    database.question_grants.update_one({"_id": ObjectId(granted["id"])},
                                        {"$set": {"owner_id": ObjectId(outsider["id"])}})
    denied = api.get(f"{QUESTIONS}/{original['id']}", headers=reader_headers)
    assert denied.status_code == 404 and denied.headers["Cache-Control"] == "no-store"
    assert api.get("/api/shared-questions", headers=reader_headers).json()["items"] == []
    if kind == "bank":
        assert api.get(f"{BANKS}/{target['id']}", headers=reader_headers).status_code == 404
        assert api.get(BANKS, headers=reader_headers, params={"view": "shared"}).json()["items"] == []
    draft = survey(api, reader_headers)
    rejected = api.post(f"/api/surveys/{draft['id']}/question-selections", headers=reader_headers,
                        params={"expected_revision": draft["revision"]}, json={"version_id": original["id"]})
    assert rejected.status_code == 404, rejected.text


def test_orphan_bank_grant_and_entry_cannot_authorize_or_surface_a_question(api, database, people):
    (_, headers), (reader, reader_headers), _ = people
    original = version(api, headers)
    target = bank(api, headers)
    add_entry(api, headers, target, original)
    grant(api, headers, target, reader, "bank")
    database.question_banks.delete_one({"_id": ObjectId(target["id"])})
    assert database.question_bank_entries.count_documents({}) == database.question_grants.count_documents({}) == 1
    assert api.get(f"{QUESTIONS}/{original['id']}", headers=reader_headers).status_code == 404
    assert api.get("/api/shared-questions", headers=reader_headers).json()["items"] == []
    assert api.get(BANKS, headers=reader_headers, params={"view": "shared"}).json()["items"] == []


def test_cross_user_version_replacement_is_explicit_and_revoked_replacement_is_atomic(api, database, people):
    (_, headers), (reader, reader_headers), _ = people
    first = version(api, headers, {"type": "single_choice", "title": "First shared version",
                                   "options": [{"label": "Yes"}, {"label": "No"}]})
    grant(api, headers, first, reader)
    target = select(api, reader_headers, survey(api, reader_headers), first)
    local_id = target["questions"][0]["id"]
    second = version(api, headers, {"type": "number", "title": "New shared version", "min_value": 0}, parent=first)
    # Deriving a source version does not silently upgrade a selected snapshot.
    assert api.get(f"/api/surveys/{target['id']}", headers=reader_headers).json() == target
    grant(api, headers, second, reader)
    target = select(api, reader_headers, target, second, local_id)
    assert target["questions"][0]["id"] == local_id and target["questions"][0]["type"] == "number"
    assert target["question_sources"][0]["version_id"] == second["id"]
    assert target["question_sources"][0]["option_map"] == []
    assert usage(api, headers, first)["total_occurrences"] == 0
    assert usage(api, headers, first, scope="family")["total_occurrences"] == 1
    revoke(api, headers, second, reader)
    before = deepcopy(database.surveys.find_one({"_id": ObjectId(target["id"])}))
    denied = api.put(f"/api/surveys/{target['id']}/question-selections/{local_id}", headers=reader_headers,
                     params={"expected_revision": target["revision"]}, json={"version_id": second["id"]})
    assert denied.status_code == 404, denied.text
    assert database.surveys.find_one({"_id": before["_id"]}) == before
    # A different grant remains usable; one revocation does not block the owner
    # from deliberately selecting another still-authorized saved version.
    reverted = select(api, reader_headers, target, first, local_id)
    assert reverted["question_sources"][0]["version_id"] == first["id"]
