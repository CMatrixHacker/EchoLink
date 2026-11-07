"""路径沙箱:任何越界访问都必须被拒绝。"""

from __future__ import annotations

import os

import pytest

from echolink.common.errors import BadRequest, Forbidden, NotFound
from echolink.common.paths import Sandbox


@pytest.fixture()
def sandbox(workspace):
    return Sandbox([str(workspace)])


def test_resolve_root_when_path_empty(sandbox, workspace):
    assert sandbox.resolve("") == workspace.resolve()
    assert sandbox.resolve(None) == workspace.resolve()


def test_resolve_relative_path(sandbox, workspace):
    assert sandbox.resolve("docs/readme.txt") == (workspace / "docs" / "readme.txt").resolve()


def test_resolve_absolute_inside(sandbox, workspace):
    target = (workspace / "docs").resolve()
    assert sandbox.resolve(str(target)) == target


def test_rejects_parent_escape(sandbox):
    with pytest.raises(Forbidden):
        sandbox.resolve("../outside.txt")
    with pytest.raises(Forbidden):
        sandbox.resolve("docs/../../outside.txt")


def test_rejects_absolute_outside(sandbox, tmp_path):
    with pytest.raises(Forbidden):
        sandbox.resolve(str(tmp_path / "elsewhere.txt"))


def test_must_exist_raises_not_found(sandbox):
    with pytest.raises(NotFound):
        sandbox.resolve("missing.txt", must_exist=True)


def test_kind_validation(sandbox):
    with pytest.raises(BadRequest):
        sandbox.resolve("docs", kind="file")
    with pytest.raises(BadRequest):
        sandbox.resolve("docs/readme.txt", kind="dir")


def test_delete_root_is_forbidden(sandbox):
    with pytest.raises(Forbidden):
        sandbox.resolve_for_delete("")


def test_delete_disabled(sandbox, workspace):
    locked = Sandbox([str(workspace)], allow_delete=False)
    with pytest.raises(Forbidden):
        locked.resolve_for_delete("docs/readme.txt")


def test_write_disabled(sandbox, workspace):
    locked = Sandbox([str(workspace)], allow_write=False)
    with pytest.raises(Forbidden):
        locked.resolve_for_write("new.txt")


def test_display_is_relative(sandbox):
    target = (sandbox.default_root / "docs" / "readme.txt")
    assert sandbox.display(target) == "docs/readme.txt"
    assert sandbox.display(sandbox.default_root) == "."


def test_case_insensitive_on_windows(sandbox, workspace):
    if os.name != "nt":
        pytest.skip("仅 Windows 需要大小写不敏感校验")
    upper = str(workspace.resolve()).upper()
    assert sandbox.is_allowed(__import__("pathlib").Path(upper))
