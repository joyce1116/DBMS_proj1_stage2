"""Copy immutable library versions into phase-one survey question snapshots.

Sources stay outside question content so answer validation and jump rules keep
using only the survey's stable local IDs.
"""

from collections.abc import Callable
from copy import deepcopy

from bson import ObjectId
from pydantic import TypeAdapter, ValidationError

from app.surveys.models import QuestionSource, parse_question


def public_sources(sources: list[dict]) -> list[dict]:
    return [
        {**deepcopy(source), "family_id": str(source["family_id"]),
         "version_id": str(source["version_id"])}
        for source in sources
    ]


def validate_sources(questions: list[dict], sources: list[dict]) -> None:
    """Check snapshot provenance without depending on mutable library reads."""
    try:
        TypeAdapter(list[QuestionSource]).validate_python(public_sources(sources))
        by_id = {question["id"]: question for question in questions}
        seen_questions = set()
        for source in sources:
            question_id = source["question_id"]
            if question_id in seen_questions or question_id not in by_id:
                raise ValueError
            seen_questions.add(question_id)
            if not isinstance(source["family_id"], ObjectId) or not isinstance(source["version_id"], ObjectId):
                raise ValueError
            mappings = source["option_map"]
            local_ids = [mapping["local_id"] for mapping in mappings]
            source_ids = [mapping["source_id"] for mapping in mappings]
            option_ids = [option["id"] for option in by_id[question_id].get("options", [])]
            if (len(local_ids) != len(set(local_ids))
                    or len(source_ids) != len(set(source_ids))
                    or len(option_ids) != len(local_ids)
                    or set(option_ids) != set(local_ids)):
                raise ValueError
    except (KeyError, TypeError, ValidationError, ValueError):
        raise ValueError("Question sources must uniquely match existing questions and options") from None


def selected_question(
    version: dict, prepare_question: Callable, previous: dict | None = None,
    previous_source: dict | None = None,
) -> tuple[dict, dict]:
    """Allocate local IDs; preserve mapped options only within the same family."""
    content = parse_question(version["content"]).model_dump()
    old_options = {}
    if previous_source is not None and str(previous_source["family_id"]) == version["family_id"]:
        old_options = {
            mapping["source_id"]: mapping["local_id"]
            for mapping in previous_source["option_map"]
        }
    source_ids = []
    for option in content.get("options", []):
        source_id = option["id"]
        if source_id is None:
            raise ValueError("A saved question version must have stable option IDs")
        source_ids.append(source_id)
        # The original draft question preparer checks preserved IDs against the
        # previous snapshot and allocates every absent ID using its usual path.
        option["id"] = old_options.get(source_id)
    question = prepare_question(content, previous)
    source = {
        "question_id": question["id"],
        "family_id": ObjectId(version["family_id"]),
        "version_id": ObjectId(version["id"]),
        "version": version["version"],
        "option_map": [
            {"local_id": option["id"], "source_id": source_id}
            for option, source_id in zip(question.get("options", []), source_ids, strict=True)
        ],
    }
    return question, source
