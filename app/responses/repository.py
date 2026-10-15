"""Bounded reads and MongoDB aggregation over persisted submissions only."""

from bson import ObjectId
from pymongo import DESCENDING
from pymongo.database import Database


class ResponseRepository:
    def __init__(self, db: Database):
        self.collection = db.submissions
        self.users = db.users

    def request(self, survey_id: ObjectId, respondent_id: ObjectId, request_id: str):
        return self.collection.find_one({"survey_id": survey_id, "respondent_id": respondent_id,
                                         "request_id": request_id})

    def has_response(self, survey_id: ObjectId, respondent_id: ObjectId) -> bool:
        return self.collection.find_one({"survey_id": survey_id, "respondent_id": respondent_id},
                                        {"_id": 1}) is not None

    def insert(self, document: dict) -> None:
        self.collection.insert_one(document)

    def count(self, survey_id: ObjectId) -> int:
        return self.collection.count_documents({"survey_id": survey_id})

    def list_responses(self, survey_id: ObjectId, offset: int, limit: int) -> list[dict]:
        return list(self.collection.find({"survey_id": survey_id})
                    .sort([("submitted_at", DESCENDING), ("_id", DESCENDING)])
                    .skip(offset).limit(limit))

    def respondents(self, ids: set[ObjectId]) -> dict:
        return {doc["_id"]: {"id": str(doc["_id"]), "username": doc["username"]}
                for doc in self.users.find({"_id": {"$in": list(ids)}}, {"username": 1})}

    def statistics(self, survey_id: ObjectId, choices: list[str]) -> dict:
        # All three facets consume the same set of saved submissions. Restrict
        # option grouping to choice questions, avoiding unbounded text groups.
        result = next(self.collection.aggregate([
            {"$match": {"survey_id": survey_id}},
            {"$facet": {
                "total": [{"$count": "count"}],
                "summaries": [
                    {"$unwind": "$answers"},
                    {"$group": {"_id": "$answers.question_id", "visited_count": {"$sum": 1},
                                "answer_count": {"$sum": {"$cond": [{"$ne": ["$answers.value", None]}, 1, 0]}},
                                "average": {"$avg": "$answers.value"}}},
                ],
                "options": [
                    {"$unwind": "$answers"},
                    {"$match": {"answers.question_id": {"$in": choices}}},
                    {"$unwind": "$answers.value"},
                    {"$group": {"_id": {"q": "$answers.question_id", "option": "$answers.value"},
                                "count": {"$sum": 1}}},
                ],
            }},
        ]))
        counts = {}
        for row in result["options"]:
            counts.setdefault(row["_id"]["q"], {})[row["_id"]["option"]] = row["count"]
        return {"total": result["total"][0]["count"] if result["total"] else 0,
                "summaries": {row["_id"]: row for row in result["summaries"]}, "options": counts}

    def contents(self, survey_id: ObjectId, question_id: str, offset: int, limit: int) -> list[dict]:
        return list(self.collection.aggregate([
            {"$match": {"survey_id": survey_id}},
            {"$sort": {"submitted_at": -1, "_id": -1}},
            {"$unwind": "$answers"},
            {"$match": {"answers.question_id": question_id, "answers.value": {"$ne": None}}},
            {"$skip": offset}, {"$limit": limit},
            {"$project": {"_id": 0, "response_id": {"$toString": "$_id"}, "value": "$answers.value"}},
        ]))
