import hashlib
import re
import secrets
import unicodedata

from pwdlib import PasswordHash


_password_hasher = PasswordHash.recommended()
# Run the same expensive verification for unknown usernames and wrong passwords.
_dummy_hash = _password_hasher.hash(secrets.token_urlsafe(32))


def normalize_username(value: str) -> str:
    """Normalize presentation while keeping the user's chosen ASCII case."""
    username = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
        raise ValueError("Username must contain 3-32 ASCII letters, digits or underscores")
    return username


def username_key(username: str) -> str:
    return normalize_username(username).casefold()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _password_hasher.verify(password, password_hash)


def verify_missing_user(password: str) -> None:
    _password_hasher.verify(password, _dummy_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # A high-entropy random token can use a fast digest; passwords use Argon2.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
