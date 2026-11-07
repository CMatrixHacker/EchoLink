"""Windows 可执行文件入口(图形界面版,无控制台窗口)。

窗口版 exe 里 ``sys.stdout`` / ``sys.stderr`` 是 None,任何 ``print`` 都会抛异常,
所以先把它们指向 devnull;真正崩溃时用系统弹窗告知,而不是静默退出。
"""

from __future__ import annotations

import multiprocessing
import os
import sys
import traceback


def _silence_streams() -> None:
    if sys.stdout is None or sys.stderr is None:
        null = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 - 进程级常驻
        if sys.stdout is None:
            sys.stdout = null
        if sys.stderr is None:
            sys.stderr = null


def _fatal(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "EchoLink 启动失败", 0x10)
    except Exception:
        pass


def main() -> int:
    _silence_streams()
    try:
        from echolink.client.ui.window import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name in ("PySide6", "shiboken6"):
            _fatal("当前 Python 运行环境缺少 PySide6,图形界面不可用。\n\n"
                   "请改用同目录下的 echolink.exe(命令行版),或先 pip install PySide6 再打包。")
            return 1
        _fatal(f"加载界面失败:\n{exc}")
        return 1
    try:
        argv = ["EchoLink"] + [arg for arg in sys.argv[1:] if arg in ("--tray", "--minimized")]
        return int(gui_main(argv) or 0)
    except Exception:  # noqa: BLE001 - 兜底,避免闪退无提示
        _fatal(f"运行出错:\n{traceback.format_exc(limit=3)}")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
