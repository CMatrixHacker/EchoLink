"""端到端:真实 TCP 服务 + 真实客户端,覆盖全部命令族。"""

from __future__ import annotations

import hashlib
import json
import os
import time

import pytest

from echolink.client.core import EchoLinkClient
from echolink.common.auth import generate_secret
from echolink.common.errors import RemoteError
from echolink.platform.generic import NullBackend
from echolink.server.config import Limits

from conftest import connect, make_server


# ==================== 握手与元数据 ====================
def test_handshake_exposes_metadata(client):
    assert client.connected is True
    assert client.server["name"] == "测试电脑"
    assert client.server["protocol"] == 1
    assert client.roots, "应至少暴露一个根目录"
    assert client.max_chunk >= 1024
    assert "system.info" in client.command_names()
    assert client.capabilities["screenshot"] is True


def test_ping(client):
    result = client.ping()
    assert result["pong"] > 0
    assert result["session"]


def test_capabilities_command(client):
    data = client.call("system.capabilities")
    assert data["capabilities"]["lock"] is True
    assert "files.list" in data["commands"]


def test_auth_with_wrong_secret_fails(server):
    port = server.info()["tcp"]["port"]
    with pytest.raises(RemoteError) as info:
        EchoLinkClient.connect(f"127.0.0.1:{port}", generate_secret(), timeout=5.0)
    assert info.value.code == "auth_failed"


def test_unknown_command(client):
    with pytest.raises(RemoteError) as info:
        client.call("system.nonexistent")
    assert info.value.code == "unknown_command"


def test_bad_request_payload(client):
    with pytest.raises(RemoteError) as info:
        client.call("system.volume", {"level": 999})
    assert info.value.code == "bad_request"


# ==================== 文件浏览 ====================
def test_list_root(client, workspace):
    data = client.list_dir()
    names = [item["name"] for item in data["entries"]]
    assert "docs" in names
    assert "big.bin" in names
    assert ".hidden.txt" not in names
    assert data["dirs"] == 1
    assert data["files"] == 1


def test_list_shows_hidden_when_asked(client):
    data = client.list_dir("", show_hidden=True)
    assert ".hidden.txt" in [item["name"] for item in data["entries"]]


def test_list_subdirectory_and_parent(client):
    data = client.list_dir("docs")
    assert data["path"] == "docs"
    assert data["parent"] == "."
    assert data["entries"][0]["name"] == "readme.txt"


def test_stat_and_exists(client):
    info = client.stat("docs/readme.txt")
    assert info["type"] == "file"
    assert info["size"] > 0
    assert client.call("files.exists", {"path": "docs"})["exists"] is True
    assert client.call("files.exists", {"path": "nope.txt"})["exists"] is False


def test_path_escape_is_forbidden(client):
    with pytest.raises(RemoteError) as info:
        client.list_dir("../../Windows")
    assert info.value.code == "forbidden"


def test_mkdir_rename_copy_delete(client, workspace):
    client.mkdir("newdir")
    assert (workspace / "newdir").is_dir()

    resolved = client.rename("newdir", "renamed")
    assert resolved["to"] == "renamed"
    assert (workspace / "renamed").is_dir()

    client.call("files.copy", {"source": "docs/readme.txt", "destination": "renamed/copy.txt"})
    assert (workspace / "renamed" / "copy.txt").read_text(encoding="utf-8").startswith("hello")

    client.delete("renamed/copy.txt")
    assert not (workspace / "renamed" / "copy.txt").exists()

    client.delete("renamed", recursive=True)
    assert not (workspace / "renamed").exists()


def test_delete_directory_needs_recursive(client):
    client.mkdir("dir2")
    with pytest.raises(RemoteError) as info:
        client.call("files.delete", {"path": "dir2"})
    assert info.value.code == "bad_request"


def test_delete_recursive_needs_confirm(client):
    client.mkdir("dir3")
    with pytest.raises(RemoteError) as info:
        client.call("files.delete", {"path": "dir3", "recursive": True})
    assert info.value.code == "confirm_required"
    assert (client.call("files.exists", {"path": "dir3"}))["exists"] is True


