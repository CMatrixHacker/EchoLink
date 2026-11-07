"""Windows 可执行文件入口(控制台版)。

保留控制台,方便直接看到服务端日志与命令行输出。
"""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from echolink.__main__ import main as app_main

    return app_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
