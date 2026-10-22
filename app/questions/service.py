"""Reuse phase-one question validation while preserving every saved version."""

from copy import deepcopy
from typing import TYPE_CHECKING
from uuid import uuid4

from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.core.time import mongo_utc_now
from app.questions.repository import QuestionRepository
from app.surveys.models import parse_question

if TYPE_CHECKING:
    from app.questions.access import QuestionAccessService


class QuestionNotFound(Exception):
    pass


class InvalidQuestionChange(Exception):
    pass


class VersionConflict(Exception):
    pass


def question_version_public(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "family_id": str(document["family_id"]),
        "owner_id": str(document["owner_id"]),
        "version": document["version"],
        "parent_id": str(document["parent_id"]) if document["parent_id"] is not None else None,
        "operation": document["operation"],
        "content": deepcopy(document["content"]),
        "created_at": document["created_at"],
    }


class QuestionService:
    MAX_VERSION = 2147483647
    ALLOCATION_ATTEMPTS = 8

    def __init__(
        self, repository: QuestionRepository, access: "QuestionAccessService | None" = None,
    ):
        self.repository = repository
        self.access = access

    @staticmethod
    def _prepare_content(data: dict, parent: dict | None = None) -> dict:
        # Use the exact same validation/defaults as survey editing. This is also
        # a guard for callers other than the HTTP layer, not a second rule set.
        try:
            content = parse_question(data).model_dump()
        except ValidationError:
            raise InvalidQuestionChange("Question configuration is invalid") from None
        allowed_ids = {option["id"] for option in (parent or {}).get("content", {}).get("options", [])}
        for option in content.get("options", []):
            supplied_id = option.get("id")
            if supplied_id is not None and supplied_id not in allowed_ids:
                raise InvalidQuestionChange("Option IDs must belong to the source question version")
            option["id"] = supplied_id if supplied_id is not None else "opt_" + uuid4().hex
        return content

    def _owned(self, version_id: str, owner_id: str) -> dict:
        if not ObjectId.is_valid(version_id):
            raise QuestionNotFound
        document = self.repository.find_owned(ObjectId(version_id), ObjectId(owner_id))
        if document is None:
            raise QuestionNotFound
        return document

    def create(self, owner_id: str, data: dict) -> dict:
        content = self._prepare_content(data)
        version_id = ObjectId()
        document = {
            "_id": version_id,
            "family_id": version_id,
            "owner_id": ObjectId(owner_id),
            "version": 1,
            "parent_id": None,
            "operation": "create",
            "content": content,
            "created_at": mongo_utc_now(),
            "schema_version": 1,
        }
        return question_version_public(self.repository.create(document))

    def detail(self, version_id: str, owner_id: str) -> dict:
        document = (self.access.readable_version(version_id, owner_id)
                    if self.access is not None else self._owned(version_id, owner_id))
        return question_version_public(document)

    @staticmethod
    def _page(documents: list[dict], total: int, offset: int, limit: int) -> dict:
        return {
            "items": [question_version_public(document) for document in documents],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def list_owned(self, owner_id: str, offset: int, limit: int) -> dict:
        # Deliberately lists all versions. Selecting a family's latest version
        # is a separate product decision, not an implicit destructive collapse.
        documents, total = self.repository.list_owned(ObjectId(owner_id), offset, limit)
        return self._page(documents, total, offset, limit)

    def history(self, version_id: str, owner_id: str, offset: int, limit: int) -> dict:
        source = self._owned(version_id, owner_id)
        documents, total = self.repository.history(source["family_id"], source["owner_id"], offset, limit)
        return self._page(documents, total, offset, limit)

    def _append(self, parent: dict, content: dict, operation: str) -> dict:
        document = {
            "_id": ObjectId(),
            "family_id": parent["family_id"],
            "owner_id": parent["owner_id"],
            "parent_id": parent["_id"],
            "operation": operation,
            "content": deepcopy(content),
            "created_at": mongo_utc_now(),
            "schema_version": 1,
        }
        for _attempt in range(self.ALLOCATION_ATTEMPTS):
            version = self.repository.next_version(parent["family_id"], parent["owner_id"])
            if version > self.MAX_VERSION:
                raise VersionConflict
            document["version"] = version
            try:
                saved = self.repository.create(document)
            except DuplicateKeyError as exc:
                # Retry only a racing family sequence allocation. In particular,
                # an unrelated duplicate _id or future unique index must surface
                # through the application's existing database error handling.
                if not exc.details or exc.details.get("keyPattern") != {"family_id": 1, "version": 1}:
                    raise
                continue
            return question_version_public(saved)
        raise VersionConflict

    def revise(self, version_id: str, owner_id: str, data: dict) -> dict:
        parent = self._owned(version_id, owner_id)
        return self._append(parent, self._prepare_content(data, parent), "revise")

    def restore(self, version_id: str, owner_id: str) -> dict:
        parent = self._owned(version_id, owner_id)
        # Historical content is copied verbatim, including its stable option
        # IDs. Recovery never overwrites the source or any newer version.
        return self._append(parent, parent["content"], "restore")