def test_search(client):
    data = client.search("", "*.txt")
    names = [item["name"] for item in data["results"]]
    assert "readme.txt" in names
    assert data["count"] >= 1


def test_hash_matches_local(client, workspace):
    expected = hashlib.sha256((workspace / "big.bin").read_bytes()).hexdigest()
    assert client.hash_file("big.bin")["digest"] == expected
    assert client.hash_file("docs/readme.txt", "md5")["digest"] == hashlib.md5(
        (workspace / "docs" / "readme.txt").read_bytes()
    ).hexdigest()


def test_preview_text_and_binary(client):
    text = client.preview("docs/readme.txt")
    assert text["binary"] is False
    assert "hello echolink" in text["text"]

    binary = client.preview("big.bin")
    assert binary["binary"] is True


# ==================== 上传 / 下载 ====================
def test_download_file(client, tmp_path, workspace):
    target = tmp_path / "downloaded.bin"
    progress = []
    result = client.download("big.bin", str(target), progress=lambda done, total: progress.append((done, total)))
    assert result["size"] == 307_200
    assert target.read_bytes() == (workspace / "big.bin").read_bytes()
    assert progress and progress[-1][0] == 307_200
    assert progress[-1][1] == 307_200


def test_upload_file(client, tmp_path, workspace):
    source = tmp_path / "upload.bin"
    payload = os.urandom(200_000)
    source.write_bytes(payload)
    progress = []
    result = client.upload(str(source), "incoming.bin", progress=lambda done, total: progress.append(done))
    assert (workspace / "incoming.bin").read_bytes() == payload
    assert result["size"] == len(payload)
    assert progress[-1] == len(payload)


def test_upload_conflict_without_overwrite(client, tmp_path):
    source = tmp_path / "dup.txt"
    source.write_text("first", encoding="utf-8")
    client.upload(str(source), "dup.txt")
    with pytest.raises(RemoteError) as info:
        client.upload(str(source), "dup.txt", overwrite=False)
    assert info.value.code == "conflict"


def test_upload_overwrite(client, tmp_path, workspace):
    source = tmp_path / "over.txt"
    source.write_text("v1", encoding="utf-8")
    client.upload(str(source), "over.txt")
    source.write_text("v2", encoding="utf-8")
    client.upload(str(source), "over.txt", overwrite=True)
    assert (workspace / "over.txt").read_text(encoding="utf-8") == "v2"


def test_no_partial_file_when_upload_aborts(client, workspace):
    """打开写入后不提交,目标文件不应出现。"""
    meta = client.call("files.open_write", {"path": "aborted.bin"})
    client.call("files.write", {"handle": meta["handle"], "offset": 0, "data": "YWJj"})
    client.call("files.close", {"handle": meta["handle"], "commit": False})
    assert not (workspace / "aborted.bin").exists()
    assert not list(workspace.glob("aborted.bin.echolink-part*"))


def test_read_handle_close_invalidates(client):
    meta = client.call("files.open_read", {"path": "docs/readme.txt"})
    client.call("files.close", {"handle": meta["handle"]})
    with pytest.raises(RemoteError) as info:
        client.call("files.read", {"handle": meta["handle"]})
    assert info.value.code == "not_found"


def test_download_small_file_empty(client, tmp_path, workspace):
    (workspace / "empty.txt").write_text("", encoding="utf-8")
    target = tmp_path / "empty.txt"
    client.download("empty.txt", str(target))
    assert target.read_bytes() == b""


# ==================== 系统控制 ====================
def test_system_info(client):
    data = client.info()
    assert data["server"] == "测试电脑"
    assert data["platform"] == "测试后端"
    assert data["capabilities"]["mute"] is True
    assert data["roots"]


def test_mute_and_volume(client, backend):
    assert client.mute("on")["muted"] is True
    assert client.mute("off")["muted"] is False
    assert client.mute("toggle")["muted"] is True
    assert client.call("system.audio")["muted"] is True
    assert client.volume(35)["level"] == 35
    assert backend.calls[-1]["action"] == "set_volume"


def test_lock_and_sleep_need_no_confirm_but_power_does(client, backend):
    client.lock()
    assert {"action": "lock"} in backend.calls
    client.sleep()
    assert {"action": "sleep"} in backend.calls


