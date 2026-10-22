"""MongoDB validation and indexes for the incremental questionnaire backend.

Existing collections and documents are preserved. Future schema changes belong
in explicit migrations; startup does not silently replace an existing validator.
"""

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import CollectionInvalid

from app.core.config import Settings
from app.surveys.schema import SURVEY_SCHEMA
from app.responses.schema import SUBMISSION_SCHEMA
from app.questions.schema import QUESTION_VERSION_SCHEMA
from app.questions.access_schema import QUESTION_GRANT_SCHEMA
from app.banks.schema import BANK_SCHEMA, BANK_ENTRY_SCHEMA


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
        ("users", USER_SCHEMA), ("sessions", SESSION_SCHEMA), ("surveys", SURVEY_SCHEMA),
        ("submissions", SUBMISSION_SCHEMA),
        ("question_versions", QUESTION_VERSION_SCHEMA),
        ("question_banks", BANK_SCHEMA),
        ("question_bank_entries", BANK_ENTRY_SCHEMA),
        ("question_grants", QUESTION_GRANT_SCHEMA),
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
                    + ("For surveys: migrate_surveys_v2 (v1 only), then "
                       "migrate_surveys_v3 (v2 only), then migrate_surveys_v4 (v3 only)."
                       if name == "surveys" else "")
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
    db.submissions.create_index(
        [("survey_id", ASCENDING), ("respondent_id", ASCENDING), ("request_id", ASCENDING)],
        unique=True, name="uq_submissions_request",
    )
    db.submissions.create_index(
        [("survey_id", ASCENDING), ("respondent_id", ASCENDING)],
        unique=True, partialFilterExpression={"single_response": True}, name="uq_submissions_once",
    )
    db.submissions.create_index(
        [("survey_id", ASCENDING), ("submitted_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_submissions_survey_time",
    )
    db.question_versions.create_index(
        [("family_id", ASCENDING), ("version", ASCENDING)],
        unique=True, name="uq_question_versions_family_version",
    )
    db.question_versions.create_index(
        [("owner_id", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_question_versions_owner_created",
    )
    db.question_banks.create_index(
        [("owner_id", ASCENDING), ("archived", ASCENDING),
         ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_question_banks_owner_active_created",
    )
    db.question_bank_entries.create_index(
        [("bank_id", ASCENDING), ("family_id", ASCENDING)],
        unique=True, name="uq_question_bank_entries_family",
    )
    db.question_bank_entries.create_index(
        [("bank_id", ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_question_bank_entries_created",
    )
    db.question_bank_entries.create_index(
        [("version_id", ASCENDING), ("bank_id", ASCENDING)],
        name="ix_question_bank_entries_version",
    )
    db.question_grants.create_index(
        [("resource_type", ASCENDING), ("resource_id", ASCENDING), ("grantee_id", ASCENDING)],
        unique=True, name="uq_question_grants_resource_grantee",
    )
    db.question_grants.create_index(
        [("grantee_id", ASCENDING), ("resource_type", ASCENDING),
         ("created_at", DESCENDING), ("_id", DESCENDING)],
        name="ix_question_grants_grantee_created",
    )
    for field, name in (
        ("question_sources.version_id", "ix_surveys_source_version"),
        ("question_sources.family_id", "ix_surveys_source_family"),
    ):
        db.surveys.create_index(
            [(field, ASCENDING), ("created_at", DESCENDING), ("_id", DESCENDING)], name=name,
        )
