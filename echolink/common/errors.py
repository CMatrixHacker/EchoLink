"""统一的错误码与异常类型。

服务端把所有异常转换成 ``{"ok": false, "error": {"code", "message"}}``，
客户端据 ``code`` 做分支处理，因此错误码本身就是协议的一部分。
"""

from __future__ import annotations

from typing import Any, Dict, Optional


class ErrorCode:
    """协议错误码常量。"""

    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"
    AUTH_FAILED = "auth_failed"
    CONFIRM_REQUIRED = "confirm_required"
    UNSUPPORTED = "unsupported"
    BUSY = "busy"
    TOO_LARGE = "too_large"
    TIMEOUT = "timeout"
    UNKNOWN_COMMAND = "unknown_command"
    INTERNAL = "internal"


class CommandError(Exception):
    """命令执行失败(可安全展示给客户端)。"""

    code = ErrorCode.BAD_REQUEST

    def __init__(self, message: str, code: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return f"[{self.code}] {self.message}"


class BadRequest(CommandError):
    code = ErrorCode.BAD_REQUEST


class NotFound(CommandError):
    code = ErrorCode.NOT_FOUND


class Forbidden(CommandError):
    code = ErrorCode.FORBIDDEN


class Conflict(CommandError):
    code = ErrorCode.CONFLICT


class Unsupported(CommandError):
    code = ErrorCode.UNSUPPORTED


class AuthError(CommandError):
    code = ErrorCode.AUTH_FAILED


class ConfirmRequired(CommandError):
    code = ErrorCode.CONFIRM_REQUIRED


class RemoteError(Exception):
    """客户端侧:服务端返回了 ok=false。"""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"
