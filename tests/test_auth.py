"""HMAC 挑战-应答认证。"""

from __future__ import annotations

import os

import pytest

from echolink.common.auth import (
    SecretError,
    compute_mac,
    generate_secret,
    new_nonce,
    parse_secret,
    read_secret_file,
    verify_mac,
    write_secret_file,
)


def test_generate_secret_shape():
    secret = generate_secret()
    assert len(secret) == 64
    assert len(bytes.fromhex(secret)) == 32
    assert generate_secret() != secret


def test_parse_secret_hex_and_passphrase():
    hexed = generate_secret()
    assert len(parse_secret(hexed)) == 32
    assert parse_secret("a-sufficiently-long-passphrase") == b"a-sufficiently-long-passphrase"


def test_parse_secret_rejects_short():
    with pytest.raises(SecretError):
        parse_secret("short")
    with pytest.raises(SecretError):
        parse_secret("")


def test_mac_matches_and_rejects_wrong_secret():
    secret = parse_secret(generate_secret())
    nonce = new_nonce()
    mac = compute_mac(secret, nonce, "phone-1")
    assert verify_mac(secret, nonce, "phone-1", mac)
    assert not verify_mac(secret, nonce, "phone-2", mac)
    assert not verify_mac(secret, nonce, "phone-1", "deadbeef")
    assert not verify_mac(parse_secret(generate_secret()), nonce, "phone-1", mac)


def test_mac_changes_with_nonce():
    secret = parse_secret(generate_secret())
    assert compute_mac(secret, new_nonce(), "c") != compute_mac(secret, new_nonce(), "c")


def test_verify_mac_is_case_insensitive_and_strips():
    secret = parse_secret(generate_secret())
    nonce = new_nonce()
    mac = compute_mac(secret, nonce, "c")
    assert verify_mac(secret, nonce, "c", f"  {mac.upper()}  ")


def test_verify_mac_handles_empty():
    secret = parse_secret(generate_secret())
    assert not verify_mac(secret, new_nonce(), "c", "")


def test_secret_file_roundtrip(tmp_path):
    path = tmp_path / "key.txt"
    secret = write_secret_file(str(path))
    assert read_secret_file(str(path)) == secret
    assert len(parse_secret(secret)) == 32
    with pytest.raises(SecretError):
        write_secret_file(str(path))  # 已存在且未允许覆盖


def test_secret_file_with_key_value(tmp_path):
    path = tmp_path / "key.conf"
    path.write_text("# 注释\nsecret=abcdefghijklmnopqrstuvwxyz012345\n", encoding="utf-8")
    assert read_secret_file(str(path)) == "abcdefghijklmnopqrstuvwxyz012345"


def test_secret_file_missing(tmp_path):
    with pytest.raises(SecretError):
        read_secret_file(str(tmp_path / "nope.txt"))
