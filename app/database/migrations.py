"""Explicit, resumable offline migrations; original documents are retained.

Stop every application process before calling this function. MongoDB standalone
does not provide a transaction spanning collMod and document updates; a durable
record and untouched original snapshots make interrupted runs resumable.
"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from uuid import uuid4

from pymongo.database import Database

from app.surveys.schema import LEGACY_SURVEY_SCHEMA, SURVEY_SCHEMA_V2, SURVEY_SCHEMA


MIGRATION_ID = "surveys_v1_to_v2"
LEGACY_VALIDATOR = {"$jsonSchema": LEGACY_SURVEY_SCHEMA}
CURRENT_VALIDATOR = {"$jsonSchema": SURVEY_SCHEMA_V2}
TRANSITION_VALIDATOR = {"$or": [LEGACY_VALIDATOR, CURRENT_VALIDATOR]}


@dataclass(frozen=True)
class MigrationPlan:
    source: int
    target: int
    source_validator: dict
    target_validator: dict
    defaults: dict

    @property
    def migration_id(self):
        return f"surveys_v{self.source}_to_v{self.target}"

    @property
    def transition(self):
        return {"$or": [self.source_validator, self.target_validator]}

    def upgraded(self, document):
        return {**deepcopy(document), **deepcopy(self.defaults), "schema_version": self.target}


V2_PLAN = MigrationPlan(1, 2, LEGACY_VALIDATOR, CURRENT_VALIDATOR,
                        {"share_id": None, "published_at": None, "closed_at": None})
V3_PLAN = MigrationPlan(2, 3, CURRENT_VALIDATOR, {"$jsonSchema": SURVEY_SCHEMA},
                        {"jump_rules": [], "submission_serial": 0, "last_submission_at": None})


def _validate_record(record: dict, plan: MigrationPlan) -> None:
    counts = record.get("counts")
    if (
        record.get("state") not in ("in_progress", "completed")
        or not isinstance(record.get("backup_collection"), str)
        or not re.fullmatch(rf"surveys_v{plan.source}_backup_[a-f0-9]{{32}}", record["backup_collection"])
        or not isinstance(record.get("started_at"), datetime)
        or not isinstance(counts, dict)
        or any(
            type(counts.get(key)) is not int or counts[key] < 0
            for key in (f"original_v{plan.source}", f"original_v{plan.target}", "total")
        )
    ):
        raise RuntimeError("Migration metadata is unknown or damaged; no data was changed.")
    if counts[f"original_v{plan.source}"] + counts[f"original_v{plan.target}"] != counts["total"]:
        raise RuntimeError("Migration metadata counts are inconsistent.")
    if record["state"] == "completed" and not isinstance(
        record.get("completed_at"), datetime
    ):
        raise RuntimeError("Completed migration metadata has no valid completion time.")


def _check_snapshots(db: Database, record: dict, source_count: int, target_count: int, plan: MigrationPlan) -> int:
    """Check owned snapshots without creating or changing any collection."""
    counts = record["counts"]
    if source_count + target_count != counts["total"]:
        raise RuntimeError("Survey count changed during migration; keep the app stopped.")
    backup_name = record["backup_collection"]
    if backup_name not in db.list_collection_names():
        if source_count != counts[f"original_v{plan.source}"] or target_count != counts[f"original_v{plan.target}"]:
            raise RuntimeError("Converted surveys have no original backup collection.")
        if record["state"] == "completed":
            raise RuntimeError("Completed migration is missing its backup collection.")
        return 0

    backup = db[backup_name]
    if backup.find_one({"$nor": [plan.source_validator]}, {"_id": 1}):
        raise RuntimeError("Migration backup contains an invalid original survey.")
    converted = 0
    backup_count = 0
    for original in backup.find():
        backup_count += 1
        current = db.surveys.find_one({"_id": original["_id"]})
        if current == original:
            continue
        if current == plan.upgraded(original):
            converted += 1
            continue
        raise RuntimeError("A survey differs from its original snapshot; keep the app stopped.")
    if (
        backup_count > counts[f"original_v{plan.source}"]
        or source_count + converted != counts[f"original_v{plan.source}"]
        or target_count - converted != counts[f"original_v{plan.target}"]
    ):
        raise RuntimeError("Survey and backup counts do not match the migration record.")
    if record["state"] == "completed" and backup_count != counts[f"original_v{plan.source}"]:
        raise RuntimeError("Completed migration has an incomplete backup.")
    return backup_count


def _migrate(db: Database, plan: MigrationPlan, *, dry_run: bool = False) -> dict:
    """Migrate only known schemas; retain IDs, revisions and original snapshots.

    ``dry_run`` performs the same preflight reads and makes no writes at all.
    The application must remain stopped until this function finishes, including
    when resuming an interrupted migration. Unknown schemas or corrupt input
    fail before migration writes. No collections are deleted or rolled back.
    """
    LEGACY_VALIDATOR = plan.source_validator
    CURRENT_VALIDATOR = plan.target_validator
    TRANSITION_VALIDATOR = plan.transition
    MIGRATION_ID = plan.migration_id
    if "surveys" not in db.list_collection_names():
        return {"state": "absent", "dry_run": dry_run, f"v{plan.source}_count": 0, f"v{plan.target}_count": 0}

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

    source_count = db.surveys.count_documents({"schema_version": plan.source})
    target_count = db.surveys.count_documents({"schema_version": plan.target})
    record = db.schema_migrations.find_one({"_id": MIGRATION_ID})
    summary = {
        "dry_run": dry_run,
        f"v{plan.source}_count": source_count,
        f"v{plan.target}_count": target_count,
        "backup_collection": None if record is None else record.get("backup_collection"),
    }
    if record is None:
        if validator == CURRENT_VALIDATOR:
            if source_count:
                raise RuntimeError("The target validator contains legacy surveys without migration metadata.")
            return {**summary, "state": f"already_v{plan.target}"}
        if validator != LEGACY_VALIDATOR or target_count:
            raise RuntimeError("A partial migration has no matching migration record.")
    else:
        _validate_record(record, plan)
        if record["state"] == "completed" and (
            validator != CURRENT_VALIDATOR or source_count
        ):
            raise RuntimeError("Completed migration metadata conflicts with the surveys schema.")
        if record["state"] == "completed":
            # The app may have edited/published old drafts or created new target
            # surveys after completion. Original snapshots stay independent.
            backup_name = record["backup_collection"]
            if (
                backup_name not in db.list_collection_names()
                or db[backup_name].count_documents({}) != record["counts"][f"original_v{plan.source}"]
                or db[backup_name].find_one({"$nor": [LEGACY_VALIDATOR]}, {"_id": 1})
            ):
                raise RuntimeError("Completed migration has a missing or damaged original backup.")
            return {**summary, "state": f"already_v{plan.target}"}
        if (validator == LEGACY_VALIDATOR and target_count) or (
            validator == CURRENT_VALIDATOR and source_count
        ):
            raise RuntimeError("Migration validator conflicts with current survey versions.")
        _check_snapshots(db, record, source_count, target_count, plan)

    if dry_run:
        return {**summary, "state": "dry_run"}

    if record is None:
        backup_name = f"surveys_v{plan.source}_backup_" + uuid4().hex
        if backup_name in db.list_collection_names():
            raise RuntimeError("Proposed backup collection already exists and is not owned by this migration.")
        record = {
            "_id": MIGRATION_ID,
            "state": "in_progress",
            "backup_collection": backup_name,
            "started_at": datetime.now(timezone.utc),
            "counts": {
                f"original_v{plan.source}": source_count,
                f"original_v{plan.target}": target_count,
                "total": source_count + target_count,
            },
        }
        db.schema_migrations.insert_one(record)

    backup_name = record["backup_collection"]
    if backup_name not in db.list_collection_names():
        db.create_collection(backup_name)
    backup = db[backup_name]
    for original in db.surveys.find({"schema_version": plan.source}):
        # Existing snapshots retain the first pre-migration document exactly.
        backup.update_one(
            {"_id": original["_id"]}, {"$setOnInsert": original}, upsert=True
        )
    backed_up = _check_snapshots(db, record, source_count, target_count, plan)
    if backed_up != record["counts"][f"original_v{plan.source}"]:
        raise RuntimeError("The original snapshot is incomplete; no surveys were converted.")

    if validator != CURRENT_VALIDATOR:
        db.command({
            "collMod": "surveys",
            "validator": TRANSITION_VALIDATOR,
            "validationLevel": "strict",
            "validationAction": "error",
        })
        db.surveys.update_many(
            {"schema_version": plan.source},
            {"$set": {
                **deepcopy(plan.defaults), "schema_version": plan.target,
            }},
        )
    if db.surveys.find_one({"$nor": [CURRENT_VALIDATOR]}, {"_id": 1}):
        raise RuntimeError("Final target validation failed; snapshots are retained for inspection.")
    _check_snapshots(db, record, 0, db.surveys.count_documents({}), plan)
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
            "counts.converted": record["counts"][f"original_v{plan.source}"],
        }},
    )
    return {
        **summary,
        "state": "completed",
        "backup_collection": backup_name,
        "converted_count": record["counts"][f"original_v{plan.source}"],
    }


def migrate_surveys_v2(db: Database, *, dry_run: bool = False) -> dict:
    return _migrate(db, V2_PLAN, dry_run=dry_run)


def migrate_surveys_v3(db: Database, *, dry_run: bool = False) -> dict:
    return _migrate(db, V3_PLAN, dry_run=dry_run)
