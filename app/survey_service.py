"""Draft editing and publication lifecycle; no answer or jump engine yet."""

from copy import deepcopy
from datetime import datetime, timezone
import re
import secrets
from uuid import uuid4

from bson import ObjectId
from pydantic import TypeAdapter, ValidationError
from pymongo.errors import DuplicateKeyError

from app.services import mongo_utc_now
from app.survey_repository import SurveyRepository
from app.survey_models import QuestionPublic


class SurveyNotFound(Exception):
    pass


class RevisionConflict(Exception):
    pass


class InvalidSurveyChange(Exception):
    pass


class SurveyUnavailable(Exception):
    pass


def survey_availability(document: dict, now: datetime | None = None) -> str:
    if document["status"] == "draft":
        return "draft"
    if document["status"] == "closed":
        return "closed"
    checked_at = mongo_utc_now() if now is None else now
    if document["deadline"] is not None and document["deadline"] <= checked_at:
        return "expired"
    return "open"


def bson_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    value = value.astimezone(timezone.utc)
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def survey_public(document: dict, *, include_questions: bool = True) -> dict:
    availability = survey_availability(document)
    public = {
        "id": str(document["_id"]),
        "owner_id": str(document["owner_id"]),
        "title": document["title"],
        "description": document["description"],
        "allow_anonymous": document["allow_anonymous"],
        "allow_repeat": document["allow_repeat"],
        "deadline": document["deadline"],
        "status": document["status"],
        "availability": availability,
        "is_open": availability == "open",
        "share_id": document["share_id"],
        "share_url": "/survey/" + document["share_id"] if document["share_id"] else None,
        "published_at": document["published_at"],
        "closed_at": document["closed_at"],
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
            "schema_version": 2,
            "share_id": None,
            "published_at": None,
            "closed_at": None,
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

    def publish(self, survey_id: str, owner_id: str, revision: int) -> dict:
        document = self._editable(survey_id, owner_id, revision)
        now = mongo_utc_now()
        if not document["questions"]:
            raise InvalidSurveyChange("Add at least one question before publishing")
        if document["deadline"] is not None and document["deadline"] <= now:
            raise InvalidSurveyChange("Deadline must be in the future when publishing")
        try:
            TypeAdapter(list[QuestionPublic]).validate_python(document["questions"])
        except ValidationError:
            raise InvalidSurveyChange("Question configuration is invalid; repair the draft first") from None
        question_ids = [question["id"] for question in document["questions"]]
        option_ids = [option["id"] for question in document["questions"] for option in question.get("options", [])]
        if len(set(question_ids)) != len(question_ids) or len(set(option_ids)) != len(option_ids):
            raise InvalidSurveyChange("Question and option IDs must be unique before publishing")
        for attempt in range(3):
            try:
                result = self.repository.transition(
                    document["_id"], document["owner_id"], revision, "draft",
                    {"status": "published", "share_id": secrets.token_urlsafe(24),
                     "published_at": now, "updated_at": now},
                )
            except DuplicateKeyError as exc:
                if not exc.details or exc.details.get("keyPattern") != {"share_id": 1} or attempt == 2:
                    raise
                continue
            if result is None:
                raise RevisionConflict
            return survey_public(result)
        raise AssertionError("Unreachable token allocation branch")

    def close(self, survey_id: str, owner_id: str, revision: int) -> dict:
        document = self._owned(survey_id, owner_id)
        if document["revision"] != revision or document["status"] != "published":
            raise RevisionConflict
        now = mongo_utc_now()
        result = self.repository.transition(
            document["_id"], document["owner_id"], revision, "published",
            {"status": "closed", "closed_at": now, "updated_at": now},
        )
        if result is None:
            raise RevisionConflict
        return survey_public(result)

    def shared(self, share_id: str) -> dict:
        if re.fullmatch(r"[A-Za-z0-9_-]{32}", share_id) is None:
            raise SurveyNotFound
        document = self.repository.find_shared(share_id)
        if document is None or document["status"] == "draft":
            raise SurveyNotFound
        if survey_availability(document) != "open":
            raise SurveyUnavailable
        # Explicit allowlist: sharing must never expose owner/session details.
        return {
            "id": str(document["_id"]), "title": document["title"],
            "description": document["description"],
            "allow_anonymous": document["allow_anonymous"],
            "allow_repeat": document["allow_repeat"], "deadline": document["deadline"],
            "revision": document["revision"], "questions": document["questions"],
        }
