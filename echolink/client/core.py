"""客户端核心:与界面无关的高层 API。

CLI、Windows 图形界面、Android App 全部复用这一个类,
因此"换了界面也不会换一套行为"。
"""

from __future__ import annotations

import os
import platform as _platform
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from echolink import __version__
from echolink.common.auth import compute_mac, parse_secret
from echolink.common.errors import RemoteError
from echolink.common.protocol import (
    AUTH,
    BYE,
    CHALLENGE,
    DEFAULT_CHUNK_BYTES,
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
    b64d,
    b64e,
)
from echolink.transport import open_transport
from echolink.transport.base import Transport

ProgressCallback = Optional[Callable[[int, int], None]]


def join_remote(directory: str, name: str) -> str:
    """拼接远程路径(统一用 /)。"""
    if not directory or directory == ".":
        return name
    return f"{directory.rstrip('/')}/{name}"


def default_device_name() -> str:
    try:
        return socket.gethostname()
    except OSError:  # pragma: no cover
        return "device"


def default_platform_name() -> str:
    system = _platform.system().lower()
    if system == "darwin":
        return "macos"
    return system or "unknown"


class EchoLinkClient:
    """一条到电脑端服务的会话。"""

    def __init__(
        self,
        transport: Optional[Transport] = None,
        secret: str = "",
        client_id: Optional[str] = None,
        device: str = "",
        app: str = "echolink-client",
        platform: Optional[str] = None,
        timeout: float = 20.0,
    ):
        self.transport = transport
        self.secret = secret
        self.client_id = client_id or f"{app}-{int(time.time()) % 100000}-{os.getpid()}"
        self.device = device or default_device_name()
        self.app_name = app
        self.platform = platform or default_platform_name()
        self.timeout = timeout

        self.channel: Optional[FrameChannel] = None
        self.connected = False
        self.server: Dict[str, Any] = {}
        self.commands: List[Dict[str, Any]] = []
        self.roots: List[Dict[str, str]] = []
        self.capabilities: Dict[str, bool] = {}
        self.permissions: Dict[str, bool] = {}
        self.max_chunk = DEFAULT_CHUNK_BYTES
        self.welcome: Dict[str, Any] = {}

        self._next_id = 0
        self._lock = threading.RLock()
        self.stats = {"requests": 0, "bytes_sent": 0, "bytes_received": 0}

    # ================= 连接 =================
    @classmethod
    def connect(
        cls,
        target: str,
        secret: str,
        timeout: float = 20.0,
        **kwargs: Any,
    ) -> "EchoLinkClient":
        """一行完成"建链 + 握手"。"""
        transport = open_transport(target, timeout=timeout)
        client = cls(transport=transport, secret=secret, timeout=timeout, **kwargs)
        try:
            client.handshake()
        except Exception:
            client.close()
            raise
        return client

    def handshake(self) -> Dict[str, Any]:
        if self.transport is None:
            raise ProtocolError("没有可用的传输层")
        secret_bytes = parse_secret(self.secret)
        self.channel = FrameChannel(self.transport, timeout=self.timeout)
        self.channel.send(
            {
                "type": HELLO,
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "client_id": self.client_id,
                "device": self.device,
                "app": self.app_name,
                "platform": self.platform,
                "client_version": __version__,
            }
        )
        frame = self.channel.recv(timeout=self.timeout)
        kind = frame.get("type")
        if kind == WELCOME:  # 服务端直接拒绝(会话超限等)
            error = frame.get("error") or {}
            raise RemoteError(error.get("code", "auth_failed"), error.get("message", "连接被拒绝"))
        if kind != CHALLENGE:
            raise ProtocolError(f"期望 challenge,收到 {kind}")
        nonce = str(frame.get("nonce") or "")
        if not nonce:
            raise ProtocolError("服务端未下发挑战串")
        self.channel.send(
            {
                "type": AUTH,
                "mac": compute_mac(secret_bytes, nonce, self.client_id),
                "client_id": self.client_id,
                "device": self.device,
            }
        )
        welcome = self.channel.recv(timeout=self.timeout)
        if welcome.get("type") != WELCOME:
            raise ProtocolError(f"期望 welcome,收到 {welcome.get('type')}")
        if not welcome.get("ok"):
            error = welcome.get("error") or {}
            raise RemoteError(error.get("code", "auth_failed"), error.get("message", "认证失败"))
        self.welcome = welcome
        self.server = welcome.get("server") or {}
        self.commands = welcome.get("commands") or []
        self.roots = welcome.get("roots") or []
        self.capabilities = welcome.get("capabilities") or {}
        self.permissions = welcome.get("permissions") or {}
        self.max_chunk = int((welcome.get("limits") or {}).get("max_chunk") or DEFAULT_CHUNK_BYTES)
        self.connected = True
        return welcome

    def close(self) -> None:
        if self.channel is not None and self.connected:
            try:
                self.channel.send({"type": BYE, "reason": "client closing"}, timeout=2.0)
            except Exception:
                pass
        self.connected = False
        if self.channel is not None:
            self.channel.close()
        elif self.transport is not None:
            self.transport.close()

    def __enter__(self) -> "EchoLinkClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ================= 请求 =================
    def call(self, command: str, args: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Any:
        if self.channel is None or not self.connected:
            raise RuntimeError("尚未连接")
        effective = timeout if timeout is not None else self.timeout
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self.channel.send({"type": REQUEST, "id": request_id, "cmd": command, "args": args or {}}, timeout=effective)
            deadline = time.monotonic() + effective
            while True:
                remaining = max(0.5, deadline - time.monotonic())
                frame = self.channel.recv(timeout=remaining)
                kind = frame.get("type")
                if kind == RESPONSE and frame.get("id") == request_id:
                    self.stats["requests"] += 1
                    if frame.get("ok"):
                        return frame.get("data")
                    error = frame.get("error") or {}
                    raise RemoteError(
                        error.get("code", "internal"),
                        error.get("message", "未知错误"),
                        error.get("details"),
                    )
                if kind == PING:
                    self.channel.send({"type": PONG, "id": frame.get("id")})
                    continue
                # 其他帧(事件/过期响应)忽略
            # 不会执行到这里

    # ================= 便捷封装 =================
    def ping(self) -> Dict[str, Any]:
        return self.call("system.ping", {"echo": time.time()})

    def info(self) -> Dict[str, Any]:
        return self.call("system.info")

    def capabilities_of_server(self) -> Dict[str, bool]:
        return self.call("system.capabilities").get("capabilities", {})

    def roots_of_server(self) -> List[Dict[str, str]]:
        return self.call("files.roots").get("roots", [])

    def list_dir(self, path: Optional[str] = None, show_hidden: bool = False) -> Dict[str, Any]:
        return self.call("files.list", {"path": path or "", "show_hidden": show_hidden})

    def stat(self, path: str) -> Dict[str, Any]:
        return self.call("files.stat", {"path": path})

    def mkdir(self, path: str) -> Dict[str, Any]:
        return self.call("files.mkdir", {"path": path, "parents": True})

    def delete(self, path: str, recursive: bool = False) -> Dict[str, Any]:
        args: Dict[str, Any] = {"path": path}
        if recursive:
            args["recursive"] = True
            args["confirm"] = True
        return self.call("files.delete", args)

    def rename(self, path: str, new_name: str, overwrite: bool = False) -> Dict[str, Any]:
        return self.call("files.rename", {"path": path, "new_name": new_name, "overwrite": overwrite})

    def search(self, root: str, pattern: str, max_depth: int = 6, limit: int = 200) -> Dict[str, Any]:
        return self.call(
            "files.search",
            {"root": root, "pattern": pattern, "max_depth": max_depth, "limit": limit},
            timeout=max(self.timeout, 30.0),
        )

    def preview(self, path: str, max_bytes: int = 64 * 1024) -> Dict[str, Any]:
        return self.call("files.preview", {"path": path, "max_bytes": max_bytes})

    def hash_file(self, path: str, algorithm: str = "sha256") -> Dict[str, Any]:
        return self.call("files.hash", {"path": path, "algorithm": algorithm}, timeout=max(self.timeout, 60.0))

    # ---------- 文件传输 ----------
    def download(
        self,
        remote: str,
        local: str,
        progress: ProgressCallback = None,
        overwrite: bool = True,
        chunk_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        meta = self.call("files.open_read", {"path": remote})
        handle = meta["handle"]
        total = int(meta.get("size") or 0)
        chunk = max(1, min(int(chunk_size or meta.get("chunk_size") or self.max_chunk), self.max_chunk))
        target = Path(local).expanduser()
        if target.exists() and not overwrite:
            self.call("files.close", {"handle": handle, "commit": False})
            raise FileExistsError(f"本地已存在: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + ".part")
        done = 0
        try:
            with open(temp, "wb") as file_handle:
                while True:
                    result = self.call(
                        "files.read",
                        {"handle": handle, "offset": done, "length": chunk},
                        timeout=max(self.timeout, 60.0),
                    )
                    data = b64d(result.get("data") or "")
                    if data:
                        file_handle.write(data)
                        done += len(data)
                    if progress is not None:
                        progress(done, total)
                    if result.get("eof") or not data:
                        break
                file_handle.flush()
                os.fsync(file_handle.fileno())
            os.replace(temp, target)
        except Exception:
            try:
                self.call("files.close", {"handle": handle, "commit": False}, timeout=10.0)
            except Exception:
                pass
            try:
                temp.unlink()
            except OSError:
                pass
            raise
        self.call("files.close", {"handle": handle})
        return {"path": str(target), "size": done, "remote": remote}

    def upload(
        self,
        local: str,
        remote: str,
        progress: ProgressCallback = None,
        overwrite: bool = True,
        chunk_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        source = Path(local).expanduser()
        if not source.is_file():
            raise FileNotFoundError(f"本地文件不存在: {source}")
        size = source.stat().st_size
        meta = self.call("files.open_write", {"path": remote, "overwrite": overwrite})
        handle = meta["handle"]
        chunk = max(1, min(int(chunk_size or meta.get("chunk_size") or self.max_chunk), self.max_chunk))
        done = 0
        try:
            with open(source, "rb") as file_handle:
                while True:
                    block = file_handle.read(chunk)
                    if not block:
                        break
                    self.call(
                        "files.write",
                        {"handle": handle, "offset": done, "data": b64e(block)},
                        timeout=max(self.timeout, 60.0),
                    )
                    done += len(block)
                    if progress is not None:
                        progress(done, size)
            self.call("files.close", {"handle": handle, "commit": True}, timeout=max(self.timeout, 60.0))
        except Exception:
            try:
                self.call("files.close", {"handle": handle, "commit": False}, timeout=10.0)
            except Exception:
                pass
            raise
        return {"path": meta.get("absolute") or remote, "size": done}

    def screenshot(self, save_to: Optional[str] = None) -> Dict[str, Any]:
        result = self.call("system.screenshot", timeout=max(self.timeout, 60.0))
        data = b64d(result.get("data") or "")
        payload = {"format": result.get("format", "png"), "size": len(data), "data": data}
        if save_to:
            target = Path(save_to).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            payload["path"] = str(target)
        return payload

    # ---------- 系统控制 ----------
    def mute(self, state: str = "toggle") -> Dict[str, Any]:
        return self.call("system.mute", {"state": state})

    def volume(self, level: int) -> Dict[str, Any]:
        return self.call("system.volume", {"level": int(level)})

    def lock(self) -> Dict[str, Any]:
        return self.call("system.lock")

    def sleep(self) -> Dict[str, Any]:
        return self.call("system.sleep", {"confirm": True})

    def shutdown(self, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        return self.call("system.shutdown", {"confirm": True, "delay": delay, "force": force})

    def restart(self, delay: int = 0, force: bool = False) -> Dict[str, Any]:
        return self.call("system.restart", {"confirm": True, "delay": delay, "force": force})

    def logoff(self) -> Dict[str, Any]:
        return self.call("system.logoff", {"confirm": True})

    def processes(self, limit: int = 60) -> Dict[str, Any]:
        return self.call("system.processes", {"limit": limit})

    def kill(self, pid: int) -> Dict[str, Any]:
        return self.call("system.kill", {"pid": int(pid), "confirm": True})

    def clipboard_get(self) -> str:
        return self.call("desktop.clipboard_get").get("text", "")

    def clipboard_set(self, text: str) -> Dict[str, Any]:
        return self.call("desktop.clipboard_set", {"text": text})

    def notify(self, title: str, message: str) -> Dict[str, Any]:
        return self.call("desktop.notify", {"title": title, "message": message})

    def open_target(self, target: str) -> Dict[str, Any]:
        return self.call("desktop.open", {"target": target})

    def media(self, action: str = "play_pause") -> Dict[str, Any]:
        return self.call("desktop.media", {"action": action})

    def has(self, capability: str) -> bool:
        return bool(self.capabilities.get(capability))

    def command_names(self) -> List[str]:
        return [item.get("name", "") for item in self.commands]