def test_shutdown_requires_confirm(client, backend):
    before = len(backend.calls)
    with pytest.raises(RemoteError) as info:
        client.call("system.shutdown")
    assert info.value.code == "confirm_required"
    assert len(backend.calls) == before, "未确认时不应真的执行"

    client.shutdown(delay=5, force=True)
    assert backend.calls[-1] == {"action": "shutdown", "restart": False, "delay": 5, "force": True}


def test_restart_and_logoff(client, backend):
    client.restart()
    assert backend.calls[-1]["restart"] is True
    client.logoff()
    assert {"action": "logoff"} in backend.calls


def test_screenshot_returns_png(client):
    result = client.screenshot()
    assert result["format"] == "png"
    assert result["size"] > 0
    assert result["data"].startswith(b"\x89PNG")


def test_screenshot_saves_to_disk(client, tmp_path):
    target = tmp_path / "shot.png"
    result = client.screenshot(str(target))
    assert target.exists()
    assert target.read_bytes()[:4] == b"\x89PNG"
    assert result["path"] == str(target)


def test_processes_and_kill(client, backend):
    data = client.processes(10)
    assert data["count"] >= 1
    pid = data["processes"][0]["pid"]
    client.kill(pid)
    assert backend.calls[-1] == {"action": "kill", "pid": pid}


def test_kill_needs_confirm(client):
    with pytest.raises(RemoteError) as info:
        client.call("system.kill", {"pid": 1234})
    assert info.value.code == "confirm_required"


# ==================== 桌面 ====================
def test_clipboard_roundtrip(client):
    client.clipboard_set("远程写入的内容 🎉")
    assert client.clipboard_get() == "远程写入的内容 🎉"


def test_notify_open_media(client, backend):
    client.notify("标题", "正文")
    client.open_target("https://example.com")
    client.media("next")
    actions = [item["action"] for item in backend.calls]
    assert "notify" in actions
    assert "open" in actions
    assert "media" in actions


# ==================== 权限开关 ====================
def test_write_disabled(workspace, secret):
    server = make_server(workspace, secret, allow_write=False)
    connection = connect(server, secret)
    try:
        with pytest.raises(RemoteError) as info:
            connection.mkdir("nope")
        assert info.value.code == "forbidden"
        # 读取仍然可用
        assert connection.list_dir()["count"] >= 1
    finally:
        connection.close()
        server.stop()


def test_delete_disabled(workspace, secret):
    server = make_server(workspace, secret, allow_delete=False)
    connection = connect(server, secret)
    try:
        with pytest.raises(RemoteError) as info:
            connection.delete("docs/readme.txt")
        assert info.value.code == "forbidden"
    finally:
        connection.close()
        server.stop()


def test_power_disabled(workspace, secret, backend):
    server = make_server(workspace, secret, backend=backend, allow_power=False)
    connection = connect(server, secret)
    try:
        with pytest.raises(RemoteError) as info:
            connection.call("system.shutdown", {"confirm": True})
        assert info.value.code == "forbidden"
        assert backend.calls == []
    finally:
        connection.close()
        server.stop()


def test_session_limit(workspace, secret):
    server = make_server(workspace, secret, limits=Limits(max_sessions=1))
    first = connect(server, secret)
    try:
        with pytest.raises(RemoteError) as info:
            connect(server, secret)
        assert info.value.code == "busy"
    finally:
        first.close()
        server.stop()


# ==================== 审计日志 ====================
def test_audit_log_is_written(server_config, client):
    client.info()
    client.clipboard_set("super-secret-clipboard-text")
    time.sleep(0.2)
    path = server_config.audit_file
    assert os.path.exists(path)
    lines = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    commands = [item["cmd"] for item in lines]
    assert "system.info" in commands
    assert "desktop.clipboard_set" in commands
    raw = open(path, encoding="utf-8").read()
    assert "super-secret-clipboard-text" not in raw, "剪贴板内容不应落进审计日志"


def test_server_info_reports_sessions(server, client):
    info = server.info()
    assert info["sessions"]
    assert info["sessions"][0]["client"].startswith("pytest")
    assert info["tcp"]["port"] > 0
