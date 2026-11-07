"""文件传输句柄:分块写入、临时文件提交、放弃回滚。"""

from __future__ import annotations

import os

import pytest

from echolink.common.errors import BadRequest, Conflict, NotFound
from echolink.server.transfers import PART_SUFFIX, TransferRegistry


@pytest.fixture()
def registry():
    return TransferRegistry(max_chunk=1024)


def test_write_then_commit(tmp_path, registry):
    target = tmp_path / "out.bin"
    transfer = registry.open_write(target)
    registry.write_chunk(transfer.handle, 0, b"hello ")
    registry.write_chunk(transfer.handle, 6, b"world")
    result = registry.close(transfer.handle, commit=True)
    assert target.read_bytes() == b"hello world"
    assert result["committed"] is True
    assert result["size"] == 11


def test_abort_leaves_no_file(tmp_path, registry):
    target = tmp_path / "out.bin"
    transfer = registry.open_write(target)
    registry.write_chunk(transfer.handle, 0, b"partial")
    result = registry.close(transfer.handle, commit=False)
    assert not target.exists()
    assert not list(tmp_path.glob(f"*{PART_SUFFIX}*"))
    assert result.get("aborted") is True
    # 未提交时不会出现在目录里,只有 .part 临时文件曾经存在


def test_overwrite_guard(tmp_path, registry):
    target = tmp_path / "exists.txt"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(Conflict):
        registry.open_write(target)
    transfer = registry.open_write(target, overwrite=True)
    registry.write_chunk(transfer.handle, 0, b"new")
    registry.close(transfer.handle, commit=True)
    assert target.read_text(encoding="utf-8") == "new"


def test_read_chunks_and_eof(tmp_path, registry):
    target = tmp_path / "data.bin"
    payload = bytes(range(256)) * 12  # 3072 字节
    target.write_bytes(payload)
    transfer = registry.open_read(target)
    collected = bytearray()
    while True:
        data, offset, eof = registry.read_chunk(transfer.handle)
        collected.extend(data)
        assert offset == len(collected)
        if eof:
            break
    assert bytes(collected) == payload
    registry.close(transfer.handle)


def test_read_respects_max_chunk(tmp_path):
    registry = TransferRegistry(max_chunk=64)
    target = tmp_path / "data.bin"
    target.write_bytes(b"x" * 200)
    transfer = registry.open_read(target)
    data, _offset, _eof = registry.read_chunk(transfer.handle, length=10_000)
    assert len(data) == 64
    registry.close(transfer.handle)


def test_read_offset_out_of_range(tmp_path, registry):
    target = tmp_path / "small.bin"
    target.write_bytes(b"12345")
    transfer = registry.open_read(target)
    with pytest.raises(BadRequest):
        registry.read_chunk(transfer.handle, offset=999)
    registry.close(transfer.handle)


def test_unknown_handle(registry):
    with pytest.raises(NotFound):
        registry.get("nope")


def test_mode_mismatch(tmp_path, registry):
    target = tmp_path / "x.bin"
    target.write_bytes(b"abc")
    transfer = registry.open_read(target)
    with pytest.raises(BadRequest):
        registry.write_chunk(transfer.handle, 0, b"zzz")
    registry.close(transfer.handle)


def test_write_gap_pads_with_zeros(tmp_path, registry):
    target = tmp_path / "gap.bin"
    transfer = registry.open_write(target)
    registry.write_chunk(transfer.handle, 4, b"abcd")
    registry.close(transfer.handle)
    assert target.read_bytes() == b"\x00\x00\x00\x00abcd"


def test_capacity_limit(tmp_path):
    registry = TransferRegistry(max_open=2)
    first = registry.open_write(tmp_path / "a.txt")
    registry.open_write(tmp_path / "b.txt")
    from echolink.common.errors import CommandError

    with pytest.raises(CommandError):
        registry.open_write(tmp_path / "c.txt")
    registry.close(first.handle, commit=False)


def test_upload_limit(tmp_path):
    from echolink.common.errors import CommandError

    registry = TransferRegistry(max_upload_bytes=10)
    transfer = registry.open_write(tmp_path / "limited.bin")
    with pytest.raises(CommandError):
        registry.write_chunk(transfer.handle, 0, b"0123456789ABCDEF")
    registry.close(transfer.handle, commit=False)


def test_close_all_aborts_open_handles(tmp_path):
    registry = TransferRegistry()
    transfer = registry.open_write(tmp_path / "left.bin")
    registry.write_chunk(transfer.handle, 0, b"data")
    assert registry.close_all(commit=False) == 1
    assert not (tmp_path / "left.bin").exists()
    assert len(registry) == 0


def test_part_file_naming_hidden_while_uploading(tmp_path):
    registry = TransferRegistry()
    transfer = registry.open_write(tmp_path / "video.mp4")
    registry.write_chunk(transfer.handle, 0, b"chunk")
    part_files = [item.name for item in tmp_path.iterdir()]
    assert any(PART_SUFFIX in name for name in part_files)
    assert "video.mp4" not in part_files
    registry.close(transfer.handle, commit=True)
    assert (tmp_path / "video.mp4").exists()
    assert not any(PART_SUFFIX in item.name for item in tmp_path.iterdir())
