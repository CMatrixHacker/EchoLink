"""Windows 平台后端。

全部通过 ``ctypes`` 直连 Win32 API,不依赖 pywin32 / wmi 等重量级库:

* 静音 / 音量:优先 pycaw(Core Audio),缺失时退化到系统音量键(仅切换);
* 关机 / 重启 / 注销:``shutdown.exe``;
* 休眠:``SetSuspendState``;
* 截屏:GDI ``BitBlt`` + 自带 PNG 编码器(无 Pillow 也可用);
* 进程列表:Toolhelp32 快照;
* 剪贴板:user32 剪贴板 API;
* 媒体键:``keybd_event``。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import platform
import socket
import struct
import subprocess
import threading
import time
import zlib
from typing import Any, Dict, List, Optional, Tuple

from echolink.common.errors import CommandError, ErrorCode, Unsupported
from echolink.platform.base import SystemBackend


def _load(name: str):
    return ctypes.WinDLL(name, use_last_error=True)


_user32 = _load("user32")
_kernel32 = _load("kernel32")
_gdi32 = _load("gdi32")
_powrprof = _load("powrprof")

# 每个线程各自记录 COM 是否已初始化(Core Audio 需要)
_COM_STATE = threading.local()

# ---- 常量 ----
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
VK_MEDIA_STOP = 0xB2
VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001

MB_OK = 0x00000000
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_SYSTEMMODAL = 0x00001000

_MEDIA_KEYS = {
    "play_pause": VK_MEDIA_PLAY_PAUSE,
    "play": VK_MEDIA_PLAY_PAUSE,
    "pause": VK_MEDIA_PLAY_PAUSE,
    "next": VK_MEDIA_NEXT_TRACK,
    "prev": VK_MEDIA_PREV_TRACK,
    "previous": VK_MEDIA_PREV_TRACK,
    "stop": VK_MEDIA_STOP,
}


class MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wt.DWORD),
        ("dwMemoryLoad", wt.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wt.BYTE),
        ("BatteryFlag", wt.BYTE),
        ("BatteryLifePercent", wt.BYTE),
        ("Reserved1", wt.BYTE),
        ("BatteryLifeTime", wt.DWORD),
        ("BatteryFullLifeTime", wt.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("cntUsage", wt.DWORD),
        ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


# ---- 函数签名(64 位下必须声明 restype,否则句柄会被截断) ----
_user32.GetDC.restype = wt.HDC
_user32.GetDC.argtypes = [wt.HWND]
_user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.keybd_event.argtypes = [wt.BYTE, wt.BYTE, wt.DWORD, ctypes.c_void_p]
_user32.OpenClipboard.argtypes = [wt.HWND]
_user32.OpenClipboard.restype = wt.BOOL
_user32.CloseClipboard.restype = wt.BOOL
_user32.GetClipboardData.restype = ctypes.c_void_p
_user32.GetClipboardData.argtypes = [wt.UINT]
_user32.SetClipboardData.restype = ctypes.c_void_p
_user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
_user32.EmptyClipboard.restype = wt.BOOL
_user32.MessageBoxTimeoutW.restype = ctypes.c_int
_user32.MessageBoxTimeoutW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT, wt.WORD, wt.DWORD]

_gdi32.CreateCompatibleDC.restype = wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
_gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
_gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
_gdi32.SelectObject.restype = wt.HGDIOBJ
_gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
_gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
_gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT]
_gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
_gdi32.DeleteDC.argtypes = [wt.HDC]

_kernel32.GlobalAlloc.restype = ctypes.c_void_p
_kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
_kernel32.CloseHandle.argtypes = [wt.HANDLE]
_kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
_kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(width: int, height: int, bgra: bytes) -> bytes:
    """把自上而下的 BGRA 像素编码成 PNG(无第三方依赖)。

    先尝试 Pillow(快),失败则用纯 Python 实现(慢但正确)。
    """
    stride = width * 4
    try:  # pragma: no cover - 取决于环境是否装了 Pillow
        from PIL import Image  # type: ignore

        image = Image.frombuffer("RGB", (width, height), bgra, "raw", "BGRX", stride, 1)
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=False)
        return buffer.getvalue()
    except Exception:
        pass

    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type: None
        row = bgra[y * stride : (y + 1) * stride]
        # BGRA -> RGBA
        swapped = bytearray(row)
        swapped[0::4] = row[2::4]
        swapped[2::4] = row[0::4]
        swapped[3::4] = b"\xff" * width
        raw.extend(swapped)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


class WindowsBackend(SystemBackend):
    """Windows 具体实现。"""

    name = "windows"
    platform_label = "Windows"

    def __init__(self) -> None:
        try:  # 让像素坐标与物理分辨率一致
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                _user32.SetProcessDPIAware()
            except Exception:  # pragma: no cover
                pass

    # ================= 信息 =================
    def uptime(self) -> float:
        try:
            return _kernel32.GetTickCount64() / 1000.0
        except Exception:  # pragma: no cover
            return 0.0

    def info(self) -> Dict[str, Any]:
        memory = MEMORYSTATUSEX()
        memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        mem_ok = bool(_kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)))
        status = SYSTEM_POWER_STATUS()
        battery = None
        if _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            if status.BatteryLifePercent != 255:
                battery = {
                    "percent": int(status.BatteryLifePercent),
                    "plugged": status.ACLineStatus == 1,
                }
        payload: Dict[str, Any] = {
            "platform": self.platform_label,
            "backend": self.name,
            "hostname": socket.gethostname(),
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "os": f"{platform.system()} {platform.release()} ({platform.version()})",
            "arch": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "uptime_seconds": round(self.uptime(), 1),
            "battery": battery,
            "resources": {
                "memory_total": memory.ullTotalPhys if mem_ok else None,
                "memory_free": memory.ullAvailPhys if mem_ok else None,
                "memory_percent": memory.dwMemoryLoad if mem_ok else None,
            },
            "audio": self.audio_status(),
            "capabilities": self.capabilities(),
        }
        try:
            import shutil

            usage = shutil.disk_usage(os.path.abspath(os.sep))
            payload["resources"]["disk_total"] = usage.total
            payload["resources"]["disk_free"] = usage.free
        except OSError:  # pragma: no cover
            pass
        return payload

    # ================= 声音 =================
    @staticmethod
    def _ensure_com() -> None:
        """Core Audio 是 COM 接口,每个工作线程都需要初始化一次。"""
        import threading

        local = _COM_STATE
        if getattr(local, "ready", False):
            return
        try:
            import comtypes  # type: ignore

            comtypes.CoInitialize()
        except Exception:  # pragma: no cover - 已经初始化过
            pass
        local.ready = True

    @staticmethod
    def _endpoint_volume():
        """取 Core Audio 默认输出设备的音量接口;不可用时返回 None。"""
        WindowsBackend._ensure_com()
        try:
            from pycaw.pycaw import AudioUtilities  # type: ignore

            speakers = AudioUtilities.GetSpeakers()
            endpoint = getattr(speakers, "EndpointVolume", None)
            if endpoint is not None:
                return endpoint
            # 老版本 pycaw 需要手动 Activate
            from comtypes import CLSCTX_ALL  # type: ignore
            from pycaw.pycaw import IAudioEndpointVolume  # type: ignore

            interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))
        except Exception:
            return None

    def audio_status(self) -> Dict[str, Any]:
        endpoint = self._endpoint_volume()
        if endpoint is None:
            return {"exact": False, "muted": None, "level": None}
        try:
            return {
                "exact": True,
                "muted": bool(endpoint.GetMute()),
                "level": round(float(endpoint.GetMasterVolumeLevelScalar()) * 100),
            }
        except Exception:  # pragma: no cover
            return {"exact": False, "muted": None, "level": None}

    def set_mute(self, state: str = "toggle") -> Dict[str, Any]:
        state = (state or "toggle").lower()
        target: Optional[bool]
        if state in ("toggle", "switch"):
            target = None
        elif state in ("on", "true", "1", "mute", "muted"):
            target = True
        elif state in ("off", "false", "0", "unmute", "unmuted"):
            target = False
        else:
            raise CommandError(f"未知静音状态: {state}(可用 on/off/toggle)")

        endpoint = self._endpoint_volume()
        if endpoint is not None:
            current = bool(endpoint.GetMute())
            final = (not current) if target is None else target
            endpoint.SetMute(1 if final else 0, None)
            return {"muted": final, "exact": True, "level": round(float(endpoint.GetMasterVolumeLevelScalar()) * 100)}

        if target is not None:
            raise Unsupported("精确设置静音需要安装 pycaw(pip install pycaw),当前只能切换")
        self._tap_key(VK_VOLUME_MUTE)
        return {"muted": None, "exact": False, "note": "已发送系统静音快捷键,实际状态无法读取"}

    def set_volume(self, level: int) -> Dict[str, Any]:
        try:
            level = int(level)
        except (TypeError, ValueError) as exc:
            raise CommandError("音量必须是 0-100 的整数") from exc
        if not 0 <= level <= 100:
            raise CommandError("音量必须在 0-100 之间")
        endpoint = self._endpoint_volume()
        if endpoint is None:
            raise Unsupported("调节音量需要安装 pycaw(pip install pycaw)")
        endpoint.SetMasterVolumeLevelScalar(level / 100.0, None)
        if level > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)
        return {"level": level, "muted": bool(endpoint.GetMute()), "exact": True}

    # ================= 电源 =================
    def lock(self) -> Dict[str, Any]:
        if not _user32.LockWorkStation():
            raise CommandError("锁屏失败(可能被组策略禁用)")
        return {"locked": True}

    def sleep(self) -> Dict[str, Any]:
        ok = False
        try:
            ok = bool(_powrprof.SetSuspendState(False, False, False))
        except Exception:
            ok = False
        if not ok:
            subprocess.Popen(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
            ok = True
        return {"sleep": True, "ok": ok}

    def shutdown_power(self, restart: bool = False, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        delay = max(0, min(int(delay or 0), 86400))
        args = ["shutdown", "/r" if restart else "/s", "/t", str(delay)]
        if force:
            args.append("/f")
        try:
            subprocess.Popen(args, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except OSError as exc:
            raise CommandError(f"调用关机命令失败: {exc}") from exc
        return {"action": "restart" if restart else "shutdown", "delay": delay, "force": bool(force)}

    def cancel_shutdown(self) -> Dict[str, Any]:
        subprocess.Popen(["shutdown", "/a"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return {"cancelled": True}

    def logoff(self) -> Dict[str, Any]:
        subprocess.Popen(["shutdown", "/l"], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        return {"logged_off": True}

    # ================= 屏幕 =================
    def screenshot(self, fmt: str = "png", monitor: int = -1) -> Tuple[bytes, str]:
        width = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        origin_x = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        origin_y = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        if width <= 0 or height <= 0:  # pragma: no cover - 无桌面会话(服务、RDP 断开)
            raise CommandError("无法读取屏幕尺寸(可能运行在没有桌面会话的环境中)")
        if width * height > 40_000_000:  # pragma: no cover
            raise CommandError("屏幕尺寸异常,已拒绝截图")

        hdc_screen = _user32.GetDC(None)
        if not hdc_screen:  # pragma: no cover
            raise CommandError("无法获取屏幕设备上下文")
        mem_dc = None
        bitmap = None
        try:
            mem_dc = _gdi32.CreateCompatibleDC(hdc_screen)
            bitmap = _gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
            if not mem_dc or not bitmap:  # pragma: no cover
                raise CommandError("创建兼容位图失败")
            old = _gdi32.SelectObject(mem_dc, bitmap)
            _gdi32.BitBlt(mem_dc, 0, 0, width, height, hdc_screen, origin_x, origin_y, SRCCOPY)
            info_header = BITMAPINFO()
            info_header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info_header.bmiHeader.biWidth = width
            info_header.bmiHeader.biHeight = -height  # 负数 = 自上而下
            info_header.bmiHeader.biPlanes = 1
            info_header.bmiHeader.biBitCount = 32
            info_header.bmiHeader.biCompression = 0
            buffer = ctypes.create_string_buffer(width * height * 4)
            scanned = _gdi32.GetDIBits(mem_dc, bitmap, 0, height, buffer, ctypes.byref(info_header), DIB_RGB_COLORS)
            if scanned != height:  # pragma: no cover
                raise CommandError("读取屏幕像素失败")
            _gdi32.SelectObject(mem_dc, old)
            payload = encode_png(width, height, buffer.raw)
            return payload, "png"
        finally:
            if bitmap:
                _gdi32.DeleteObject(bitmap)
            if mem_dc:
                _gdi32.DeleteDC(mem_dc)
            _user32.ReleaseDC(None, hdc_screen)

    # ================= 进程 =================
    def processes(self, limit: int = 60) -> List[Dict[str, Any]]:
        snapshot = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (0, None) or snapshot == ctypes.c_void_p(-1).value:  # pragma: no cover
            raise CommandError("无法创建进程快照")
        items: List[Dict[str, Any]] = []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = _kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                items.append(
                    {
                        "pid": int(entry.th32ProcessID),
                        "name": entry.szExeFile,
                        "threads": int(entry.cntThreads),
                        "parent": int(entry.th32ParentProcessID),
                    }
                )
                ok = _kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            _kernel32.CloseHandle(snapshot)
        items.sort(key=lambda item: item["name"].lower())
        limit = max(1, min(int(limit or 60), 2000))
        return items[:limit]

    def kill(self, pid: int) -> Dict[str, Any]:
        try:
            pid = int(pid)
        except (TypeError, ValueError) as exc:
            raise CommandError("pid 必须是整数") from exc
        if pid in (0, 4):
            raise CommandError("拒绝结束系统关键进程")
        handle = _kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if not handle:
            raise CommandError(f"无法打开进程 {pid}(进程不存在或权限不足)")
        try:
            if not _kernel32.TerminateProcess(handle, 1):
                raise CommandError(f"结束进程 {pid} 失败")
        finally:
            _kernel32.CloseHandle(handle)
        return {"pid": pid, "killed": True}

    # ================= 剪贴板 =================
    def clipboard_get(self) -> str:
        if not _user32.OpenClipboard(None):
            raise CommandError("剪贴板被其他程序占用,请稍后重试")
        try:
            handle = _user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                _kernel32.GlobalUnlock(handle)
        finally:
            _user32.CloseClipboard()

    def clipboard_set(self, text: str) -> Dict[str, Any]:
        text = "" if text is None else str(text)
        data = (text + "\0").encode("utf-16-le")
        handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise CommandError("分配剪贴板内存失败")
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:  # pragma: no cover
            _kernel32.GlobalFree(handle)
            raise CommandError("锁定剪贴板内存失败")
        try:
            ctypes.memmove(pointer, data, len(data))
        finally:
            _kernel32.GlobalUnlock(handle)

        if not _user32.OpenClipboard(None):
            _kernel32.GlobalFree(handle)
            raise CommandError("剪贴板被其他程序占用,请稍后重试")
        try:
            _user32.EmptyClipboard()
            if not _user32.SetClipboardData(CF_UNICODETEXT, handle):
                _kernel32.GlobalFree(handle)
                raise CommandError("写入剪贴板失败")
        finally:
            _user32.CloseClipboard()
        return {"length": len(text)}

    # ================= 其他 =================
    def open_target(self, target: str) -> Dict[str, Any]:
        target = (target or "").strip()
        if not target:
            raise CommandError("缺少要打开的目标")
        try:
            os.startfile(target)  # type: ignore[attr-defined]
        except OSError as exc:
            raise CommandError(f"无法打开 {target}: {exc}") from exc
        return {"opened": target}

    def notify(self, title: str, message: str) -> Dict[str, Any]:
        title = title or "EchoLink"
        message = message or ""

        def _popup() -> None:
            try:
                _user32.MessageBoxTimeoutW(
                    None,
                    message,
                    title,
                    MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_SYSTEMMODAL,
                    0,
                    6000,
                )
            except Exception:  # pragma: no cover
                pass

        threading.Thread(target=_popup, name="echolink-notify", daemon=True).start()
        return {"notified": True}

    def media(self, action: str) -> Dict[str, Any]:
        key = _MEDIA_KEYS.get((action or "").lower())
        if key is None:
            raise CommandError(f"未知媒体动作: {action}(可用 play_pause/next/prev/stop)")
        self._tap_key(key)
        return {"action": action}

    # ================= 内部工具 =================
    @staticmethod
    def _tap_key(vk: int) -> None:
        _user32.keybd_event(vk, 0, 0, None)
        time.sleep(0.02)
        _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
