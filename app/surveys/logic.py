"""Configuration-driven, forward-only navigation shared by preview and submission."""

import operator

from pydantic import TypeAdapter, ValidationError

from app.surveys.models import JumpRule
from app.surveys.validation import InvalidAnswer, validate_answer


class InvalidRules(ValueError):
    pass


def validate_rule_set(questions: list[dict], rules: list[dict]) -> None:
    try:
        TypeAdapter(list[JumpRule]).validate_python(rules)
    except ValidationError:
        raise InvalidRules("Invalid jump rule structure") from None
    if len(rules) > 200:
        raise InvalidRules("At most 200 jump rules are allowed")
    positions = {q["id"]: i for i, q in enumerate(questions)}
    by_id = {q["id"]: q for q in questions}
    for rule in rules:
        source, target = rule["source_id"], rule["target_id"]
        if source not in positions or (target != "end" and target not in positions):
            raise InvalidRules("Rule source and target must belong to this survey")
        if target != "end" and positions[target] <= positions[source]:
            raise InvalidRules("Jump targets must be after the source question")
        question = by_id[source]
        if rule["kind"] == "number":
            if question["type"] != "number":
                raise InvalidRules("Numeric rules require a numeric source question")
        else:
            if question["type"] not in ("single_choice", "multiple_choice"):
                raise InvalidRules("Choice rules require a choice source question")
            if question["type"] == "single_choice" and (
                rule["operator"] != "eq" or len(rule["option_ids"]) != 1
            ):
                raise InvalidRules("Single-choice rules require eq and one option")
            if not set(rule["option_ids"]) <= {o["id"] for o in question["options"]}:
                raise InvalidRules("Rule options must belong to its source question")


def _matches(rule: dict, value: object) -> bool:
    if value is None:
        return False
    if rule["kind"] == "number":
        operations = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt,
                      "gte": operator.ge, "lt": operator.lt, "lte": operator.le}
        return operations[rule["operator"]](value, rule["value"])
    selected = set(value) if isinstance(value, list) else {value}
    expected = set(rule["option_ids"])
    if rule["operator"] == "eq":
        return selected == expected
    if rule["operator"] == "contains_all":
        return expected <= selected
    return bool(expected & selected)


def evaluate(questions: list[dict], rules: list[dict], answers: dict, *, complete: bool) -> dict:
    """Derive the path on the server. Never accept answers outside that path.

    Preview stops before the first absent answer (optional questions included).
    Complete mode treats absent values as null and enforces visited requiredness.
    """
    positions = {q["id"]: i for i, q in enumerate(questions)}
    for question_id in answers:
        if question_id not in positions:
            raise InvalidAnswer(question_id, "unknown", "答案包含未知题目")
    by_source: dict[str, list] = {}
    for rule in rules:
        by_source.setdefault(rule["source_id"], []).append(rule)
    path, normalized = [], []
    position = 0
    pending = None
    while position < len(questions):
        question = questions[position]
        question_id = question["id"]
        if not complete and question_id not in answers:
            pending = question_id
            break
        value = validate_answer(question, answers.get(question_id))
        path.append(question_id)
        normalized.append({"question_id": question_id, "value": value})
        target = next((r["target_id"] for r in by_source.get(question_id, [])
                       if _matches(r, value)), None)
        if target == "end":
            position = len(questions)
        elif target is None:
            position += 1
        else:
            following = positions[target]
            if following <= position:
                raise InvalidRules("Jump configuration does not move forward")
            position = following
    for question_id in answers.keys() - set(path):
        raise InvalidAnswer(question_id, "off_path", "不能提交未经过题目的答案，请按新路径移除旧答案")
    return {"path": path, "answers": normalized, "completed": pending is None,
            "next_question_id": pending}
