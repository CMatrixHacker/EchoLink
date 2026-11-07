"""服务端配置:JSON 文件 + 代码内默认值。

设计原则:默认安全(只允许白名单目录、关机类操作需要显式 ``confirm``)、
启动时强校验(没有密钥就拒绝启动,绝不裸奔)。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from echolink.common.auth import MIN_SECRET_BYTES, SecretError, generate_secret, parse_secret
from echolink.common.protocol import DEFAULT_CHUNK_BYTES, MAX_CHUNK_BYTES

CONFIG_ENV = "ECHOLINK_CONFIG"
DEFAULT_CONFIG_PATH = "server.json"
DEFAULT_PORT = 8765


@dataclass
class TcpConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = DEFAULT_PORT

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TcpConfig":
        return cls(
            enabled=bool(data.get("enabled", True)),
            host=str(data.get("host", "0.0.0.0")),
            port=int(data.get("port", DEFAULT_PORT)),
        )


@dataclass
class BluetoothConfig:
    enabled: bool = False
    port: str = ""
    baudrate: int = 115200
    auto_detect: bool = True
    retry_seconds: float = 3.0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BluetoothConfig":
        return cls(
            enabled=bool(data.get("enabled", False)),
            port=str(data.get("port") or ""),
            baudrate=int(data.get("baudrate", 115200)),
            auto_detect=bool(data.get("auto_detect", True)),
            retry_seconds=float(data.get("retry_seconds", 3.0)),
        )


@dataclass
class Limits:
    max_chunk: int = DEFAULT_CHUNK_BYTES
    handshake_timeout: float = 30.0
    idle_timeout: float = 0.0  # 0 = 不超时
    max_sessions: int = 8
    max_search_results: int = 500
    max_upload_bytes: int = 0  # 0 = 不限制
    max_screenshot_bytes: int = 24 * 1024 * 1024

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Limits":
        limits = cls(
            max_chunk=int(data.get("max_chunk", DEFAULT_CHUNK_BYTES)),
            handshake_timeout=float(data.get("handshake_timeout", 30.0)),
            idle_timeout=float(data.get("idle_timeout", 0.0)),
            max_sessions=int(data.get("max_sessions", 8)),
            max_search_results=int(data.get("max_search_results", 500)),
            max_upload_bytes=int(data.get("max_upload_bytes", 0)),
            max_screenshot_bytes=int(data.get("max_screenshot_bytes", 24 * 1024 * 1024)),
        )
        if not 1 <= limits.max_chunk <= MAX_CHUNK_BYTES:
            raise ValueError(f"limits.max_chunk 必须在 1..{MAX_CHUNK_BYTES} 之间")
        if limits.max_sessions < 1:
            raise ValueError("limits.max_sessions 至少为 1")
        return limits


@dataclass
class ServerConfig:
    name: str = "EchoLink"
    secret: str = ""
    roots: List[str] = field(default_factory=list)
    allow_write: bool = True
    allow_delete: bool = True
    allow_power: bool = True
    allow_kill_process: bool = True
    backend: str = "auto"
    audit_file: str = ""
    tcp: TcpConfig = field(default_factory=TcpConfig)
    bluetooth: BluetoothConfig = field(default_factory=BluetoothConfig)
    limits: Limits = field(default_factory=Limits)
    source_path: Optional[str] = None

    # ---------- 派生属性 ----------
    @property
    def secret_bytes(self) -> bytes:
        try:
            return parse_secret(self.secret)
        except SecretError as exc:
            raise ValueError(f"secret 非法: {exc}") from exc

    def validate(self) -> None:
        if not self.secret or len(self.secret.strip()) < MIN_SECRET_BYTES:
            raise ValueError("缺少 secret:请先运行 `echolink keygen` 生成密钥并写入配置")
        self.secret_bytes  # 触发格式校验
        if not self.roots:
            raise ValueError("至少需要配置一个可访问目录(roots)")
        if not self.tcp.enabled and not self.bluetooth.enabled:
            raise ValueError("tcp 与 bluetooth 至少要启用一个")
        if self.tcp.enabled and not 0 <= self.tcp.port < 65536:
            raise ValueError(f"tcp.port 非法: {self.tcp.port}(0 表示自动选择空闲端口)")
        self.limits = Limits.from_dict(self.limits.__dict__)

    # ---------- 序列化 ----------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "secret": self.secret,
            "roots": list(self.roots),
            "permissions": {
                "write": self.allow_write,
                "delete": self.allow_delete,
                "power": self.allow_power,
                "kill_process": self.allow_kill_process,
            },
            "backend": self.backend,
            "audit_file": self.audit_file,
            "tcp": {
                "enabled": self.tcp.enabled,
                "host": self.tcp.host,
                "port": self.tcp.port,
            },
            "bluetooth": {
                "enabled": self.bluetooth.enabled,
                "port": self.bluetooth.port,
                "baudrate": self.bluetooth.baudrate,
                "auto_detect": self.bluetooth.auto_detect,
                "retry_seconds": self.bluetooth.retry_seconds,
            },
            "limits": {
                "max_chunk": self.limits.max_chunk,
                "handshake_timeout": self.limits.handshake_timeout,
                "idle_timeout": self.limits.idle_timeout,
                "max_sessions": self.limits.max_sessions,
                "max_search_results": self.limits.max_search_results,
                "max_upload_bytes": self.limits.max_upload_bytes,
                "max_screenshot_bytes": self.limits.max_screenshot_bytes,
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        permissions = data.get("permissions") or {}
        logging_cfg = data.get("logging") or {}
        audit_file = data.get("audit_file") or logging_cfg.get("audit_file") or ""
        return cls(
            name=str(data.get("name") or "EchoLink"),
            secret=str(data.get("secret") or ""),
            roots=[str(item) for item in (data.get("roots") or [])],
            allow_write=bool(permissions.get("write", True)),
            allow_delete=bool(permissions.get("delete", True)),
            allow_power=bool(permissions.get("power", True)),
            allow_kill_process=bool(permissions.get("kill_process", True)),
            backend=str(data.get("backend") or "auto"),
            audit_file=str(audit_file),
            tcp=TcpConfig.from_dict(data.get("tcp") or {}),
            bluetooth=BluetoothConfig.from_dict(data.get("bluetooth") or {}),
            limits=Limits.from_dict(data.get("limits") or {}),
        )

    # ---------- 文件 IO ----------
    @classmethod
    def load(cls, path: str) -> "ServerConfig":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("配置文件根节点必须是对象")
        config = cls.from_dict(data)
        config.source_path = os.path.abspath(path)
        base = os.path.dirname(config.source_path)
        config.roots = [_resolve_relative(item, base) for item in config.roots]
        if config.audit_file:
            config.audit_file = _resolve_relative(config.audit_file, base)
        return config

    def save(self, path: str) -> str:
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        try:
            os.chmod(target, 0o600)
        except OSError:  # pragma: no cover
            pass
        return target


def _resolve_relative(value: str, base: str) -> str:
    text = os.path.expandvars(os.path.expanduser(str(value)))
    if os.path.isabs(text):
        return os.path.normpath(text)
    return os.path.normpath(os.path.join(base, text))


def default_config(path: Optional[str] = None) -> ServerConfig:
    """生成一份可以直接运行的默认配置(含新密钥与常用目录)。"""
    home = os.path.expanduser("~")
    download = os.path.join(home, "Downloads")
    documents = os.path.join(home, "Documents")
    roots = [item for item in (documents, download) if os.path.isdir(item)] or [home]
    config = ServerConfig(
        name=f"{os.environ.get('COMPUTERNAME') or 'My PC'} 上的 EchoLink",
        secret=generate_secret(),
        roots=roots,
        audit_file=os.path.join(os.path.dirname(os.path.abspath(path or DEFAULT_CONFIG_PATH)) or ".", "logs", "audit.jsonl"),
    )
    if path:
        config.source_path = os.path.abspath(path)
    return config


def config_path_from_args(explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    env = os.environ.get(CONFIG_ENV)
    if env:
        return env
    return DEFAULT_CONFIG_PATH
