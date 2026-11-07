"""开机自启动开关。

Windows 上写 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``，
只影响当前用户、不需要管理员权限，也不改动系统服务，卸载时删键即可。
其它平台退化为 XDG autostart 的 ``.desktop`` 文件。
"""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from typing import Optional

APP_KEY = "EchoLink"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUNTIME_DIR = Path.home() / ".echolink"
NO_WINDOW = "--tray"


def _launch_command(extra: str = NO_WINDOW) -> str:
    """返回自启动时执行的命令行。

    打包后 ``sys.frozen`` 为真，直接用 exe 本身；
    源码运行时用 pythonw.exe（避免弹控制台），找不到再退回 python.exe。
    """
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        exe = sys.executable
        candidate = Path(exe).with_name("pythonw.exe")
        if candidate.exists():
            exe = str(candidate)
        return f'"{exe}" -m echolink gui {extra}'.strip()
    return f'"{exe}" {extra}'.strip()


def _is_windows() -> bool:
    return os.name == "nt"


def _xdg_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))
    return base / "autostart" / "echolink.desktop"


def status() -> dict:
    """返回 {"enabled": bool, "command": str, "supported": bool, "where": str}"""
    if _is_windows():
        import winreg  # noqa: PLC0415 - 仅 Windows 可用

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, APP_KEY)
                return {"enabled": True, "command": str(value), "supported": True, "where": f"HKCU\\{RUN_KEY}"}
        except FileNotFoundError:
            return {"enabled": False, "command": "", "supported": True, "where": f"HKCU\\{RUN_KEY}"}
        except OSError as exc:
            return {"enabled": False, "command": "", "supported": False, "where": str(exc)}

    path = _xdg_path()
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if line.startswith("Exec="):
                return {"enabled": True, "command": line[5:], "supported": True, "where": str(path)}
        return {"enabled": True, "command": "", "supported": True, "where": str(path)}
    return {"enabled": False, "command": "", "supported": True, "where": str(path)}


def enable(extra: str = NO_WINDOW) -> dict:
    command = _launch_command(extra)
    if _is_windows():
        import winreg  # noqa: PLC0415

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_KEY, 0, winreg.REG_SZ, command)
    else:
        path = _xdg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "[Desktop Entry]",
                    "Type=Application",
                    "Name=EchoLink",
                    "Comment=EchoLink 控制台（托盘常驻）",
                    f"Exec={command}",
                    "Terminal=false",
                    "X-GNOME-Autostart-enabled=true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    return {"enabled": True, "command": command}


def disable() -> dict:
    if _is_windows():
        import winreg  # noqa: PLC0415

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_KEY)
        except FileNotFoundError:
            pass
    else:
        path = _xdg_path()
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return {"enabled": False}


def set_enabled(enabled: bool, extra: str = NO_WINDOW) -> dict:
    return enable(extra) if enabled else disable()
