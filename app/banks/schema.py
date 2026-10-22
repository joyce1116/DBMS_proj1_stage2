"""New collections only: banks never rewrite survey or question schemas."""


BANK_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "owner_id", "title", "description", "revision", "archived",
        "created_at", "updated_at", "schema_version",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "owner_id": {"bsonType": "objectId"},
        "title": {"bsonType": "string", "minLength": 1, "maxLength": 200},
        "description": {"bsonType": "string", "maxLength": 5000},
        "revision": {"bsonType": "int", "minimum": 1, "maximum": 2147483647},
        "archived": {"bsonType": "bool"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
}


BANK_ENTRY_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "bank_id", "family_id", "version_id", "created_at", "updated_at",
        "schema_version",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "bank_id": {"bsonType": "objectId"},
        "family_id": {"bsonType": "objectId"},
        "version_id": {"bsonType": "objectId"},
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
}
