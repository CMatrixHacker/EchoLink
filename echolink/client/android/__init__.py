"""Android 客户端包(Kivy)。"""

from __future__ import annotations

__all__ = ["main"]


def main() -> None:  # pragma: no cover - 需要 Kivy 运行环境
    from echolink.client.android.main import run

    run()
