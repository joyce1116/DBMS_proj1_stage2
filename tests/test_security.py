"""These tests run without starting or connecting to MongoDB."""

import hashlib

import pytest

from app.auth.security import (
    hash_password,
    hash_token,
    new_token,
    normalize_username,
    username_key,
    verify_password,
)


@pytest.mark.parametrize(
    ("supplied", "display", "key"),
    [
        ("  Alice_01  ", "Alice_01", "alice_01"),
        ("Ａｌｉｃｅ＿０１", "Alice_01", "alice_01"),
    ],
)
def test_username_normalization_preserves_display_case(supplied, display, key):
    assert normalize_username(supplied) == display
    assert username_key(supplied) == key


@pytest.mark.parametrize("value", ["ab", "用户名", "a" * 33])
def test_invalid_username_is_rejected(value):
    with pytest.raises(ValueError):
        normalize_username(value)


def test_password_hashes_are_salted_argon2_and_verify():
    password = "private-passphrase-42"
    first, second = hash_password(password), hash_password(password)
    assert first.startswith("$argon2id$")
    assert second.startswith("$argon2id$")
    assert first != second
    assert password not in first
    assert verify_password(password, first)
    assert not verify_password("wrong-passphrase", first)


def test_password_whitespace_is_part_of_the_secret():
    password = "  password with spaces  "
    encoded = hash_password(password)
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)


def test_session_tokens_are_random_and_hash_to_sha256():
    first, second = new_token(), new_token()
    assert len(first) >= 32
    assert first != second
    assert hash_token(first) == hashlib.sha256(first.encode("utf-8")).hexdigest()
    assert len(hash_token(first)) == 64
