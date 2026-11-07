"""路径沙箱:确保远程只能访问配置里显式授权的目录。

所有文件命令都必须先经过 :class:`Sandbox` 解析,任何越界(``..``、符号链接、
Windows 8.3 短名、UNC 设备路径)都会被拒绝。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Optional

from echolink.common.errors import BadRequest, Forbidden, NotFound


def _norm(path: Path) -> str:
    """用于比较的规范化字符串(Windows 大小写不敏感)。"""
    return os.path.normcase(os.path.abspath(str(path)))


class Sandbox:
    """受限文件系统视图。"""

    def __init__(self, roots: Iterable[str], allow_delete: bool = True, allow_write: bool = True):
        roots = list(roots or [])
        if not roots:
            raise ValueError("Sandbox 至少需要一个根目录")
        self.roots: List[Path] = []
        for raw in roots:
            path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
            try:
                path = path.resolve(strict=False)
            except OSError:  # pragma: no cover - 极端路径
                path = Path(os.path.abspath(str(path)))
            self.roots.append(path)
        self.allow_delete = allow_delete
        self.allow_write = allow_write

    # ---------- 查询 ----------
    @property
    def root_list(self) -> List[str]:
        return [str(path) for path in self.roots]

    @property
    def default_root(self) -> Path:
        return self.roots[0]

    def is_allowed(self, path: Path) -> bool:
        target = _norm(path)
        for root in self.roots:
            root_text = _norm(root)
            if target == root_text or target.startswith(root_text + os.sep):
                return True
        return False

    def describe(self) -> List[str]:
        return [{"path": str(root), "label": root.name or str(root)} for root in self.roots]

    # ---------- 解析 ----------
    def resolve(self, raw: Optional[str], *, must_exist: bool = False, kind: str = "any") -> Path:
        """把客户端传来的路径解析成受信任的绝对路径。

        :param raw: 客户端路径;空值表示沙箱根目录(仅当只有一个根时)
        :param must_exist: 是否要求目标已存在
        :param kind: ``file`` / ``dir`` / ``any``,用于校验目标类型
        """
        text = "" if raw is None else str(raw).strip()
        if text in ("", ".", "/", "~root"):
            if len(self.roots) == 1:
                candidate = self.roots[0]
            else:
                raise BadRequest("需要指定 path(当前有多个可访问根目录)")
        else:
            text = text.strip('"').strip("'")
            candidate = Path(os.path.expandvars(os.path.expanduser(text)))
            if not candidate.is_absolute():
                candidate = self.default_root / candidate

        try:
            resolved = candidate.resolve(strict=False)
        except OSError:  # pragma: no cover
            resolved = Path(os.path.abspath(str(candidate)))

        if not self.is_allowed(resolved):
            raise Forbidden(f"路径不在授权范围内: {resolved}")

        if must_exist and not resolved.exists():
            raise NotFound(f"路径不存在: {resolved}")

        if kind == "file" and resolved.exists() and not resolved.is_file():
            raise BadRequest(f"不是文件: {resolved}")
        if kind == "dir" and resolved.exists() and not resolved.is_dir():
            raise BadRequest(f"不是目录: {resolved}")
        return resolved

    def resolve_for_write(self, raw: Optional[str]) -> Path:
        if not self.allow_write:
            raise Forbidden("服务端已禁用写入")
        return self.resolve(raw, must_exist=False)

    def resolve_for_delete(self, raw: Optional[str]) -> Path:
        if not self.allow_delete:
            raise Forbidden("服务端已禁用删除")
        path = self.resolve(raw, must_exist=True)
        for root in self.roots:
            if _norm(path) == _norm(root):
                raise Forbidden("不允许删除沙箱根目录本身")
        return path

    def display(self, path: Path) -> str:
        """把绝对路径转成相对根的展示形式(便于客户端显示)。"""
        for root in self.roots:
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            rel_text = str(rel)
            return "." if rel_text == "." else rel_text.replace("\\", "/")
        return str(path)
