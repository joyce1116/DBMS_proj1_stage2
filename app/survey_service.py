"""Draft management only; no publication, answering or jump engine yet."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId

from app.services import mongo_utc_now
from app.survey_repository import SurveyRepository


class SurveyNotFound(Exception):
    pass


class RevisionConflict(Exception):
    pass


class InvalidSurveyChange(Exception):
    pass


def bson_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def survey_public(document: dict, *, include_questions: bool = True) -> dict:
    public = {
        "id": str(document["_id"]),
        "owner_id": str(document["owner_id"]),
        "title": document["title"],
        "description": document["description"],
        "allow_anonymous": document["allow_anonymous"],
        "allow_repeat": document["allow_repeat"],
        "deadline": document["deadline"],
        "status": document["status"],
        "revision": document["revision"],
        "question_count": len(document["questions"]),
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
    }
    if include_questions:
        public["questions"] = document["questions"]
    return public


class SurveyService:
    def __init__(self, repository: SurveyRepository):
        self.repository = repository

    def create(self, owner_id: str, data: dict) -> dict:
        now = mongo_utc_now()
        document = {
            "_id": ObjectId(),
            "owner_id": ObjectId(owner_id),
            **data,
            "deadline": bson_datetime(data["deadline"]),
            "status": "draft",
            "revision": 1,
            "schema_version": 1,
            "questions": [],
            "created_at": now,
            "updated_at": now,
        }
        return survey_public(self.repository.create(document))

    def list_owned(self, owner_id: str, offset: int, limit: int) -> dict:
        documents, total = self.repository.list_owned(ObjectId(owner_id), offset, limit)
        return {
            "items": [survey_public(document, include_questions=False) for document in documents],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def _owned(self, survey_id: str, owner_id: str) -> dict:
        if not ObjectId.is_valid(survey_id):
            raise SurveyNotFound
        document = self.repository.find_owned(ObjectId(survey_id), ObjectId(owner_id))
        if document is None:
            # Do not disclose another owner's survey existence.
            raise SurveyNotFound
        return document

    def detail(self, survey_id: str, owner_id: str) -> dict:
        return survey_public(self._owned(survey_id, owner_id))

    def _editable(self, survey_id: str, owner_id: str, revision: int) -> dict:
        document = self._owned(survey_id, owner_id)
        if document["revision"] != revision or document["status"] != "draft":
            raise RevisionConflict
        return document

    def _save(self, document: dict, changes: dict) -> dict:
        changes = {**changes, "updated_at": mongo_utc_now()}
        result = self.repository.update_draft(
            document["_id"], document["owner_id"], document["revision"], changes
        )
        if result is None:
            raise RevisionConflict
        return survey_public(result)

    def patch(self, survey_id: str, owner_id: str, revision: int, changes: dict) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        if "deadline" in changes:
            changes["deadline"] = bson_datetime(changes["deadline"])
        return self._save(document, changes)

    @staticmethod
    def _prepare_question(data: dict, previous: dict | None = None) -> dict:
        question = deepcopy(data)
        question["id"] = previous["id"] if previous else "q_" + uuid4().hex
        allowed_ids = {option["id"] for option in (previous or {}).get("options", [])}
        for option in question.get("options", []):
            supplied_id = option.get("id")
            if supplied_id is not None and supplied_id not in allowed_ids:
                raise InvalidSurveyChange("Option IDs must belong to the question being edited")
            option["id"] = supplied_id if supplied_id is not None else "opt_" + uuid4().hex
        return question

    def add_question(self, survey_id: str, owner_id: str, revision: int, data: dict) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        if len(document["questions"]) >= 100:
            raise InvalidSurveyChange("A survey can contain at most 100 questions")
        question = self._prepare_question(data)
        return self._save(document, {"questions": [*document["questions"], question]})

    def replace_question(
        self, survey_id: str, owner_id: str, question_id: str, revision: int, data: dict
    ) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        for position, previous in enumerate(document["questions"]):
            if previous["id"] == question_id:
                questions = deepcopy(document["questions"])
                questions[position] = self._prepare_question(data, previous)
                return self._save(document, {"questions": questions})
        raise SurveyNotFound

    def delete_question(
        self, survey_id: str, owner_id: str, question_id: str, revision: int
    ) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        questions = [question for question in document["questions"] if question["id"] != question_id]
        if len(questions) == len(document["questions"]):
            raise SurveyNotFound
        return self._save(document, {"questions": questions})

    def reorder_questions(
        self, survey_id: str, owner_id: str, revision: int, question_ids: list[str]
    ) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        by_id = {question["id"]: question for question in document["questions"]}
        if len(question_ids) != len(by_id) or set(question_ids) != set(by_id):
            raise InvalidSurveyChange("Question order must contain every existing question exactly once")
        return self._save(document, {"questions": [by_id[question_id] for question_id in question_ids]})
