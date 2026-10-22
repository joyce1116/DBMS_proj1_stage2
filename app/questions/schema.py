"""Independent version storage, derived without changing phase-one schemas.

Question content shares the four existing type validators. Only the survey-local
question ID is removed: the version document's ObjectId identifies this record.
Cross-record ownership and parent/family relationships belong to the service.
"""

from copy import deepcopy

from app.surveys.schema import SURVEY_SCHEMA


QUESTION_CONTENT_SCHEMA = deepcopy(SURVEY_SCHEMA["properties"]["questions"]["items"])
for _variant in QUESTION_CONTENT_SCHEMA["oneOf"]:
    _variant["properties"].pop("id")
    _variant["required"].remove("id")


QUESTION_VERSION_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "family_id", "owner_id", "version", "parent_id", "operation",
        "content", "created_at", "schema_version",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "family_id": {"bsonType": "objectId"},
        "owner_id": {"bsonType": "objectId"},
        "version": {"bsonType": "int", "minimum": 1, "maximum": 2147483647},
        "parent_id": {"bsonType": ["objectId", "null"]},
        "operation": {"bsonType": "string", "enum": ["create", "revise", "restore"]},
        "content": QUESTION_CONTENT_SCHEMA,
        "created_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
    "oneOf": [
        {"properties": {
            "operation": {"enum": ["create"]},
            "version": {"enum": [1]},
            "parent_id": {"bsonType": "null"},
        }},
        {"properties": {
            "operation": {"enum": ["revise", "restore"]},
            "version": {"minimum": 2},
            "parent_id": {"bsonType": "objectId"},
        }},
    ],
}
