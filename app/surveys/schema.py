"""MongoDB 6-compatible historical schemas and current v3 rules/submission gate.

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

LEGACY_SURVEY_SCHEMA = {
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

# Keep the exact v1 validator above for explicit, data-preserving upgrades.
SURVEY_SCHEMA = deepcopy(LEGACY_SURVEY_SCHEMA)
SURVEY_SCHEMA["required"] += ["share_id", "published_at", "closed_at"]
SURVEY_SCHEMA["properties"].update({
    "schema_version": {"bsonType": "int", "enum": [2]},
    "status": {"bsonType": "string", "enum": ["draft", "published", "closed"]},
    "share_id": {"bsonType": ["string", "null"], "pattern": "^[A-Za-z0-9_-]{32}$"},
    "published_at": {"bsonType": ["date", "null"]},
    "closed_at": {"bsonType": ["date", "null"]},
})
SURVEY_SCHEMA["oneOf"] = [
    {"properties": {
        "status": {"enum": ["draft"]},
        "share_id": {"bsonType": "null"},
        "published_at": {"bsonType": "null"},
        "closed_at": {"bsonType": "null"},
    }},
    {"properties": {
        "status": {"enum": ["published"]},
        "share_id": {"bsonType": "string"},
        "published_at": {"bsonType": "date"},
        "closed_at": {"bsonType": "null"},
    }},
    {"properties": {
        "status": {"enum": ["closed"]},
        "share_id": {"bsonType": "string"},
        "published_at": {"bsonType": "date"},
        "closed_at": {"bsonType": "date"},
    }},
]

# Immutable target of the original v1 -> v2 migration.
SURVEY_SCHEMA_V2 = deepcopy(SURVEY_SCHEMA)

_RULE_COMMON = {
    "source_id": deepcopy(_QUESTION_PROPERTIES["id"]),
    "target_id": {"bsonType": "string", "pattern": "^(q_[a-f0-9]{32}|end)$"},
}
_RULE_SCHEMAS = []
for kind, extra in (
    ("choice", {
        "operator": {"enum": ["eq", "contains_any", "contains_all"]},
        "option_ids": {"bsonType": "array", "minItems": 1, "maxItems": 50,
                       "uniqueItems": True, "items": deepcopy(_OPTION_SCHEMA["properties"]["id"])},
    }),
    ("number", {
        "operator": {"enum": ["eq", "ne", "gt", "gte", "lt", "lte"]},
        "value": {**deepcopy(_NUMERIC_BOUND), "bsonType": ["int", "long", "double"]},
    }),
):
    properties = {**deepcopy(_RULE_COMMON), "kind": {"enum": [kind]}, **extra}
    _RULE_SCHEMAS.append({"bsonType": "object", "required": list(properties),
                          "additionalProperties": False, "properties": properties})

SURVEY_SCHEMA["required"] += ["jump_rules", "submission_serial", "last_submission_at"]
SURVEY_SCHEMA["properties"].update({
    "schema_version": {"bsonType": "int", "enum": [3]},
    "jump_rules": {"bsonType": "array", "maxItems": 200, "items": {"oneOf": _RULE_SCHEMAS}},
    "submission_serial": {"bsonType": ["int", "long"], "minimum": 0},
    "last_submission_at": {"bsonType": ["date", "null"]},
})
