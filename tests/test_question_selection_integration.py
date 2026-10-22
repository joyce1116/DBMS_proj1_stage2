"""Question-version selection keeps phase-one snapshots, rules and answers local."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
from threading import Barrier
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.surveys.repository import SurveyRepository


pytestmark = pytest.mark.integration


def actor(api, username="Selection_Owner"):
    credentials = {"username": username, "password": "Selection_Password_123"}
    created = api.post("/api/auth/register", json=credentials)
    assert created.status_code == 201, created.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return created.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def owner(api):
    return actor(api)


def create_survey(api, headers, **fields):
    result = api.post("/api/surveys", headers=headers, json={"title": "Selection survey", **fields})
    assert result.status_code == 201, result.text
    return result.json()


def create_version(api, headers, content=None):
    content = content or {"type": "single_choice", "title": "Reusable choice", "required": True,
                          "options": [{"label": "A"}, {"label": "B"}]}
    result = api.post("/api/questions", headers=headers, json=content)
    assert result.status_code == 201, result.text
    return result.json()


def derive(api, headers, previous, content):
    result = api.post(f"/api/questions/{previous['id']}/versions", headers=headers, json=content)
    assert result.status_code == 201, result.text
    return result.json()


def selection_url(survey, question_id=None):
    base = f"/api/surveys/{survey['id']}/question-selections"
    return base if question_id is None else base + "/" + question_id


def select(api, headers, survey, version):
    result = api.post(selection_url(survey), headers=headers,
                      params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
    assert result.status_code == 201, result.text
    return result.json()


def replace_selection(api, headers, survey, question_id, version):
    result = api.put(selection_url(survey, question_id), headers=headers,
                     params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
    assert result.status_code == 200, result.text
    return result.json()


def stored(database, survey):
    return database.surveys.find_one({"_id": ObjectId(survey["id"])})


def sources_by_question(survey):
    return {source["question_id"]: source for source in survey["question_sources"]}


def local_input(question):
    return {key: deepcopy(value) for key, value in question.items() if key != "id"}


def assert_source(question, source, version):
    assert set(source) == {"question_id", "family_id", "version_id", "version", "option_map"}
    assert source["question_id"] == question["id"]
    assert source["family_id"] == version["family_id"]
    assert source["version_id"] == version["id"] and source["version"] == version["version"]
    assert re.fullmatch(r"q_[a-f0-9]{32}", question["id"])
    expected_map = [{"local_id": local["id"], "source_id": original["id"]}
                    for local, original in zip(question.get("options", []), version["content"].get("options", []))]
    assert source["option_map"] == expected_map
    assert {option["id"] for option in question.get("options", [])}.isdisjoint(
        option["id"] for option in version["content"].get("options", []))
    content = local_input(question)
    for option, original in zip(content.get("options", []), version["content"].get("options", [])):
        assert re.fullmatch(r"opt_[a-f0-9]{32}", option["id"])
        option["id"] = original["id"]
    assert content == version["content"]


@pytest.mark.parametrize("content", [
    {"type": "single_choice", "title": "Single", "required": True,
     "options": [{"label": "A"}, {"label": "B"}]},
    {"type": "multiple_choice", "title": "Multiple", "required": True,
     "options": [{"label": "A"}, {"label": "B"}], "min_selections": 1, "max_selections": 2},
    {"type": "text", "title": "Text", "min_length": 2, "max_length": 20},
    {"type": "number", "title": "Number", "integer_only": True, "min_value": 0, "max_value": 9},
])
def test_selects_four_types_as_local_snapshots_with_typed_database_sources(api, database, owner, content):
    _, headers = owner
    version = create_version(api, headers, content)
    original = database.question_versions.find_one({"_id": ObjectId(version["id"])})
    survey = create_survey(api, headers)
    assert survey["question_sources"] == []
    selected = select(api, headers, survey, version)
    assert selected["revision"] == survey["revision"] + 1 and selected["question_count"] == 1
    assert_source(selected["questions"][0], selected["question_sources"][0], version)
    document = stored(database, survey)
    assert document["questions"] == selected["questions"]
    source = document["question_sources"][0]
    assert source == {**selected["question_sources"][0], "family_id": ObjectId(version["family_id"]),
                      "version_id": ObjectId(version["id"])}
    assert database.question_versions.find_one({"_id": original["_id"]}) == original
    assert api.get(f"/api/surveys/{survey['id']}", headers=headers).json() == selected


def test_same_version_can_be_reused_in_multiple_surveys_and_twice_in_one_survey(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    first = select(api, headers, create_survey(api, headers), version)
    second = select(api, headers, create_survey(api, headers), version)
    first = select(api, headers, first, version)
    questions = [*first["questions"], *second["questions"]]
    sources = [*first["question_sources"], *second["question_sources"]]
    assert len({question["id"] for question in questions}) == 3
    all_options = [option["id"] for question in questions for option in question["options"]]
    assert len(all_options) == len(set(all_options)) == 6
    assert len({source["version_id"] for source in sources}) == 1
    for question, source in zip(questions, sources):
        assert_source(question, source, version)
    assert database.question_versions.count_documents({}) == 1


def test_versions_coexist_and_new_versions_or_restores_never_refresh_snapshots(api, database, owner, mongo_settings):
    _, headers = owner
    original = create_version(api, headers)
    first = select(api, headers, create_survey(api, headers), original)
    revised_content = deepcopy(original["content"])
    revised_content["title"] = "Revised independently"
    revised_content["options"][0]["label"] = "Changed A"
    revised = derive(api, headers, original, revised_content)
    second = select(api, headers, create_survey(api, headers), revised)
    first = select(api, headers, first, revised)
    before = list(database.surveys.find({}).sort("_id", 1))
    restored = api.post(f"/api/questions/{original['id']}/restore", headers=headers)
    assert restored.status_code == 201, restored.text
    derive(api, headers, revised, {"type": "text", "title": "A later version"})
    assert list(database.surveys.find({}).sort("_id", 1)) == before
    assert [source["version"] for source in first["question_sources"]] == [1, 2]
    assert_source(first["questions"][0], first["question_sources"][0], original)
    assert_source(first["questions"][1], first["question_sources"][1], revised)
    assert_source(second["questions"][0], second["question_sources"][0], revised)
    with TestClient(create_app(mongo_settings)) as restarted:
        assert restarted.get(f"/api/surveys/{first['id']}", headers=headers).json() == first
        assert restarted.get(f"/api/surveys/{second['id']}", headers=headers).json() == second
    assert list(database.surveys.find({}).sort("_id", 1)) == before


def test_explicit_same_family_replacement_preserves_question_and_retained_option_identity(api, database, owner):
    _, headers = owner
    first = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), first)
    previous = survey["questions"][0]
    second = derive(api, headers, first, {
        "type": "multiple_choice", "title": "New version", "max_selections": 2,
        "options": [{"label": "C"}, {**first["content"]["options"][0], "label": "Edited A"}],
    })
    updated = replace_selection(api, headers, survey, previous["id"], second)
    question, source = updated["questions"][0], updated["question_sources"][0]
    assert question["id"] == previous["id"]
    assert question["options"][1]["id"] == previous["options"][0]["id"]
    assert question["options"][0]["id"] not in {option["id"] for option in previous["options"]}
    assert updated["revision"] == survey["revision"] + 1
    assert_source(question, source, second)
    again = replace_selection(api, headers, updated, question["id"], second)
    assert again["questions"] == updated["questions"] and again["question_sources"] == updated["question_sources"]
    assert stored(database, survey)["questions"] == again["questions"]


def test_different_family_replacement_and_adopting_manual_question_generate_fresh_option_ids(api, owner):
    _, headers = owner
    original = create_version(api, headers)
    other = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), original)
    previous = survey["questions"][0]
    updated = replace_selection(api, headers, survey, previous["id"], other)
    assert updated["questions"][0]["id"] == previous["id"]
    assert {option["id"] for option in updated["questions"][0]["options"]}.isdisjoint(
        option["id"] for option in previous["options"])
    assert_source(updated["questions"][0], updated["question_sources"][0], other)
    manual = api.post(f"/api/surveys/{updated['id']}/questions", headers=headers,
                      params={"expected_revision": updated["revision"]},
                      json={"type": "text", "title": "Existing manual question"})
    assert manual.status_code == 201, manual.text
    updated = manual.json()
    manual_id = updated["questions"][-1]["id"]
    updated = replace_selection(api, headers, updated, manual_id, original)
    assert_source(updated["questions"][-1], sources_by_question(updated)[manual_id], original)


def test_replacement_preserves_local_jump_rules_when_the_source_option_survives(api, database, owner):
    _, headers = owner
    first = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), first)
    previous = survey["questions"][0]
    rules = [{"kind": "choice", "source_id": previous["id"], "target_id": "end", "operator": "eq",
              "option_ids": [previous["options"][0]["id"]]}]
    response = api.put(f"/api/surveys/{survey['id']}/rules", headers=headers,
                       json={"expected_revision": survey["revision"], "rules": rules})
    assert response.status_code == 200, response.text
    survey = response.json()
    revised = derive(api, headers, first, {
        "type": "single_choice", "title": "Renamed source", "required": True,
        "options": [{**first["content"]["options"][0], "label": "Renamed A"}, {"label": "C"}],
    })
    updated = replace_selection(api, headers, survey, previous["id"], revised)
    assert updated["jump_rules"] == rules
    assert updated["questions"][0]["options"][0]["id"] == previous["options"][0]["id"]
    assert stored(database, survey)["jump_rules"] == rules


@pytest.mark.parametrize("change", ["removed_option", "changed_type", "different_family"])
def test_replacement_that_breaks_a_rule_is_rejected_without_partial_snapshot_or_source_write(api, database, owner, change):
    _, headers = owner
    first = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), first)
    previous = survey["questions"][0]
    response = api.put(f"/api/surveys/{survey['id']}/rules", headers=headers,
                       json={"expected_revision": survey["revision"], "rules": [
                           {"kind": "choice", "source_id": previous["id"], "target_id": "end",
                            "operator": "eq", "option_ids": [previous["options"][0]["id"]]},
                       ]})
    assert response.status_code == 200, response.text
    survey = response.json()
    if change == "different_family":
        changed = create_version(api, headers)
    else:
        content = {"type": "text", "title": "Changed type"} if change == "changed_type" else {
            "type": "single_choice", "title": "Removed rule option",
            "options": [first["content"]["options"][1], {"label": "C"}],
        }
        changed = derive(api, headers, first, content)
    before = stored(database, survey)
    rejected = api.put(selection_url(survey, previous["id"]), headers=headers,
                       params={"expected_revision": survey["revision"]}, json={"version_id": changed["id"]})
    assert rejected.status_code == 422, rejected.text
    assert stored(database, survey) == before


def test_direct_edit_preserves_source_after_normalized_noop_and_detaches_real_changes(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    previous = survey["questions"][0]
    body = local_input(previous)
    body["title"] = "  " + body["title"] + "  "
    body["options"][0]["label"] = "  " + body["options"][0]["label"] + "  "
    result = api.put(f"/api/surveys/{survey['id']}/questions/{previous['id']}", headers=headers,
                     params={"expected_revision": survey["revision"]}, json=body)
    assert result.status_code == 200, result.text
    unchanged = result.json()
    assert unchanged["questions"] == survey["questions"]
    assert unchanged["question_sources"] == survey["question_sources"]
    body["title"] = "Only this survey changes"
    result = api.put(f"/api/surveys/{survey['id']}/questions/{previous['id']}", headers=headers,
                     params={"expected_revision": unchanged["revision"]}, json=body)
    assert result.status_code == 200, result.text
    detached = result.json()
    assert detached["questions"][0]["id"] == previous["id"]
    assert detached["questions"][0]["options"] == previous["options"]
    assert detached["question_sources"] == [] and stored(database, survey)["question_sources"] == []
    assert api.get(f"/api/questions/{version['id']}", headers=headers).json() == version
    reselected = replace_selection(api, headers, detached, previous["id"], version)
    assert_source(reselected["questions"][0], reselected["question_sources"][0], version)


def test_reorder_and_delete_keep_source_links_attached_to_local_question_identity(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    survey = select(api, headers, survey, version)
    manual = api.post(f"/api/surveys/{survey['id']}/questions", headers=headers,
                      params={"expected_revision": survey["revision"]}, json={"type": "text", "title": "Manual"})
    assert manual.status_code == 201, manual.text
    survey = manual.json()
    links = sources_by_question(survey)
    ids = [question["id"] for question in survey["questions"]]
    reordered = api.put(f"/api/surveys/{survey['id']}/question-order", headers=headers,
                        params={"expected_revision": survey["revision"]}, json={"question_ids": ids[::-1]})
    assert reordered.status_code == 200, reordered.text
    survey = reordered.json()
    assert sources_by_question(survey) == links
    deleted = api.delete(f"/api/surveys/{survey['id']}/questions/{ids[0]}", headers=headers,
                         params={"expected_revision": survey["revision"]})
    assert deleted.status_code == 200, deleted.text
    survey = deleted.json()
    assert [question["id"] for question in survey["questions"]] == ids[:0:-1]
    assert sources_by_question(survey) == {ids[1]: links[ids[1]]}
    deleted = api.delete(f"/api/surveys/{survey['id']}/questions/{ids[2]}", headers=headers,
                         params={"expected_revision": survey["revision"]})
    assert deleted.status_code == 200, deleted.text
    assert sources_by_question(deleted.json()) == {ids[1]: links[ids[1]]}
    assert len(stored(database, survey)["question_sources"]) == 1


def test_invalid_legacy_edits_deletes_and_reordering_preserve_sources_with_rules(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    text_version = create_version(api, headers, {"type": "text", "title": "Jump target"})
    survey = select(api, headers, create_survey(api, headers), version)
    survey = select(api, headers, survey, text_version)
    source, target = survey["questions"]
    configured = api.put(f"/api/surveys/{survey['id']}/rules", headers=headers,
                         json={"expected_revision": survey["revision"], "rules": [
                             {"kind": "choice", "source_id": source["id"], "target_id": target["id"],
                              "operator": "eq", "option_ids": [source["options"][0]["id"]]},
                         ]})
    assert configured.status_code == 200, configured.text
    survey = configured.json()
    before = stored(database, survey)
    base = f"/api/surveys/{survey['id']}"
    for method, path, body in (
        ("PUT", base + "/questions/" + source["id"], {"type": "text", "title": "Would detach source"}),
        ("DELETE", base + "/questions/" + source["id"], None),
        ("DELETE", base + "/questions/" + target["id"], None),
        ("PUT", base + "/question-order", {"question_ids": [target["id"], source["id"]]}),
    ):
        result = api.request(method, path, headers=headers,
                             params={"expected_revision": survey["revision"]}, json=body)
        assert result.status_code == 422, result.text
        assert stored(database, survey) == before
    cleared = api.put(base + "/rules", headers=headers,
                      json={"expected_revision": survey["revision"], "rules": []})
    assert cleared.status_code == 200, cleared.text
    removed = api.delete(base + "/questions/" + target["id"], headers=headers,
                         params={"expected_revision": cleared.json()["revision"]})
    assert removed.status_code == 200, removed.text
    assert sources_by_question(removed.json()) == {source["id"]: sources_by_question(survey)[source["id"]]}


def test_selection_obeys_existing_hundred_question_limit_but_can_replace_at_capacity(api, database, owner):
    _, headers = owner
    version = create_version(api, headers, {"type": "text", "title": "At capacity"})
    survey = create_survey(api, headers)
    # Seed 99 valid old-style snapshots to exercise the boundary without 99 HTTP calls.
    questions = [{**deepcopy(version["content"]), "id": "q_" + uuid4().hex} for _ in range(99)]
    database.surveys.update_one({"_id": ObjectId(survey["id"])}, {"$set": {"questions": questions}})
    survey = select(api, headers, survey, version)
    assert survey["question_count"] == len(survey["questions"]) == 100
    assert len(survey["question_sources"]) == 1
    before = stored(database, survey)
    rejected = api.post(selection_url(survey), headers=headers,
                        params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
    assert rejected.status_code == 422, rejected.text
    assert stored(database, survey) == before
    updated = replace_selection(api, headers, survey, questions[0]["id"], version)
    assert updated["question_count"] == 100 and len(updated["question_sources"]) == 2
    assert_source(updated["questions"][0], sources_by_question(updated)[questions[0]["id"]], version)


@pytest.mark.parametrize("fault", ["dangling_question", "duplicate_question", "missing_option",
                                   "duplicate_source_option", "duplicate_local_option", "string_family"])
def test_corrupt_stored_provenance_cannot_be_saved_or_published(api, database, owner, fault):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    sources = deepcopy(stored(database, survey)["question_sources"])
    if fault == "dangling_question":
        sources[0]["question_id"] = "q_" + uuid4().hex
    elif fault == "duplicate_question":
        sources.append(deepcopy(sources[0]))
    elif fault == "missing_option":
        sources[0]["option_map"].pop()
    elif fault == "duplicate_source_option":
        sources[0]["option_map"][1]["source_id"] = sources[0]["option_map"][0]["source_id"]
    elif fault == "duplicate_local_option":
        sources[0]["option_map"][1]["local_id"] = sources[0]["option_map"][0]["local_id"]
    else:
        sources[0]["family_id"] = str(sources[0]["family_id"])
    # Fault injection deliberately bypasses storage validation to verify the
    # application boundary also rejects inconsistent provenance before writes.
    database.surveys.update_one({"_id": ObjectId(survey["id"])}, {"$set": {"question_sources": sources}},
                                bypass_document_validation=True)
    before = stored(database, survey)
    base = f"/api/surveys/{survey['id']}"
    for method, path, body in (
        ("PATCH", base, {"expected_revision": survey["revision"], "title": "Would save broken sources"}),
        ("POST", base + "/publish", {"expected_revision": survey["revision"]}),
    ):
        result = api.request(method, path, headers=headers, json=body)
        assert result.status_code == 422, result.text
        assert stored(database, survey) == before


@pytest.mark.parametrize("method", ["POST", "PUT"])
def test_selection_endpoints_require_login(api, database, owner, method):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    before = stored(database, survey)
    question_id = survey["questions"][0]["id"] if method == "PUT" else None
    result = api.request(method, selection_url(survey, question_id),
                         params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
    assert result.status_code == 401 and result.headers["WWW-Authenticate"] == "Bearer"
    assert stored(database, survey) == before


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_selection_never_discloses_other_owners_missing_or_malformed_surveys(api, database, owner, target):
    _, alice = owner
    version = create_version(api, alice)
    survey = select(api, alice, create_survey(api, alice), version)
    _, bob = actor(api, "Selection_Bob")
    bobs_version = create_version(api, bob)
    survey_id = {"foreign": survey["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target]
    inaccessible = {**survey, "id": survey_id}
    before = stored(database, survey)
    errors = []
    for method, question_id in (("POST", None), ("PUT", survey["questions"][0]["id"])):
        result = api.request(method, selection_url(inaccessible, question_id), headers=bob,
                             params={"expected_revision": survey["revision"]}, json={"version_id": bobs_version["id"]})
        assert result.status_code == 404, result.text
        errors.append(result.json())
    assert errors[0] == errors[1]
    assert stored(database, survey) == before


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_selection_rejects_unowned_missing_or_malformed_versions_without_changing_owned_survey(api, database, owner, target):
    _, alice = owner
    version = create_version(api, alice)
    _, bob = actor(api, "Selection_Bob")
    bobs_version = create_version(api, bob)
    survey = select(api, bob, create_survey(api, bob), bobs_version)
    before = stored(database, survey)
    version_id = {"foreign": version["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target]
    errors = []
    for method, question_id in (("POST", None), ("PUT", survey["questions"][0]["id"])):
        result = api.request(method, selection_url(survey, question_id), headers=bob,
                             params={"expected_revision": survey["revision"]}, json={"version_id": version_id})
        assert result.status_code == 404, result.text
        errors.append(result.json())
    assert errors[0] == errors[1]
    assert stored(database, survey) == before


def test_replacement_requires_a_question_in_the_owned_survey(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    other = select(api, headers, create_survey(api, headers), version)
    before = stored(database, survey)
    for question_id in (other["questions"][0]["id"], "q_" + "f" * 32, "bad-id"):
        result = api.put(selection_url(survey, question_id), headers=headers,
                         params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
        assert result.status_code == 404, result.text
    assert stored(database, survey) == before


@pytest.mark.parametrize("body", [None, {}, {"version_id": None}, {"version_id": 3},
    {"version_id": "placeholder", "question_id": "q_" + "a" * 32},
    {"version_id": "placeholder", "source": {"owner_id": "forged"}}])
def test_selection_request_contract_rejects_missing_wrong_type_and_client_metadata(api, database, owner, body):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    before = stored(database, survey)
    body = deepcopy(body)
    if body and body.get("version_id") == "placeholder":
        body["version_id"] = version["id"]
    for method, question_id in (("POST", None), ("PUT", survey["questions"][0]["id"])):
        result = api.request(method, selection_url(survey, question_id), headers=headers,
                             params={"expected_revision": survey["revision"]}, json=body)
        assert result.status_code == 422, result.text
    assert stored(database, survey) == before


def test_selection_requires_current_bounded_revision_and_never_partially_writes(api, database, owner):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    before = stored(database, survey)
    for method, question_id in (("POST", None), ("PUT", survey["questions"][0]["id"])):
        for revision, expected in ((survey["revision"] - 1, 409), (0, 422), (2147483648, 422), (None, 422)):
            params = {} if revision is None else {"expected_revision": revision}
            result = api.request(method, selection_url(survey, question_id), headers=headers,
                                 params=params, json={"version_id": version["id"]})
            assert result.status_code == expected, result.text
    assert stored(database, survey) == before


@pytest.mark.parametrize("replacement", [False, True])
def test_concurrent_selections_commit_snapshot_and_source_in_one_revision(api, database, owner, monkeypatch, replacement):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    next_version = derive(api, headers, version, {"type": "text", "title": "Concurrent replacement"})
    barrier = Barrier(2)
    update = SurveyRepository.update_draft

    def synchronize(repository, *args):
        barrier.wait(timeout=10)
        return update(repository, *args)

    def request(index):
        if replacement and index == 0:
            return api.put(selection_url(survey, survey["questions"][0]["id"]), headers=headers,
                           params={"expected_revision": survey["revision"]}, json={"version_id": next_version["id"]})
        return api.post(selection_url(survey), headers=headers,
                        params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})

    with monkeypatch.context() as patch:
        patch.setattr(SurveyRepository, "update_draft", synchronize)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(request, range(2)))
    successes = [result for result in results if result.status_code in (200, 201)]
    assert len(successes) == 1 and sum(result.status_code == 409 for result in results) == 1, [r.text for r in results]
    current = api.get(f"/api/surveys/{survey['id']}", headers=headers).json()
    assert current == successes[0].json() and current["revision"] == survey["revision"] + 1
    assert len(current["questions"]) == len(current["question_sources"])
    versions = {version["id"]: version, next_version["id"]: next_version}
    for question in current["questions"]:
        source = sources_by_question(current)[question["id"]]
        assert_source(question, source, versions[source["version_id"]])
    assert stored(database, survey)["questions"] == current["questions"]


@pytest.mark.parametrize("state", ["published", "closed"])
def test_selected_questions_and_sources_freeze_after_publication_and_closing(api, database, owner, state):
    _, headers = owner
    version = create_version(api, headers)
    survey = select(api, headers, create_survey(api, headers), version)
    result = api.post(f"/api/surveys/{survey['id']}/publish", headers=headers,
                      json={"expected_revision": survey["revision"]})
    assert result.status_code == 200, result.text
    survey = result.json()
    if state == "closed":
        result = api.post(f"/api/surveys/{survey['id']}/close", headers=headers,
                          json={"expected_revision": survey["revision"]})
        assert result.status_code == 200, result.text
        survey = result.json()
    before = stored(database, survey)
    for method, question_id in (("POST", None), ("PUT", survey["questions"][0]["id"])):
        result = api.request(method, selection_url(survey, question_id), headers=headers,
                             params={"expected_revision": survey["revision"]}, json={"version_id": version["id"]})
        assert result.status_code == 409, result.text
    content = deepcopy(version["content"])
    content["title"] = "Version changed after publication"
    derive(api, headers, version, content)
    assert api.post(f"/api/questions/{version['id']}/restore", headers=headers).status_code == 201
    assert stored(database, survey) == before


def test_selected_snapshots_keep_local_validation_navigation_submission_and_statistics(api, database, owner):
    _, headers = owner
    _, reader = actor(api, "Selection_Reader")
    versions = [
        create_version(api, headers),
        create_version(api, headers, {"type": "multiple_choice", "title": "Multiple", "required": True,
                                     "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
                                     "min_selections": 2, "max_selections": 2}),
        create_version(api, headers, {"type": "number", "title": "Age", "required": True,
                                     "min_value": 0, "max_value": 120, "integer_only": True}),
        create_version(api, headers, {"type": "text", "title": "Comment", "required": True,
                                     "min_length": 2, "max_length": 20}),
    ]
    survey = create_survey(api, headers, allow_repeat=True)
    for version in versions:
        survey = select(api, headers, survey, version)
    single, multi, number, text = survey["questions"]
    rules = [
        {"kind": "choice", "source_id": single["id"], "target_id": number["id"],
         "operator": "eq", "option_ids": [single["options"][1]["id"]]},
        {"kind": "number", "source_id": number["id"], "target_id": "end", "operator": "lt", "value": 18},
    ]
    # A source-library option has the right syntax but is not a local rule option.
    invalid_rules = deepcopy(rules)
    invalid_rules[0]["option_ids"] = [versions[0]["content"]["options"][1]["id"]]
    before = stored(database, survey)
    rejected = api.put(f"/api/surveys/{survey['id']}/rules", headers=headers,
                       json={"expected_revision": survey["revision"], "rules": invalid_rules})
    assert rejected.status_code == 422, rejected.text
    assert stored(database, survey) == before
    configured = api.put(f"/api/surveys/{survey['id']}/rules", headers=headers,
                         json={"expected_revision": survey["revision"], "rules": rules})
    assert configured.status_code == 200, configured.text
    survey = configured.json()
    published = api.post(f"/api/surveys/{survey['id']}/publish", headers=headers,
                         json={"expected_revision": survey["revision"]})
    assert published.status_code == 200, published.text
    survey = published.json()
    shared = api.get(survey["share_url"], headers=reader)
    assert shared.status_code == 200, shared.text
    assert shared.json()["questions"] == survey["questions"]
    assert "question_sources" not in shared.json()
    for version in versions:
        assert version["id"] not in shared.text and version["family_id"] not in shared.text
    checked = api.post(survey["share_url"] + "/validate", headers=reader,
                       json={"question_id": single["id"], "value": single["options"][0]["id"]})
    assert checked.status_code == 200, checked.text
    rejected = api.post(survey["share_url"] + "/validate", headers=reader,
                        json={"question_id": single["id"], "value": versions[0]["content"]["options"][0]["id"]})
    assert rejected.status_code == 422, rejected.text
    full = {single["id"]: single["options"][0]["id"],
            multi["id"]: [option["id"] for option in multi["options"][:2]][::-1],
            number["id"]: 20, text["id"]: "Useful comment"}
    short = {single["id"]: single["options"][1]["id"], number["id"]: 10}
    for answers in (full, short):
        preview = api.post(survey["share_url"] + "/navigate", headers=reader,
                           json={"survey_revision": survey["revision"], "answers": answers})
        assert preview.status_code == 200 and preview.json()["completed"], preview.text
        expected_path = [question["id"] for question in survey["questions"]] if answers is full else [single["id"], number["id"]]
        assert preview.json()["path"] == expected_path
        submitted = api.post(survey["share_url"] + "/responses", headers=reader,
                             json={"survey_revision": survey["revision"], "request_id": uuid4().hex, "answers": answers})
        assert submitted.status_code == 201 and submitted.json()["path"] == expected_path, submitted.text
    base = f"/api/surveys/{survey['id']}"
    result = api.get(base + "/statistics", headers=headers)
    assert result.status_code == 200, result.text
    statistics = result.json()
    assert statistics["total_responses"] == 2
    single_stats, multi_stats, number_stats, text_stats = statistics["questions"]
    assert [item["question_id"] for item in statistics["questions"]] == [question["id"] for question in survey["questions"]]
    assert [option["option_id"] for option in single_stats["options"]] == [option["id"] for option in single["options"]]
    assert [option["count"] for option in single_stats["options"]] == [1, 1]
    assert [option["count"] for option in multi_stats["options"]] == [1, 1, 0]
    assert multi_stats["visited_count"] == multi_stats["answer_count"] == 1
    assert number_stats["average"] == 15
    assert text_stats["visited_count"] == text_stats["answer_count"] == 1
    detail = api.get(base + "/statistics/" + text["id"], headers=headers).json()
    assert [item["value"] for item in detail["contents"]] == ["Useful comment"]
    submissions = list(database.submissions.find({"survey_id": ObjectId(survey["id"])}))
    assert len(submissions) == 2
    local_question_ids = {question["id"] for question in survey["questions"]}
    for submission in submissions:
        assert set(submission["path"]) <= local_question_ids
        assert {answer["question_id"] for answer in submission["answers"]} <= local_question_ids
    assert stored(database, survey)["question_sources"] == before["question_sources"]
