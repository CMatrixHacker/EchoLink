"""传输层实现集合。"""

from __future__ import annotations

from echolink.transport.base import (  # noqa: F401
    Transport,
    TransportClosed,
    TransportError,
    TransportTimeout,
)

__all__ = ["Transport", "TransportClosed", "TransportError", "TransportTimeout", "open_transport"]


def open_transport(spec: str, timeout: float = 10.0) -> "Transport | None":
    """按简洁写法打开客户端传输。

    支持::

        tcp://192.168.1.10:8765
        192.168.1.10:8765              # 省略协议时默认 TCP
        serial:COM5                    # Windows 蓝牙/串口虚拟口
        bt:AA:BB:CC:DD:EE:FF           # Android RFCOMM(仅安卓可用)
    """
    from echolink.transport import tcp as tcp_mod

    if not spec:
        raise TransportError("未指定连接目标")
    text = spec.strip()
    lowered = text.lower()

    if lowered.startswith("tcp://"):
        host, port = _split_host_port(text[6:])
        return tcp_mod.TcpClient.connect(host, port, timeout=timeout)
    if lowered.startswith("serial://"):
        return _open_serial(text[9:])
    if lowered.startswith("serial:"):
        return _open_serial(text[7:])
    if lowered.startswith("bt:"):
        from echolink.transport.android_bt import AndroidBluetoothTransport

        return AndroidBluetoothTransport.connect(text[3:].strip(), timeout=timeout)
    if lowered.startswith("com") or lowered.startswith("/dev/tty"):
        return _open_serial(text)

    host, port = _split_host_port(text)
    return tcp_mod.TcpClient.connect(host, port, timeout=timeout)


def _split_host_port(text: str) -> "tuple[str, int]":
    from echolink.transport.base import TransportError as _Err

    raw = text.strip().strip("/")
    if not raw:
        raise _Err("连接目标为空")
    if raw.startswith("["):  # IPv6: [::1]:8765
        end = raw.find("]")
        if end < 0:
            raise _Err(f"IPv6 地址格式错误: {text}")
        host = raw[1:end]
        rest = raw[end + 1 :].lstrip(":")
        port_text = rest or "8765"
    elif ":" in raw:
        host, port_text = raw.rsplit(":", 1)
    else:
        host, port_text = raw, "8765"
    if not host:
        host = "127.0.0.1"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise _Err(f"端口非法: {port_text}") from exc
    if not 0 < port < 65536:
        raise _Err(f"端口越界: {port}")
    return host, port


def _open_serial(port: str) -> "Transport":
    from echolink.transport.serial_bt import SerialTransport, find_bluetooth_port

    target = port.strip().strip("/") or None
    if target is None or target in ("auto", "bt", "bluetooth"):
        detected = find_bluetooth_port()
        if not detected:
            raise TransportError("未找到蓝牙串口,请先在系统设置中配对电脑")
        target = detected
    return SerialTransport(target).open()
