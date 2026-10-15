"""Branch semantics are tested independently of transport and persistence."""

import pytest
from pydantic import ValidationError

from app.surveys.logic import InvalidRules, evaluate, validate_rule_set
from app.surveys.models import SubmissionInput
from app.surveys.validation import InvalidAnswer


def qid(n):
    return "q_" + f"{n:032x}"


def oid(n):
    return "opt_" + f"{n:032x}"


@pytest.fixture
def questions():
    return [
        {"id": qid(1), "type": "single_choice", "title": "分支", "required": True,
         "options": [{"id": oid(1), "label": "A"}, {"id": oid(2), "label": "B"}]},
        {"id": qid(2), "type": "multiple_choice", "title": "多选", "required": True,
         "options": [{"id": oid(3), "label": "C"}, {"id": oid(4), "label": "D"}],
         "min_selections": 1, "max_selections": 2},
        {"id": qid(3), "type": "number", "title": "数字", "required": False,
         "min_value": 0, "max_value": 100, "integer_only": False},
        {"id": qid(4), "type": "text", "title": "必答文本", "required": True,
         "min_length": 1, "max_length": 20},
    ]


def choice(source=1, target=3, operator="eq", options=None):
    return {"kind": "choice", "source_id": qid(source), "target_id": qid(target) if target != "end" else target,
            "operator": operator, "option_ids": options or [oid(1)]}


def number(operator="gte", value=18):
    return {"kind": "number", "source_id": qid(3), "target_id": "end", "operator": operator, "value": value}


def test_preview_and_final_submission_use_the_same_skipped_required_path(questions):
    rules = [choice(), number()]
    validate_rule_set(questions, rules)
    preview = evaluate(questions, rules, {}, complete=False)
    assert preview == {"path": [], "answers": [], "next_question_id": qid(1), "completed": False}
    preview = evaluate(questions, rules, {qid(1): oid(1)}, complete=False)
    assert preview["path"] == [qid(1)] and preview["next_question_id"] == qid(3)
    answers = {qid(1): oid(1), qid(3): 18}
    final = evaluate(questions, rules, answers, complete=True)
    assert final == evaluate(questions, rules, answers, complete=False)
    assert final["path"] == [qid(1), qid(3)] and final["completed"]


def test_first_matching_rule_wins_and_default_is_next(questions):
    rules = [choice(target=3), choice(target="end")]
    assert evaluate(questions, rules, {qid(1): oid(1)}, complete=False)["next_question_id"] == qid(3)
    assert evaluate(questions, list(reversed(rules)), {qid(1): oid(1)}, complete=False)["completed"]
    assert evaluate(questions, rules, {qid(1): oid(2)}, complete=False)["next_question_id"] == qid(2)


@pytest.mark.parametrize("op,value,matches", [
    ("eq", 18, True), ("eq", 19, False), ("ne", 18, False), ("ne", 19, True),
    ("gt", 18, False), ("gt", 19, True), ("gte", 18, True), ("gte", 17, False),
    ("lt", 18, False), ("lt", 17, True), ("lte", 18, True), ("lte", 19, False),
    ("ne", None, False),
])
def test_numeric_boundaries_and_unanswered_never_matches(questions, op, value, matches):
    result = evaluate(questions, [choice(), number(op)], {qid(1): oid(1), qid(3): value}, complete=False)
    assert result["completed"] is matches
    assert result["next_question_id"] == (None if matches else qid(4))


@pytest.mark.parametrize("op,expected,selected,matches", [
    ("eq", [3], [3], True), ("eq", [3], [3, 4], False), ("eq", [3, 4], [4, 3], True),
    ("contains_any", [3, 4], [4], True), ("contains_any", [3], [4], False),
    ("contains_all", [3, 4], [4], False), ("contains_all", [3], [4, 3], True),
])
def test_multi_choice_set_conditions(questions, op, expected, selected, matches):
    rule = choice(2, "end", op, [oid(n) for n in expected])
    validate_rule_set(questions, [rule])
    result = evaluate(questions, [rule], {qid(1): oid(2), qid(2): [oid(n) for n in selected]}, complete=False)
    assert result["completed"] is matches
    assert result["answers"][1]["value"] == sorted(oid(n) for n in selected)


@pytest.mark.parametrize("rule", [
    choice(source=99), choice(target=99), choice(target=1), choice(source=2, target=1, options=[oid(3)]),
    choice(options=[oid(3)]), choice(operator="contains_any"), choice(options=[oid(1), oid(2)]),
    choice(source=4, target="end"), {**number(), "source_id": qid(1)},
    {**choice(), "option_ids": [oid(1), oid(1)]}, {**number(), "value": True},
    {**number(), "value": float("inf")}, {**number(), "extra": "forbidden"},
])
def test_invalid_references_backward_jumps_and_rule_types(questions, rule):
    with pytest.raises(InvalidRules):
        validate_rule_set(questions, [rule])


@pytest.mark.parametrize("answers,code", [
    ({}, "required"), ({qid(1): oid(1), qid(3): 18, qid(2): [oid(3)]}, "off_path"),
    ({qid(1): oid(1), qid(3): 18, qid(4): "旧答案"}, "off_path"),
    ({qid(99): "伪造"}, "unknown"), ({qid(1): oid(1), qid(3): "18"}, "type"),
])
def test_missing_forged_and_stale_answers_are_rejected(questions, answers, code):
    with pytest.raises(InvalidAnswer) as error:
        evaluate(questions, [choice(), number()], answers, complete=True)
    assert error.value.code == code


def test_optional_absence_is_null_in_final_but_preview_requires_explicit_skip(questions):
    answers = {qid(1): oid(1), qid(4): "有效"}
    final = evaluate(questions, [choice()], answers, complete=True)
    assert final["answers"][1] == {"question_id": qid(3), "value": None}
    assert evaluate(questions, [choice()], {qid(1): oid(1)}, complete=False)["next_question_id"] == qid(3)


@pytest.mark.parametrize("change", [
    {"request_id": "short"}, {"request_id": "x" * 65}, {"anonymous": "true"},
    {"survey_revision": True}, {"survey_revision": 0}, {"survey_revision": 10**100},
    {"answers": {"bad": 1}}, {"owner_id": "forged"},
])
def test_submission_contract_is_strict(change):
    payload = {"request_id": "request_123", "survey_revision": 1, "answers": {qid(1): oid(1)}}
    with pytest.raises(ValidationError):
        SubmissionInput.model_validate({**payload, **change})
