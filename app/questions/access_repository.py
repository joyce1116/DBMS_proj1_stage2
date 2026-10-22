"""Grant persistence and database-side discovery of exactly readable versions."""

from bson import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError


class QuestionAccessRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def grant_query(resource_type: str, resource_id: ObjectId, grantee_id: ObjectId) -> dict:
        return {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "grantee_id": grantee_id,
        }

    def find_version(self, version_id: ObjectId) -> dict | None:
        return self.db.question_versions.find_one({"_id": version_id})

    def owned_resource(self, resource_type: str, resource_id: ObjectId, owner_id: ObjectId) -> dict | None:
        query = {"_id": resource_id, "owner_id": owner_id}
        collection = self.db.question_versions
        if resource_type == "bank":
            collection = self.db.question_banks
            query["archived"] = False
        return collection.find_one(query)

    def has_direct_grant(self, version: dict, grantee_id: ObjectId) -> bool:
        query = self.grant_query("version", version["_id"], grantee_id)
        query["owner_id"] = version["owner_id"]
        return self.db.question_grants.find_one(query, {"_id": 1}) is not None

    def has_bank_grant(self, version: dict, grantee_id: ObjectId) -> bool:
        # Resolve both the current entry and its live bank. A leftover grant or
        # archived bank alone cannot make a version readable.
        pipeline = [
            {"$match": {"version_id": version["_id"], "family_id": version["family_id"]}},
            {"$lookup": {
                "from": "question_banks", "let": {"bank_id": "$bank_id"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$bank_id"]},
                    {"$eq": ["$owner_id", version["owner_id"]]},
                    {"$eq": ["$archived", False]},
                ]}}}], "as": "bank",
            }},
            {"$unwind": "$bank"},
            {"$lookup": {
                "from": "question_grants", "let": {"bank_id": "$bank._id"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$resource_type", "bank"]},
                    {"$eq": ["$resource_id", "$$bank_id"]},
                    {"$eq": ["$owner_id", version["owner_id"]]},
                    {"$eq": ["$grantee_id", grantee_id]},
                ]}}}, {"$limit": 1}], "as": "grant",
            }},
            {"$match": {"grant.0": {"$exists": True}}},
            {"$limit": 1}, {"$project": {"_id": 1}},
        ]
        return next(self.db.question_bank_entries.aggregate(pipeline), None) is not None

    def user_by_key(self, username_key: str) -> dict | None:
        return self.db.users.find_one({"username_key": username_key}, {"username": 1})

    def save_grant(self, document: dict) -> tuple[dict, bool]:
        query = self.grant_query(
            document["resource_type"], document["resource_id"], document["grantee_id"]
        )
        query["owner_id"] = document["owner_id"]
        try:
            saved = self.db.question_grants.find_one_and_update(
                query, {"$setOnInsert": document}, upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            # Handle only the expected race between identical grants. Other
            # unique failures still use the application's database error path.
            expected = {"resource_type": 1, "resource_id": 1, "grantee_id": 1}
            if not exc.details or exc.details.get("keyPattern") != expected:
                raise
            saved = self.db.question_grants.find_one(query)
            if saved is None:
                raise
        return saved, saved["_id"] == document["_id"]

    def list_grants(
        self, resource_type: str, resource_id: ObjectId, owner_id: ObjectId,
        offset: int, limit: int,
    ) -> tuple[list[dict], int]:
        query = {"resource_type": resource_type, "resource_id": resource_id, "owner_id": owner_id}
        rows = list(self.db.question_grants.aggregate([
            {"$match": query},
            {"$sort": {"created_at": DESCENDING, "_id": DESCENDING}},
            {"$skip": offset}, {"$limit": limit},
            {"$lookup": {
                "from": "users", "localField": "grantee_id", "foreignField": "_id", "as": "user",
            }},
            {"$unwind": "$user"}, {"$set": {"username": "$user.username"}},
            {"$unset": "user"},
        ]))
        return rows, self.db.question_grants.count_documents(query)

    def revoke(self, resource_type: str, resource_id: ObjectId, owner_id: ObjectId, grantee_id: ObjectId):
        self.db.question_grants.delete_one({
            **self.grant_query(resource_type, resource_id, grantee_id), "owner_id": owner_id,
        })

    def list_shared(self, grantee_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        # Begin with this user's grants, rather than collecting every version ID
        # in Python. Both branches validate live resources and converge before
        # deduplication, sorting and pagination inside MongoDB.
        bank_pipeline = [
            {"$match": {"grantee_id": grantee_id, "resource_type": "bank"}},
            {"$lookup": {
                "from": "question_banks", "let": {"id": "$resource_id", "owner": "$owner_id"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$id"]}, {"$eq": ["$owner_id", "$$owner"]},
                    {"$eq": ["$archived", False]}, {"$ne": ["$owner_id", grantee_id]},
                ]}}}], "as": "bank",
            }},
            {"$unwind": "$bank"},
            {"$lookup": {
                "from": "question_bank_entries", "localField": "bank._id",
                "foreignField": "bank_id", "as": "entry",
            }},
            {"$unwind": "$entry"},
            {"$lookup": {
                "from": "question_versions", "let": {
                    "id": "$entry.version_id", "family": "$entry.family_id", "owner": "$bank.owner_id",
                },
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$id"]}, {"$eq": ["$family_id", "$$family"]},
                    {"$eq": ["$owner_id", "$$owner"]},
                ]}}}], "as": "version",
            }},
            {"$unwind": "$version"}, {"$replaceWith": "$version"},
        ]
        pipeline = [
            {"$match": {"grantee_id": grantee_id, "resource_type": "version"}},
            {"$lookup": {
                "from": "question_versions", "let": {"id": "$resource_id", "owner": "$owner_id"},
                "pipeline": [{"$match": {"$expr": {"$and": [
                    {"$eq": ["$_id", "$$id"]}, {"$eq": ["$owner_id", "$$owner"]},
                    {"$ne": ["$owner_id", grantee_id]},
                ]}}}], "as": "version",
            }},
            {"$unwind": "$version"}, {"$replaceWith": "$version"},
            {"$unionWith": {"coll": "question_grants", "pipeline": bank_pipeline}},
            {"$group": {"_id": "$_id", "document": {"$first": "$$ROOT"}}},
            {"$replaceWith": "$document"},
            {"$sort": {"created_at": DESCENDING, "_id": DESCENDING}},
            {"$facet": {
                "items": [{"$skip": offset}, {"$limit": limit}],
                "total": [{"$count": "count"}],
            }},
        ]
        page = next(self.db.question_grants.aggregate(pipeline))
        return page["items"], page["total"][0]["count"] if page["total"] else 0
