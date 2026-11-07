"""审计日志:每一条远程命令都留痕,便于事后追溯。"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

_log = logging.getLogger("echolink.audit")

_SENSITIVE_KEYS = {"data", "text", "content", "secret", "mac"}
_MAX_PREVIEW = 200


def _sanitize(args: Dict[str, Any]) -> Dict[str, Any]:
    """裁剪参数,避免把文件内容/剪贴板全文写进日志。"""
    cleaned: Dict[str, Any] = {}
    for key, value in (args or {}).items():
        if key in _SENSITIVE_KEYS:
            if isinstance(value, str):
                cleaned[key] = f"<{len(value)} 字符>"
            else:
                cleaned[key] = "<已省略>"
            continue
        if isinstance(value, str) and len(value) > _MAX_PREVIEW:
            cleaned[key] = value[:_MAX_PREVIEW] + "…"
        else:
            cleaned[key] = value
    return cleaned


class Auditor:
    """JSONL 审计写入器(线程安全,失败不影响主流程)。"""

    def __init__(self, path: Optional[str] = None):
        self.path = os.path.abspath(path) if path else None
        self._lock = threading.Lock()
        if self.path:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

    def record(
        self,
        *,
        client: str,
        transport: str,
        command: str,
        ok: bool,
        duration_ms: float,
        args: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        entry: Dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "client": client,
            "transport": transport,
            "cmd": command,
            "ok": bool(ok),
            "ms": round(duration_ms, 2),
        }
        if args:
            entry["args"] = _sanitize(args)
        if error:
            entry["error"] = error
        if extra:
            entry.update(extra)
        _log.info("%s %s ok=%s %.1fms", client, command, ok, duration_ms)
        if not self.path:
            return
        try:
            with self._lock:
                with open(self.path, "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover
            _log.warning("写入审计日志失败: %s", exc)

    def tail(self, limit: int = 50) -> list:
        if not self.path or not os.path.exists(self.path):
            return []
        items: list = []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:  # pragma: no cover
            return []
        return items[-limit:]
