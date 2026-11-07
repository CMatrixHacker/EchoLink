"""命令注册表与请求上下文。

处理器用装饰器声明元数据(摘要、危险等级、所需权限、参数说明),
服务端据此做权限校验,客户端据此自动生成界面 —— 单一事实来源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional

from echolink.common.errors import BadRequest, CommandError, ConfirmRequired, ErrorCode, Forbidden

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

    from echolink.server.config import ServerConfig
    from echolink.server.session import Session

# 要求的权限名 -> 配置里的开关字段
PERMISSION_FLAGS = {
    "power": "allow_power",
    "write": "allow_write",
    "delete": "allow_delete",
    "kill": "allow_kill_process",
}


@dataclass
class CommandSpec:
    """一条命令的元数据。"""

    name: str
    handler: Callable[["RequestContext"], Any]
    summary: str = ""
    danger: bool = False
    requires: tuple = ()
    params: Dict[str, str] = field(default_factory=dict)

    def info(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "danger": self.danger,
            "requires": list(self.requires),
            "params": dict(self.params),
        }


class RequestContext:
    """一次命令调用的上下文。"""

    def __init__(self, session: "Session", command: str, args: Dict[str, Any]):
        self.session = session
        self.command = command
        self.args = dict(args or {})

    # ---------- 依赖注入 ----------
    @property
    def config(self) -> "ServerConfig":
        return self.session.config

    @property
    def backend(self):
        return self.session.backend

    @property
    def sandbox(self):
        return self.session.sandbox

    @property
    def transfers(self):
        return self.session.transfers

    @property
    def limits(self):
        return self.session.config.limits

    @property
    def client_id(self) -> str:
        return self.session.client_id

    # ---------- 参数工具 ----------
    def arg(self, name: str, default: Any = None) -> Any:
        return self.args.get(name, default)

    def param_int(
        self,
        name: str,
        default: Optional[int] = None,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
        required: bool = False,
    ) -> Optional[int]:
        raw = self.args.get(name, None)
        if raw is None or raw == "":
            if required:
                raise BadRequest(f"缺少参数 {name}")
            return default
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise BadRequest(f"参数 {name} 必须是整数") from exc
        if minimum is not None and value < minimum:
            raise BadRequest(f"参数 {name} 不能小于 {minimum}")
        if maximum is not None and value > maximum:
            raise BadRequest(f"参数 {name} 不能大于 {maximum}")
        return value

    def param_bool(self, name: str, default: bool = False) -> bool:
        raw = self.args.get(name, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on", "y")
        return default

    def param_str(self, name: str, default: str = "", max_length: int = 4096, required: bool = False) -> str:
        raw = self.args.get(name, default)
        if raw is None:
            raw = default
        text = str(raw)
        if required and not text.strip():
            raise BadRequest(f"缺少参数 {name}")
        if len(text) > max_length:
            raise BadRequest(f"参数 {name} 过长(上限 {max_length} 字符)")
        return text

    def require_confirm(self, action: str) -> None:
        """危险操作必须显式带上 ``confirm: true``。"""
        if not self.param_bool("confirm", False):
            raise ConfirmRequired(f"{action} 需要确认:请在请求中带上 confirm=true")


def command(
    name: str,
    summary: str = "",
    danger: bool = False,
    requires: Iterable[str] = (),
    params: Optional[Dict[str, str]] = None,
):
    """把一个函数登记为可远程调用的命令。"""

    def decorator(func: Callable[["RequestContext"], Any]) -> Callable[["RequestContext"], Any]:
        func.__echolink_command__ = CommandSpec(  # type: ignore[attr-defined]
            name=name,
            handler=func,
            summary=summary,
            danger=danger,
            requires=tuple(requires),
            params=dict(params or {}),
        )
        return func

    return decorator


class Registry:
    """命令集合。"""

    def __init__(self) -> None:
        self._specs: Dict[str, CommandSpec] = {}

    def add(self, spec: CommandSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"命令重复注册: {spec.name}")
        self._specs[spec.name] = spec

    def load_module(self, module: ModuleType) -> int:
        count = 0
        for value in vars(module).values():
            spec = getattr(value, "__echolink_command__", None)
            if isinstance(spec, CommandSpec):
                self.add(spec)
                count += 1
        return count

    def get(self, name: str) -> Optional[CommandSpec]:
        return self._specs.get(name)

    def require(self, name: str) -> CommandSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise CommandError(f"未知命令: {name}", ErrorCode.UNKNOWN_COMMAND)
        return spec

    def names(self) -> List[str]:
        return sorted(self._specs)

    def catalog(self) -> List[Dict[str, Any]]:
        return [self._specs[name].info() for name in sorted(self._specs)]

    def __len__(self) -> int:
        return len(self._specs)

    def __contains__(self, name: object) -> bool:
        return name in self._specs


def ensure_allowed(spec: CommandSpec, config: "ServerConfig") -> None:
    """按命令声明的 ``requires`` 检查服务端总开关。"""
    for requirement in spec.requires:
        flag = PERMISSION_FLAGS.get(requirement)
        if flag is None:
            continue
        if not getattr(config, flag, True):
            raise Forbidden(f"服务端已禁用「{requirement}」类操作")
