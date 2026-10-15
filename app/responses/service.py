"""Authoritative submission and owner-only reporting on the original survey model."""

from hashlib import sha256
import json

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.responses.repository import ResponseRepository
from app.surveys.logic import evaluate
from app.surveys.service import (
    RevisionConflict, SurveyNotFound, SurveyService, SurveyUnavailable, survey_availability,
)


class ResponseConflict(Exception):
    pass


class InvalidSubmission(Exception):
    pass


def receipt(document: dict) -> dict:
    return {"response_id": str(document["_id"]), "survey_id": str(document["survey_id"]),
            "survey_revision": document["survey_revision"], "submitted_at": document["submitted_at"],
            "anonymous": document["anonymous"], "path": document["path"]}


class ResponseService:
    def __init__(self, surveys: SurveyService, repository: ResponseRepository):
        self.surveys = surveys
        self.repository = repository

    def navigate(self, share_id: str, revision: int, answers: dict) -> dict:
        survey = self.surveys.shared_document(share_id)
        if survey["revision"] != revision:
            raise RevisionConflict
        return evaluate(survey["questions"], survey["jump_rules"], answers, complete=False)

    def _replay(self, existing: dict, fingerprint: str) -> tuple[dict, bool]:
        if existing["fingerprint"] != fingerprint:
            raise ResponseConflict("This request_id was already used for different content")
        return receipt(existing), False

    def submit(self, share_id: str, respondent_id: str, data: dict) -> tuple[dict, bool]:
        # A saved request can be replayed even after closing or expiration. The
        # receipt is only retrievable by the authenticated original respondent.
        survey = self.surveys.shared_document(share_id, require_open=False)
        user_id = ObjectId(respondent_id)
        try:
            canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                   separators=(",", ":")).encode("utf-8")
        except (ValueError, UnicodeError):
            raise InvalidSubmission("Answers must contain valid finite JSON values") from None
        fingerprint = sha256(canonical).hexdigest()
        existing = self.repository.request(survey["_id"], user_id, data["request_id"])
        if existing is not None:
            return self._replay(existing, fingerprint)
        if survey_availability(survey) != "open":
            raise SurveyUnavailable
        if data["survey_revision"] != survey["revision"]:
            raise RevisionConflict
        if data["anonymous"] and not survey["allow_anonymous"]:
            raise InvalidSubmission("Anonymous responses are not enabled for this survey")
        result = evaluate(survey["questions"], survey["jump_rules"], data["answers"], complete=True)
        if not survey["allow_repeat"] and self.repository.has_response(survey["_id"], user_id):
            existing = self.repository.request(survey["_id"], user_id, data["request_id"])
            if existing is not None:
                return self._replay(existing, fingerprint)
            raise ResponseConflict("This account has already submitted a response")
        accepted = self.surveys.repository.accept_submission(survey["_id"], survey["revision"])
        if accepted is None:
            raise SurveyUnavailable
        document = {
            "_id": ObjectId(), "survey_id": survey["_id"], "respondent_id": user_id,
            "request_id": data["request_id"], "fingerprint": fingerprint,
            "survey_revision": survey["revision"], "anonymous": data["anonymous"],
            "single_response": not survey["allow_repeat"], "schema_version": 1,
            "submission_serial": accepted["submission_serial"],
            "submitted_at": accepted["last_submission_at"],
            "path": result["path"], "answers": result["answers"],
        }
        try:
            self.repository.insert(document)
        except DuplicateKeyError:
            # Both retries and simultaneous distinct requests may pass the
            # preliminary read. The database indexes enforce the final decision.
            existing = self.repository.request(survey["_id"], user_id, data["request_id"])
            if existing is not None:
                return self._replay(existing, fingerprint)
            raise ResponseConflict("This account has already submitted a response") from None
        return receipt(document), True

    def responses(self, survey_id: str, owner_id: str, offset: int, limit: int) -> dict:
        survey = self.surveys._owned(survey_id, owner_id)
        documents = self.repository.list_responses(survey["_id"], offset, limit)
        names = self.repository.respondents({doc["respondent_id"] for doc in documents if not doc["anonymous"]})
        items = [{**receipt(doc), "answers": doc["answers"],
                  "respondent": None if doc["anonymous"] else names.get(doc["respondent_id"])}
                 for doc in documents]
        return {"items": items, "total": self.repository.count(survey["_id"]), "offset": offset, "limit": limit}

    def _statistics(self, survey: dict) -> dict:
        aggregate = self.repository.statistics(survey["_id"],
            [q["id"] for q in survey["questions"] if q["type"] in ("single_choice", "multiple_choice")])
        summaries, counts = aggregate["summaries"], aggregate["options"]
        questions = [{
            "question_id": q["id"], "title": q["title"], "type": q["type"],
            "visited_count": summaries.get(q["id"], {}).get("visited_count", 0),
            "answer_count": summaries.get(q["id"], {}).get("answer_count", 0),
            "average": summaries.get(q["id"], {}).get("average") if q["type"] == "number" else None,
            "options": [{"option_id": opt["id"], "label": opt["label"],
                         "count": counts.get(q["id"], {}).get(opt["id"], 0)} for opt in q.get("options", [])],
        } for q in survey["questions"]]
        return {"survey_id": str(survey["_id"]), "total_responses": aggregate["total"], "questions": questions}

    def statistics(self, survey_id: str, owner_id: str) -> dict:
        survey = self.surveys._owned(survey_id, owner_id)
        return self._statistics(survey)

    def question_statistics(self, survey_id: str, owner_id: str, question_id: str,
                            offset: int, limit: int) -> dict:
        survey = self.surveys._owned(survey_id, owner_id)
        if question_id not in {q["id"] for q in survey["questions"]}:
            raise SurveyNotFound
        summary = next(q for q in self._statistics(survey)["questions"] if q["question_id"] == question_id)
        fill_in = summary["type"] in ("text", "number")
        return {**summary, "offset": offset, "limit": limit,
                "contents_total": summary["answer_count"] if fill_in else 0,
                "contents": self.repository.contents(survey["_id"], question_id, offset, limit) if fill_in else []}
