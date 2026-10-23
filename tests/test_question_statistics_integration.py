"""Cross-survey reporting over saved phase-one submissions in real MongoDB."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.responses.repository import ResponseRepository
from app.core.time import mongo_utc_now


pytestmark = pytest.mark.integration
QUESTIONS = "/api/questions"


def actor(api, username):
    credentials = {"username": username, "password": "Statistics_Password_123"}
    registered = api.post("/api/auth/register", json=credentials)
    assert registered.status_code == 201, registered.text
    logged_in = api.post("/api/auth/login", json=credentials)
    assert logged_in.status_code == 200, logged_in.text
    return registered.json(), {"Authorization": "Bearer " + logged_in.json()["access_token"]}


@pytest.fixture
def people(api):
    return actor(api, "Statistics_Owner"), actor(api, "Statistics_Reader"), actor(api, "Statistics_Outsider")


def version(api, headers, content=None, parent=None):
    path = QUESTIONS if parent is None else f"{QUESTIONS}/{parent['id']}/versions"
    result = api.post(path, headers=headers, json=content or {"type": "text", "title": "Reusable text"})
    assert result.status_code == 201, result.text
    return result.json()


def survey(api, headers, title="Cross-survey reporting", **settings):
    result = api.post("/api/surveys", headers=headers, json={"title": title, **settings})
    assert result.status_code == 201, result.text
    return result.json()


def select(api, headers, target, question):
    result = api.post(f"/api/surveys/{target['id']}/question-selections", headers=headers,
                      params={"expected_revision": target["revision"]}, json={"version_id": question["id"]})
    assert result.status_code == 201, result.text
    return result.json()


def add_local(api, headers, target, content):
    result = api.post(f"/api/surveys/{target['id']}/questions", headers=headers,
                      params={"expected_revision": target["revision"]}, json=content)
    assert result.status_code == 201, result.text
    return result.json()


def publish(api, headers, target):
    result = api.post(f"/api/surveys/{target['id']}/publish", headers=headers,
                      json={"expected_revision": target["revision"]})
    assert result.status_code == 200, result.text
    return result.json()


def rules(api, headers, target, configured):
    result = api.put(f"/api/surveys/{target['id']}/rules", headers=headers,
                     json={"expected_revision": target["revision"], "rules": configured})
    assert result.status_code == 200, result.text
    return result.json()


def payload(target, answers, anonymous=False):
    return {"survey_revision": target["revision"], "request_id": uuid4().hex,
            "answers": answers, "anonymous": anonymous}


def submit(api, headers, target, answers, anonymous=False):
    result = api.post(target["share_url"] + "/responses", headers=headers,
                      json=payload(target, answers, anonymous))
    assert result.status_code == 201, result.text
    return result.json()


def grant(api, headers, question, recipient):
    result = api.post(f"{QUESTIONS}/{question['id']}/grants", headers=headers,
                      json={"username": recipient["username"]})
    assert result.status_code == 201, result.text


def revoke(api, headers, question, recipient):
    result = api.delete(f"{QUESTIONS}/{question['id']}/grants/{recipient['id']}", headers=headers)
    assert result.status_code == 204, result.text


def endpoint(question, suffix=""):
    return f"{QUESTIONS}/{question['id']}/statistics" + suffix


def report(api, headers, question, **params):
    result = api.get(endpoint(question), headers=headers, params=params)
    assert result.status_code == 200, result.text
    assert result.headers["Cache-Control"] == "no-store"
    return result.json()


def family(api, headers, question, **params):
    result = api.get(endpoint(question, "/family"), headers=headers, params=params)
    assert result.status_code == 200, result.text
    return result.json()


def contents(api, headers, question, **params):
    result = api.get(endpoint(question, "/contents"), headers=headers, params=params)
    assert result.status_code == 200, result.text
    return result.json()


def assert_counts(summary, *, surveys=0, occurrences=0, responses=0, visited=0, answered=0):
    assert summary["survey_scope"] == "owned"
    assert (summary["survey_count"], summary["question_occurrence_count"], summary["total_responses"],
            summary["visited_count"], summary["answer_count"]) == (surveys, occurrences, responses, visited, answered)


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "number", "text"])
def test_unused_version_has_complete_zero_statistics_and_empty_contents(api, people, kind):
    (_, headers), _, _ = people
    content = {"type": kind, "title": "Never selected"}
    if kind.endswith("choice"):
        content["options"] = [{"label": "A"}, {"label": "B"}]
    question = version(api, headers, content)
    result = report(api, headers, question)
    assert_counts(result)
    assert result["version_id"] == question["id"] and result["family_id"] == question["family_id"]
    assert (result["version"], result["title"], result["type"]) == (1, content["title"], kind)
    assert result["average"] is result["min_value"] is result["max_value"] is None
    assert result["distribution"] == [] and result["distribution_total"] == 0
    assert result["offset"] == 0 and result["limit"] == 20
    assert result["options"] == [{"option_id": option["id"], "label": option["label"], "count": 0}
                                 for option in question["content"].get("options", [])]
    detail = contents(api, headers, question)
    assert detail == {"version_id": question["id"], "survey_scope": "owned", "items": [],
                      "total": 0, "offset": 0, "limit": 20}


def test_single_choice_maps_different_local_ids_and_counts_repeated_occurrences(api, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "single_choice", "title": "Reusable choice",
                                    "options": [{"label": "A"}, {"label": "B"}, {"label": "Unused"}]})
    first = select(api, headers, survey(api, headers, allow_repeat=True), question)
    first = publish(api, headers, select(api, headers, first, question))
    second = publish(api, headers, select(api, headers, survey(api, headers, allow_repeat=True), question))
    left, right = first["questions"]
    other = second["questions"][0]
    assert {o["id"] for o in left["options"]}.isdisjoint(o["id"] for o in other["options"])
    submit(api, reader, first, {left["id"]: left["options"][0]["id"], right["id"]: right["options"][1]["id"]})
    submit(api, reader, first, {left["id"]: left["options"][1]["id"], right["id"]: right["options"][1]["id"]})
    submit(api, reader, second, {other["id"]: other["options"][0]["id"]})
    result = report(api, headers, question)
    assert_counts(result, surveys=2, occurrences=3, responses=3, visited=5, answered=5)
    assert [o["option_id"] for o in result["options"]] == [o["id"] for o in question["content"]["options"]]
    assert [o["count"] for o in result["options"]] == [2, 3, 0]
    assert result["average"] is result["min_value"] is result["max_value"] is None
    assert result["distribution"] == [] and result["distribution_total"] == 0
    old = api.get(f"/api/surveys/{first['id']}/statistics", headers=headers).json()
    assert old["total_responses"] == 2
    assert [[o["count"] for o in q["options"]] for q in old["questions"]] == [[1, 1, 0], [0, 2, 0]]


def test_multiple_choice_maps_every_selected_option_but_null_is_not_an_answer(api, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "multiple_choice", "title": "Reusable multi",
                                    "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]})
    targets = [publish(api, headers, select(api, headers, survey(api, headers, allow_repeat=True), question))
               for _ in range(2)]
    for target in targets:
        local = target["questions"][0]
        submit(api, reader, target, {local["id"]: [local["options"][1]["id"], local["options"][0]["id"]]})
    submit(api, reader, targets[0], {})
    result = report(api, headers, question)
    assert_counts(result, surveys=2, occurrences=2, responses=3, visited=3, answered=2)
    assert [o["count"] for o in result["options"]] == [2, 2, 0]
    assert contents(api, headers, question)["items"] == []


def test_numeric_statistics_use_answer_weighting_and_ordered_frequency_pages(api, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "number", "title": "Measured value"})
    first = publish(api, headers, select(api, headers, survey(api, headers, allow_repeat=True), question))
    second = select(api, headers, survey(api, headers), question)
    second = publish(api, headers, select(api, headers, second, question))
    local = first["questions"][0]
    for value in (-10, 0, 10, 10.0):
        submit(api, reader, first, {local["id"]: value})
    submit(api, reader, second, {q["id"]: value for q, value in zip(second["questions"], (100, 20))})
    result = report(api, headers, question, offset=1, limit=2)
    assert_counts(result, surveys=2, occurrences=3, responses=5, visited=6, answered=6)
    assert result["average"] == pytest.approx(130 / 6)
    assert result["min_value"] == -10 and result["max_value"] == 100
    assert result["options"] == []
    assert result["distribution_total"] == 5
    assert result["distribution"] == [{"value": 0, "count": 1}, {"value": 10, "count": 2}]
    assert (result["offset"], result["limit"]) == (1, 2)
    beyond = report(api, headers, question, offset=100, limit=1)
    assert beyond["distribution"] == [] and beyond["distribution_total"] == 5
    assert beyond["average"] == result["average"] and beyond["answer_count"] == 6


def test_skipped_question_and_visited_null_have_distinct_meanings(api, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "number", "title": "Optional numeric answer"})
    target = add_local(api, headers, survey(api, headers, allow_repeat=True),
                       {"type": "single_choice", "title": "Stop here?", "required": True,
                        "options": [{"label": "Stop"}, {"label": "Continue"}]})
    target = select(api, headers, target, question)
    gate, local = target["questions"]
    target = rules(api, headers, target, [{"kind": "choice", "source_id": gate["id"], "target_id": "end",
                                         "operator": "eq", "option_ids": [gate["options"][0]["id"]]}])
    target = publish(api, headers, target)
    submit(api, reader, target, {gate["id"]: gate["options"][0]["id"]})
    submit(api, reader, target, {gate["id"]: gate["options"][1]["id"], local["id"]: None})
    submit(api, reader, target, {gate["id"]: gate["options"][1]["id"], local["id"]: 0})
    result = report(api, headers, question)
    assert_counts(result, surveys=1, occurrences=1, responses=3, visited=2, answered=1)
    assert result["average"] == result["min_value"] == result["max_value"] == 0
    assert result["distribution"] == [{"value": 0, "count": 1}]
    detail = contents(api, headers, question)
    assert detail["total"] == 1 and [item["value"] for item in detail["items"]] == [0]


def test_anonymous_repeats_count_independently_but_request_replay_counts_once(api, database, people):
    (_, headers), (respondent, reader), _ = people
    question = version(api, headers)
    target = publish(api, headers, select(api, headers,
        survey(api, headers, allow_repeat=True, allow_anonymous=True), question))
    local = target["questions"][0]
    data = payload(target, {local["id"]: "Anonymous text"}, anonymous=True)
    first = api.post(target["share_url"] + "/responses", headers=reader, json=data)
    replay = api.post(target["share_url"] + "/responses", headers=reader, json=data)
    assert first.status_code == 201 and replay.status_code == 200 and first.json() == replay.json()
    submit(api, reader, target, {local["id"]: "Another anonymous response"}, anonymous=True)
    submit(api, reader, target, {local["id"]: "Named response"})
    result = report(api, headers, question)
    assert_counts(result, surveys=1, occurrences=1, responses=3, visited=3, answered=3)
    detail = contents(api, headers, question)
    assert detail["total"] == len(detail["items"]) == 3
    assert set(detail["items"][0]) == {"survey_id", "question_id", "response_id", "submitted_at", "value"}
    assert respondent["username"] not in str(detail) and respondent["id"] not in str(detail)
    assert database.submissions.count_documents({}) == 3


def test_family_separates_changed_types_and_includes_unselected_versions(api, people):
    (_, headers), (_, reader), _ = people
    first = version(api, headers, {"type": "single_choice", "title": "Original choice",
                                 "options": [{"label": "A"}, {"label": "B"}]})
    second = version(api, headers, {"type": "number", "title": "Now numeric"}, parent=first)
    third = version(api, headers, {"type": "text", "title": "Unused text version"}, parent=first)
    restored_result = api.post(f"{QUESTIONS}/{first['id']}/restore", headers=headers)
    assert restored_result.status_code == 201, restored_result.text
    restored = restored_result.json()
    for question, value in ((first, 1), (second, 42)):
        target = publish(api, headers, select(api, headers, survey(api, headers), question))
        local = target["questions"][0]
        answer = local["options"][value]["id"] if local["type"] == "single_choice" else value
        submit(api, reader, target, {local["id"]: answer})
    result = family(api, headers, second, offset=1, limit=2)
    assert set(result) == {"family_id", "survey_scope", "versions", "total_versions", "offset", "limit"}
    assert result["family_id"] == first["family_id"] and result["survey_scope"] == "owned"
    assert [v["version_id"] for v in result["versions"]] == [second["id"], third["id"]]
    assert result["total_versions"] == 4 and (result["offset"], result["limit"]) == (1, 2)
    numeric, unused = result["versions"]
    assert numeric["type"] == "number" and numeric["average"] == 42 and numeric["options"] == []
    assert_counts(unused)
    all_versions = family(api, headers, first)["versions"]
    assert [v["version_id"] for v in all_versions] == [first["id"], second["id"], third["id"], restored["id"]]
    assert [o["count"] for o in all_versions[0]["options"]] == [0, 1]
    assert_counts(all_versions[-1])
    assert all(v["average"] is None for v in (all_versions[0], all_versions[2], all_versions[3]))
    beyond = family(api, headers, first, offset=100)
    assert beyond["versions"] == [] and beyond["total_versions"] == 4


def test_versions_with_retained_option_ids_and_changed_labels_are_never_merged(api, people):
    (_, headers), (_, reader), _ = people
    first = version(api, headers, {"type": "single_choice", "title": "Original labels",
                                 "options": [{"label": "Agree"}, {"label": "Disagree"}]})
    changed = deepcopy(first["content"])
    changed["title"] = "Reinterpreted labels"
    changed["options"][0]["label"] = "Strongly agree"
    changed["options"].reverse()
    second = version(api, headers, changed, parent=first)
    for question in (first, second):
        target = publish(api, headers, select(api, headers, survey(api, headers), question))
        local = target["questions"][0]
        submit(api, reader, target, {local["id"]: local["options"][0]["id"]})
    one, two = family(api, headers, first)["versions"]
    assert [o["label"] for o in one["options"]] == ["Agree", "Disagree"]
    assert [o["label"] for o in two["options"]] == ["Disagree", "Strongly agree"]
    assert [o["count"] for o in one["options"]] == [1, 0]
    assert [o["count"] for o in two["options"]] == [1, 0]
    assert one["options"][0]["option_id"] == two["options"][1]["option_id"]
    assert one["answer_count"] == two["answer_count"] == 1


def test_draft_selection_is_not_a_statistical_occurrence_but_empty_published_survey_is(api, people):
    (_, headers), _, _ = people
    question = version(api, headers)
    draft = select(api, headers, survey(api, headers), question)
    assert_counts(report(api, headers, question))
    assert_counts(family(api, headers, question)["versions"][0])
    publish(api, headers, draft)
    assert_counts(report(api, headers, question), surveys=1, occurrences=1)


@pytest.mark.parametrize("state", ["closed", "expired"])
def test_closed_and_expired_surveys_remain_in_historical_statistics(api, database, people, state):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "number", "title": "Historical score"})
    target = publish(api, headers, select(api, headers, survey(api, headers), question))
    submit(api, reader, target, {target["questions"][0]["id"]: 7})
    if state == "closed":
        result = api.post(f"/api/surveys/{target['id']}/close", headers=headers,
                          json={"expected_revision": target["revision"]})
        assert result.status_code == 200, result.text
    else:
        database.surveys.update_one({"_id": ObjectId(target["id"])},
                                    {"$set": {"deadline": mongo_utc_now() - timedelta(seconds=1)}})
    result = report(api, headers, question)
    assert_counts(result, surveys=1, occurrences=1, responses=1, visited=1, answered=1)
    assert result["average"] == 7 and contents(api, headers, question)["total"] == 1


def test_legacy_local_lookalike_is_not_inferred_as_the_saved_version(api, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers)
    target = publish(api, headers, add_local(api, headers, survey(api, headers), question["content"]))
    submit(api, reader, target, {target["questions"][0]["id"]: "Only local"})
    assert_counts(report(api, headers, question))
    assert contents(api, headers, question)["total"] == 0
    original = api.get(f"/api/surveys/{target['id']}/statistics", headers=headers)
    assert original.status_code == 200 and original.json()["questions"][0]["answer_count"] == 1


@pytest.mark.parametrize("changed", [False, True])
def test_noop_local_save_retains_provenance_but_real_local_edit_detaches(api, people, changed):
    (_, headers), (_, reader), _ = people
    question = version(api, headers)
    target = select(api, headers, survey(api, headers), question)
    local = target["questions"][0]
    content = {key: value for key, value in local.items() if key != "id"}
    if changed:
        content["title"] = "Independent local content"
    updated = api.put(f"/api/surveys/{target['id']}/questions/{local['id']}", headers=headers,
                      params={"expected_revision": target["revision"]}, json=content)
    assert updated.status_code == 200, updated.text
    target = publish(api, headers, updated.json())
    submit(api, reader, target, {local["id"]: "Persisted"})
    result = report(api, headers, question)
    if changed:
        assert_counts(result)
    else:
        assert_counts(result, surveys=1, occurrences=1, responses=1, visited=1, answered=1)
    old = api.get(f"/api/surveys/{target['id']}/statistics/{local['id']}", headers=headers)
    assert old.status_code == 200 and old.json()["contents"][0]["value"] == "Persisted"


def test_all_statistics_operations_require_login_and_prevent_response_caching(api, people):
    (_, headers), _, _ = people
    question = version(api, headers)
    for suffix in ("", "/family", "/contents"):
        denied = api.get(endpoint(question, suffix))
        assert denied.status_code == 401 and denied.headers["WWW-Authenticate"] == "Bearer"
        assert denied.headers["Cache-Control"] == "no-store"
        allowed = api.get(endpoint(question, suffix), headers=headers)
        assert allowed.status_code == 200 and allowed.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("target", ["foreign", "missing", "malformed"])
def test_unknown_or_unowned_version_has_the_same_not_found_contract(api, people, target):
    (_, headers), (_, reader), _ = people
    question = version(api, headers)
    inaccessible = {**question, "id": {"foreign": question["id"], "missing": str(ObjectId()), "malformed": "bad-id"}[target]}
    for suffix in ("", "/family", "/contents"):
        response = api.get(endpoint(inaccessible, suffix), headers=reader)
        assert response.status_code == 404 and response.headers["Cache-Control"] == "no-store"


def test_read_grant_does_not_grant_access_to_the_source_owners_responses(api, people):
    (_, headers), (recipient, reader), _ = people
    question = version(api, headers)
    target = publish(api, headers, select(api, headers, survey(api, headers), question))
    submit(api, reader, target, {target["questions"][0]["id"]: "Owner's private survey answer"})
    grant(api, headers, question, recipient)
    assert_counts(report(api, reader, question))
    assert contents(api, reader, question)["items"] == []
    denied = api.get(endpoint(question, "/family"), headers=reader)
    assert denied.status_code == 404
    assert_counts(report(api, headers, question), surveys=1, occurrences=1, responses=1, visited=1, answered=1)
    revoke(api, headers, question, recipient)
    assert api.get(endpoint(question), headers=reader).status_code == 404


def test_revoked_recipient_can_report_their_frozen_snapshot_without_reading_source_history(api, people):
    (_, headers), (recipient, reader), (_, outsider) = people
    question = version(api, headers)
    grant(api, headers, question, recipient)
    target = publish(api, reader, select(api, reader, survey(api, reader), question))
    submit(api, outsider, target, {target["questions"][0]["id"]: "Recipient's frozen response"})
    revoke(api, headers, question, recipient)
    for suffix in ("", "/history"):
        assert api.get(f"{QUESTIONS}/{question['id']}" + suffix, headers=reader).status_code == 404
    assert api.get(endpoint(question, "/family"), headers=reader).status_code == 404
    result = report(api, reader, question)
    assert_counts(result, surveys=1, occurrences=1, responses=1, visited=1, answered=1)
    assert [item["value"] for item in contents(api, reader, question)["items"]] == ["Recipient's frozen response"]
    assert_counts(report(api, headers, question))
    assert contents(api, headers, question)["items"] == []
    assert api.get(endpoint(question), headers=outsider).status_code == 404


def test_draft_reference_authorizes_empty_exact_report_until_source_is_detached(api, people):
    (_, headers), (recipient, reader), _ = people
    question = version(api, headers)
    grant(api, headers, question, recipient)
    target = select(api, reader, survey(api, reader), question)
    revoke(api, headers, question, recipient)
    assert_counts(report(api, reader, question))
    assert contents(api, reader, question)["total"] == 0
    local = target["questions"][0]
    updated = api.put(f"/api/surveys/{target['id']}/questions/{local['id']}", headers=reader,
                      params={"expected_revision": target["revision"]}, json={"type": "text", "title": "Detached"})
    assert updated.status_code == 200, updated.text
    assert api.get(endpoint(question), headers=reader).status_code == 404
    assert api.get(endpoint(question, "/contents"), headers=reader).status_code == 404


def test_bank_grant_authorizes_exact_report_but_archive_removes_unreferenced_access(api, people):
    (_, headers), (recipient, reader), _ = people
    question = version(api, headers)
    created = api.post("/api/question-banks", headers=headers, json={"title": "Statistics bank"})
    assert created.status_code == 201, created.text
    target = created.json()
    added = api.post(f"/api/question-banks/{target['id']}/entries", headers=headers,
                     json={"version_id": question["id"]})
    assert added.status_code == 201, added.text
    shared = api.post(f"/api/question-banks/{target['id']}/grants", headers=headers,
                      json={"username": recipient["username"]})
    assert shared.status_code == 201, shared.text
    assert_counts(report(api, reader, question))
    assert api.get(endpoint(question, "/family"), headers=reader).status_code == 404
    archived = api.delete(f"/api/question-banks/{target['id']}", headers=headers,
                          params={"expected_revision": target["revision"]})
    assert archived.status_code == 204, archived.text
    assert api.get(endpoint(question), headers=reader).status_code == 404


def test_contents_have_stable_order_with_same_timestamp_and_repeated_local_instances(api, database, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers)
    first = select(api, headers, survey(api, headers, allow_repeat=True), question)
    first = publish(api, headers, select(api, headers, first, question))
    second = publish(api, headers, select(api, headers, survey(api, headers), question))
    one = submit(api, reader, first, {q["id"]: f"First {index}" for index, q in enumerate(first["questions"])})
    two = submit(api, reader, first, {first["questions"][0]["id"]: "Later first", first["questions"][1]["id"]: None})
    three = submit(api, reader, second, {second["questions"][0]["id"]: "Other survey"})
    same_time = mongo_utc_now()
    database.submissions.update_many({}, {"$set": {"submitted_at": same_time}})
    expected = []
    for receipt, target, answers in ((one, first, {q["id"]: f"First {i}" for i, q in enumerate(first["questions"])}),
                                      (two, first, {first["questions"][0]["id"]: "Later first"}),
                                      (three, second, {second["questions"][0]["id"]: "Other survey"})):
        for question_id, value in answers.items():
            expected.append({"response_id": receipt["response_id"], "survey_id": target["id"],
                             "question_id": question_id, "value": value})
    expected.sort(key=lambda item: item["question_id"])
    expected.sort(key=lambda item: item["response_id"], reverse=True)
    pages = [contents(api, headers, question, offset=offset, limit=2) for offset in (0, 2, 4)]
    actual = [item for page in pages for item in page["items"]]
    assert all(page["total"] == 4 for page in pages)
    assert pages[-1]["items"] == []
    assert [{key: value for key, value in item.items() if key != "submitted_at"} for item in actual] == expected
    assert len({item["submitted_at"] for item in actual}) == 1
    assert_counts(report(api, headers, question), surveys=2, occurrences=3, responses=3, visited=5, answered=4)


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice"])
def test_choice_contents_are_empty_even_when_there_are_answers(api, people, kind):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": kind, "title": "No raw choice contents",
                                    "options": [{"label": "A"}, {"label": "B"}]})
    target = publish(api, headers, select(api, headers, survey(api, headers), question))
    local = target["questions"][0]
    value = local["options"][0]["id"]
    submit(api, reader, target, {local["id"]: [value] if kind == "multiple_choice" else value})
    result = contents(api, headers, question, offset=2, limit=1)
    assert result["items"] == [] and result["total"] == 0 and (result["offset"], result["limit"]) == (2, 1)
    assert report(api, headers, question)["answer_count"] == 1


def test_statistics_validate_bounded_pagination_and_disallow_global_survey_scope(api, people):
    (_, headers), _, _ = people
    question = version(api, headers)
    for suffix in ("", "/family", "/contents"):
        for params in ({"offset": -1}, {"offset": 2147483648}, {"limit": 0}, {"limit": 101},
                       {"survey_scope": "all"}, {"survey_scope": "foreign"}):
            result = api.get(endpoint(question, suffix), headers=headers, params=params)
            assert result.status_code == 422, result.text
        valid = api.get(endpoint(question, suffix), headers=headers,
                        params={"survey_scope": "owned", "offset": 2147483647, "limit": 100})
        assert valid.status_code == 200, valid.text


@pytest.mark.parametrize("after_insert", [False, True])
def test_failed_submission_and_retry_statistics_count_persisted_answers_only(api, database, people, monkeypatch, after_insert):
    (_, headers), (_, reader), _ = people
    question = version(api, headers, {"type": "number", "title": "Saved records only"})
    target = publish(api, headers, select(api, headers, survey(api, headers, allow_repeat=True), question))
    data = payload(target, {target["questions"][0]["id"]: 9})
    original = ResponseRepository.insert

    def fail(repository, document):
        if after_insert:
            original(repository, document)
        raise OperationFailure("simulated lost acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(ResponseRepository, "insert", fail)
        failed = api.post(target["share_url"] + "/responses", headers=reader, json=data)
        assert failed.status_code == 503, failed.text
    initial = report(api, headers, question)
    expected = int(after_insert)
    assert_counts(initial, surveys=1, occurrences=1, responses=expected, visited=expected, answered=expected)
    retried = api.post(target["share_url"] + "/responses", headers=reader, json=data)
    assert retried.status_code == (200 if after_insert else 201), retried.text
    final = report(api, headers, question)
    assert_counts(final, surveys=1, occurrences=1, responses=1, visited=1, answered=1)
    assert final["distribution"] == [{"value": 9, "count": 1}] and final["average"] == 9
    assert database.submissions.count_documents({}) == 1


def test_reporting_does_not_write_survey_submission_or_saved_version_documents(api, database, people):
    (_, headers), (_, reader), _ = people
    question = version(api, headers)
    target = publish(api, headers, select(api, headers, survey(api, headers), question))
    submit(api, reader, target, {target["questions"][0]["id"]: "Persisted before reporting"})
    before = {name: list(database[name].find({}).sort("_id", 1))
              for name in ("surveys", "submissions", "question_versions", "question_grants")}
    report(api, headers, question)
    family(api, headers, question)
    contents(api, headers, question)
    after = {name: list(database[name].find({}).sort("_id", 1)) for name in before}
    assert after == before
