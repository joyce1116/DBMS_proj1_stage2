"""Explicit, offline migration of survey drafts from schema v1 to v2.

Stop every application process before calling this function. MongoDB standalone
does not provide a transaction spanning collMod and document updates; a durable
record and untouched original snapshots make interrupted runs resumable.
"""

from copy import deepcopy
from datetime import datetime, timezone
import re
from uuid import uuid4

from pymongo.database import Database

from app.surveys.schema import LEGACY_SURVEY_SCHEMA, SURVEY_SCHEMA


MIGRATION_ID = "surveys_v1_to_v2"
LEGACY_VALIDATOR = {"$jsonSchema": LEGACY_SURVEY_SCHEMA}
CURRENT_VALIDATOR = {"$jsonSchema": SURVEY_SCHEMA}
TRANSITION_VALIDATOR = {"$or": [LEGACY_VALIDATOR, CURRENT_VALIDATOR]}
_BACKUP_NAME = re.compile(r"^surveys_v1_backup_[a-f0-9]{32}$")


def _upgraded(document: dict) -> dict:
    return {
        **deepcopy(document),
        "schema_version": 2,
        "share_id": None,
        "published_at": None,
        "closed_at": None,
    }


def _validate_record(record: dict) -> None:
    counts = record.get("counts")
    if (
        record.get("state") not in ("in_progress", "completed")
        or not isinstance(record.get("backup_collection"), str)
        or not _BACKUP_NAME.fullmatch(record["backup_collection"])
        or not isinstance(record.get("started_at"), datetime)
        or not isinstance(counts, dict)
        or any(
            type(counts.get(key)) is not int or counts[key] < 0
            for key in ("original_v1", "original_v2", "total")
        )
    ):
        raise RuntimeError("Migration metadata is unknown or damaged; no data was changed.")
    if counts["original_v1"] + counts["original_v2"] != counts["total"]:
        raise RuntimeError("Migration metadata counts are inconsistent.")
    if record["state"] == "completed" and not isinstance(
        record.get("completed_at"), datetime
    ):
        raise RuntimeError("Completed migration metadata has no valid completion time.")


def _check_snapshots(db: Database, record: dict, v1_count: int, v2_count: int) -> int:
    """Check owned snapshots without creating or changing any collection."""
    counts = record["counts"]
    if v1_count + v2_count != counts["total"]:
        raise RuntimeError("Survey count changed during migration; keep the app stopped.")
    backup_name = record["backup_collection"]
    if backup_name not in db.list_collection_names():
        if v1_count != counts["original_v1"] or v2_count != counts["original_v2"]:
            raise RuntimeError("Converted surveys have no original backup collection.")
        if record["state"] == "completed":
            raise RuntimeError("Completed migration is missing its backup collection.")
        return 0

    backup = db[backup_name]
    if backup.find_one({"$nor": [LEGACY_VALIDATOR]}, {"_id": 1}):
        raise RuntimeError("Migration backup contains an invalid original survey.")
    converted = 0
    backup_count = 0
    for original in backup.find():
        backup_count += 1
        current = db.surveys.find_one({"_id": original["_id"]})
        if current == original:
            continue
        if current == _upgraded(original):
            converted += 1
            continue
        raise RuntimeError("A survey differs from its original snapshot; keep the app stopped.")
    if (
        backup_count > counts["original_v1"]
        or v1_count + converted != counts["original_v1"]
        or v2_count - converted != counts["original_v2"]
    ):
        raise RuntimeError("Survey and backup counts do not match the migration record.")
    if record["state"] == "completed" and backup_count != counts["original_v1"]:
        raise RuntimeError("Completed migration has an incomplete backup.")
    return backup_count


