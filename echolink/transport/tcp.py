"""TCP 传输:局域网/回环链路,也是测试与默认连接方式。"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, List, Optional, Tuple

from echolink.transport.base import Transport, TransportClosed, TransportError, TransportTimeout

_MIN_SOCKET_TIMEOUT = 0.001


class TcpTransport(Transport):
    """包装一个已连接的 socket。"""

    name = "tcp"

    def __init__(self, sock: socket.socket, peer: Optional[Tuple[str, int]] = None):
        self._sock = sock
        self._peer = peer
        self._closed = False
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass  # 非 TCP socket(如测试用 socketpair)忽略
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass

    @property
    def describe(self) -> str:
        if self._peer:
            return f"tcp://{self._peer[0]}:{self._peer[1]}"
        try:
            host, port = self._sock.getpeername()[:2]
            return f"tcp://{host}:{port}"
        except OSError:  # pragma: no cover
            return "tcp://?"

    @property
    def socket(self) -> socket.socket:
        return self._sock

    # ---------- 读 ----------
    def recv_exact(self, n: int, timeout: Optional[float] = None) -> bytes:
        if n <= 0:
            return b""
        deadline = None if timeout is None else time.monotonic() + timeout
        buffer = bytearray()
        while len(buffer) < n:
            remain = self._remaining(deadline)
            if remain is not None and remain <= 0:
                raise TransportTimeout(f"接收超时(已收 {len(buffer)}/{n} 字节)")
            try:
                self._sock.settimeout(remain)
                chunk = self._sock.recv(n - len(buffer))
            except socket.timeout as exc:
                raise TransportTimeout(f"接收超时(已收 {len(buffer)}/{n} 字节)") from exc
            except OSError as exc:
                raise TransportClosed(f"接收失败: {exc}") from exc
            if not chunk:
                raise TransportClosed("对端已关闭连接")
            buffer.extend(chunk)
        return bytes(buffer)

    # ---------- 写 ----------
    def send_all(self, data: bytes, timeout: Optional[float] = None) -> None:
        if not data:
            return
        deadline = None if timeout is None else time.monotonic() + timeout
        view = memoryview(data)
        sent = 0
        while sent < len(data):
            remain = self._remaining(deadline)
            if remain is not None and remain <= 0:
                raise TransportTimeout(f"发送超时(已发 {sent}/{len(data)} 字节)")
            try:
                self._sock.settimeout(remain)
                count = self._sock.send(view[sent:])
            except socket.timeout as exc:
                raise TransportTimeout("发送超时") from exc
            except OSError as exc:
                raise TransportClosed(f"发送失败: {exc}") from exc
            if count <= 0:
                raise TransportClosed("对端已关闭连接")
            sent += count

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:  # pragma: no cover
            pass

    @staticmethod
    def _remaining(deadline: Optional[float]) -> Optional[float]:
        if deadline is None:
            return None
        return max(_MIN_SOCKET_TIMEOUT, deadline - time.monotonic())


class TcpServer:
    """极简 TCP 监听器:每来一个连接就开一条线程交给 handler。"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8765,
        handler: Optional[Callable[[Transport], None]] = None,
        backlog: int = 16,
    ):
        self.host = host
        self.port = int(port)
        self.backlog = int(backlog)
        self._handler = handler
        self._listener: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._worker_threads: List[threading.Thread] = []
        self._lock = threading.Lock()
        self._running = False
        self.bound_port: Optional[int] = None

    # ---------- 生命周期 ----------
    def start(self) -> int:
        if self._running:
            raise TransportError("服务已在运行")
        if self._handler is None:
            raise TransportError("缺少连接处理函数")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(self.backlog)
        listener.settimeout(0.5)
        self._listener = listener
        self.bound_port = listener.getsockname()[1]
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, name="echolink-accept", daemon=True)
        self._thread.start()
        return self.bound_port

    def stop(self, join_timeout: float = 3.0) -> None:
        self._running = False
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)
            self._thread = None
        with self._lock:
            workers = list(self._worker_threads)
            self._worker_threads.clear()
        for worker in workers:
            worker.join(timeout=join_timeout)

    @property
    def running(self) -> bool:
        return self._running

    # ---------- 内部 ----------
    def _accept_loop(self) -> None:
        assert self._listener is not None
        while self._running:
            try:
                conn, peer = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = TcpTransport(conn, peer)
            thread = threading.Thread(
                target=self._run_handler,
                args=(transport,),
                name=f"echolink-session-{peer[0]}:{peer[1]}",
                daemon=True,
            )
            with self._lock:
                self._worker_threads = [t for t in self._worker_threads if t.is_alive()]
                self._worker_threads.append(thread)
            thread.start()

    def _run_handler(self, transport: TcpTransport) -> None:
        try:
            assert self._handler is not None
            self._handler(transport)
        except Exception:  # pragma: no cover - 交给上层日志
            pass
        finally:
            transport.close()


class TcpClient:
    """TCP 客户端连接助手。"""

    @staticmethod
    def connect(host: str, port: int, timeout: float = 10.0) -> TcpTransport:
        try:
            sock = socket.create_connection((host, int(port)), timeout=timeout)
        except OSError as exc:
            raise TransportError(f"无法连接 {host}:{port} —— {exc}") from exc
        sock.settimeout(None)
        return TcpTransport(sock, (host, int(port)))
