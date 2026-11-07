"""非 Windows 平台后端(开发调试 / Linux / macOS)以及测试用空后端。

Linux/macOS 实现走系统命令,够用即可;Windows 才是本项目的目标平台。
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import signal
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from echolink.common.errors import CommandError, Unsupported
from echolink.platform.base import SystemBackend


def _run(cmd: List[str], timeout: float = 15.0) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise Unsupported(f"缺少系统命令: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"命令执行超时: {' '.join(cmd)}") from exc


def _which(name: str) -> bool:
    return shutil.which(name) is not None


class PosixBackend(SystemBackend):
    """Linux / macOS 尽力而为的实现。"""

    name = "posix"
    platform_label = "Linux/macOS"

    def __init__(self) -> None:
        self.is_mac = _platform.system() == "Darwin"
        self.platform_label = "macOS" if self.is_mac else "Linux"

    # ---------- 信息 ----------
    def uptime(self) -> float:
        try:
            if not self.is_mac:
                with open("/proc/uptime", "r", encoding="utf-8") as handle:
                    return float(handle.read().split()[0])
        except OSError:
            pass
        try:
            return time.monotonic()
        except Exception:  # pragma: no cover
            return 0.0

    def info(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "platform": self.platform_label,
            "backend": self.name,
            "hostname": _platform.node(),
            "user": os.environ.get("USER", ""),
            "os": f"{_platform.system()} {_platform.release()}",
            "arch": _platform.machine(),
            "python": _platform.python_version(),
            "cpu_count": os.cpu_count(),
            "uptime_seconds": round(self.uptime(), 1),
            "audio": self.audio_status(),
            "capabilities": self.capabilities(),
            "resources": {},
        }
        try:
            usage = shutil.disk_usage("/")
            payload["resources"].update({"disk_total": usage.total, "disk_free": usage.free})
        except OSError:  # pragma: no cover
            pass
        return payload

    # ---------- 声音 ----------
    def set_mute(self, state: str = "toggle") -> Dict[str, Any]:
        state = (state or "toggle").lower()
        if self.is_mac:
            if state in ("toggle", "switch"):
                script = 'set cur to output muted of (get volume settings)\nset volume output muted (not cur)'
                result = _run(["osascript", "-e", script])
                return {"muted": "true" in (result.stdout or b"").decode().strip().lower()}
            value = "true" if state in ("on", "mute", "muted") else "false"
            _run(["osascript", "-e", f"set volume output muted {value}"])
            return {"muted": value == "true", "exact": True}
        if not _which("amixer"):
            raise Unsupported("缺少 amixer(alsa-utils)")
        _run(["amixer", "-D", "pulse", "sset", "Master", "toggle"])
        return {"muted": None, "exact": False}

    def set_volume(self, level: int) -> Dict[str, Any]:
        level = max(0, min(int(level), 100))
        if self.is_mac:
            _run(["osascript", "-e", f"set volume output volume {level}"])
            return {"level": level, "exact": True}
        if not _which("amixer"):
            raise Unsupported("缺少 amixer(alsa-utils)")
        _run(["amixer", "-D", "pulse", "sset", "Master", f"{level}%"])
        return {"level": level, "exact": True}

    def audio_status(self) -> Dict[str, Any]:
        if self.is_mac:
            result = _run(["osascript", "-e", "get volume settings"])
            text = (result.stdout or b"").decode(errors="ignore")
            muted = "true" in text.split("muted:")[-1] if "muted:" in text else None
            level = None
            if "output volume:" in text:
                try:
                    level = int(text.split("output volume:")[1].split(",")[0].strip())
                except (IndexError, ValueError):
                    level = None
            return {"exact": True, "muted": muted, "level": level}
        return {"exact": False, "muted": None, "level": None}

    # ---------- 电源 ----------
    def lock(self) -> Dict[str, Any]:
        if self.is_mac:
            _run(["osascript", "-e", 'tell application "System Events" to keystroke "q" using {command down, control down}'])
            return {"locked": True}
        for cmd in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"], ["gnome-screensaver-command", "-l"]):
            if _which(cmd[0]):
                _run(cmd)
                return {"locked": True}
        raise Unsupported("没有可用的锁屏命令")

    def sleep(self) -> Dict[str, Any]:
        if self.is_mac:
            _run(["pmset", "sleepnow"])
            return {"sleep": True}
        if _which("systemctl"):
            _run(["systemctl", "suspend"])
            return {"sleep": True}
        _run(["pm-suspend"])
        return {"sleep": True}

    def shutdown_power(self, restart: bool = False, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        if self.is_mac:
            action = "restart" if restart else "shut down"
            _run(["osascript", "-e", f'tell application "System Events" to {action}'])
            return {"action": "restart" if restart else "shutdown"}
        if _which("systemctl"):
            _run(["systemctl", "reboot" if restart else "poweroff"])
            return {"action": "restart" if restart else "shutdown"}
        _run(["shutdown", "-r" if restart else "-h", "now"])
        return {"action": "restart" if restart else "shutdown"}

    def logoff(self) -> Dict[str, Any]:
        if self.is_mac:
            _run(["osascript", "-e", 'tell application "System Events" to log out'])
            return {"logged_off": True}
        for cmd in (["gnome-session-quit", "--logout", "--no-prompt"], ["loginctl", "terminate-user", os.environ.get("USER", "")]):
            if _which(cmd[0]):
                _run(cmd)
                return {"logged_off": True}
        raise Unsupported("没有可用的注销命令")

    # ---------- 屏幕 ----------
    def screenshot(self, fmt: str = "png", monitor: int = -1) -> Tuple[bytes, str]:
        import tempfile

        path = os.path.join(tempfile.gettempdir(), f"echolink-shot-{int(time.time())}.png")
        if self.is_mac:
            _run(["screencapture", "-x", "-t", "png", path])
        elif _which("grim"):
            _run(["grim", path])
        elif _which("gnome-screenshot"):
            _run(["gnome-screenshot", "-f", path])
        elif _which("import"):
            _run(["import", "-window", "root", path])
        else:
            raise Unsupported("没有可用的截图工具(grim / gnome-screenshot / ImageMagick)")
        try:
            with open(path, "rb") as handle:
                return handle.read(), "png"
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    # ---------- 进程 ----------
    def processes(self, limit: int = 60) -> List[Dict[str, Any]]:
        result = _run(["ps", "-eo", "pid=,comm=,pcpu="])
        items: List[Dict[str, Any]] = []
        for line in (result.stdout or b"").decode(errors="ignore").splitlines():
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            cpu = None
            if len(parts) == 3:
                try:
                    cpu = float(parts[2])
                except ValueError:
                    cpu = None
            items.append({"pid": pid, "name": parts[1], "cpu_percent": cpu})
        items.sort(key=lambda item: (-(item["cpu_percent"] or 0), item["name"]))
        return items[: max(1, min(int(limit), 2000))]

    def kill(self, pid: int) -> Dict[str, Any]:
        pid = int(pid)
        if pid <= 1:
            raise CommandError("拒绝结束系统关键进程")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            raise CommandError(f"结束进程 {pid} 失败: {exc}") from exc
        return {"pid": pid, "killed": True}

    # ---------- 桌面 ----------
    def clipboard_get(self) -> str:
        if self.is_mac:
            return (_run(["pbpaste"]).stdout or b"").decode("utf-8", "replace")
        for cmd in (["xclip", "-selection", "clipboard", "-o"], ["xsel", "--clipboard", "--output"]):
            if _which(cmd[0]):
                return (_run(cmd).stdout or b"").decode("utf-8", "replace")
        raise Unsupported("缺少 xclip 或 xsel")

    def clipboard_set(self, text: str) -> Dict[str, Any]:
        text = "" if text is None else str(text)
        if self.is_mac:
            proc = subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=False)
            if proc.returncode != 0:
                raise CommandError("写入剪贴板失败")
            return {"length": len(text)}
        for cmd in (["xclip", "-selection", "clipboard", "-i"], ["xsel", "--clipboard", "--input"]):
            if _which(cmd[0]):
                proc = subprocess.run(cmd, input=text.encode("utf-8"), check=False)
                if proc.returncode != 0:
                    raise CommandError("写入剪贴板失败")
                return {"length": len(text)}
        raise Unsupported("缺少 xclip 或 xsel")

    def open_target(self, target: str) -> Dict[str, Any]:
        target = (target or "").strip()
        if not target:
            raise CommandError("缺少要打开的目标")
        if self.is_mac:
            _run(["open", target])
        elif _which("xdg-open"):
            _run(["xdg-open", target])
        else:
            raise Unsupported("缺少 xdg-open")
        return {"opened": target}

    def notify(self, title: str, message: str) -> Dict[str, Any]:
        if self.is_mac:
            script = f'display notification "{message}" with title "{title}"'
            _run(["osascript", "-e", script])
            return {"notified": True}
        if _which("notify-send"):
            _run(["notify-send", title or "EchoLink", message or ""])
            return {"notified": True}
        raise Unsupported("缺少 notify-send")

    def media(self, action: str) -> Dict[str, Any]:
        mapping = {"play_pause": "play-pause", "next": "next", "prev": "previous", "stop": "stop"}
        key = mapping.get((action or "").lower())
        if key is None:
            raise CommandError(f"未知媒体动作: {action}")
        if self.is_mac:
            script = 'tell application "System Events" to key code 16 using {command down}'
            _run(["osascript", "-e", script])
            return {"action": action}
        if not _which("playerctl"):
            raise Unsupported("缺少 playerctl")
        _run(["playerctl", key])
        return {"action": action}


class NullBackend(SystemBackend):
    """用于测试:不做任何真实操作,只记录调用。"""

    name = "null"
    platform_label = "测试后端"

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.clipboard = ""
        self._muted = False
        self._level = 50

    def _record(self, name: str, **payload: Any) -> None:
        self.calls.append({"action": name, **payload})

    def info(self) -> Dict[str, Any]:
        return {
            "platform": self.platform_label,
            "backend": self.name,
            "hostname": "test-host",
            "os": "TestOS 1.0",
            "arch": "x86_64",
            "cpu_count": 4,
            "uptime_seconds": 123.4,
            "audio": {"exact": True, "muted": self._muted, "level": self._level},
            "capabilities": self.capabilities(),
            "resources": {"memory_total": 1, "memory_free": 1, "memory_percent": 0},
        }

    def uptime(self) -> float:
        return 123.4

    def set_mute(self, state: str = "toggle") -> Dict[str, Any]:
        state = (state or "toggle").lower()
        if state in ("on", "mute", "muted"):
            self._muted = True
        elif state in ("off", "unmute", "unmuted"):
            self._muted = False
        else:
            self._muted = not self._muted
        self._record("set_mute", state=state, muted=self._muted)
        return {"muted": self._muted, "exact": True, "level": self._level}

    def set_volume(self, level: int) -> Dict[str, Any]:
        self._level = max(0, min(int(level), 100))
        self._record("set_volume", level=self._level)
        return {"level": self._level, "muted": self._muted, "exact": True}

    def audio_status(self) -> Dict[str, Any]:
        return {"exact": True, "muted": self._muted, "level": self._level}

    def lock(self) -> Dict[str, Any]:
        self._record("lock")
        return {"locked": True}

    def sleep(self) -> Dict[str, Any]:
        self._record("sleep")
        return {"sleep": True}

    def shutdown_power(self, restart: bool = False, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        self._record("shutdown", restart=restart, delay=delay, force=force)
        return {"action": "restart" if restart else "shutdown", "delay": delay}

    def logoff(self) -> Dict[str, Any]:
        self._record("logoff")
        return {"logged_off": True}

    def screenshot(self, fmt: str = "png", monitor: int = -1) -> Tuple[bytes, str]:
        self._record("screenshot", fmt=fmt, monitor=monitor)
        return b"\x89PNG\r\n\x1a\nFAKE", "png"

    def processes(self, limit: int = 60) -> List[Dict[str, Any]]:
        self._record("processes", limit=limit)
        return [
            {"pid": 1, "name": "init", "threads": 1},
            {"pid": 4242, "name": "test.exe", "threads": 8},
        ][: max(1, int(limit))]

    def kill(self, pid: int) -> Dict[str, Any]:
        self._record("kill", pid=pid)
        return {"pid": pid, "killed": True}

    def clipboard_get(self) -> str:
        self._record("clipboard_get")
        return self.clipboard

    def clipboard_set(self, text: str) -> Dict[str, Any]:
        self.clipboard = "" if text is None else str(text)
        self._record("clipboard_set", length=len(self.clipboard))
        return {"length": len(self.clipboard)}

    def open_target(self, target: str) -> Dict[str, Any]:
        self._record("open", target=target)
        return {"opened": target}

    def notify(self, title: str, message: str) -> Dict[str, Any]:
        self._record("notify", title=title, message=message)
        return {"notified": True}

    def media(self, action: str) -> Dict[str, Any]:
        self._record("media", value=action)
        return {"action": action}
