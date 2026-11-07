"""EchoLink 线协议:4 字节大端长度前缀 + UTF-8 JSON 正文。

设计目标:
* 单连接上双向、可交错(请求/响应靠 ``id`` 配对,服务端可随时插入 ``evt`` 事件);
* 与传输层解耦,只要实现 ``recv_exact`` / ``send_all`` 即可跑在 TCP 或蓝牙串口上;
* 二进制数据一律 base64 内联,避免双通道状态机,牺牲约 33% 带宽换取实现可靠性。
"""

from __future__ import annotations

import base64
import binascii
import json
import struct
from typing import Any, Dict, Optional

PROTOCOL_NAME = "echolink"
PROTOCOL_VERSION = 1

_LEN = struct.Struct(">I")

# 单个 JSON 帧的硬上限(防止恶意长度导致内存爆炸)
MAX_FRAME_BYTES = 8 * 1024 * 1024
# 单个文件分块的上限 / 默认值
MAX_CHUNK_BYTES = 256 * 1024
DEFAULT_CHUNK_BYTES = 128 * 1024

# ---- 帧类型 ----
HELLO = "hello"
CHALLENGE = "challenge"
AUTH = "auth"
WELCOME = "welcome"
REQUEST = "req"
RESPONSE = "res"
EVENT = "evt"
PING = "ping"
PONG = "pong"
BYE = "bye"


class ProtocolError(Exception):
    """帧格式非法或协议状态错误。"""


def encode_frame(message: Dict[str, Any]) -> bytes:
    """把 dict 编码成一帧完整的字节串。"""
    if not isinstance(message, dict):
        raise ProtocolError("帧内容必须是 dict")
    try:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"帧无法序列化: {exc}") from exc
    if not payload:
        raise ProtocolError("帧内容不能为空")
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(f"帧过大: {len(payload)} 字节 > 上限 {MAX_FRAME_BYTES}")
    return _LEN.pack(len(payload)) + payload


def decode_payload(payload: bytes) -> Dict[str, Any]:
    """把帧正文解码成 dict。"""
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"帧正文不是合法的 UTF-8 JSON: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("帧正文必须是 JSON 对象")
    return message


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: str) -> bytes:
    if not isinstance(text, str):
        raise ProtocolError("base64 字段必须是字符串")
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProtocolError(f"base64 解码失败: {exc}") from exc


class FrameChannel:
    """把 ``Transport`` 包装成收发 dict 的通道。

    线程安全性:一个 ``FrameChannel`` 只应由一个线程读写(会话即单线程模型)。
    """

    def __init__(self, transport: Any, timeout: Optional[float] = None):
        self.transport = transport
        self.timeout = timeout
        self.bytes_sent = 0
        self.bytes_received = 0

    # -- 发送 --
    def send(self, message: Dict[str, Any], timeout: Optional[float] = None) -> None:
        data = encode_frame(message)
        self.transport.send_all(data, timeout=self._to(timeout))
        self.bytes_sent += len(data)

    # -- 接收 --
    def recv(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        header = self.transport.recv_exact(_LEN.size, timeout=self._to(timeout))
        (length,) = _LEN.unpack(header)
        if length == 0:
            raise ProtocolError("帧长度为 0")
        if length > MAX_FRAME_BYTES:
            raise ProtocolError(f"帧长度 {length} 超过上限 {MAX_FRAME_BYTES}")
        payload = self.transport.recv_exact(length, timeout=self._to(timeout))
        self.bytes_received += _LEN.size + length
        return decode_payload(payload)

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception:  # pragma: no cover - 关闭失败不应影响上级清理
            pass

    @property
    def describe(self) -> str:
        return getattr(self.transport, "describe", "unknown transport")

    def _to(self, timeout: Optional[float]) -> Optional[float]:
        return self.timeout if timeout is None else timeout
