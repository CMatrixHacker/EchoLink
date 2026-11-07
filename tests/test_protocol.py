"""协议帧编解码与通道行为。"""

from __future__ import annotations

import json
import socket

import pytest

from echolink.common.protocol import (
    MAX_FRAME_BYTES,
    FrameChannel,
    ProtocolError,
    b64d,
    b64e,
    decode_payload,
    encode_frame,
)
from echolink.transport.base import TransportClosed, TransportTimeout
from echolink.transport.tcp import TcpTransport


def test_encode_frame_roundtrip():
    frame = encode_frame({"type": "req", "id": 7, "cmd": "system.ping", "args": {"中文": "值"}})
    length = int.from_bytes(frame[:4], "big")
    assert length == len(frame) - 4
    payload = decode_payload(frame[4:])
    assert payload["args"]["中文"] == "值"
    assert payload["id"] == 7


def test_encode_rejects_non_dict():
    with pytest.raises(ProtocolError):
        encode_frame(["not", "a", "dict"])  # type: ignore[arg-type]


def test_encode_rejects_oversized():
    with pytest.raises(ProtocolError):
        encode_frame({"blob": "x" * (MAX_FRAME_BYTES + 100)})


def test_decode_rejects_invalid_json():
    with pytest.raises(ProtocolError):
        decode_payload(b"{not json")
    with pytest.raises(ProtocolError):
        decode_payload(b'["a"]')


def test_base64_helpers():
    raw = b"\x00\x01binary\xff"
    assert b64d(b64e(raw)) == raw
    with pytest.raises(ProtocolError):
        b64d("!!!not-base64!!!")


def test_frame_channel_over_socketpair():
    left, right = socket.socketpair()
    channel_left = FrameChannel(TcpTransport(left))
    channel_right = FrameChannel(TcpTransport(right))
    try:
        channel_left.send({"type": "req", "id": 1, "cmd": "a", "args": {"n": 1}})
        first = channel_right.recv(timeout=2.0)
        assert first["cmd"] == "a"

        channel_right.send({"type": "res", "id": 1, "ok": True, "data": {"pong": 1}})
        second = channel_left.recv(timeout=2.0)
        assert second["data"]["pong"] == 1
        assert channel_left.bytes_sent > 0 and channel_right.bytes_received > 0
    finally:
        channel_left.close()
        channel_right.close()


def test_frame_channel_detects_closed_peer():
    left, right = socket.socketpair()
    channel = FrameChannel(TcpTransport(left))
    right.close()
    with pytest.raises(TransportClosed):
        channel.recv(timeout=2.0)
    channel.close()


def test_frame_channel_timeout():
    left, right = socket.socketpair()
    channel = FrameChannel(TcpTransport(left))
    try:
        with pytest.raises(TransportTimeout):
            channel.recv(timeout=0.15)
    finally:
        channel.close()
        right.close()


def test_frame_channel_rejects_zero_length():
    left, right = socket.socketpair()
    channel = FrameChannel(TcpTransport(left))
    try:
        right.sendall(b"\x00\x00\x00\x00")
        with pytest.raises(ProtocolError):
            channel.recv(timeout=2.0)
    finally:
        channel.close()
        right.close()


def test_partial_frame_is_reassembled():
    left, right = socket.socketpair()
    channel = FrameChannel(TcpTransport(left))
    frame = encode_frame({"type": "req", "id": 9, "cmd": "x", "args": {"k": "v"}})
    try:
        # 故意拆成多段发送,模拟蓝牙串口的碎片化
        for index in range(0, len(frame), 3):
            right.sendall(frame[index : index + 3])
        received = channel.recv(timeout=2.0)
        assert received["id"] == 9
    finally:
        channel.close()
        right.close()
