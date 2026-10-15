"""Reusable answer validation; no HTTP, persistence or path/jump decisions."""

from math import isfinite


class InvalidAnswer(ValueError):
    def __init__(self, question_id: str, code: str, message: str):
        super().__init__(message)
        self.question_id = question_id
        self.code = code


def validate_answer(question: dict, value: object) -> str | list[str] | int | float | None:
    """Validate one visited question; an optional unanswered value becomes None.

    Final submission must separately determine which questions were visited and
    call this function for those questions. This function never trusts a client
    to decide requiredness or supply validation constraints.
    """
    def reject(code: str, message: str):
        raise InvalidAnswer(question["id"], code, message)

    kind = question["type"]
    missing = value is None
    if kind in ("text", "single_choice") and isinstance(value, str) and not value.strip():
        missing = True
    if kind == "multiple_choice" and isinstance(value, list) and not value:
        missing = True
    if missing:
        if question["required"]:
            reject("required", "该题必须回答")
        return None

    if kind == "single_choice":
        if not isinstance(value, str):
            reject("type", "单选答案必须是一个选项 ID")
        if value not in {option["id"] for option in question["options"]}:
            reject("option", "答案包含不属于本题的选项")
        return value

    if kind == "multiple_choice":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            reject("type", "多选答案必须是选项 ID 数组")
        if len(value) != len(set(value)):
            reject("duplicate", "不能重复选择同一选项")
        options = [option["id"] for option in question["options"]]
        if any(item not in options for item in value):
            reject("option", "答案包含不属于本题的选项")
        minimum = max(int(question["required"]), question["min_selections"])
        maximum = question["max_selections"]
        maximum = len(options) if maximum is None else maximum
        if not minimum <= len(value) <= maximum:
            reject("count", f"请选择 {minimum} 至 {maximum} 个选项")
        selected = set(value)
        return [option_id for option_id in options if option_id in selected]

    if kind == "text":
        if not isinstance(value, str):
            reject("type", "文本答案必须是字符串")
        if not question["min_length"] <= len(value) <= question["max_length"]:
            reject("length", f"文本长度须在 {question['min_length']} 至 {question['max_length']} 之间")
        return value

    if kind == "number":
        # bool is an int subclass; numeric strings and non-finite floats are not
        # accepted. Check magnitude before isfinite to handle huge ints safely.
        if type(value) not in (int, float):
            reject("type", "数字答案必须是 JSON 数值，不能是布尔值或字符串")
        if abs(value) > 10**15 or not isfinite(value):
            reject("number", "数字必须有限且在 ±10^15 范围内")
        if question["integer_only"] and isinstance(value, float) and not value.is_integer():
            reject("integer", "该题必须填写整数")
        minimum, maximum = question["min_value"], question["max_value"]
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            reject("range", "数字不在本题允许的范围内")
        return int(value) if question["integer_only"] else value

    reject("question_type", "不支持的题目类型")
