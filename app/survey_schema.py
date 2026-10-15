"""MongoDB 6-compatible schema for the draft-survey milestone.

Cross-field constraints (ranges, Unicode option uniqueness and stable ID
ownership) are also enforced by application validation and draft editing logic.
"""

from copy import deepcopy


_OPTION_SCHEMA = {
    "bsonType": "object",
    "required": ["id", "label"],
    "additionalProperties": False,
    "properties": {
        "id": {"bsonType": "string", "pattern": "^opt_[a-f0-9]{32}$"},
        "label": {"bsonType": "string", "minLength": 1, "maxLength": 200},
    },
}

_QUESTION_PROPERTIES = {
    "id": {"bsonType": "string", "pattern": "^q_[a-f0-9]{32}$"},
    "title": {"bsonType": "string", "minLength": 1, "maxLength": 500},
    "required": {"bsonType": "bool"},
}

_OPTIONS = {
    "bsonType": "array", "minItems": 2, "maxItems": 50, "items": _OPTION_SCHEMA,
}


def _question_schema(kind: str, extra: dict) -> dict:
    properties = {
        **deepcopy(_QUESTION_PROPERTIES),
        "type": {"bsonType": "string", "enum": [kind]},
        **deepcopy(extra),
    }
    return {
        "bsonType": "object",
        "required": list(properties),
        "additionalProperties": False,
        "properties": properties,
    }


_NUMERIC_BOUND = {
    "bsonType": ["int", "long", "double", "null"],
    "minimum": -(10**15),
    "maximum": 10**15,
}

SURVEY_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "owner_id", "title", "description", "allow_anonymous", "allow_repeat",
        "deadline", "status", "revision", "schema_version", "created_at", "updated_at",
        "questions",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "owner_id": {"bsonType": "objectId"},
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        "description": {"bsonType": "string", "maxLength": 5000},
        "allow_anonymous": {"bsonType": "bool"},
        "allow_repeat": {"bsonType": "bool"},
        "deadline": {"bsonType": ["date", "null"]},
        "status": {"bsonType": "string", "enum": ["draft"]},
        "revision": {"bsonType": "int", "minimum": 1},
        "schema_version": {"bsonType": "int", "enum": [1]},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "questions": {
            "bsonType": "array",
            "maxItems": 100,
            "items": {
                "oneOf": [
                    _question_schema("single_choice", {"options": _OPTIONS}),
                    _question_schema("multiple_choice", {
                        "options": _OPTIONS,
                        "min_selections": {"bsonType": "int", "minimum": 0, "maximum": 50},
                        "max_selections": {
                            "bsonType": ["int", "null"], "minimum": 0, "maximum": 50,
                        },
                    }),
                    _question_schema("text", {
                        "min_length": {"bsonType": "int", "minimum": 0, "maximum": 10000},
                        "max_length": {"bsonType": "int", "minimum": 1, "maximum": 10000},
                    }),
                    _question_schema("number", {
                        "min_value": _NUMERIC_BOUND,
                        "max_value": _NUMERIC_BOUND,
                        "integer_only": {"bsonType": "bool"},
                    }),
                ],
            },
        },
    },
}
