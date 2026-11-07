"""串口传输(用假的 pyserial 对象验证分片与超时)。"""

from __future__ import annotations

import pytest

from echolink.transport.base import TransportClosed, TransportTimeout
from echolink.transport.serial_bt import SerialTransport


class FakeSerial:
    """模拟 pyserial.Serial 的读写行为。"""

    def __init__(self, chunks=None):
        self.chunks = list(chunks or [])
        self.written = bytearray()
        self.closed = False
        self.flushes = 0

    def read(self, size: int) -> bytes:
        if self.chunks:
            chunk = self.chunks.pop(0)
            return chunk[:size]
        return b""

    def write(self, data: bytes) -> int:
        self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flushes += 1

    def close(self) -> None:
        self.closed = True


def make_transport(chunks=None) -> tuple[SerialTransport, FakeSerial]:
    transport = SerialTransport("COM_TEST")
    fake = FakeSerial(chunks)
    transport._ser = fake  # noqa: SLF001 - 测试注入
    return transport, fake


def test_recv_exact_across_fragments():
    transport, _fake = make_transport([b"ab", b"cde", b"fghi"])
    try:
        assert transport.recv_exact(5, timeout=1.0) == b"abcde"
        assert transport.recv_exact(4, timeout=1.0) == b"fghi"
    finally:
        transport.close()


def test_recv_times_out_when_no_data():
    transport, _fake = make_transport([])
    try:
        with pytest.raises(TransportTimeout):
            transport.recv_exact(4, timeout=0.2)
    finally:
        transport.close()


def test_send_all_writes_and_flushes():
    transport, fake = make_transport([])
    transport.send_all(b"hello")
    assert bytes(fake.written) == b"hello"
    assert fake.flushes == 1
    transport.close()
    assert fake.closed


def test_send_after_close_raises():
    transport, fake = make_transport([])
    transport.close()
    with pytest.raises(TransportClosed):
        transport.send_all(b"data")


def test_describe_contains_port():
    transport = SerialTransport("COM7")
    assert "COM7" in transport.describe


def test_open_without_pyserial_raises_transport_error(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "serial":
            raise ImportError("no pyserial")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from echolink.transport.base import TransportError

    with pytest.raises(TransportError):
        SerialTransport("COM1").open()
