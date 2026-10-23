"""Aggregate saved responses through the caller's frozen survey sources only.

The lookup/unwind pair streams submissions into MongoDB facets. Python receives
bounded content/distribution pages and grouped counters, never all answers.
"""

from bson import ObjectId
from pymongo.database import Database


class QuestionStatisticsRepository:
    def __init__(self, db: Database):
        self.surveys = db.surveys

    def has_owned_source(self, version_id: ObjectId, user_id: ObjectId) -> bool:
        # Draft snapshots also preserve the right to inspect this exact version's
        # statistics after revocation. They do not participate in the metrics.
        return self.surveys.find_one(
            {"owner_id": user_id, "question_sources.version_id": version_id}, {"_id": 1},
        ) is not None

    @staticmethod
    def _sources_and_responses(version_ids: list[ObjectId], user_id: ObjectId) -> list[dict]:
        return [
            {"$match": {
                "owner_id": user_id, "status": {"$in": ["published", "closed"]},
                "question_sources.version_id": {"$in": version_ids},
            }},
            {"$project": {"source": "$question_sources"}},
            {"$unwind": "$source"},
            {"$match": {"source.version_id": {"$in": version_ids}}},
            {"$lookup": {
                "from": "submissions",
                "let": {"survey": "$_id", "question": "$source.question_id"},
                "pipeline": [
                    {"$match": {"$expr": {"$eq": ["$survey_id", "$$survey"]}}},
                    {"$project": {
                        "submitted_at": 1,
                        "answers": {"$filter": {
                            "input": "$answers", "as": "answer",
                            "cond": {"$eq": ["$$answer.question_id", "$$question"]},
                        }},
                    }},
                ],
                "as": "response",
            }},
            # Keep a zero-response occurrence for the coverage facet. The two
            # stages must remain adjacent so MongoDB can coalesce the join.
            {"$unwind": {"path": "$response", "preserveNullAndEmptyArrays": True}},
        ]

    def statistics(
        self, version_ids: list[ObjectId], user_id: ObjectId,
        offset: int = 0, limit: int = 20, *, include_distribution: bool = False,
    ) -> dict:
        facets = {
            "coverage": [
                # A join row repeats each occurrence for each submission. Count
                # each (version, survey, local question) once before rolling up.
                {"$group": {"_id": {
                    "version": "$source.version_id", "survey": "$_id",
                    "question": "$source.question_id",
                }}},
                {"$group": {
                    "_id": {"version": "$_id.version", "survey": "$_id.survey"},
                    "occurrences": {"$sum": 1},
                }},
                {"$group": {
                    "_id": "$_id.version", "survey_count": {"$sum": 1},
                    "question_occurrence_count": {"$sum": "$occurrences"},
                }},
            ],
            "responses": [
                {"$match": {"response._id": {"$exists": True}}},
                {"$group": {"_id": {
                    "version": "$source.version_id", "response": "$response._id",
                }}},
                {"$group": {"_id": "$_id.version", "total_responses": {"$sum": 1}}},
            ],
            "summaries": [
                # Skipped questions have an empty filtered answers array. A
                # visited optional question instead has one answer with null.
                {"$unwind": "$response.answers"},
                {"$group": {
                    "_id": "$source.version_id", "visited_count": {"$sum": 1},
                    "answer_count": {"$sum": {"$cond": [
                        {"$ne": ["$response.answers.value", None]}, 1, 0,
                    ]}},
                    "average": {"$avg": "$response.answers.value"},
                    "min_value": {"$min": "$response.answers.value"},
                    "max_value": {"$max": "$response.answers.value"},
                }},
            ],
            "options": [
                {"$unwind": "$response.answers"},
                # MongoDB unwinds a non-array single-choice value as one item.
                {"$unwind": "$response.answers.value"},
                {"$set": {"mapped_option": {"$arrayElemAt": [
                    {"$filter": {
                        "input": "$source.option_map", "as": "mapping",
                        "cond": {"$eq": ["$$mapping.local_id", "$response.answers.value"]},
                    }}, 0,
                ]}}},
                {"$match": {"mapped_option.source_id": {"$exists": True}}},
                {"$group": {
                    "_id": {"version": "$source.version_id", "option": "$mapped_option.source_id"},
                    "count": {"$sum": 1},
                }},
            ],
        }
        if include_distribution:
            values = [
                {"$unwind": "$response.answers"},
                {"$match": {"response.answers.value": {"$ne": None}}},
                {"$group": {
                    "_id": {"version": "$source.version_id", "value": "$response.answers.value"},
                    "count": {"$sum": 1},
                }},
            ]
            facets["distribution"] = values + [
                {"$sort": {"_id.value": 1}}, {"$skip": offset}, {"$limit": limit},
                {"$project": {"_id": 0, "value": "$_id.value", "count": 1}},
            ]
            facets["distribution_total"] = values + [{"$count": "count"}]
        pipeline = self._sources_and_responses(version_ids, user_id) + [{"$facet": facets}]
        return next(self.surveys.aggregate(pipeline, allowDiskUse=True))

    def contents(self, version_id: ObjectId, user_id: ObjectId, offset: int, limit: int) -> dict:
        pipeline = self._sources_and_responses([version_id], user_id) + [
            {"$unwind": "$response.answers"},
            {"$match": {"response.answers.value": {"$ne": None}}},
            {"$facet": {
                "items": [
                    {"$sort": {
                        "response.submitted_at": -1, "response._id": -1,
                        "source.question_id": 1,
                    }},
                    {"$skip": offset}, {"$limit": limit},
                    {"$project": {
                        "_id": 0, "survey_id": {"$toString": "$_id"},
                        "question_id": "$source.question_id",
                        "response_id": {"$toString": "$response._id"},
                        "submitted_at": "$response.submitted_at", "value": "$response.answers.value",
                    }},
                ],
                "total": [{"$count": "count"}],
            }},
        ]
        return next(self.surveys.aggregate(pipeline, allowDiskUse=True))
