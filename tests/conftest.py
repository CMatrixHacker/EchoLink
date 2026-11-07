"""pytest 公共夹具:一个真实运行的 TCP 服务 + 一个已认证客户端。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from echolink.client.core import EchoLinkClient  # noqa: E402
from echolink.common.auth import generate_secret  # noqa: E402
from echolink.platform.generic import NullBackend  # noqa: E402
from echolink.server.app import EchoLinkServer  # noqa: E402
from echolink.server.config import ServerConfig, TcpConfig  # noqa: E402


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "shared"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "readme.txt").write_text("hello echolink\n第二行\n", encoding="utf-8")
    (root / "big.bin").write_bytes(bytes(range(256)) * 1200)  # 307200 字节,跨多个分块
    (root / ".hidden.txt").write_text("hidden", encoding="utf-8")
    return root


@pytest.fixture()
def secret() -> str:
    return generate_secret()


@pytest.fixture()
def server_config(workspace: Path, secret: str, tmp_path: Path) -> ServerConfig:
    return ServerConfig(
        name="测试电脑",
        secret=secret,
        roots=[str(workspace)],
        audit_file=str(tmp_path / "logs" / "audit.jsonl"),
        tcp=TcpConfig(enabled=True, host="127.0.0.1", port=0),
    )


@pytest.fixture()
def backend() -> NullBackend:
    return NullBackend()


@pytest.fixture()
def server(server_config: ServerConfig, backend: NullBackend):
    instance = EchoLinkServer(server_config, backend=backend)
    instance.start()
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture()
def client(server: EchoLinkServer, secret: str):
    port = server.info()["tcp"]["port"]
    connection = EchoLinkClient.connect(f"127.0.0.1:{port}", secret, timeout=10.0, app="pytest")
    try:
        yield connection
    finally:
        connection.close()


def make_server(workspace: Path, secret: str, backend=None, **overrides) -> EchoLinkServer:
    """按需构造不同权限配置的服务实例。"""
    config = ServerConfig(
        name="测试电脑",
        secret=secret,
        roots=[str(workspace)],
        tcp=TcpConfig(enabled=True, host="127.0.0.1", port=0),
        **overrides,
    )
    instance = EchoLinkServer(config, backend=backend or NullBackend())
    instance.start()
    return instance


def connect(server: EchoLinkServer, secret: str, **kwargs) -> EchoLinkClient:
    port = server.info()["tcp"]["port"]
    return EchoLinkClient.connect(f"127.0.0.1:{port}", secret, timeout=10.0, app="pytest", **kwargs)
