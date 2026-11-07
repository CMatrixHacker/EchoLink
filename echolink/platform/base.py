"""平台后端抽象:把"操作系统能力"收敛成一组稳定方法。

服务端只依赖这组方法,因此新增平台(或写测试桩)无需改动任何业务代码。
未被具体平台实现的方法统一抛 :class:`Unsupported`,并通过 ``capabilities()``
暴露能力矩阵,客户端可据此隐藏不可用按钮。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from echolink.common.errors import Unsupported

# 能力名 -> 方法名
FEATURES: Dict[str, str] = {
    "mute": "set_mute",
    "volume": "set_volume",
    "lock": "lock",
    "sleep": "sleep",
    "shutdown": "shutdown_power",
    "logoff": "logoff",
    "screenshot": "screenshot",
    "processes": "processes",
    "kill": "kill",
    "clipboard": "clipboard_get",
    "open": "open_target",
    "notify": "notify",
    "media": "media",
}


class SystemBackend:
    """所有平台后端的基类(默认全部不支持)。"""

    name = "generic"
    platform_label = "未知系统"

    # ---------- 能力自检 ----------
    def capabilities(self) -> Dict[str, bool]:
        caps: Dict[str, bool] = {}
        for feature, method in FEATURES.items():
            caps[feature] = getattr(type(self), method, None) is not getattr(SystemBackend, method, None)
        return caps

    # ---------- 基础信息 ----------
    def info(self) -> Dict[str, Any]:
        return {"platform": self.platform_label, "backend": self.name}

    def uptime(self) -> float:
        raise Unsupported(f"{self.platform_label} 不支持读取运行时长")

    # ---------- 声音 ----------
    def set_mute(self, state: str = "toggle") -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持静音控制")

    def set_volume(self, level: int) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持音量控制")

    def audio_status(self) -> Dict[str, Any]:
        return {}

    # ---------- 电源 ----------
    def lock(self) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持锁屏")

    def sleep(self) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持休眠")

    def shutdown_power(self, restart: bool = False, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持关机/重启")

    def logoff(self) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持注销")

    # ---------- 屏幕 ----------
    def screenshot(self, fmt: str = "png", monitor: int = -1) -> Tuple[bytes, str]:
        raise Unsupported(f"{self.platform_label} 不支持截屏")

    # ---------- 进程 ----------
    def processes(self, limit: int = 60) -> List[Dict[str, Any]]:
        raise Unsupported(f"{self.platform_label} 不支持进程列表")

    def kill(self, pid: int) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持结束进程")

    # ---------- 桌面交互 ----------
    def clipboard_get(self) -> str:
        raise Unsupported(f"{self.platform_label} 不支持读取剪贴板")

    def clipboard_set(self, text: str) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持写入剪贴板")

    def open_target(self, target: str) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持打开目标")

    def notify(self, title: str, message: str) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持桌面通知")

    def media(self, action: str) -> Dict[str, Any]:
        raise Unsupported(f"{self.platform_label} 不支持媒体按键")
