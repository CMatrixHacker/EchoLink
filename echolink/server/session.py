"""单个连接的生命周期:握手 → 认证 → 请求循环。

协议时序::

    客户端                      服务端
      |-- hello (version, client_id, device) -->|
      |<-- challenge (nonce) --------------------|
      |-- auth (mac = HMAC(secret, nonce:id)) -->|
      |<-- welcome (ok, commands, roots, caps) --|
      |-- req {id, cmd, args} ------------------>|
      |<-- res {id, ok, data} -------------------|
      |-- bye --------------------------------->|
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Callable, Dict, Optional

from echolink import __version__
from echolink.common.auth import new_nonce, verify_mac
from echolink.common.errors import AuthError, CommandError, ErrorCode, Forbidden
from echolink.common.paths import Sandbox
from echolink.common.protocol import (
    AUTH,
    BYE,
    CHALLENGE,
    EVENT,
    HELLO,
    PING,
    PONG,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    REQUEST,
    RESPONSE,
    WELCOME,
    FrameChannel,
    ProtocolError,
    b64e,
)
from echolink.server.audit import Auditor
from echolink.server.config import ServerConfig
from echolink.server.dispatch import Registry, RequestContext, ensure_allowed
from echolink.server.transfers import TransferRegistry
from echolink.transport.base import Transport, TransportClosed, TransportTimeout

_log = logging.getLogger("echolink.session")


class Session:
    """一条已建立的连接。"""

    def __init__(
        self,
        transport: Transport,
        config: ServerConfig,
        registry: Registry,
        backend: Any,
        sandbox: Sandbox,
        auditor: Optional[Auditor] = None,
        on_close: Optional[Callable[["Session"], None]] = None,
        reject: Optional[Dict[str, Any]] = None,
    ):
        self.id = uuid.uuid4().hex[:12]
        self.transport = transport
        self.config = config
        self.registry = registry
        self.backend = backend
        self.sandbox = sandbox
        self.auditor = auditor or Auditor()
        self.on_close = on_close
        # 非空时表示"握手阶段直接回绝"(会话超限等),仍会先读掉 hello 再优雅关闭
        self.reject = reject

        self.client_id = "unknown"
        self.client_info: Dict[str, Any] = {}
        self.authenticated = False
        self.started_at = time.time()
        self.last_seen = time.time()
        self.requests_served = 0
        self.transfers = TransferRegistry(
            max_chunk=config.limits.max_chunk,
            max_upload_bytes=config.limits.max_upload_bytes,
        )

    # ================= 主流程 =================
    def run(self) -> None:
        channel = FrameChannel(self.transport)
        try:
            if not self._handshake(channel):
                return
            self._loop(channel)
        except TransportTimeout:
            _log.info("会话 %s 超时结束(%s)", self.id, self.client_id)
        except TransportClosed:
            _log.info("会话 %s 连接断开(%s)", self.id, self.client_id)
        except ProtocolError as exc:
            _log.warning("会话 %s 协议错误: %s", self.id, exc)
        except Exception:  # pragma: no cover - 兜底,避免线程静默死亡
            _log.exception("会话 %s 异常终止", self.id)
        finally:
            try:
                self.transfers.close_all(commit=False)
            except Exception:  # pragma: no cover
                pass
            channel.close()
            if self.on_close is not None:
                try:
                    self.on_close(self)
                except Exception:  # pragma: no cover
                    pass

    # ================= 握手 =================
    def _handshake(self, channel: FrameChannel) -> bool:
        timeout = self.config.limits.handshake_timeout
        first = channel.recv(timeout=timeout)

        # 服务端在会话数超限时会直接下发 welcome(ok=false)
        if first.get("type") == WELCOME:
            _log.warning("握手被拒绝: %s", first.get("error"))
            return False

        if first.get("type") != HELLO:
            raise ProtocolError(f"期望 hello,收到 {first.get('type')}")

        if self.reject:
            # 先把客户端的 hello 读完再回绝,避免留下未读数据导致 TCP RST 丢包
            channel.send({"type": WELCOME, "ok": False, "error": dict(self.reject)})
            _log.warning("握手被拒绝: %s", self.reject)
            return False

        if first.get("protocol") not in (PROTOCOL_NAME, None):
            raise ProtocolError(f"协议不匹配: {first.get('protocol')}")
        version = first.get("version")
        if version != PROTOCOL_VERSION:
            channel.send(
                {
                    "type": WELCOME,
                    "ok": False,
                    "error": {"code": ErrorCode.UNSUPPORTED, "message": f"协议版本不一致(服务端 {PROTOCOL_VERSION})"},
                }
            )
            return False

        self.client_id = str(first.get("client_id") or "unknown")[:64]
        self.client_info = {
            "client_id": self.client_id,
            "device": str(first.get("device") or "")[:64],
            "app": str(first.get("app") or "")[:64],
            "platform": str(first.get("platform") or "")[:32],
        }

        nonce = new_nonce()
        channel.send(
            {
                "type": CHALLENGE,
                "version": PROTOCOL_VERSION,
                "alg": "HMAC-SHA256",
                "nonce": nonce,
                "server": self.config.name,
                "server_version": __version__,
            }
        )

        answer = channel.recv(timeout=timeout)
        if answer.get("type") != AUTH:
            raise ProtocolError(f"期望 auth,收到 {answer.get('type')}")
        mac = str(answer.get("mac") or "")
        claimed = str(answer.get("client_id") or self.client_id)[:64]
        if not verify_mac(self.config.secret_bytes, nonce, claimed, mac):
            _log.warning("认证失败: client=%s transport=%s", claimed, self.transport.describe)
            self.auditor.record(
                client=claimed,
                transport=self.transport.describe,
                command="session.auth",
                ok=False,
                duration_ms=0.0,
                error="认证失败",
            )
            channel.send(
                {
                    "type": WELCOME,
                    "ok": False,
                    "error": {"code": ErrorCode.AUTH_FAILED, "message": "密钥不匹配,请检查配对密钥"},
                }
            )
            raise AuthError("认证失败")

        self.client_id = claimed
        self.authenticated = True
        channel.send(
            {
                "type": WELCOME,
                "ok": True,
                "session": self.id,
                "server": {
                    "name": self.config.name,
                    "version": __version__,
                    "platform": self.backend.platform_label,
                    "protocol": PROTOCOL_VERSION,
                    "hostname": self.backend.info().get("hostname", ""),
                },
                "commands": self.registry.catalog(),
                "roots": self.sandbox.describe(),
                "capabilities": self.backend.capabilities(),
                "permissions": {
                    "write": self.config.allow_write,
                    "delete": self.config.allow_delete,
                    "power": self.config.allow_power,
                    "kill_process": self.config.allow_kill_process,
                },
                "limits": {"max_chunk": self.config.limits.max_chunk},
            }
        )
        _log.info("会话 %s 建立: client=%s transport=%s", self.id, self.client_id, self.transport.describe)
        return True

    # ================= 请求循环 =================
    def _loop(self, channel: FrameChannel) -> None:
        idle = self.config.limits.idle_timeout or None
        while True:
            frame = channel.recv(timeout=idle)
            self.last_seen = time.time()
            kind = frame.get("type")

            if kind == BYE:
                _log.info("会话 %s 客户端主动结束", self.id)
                return
            if kind == PING:
                channel.send({"type": PONG, "id": frame.get("id"), "ts": time.time()})
                continue
            if kind == EVENT:
                continue  # 客户端事件当前无用途,忽略
            if kind != REQUEST:
                channel.send(
                    {
                        "type": RESPONSE,
                        "id": frame.get("id"),
                        "ok": False,
                        "error": {"code": ErrorCode.BAD_REQUEST, "message": f"不支持的帧类型: {kind}"},
                    }
                )
                continue

            channel.send(self._dispatch(frame))

    def _dispatch(self, frame: Dict[str, Any]) -> Dict[str, Any]:
        request_id = frame.get("id")
        command = frame.get("cmd")
        args = frame.get("args") or {}
        started = time.perf_counter()
        response: Dict[str, Any]

        if not isinstance(command, str) or not command:
            return self._error_response(request_id, ErrorCode.BAD_REQUEST, "缺少 cmd")
        if not isinstance(args, dict):
            return self._error_response(request_id, ErrorCode.BAD_REQUEST, "args 必须是对象")

        try:
            spec = self.registry.require(command)
            ensure_allowed(spec, self.config)
            context = RequestContext(self, command, args)
            data = spec.handler(context)
            response = {"type": RESPONSE, "id": request_id, "ok": True, "data": data if data is not None else {}}
            self.requests_served += 1
            self.auditor.record(
                client=self.client_id,
                transport=self.transport.describe,
                command=command,
                ok=True,
                duration_ms=(time.perf_counter() - started) * 1000,
                args=args,
            )
        except CommandError as exc:
            response = self._error_response(request_id, exc.code, exc.message, exc.details)
            self.auditor.record(
                client=self.client_id,
                transport=self.transport.describe,
                command=command,
                ok=False,
                duration_ms=(time.perf_counter() - started) * 1000,
                args=args,
                error=f"{exc.code}: {exc.message}",
            )
        except (TransportClosed, TransportTimeout):
            raise
        except Exception as exc:  # pragma: no cover - 未预期异常
            _log.exception("命令 %s 执行失败", command)
            response = self._error_response(request_id, ErrorCode.INTERNAL, f"服务端内部错误: {exc}")
            self.auditor.record(
                client=self.client_id,
                transport=self.transport.describe,
                command=command,
                ok=False,
                duration_ms=(time.perf_counter() - started) * 1000,
                args=args,
                error=f"internal: {exc}",
            )
        return response

    @staticmethod
    def _error_response(
        request_id: Any, code: str, message: str, details: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        error: Dict[str, Any] = {"code": code, "message": message}
        if details:
            error["details"] = details
        return {"type": RESPONSE, "id": request_id, "ok": False, "error": error}

    # ================= 辅助 =================
    def summary(self) -> Dict[str, Any]:
        return {
            "session": self.id,
            "client": self.client_id,
            "device": self.client_info.get("device", ""),
            "transport": self.transport.describe,
            "since": self.started_at,
            "requests": self.requests_served,
            "open_transfers": len(self.transfers),
        }


def encode_binary_payload(data: bytes) -> Dict[str, Any]:
    """统一二进制载荷格式(便于客户端判断/兼容未来压缩)。"""
    return {"encoding": "base64", "data": b64e(data), "size": len(data)}
