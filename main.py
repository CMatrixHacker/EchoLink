"""buildozer / python-for-android 打包入口。

Android 上没有 "python -m" 的概念,p4a 要求应用根目录存在 ``main.py``
作为启动脚本,因此这里只做一件事:转向真正的界面实现。
"""

from __future__ import annotations

import os
import sys

# 打包进 APK 后,项目根目录并不在 sys.path 上,这里补一次
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def main() -> None:
    from echolink.client.android.main import run

    run()


if __name__ == "__main__":
    main()
