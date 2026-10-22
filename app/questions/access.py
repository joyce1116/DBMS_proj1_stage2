"""Read/use authorization is independent of immutable content and ownership."""

from bson import ObjectId
from pymongo.database import Database

from app.auth.security import username_key
from app.core.time import mongo_utc_now
from app.questions.access_repository import QuestionAccessRepository
from app.questions.service import QuestionNotFound, question_version_public


class SharingResourceNotFound(Exception):
    pass


class ShareRecipientNotFound(Exception):
    pass


class InvalidGrant(Exception):
    pass


def grant_public(document: dict, username: str | None = None) -> dict:
    return {
        "id": str(document["_id"]), "resource_type": document["resource_type"],
        "resource_id": str(document["resource_id"]), "owner_id": str(document["owner_id"]),
        "grantee_id": str(document["grantee_id"]),
        "username": document["username"] if username is None else username,
        "created_at": document["created_at"],
    }


class QuestionAccessService:
    def __init__(self, db: Database):
        self.repository = QuestionAccessRepository(db)

    def readable_version(self, version_id: str, user_id: str) -> dict:
        """Return the raw MongoDB version, or hide inaccessible/missing IDs."""
        if not ObjectId.is_valid(version_id):
            raise QuestionNotFound
        version = self.repository.find_version(ObjectId(version_id))
        if version is None:
            raise QuestionNotFound
        user = ObjectId(user_id)
        if (version["owner_id"] == user
                or self.repository.has_direct_grant(version, user)
                or self.repository.has_bank_grant(version, user)):
            return version
        raise QuestionNotFound

    def _owned_resource(self, resource_type: str, resource_id: str, owner_id: str) -> dict:
        document = None
        if ObjectId.is_valid(resource_id):
            document = self.repository.owned_resource(resource_type, ObjectId(resource_id), ObjectId(owner_id))
        if document is None:
            if resource_type == "version":
                raise QuestionNotFound
            raise SharingResourceNotFound
        return document

    def grant(self, resource_type: str, resource_id: str, owner_id: str, username: str) -> tuple[dict, bool]:
        resource = self._owned_resource(resource_type, resource_id, owner_id)
        recipient = self.repository.user_by_key(username_key(username))
        if recipient is None:
            raise ShareRecipientNotFound
        if recipient["_id"] == resource["owner_id"]:
            raise InvalidGrant("The owner already has access and cannot receive a sharing grant")
        saved, created = self.repository.save_grant({
            "_id": ObjectId(), "resource_type": resource_type, "resource_id": resource["_id"],
            "owner_id": resource["owner_id"], "grantee_id": recipient["_id"],
            "created_at": mongo_utc_now(), "schema_version": 1,
        })
        return grant_public(saved, recipient["username"]), created

    def grants(self, resource_type: str, resource_id: str, owner_id: str, offset: int, limit: int) -> dict:
        resource = self._owned_resource(resource_type, resource_id, owner_id)
        documents, total = self.repository.list_grants(
            resource_type, resource["_id"], resource["owner_id"], offset, limit
        )
        return {"items": [grant_public(row) for row in documents], "total": total, "offset": offset, "limit": limit}

    def revoke(self, resource_type: str, resource_id: str, owner_id: str, grantee_id: str) -> None:
        resource = self._owned_resource(resource_type, resource_id, owner_id)
        if not ObjectId.is_valid(grantee_id):
            raise ShareRecipientNotFound
        self.repository.revoke(resource_type, resource["_id"], resource["owner_id"], ObjectId(grantee_id))

    def shared(self, user_id: str, offset: int, limit: int) -> dict:
        documents, total = self.repository.list_shared(ObjectId(user_id), offset, limit)
        return {
            "items": [question_version_public(row) for row in documents],
            "total": total, "offset": offset, "limit": limit,
        }
