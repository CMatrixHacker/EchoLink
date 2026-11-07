"""传输层抽象。

只要实现 :class:`Transport` 的两个方法(``recv_exact`` / ``send_all``),
上层协议就能跑在任何"字节流"之上 —— 这正是本项目的核心抽象:
TCP 走局域网、蓝牙 SPP 走虚拟串口、Android 走 RFCOMM socket,行为完全一致。
"""

from __future__ import annotations

import abc
from typing import Optional


class TransportError(Exception):
    """传输层通用错误。"""


class TransportTimeout(TransportError):
    """在给定时间内没有读到足够数据。"""


class TransportClosed(TransportError):
    """连接被对端或本地关闭。"""


class Transport(abc.ABC):
    """双向字节流。"""

    name = "transport"

    @property
    def describe(self) -> str:
        return self.name

    @abc.abstractmethod
    def recv_exact(self, n: int, timeout: Optional[float] = None) -> bytes:
        """精确读取 ``n`` 个字节;超时抛 :class:`TransportTimeout`,关闭抛 :class:`TransportClosed`。"""

    @abc.abstractmethod
    def send_all(self, data: bytes, timeout: Optional[float] = None) -> None:
        """把 ``data`` 全部写出。"""

    @abc.abstractmethod
    def close(self) -> None:
        """关闭连接(必须可重复调用)。"""

    # -- 便于 with 语句使用 --
    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<{type(self).__name__} {self.describe}>"
