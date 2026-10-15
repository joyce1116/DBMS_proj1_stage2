"""True-MongoDB checks for the explicit, non-destructive schema migration."""

from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import patch

from bson import ObjectId
from pymongo.errors import OperationFailure
import pytest

from app.database.migrations import (
    CURRENT_VALIDATOR,
    LEGACY_VALIDATOR,
    MIGRATION_ID,
    TRANSITION_VALIDATOR,
    migrate_surveys_v2,
)


pytestmark = pytest.mark.integration


def _original() -> dict:
    now = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    return {
        "_id": ObjectId(), "owner_id": ObjectId(), "title": "保留旧草稿",
        "description": "旧说明", "allow_anonymous": True, "allow_repeat": False,
        "deadline": None, "status": "draft", "revision": 7, "schema_version": 1,
        "created_at": now, "updated_at": now,
        "questions": [{
            "id": "q_" + "a" * 32, "title": "选择", "type": "single_choice",
            "required": True, "options": [
                {"id": "opt_" + "b" * 32, "label": "甲"},
                {"id": "opt_" + "c" * 32, "label": "乙"},
            ],
        }],
    }


def _legacy_collection(database, originals=None):
    database.create_collection(
        "surveys", validator=LEGACY_VALIDATOR,
        validationLevel="strict", validationAction="error",
    )
    originals = [_original()] if originals is None else originals
    if originals:
        database.surveys.insert_many(deepcopy(originals))
    return originals


def test_migrate_preserves_originals_and_can_rerun_after_business_edit(database):
    originals = _legacy_collection(database, [_original(), _original()])
    database.users.insert_one({"marker": "untouched"})
    result = migrate_surveys_v2(database)
    assert result["state"] == "completed" and result["converted_count"] == 2
    assert database.surveys.options()["validator"] == CURRENT_VALIDATOR
    snapshots = database[result["backup_collection"]]
    for original in originals:
        assert snapshots.find_one({"_id": original["_id"]}) == original
        upgraded = database.surveys.find_one({"_id": original["_id"]})
        assert upgraded == {
            **original, "schema_version": 2, "share_id": None,
            "published_at": None, "closed_at": None,
        }
    assert database.users.find_one()["marker"] == "untouched"
    database.surveys.update_one(
        {"_id": originals[0]["_id"]}, {"$set": {"title": "迁移后修改", "revision": 8}}
    )
    assert migrate_surveys_v2(database)["state"] == "already_v2"
    assert database.schema_migrations.count_documents({}) == 1
    assert snapshots.find_one({"_id": originals[0]["_id"]}) == originals[0]


def test_dry_run_is_readonly_and_empty_database_is_not_initialized(database):
    assert migrate_surveys_v2(database, dry_run=True)["state"] == "absent"
    assert database.list_collection_names() == []
    originals = _legacy_collection(database)
    before = set(database.list_collection_names())
    result = migrate_surveys_v2(database, dry_run=True)
    assert result["state"] == "dry_run" and result["v1_count"] == 1
    assert set(database.list_collection_names()) == before
    assert database.surveys.options()["validator"] == LEGACY_VALIDATOR
    assert list(database.surveys.find()) == originals


@pytest.mark.parametrize("problem", ["unknown_validator", "invalid_document", "unknown_version"])
def test_invalid_input_is_rejected_without_migration_writes(database, problem):
    originals = _legacy_collection(database)
    if problem == "unknown_validator":
        database.command({"collMod": "surveys", "validator": {"title": {"$exists": True}}})
    else:
        change = {"title": ""} if problem == "invalid_document" else {"schema_version": 99}
        database.surveys.update_one({}, {"$set": change}, bypass_document_validation=True)
    before = list(database.surveys.find())
    options = database.surveys.options()
    with pytest.raises(RuntimeError):
        migrate_surveys_v2(database)
    assert list(database.surveys.find()) == before
    assert database.surveys.options() == options
    assert database.list_collection_names() == ["surveys"]
    assert originals[0]["_id"] == before[0]["_id"]


def test_resume_after_interrupted_conversion_retains_first_snapshot(database):
    originals = _legacy_collection(database, [_original(), _original()])
    real_update = type(database.surveys).update_many

    def interrupt_update(collection, *args, **kwargs):
        if collection.name == "surveys":
            # Model an interruption after MongoDB updated only some documents.
            collection.update_one({"_id": originals[0]["_id"]}, args[1])
            raise OperationFailure("simulated process interruption")
        return real_update(collection, *args, **kwargs)

    with patch("pymongo.synchronous.collection.Collection.update_many", interrupt_update):
        with pytest.raises(OperationFailure, match="interruption"):
            migrate_surveys_v2(database)
    record = database.schema_migrations.find_one({"_id": MIGRATION_ID})
    assert record["state"] == "in_progress"
    assert database.surveys.options()["validator"] == TRANSITION_VALIDATOR
    assert database.surveys.count_documents({"schema_version": 1}) == 1
    backup_name = record["backup_collection"]
    assert database[backup_name].count_documents({}) == 2
    collections = set(database.list_collection_names())
    assert migrate_surveys_v2(database, dry_run=True)["state"] == "dry_run"
    assert database.schema_migrations.find_one({"_id": MIGRATION_ID}) == record
    assert migrate_surveys_v2(database)["state"] == "completed"
    assert set(database.list_collection_names()) == collections
    for original in originals:
        assert database[backup_name].find_one({"_id": original["_id"]}) == original


def test_unknown_metadata_and_unowned_transition_are_rejected(database):
    _legacy_collection(database)
    database.schema_migrations.insert_one({"_id": MIGRATION_ID, "state": "other"})
    before = database.surveys.find_one()
    with pytest.raises(RuntimeError, match="metadata"):
        migrate_surveys_v2(database)
    assert database.surveys.find_one() == before
    database.schema_migrations.delete_one({"_id": MIGRATION_ID})
    database.command({"collMod": "surveys", "validator": TRANSITION_VALIDATOR})
    with pytest.raises(RuntimeError, match="no matching migration record"):
        migrate_surveys_v2(database)
    assert database.surveys.find_one() == before


def test_new_collection_needs_no_migration_record(database):
    database.create_collection("surveys", validator=CURRENT_VALIDATOR)
    before = database.list_collection_names()
    assert migrate_surveys_v2(database)["state"] == "already_v2"
    assert database.list_collection_names() == before
