"""MongoDB validation and indexes for the current milestone only.

Existing collections and documents are preserved. Future schema changes belong
in explicit migrations; startup does not silently replace an existing validator.
"""

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import CollectionInvalid

from app.core.config import Settings
from app.surveys.schema import SURVEY_SCHEMA


USER_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "username", "username_key", "password_hash", "created_at", "schema_version"
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "username": {
            "bsonType": "string", "minLength": 3, "maxLength": 32,
            "pattern": "^[A-Za-z0-9_]+$",
        },
        "username_key": {
            "bsonType": "string", "minLength": 3, "maxLength": 32,
            "pattern": "^[a-z0-9_]+$",
        },
        "password_hash": {"bsonType": "string", "pattern": "^\\$argon2id\\$"},
        "created_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
}

SESSION_SCHEMA = {
    "bsonType": "object",
    "required": [
        "_id", "user_id", "token_hash", "created_at", "expires_at", "schema_version"
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {"bsonType": "objectId"},
        "user_id": {"bsonType": "objectId"},
        "token_hash": {"bsonType": "string", "pattern": "^[a-f0-9]{64}$"},
        "created_at": {"bsonType": "date"},
        "expires_at": {"bsonType": "date"},
        "schema_version": {"bsonType": "int", "enum": [1]},
    },
}


def make_client(settings: Settings) -> MongoClient:
    return MongoClient(
        settings.mongo_uri,
        tz_aware=True,
        serverSelectionTimeoutMS=settings.mongo_timeout_ms,
        connectTimeoutMS=settings.mongo_timeout_ms,
        socketTimeoutMS=settings.mongo_timeout_ms,
    )


def initialize_database(db: Database) -> None:
    for name, schema in (
        ("users", USER_SCHEMA), ("sessions", SESSION_SCHEMA), ("surveys", SURVEY_SCHEMA)
    ):
        try:
            db.create_collection(
                name,
                validator={"$jsonSchema": schema},
                validationLevel="strict",
                validationAction="error",
            )
        except CollectionInvalid:
            # A pre-existing collection may contain valuable data; require an
            # explicit migration rather than overwriting its validation rules.
            options = db[name].options()
            if (
                options.get("validator") != {"$jsonSchema": schema}
                or options.get("validationLevel", "strict") != "strict"
                or options.get("validationAction", "error") != "error"
            ):
                raise RuntimeError(
                    f"Collection {name!r} has a different schema. "
                    "Back up the data and apply an explicit migration. "
                    + ("For surveys v1: python -m scripts.migrate_surveys_v2" if name == "surveys" else "")
                ) from None

    db.users.create_index(
        [("username_key", ASCENDING)], unique=True, name="uq_users_username_key"
    )
    db.sessions.create_index(
        [("token_hash", ASCENDING)], unique=True, name="uq_sessions_token_hash"
    )
    db.sessions.create_index(
        [("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_sessions_expires_at"
    )
    db.surveys.create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_surveys_owner_created",
    )
    db.surveys.create_index(
        [("share_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"share_id": {"$type": "string"}},
        name="uq_surveys_share_id",
    )
