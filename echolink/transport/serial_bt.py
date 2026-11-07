"""蓝牙传输(Windows / 桌面端):通过系统配对后生成的 SPP 虚拟串口。

Windows 侧流程(详见 docs/BLUETOOTH.md):

1. 设置 → 蓝牙和其他设备 → 添加设备 → 配对手机;
2. "更多蓝牙选项" → 串行端口 → 记下"传入"方向的 COM 号;
3. 服务端配置 ``bluetooth.port = "COM5"``。

手机端发起 RFCOMM 连接后会"唤醒"这个串口,双端就可以收发字节流了。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from echolink.transport.base import Transport, TransportClosed, TransportError, TransportTimeout

DEFAULT_BAUDRATE = 115200


def _serial_module():
    try:
        import serial  # type: ignore
    except ImportError as exc:  # pragma: no cover - 取决于是否安装 pyserial
        raise TransportError("需要 pyserial 才能使用蓝牙串口: pip install pyserial") from exc
    return serial


def list_serial_ports() -> List[Dict[str, str]]:
    """列出系统串口,并标记哪些是蓝牙 SPP。"""
    _serial_module()
    from serial.tools import list_ports  # type: ignore

    result: List[Dict[str, str]] = []
    for info in list_ports.comports():
        hwid = info.hwid or ""
        description = info.description or ""
        is_bt = "BTHENUM" in hwid.upper() or "BLUETOOTH" in description.upper()
        result.append(
            {
                "device": info.device,
                "description": description,
                "hwid": hwid,
                "bluetooth": "1" if is_bt else "0",
            }
        )
    return result


def find_bluetooth_port() -> Optional[str]:
    """自动挑一个蓝牙串口。"""
    ports = list_serial_ports()
    for info in ports:
        if info["bluetooth"] == "1":
            return info["device"]
    return None


class SerialTransport(Transport):
    """把 pyserial 的串口包装成 Transport。"""

    name = "bluetooth-serial"

    def __init__(self, port: str, baudrate: int = DEFAULT_BAUDRATE, read_timeout: float = 0.2):
        self.port = port
        self.baudrate = int(baudrate)
        self.read_timeout = float(read_timeout)
        self._ser = None
        self._closed = False

    # ---------- 打开 ----------
    def open(self) -> "SerialTransport":
        serial = _serial_module()
        try:
            self._ser = serial.Serial()
            self._ser.port = self.port
            self._ser.baudrate = self.baudrate
            self._ser.timeout = self.read_timeout
            self._ser.write_timeout = 10.0
            self._ser.bytesize = serial.EIGHTBITS
            self._ser.parity = serial.PARITY_NONE
            self._ser.stopbits = serial.STOPBITS_ONE
            self._ser.xonxoff = False
            self._ser.rtscts = False
            self._ser.dsrdtr = False
            self._ser.open()
        except Exception as exc:  # serial.SerialException 等
            raise TransportError(f"无法打开串口 {self.port}: {exc}") from exc
        return self

    @property
    def describe(self) -> str:
        return f"bt-serial://{self.port}@{self.baudrate}"

    # ---------- 读 ----------
    def recv_exact(self, n: int, timeout: Optional[float] = None) -> bytes:
        if n <= 0:
            return b""
        if self._ser is None:
            raise TransportClosed("串口未打开")
        deadline = None if timeout is None else time.monotonic() + timeout
        buffer = bytearray()
        while len(buffer) < n:
            if deadline is not None and time.monotonic() > deadline:
                raise TransportTimeout(f"串口接收超时(已收 {len(buffer)}/{n} 字节)")
            try:
                chunk = self._ser.read(n - len(buffer))
            except Exception as exc:
                raise TransportClosed(f"串口读取失败: {exc}") from exc
            if chunk:
                buffer.extend(chunk)
                continue
            if deadline is None:
                # 阻塞语义:继续等,但要能被 close() 打断
                if self._closed:
                    raise TransportClosed("连接已关闭")
                time.sleep(0.02)
        return bytes(buffer)

    # ---------- 写 ----------
    def send_all(self, data: bytes, timeout: Optional[float] = None) -> None:
        if not data:
            return
        if self._ser is None:
            raise TransportClosed("串口未打开")
        try:
            self._ser.write(data)
            self._ser.flush()
        except Exception as exc:
            raise TransportClosed(f"串口写入失败: {exc}") from exc

    def close(self) -> None:
        self._closed = True
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:  # pragma: no cover
                pass
            self._ser = None
