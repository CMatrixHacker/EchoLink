"""EchoLink 控制台界面（PySide6）。

对外只暴露 ``run()`` 与 ``main()``，其余模块内部使用。
"""

from __future__ import annotations

__all__ = ["main"]


def main(argv=None) -> int:
    from echolink.client.ui.window import main as _main

    return _main(argv)