def migrate_surveys_v2(db: Database, *, dry_run: bool = False) -> dict:
    """Migrate only known schemas; retain IDs, revisions and original snapshots.

    ``dry_run`` performs the same preflight reads and makes no writes at all.
    The application must remain stopped until this function finishes, including
    when resuming an interrupted migration. Unknown schemas or corrupt input
    fail before migration writes. No collections are deleted or rolled back.
    """
    if "surveys" not in db.list_collection_names():
        return {"state": "absent", "dry_run": dry_run, "v1_count": 0, "v2_count": 0}

    options = db.surveys.options()
    validator = options.get("validator")
    if (
        validator not in (LEGACY_VALIDATOR, TRANSITION_VALIDATOR, CURRENT_VALIDATOR)
        or options.get("validationLevel", "strict") != "strict"
        or options.get("validationAction", "error") != "error"
    ):
        raise RuntimeError("Unknown surveys validator; an explicit reviewed migration is required.")
    if db.surveys.find_one({"$nor": [LEGACY_VALIDATOR, CURRENT_VALIDATOR]}, {"_id": 1}):
        raise RuntimeError("Surveys contain an invalid document or unknown schema version.")

    v1_count = db.surveys.count_documents({"schema_version": 1})
    v2_count = db.surveys.count_documents({"schema_version": 2})
    record = db.schema_migrations.find_one({"_id": MIGRATION_ID})
    summary = {
        "dry_run": dry_run,
        "v1_count": v1_count,
        "v2_count": v2_count,
        "backup_collection": None if record is None else record.get("backup_collection"),
    }
    if record is None:
        if validator == CURRENT_VALIDATOR:
            if v1_count:
                raise RuntimeError("The v2 validator contains legacy surveys without migration metadata.")
            return {**summary, "state": "already_v2"}
        if validator != LEGACY_VALIDATOR or v2_count:
            raise RuntimeError("A partial migration has no matching migration record.")
    else:
        _validate_record(record)
        if record["state"] == "completed" and (
            validator != CURRENT_VALIDATOR or v1_count
        ):
            raise RuntimeError("Completed migration metadata conflicts with the surveys schema.")
        if record["state"] == "completed":
            # The app may have edited/published old drafts or created new v2
            # surveys after completion. Original snapshots stay independent.
            backup_name = record["backup_collection"]
            if (
                backup_name not in db.list_collection_names()
                or db[backup_name].count_documents({}) != record["counts"]["original_v1"]
                or db[backup_name].find_one({"$nor": [LEGACY_VALIDATOR]}, {"_id": 1})
            ):
                raise RuntimeError("Completed migration has a missing or damaged original backup.")
            return {**summary, "state": "already_v2"}
        if (validator == LEGACY_VALIDATOR and v2_count) or (
            validator == CURRENT_VALIDATOR and v1_count
        ):
            raise RuntimeError("Migration validator conflicts with current survey versions.")
        _check_snapshots(db, record, v1_count, v2_count)

    if dry_run:
        return {**summary, "state": "dry_run"}

    if record is None:
        backup_name = "surveys_v1_backup_" + uuid4().hex
        if backup_name in db.list_collection_names():
            raise RuntimeError("Proposed backup collection already exists and is not owned by this migration.")
        record = {
            "_id": MIGRATION_ID,
            "state": "in_progress",
            "backup_collection": backup_name,
            "started_at": datetime.now(timezone.utc),
            "counts": {
                "original_v1": v1_count,
                "original_v2": v2_count,
                "total": v1_count + v2_count,
            },
        }
        db.schema_migrations.insert_one(record)

    backup_name = record["backup_collection"]
    if backup_name not in db.list_collection_names():
        db.create_collection(backup_name)
    backup = db[backup_name]
    for original in db.surveys.find({"schema_version": 1}):
        # Existing snapshots retain the first pre-migration document exactly.
        backup.update_one(
            {"_id": original["_id"]}, {"$setOnInsert": original}, upsert=True
        )
    backed_up = _check_snapshots(db, record, v1_count, v2_count)
    if backed_up != record["counts"]["original_v1"]:
        raise RuntimeError("The original snapshot is incomplete; no surveys were converted.")

    if validator != CURRENT_VALIDATOR:
        db.command({
            "collMod": "surveys",
            "validator": TRANSITION_VALIDATOR,
            "validationLevel": "strict",
            "validationAction": "error",
        })
        db.surveys.update_many(
            {"schema_version": 1},
            {"$set": {
                "schema_version": 2,
                "share_id": None,
                "published_at": None,
                "closed_at": None,
            }},
        )
    if db.surveys.find_one({"$nor": [CURRENT_VALIDATOR]}, {"_id": 1}):
        raise RuntimeError("Final v2 validation failed; snapshots are retained for inspection.")
    _check_snapshots(db, record, 0, db.surveys.count_documents({}))
    db.command({
        "collMod": "surveys",
        "validator": CURRENT_VALIDATOR,
        "validationLevel": "strict",
        "validationAction": "error",
    })
    db.schema_migrations.update_one(
        {"_id": MIGRATION_ID, "state": "in_progress"},
        {"$set": {
            "state": "completed",
            "completed_at": datetime.now(timezone.utc),
            "counts.backup": backed_up,
            "counts.converted": record["counts"]["original_v1"],
        }},
    )
    return {
        **summary,
        "state": "completed",
        "backup_collection": backup_name,
        "converted_count": record["counts"]["original_v1"],
    }
