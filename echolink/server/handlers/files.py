"""文件类命令:浏览、检索、增删改、分块上传下载、预览。"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from echolink.common.errors import BadRequest, CommandError, Conflict, ErrorCode, Forbidden, NotFound
from echolink.common.protocol import b64d, b64e
from echolink.server.dispatch import RequestContext, command

PREVIEW_BYTES = 256 * 1024


def _kind(path: Path) -> str:
    try:
        if path.is_dir():
            return "dir"
        if path.is_file():
            return "file"
        if path.is_symlink():
            return "link"
    except OSError:  # pragma: no cover
        pass
    return "other"


def _describe(sandbox, path: Path) -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "name": path.name or str(path),
        "path": sandbox.display(path),
        "type": _kind(path),
    }
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        info.update({"size": 0, "mtime": 0, "unreadable": True})
        return info
    info["size"] = stat.st_size
    info["mtime"] = stat.st_mtime
    info["hidden"] = path.name.startswith(".")
    return info


@command("files.roots", summary="列出可访问的根目录")
def files_roots(ctx: RequestContext) -> Dict[str, Any]:
    return {"roots": ctx.sandbox.describe()}


@command("files.list", summary="列出目录内容", params={"path": "目录", "show_hidden": "是否显示隐藏项"})
def files_list(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve(ctx.arg("path"), must_exist=True, kind="dir")
    show_hidden = ctx.param_bool("show_hidden", False)
    entries: List[Dict[str, Any]] = []
    try:
        with os.scandir(target) as iterator:
            for entry in iterator:
                if not show_hidden and entry.name.startswith("."):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                entries.append(
                    {
                        "name": entry.name,
                        "path": ctx.sandbox.display(Path(entry.path)),
                        "type": "dir" if is_dir else ("link" if entry.is_symlink() else "file"),
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "hidden": entry.name.startswith("."),
                    }
                )
    except PermissionError as exc:
        raise Forbidden(f"没有权限读取目录: {target}") from exc
    except OSError as exc:
        raise CommandError(f"读取目录失败: {exc}") from exc

    entries.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
    parent = target.parent
    parent_display: Optional[str] = None
    if ctx.sandbox.is_allowed(parent):
        parent_display = ctx.sandbox.display(parent)
    return {
        "path": ctx.sandbox.display(target),
        "absolute": str(target),
        "parent": parent_display,
        "count": len(entries),
        "dirs": sum(1 for item in entries if item["type"] == "dir"),
        "files": sum(1 for item in entries if item["type"] != "dir"),
        "entries": entries,
    }


@command("files.stat", summary="查看单个路径的详情", params={"path": "目标路径"})
def files_stat(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve(ctx.param_str("path", required=True), must_exist=True)
    info = _describe(ctx.sandbox, target)
    info["absolute"] = str(target)
    if info["type"] == "file":
        try:
            stat = target.stat()
            info["created"] = stat.st_ctime
            info["mode"] = oct(stat.st_mode & 0o777)
        except OSError:  # pragma: no cover
            pass
    return info


@command("files.exists", summary="判断路径是否存在", params={"path": "目标路径"})
def files_exists(ctx: RequestContext) -> Dict[str, Any]:
    try:
        target = ctx.sandbox.resolve(ctx.param_str("path", required=True))
    except Forbidden:
        raise
    return {"exists": target.exists(), "path": ctx.sandbox.display(target), "type": _kind(target) if target.exists() else None}


@command("files.mkdir", summary="创建目录", requires=("write",), params={"path": "目录路径", "parents": "是否递归创建"})
def files_mkdir(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve_for_write(ctx.param_str("path", required=True))
    if target.exists():
        raise Conflict(f"路径已存在: {target}")
    parents = ctx.param_bool("parents", True)
    try:
        if parents:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.mkdir()
    except OSError as exc:
        raise CommandError(f"创建目录失败: {exc}") from exc
    return {"created": ctx.sandbox.display(target), "absolute": str(target)}


@command(
    "files.delete",
    summary="删除文件或目录(目录需 recursive + confirm)",
    danger=True,
    requires=("delete",),
    params={"path": "目标", "recursive": "删除目录时必需", "confirm": "递归删除时必需为 true"},
)
def files_delete(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve_for_delete(ctx.param_str("path", required=True))
    is_dir = target.is_dir()
    if is_dir:
        if not ctx.param_bool("recursive", False):
            raise BadRequest("删除目录需要 recursive=true")
        ctx.require_confirm(f"递归删除目录 {target}")
        try:
            shutil.rmtree(target)
        except OSError as exc:
            raise CommandError(f"删除目录失败: {exc}") from exc
        return {"deleted": ctx.sandbox.display(target), "directory": True}

    try:
        target.unlink()
    except PermissionError as exc:
        raise Forbidden(f"没有权限删除: {target}") from exc
    except OSError as exc:
        raise CommandError(f"删除失败: {exc}") from exc
    return {"deleted": ctx.sandbox.display(target), "directory": False}


@command(
    "files.rename",
    summary="重命名或移动",
    requires=("write",),
    params={"path": "源路径", "destination": "目标路径(与新名称二选一)", "new_name": "同目录下的新名字", "overwrite": "是否覆盖"},
)
def files_rename(ctx: RequestContext) -> Dict[str, Any]:
    source = ctx.sandbox.resolve(ctx.param_str("path", required=True), must_exist=True)
    destination_arg = ctx.param_str("destination", "")
    new_name = ctx.param_str("new_name", "", max_length=255)
    if not destination_arg and not new_name:
        raise BadRequest("请提供 destination 或 new_name")
    if new_name:
        if "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            raise BadRequest("new_name 不能包含路径分隔符")
        raw_destination = str(source.parent / new_name)
    else:
        raw_destination = destination_arg

    target = ctx.sandbox.resolve_for_write(raw_destination)
    if target.exists() and not ctx.param_bool("overwrite", False):
        raise Conflict(f"目标已存在: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.parent == source.parent:
            os.replace(source, target)
        else:
            shutil.move(str(source), str(target))
    except OSError as exc:
        raise CommandError(f"移动失败: {exc}") from exc
    return {"from": ctx.sandbox.display(source), "to": ctx.sandbox.display(target), "absolute": str(target)}


@command(
    "files.copy",
    summary="在电脑上复制文件/目录",
    requires=("write",),
    params={"source": "源", "destination": "目标", "overwrite": "是否覆盖"},
)
def files_copy(ctx: RequestContext) -> Dict[str, Any]:
    source = ctx.sandbox.resolve(ctx.param_str("source", required=True), must_exist=True)
    target = ctx.sandbox.resolve_for_write(ctx.param_str("destination", required=True))
    overwrite = ctx.param_bool("overwrite", False)
    if target.exists() and not overwrite:
        raise Conflict(f"目标已存在: {target}")
    try:
        if source.is_dir():
            if target.exists() and overwrite:
                shutil.rmtree(target)
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except OSError as exc:
        raise CommandError(f"复制失败: {exc}") from exc
    return {"from": ctx.sandbox.display(source), "to": ctx.sandbox.display(target)}


@command(
    "files.search",
    summary="按文件名通配符检索",
    params={"root": "起始目录", "pattern": "如 *.pdf", "max_depth": "递归深度", "limit": "结果上限"},
)
def files_search(ctx: RequestContext) -> Dict[str, Any]:
    root = ctx.sandbox.resolve(ctx.arg("root"), must_exist=True, kind="dir")
    pattern = ctx.param_str("pattern", "*", max_length=128) or "*"
    max_depth = ctx.param_int("max_depth", 6, 1, 32) or 6
    limit = min(ctx.param_int("limit", 200, 1, 2000) or 200, ctx.limits.max_search_results)
    started = time.monotonic()
    results: List[Dict[str, Any]] = []
    truncated = False

    base_depth = len(root.parts)
    for base, dirs, names in os.walk(root, followlinks=False):
        depth = len(Path(base).parts) - base_depth
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [item for item in dirs if not item.startswith(".")]
        for name in names:
            if not fnmatch.fnmatch(name.lower(), pattern.lower()):
                continue
            path = Path(base) / name
            results.append(_describe(ctx.sandbox, path))
            if len(results) >= limit:
                truncated = True
                break
        if truncated:
            break
        if time.monotonic() - started > 15:
            truncated = True
            break

    return {
        "root": ctx.sandbox.display(root),
        "pattern": pattern,
        "count": len(results),
        "truncated": truncated,
        "results": results,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


@command(
    "files.hash",
    summary="计算文件校验值",
    params={"path": "文件", "algorithm": "sha256 / sha1 / md5"},
)
def files_hash(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve(ctx.param_str("path", required=True), must_exist=True, kind="file")
    algorithm = ctx.param_str("algorithm", "sha256", max_length=16).lower()
    if algorithm not in ("sha256", "sha1", "md5"):
        raise BadRequest("algorithm 仅支持 sha256 / sha1 / md5")
    digest = hashlib.new(algorithm)
    total = 0
    try:
        with open(target, "rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
    except OSError as exc:
        raise CommandError(f"读取文件失败: {exc}") from exc
    return {"path": ctx.sandbox.display(target), "algorithm": algorithm, "digest": digest.hexdigest(), "size": total}


@command(
    "files.preview",
    summary="预览文本文件开头(便于手机快速查看)",
    params={"path": "文件", "max_bytes": "读取字节数"},
)
def files_preview(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve(ctx.param_str("path", required=True), must_exist=True, kind="file")
    size = ctx.param_int("max_bytes", 64 * 1024, 1024, PREVIEW_BYTES) or 64 * 1024
    try:
        with open(target, "rb") as handle:
            data = handle.read(size + 1)
    except OSError as exc:
        raise CommandError(f"读取文件失败: {exc}") from exc
    truncated = len(data) > size
    data = data[:size]
    return {
        "path": ctx.sandbox.display(target),
        "size": target.stat().st_size,
        "truncated": truncated,
        "binary": b"\x00" in data[:1024],
        "text": data.decode("utf-8", "replace"),
    }


# ==================== 分块传输 ====================
@command("files.open_read", summary="打开文件用于下载", params={"path": "文件"})
def files_open_read(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve(ctx.param_str("path", required=True), must_exist=True, kind="file")
    transfer = ctx.transfers.open_read(target)
    return {
        "handle": transfer.handle,
        "size": transfer.size,
        "path": ctx.sandbox.display(target),
        "absolute": str(target),
        "chunk_size": ctx.limits.max_chunk,
    }


@command("files.read", summary="读取文件分块", params={"handle": "句柄", "offset": "偏移", "length": "长度"})
def files_read(ctx: RequestContext) -> Dict[str, Any]:
    handle = ctx.param_str("handle", required=True)
    offset = ctx.param_int("offset", None, 0)
    length = ctx.param_int("length", ctx.limits.max_chunk, 1, ctx.limits.max_chunk)
    data, position, eof = ctx.transfers.read_chunk(handle, offset, length)
    return {"data": b64e(data), "offset": position, "length": len(data), "eof": eof}


@command(
    "files.open_write",
    summary="打开文件用于上传",
    requires=("write",),
    params={"path": "目标文件", "overwrite": "是否覆盖"},
)
def files_open_write(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.sandbox.resolve_for_write(ctx.param_str("path", required=True))
    transfer = ctx.transfers.open_write(target, overwrite=ctx.param_bool("overwrite", False))
    return {
        "handle": transfer.handle,
        "path": ctx.sandbox.display(target),
        "absolute": str(target),
        "chunk_size": ctx.limits.max_chunk,
    }


@command(
    "files.write",
    summary="写入文件分块",
    requires=("write",),
    params={"handle": "句柄", "offset": "偏移", "data": "base64 数据"},
)
def files_write(ctx: RequestContext) -> Dict[str, Any]:
    handle = ctx.param_str("handle", required=True)
    raw = ctx.arg("data")
    if raw is None:
        raise BadRequest("缺少 data")
    data = b64d(raw if isinstance(raw, str) else str(raw))
    if len(data) > ctx.limits.max_chunk:
        raise CommandError(f"单块数据过大(上限 {ctx.limits.max_chunk} 字节)", ErrorCode.TOO_LARGE)
    offset = ctx.param_int("offset", None, 0)
    written = ctx.transfers.write_chunk(handle, offset, data)
    return {"written": written}


@command("files.close", summary="关闭句柄(commit=false 可放弃上传)", params={"handle": "句柄", "commit": "是否提交"})
def files_close(ctx: RequestContext) -> Dict[str, Any]:
    handle = ctx.param_str("handle", required=True)
    commit = ctx.param_bool("commit", True)
    return ctx.transfers.close(handle, commit=commit)


@command("files.transfers", summary="查看当前打开的传输句柄")
def files_transfers(ctx: RequestContext) -> Dict[str, Any]:
    items = list(ctx.transfers._items.values())  # noqa: SLF001 - 只读诊断
    return {"count": len(items), "transfers": [item.info() for item in items]}
