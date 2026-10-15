"""Strict storage contract. Respondent IDs are internal even for anonymous answers."""

from app.surveys.schema import _QUESTION_PROPERTIES


_PROPERTIES = {
    "_id": {"bsonType": "objectId"},
    "survey_id": {"bsonType": "objectId"},
    "respondent_id": {"bsonType": "objectId"},
    "request_id": {"bsonType": "string", "pattern": "^[A-Za-z0-9_-]{8,64}$"},
    "fingerprint": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"},
    "survey_revision": {"bsonType": "int", "minimum": 1},
    "anonymous": {"bsonType": "bool"},
    "single_response": {"bsonType": "bool"},
    "submission_serial": {"bsonType": ["int", "long"], "minimum": 1},
    "submitted_at": {"bsonType": "date"},
    "path": {"bsonType": "array", "minItems": 1, "maxItems": 100,
             "uniqueItems": True, "items": _QUESTION_PROPERTIES["id"]},
    "answers": {
        "bsonType": "array", "minItems": 1, "maxItems": 100,
        "items": {
            "bsonType": "object", "required": ["question_id", "value"],
            "additionalProperties": False,
            "properties": {
                "question_id": _QUESTION_PROPERTIES["id"],
                "value": {"oneOf": [
                    {"bsonType": "null"},
                    {"bsonType": "string", "maxLength": 10000},
                    {"bsonType": ["int", "long", "double"], "minimum": -(10**15), "maximum": 10**15},
                    {"bsonType": "array", "maxItems": 50, "uniqueItems": True,
                     "items": {"bsonType": "string", "pattern": "^opt_[a-f0-9]{32}$"}},
                ]},
            },
        },
    },
    "schema_version": {"bsonType": "int", "enum": [1]},
}

SUBMISSION_SCHEMA = {"bsonType": "object", "required": list(_PROPERTIES),
                     "additionalProperties": False, "properties": _PROPERTIES}
