"""Find saved snapshot references without granting access to other surveys."""

from bson import ObjectId
from pymongo.database import Database

from app.questions.repository import QuestionRepository
from app.questions.service import QuestionNotFound


class QuestionUsageService:
    def __init__(self, db: Database):
        self.db = db
        self.questions = QuestionRepository(db)

    def usage(self, version_id: str, owner_id: str, scope: str, offset: int, limit: int) -> dict:
        # A read/use recipient is not entitled to discover other people's use.
        version = None
        owner = ObjectId(owner_id)
        if ObjectId.is_valid(version_id):
            version = self.questions.find_owned(ObjectId(version_id), owner)
        if version is None:
            raise QuestionNotFound
        field = "version_id" if scope == "version" else "family_id"
        value = version["_id"] if scope == "version" else version["family_id"]
        pipeline = [
            {"$match": {"question_sources." + field: value}},
            {"$sort": {"created_at": -1, "_id": -1}},
            {"$project": {
                "_id": 0, "survey_id": {"$toString": "$_id"},
                "owned": {"$eq": ["$owner_id", owner]},
                "title": {"$cond": [{"$eq": ["$owner_id", owner]}, "$title", None]},
                "status": {"$cond": [{"$eq": ["$owner_id", owner]}, "$status", None]},
                "questions": {"$map": {
                    "input": {"$filter": {
                        "input": {"$ifNull": ["$question_sources", []]}, "as": "source",
                        "cond": {"$eq": ["$$source." + field, value]},
                    }},
                    "as": "source", "in": {
                        "question_id": "$$source.question_id",
                        "version_id": {"$toString": "$$source.version_id"},
                        "version": "$$source.version",
                    },
                }},
            }},
            {"$facet": {
                "items": [{"$skip": offset}, {"$limit": limit}],
                "totals": [{"$group": {
                    "_id": None, "surveys": {"$sum": 1},
                    "occurrences": {"$sum": {"$size": "$questions"}},
                }}],
            }},
        ]
        page = next(self.db.surveys.aggregate(pipeline))
        totals = page["totals"][0] if page["totals"] else {"surveys": 0, "occurrences": 0}
        return {
            "version_id": str(version["_id"]), "family_id": str(version["family_id"]),
            "scope": scope, "items": page["items"], "total_surveys": totals["surveys"],
            "total_occurrences": totals["occurrences"], "offset": offset, "limit": limit,
        }
