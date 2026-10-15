"""Owner-scoped MongoDB access and atomic draft revisions."""

from bson import ObjectId
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database


class SurveyRepository:
    def __init__(self, db: Database):
        self.collection = db.surveys

    def create(self, document: dict) -> dict:
        self.collection.insert_one(document)
        return document

    def find_owned(self, survey_id: ObjectId, owner_id: ObjectId) -> dict | None:
        return self.collection.find_one({"_id": survey_id, "owner_id": owner_id})

    def find_shared(self, share_id: str) -> dict | None:
        return self.collection.find_one({"share_id": share_id})

    def list_owned(self, owner_id: ObjectId, offset: int, limit: int) -> tuple[list[dict], int]:
        query = {"owner_id": owner_id}
        documents = list(
            self.collection.find(query)
            .sort([("created_at", DESCENDING), ("_id", DESCENDING)])
            .skip(offset)
            .limit(limit)
        )
        return documents, self.collection.count_documents(query)

    def update_draft(
        self, survey_id: ObjectId, owner_id: ObjectId, revision: int, changes: dict
    ) -> dict | None:
        # A single-document compare-and-set prevents silent lost edits even when
        # two requests both read the same old question array.
        return self.collection.find_one_and_update(
            {"_id": survey_id, "owner_id": owner_id, "revision": revision, "status": "draft"},
            {"$set": changes, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )

    def transition(
        self, survey_id: ObjectId, owner_id: ObjectId, revision: int,
        source_status: str, changes: dict,
    ) -> dict | None:
        return self.collection.find_one_and_update(
            {"_id": survey_id, "owner_id": owner_id,
             "revision": revision, "status": source_status},
            {"$set": changes, "$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER,
        )

    def accept_submission(self, survey_id: ObjectId, revision: int) -> dict | None:
        # This single-document gate orders acceptance against closing. The
        # server's clock checks the deadline and timestamps the accepted attempt.
        return self.collection.find_one_and_update(
            {"_id": survey_id, "revision": revision, "status": "published",
             "$expr": {"$or": [{"$eq": ["$deadline", None]}, {"$gt": ["$deadline", "$$NOW"]}]}},
            [{"$set": {"submission_serial": {"$add": ["$submission_serial", 1]},
                       "last_submission_at": "$$NOW"}}],
            return_document=ReturnDocument.AFTER,
        )
