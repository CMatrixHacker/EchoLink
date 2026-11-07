"""入口层回归测试:``echolink cli …`` 的参数透传、图形界面缺失提示、信息渲染。"""

from __future__ import annotations

import argparse

import pytest

from echolink.__main__ import _split_cli_token, main
from echolink.client import cli as cli_module


# ==================== cli 参数透传 ====================
def test_split_cli_token_basic():
    assert _split_cli_token(["cli", "-t", "1.2.3.4:8765", "ping"]) == ([], ["-t", "1.2.3.4:8765", "ping"])


def test_split_cli_token_with_global_option():
    head, tail = _split_cli_token(["--log-level", "DEBUG", "cli", "info"])
    assert head == ["--log-level", "DEBUG"]
    assert tail == ["info"]


def test_split_cli_token_does_not_match_option_value():
    # --log-level 的取值恰好是 "cli" 时,不应被当成子命令
    assert _split_cli_token(["--log-level", "cli"]) is None


def test_split_cli_token_without_cli():
    assert _split_cli_token(["server", "--port", "9000"]) is None


def test_main_cli_passes_options_through(capsys):
    """``-t`` / ``--secret`` 这类选项必须被命令行客户端收到,而不是被外层解析器吞掉。"""
    code = main(["cli", "-t", "127.0.0.1:1", "--secret", "a" * 32, "ping"])
    assert code == 1  # 连接被拒绝,说明参数已被接受(而不是 argparse 报用法错误)
    assert "连接失败" in capsys.readouterr().err


def test_main_cli_without_args_prints_help(capsys):
    assert main(["cli"]) == 0
    out = capsys.readouterr().out
    assert "用法" in out or "usage" in out


def test_main_cli_help_exits_cleanly():
    with pytest.raises(SystemExit) as exc:
        main(["cli", "-h"])
    assert exc.value.code == 0


def test_cli_parser_accepts_target():
    args = cli_module.build_parser().parse_args(["-t", "10.0.0.2:8765", "ls", "docs"])
    assert args.target == "10.0.0.2:8765"
    assert args.path == "docs"


# ==================== 信息渲染 ====================
class _FakeClient:
    def __init__(self, audio):
        self._audio = audio

    def info(self):
        return {
            "server": "测试机",
            "hostname": "test",
            "os": "Windows",
            "platform": "Windows",
            "backend": "windows",
            "uptime_seconds": 12,
            "resources": {},
            "audio": self._audio,
            "roots": [{"label": "shared", "path": "D:/shared"}],
            "permissions": {"write": True},
            "capabilities": {"mute": True},
        }


def test_info_handles_unknown_volume(capsys):
    cli_module.cmd_info(_FakeClient({"level": None, "muted": None}), argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert "None%" not in out
    assert "未知" in out


def test_info_renders_known_volume(capsys):
    cli_module.cmd_info(_FakeClient({"level": 42, "muted": True}), argparse.Namespace(json=False))
    out = capsys.readouterr().out
    assert "42%" in out
    assert "静音" in out
