"""Exact-version statistics preserve survey ownership and version semantics."""

from bson import ObjectId
from pymongo.database import Database

from app.questions.access import QuestionAccessService
from app.questions.repository import QuestionRepository
from app.questions.service import QuestionNotFound
from app.questions.statistics_repository import QuestionStatisticsRepository


class QuestionStatisticsService:
    def __init__(self, db: Database):
        self.questions = QuestionRepository(db)
        self.access = QuestionAccessService(db)
        self.repository = QuestionStatisticsRepository(db)

    def _version(self, version_id: str, user_id: str) -> dict:
        try:
            return self.access.readable_version(version_id, user_id)
        except QuestionNotFound:
            # Revocation cannot take away reports over the caller's preserved
            # local snapshots. This exception grants no other version API right.
            if (ObjectId.is_valid(version_id)
                    and self.repository.has_owned_source(ObjectId(version_id), ObjectId(user_id))):
                version = self.questions.collection.find_one({"_id": ObjectId(version_id)})
                if version is not None:
                    return version
            raise

    @staticmethod
    def _summaries(versions: list[dict], aggregate: dict) -> list[dict]:
        coverage = {row["_id"]: row for row in aggregate["coverage"]}
        responses = {row["_id"]: row for row in aggregate["responses"]}
        summaries = {row["_id"]: row for row in aggregate["summaries"]}
        options = {}
        for row in aggregate["options"]:
            options.setdefault(row["_id"]["version"], {})[row["_id"]["option"]] = row["count"]
        items = []
        for version in versions:
            version_id, content = version["_id"], version["content"]
            summary = summaries.get(version_id, {})
            counts = options.get(version_id, {})
            numeric = content["type"] == "number"
            items.append({
                "version_id": str(version_id), "family_id": str(version["family_id"]),
                "version": version["version"], "title": content["title"], "type": content["type"],
                "survey_scope": "owned",
                "survey_count": coverage.get(version_id, {}).get("survey_count", 0),
                "question_occurrence_count": coverage.get(version_id, {}).get("question_occurrence_count", 0),
                "total_responses": responses.get(version_id, {}).get("total_responses", 0),
                "visited_count": summary.get("visited_count", 0),
                "answer_count": summary.get("answer_count", 0),
                "options": [
                    {"option_id": option["id"], "label": option["label"], "count": counts.get(option["id"], 0)}
                    for option in content.get("options", [])
                ],
                "average": summary.get("average") if numeric else None,
                "min_value": summary.get("min_value") if numeric else None,
                "max_value": summary.get("max_value") if numeric else None,
            })
        return items

    def statistics(self, version_id: str, user_id: str, offset: int, limit: int) -> dict:
        version = self._version(version_id, user_id)
        aggregate = self.repository.statistics(
            [version["_id"]], ObjectId(user_id), offset, limit,
            include_distribution=version["content"]["type"] == "number",
        )
        total = aggregate.get("distribution_total", [])
        return {
            **self._summaries([version], aggregate)[0],
            "distribution": aggregate.get("distribution", []),
            "distribution_total": total[0]["count"] if total else 0,
            "offset": offset, "limit": limit,
        }

    def family_statistics(self, version_id: str, user_id: str, offset: int, limit: int) -> dict:
        version = None
        if ObjectId.is_valid(version_id):
            version = self.questions.find_owned(ObjectId(version_id), ObjectId(user_id))
        if version is None:
            raise QuestionNotFound
        versions, total = self.questions.history(version["family_id"], version["owner_id"], offset, limit)
        # One batch aggregation for the page, independent of its version count.
        aggregate = self.repository.statistics([row["_id"] for row in versions], ObjectId(user_id))
        return {
            "family_id": str(version["family_id"]), "survey_scope": "owned",
            "versions": self._summaries(versions, aggregate), "total_versions": total,
            "offset": offset, "limit": limit,
        }

    def contents(self, version_id: str, user_id: str, offset: int, limit: int) -> dict:
        version = self._version(version_id, user_id)
        aggregate = (
            self.repository.contents(version["_id"], ObjectId(user_id), offset, limit)
            if version["content"]["type"] in ("text", "number") else {"items": [], "total": []}
        )
        return {
            "version_id": str(version["_id"]), "survey_scope": "owned", "items": aggregate["items"],
            "total": aggregate["total"][0]["count"] if aggregate["total"] else 0,
            "offset": offset, "limit": limit,
        }
