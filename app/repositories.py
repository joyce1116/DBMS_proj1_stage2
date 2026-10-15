"""MongoDB persistence, separate from HTTP and account business rules."""

from datetime import datetime

from bson import ObjectId
from pymongo.database import Database


class AccountRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_user(self, document: dict) -> dict:
        self.db.users.insert_one(document)
        return document

    def find_user_by_key(self, key: str) -> dict | None:
        return self.db.users.find_one({"username_key": key})

    def find_user(self, user_id: ObjectId) -> dict | None:
        return self.db.users.find_one({"_id": user_id})

    def create_session(self, document: dict) -> None:
        self.db.sessions.insert_one(document)

    def find_active_session(self, token_hash: str, now: datetime) -> dict | None:
        # MongoDB's TTL worker deletes asynchronously. Always check expiry here.
        return self.db.sessions.find_one(
            {"token_hash": token_hash, "expires_at": {"$gt": now}}
        )

    def delete_session(self, token_hash: str) -> None:
        self.db.sessions.delete_one({"token_hash": token_hash})
