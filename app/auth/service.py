from datetime import datetime, timedelta, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.auth.repository import AccountRepository
from app.core.time import mongo_utc_now
from app.auth.security import (
    hash_password, hash_token, new_token, username_key,
    verify_missing_user, verify_password,
)


class UsernameTaken(Exception):
    pass


class InvalidCredentials(Exception):
    pass


def public_user(document: dict) -> dict:
    return {
        "id": str(document["_id"]),
        "username": document["username"],
        "created_at": document["created_at"],
    }


class AccountService:
    def __init__(self, repository: AccountRepository, session_ttl_seconds: int):
        self.repository = repository
        self.session_ttl_seconds = session_ttl_seconds

    def register(self, username: str, password: str) -> dict:
        user = {
            "_id": ObjectId(),
            "username": username,
            "username_key": username_key(username),
            "password_hash": hash_password(password),
            "created_at": mongo_utc_now(),
            "schema_version": 1,
        }
        try:
            self.repository.create_user(user)
        except DuplicateKeyError:
            # The unique index also handles concurrent registrations correctly.
            raise UsernameTaken from None
        return public_user(user)

    def login(self, username: str, password: str) -> dict:
        user = self.repository.find_user_by_key(username_key(username))
        if user is None:
            verify_missing_user(password)
            raise InvalidCredentials
        if not verify_password(password, user["password_hash"]):
            raise InvalidCredentials

        now = mongo_utc_now()
        expires_at = now + timedelta(seconds=self.session_ttl_seconds)
        token = new_token()
        self.repository.create_session({
            "_id": ObjectId(),
            "user_id": user["_id"],
            "token_hash": hash_token(token),
            "created_at": now,
            "expires_at": expires_at,
            "schema_version": 1,
        })
        return {"access_token": token, "token_type": "bearer",
                "expires_at": expires_at, "user": public_user(user)}

    def current_user(self, token: str) -> dict:
        session = self.repository.find_active_session(hash_token(token), datetime.now(timezone.utc))
        if session is None:
            raise InvalidCredentials
        user = self.repository.find_user(session["user_id"])
        if user is None:
            raise InvalidCredentials
        return public_user(user)

    def logout(self, token: str) -> None:
        self.current_user(token)
        self.repository.delete_session(hash_token(token))
