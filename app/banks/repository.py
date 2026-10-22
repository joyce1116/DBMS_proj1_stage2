"""Persistence and bounded joins for banks, membership, and read grants."""

from bson import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database


class BankRepository:
    def __init__(self, db: Database):
        self.banks = db.question_banks
        self.entries = db.question_bank_entries
        self.grants = db.question_grants
        self.versions = db.question_versions

    def create(self, document: dict) -> dict:
        self.banks.insert_one(document)
        return document

    def find_active(self, bank_id: ObjectId) -> dict | None:
        return self.banks.find_one({"_id": bank_id, "archived": False})

    def has_read_grant(self, bank: dict, user_id: ObjectId) -> bool:
        return self.grants.find_one({
            "resource_type": "bank", "resource_id": bank["_id"],
            "owner_id": bank["owner_id"], "grantee_id": user_id,
        }, {"_id": 1}) is not None

    def list_owned(self, user_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        query = {"owner_id": user_id, "archived": False}
        documents = list(self.banks.find(query).sort([
            ("created_at", DESCENDING), ("_id", DESCENDING),
        ]).skip(offset).limit(limit))
        return documents, self.banks.count_documents(query)

    def list_shared(self, user_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        # Starting from the indexed grants avoids materializing every shared ID
        # in Python or growing an unbounded $in array. Only a page leaves Mongo.
        result = next(self.grants.aggregate([
            {"$match": {"resource_type": "bank", "grantee_id": user_id}},
            {"$lookup": {
                "from": self.banks.name, "localField": "resource_id",
                "foreignField": "_id", "as": "bank",
            }},
            {"$unwind": "$bank"},
            {"$match": {
                "bank.archived": False, "bank.owner_id": {"$ne": user_id},
                "$expr": {"$eq": ["$owner_id", "$bank.owner_id"]},
            }},
            {"$replaceRoot": {"newRoot": "$bank"}},
            {"$sort": {"created_at": -1, "_id": -1}},
            {"$facet": {
                "items": [{"$skip": offset}, {"$limit": limit}],
                "count": [{"$count": "total"}],
            }},
        ]))
        return result["items"], result["count"][0]["total"] if result["count"] else 0

    def update_metadata(
        self, bank_id: ObjectId, owner_id: ObjectId, revision: int, changes: dict,
    ) -> dict | None:
        return self.banks.find_one_and_update(
            {"_id": bank_id, "owner_id": owner_id, "archived": False, "revision": revision},
            {"$set": changes, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )

    def find_owned_version(self, version_id: ObjectId, owner_id: ObjectId) -> dict | None:
        return self.versions.find_one({"_id": version_id, "owner_id": owner_id})

    def find_versions(self, version_ids: list[ObjectId]) -> dict[ObjectId, dict]:
        return {
            document["_id"]: document
            for document in self.versions.find({"_id": {"$in": version_ids}})
        }

    def create_entry(self, document: dict) -> dict:
        self.entries.insert_one(document)
        return document

    def find_entry(self, bank_id: ObjectId, family_id: ObjectId) -> dict | None:
        return self.entries.find_one({"bank_id": bank_id, "family_id": family_id})

    def list_entries(self, bank_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        query = {"bank_id": bank_id}
        documents = list(self.entries.find(query).sort([
            ("created_at", DESCENDING), ("_id", DESCENDING),
        ]).skip(offset).limit(limit))
        return documents, self.entries.count_documents(query)

    def update_entry(
        self, bank_id: ObjectId, family_id: ObjectId, expected_version_id: ObjectId,
        version_id: ObjectId, updated_at,
    ) -> dict | None:
        return self.entries.find_one_and_update(
            {"bank_id": bank_id, "family_id": family_id, "version_id": expected_version_id},
            {"$set": {"version_id": version_id, "updated_at": updated_at}},
            return_document=ReturnDocument.AFTER,
        )

    def delete_entry(
        self, bank_id: ObjectId, family_id: ObjectId, expected_version_id: ObjectId,
    ) -> bool:
        result = self.entries.delete_one({
            "bank_id": bank_id, "family_id": family_id, "version_id": expected_version_id,
        })
        return result.deleted_count == 1
