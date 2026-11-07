"""配置读写与校验。"""

from __future__ import annotations

import json
import os

import pytest

from echolink.common.auth import generate_secret
from echolink.server.config import (
    Limits,
    ServerConfig,
    TcpConfig,
    default_config,
)


def test_default_config_has_secret_and_roots(tmp_path):
    config = default_config(str(tmp_path / "server.json"))
    assert len(config.secret) == 64
    assert config.roots
    config.validate()


def test_save_and_load_roundtrip(tmp_path, workspace):
    path = tmp_path / "server.json"
    config = ServerConfig(
        name="书桌电脑",
        secret=generate_secret(),
        roots=[str(workspace)],
        tcp=TcpConfig(enabled=True, host="127.0.0.1", port=9999),
    )
    config.save(str(path))
    loaded = ServerConfig.load(str(path))
    assert loaded.name == "书桌电脑"
    assert loaded.secret == config.secret
    assert loaded.roots == [os.path.normpath(str(workspace))]
    assert loaded.tcp.port == 9999
    loaded.validate()


def test_saved_file_is_json_and_utf8(tmp_path, workspace):
    path = tmp_path / "server.json"
    config = ServerConfig(name="中文名", secret=generate_secret(), roots=[str(workspace)])
    config.save(str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["name"] == "中文名"
    assert data["permissions"]["write"] is True


def test_relative_roots_resolved_against_config_dir(tmp_path):
    base = tmp_path / "cfg"
    base.mkdir()
    (base / "shared").mkdir()
    config_file = base / "server.json"
    config_file.write_text(
        json.dumps({"secret": generate_secret(), "roots": ["shared"], "tcp": {"port": 8765}}),
        encoding="utf-8",
    )
    loaded = ServerConfig.load(str(config_file))
    assert loaded.roots[0] == os.path.normpath(str(base / "shared"))


def test_validate_requires_secret(workspace):
    config = ServerConfig(secret="", roots=[str(workspace)])
    with pytest.raises(ValueError):
        config.validate()


def test_validate_requires_roots(secret):
    config = ServerConfig(secret=secret, roots=[])
    with pytest.raises(ValueError):
        config.validate()


def test_validate_requires_some_transport(secret, workspace):
    config = ServerConfig(secret=secret, roots=[str(workspace)], tcp=TcpConfig(enabled=False))
    with pytest.raises(ValueError):
        config.validate()


def test_validate_rejects_bad_port(secret, workspace):
    config = ServerConfig(secret=secret, roots=[str(workspace)], tcp=TcpConfig(port=70000))
    with pytest.raises(ValueError):
        config.validate()


def test_limits_validation():
    with pytest.raises(ValueError):
        Limits.from_dict({"max_chunk": 10_000_000})
    with pytest.raises(ValueError):
        Limits.from_dict({"max_sessions": 0})


def test_permissions_from_dict(workspace, secret):
    config = ServerConfig.from_dict(
        {
            "secret": secret,
            "roots": [str(workspace)],
            "permissions": {"write": False, "delete": False, "power": False, "kill_process": False},
        }
    )
    assert config.allow_write is False
    assert config.allow_delete is False
    assert config.allow_power is False
    assert config.allow_kill_process is False


def test_secret_bytes_derived(workspace):
    secret = generate_secret()
    config = ServerConfig(secret=secret, roots=[str(workspace)])
    assert len(config.secret_bytes) == 32
