"""Android 端蓝牙传输:通过 pyjnius 调用系统 RFCOMM(SPP)套接字。

这套代码只在打包成 APK 后运行(需要 ``pyjnius`` 与 BLUETOOTH_CONNECT 权限),
在桌面上导入本模块不会报错,调用时才检查依赖,便于共用同一份包。
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from echolink.transport.base import Transport, TransportClosed, TransportError, TransportTimeout

# 标准 SPP(串口)服务的 UUID
SPP_UUID = "00001101-0000-1000-8000-00805F9B34FB"

_READ_CHUNK = 16 * 1024


def _android_available() -> bool:
    try:
        from jnius import autoclass  # noqa: F401
    except Exception:
        return False
    return True


def _autoclass(name: str):
    try:
        from jnius import autoclass
    except Exception as exc:  # pragma: no cover - 非安卓环境
        raise TransportError("蓝牙直连仅在 Android 上可用(需要 pyjnius)") from exc
    return autoclass(name)


def request_bluetooth_permissions() -> None:
    """Android 12+ 需要在运行时申请蓝牙权限。"""
    try:
        from android.permissions import Permission, request_permissions  # type: ignore
    except Exception:
        return
    perms = []
    for attr in ("BLUETOOTH_CONNECT", "BLUETOOTH_SCAN", "BLUETOOTH"):
        perm = getattr(Permission, attr, None)
        if perm:
            perms.append(perm)
    if perms:
        try:
            request_permissions(perms)
        except Exception:  # pragma: no cover
            pass


def bluetooth_enabled() -> bool:
    adapter = _autoclass("android.bluetooth.BluetoothAdapter")
    return bool(adapter.getDefaultAdapter() and adapter.getDefaultAdapter().isEnabled())


def list_paired_devices() -> List[Dict[str, str]]:
    """列出已配对的蓝牙设备(用于让用户选电脑)。"""
    adapter = _autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
    if adapter is None:
        return []
    devices = []
    for device in adapter.getBondedDevices().toArray():
        devices.append(
            {
                "name": str(device.getName() or ""),
                "address": str(device.getAddress() or ""),
                "type": int(device.getType() or 0),
            }
        )
    return devices


def _to_bytes(java_array, count: int) -> bytes:
    """把 Java byte[] 转成 Python bytes,兼容不同 pyjnius 版本。"""
    try:
        return bytes(java_array)[:count]
    except Exception:  # pragma: no cover - 旧版 pyjnius 不支持 buffer 协议
        return bytes(bytearray(java_array[i] & 0xFF for i in range(count)))


class AndroidBluetoothTransport(Transport):
    """基于 BluetoothSocket 的字节流。"""

    name = "android-bluetooth"

    def __init__(self, socket_obj, address: str):
        self._socket = socket_obj
        self._address = address
        self._input = None
        self._output = None
        self._closed = False

    # ---------- 建立连接 ----------
    @classmethod
    def connect(cls, address: str, timeout: float = 20.0) -> "AndroidBluetoothTransport":
        request_bluetooth_permissions()
        adapter = _autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
        if adapter is None:
            raise TransportError("设备不支持蓝牙")
        if not adapter.isEnabled():
            raise TransportError("请先打开手机蓝牙")
        device = adapter.getRemoteDevice(address)
        UUID = _autoclass("java.util.UUID")
        uuid = UUID.fromString(SPP_UUID)

        socket_obj = None
        last_error: Optional[Exception] = None
        # 依次尝试安全/非安全 RFCOMM(不同电脑的 SPP 实现略有差异)
        for secure in (True, False):
            try:
                if secure:
                    socket_obj = device.createRfcommSocketToServiceRecord(uuid)
                else:
                    socket_obj = device.createInsecureRfcommSocketToServiceRecord(uuid)
                adapter.cancelDiscovery()
                socket_obj.connect()
                break
            except Exception as exc:  # pragma: no cover - 需真机
                last_error = exc
                socket_obj = None
        if socket_obj is None:
            raise TransportError(f"无法连接 {address}(请确认电脑已配对并开启服务): {last_error}")

        transport = cls(socket_obj, address)
        transport._input = socket_obj.getInputStream()
        transport._output = socket_obj.getOutputStream()
        return transport

    @property
    def describe(self) -> str:
        return f"bt://{self._address}"

    # ---------- 读 ----------
    def recv_exact(self, n: int, timeout: Optional[float] = None) -> bytes:
        if n <= 0:
            return b""
        if self._input is None:
            raise TransportClosed("蓝牙连接未建立")
        deadline = None if timeout is None else time.monotonic() + timeout
        buffer = bytearray()
        jarray = None
        while len(buffer) < n:
            if deadline is not None and time.monotonic() > deadline:
                raise TransportTimeout(f"蓝牙接收超时(已收 {len(buffer)}/{n} 字节)")
            want = min(n - len(buffer), _READ_CHUNK)
            try:
                if jarray is None:
                    jarray = _autoclass("java.lang.reflect.Array").newInstance(
                        _autoclass("java.lang.Byte").TYPE, _READ_CHUNK
                    )
                count = int(self._input.read(jarray, 0, want))
            except Exception as exc:  # pragma: no cover - 需真机
                raise TransportClosed(f"蓝牙读取失败: {exc}") from exc
            if count < 0:
                raise TransportClosed("对端已关闭蓝牙连接")
            if count == 0:
                continue
            buffer.extend(_to_bytes(jarray, count))
        return bytes(buffer)

    # ---------- 写 ----------
    def send_all(self, data: bytes, timeout: Optional[float] = None) -> None:
        if not data:
            return
        if self._output is None:
            raise TransportClosed("蓝牙连接未建立")
        try:
            # pyjnius 会把 bytes 自动转成 Java byte[]
            self._output.write(data)
            self._output.flush()
        except Exception as exc:  # pragma: no cover - 需真机
            raise TransportClosed(f"蓝牙写入失败: {exc}") from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for closer in (self._input, self._output, self._socket):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception:  # pragma: no cover
                pass
