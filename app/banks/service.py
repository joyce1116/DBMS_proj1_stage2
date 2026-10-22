"""Bank organization preserves all immutable versions and survey snapshots."""

from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.banks.models import BankCreate, BankPatch
from app.banks.repository import BankRepository
from app.core.time import mongo_utc_now
from app.questions.service import question_version_public


class BankNotFound(Exception):
    pass


class BankConflict(Exception):
    pass


class InvalidBankChange(Exception):
    pass


def bank_public(document: dict, user_id: str) -> dict:
    return {
        "id": str(document["_id"]), "owner_id": str(document["owner_id"]),
        "title": document["title"], "description": document["description"],
        "revision": document["revision"], "created_at": document["created_at"],
        "updated_at": document["updated_at"],
        "can_manage": str(document["owner_id"]) == user_id,
    }


def entry_public(document: dict, version: dict) -> dict:
    return {
        "id": str(document["_id"]), "bank_id": str(document["bank_id"]),
        "family_id": str(document["family_id"]), "version_id": str(document["version_id"]),
        "created_at": document["created_at"], "updated_at": document["updated_at"],
        "question": question_version_public(version),
    }


class BankService:
    MAX_REVISION = 2147483647

    def __init__(self, repository: BankRepository):
        self.repository = repository

    @staticmethod
    def _id(value: str) -> ObjectId:
        if not ObjectId.is_valid(value):
            raise BankNotFound
        return ObjectId(value)

    def _bank(self, bank_id: str, user_id: str, *, manage: bool = False) -> dict:
        document = self.repository.find_active(self._id(bank_id))
        if document is None:
            raise BankNotFound
        if str(document["owner_id"]) != user_id:
            if manage or not self.repository.has_read_grant(document, self._id(user_id)):
                raise BankNotFound
        return document

    def _owned_version(self, version_id: str, user_id: str) -> dict:
        version = self.repository.find_owned_version(self._id(version_id), self._id(user_id))
        if version is None:
            raise BankNotFound
        return version

    @staticmethod
    def _page(items: list[dict], total: int, offset: int, limit: int) -> dict:
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def create(self, user_id: str, data: dict) -> dict:
        try:
            metadata = BankCreate.model_validate(data).model_dump()
        except ValidationError:
            raise InvalidBankChange("Bank metadata is invalid") from None
        now = mongo_utc_now()
        document = {
            "_id": ObjectId(), "owner_id": self._id(user_id), **metadata,
            "revision": 1, "archived": False, "created_at": now,
            "updated_at": now, "schema_version": 1,
        }
        return bank_public(self.repository.create(document), user_id)

    def detail(self, bank_id: str, user_id: str) -> dict:
        return bank_public(self._bank(bank_id, user_id), user_id)

    def list_banks(self, user_id: str, view: str, offset: int, limit: int) -> dict:
        if view not in ("owned", "shared"):
            raise InvalidBankChange("Bank view must be owned or shared")
        loader = self.repository.list_owned if view == "owned" else self.repository.list_shared
        documents, total = loader(self._id(user_id), offset, limit)
        return self._page([bank_public(item, user_id) for item in documents], total, offset, limit)

    def patch(self, bank_id: str, user_id: str, data: dict) -> dict:
        bank = self._bank(bank_id, user_id, manage=True)
        try:
            patch = BankPatch.model_validate(data)
        except ValidationError:
            raise InvalidBankChange("Bank metadata changes are invalid") from None
        if patch.expected_revision >= self.MAX_REVISION:
            raise BankConflict
        changes = patch.model_dump(exclude_unset=True, exclude={"expected_revision"})
        saved = self.repository.update_metadata(
            bank["_id"], bank["owner_id"], patch.expected_revision,
            {**changes, "updated_at": mongo_utc_now()},
        )
        if saved is None:
            raise BankConflict
        return bank_public(saved, user_id)

    def archive(self, bank_id: str, user_id: str, expected_revision: int) -> None:
        bank = self._bank(bank_id, user_id, manage=True)
        if expected_revision >= self.MAX_REVISION:
            raise BankConflict
        if self.repository.update_metadata(
            bank["_id"], bank["owner_id"], expected_revision,
            {"archived": True, "updated_at": mongo_utc_now()},
        ) is None:
            raise BankConflict
        # Entries, grants, versions and existing survey snapshots remain intact.
        # Any operation authorized before this write may finish on standalone
        # MongoDB; subsequent reads must first pass the active-bank check.

    def list_entries(self, bank_id: str, user_id: str, offset: int, limit: int) -> dict:
        bank = self._bank(bank_id, user_id)
        documents, total = self.repository.list_entries(bank["_id"], offset, limit)
        versions = self.repository.find_versions([item["version_id"] for item in documents])
        if any(item["version_id"] not in versions for item in documents):
            raise BankNotFound
        return self._page([
            entry_public(item, versions[item["version_id"]]) for item in documents
        ], total, offset, limit)

    def add_entry(self, bank_id: str, user_id: str, version_id: str) -> dict:
        bank = self._bank(bank_id, user_id, manage=True)
        version = self._owned_version(version_id, user_id)
        now = mongo_utc_now()
        document = {
            "_id": ObjectId(), "bank_id": bank["_id"],
            "family_id": version["family_id"], "version_id": version["_id"],
            "created_at": now, "updated_at": now, "schema_version": 1,
        }
        try:
            saved = self.repository.create_entry(document)
        except DuplicateKeyError as exc:
            if not exc.details or exc.details.get("keyPattern") != {"bank_id": 1, "family_id": 1}:
                raise
            raise BankConflict from None
        return entry_public(saved, version)

    def replace_entry(
        self, bank_id: str, family_id: str, user_id: str,
        version_id: str, expected_version_id: str,
    ) -> dict:
        bank = self._bank(bank_id, user_id, manage=True)
        family = self._id(family_id)
        expected = self._id(expected_version_id)
        if self.repository.find_entry(bank["_id"], family) is None:
            raise BankNotFound
        version = self._owned_version(version_id, user_id)
        if version["family_id"] != family:
            raise InvalidBankChange("The selected version must belong to the existing bank entry's family")
        saved = self.repository.update_entry(
            bank["_id"], family, expected, version["_id"], mongo_utc_now(),
        )
        if saved is None:
            raise BankConflict
        return entry_public(saved, version)

    def remove_entry(
        self, bank_id: str, family_id: str, user_id: str, expected_version_id: str,
    ) -> None:
        bank = self._bank(bank_id, user_id, manage=True)
        family = self._id(family_id)
        expected = self._id(expected_version_id)
        if self.repository.find_entry(bank["_id"], family) is None:
            raise BankNotFound
        if not self.repository.delete_entry(bank["_id"], family, expected):
            raise BankConflict
