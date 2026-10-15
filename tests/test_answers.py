"""Real answer boundaries, plus the authenticated read-only validation API."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math

import pytest

from app.surveys.models import parse_question
from app.surveys.validation import InvalidAnswer, validate_answer


QID = "q_" + "1" * 32
A, B, C = ("opt_" + letter * 32 for letter in "abc")


def question(kind, **changes):
    payload = {"type": kind, "title": "Test", "required": True}
    if kind in ("single_choice", "multiple_choice"):
        payload["options"] = [{"id": key, "label": label} for key, label in zip((A, B, C), "ABC", strict=True)]
    if kind == "text":
        payload.update(min_length=2, max_length=5)
    if kind == "number":
        payload.update(min_value=0, max_value=120, integer_only=True)
    payload.update(changes)
    return {"id": QID, **parse_question(payload).model_dump()}


@pytest.mark.parametrize("kind,value,expected,changes", [
    ("single_choice", A, A, {}),
    ("multiple_choice", [C, A], [A, C], {"min_selections": 2, "max_selections": 2}),
    ("text", "中文", "中文", {}),
    ("text", " abc ", " abc ", {}),
    ("number", 0, 0, {}),
    ("number", 120, 120, {}),
    ("number", 1.0, 1, {}),
    ("number", 1.5, 1.5, {"integer_only": False}),
])
def test_valid_answers_and_normalized_values(kind, value, expected, changes):
    result = validate_answer(question(kind, **changes), value)
    assert result == expected
    assert type(result) is type(expected)


@pytest.mark.parametrize("kind,value", [
    ("single_choice", None), ("single_choice", ""),
    ("multiple_choice", None), ("multiple_choice", []),
    ("text", None), ("text", " \t"), ("number", None),
])
def test_optional_unanswered_values_are_none(kind, value):
    assert validate_answer(question(kind, required=False), value) is None


def test_optional_multiselect_can_skip_but_nonempty_answer_must_meet_minimum():
    definition = question("multiple_choice", required=False, min_selections=2, max_selections=2)
    assert validate_answer(definition, []) is None
    with pytest.raises(InvalidAnswer) as error:
        validate_answer(definition, [A])
    assert error.value.code == "count"


@pytest.mark.parametrize("kind", ["single_choice", "multiple_choice", "text", "number"])
def test_required_questions_reject_no_answer(kind):
    with pytest.raises(InvalidAnswer) as error:
        validate_answer(question(kind), None)
    assert error.value.code == "required" and error.value.question_id == QID


@pytest.mark.parametrize("kind,value,code,changes", [
    ("single_choice", [A], "type", {}),
    ("single_choice", "not-an-option", "option", {}),
    ("multiple_choice", A, "type", {}),
    ("multiple_choice", [A, 1], "type", {}),
    ("multiple_choice", [A, A], "duplicate", {}),
    ("multiple_choice", ["foreign-option"], "option", {}),
    ("multiple_choice", [A], "count", {"min_selections": 2}),
    ("multiple_choice", [A, B, C], "count", {"max_selections": 2}),
    ("text", 12, "type", {}),
    ("text", "a", "length", {}),
    ("text", "abcdef", "length", {}),
    ("number", True, "type", {}),
    ("number", False, "type", {}),
    ("number", "20", "type", {}),
    ("number", "", "type", {"required": False}),
    ("number", 0.5, "integer", {}),
    ("number", -1, "range", {}),
    ("number", 121, "range", {}),
    ("number", math.nan, "number", {}),
    ("number", math.inf, "number", {}),
    ("number", -math.inf, "number", {}),
    ("number", 10**100, "number", {}),
])
def test_invalid_answer_boundaries(kind, value, code, changes):
    with pytest.raises(InvalidAnswer) as error:
        validate_answer(question(kind, **changes), value)
    assert error.value.code == code


def test_validation_does_not_mutate_question_or_input():
    definition = question("multiple_choice")
    supplied = [C, A]
    original = deepcopy((definition, supplied))
    assert validate_answer(definition, supplied) == [A, C]
    assert (definition, supplied) == original


@pytest.fixture
def answer_form(api):
    credentials = {"username": "Answer_Tester", "password": "answer-test-password"}
    assert api.post("/api/auth/register", json=credentials).status_code == 201
    login = api.post("/api/auth/login", json=credentials)
    assert login.status_code == 200
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    response = api.post("/api/surveys", headers=headers, json={
        "title": "Answer test", "allow_anonymous": True,
        "deadline": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    })
    assert response.status_code == 201
    survey = response.json()
    for kind in ("single_choice", "multiple_choice", "text", "number"):
        definition = question(kind)
        del definition["id"]
        for option in definition.get("options", []):
            del option["id"]
        response = api.post(f"/api/surveys/{survey['id']}/questions", headers=headers,
                            params={"expected_revision": survey["revision"]}, json=definition)
        assert response.status_code == 201, response.text
        survey = response.json()
    response = api.post(f"/api/surveys/{survey['id']}/publish", headers=headers,
                        json={"expected_revision": survey["revision"]})
    assert response.status_code == 200, response.text
    return response.json(), headers


def snapshot(database):
    return {name: list(database[name].find({}).sort("_id", 1)) for name in database.list_collection_names()}


@pytest.mark.integration
class TestAnswerAPI:
    def test_checks_all_four_types_without_saving_answers(self, api, database, answer_form):
        survey, headers = answer_form
        before = snapshot(database)
        for item in survey["questions"]:
            values = {
                "single_choice": item.get("options", [{"id": ""}])[0]["id"],
                "multiple_choice": [option["id"] for option in item.get("options", [])[:2]],
                "text": "中文", "number": 20.0,
            }
            response = api.post(survey["share_url"] + "/validate", headers=headers,
                                json={"question_id": item["id"], "value": values[item["type"]]})
            assert response.status_code == 200, response.text
            assert response.headers["Cache-Control"] == "no-store"
            assert set(response.json()) == {"question_id", "valid", "value"}
            assert response.json()["valid"] is True
        assert snapshot(database) == before

    def test_rejects_bad_answers_with_location_and_no_write(self, api, database, answer_form):
        survey, headers = answer_form
        before = snapshot(database)
        for item, value, code in zip(survey["questions"], ["foreign", [], "x", 121],
                                     ["option", "required", "length", "range"], strict=True):
            response = api.post(survey["share_url"] + "/validate", headers=headers,
                                json={"question_id": item["id"], "value": value})
            assert response.status_code == 422
            assert response.json()["detail"]["question_id"] == item["id"]
            assert response.json()["detail"]["code"] == code
            assert "value" not in response.json()["detail"]
        assert snapshot(database) == before

    def test_anonymous_flag_does_not_bypass_authentication(self, api, answer_form):
        survey, _ = answer_form
        payload = {"question_id": survey["questions"][0]["id"], "value": None}
        for headers in ({}, {"Authorization": "Bearer invalid"}):
            response = api.post(survey["share_url"] + "/validate", headers=headers, json=payload)
            assert response.status_code == 401

    def test_unknown_question_or_share_is_not_found(self, api, answer_form):
        survey, headers = answer_form
        payload = {"question_id": "q_" + "f" * 32, "value": None}
        assert api.post(survey["share_url"] + "/validate", headers=headers, json=payload).status_code == 404
        payload["question_id"] = survey["questions"][0]["id"]
        assert api.post("/survey/" + "x" * 32 + "/validate", headers=headers, json=payload).status_code == 404

    @pytest.mark.parametrize("state", ["closed", "expired"])
    def test_unavailable_survey_rejects_validation(self, api, database, answer_form, monkeypatch, state):
        survey, headers = answer_form
        if state == "closed":
            response = api.post(f"/api/surveys/{survey['id']}/close", headers=headers,
                                json={"expected_revision": survey["revision"]})
            assert response.status_code == 200
        else:
            deadline = database.surveys.find_one({})["deadline"]
            monkeypatch.setattr("app.surveys.service.mongo_utc_now", lambda: deadline)
        before = snapshot(database)
        response = api.post(survey["share_url"] + "/validate", headers=headers,
                            json={"question_id": survey["questions"][0]["id"], "value": None})
        assert response.status_code == 410
        assert snapshot(database) == before

    def test_request_cannot_override_server_constraints(self, api, database, answer_form):
        survey, headers = answer_form
        before = snapshot(database)
        item = survey["questions"][-1]
        for payload in (
            {"question_id": item["id"]},
            {"question_id": item["id"], "value": None, "required": False},
            {"question_id": item["id"], "value": 200, "max_value": 999},
        ):
            response = api.post(survey["share_url"] + "/validate", headers=headers, json=payload)
            assert response.status_code == 422
        assert snapshot(database) == before

    def test_nonfinite_json_number_returns_safe_error(self, api, answer_form):
        survey, headers = answer_form
        body = json.dumps({"question_id": survey["questions"][-1]["id"]})[:-1] + ', "value": NaN}'
        response = api.post(survey["share_url"] + "/validate", content=body,
                            headers={**headers, "Content-Type": "application/json"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "number"
