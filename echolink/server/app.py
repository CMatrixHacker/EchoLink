"""服务端主程序:监听 TCP / 蓝牙串口,管理多条并发会话。"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from typing import Any, Dict, List, Optional

from echolink import __version__
from echolink.common.errors import ErrorCode
from echolink.common.paths import Sandbox
from echolink.common.protocol import WELCOME, FrameChannel
from echolink.platform import get_backend
from echolink.server.audit import Auditor
from echolink.server.config import ServerConfig
from echolink.server.dispatch import Registry
from echolink.server.handlers import build_registry
from echolink.server.session import Session
from echolink.transport.base import Transport, TransportClosed, TransportError
from echolink.transport.serial_bt import SerialTransport, find_bluetooth_port
from echolink.transport.tcp import TcpServer

_log = logging.getLogger("echolink.server")


class EchoLinkServer:
    """把配置、后端、会话粘在一起的门面。"""

    def __init__(
        self,
        config: ServerConfig,
        backend: Optional[Any] = None,
        registry: Optional[Registry] = None,
    ):
        config.validate()
        self.config = config
        self.backend = backend if backend is not None else get_backend(config.backend)
        self.registry = registry if registry is not None else build_registry()
        self.sandbox = Sandbox(
            config.roots,
            allow_delete=config.allow_delete,
            allow_write=config.allow_write,
        )
        self.auditor = Auditor(config.audit_file)

        self._tcp: Optional[TcpServer] = None
        self._serial_transport: Optional[Transport] = None
        self._serial_thread: Optional[threading.Thread] = None
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._started_at: Optional[float] = None

    # ================= 生命周期 =================
    def start(self) -> Dict[str, Any]:
        if self._started_at is not None:
            raise RuntimeError("服务已在运行")
        self._stop.clear()
        self._started_at = time.time()

        if self.config.tcp.enabled:
            self._tcp = TcpServer(
                host=self.config.tcp.host,
                port=self.config.tcp.port,
                handler=self._handle_tcp,
            )
            port = self._tcp.start()
            _log.info("TCP 监听已启动: %s:%s", self.config.tcp.host, port)

        if self.config.bluetooth.enabled:
            self._serial_thread = threading.Thread(target=self._serial_loop, name="echolink-bt", daemon=True)
            self._serial_thread.start()
            _log.info("蓝牙串口服务已启动(端口: %s)", self.config.bluetooth.port or "自动探测")

        _log.info(
            "EchoLink %s 就绪 | 后端=%s | 根目录=%s | 命令数=%d",
            __version__,
            self.backend.name,
            ", ".join(self.sandbox.root_list),
            len(self.registry),
        )
        return self.info()

    def serve_forever(self) -> None:
        self.start()
        try:
            while not self._stop.wait(0.5):
                pass
        except KeyboardInterrupt:  # pragma: no cover - 交互式
            _log.info("收到 Ctrl+C,正在停止…")
        finally:
            self.stop()

    def stop(self) -> None:
        if self._started_at is None:
            return
        self._stop.set()
        if self._tcp is not None:
            self._tcp.stop()
            self._tcp = None
        transport = self._serial_transport
        if transport is not None:
            try:
                transport.close()
            except Exception:  # pragma: no cover
                pass
        if self._serial_thread is not None:
            self._serial_thread.join(timeout=3.0)
            self._serial_thread = None
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            try:
                session.transport.close()
            except Exception:  # pragma: no cover
                pass
        self._started_at = None
        _log.info("服务已停止")

    @property
    def running(self) -> bool:
        return self._started_at is not None

    # ================= 会话处理 =================
    def _handle_tcp(self, transport: Transport) -> None:
        self._run_session(transport, "tcp")

    def _run_session(self, transport: Transport, kind: str) -> None:
        with self._lock:
            over_limit = len(self._sessions) >= self.config.limits.max_sessions
        if over_limit:
            _log.warning("拒绝新连接 %s: 会话数已达上限", transport.describe)
            rejected = Session(
                transport=transport,
                config=self.config,
                registry=self.registry,
                backend=self.backend,
                sandbox=self.sandbox,
                auditor=self.auditor,
                reject={
                    "code": ErrorCode.BUSY,
                    "message": f"服务端会话数已达上限({self.config.limits.max_sessions})",
                },
            )
            try:
                rejected.run()
            finally:
                transport.close()
            return

        session = Session(
            transport=transport,
            config=self.config,
            registry=self.registry,
            backend=self.backend,
            sandbox=self.sandbox,
            auditor=self.auditor,
            on_close=self._forget_session,
        )
        with self._lock:
            self._sessions[session.id] = session
        _log.info("新会话 %s(%s, %s)", session.id, kind, transport.describe)
        try:
            session.run()
        finally:
            self._forget_session(session)

    def _forget_session(self, session: Session) -> None:
        with self._lock:
            self._sessions.pop(session.id, None)

    # ================= 蓝牙串口 =================
    def _serial_loop(self) -> None:
        cfg = self.config.bluetooth
        retry = max(0.5, float(cfg.retry_seconds))
        while not self._stop.is_set():
            port = cfg.port
            if not port and cfg.auto_detect:
                try:
                    port = find_bluetooth_port()
                except TransportError as exc:  # pyserial 缺失
                    _log.error("蓝牙不可用: %s", exc)
                    self._stop.wait(retry * 2)
                    continue
            if not port:
                _log.warning("未找到蓝牙串口,%.0f 秒后重试(可在配置中指定 bluetooth.port)", retry)
                self._stop.wait(retry)
                continue
            try:
                transport = SerialTransport(port, cfg.baudrate).open()
            except TransportError as exc:
                _log.warning("打开串口 %s 失败: %s", port, exc)
                self._stop.wait(retry)
                continue

            self._serial_transport = transport
            _log.info("蓝牙串口 %s 已就绪,等待手机连接…", port)
            try:
                self._run_session(transport, "bluetooth")
            except Exception:  # pragma: no cover
                _log.exception("蓝牙会话异常")
            finally:
                try:
                    transport.close()
                except Exception:
                    pass
                self._serial_transport = None
            if not self._stop.is_set():
                self._stop.wait(max(0.5, retry / 2))

    # ================= 状态 =================
    def info(self) -> Dict[str, Any]:
        with self._lock:
            sessions: List[Dict[str, Any]] = [item.summary() for item in self._sessions.values()]
        return {
            "name": self.config.name,
            "version": __version__,
            "started_at": self._started_at,
            "uptime": (time.time() - self._started_at) if self._started_at else 0,
            "backend": self.backend.name,
            "platform": self.backend.platform_label,
            "tcp": {
                "enabled": self.config.tcp.enabled,
                "host": self.config.tcp.host,
                "port": self._tcp.bound_port if self._tcp else self.config.tcp.port,
            },
            "bluetooth": {
                "enabled": self.config.bluetooth.enabled,
                "port": self.config.bluetooth.port or (self._serial_transport.describe if self._serial_transport else ""),
                "connected": self._serial_transport is not None,
            },
            "roots": self.sandbox.root_list,
            "permissions": {
                "write": self.config.allow_write,
                "delete": self.config.allow_delete,
                "power": self.config.allow_power,
                "kill_process": self.config.allow_kill_process,
            },
            "capabilities": self.backend.capabilities(),
            "commands": len(self.registry),
            "sessions": sessions,
        }

    def install_signal_handlers(self) -> None:
        """让 Ctrl+C / SIGTERM 干净退出。"""

        def _handler(signum, _frame):  # pragma: no cover - 依赖信号
            _log.info("收到信号 %s,正在停止…", signum)
            threading.Thread(target=self.stop, daemon=True).start()

        for name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):  # pragma: no cover - 非主线程
                pass


def run_server(config: ServerConfig) -> None:  # pragma: no cover - 入口封装
    server = EchoLinkServer(config)
    server.install_signal_handlers()
    server.serve_forever()
