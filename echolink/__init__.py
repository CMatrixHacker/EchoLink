"""EchoLink —— 以电脑为中心的多设备互联工具。

模块划分::

    echolink.common     协议、认证、路径沙箱等共享组件
    echolink.transport  传输层(TCP / 蓝牙串口 / Android RFCOMM)
    echolink.server     电脑端服务(会话、命令分发、审计)
    echolink.platform   各操作系统能力后端(音量、电源、截图、剪贴板...)
    echolink.client     客户端核心、命令行、桌面 GUI、Android App
"""

from __future__ import annotations

__all__ = ["__version__", "PROTOCOL_VERSION"]

__version__ = "1.0.0"

from echolink.common.protocol import PROTOCOL_VERSION  # noqa: E402
