"""客户端包:核心 API + CLI + 桌面 GUI + Android App。"""

from __future__ import annotations

from echolink.client.core import EchoLinkClient, join_remote  # noqa: F401

__all__ = ["EchoLinkClient", "join_remote"]
