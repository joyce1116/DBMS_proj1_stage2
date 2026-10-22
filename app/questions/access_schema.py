"""Explicit, revocable read/use grants without changing saved question versions."""


QUESTION_GRANT_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "resource_type", "resource_id", "owner_id", "grantee_id",
        "created_at", "schema_version",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "resource_type": {"bsonType": "string", "enum": ["version", "bank"]},
        "resource_id": {"bsonType": "objectId"},
        "owner_id": {"bsonType": "objectId"},
        "grantee_id": {"bsonType": "objectId"},
        "created_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
}
