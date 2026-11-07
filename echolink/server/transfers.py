"""文件传输句柄管理。

大文件不做"一次性塞进一帧",而是:

1. ``files.open_read`` / ``files.open_write`` 建立句柄;
2. 客户端按偏移反复 ``files.read`` / ``files.write``(每块 ≤ ``limits.max_chunk``);
3. ``files.close`` 结束。

写操作先落盘到同目录的 ``.echolink-part`` 临时文件,提交时才 ``os.replace``,
因此中途断线不会留下半个"看起来正常"的文件。
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Dict, Optional, Tuple

from echolink.common.errors import BadRequest, CommandError, Conflict, ErrorCode, NotFound
from echolink.common.protocol import DEFAULT_CHUNK_BYTES

PART_SUFFIX = ".echolink-part"


@dataclass
class Transfer:
    handle: str
    path: Path
    mode: str  # "read" | "write"
    file: IO[bytes]
    size: int
    temp_path: Optional[Path] = None
    offset: int = 0
    written: int = 0
    created_at: float = field(default_factory=time.time)

    def info(self) -> Dict[str, Any]:
        return {
            "handle": self.handle,
            "mode": self.mode,
            "path": str(self.path),
            "size": self.size,
            "offset": self.offset,
            "written": self.written,
        }


class TransferRegistry:
    """单个会话内的句柄表。"""

    def __init__(self, max_chunk: int = DEFAULT_CHUNK_BYTES, max_open: int = 16, max_upload_bytes: int = 0):
        self.max_chunk = max(1, int(max_chunk))
        self.max_open = max(1, int(max_open))
        self.max_upload_bytes = max(0, int(max_upload_bytes))
        self._items: Dict[str, Transfer] = {}

    # ---------- 生命周期 ----------
    def open_read(self, path: Path) -> Transfer:
        self._ensure_capacity()
        if not path.exists():
            raise NotFound(f"文件不存在: {path}")
        if not path.is_file():
            raise BadRequest(f"不是普通文件: {path}")
        try:
            handle_file = open(path, "rb")
        except OSError as exc:
            raise CommandError(f"无法读取文件: {exc}") from exc
        transfer = Transfer(
            handle=self._new_handle(),
            path=path,
            mode="read",
            file=handle_file,
            size=path.stat().st_size,
        )
        self._items[transfer.handle] = transfer
        return transfer

    def open_write(self, path: Path, overwrite: bool = False) -> Transfer:
        self._ensure_capacity()
        if path.exists():
            if path.is_dir():
                raise BadRequest(f"目标是目录: {path}")
            if not overwrite:
                raise Conflict(f"目标已存在: {path}(如需覆盖请传 overwrite=true)")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(f"无法创建目录 {path.parent}: {exc}") from exc
        temp_path = path.with_name(f"{path.name}{PART_SUFFIX}-{secrets.token_hex(4)}")
        try:
            handle_file = open(temp_path, "wb")
        except OSError as exc:
            raise CommandError(f"无法写入文件: {exc}") from exc
        transfer = Transfer(
            handle=self._new_handle(),
            path=path,
            mode="write",
            file=handle_file,
            size=0,
            temp_path=temp_path,
        )
        self._items[transfer.handle] = transfer
        return transfer

    def get(self, handle: str, mode: Optional[str] = None) -> Transfer:
        transfer = self._items.get(str(handle or ""))
        if transfer is None:
            raise NotFound(f"句柄不存在或已关闭: {handle}(可能因超时被回收)")
        if mode and transfer.mode != mode:
            raise BadRequest(f"句柄 {handle} 是 {transfer.mode} 模式,不能用于 {mode}")
        return transfer

    def close(self, handle: str, commit: bool = True) -> Dict[str, Any]:
        transfer = self._items.pop(str(handle or ""), None)
        if transfer is None:
            raise NotFound(f"句柄不存在或已关闭: {handle}")
        return self._finalize(transfer, commit)

    def close_all(self, commit: bool = False) -> int:
        items = list(self._items.values())
        self._items.clear()
        for transfer in items:
            try:
                self._finalize(transfer, commit)
            except Exception:
                pass
        return len(items)

    # ---------- 读写 ----------
    def read_chunk(self, handle: str, offset: Optional[int] = None, length: Optional[int] = None) -> Tuple[bytes, int, bool]:
        transfer = self.get(handle, "read")
        position = transfer.offset if offset is None else max(0, int(offset))
        if position > transfer.size:
            raise BadRequest(f"偏移 {position} 超过文件大小 {transfer.size}")
        limit = self.max_chunk if length is None else max(1, min(int(length), self.max_chunk))
        try:
            transfer.file.seek(position)
            data = transfer.file.read(limit)
        except OSError as exc:
            raise CommandError(f"读取失败: {exc}") from exc
        transfer.offset = position + len(data)
        return data, transfer.offset, transfer.offset >= transfer.size

    def write_chunk(self, handle: str, offset: Optional[int], data: bytes) -> int:
        transfer = self.get(handle, "write")
        position = transfer.written if offset is None else max(0, int(offset))
        if position > transfer.written:
            # 允许乱序/重传:空洞补零,保证文件长度正确
            gap = position - transfer.written
            try:
                transfer.file.seek(transfer.written)
                transfer.file.write(b"\x00" * gap)
            except OSError as exc:  # pragma: no cover
                raise CommandError(f"写入失败: {exc}") from exc
        if self.max_upload_bytes and transfer.written + len(data) > self.max_upload_bytes:
            raise CommandError(
                f"超出服务端上传上限({self.max_upload_bytes} 字节)", ErrorCode.TOO_LARGE
            )
        try:
            transfer.file.seek(position)
            transfer.file.write(data)
        except OSError as exc:
            raise CommandError(f"写入失败: {exc}") from exc
        transfer.written = max(transfer.written, position + len(data))
        transfer.offset = position + len(data)
        return transfer.written

    # ---------- 内部 ----------
    def _finalize(self, transfer: Transfer, commit: bool) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "handle": transfer.handle,
            "path": str(transfer.path),
            "mode": transfer.mode,
            "committed": bool(commit and transfer.mode == "write"),
        }
        if transfer.mode == "write":
            try:
                transfer.file.flush()
                os.fsync(transfer.file.fileno())
            except (OSError, ValueError):
                pass
        try:
            transfer.file.close()
        except OSError:  # pragma: no cover
            pass

        if transfer.mode == "write" and transfer.temp_path is not None:
            if commit:
                try:
                    os.replace(transfer.temp_path, transfer.path)
                except OSError as exc:
                    raise CommandError(f"提交文件失败: {exc}") from exc
                result["size"] = transfer.written
            else:
                try:
                    os.unlink(transfer.temp_path)
                except OSError:
                    pass
                result["aborted"] = True
        else:
            result["size"] = transfer.size
        return result

    def _ensure_capacity(self) -> None:
        if len(self._items) >= self.max_open:
            raise CommandError(f"会话内打开的文件过多(上限 {self.max_open}),请先关闭旧句柄", ErrorCode.BUSY)

    @staticmethod
    def _new_handle() -> str:
        return secrets.token_hex(8)

    def __len__(self) -> int:
        return len(self._items)
