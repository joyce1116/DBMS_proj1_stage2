"""Real MongoDB: rules, submission, anonymity, statistics and write races."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import OperationFailure
import pytest

from app.core.time import mongo_utc_now
from app.main import create_app
from app.responses.repository import ResponseRepository
from app.surveys.repository import SurveyRepository


pytestmark = pytest.mark.integration


def actor(api, name):
    data = {"username": name, "password": "Response_Password_123"}
    result = api.post("/api/auth/register", json=data)
    assert result.status_code == 201, result.text
    login = api.post("/api/auth/login", json=data)
    assert login.status_code == 200, login.text
    return result.json(), {"Authorization": "Bearer " + login.json()["access_token"]}


@pytest.fixture
def world(api):
    owner, headers = actor(api, "Response_Owner")
    bob, bob_headers = actor(api, "Response_Bob")
    return owner, headers, bob, bob_headers


def survey(api, headers, *, publish=True, rules=True, **settings):
    result = api.post("/api/surveys", headers=headers, json={"title": "分支调查", **settings})
    assert result.status_code == 201, result.text
    doc = result.json()
    base = "/api/surveys/" + doc["id"]
    for question in [
        {"type": "single_choice", "title": "参与", "required": True,
         "options": [{"label": "是"}, {"label": "否"}]},
        {"type": "multiple_choice", "title": "多选", "required": True,
         "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}],
         "min_selections": 2, "max_selections": 2},
        {"type": "number", "title": "年龄", "required": True,
         "min_value": 0, "max_value": 120, "integer_only": True},
        {"type": "text", "title": "建议", "required": False, "min_length": 2, "max_length": 20},
    ]:
        result = api.post(base + "/questions", headers=headers,
                          params={"expected_revision": doc["revision"]}, json=question)
        assert result.status_code == 201, result.text
        doc = result.json()
    if rules:
        q = doc["questions"]
        result = api.put(base + "/rules", headers=headers, json={"expected_revision": doc["revision"], "rules": [
            {"kind": "choice", "source_id": q[0]["id"], "target_id": q[2]["id"],
             "operator": "eq", "option_ids": [q[0]["options"][1]["id"]]},
            {"kind": "number", "source_id": q[2]["id"], "target_id": "end", "operator": "lt", "value": 18},
        ]})
        assert result.status_code == 200, result.text
        doc = result.json()
    if publish:
        result = api.post(base + "/publish", headers=headers, json={"expected_revision": doc["revision"]})
        assert result.status_code == 200, result.text
        doc = result.json()
    return doc


def payload(doc, *, branch=False, age=20, text="有效建议", anonymous=False, **changes):
    q = doc["questions"]
    answers = {q[0]["id"]: q[0]["options"][int(branch)]["id"], q[2]["id"]: age}
    if not branch:
        answers[q[1]["id"]] = [opt["id"] for opt in q[1]["options"][:2]][::-1]
    if age >= 18:
        answers[q[3]["id"]] = text
    return {"survey_revision": doc["revision"], "request_id": uuid4().hex,
            "answers": answers, "anonymous": anonymous, **changes}


def post(api, doc, headers, data):
    return api.post(doc["share_url"] + "/responses", headers=headers, json=data)


def test_preview_submission_and_normalized_storage(api, database, world):
    owner, headers, bob, reader = world
    doc = survey(api, headers)
    before = database.surveys.find_one({"_id": ObjectId(doc["id"])})
    data = payload(doc, branch=True, age=17)
    preview = api.post(doc["share_url"] + "/navigate", headers=reader,
                       json={"survey_revision": doc["revision"], "answers": data["answers"]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["completed"]
    assert database.submissions.count_documents({}) == 0
    assert database.surveys.find_one({"_id": before["_id"]}) == before
    response = post(api, doc, reader, data)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    stored = database.submissions.find_one()
    assert stored["path"] == preview.json()["path"] == [doc["questions"][i]["id"] for i in (0, 2)]
    assert stored["answers"] == preview.json()["answers"]
    assert stored["respondent_id"] == ObjectId(bob["id"])
    assert stored["survey_revision"] == doc["revision"] and stored["single_response"]
    assert stored["submitted_at"].utcoffset() == timedelta(0)
    assert stored["submitted_at"].microsecond % 1000 == 0
    after = database.surveys.find_one({"_id": before["_id"]})
    assert after == {**before, "submission_serial": 1, "last_submission_at": stored["submitted_at"]}


@pytest.mark.parametrize("fault,expected", [
    ("required", 422), ("unknown", 422), ("off_path", 422), ("range", 422),
    ("string_number", 422), ("multi_count", 422), ("stale_revision", 409), ("anonymous", 422),
    ("extra", 422), ("invalid_request_id", 422),
])
def test_rejected_submissions_do_not_write_or_affect_statistics(api, database, world, fault, expected):
    _, headers, _, reader = world
    doc = survey(api, headers)
    q = doc["questions"]
    data = payload(doc)
    if fault == "required": del data["answers"][q[2]["id"]]
    if fault == "unknown": data["answers"]["q_" + "f" * 32] = "伪造"
    if fault == "off_path":
        data["answers"][q[0]["id"]] = q[0]["options"][1]["id"]
    if fault == "range": data["answers"][q[2]["id"]] = 121
    if fault == "string_number": data["answers"][q[2]["id"]] = "20"
    if fault == "multi_count": data["answers"][q[1]["id"]] = [q[1]["options"][0]["id"]]
    if fault == "stale_revision": data["survey_revision"] -= 1
    if fault == "anonymous": data["anonymous"] = True
    if fault == "extra": data["respondent_id"] = "forged"
    if fault == "invalid_request_id": data["request_id"] = "short"
    before = database.surveys.find_one()
    result = post(api, doc, reader, data)
    assert result.status_code == expected, result.text
    assert database.submissions.count_documents({}) == 0
    assert database.surveys.find_one() == before
    stats = api.get("/api/surveys/" + doc["id"] + "/statistics", headers=headers).json()
    assert stats["total_responses"] == 0
    assert all(row["answer_count"] == row["visited_count"] == 0 for row in stats["questions"])


def test_idempotency_payload_conflict_and_no_repeat_across_anonymous_modes(api, database, world):
    _, headers, _, reader = world
    doc = survey(api, headers, allow_anonymous=True)
    data = payload(doc, anonymous=True)
    first = post(api, doc, reader, data)
    assert first.status_code == 201, first.text
    replay = post(api, doc, reader, deepcopy(data))
    assert replay.status_code == 200 and replay.json() == first.json()
    changed = deepcopy(data)
    changed["answers"][doc["questions"][2]["id"]] = 21
    assert post(api, doc, reader, changed).status_code == 409
    assert post(api, doc, reader, payload(doc, anonymous=False)).status_code == 409
    assert database.submissions.count_documents({}) == 1
    api.post("/api/surveys/" + doc["id"] + "/close", headers=headers,
             json={"expected_revision": doc["revision"]})
    assert post(api, doc, reader, data).status_code == 200
    assert post(api, doc, reader, payload(doc)).status_code == 410


def test_repeat_policy_anonymity_owner_scope_and_statistics(api, database, world):
    owner, headers, bob, reader = world
    doc = survey(api, headers, allow_anonymous=True, allow_repeat=True)
    first = payload(doc, anonymous=True, age=20, text="第一条建议")
    second = payload(doc, age=40, text=None)
    third = payload(doc, branch=True, age=10)
    for data in (first, second, third):
        result = post(api, doc, reader, data)
        assert result.status_code == 201, result.text
    base = "/api/surveys/" + doc["id"]
    responses = api.get(base + "/responses", headers=headers).json()
    assert responses["total"] == 3
    anonymous = next(r for r in responses["items"] if r["anonymous"])
    assert anonymous["respondent"] is None
    named = next(r for r in responses["items"] if not r["anonymous"])
    assert named["respondent"] == {"id": bob["id"], "username": bob["username"]}
    assert not {"respondent_id", "request_id", "fingerprint", "submission_serial"} & anonymous.keys()
    stats = api.get(base + "/statistics", headers=headers)
    assert stats.status_code == 200, stats.text
    result = stats.json()
    assert result["total_responses"] == 3
    single, multi, number, text = result["questions"]
    assert [o["count"] for o in single["options"]] == [2, 1]
    assert [o["count"] for o in multi["options"]] == [2, 2, 0]
    assert multi["answer_count"] == multi["visited_count"] == 2
    assert number["average"] == pytest.approx(70 / 3)
    assert text["visited_count"] == 2 and text["answer_count"] == 1
    for q, expected in ((doc["questions"][2], [10, 40, 20]), (doc["questions"][3], ["第一条建议"])):
        detail = api.get(base + "/statistics/" + q["id"], headers=headers).json()
        assert [item["value"] for item in detail["contents"]] == expected
        assert detail["contents_total"] == len(expected)
        page = api.get(base + "/statistics/" + q["id"], headers=headers, params={"offset": 1, "limit": 1}).json()
        assert len(page["contents"]) == int(len(expected) > 1)
    assert len(api.get(base + "/responses?offset=1&limit=1", headers=headers).json()["items"]) == 1
    for endpoint in ("/responses", "/statistics", "/statistics/" + doc["questions"][2]["id"]):
        assert api.get(base + endpoint, headers=reader).status_code == 404
        assert api.get(base + endpoint).status_code == 401
    assert api.get(base + "/statistics/q_" + "f" * 32, headers=headers).status_code == 404
    assert api.get(base + "/responses?limit=101", headers=headers).status_code == 422


def test_rules_block_dangling_edits_reordering_and_other_owners(api, database, world):
    _, headers, _, reader = world
    doc = survey(api, headers, publish=False)
    q = doc["questions"]
    base = "/api/surveys/" + doc["id"]
    for method, endpoint, json in (
        ("DELETE", "/questions/" + q[2]["id"], None),
        ("PUT", "/questions/" + q[0]["id"], {"type": "text", "title": "破坏类型"}),
        ("PUT", "/question-order", {"question_ids": [question["id"] for question in q[::-1]]}),
    ):
        response = api.request(method, base + endpoint, headers=headers,
                               params={"expected_revision": doc["revision"]}, json=json)
        assert response.status_code == 422, response.text
    assert api.put(base + "/rules", headers=reader,
                   json={"expected_revision": doc["revision"], "rules": []}).status_code == 404
    assert api.put(base + "/rules", headers=headers,
                   json={"expected_revision": doc["revision"] - 1, "rules": []}).status_code == 409
    assert database.surveys.find_one()["revision"] == doc["revision"]
    cleared = api.put(base + "/rules", headers=headers,
                      json={"expected_revision": doc["revision"], "rules": []})
    assert cleared.status_code == 200
    deleted = api.delete(base + "/questions/" + q[2]["id"], headers=headers,
                         params={"expected_revision": cleared.json()["revision"]})
    assert deleted.status_code == 200


@pytest.mark.parametrize("same_request,repeat", [(False, False), (True, False), (False, True), (True, True)])
def test_concurrent_submissions_obey_unique_indexes(api, database, world, monkeypatch, same_request, repeat):
    _, headers, _, reader = world
    doc = survey(api, headers, allow_repeat=repeat)
    barrier = Barrier(4)
    real_gate = SurveyRepository.accept_submission

    def synchronized_gate(repository, *args):
        barrier.wait(timeout=10)
        return real_gate(repository, *args)

    monkeypatch.setattr(SurveyRepository, "accept_submission", synchronized_gate)
    data = payload(doc)
    batch = [deepcopy(data) if same_request else payload(doc) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda data: post(api, doc, reader, data), batch))
    codes = sorted(r.status_code for r in results)
    if same_request:
        assert codes == [200, 200, 200, 201], [r.text for r in results]
        assert len({r.json()["response_id"] for r in results}) == 1
    elif repeat:
        assert codes == [201] * 4
    else:
        assert codes == [201, 409, 409, 409]
    assert database.submissions.count_documents({}) == (4 if repeat and not same_request else 1)


@pytest.mark.parametrize("accept_first", [True, False])
def test_closing_and_submission_have_an_explicit_atomic_acceptance_order(api, database, world, monkeypatch, accept_first):
    _, headers, _, reader = world
    doc = survey(api, headers)
    real_gate = SurveyRepository.accept_submission

    def gate(repository, *args):
        accepted = real_gate(repository, *args) if accept_first else None
        result = api.post("/api/surveys/" + doc["id"] + "/close", headers=headers,
                          json={"expected_revision": doc["revision"]})
        assert result.status_code == 200
        return accepted if accept_first else real_gate(repository, *args)

    monkeypatch.setattr(SurveyRepository, "accept_submission", gate)
    result = post(api, doc, reader, payload(doc))
    assert result.status_code == (201 if accept_first else 410), result.text
    assert database.submissions.count_documents({}) == int(accept_first)


def test_database_gate_rechecks_deadline_after_validation(api, database, world, monkeypatch):
    _, headers, _, reader = world
    doc = survey(api, headers)
    real_gate = SurveyRepository.accept_submission

    def expire_before_accept(repository, *args):
        database.surveys.update_one({"_id": ObjectId(doc["id"])},
                                    {"$set": {"deadline": mongo_utc_now() - timedelta(seconds=1)}})
        return real_gate(repository, *args)

    monkeypatch.setattr(SurveyRepository, "accept_submission", expire_before_accept)
    assert post(api, doc, reader, payload(doc)).status_code == 410
    assert database.submissions.count_documents({}) == 0


@pytest.mark.parametrize("after_insert", [True, False])
def test_retry_after_database_failure_is_safe_and_statistics_use_saved_records(api, database, world, monkeypatch, after_insert):
    _, headers, _, reader = world
    doc = survey(api, headers)
    data = payload(doc)
    original = ResponseRepository.insert

    def fail(repository, document):
        if after_insert: original(repository, document)
        raise OperationFailure("simulated lost acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(ResponseRepository, "insert", fail)
        assert post(api, doc, reader, data).status_code == 503
    assert database.submissions.count_documents({}) == int(after_insert)
    result = post(api, doc, reader, data)
    assert result.status_code == (200 if after_insert else 201), result.text
    stats = api.get("/api/surveys/" + doc["id"] + "/statistics", headers=headers).json()
    assert stats["total_responses"] == database.submissions.count_documents({}) == 1


def test_unknown_closed_and_unauthenticated_new_operations(api, database, world):
    _, headers, _, reader = world
    doc = survey(api, headers)
    data = payload(doc)
    for endpoint in ("/responses", "/navigate"):
        body = data if endpoint == "/responses" else {k: data[k] for k in ("answers", "survey_revision")}
        assert api.post(doc["share_url"] + endpoint, json=body).status_code == 401
        assert api.post("/survey/" + "x" * 32 + endpoint, headers=reader, json=body).status_code == 404
    closed = api.post("/api/surveys/" + doc["id"] + "/close", headers=headers,
                      json={"expected_revision": doc["revision"]})
    assert closed.status_code == 200
    assert post(api, doc, reader, data).status_code == 410
    assert api.post(doc["share_url"] + "/navigate", headers=reader,
                    json={"survey_revision": doc["revision"], "answers": {}}).status_code == 410


def test_different_accounts_can_submit_concurrently_and_persist_after_restart(api, database, world, mongo_settings, monkeypatch):
    _, headers, _, reader = world
    doc = survey(api, headers, allow_anonymous=True)
    barrier = Barrier(2)
    real_gate = SurveyRepository.accept_submission

    def synchronize(repository, *args):
        barrier.wait(timeout=10)
        return real_gate(repository, *args)

    with monkeypatch.context() as patch:
        patch.setattr(SurveyRepository, "accept_submission", synchronize)
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda h: post(api, doc, h, payload(doc, anonymous=True)), (headers, reader)))
    assert [r.status_code for r in results] == [201, 201]
    before = list(database.submissions.find())
    with TestClient(create_app(mongo_settings)) as restarted:
        stats = restarted.get("/api/surveys/" + doc["id"] + "/statistics", headers=headers)
        assert stats.status_code == 200 and stats.json()["total_responses"] == 2
        responses = restarted.get("/api/surveys/" + doc["id"] + "/responses", headers=headers).json()
        assert {r["response_id"] for r in responses["items"]} == {r.json()["response_id"] for r in results}
    assert list(database.submissions.find()) == before


def test_expired_request_replay_and_preview_version_checks(api, database, world):
    _, headers, _, reader = world
    doc = survey(api, headers)
    result = api.post(doc["share_url"] + "/navigate", headers=reader,
                      json={"survey_revision": doc["revision"] - 1, "answers": {}})
    assert result.status_code == 409
    data = payload(doc)
    assert post(api, doc, reader, data).status_code == 201
    database.surveys.update_one({"_id": ObjectId(doc["id"])},
                                {"$set": {"deadline": mongo_utc_now() - timedelta(seconds=1)}})
    assert post(api, doc, reader, data).status_code == 200
    assert post(api, doc, reader, payload(doc)).status_code == 410


def test_publish_revalidates_rules_and_frozen_rules_cannot_be_replaced(api, database, world):
    _, headers, _, reader = world
    doc = survey(api, headers, publish=False)
    base = "/api/surveys/" + doc["id"]
    bad_rules = deepcopy(doc["jump_rules"])
    bad_rules[0]["target_id"] = doc["questions"][0]["id"]
    # Storage schema checks structure; publication also checks ID relationships.
    database.surveys.update_one({"_id": ObjectId(doc["id"])}, {"$set": {"jump_rules": bad_rules}})
    assert api.post(base + "/publish", headers=headers, json={"expected_revision": doc["revision"]}).status_code == 422
    fixed = api.put(base + "/rules", headers=headers,
                    json={"expected_revision": doc["revision"], "rules": doc["jump_rules"]})
    assert fixed.status_code == 200
    opened = api.post(base + "/publish", headers=headers,
                      json={"expected_revision": fixed.json()["revision"]}).json()
    assert api.put(base + "/rules", headers=headers,
                   json={"expected_revision": opened["revision"], "rules": []}).status_code == 409
    assert api.put(base + "/rules", json={"expected_revision": opened["revision"], "rules": []}).status_code == 401
    assert api.post(base + "/questions?expected_revision=" + str(10**100), headers=headers,
                    json={"type": "text", "title": "巨大版本"}).status_code == 422
