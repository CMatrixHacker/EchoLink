"""平台后端选择入口。"""

from __future__ import annotations

import sys

from echolink.platform.base import FEATURES, SystemBackend

__all__ = ["FEATURES", "SystemBackend", "get_backend"]


def get_backend(name: str = "auto") -> SystemBackend:
    """按名称创建后端。

    ``auto``  —— 按当前操作系统自动选择
    ``null``  —— 测试用空后端
    ``windows``/``posix`` —— 强制指定
    """
    key = (name or "auto").strip().lower()
    if key == "null":
        from echolink.platform.generic import NullBackend

        return NullBackend()
    if key == "windows":
        from echolink.platform.windows import WindowsBackend

        return WindowsBackend()
    if key in ("posix", "linux", "macos", "darwin"):
        from echolink.platform.generic import PosixBackend

        return PosixBackend()
    if key == "auto":
        if sys.platform.startswith("win"):
            from echolink.platform.windows import WindowsBackend

            return WindowsBackend()
        return _posix()
    raise ValueError(f"未知后端: {name}")


def _posix() -> SystemBackend:
    from echolink.platform.generic import PosixBackend

    return PosixBackend()
