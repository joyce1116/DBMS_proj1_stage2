"""Owner-scoped reads and insert-only persistence for question versions."""

from bson import ObjectId
from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database


class QuestionRepository:
    def __init__(self, db: Database):
        self.collection = db.question_versions

    def create(self, document: dict) -> dict:
        self.collection.insert_one(document)
        return document

    def find_owned(self, version_id: ObjectId, owner_id: ObjectId) -> dict | None:
        return self.collection.find_one({"_id": version_id, "owner_id": owner_id})

    def list_owned(self, owner_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        query = {"owner_id": owner_id}
        documents = list(
            self.collection.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return documents, self.collection.count_documents(query)

    def history(
        self, family_id: ObjectId, owner_id: ObjectId, offset: int, limit: int
    ) -> tuple[list[dict], int]:
        query = {"family_id": family_id, "owner_id": owner_id}
        documents = list(
            self.collection.find(query)
            .sort([("version", ASCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return documents, self.collection.count_documents(query)

    def next_version(self, family_id: ObjectId, owner_id: ObjectId) -> int:
        # The unique (family_id, version) index arbitrates simultaneous inserts.
        # No counters or historical version records are ever rewritten.
        latest = self.collection.find_one(
            {"family_id": family_id, "owner_id": owner_id},
            projection={"version": 1},
            sort=[("version", DESCENDING)],
        )
        return 1 if latest is None else latest["version"] + 1
