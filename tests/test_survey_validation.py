"""Draft definition checks only; these are not respondent-answer checks."""

from datetime import datetime, timedelta, timezone
import math

import pytest
from pydantic import TypeAdapter, ValidationError

from app.survey_models import QuestionInput, QuestionOrder, SurveyCreate, SurveyPatch


QUESTION = TypeAdapter(QuestionInput)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "single_choice", "title": " Choose ", "options": [{"label": " A "}, {"label": "B"}]},
        {"type": "multiple_choice", "title": "Choose two", "options": [{"label": "A"}, {"label": "B"}], "min_selections": 2, "max_selections": 2},
        {"type": "text", "title": "Name", "min_length": 2, "max_length": 2},
        {"type": "number", "title": "Count", "min_value": 0, "max_value": 0, "integer_only": True},
    ],
    ids=["single-choice", "exactly-two", "text-boundary", "integer-boundary"],
)
def test_valid_question_definitions(payload):
    question = QUESTION.validate_python(payload)
    assert question.title == payload["title"].strip()
    assert question.required is False
    if "options" in payload:
        assert question.options[0].label == payload["options"][0]["label"].strip()


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "unknown", "title": "Unknown"},
        {"type": "text", "title": "  "},
        {"type": "text", "title": "x" * 501},
        {"type": "text", "title": "T", "required": "false"},
        {"type": "text", "title": "T", "jump_rules": []},
        {"type": "text", "title": "T", "min_length": True},
        {"type": "text", "title": "T", "min_length": 3, "max_length": 2},
        {"type": "single_choice", "title": "T", "options": [{"label": "only one"}]},
        {"type": "single_choice", "title": "T", "options": [{"label": "Ａ"}, {"label": "a"}]},
        {"type": "multiple_choice", "title": "T", "options": [{"label": "A"}, {"label": "B"}], "min_selections": 3},
        {"type": "multiple_choice", "title": "T", "options": [{"label": "A"}, {"label": "B"}], "max_selections": 3},
        {"type": "multiple_choice", "title": "T", "options": [{"label": "A"}, {"label": "B"}], "min_selections": 2, "max_selections": 1},
        {"type": "multiple_choice", "title": "T", "required": True, "options": [{"label": "A"}, {"label": "B"}], "max_selections": 0},
        {"type": "number", "title": "T", "min_value": 5, "max_value": 4},
        {"type": "number", "title": "T", "min_value": 0.1, "max_value": 0.9, "integer_only": True},
    ],
    ids=["unknown-type", "blank-title", "long-title", "strict-bool", "unknown-field", "strict-length", "inverted-lengths", "too-few-options", "normalized-duplicate-label", "min-exceeds-options", "max-exceeds-options", "inverted-counts", "required-but-none-permitted", "inverted-numbers", "no-valid-integer"],
)
def test_invalid_question_definitions_are_rejected(payload):
    with pytest.raises(ValidationError):
        QUESTION.validate_python(payload)


@pytest.mark.parametrize(
    "value", [True, "1.5", math.nan, math.inf, -math.inf, 10**16],
    ids=["bool", "string", "nan", "positive-infinity", "negative-infinity", "unsupported-magnitude"],
)
def test_number_bounds_require_finite_supported_numbers(value):
    for name in ("min_value", "max_value"):
        with pytest.raises(ValidationError):
            QUESTION.validate_python({"type": "number", "title": "Amount", name: value})


def test_survey_configuration_normalizes_title_and_preserves_deadline_instant():
    future = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=2)
    survey = SurveyCreate.model_validate({"title": "  Customer feedback  ", "deadline": future.isoformat()})
    assert survey.title == "Customer feedback"
    assert survey.description == ""
    assert survey.allow_anonymous is False
    assert survey.allow_repeat is False
    assert survey.deadline == future
    cleared = SurveyPatch.model_validate({"expected_revision": 1, "deadline": None})
    assert cleared.model_dump(exclude_unset=True) == {"expected_revision": 1, "deadline": None}


def test_survey_configuration_rejects_invalid_metadata_and_patch_operations():
    invalid_create = [
        {"title": " "},
        {"title": "x" * 201},
        {"title": 42},
        {"title": "T", "description": "x" * 5001},
        {"title": "T", "allow_anonymous": "true"},
        {"title": "T", "allow_repeat": 0},
        {"title": "T", "status": "published"},
        {"title": "T", "owner_id": "0" * 24},
        {"title": "T", "deadline": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
        {"title": "T", "deadline": "2099-12-31T23:59:59"},
    ]
    for payload in invalid_create:
        with pytest.raises(ValidationError):
            SurveyCreate.model_validate(payload)
    invalid_patch = [
        {"title": "T"},
        {"expected_revision": 1},
        {"expected_revision": 0, "title": "T"},
        {"expected_revision": True, "title": "T"},
        {"expected_revision": 1, "title": None},
        {"expected_revision": 1, "description": None},
        {"expected_revision": 1, "allow_anonymous": None},
        {"expected_revision": 1, "allow_repeat": None},
        {"expected_revision": 1, "questions": []},
        {"expected_revision": 1, "owner_id": "0" * 24},
    ]
    for payload in invalid_patch:
        with pytest.raises(ValidationError):
            SurveyPatch.model_validate(payload)


def test_question_order_and_option_ids_reject_duplicates_and_malformed_ids():
    qid = "q_" + "a" * 32
    with pytest.raises(ValidationError):
        QuestionOrder.model_validate({"question_ids": [qid, qid]})
    with pytest.raises(ValidationError):
        QuestionOrder.model_validate({"question_ids": ["not-a-question-id"]})
    option_id = "opt_" + "b" * 32
    for ids in ([option_id, option_id], ["bad-option-id", None]):
        options = [{"label": label, "id": value} for label, value in zip(["A", "B"], ids)]
        with pytest.raises(ValidationError):
            QUESTION.validate_python({"type": "single_choice", "title": "T", "options": options})
