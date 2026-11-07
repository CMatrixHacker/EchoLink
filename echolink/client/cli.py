"""命令行客户端:适合脚本、批处理与快速排查;内置交互式 shell。

示例::

    echolink cli -t 192.168.1.8:8765 -s <密钥> info
    echolink cli -t serial:COM5 -s <密钥> ls C:/Users/me/Documents
    echolink cli -t 192.168.1.8:8765 -s <密钥> get "D:/电影/a.mp4" ./a.mp4
    echolink cli -t 192.168.1.8:8765 -s <密钥> shell
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from echolink.client.core import EchoLinkClient, join_remote
from echolink.common.auth import read_secret_file
from echolink.common.errors import RemoteError
from echolink.common.protocol import ProtocolError
from echolink.transport.base import TransportError

DEFAULT_SECRET_PATH = os.path.join(os.path.expanduser("~"), ".echolink", "secret")


# ==================== 辅助输出 ====================
def human_size(value: Optional[int]) -> str:
    if value is None:
        return "-"
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_time(value: Optional[float]) -> str:
    if not value:
        return "-"
    import time as _time

    return _time.strftime("%Y-%m-%d %H:%M", _time.localtime(value))


def _dump(args: argparse.Namespace, data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _resolve_secret(args: argparse.Namespace) -> str:
    if getattr(args, "secret", None):
        return args.secret
    if getattr(args, "secret_file", None):
        return read_secret_file(args.secret_file)
    env = os.environ.get("ECHOLINK_SECRET")
    if env:
        return env
    if os.path.exists(DEFAULT_SECRET_PATH):
        return read_secret_file(DEFAULT_SECRET_PATH)
    raise SystemExit(
        "缺少配对密钥。请用 --secret / --secret-file 提供,或设置环境变量 ECHOLINK_SECRET,"
        f"或把密钥写进 {DEFAULT_SECRET_PATH}"
    )


def _progress_printer(label: str):
    last = {"pct": -1}

    def _update(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if pct == last["pct"]:
            return
        last["pct"] = pct
        bar = "#" * (pct // 4) + "-" * (25 - pct // 4)
        sys.stdout.write(f"\r{label} [{bar}] {pct:3d}%  {human_size(done)}/{human_size(total)}")
        sys.stdout.flush()
        if done >= total:
            sys.stdout.write("\n")
            sys.stdout.flush()

    return _update


# ==================== 子命令实现 ====================
def cmd_info(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.info()
    if args.json:
        _dump(args, data)
        return 0
    resources = data.get("resources") or {}
    audio = data.get("audio") or {}
    print(f"电脑名称 : {data.get('server')}")
    print(f"主机名   : {data.get('hostname')}")
    print(f"系统     : {data.get('os')}")
    print(f"平台后端 : {data.get('platform')} / {data.get('backend')}")
    print(f"运行时长 : {data.get('uptime_seconds')} 秒")
    memory_total = resources.get("memory_total")
    memory_percent = resources.get("memory_percent")
    if memory_total:
        print(f"内存     : {human_size(memory_total)} 已用 {memory_percent}%")
    disk_total = resources.get("disk_total")
    if disk_total:
        print(f"磁盘     : 共 {human_size(disk_total)} 可用 {human_size(resources.get('disk_free'))}")
    battery = data.get("battery")
    if battery:
        print(f"电池     : {battery.get('percent')}% {'充电中' if battery.get('plugged') else '未充电'}")
    if audio.get("level") is None:
        print("音量     : 未知(未安装 pycaw 或当前平台不支持读取)")
    else:
        print(f"音量     : {audio.get('level')}% {'(静音)' if audio.get('muted') else ''}")
    print("可访问目录:")
    for root in data.get("roots") or []:
        print(f"  - {root.get('label')}: {root.get('path')}")
    permissions = data.get("permissions") or {}
    print("权限     : " + ", ".join(f"{key}={'开' if value else '关'}" for key, value in permissions.items()))
    caps = data.get("capabilities") or {}
    enabled = [name for name, ok in caps.items() if ok]
    print("能力     : " + (", ".join(enabled) if enabled else "无"))
    return 0


def cmd_ping(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.ping()
    if args.json:
        _dump(args, data)
    else:
        print(f"pong (session={data.get('session')})")
    return 0


def cmd_caps(client: EchoLinkClient, args: argparse.Namespace) -> int:
    _dump(args, {"capabilities": client.capabilities_of_server(), "commands": client.command_names()})
    return 0


def cmd_ls(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.list_dir(args.path, show_hidden=args.hidden)
    if args.json:
        _dump(args, data)
        return 0
    print(f"{data.get('absolute')}  ({data.get('dirs')} 目录 / {data.get('files')} 文件)")
    for entry in data.get("entries") or []:
        marker = "d" if entry["type"] == "dir" else "-"
        size = human_size(entry.get("size")) if entry["type"] != "dir" else ""
        print(f"{marker} {entry['name']:<40} {size:>10}  {human_time(entry.get('mtime'))}")
    return 0


def cmd_stat(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.stat(args.path)
    if args.json:
        _dump(args, data)
    else:
        for key, value in data.items():
            print(f"{key:<10}: {value}")
    return 0


def cmd_mkdir(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.mkdir(args.path)
    print(f"已创建: {data.get('absolute')}")
    return 0


def cmd_rm(client: EchoLinkClient, args: argparse.Namespace) -> int:
    if args.recursive and not args.yes:
        answer = input(f"确认递归删除 {args.path} ? 输入 yes 继续: ").strip().lower()
        if answer != "yes":
            print("已取消")
            return 1
    data = client.delete(args.path, recursive=args.recursive)
    print(f"已删除: {data.get('deleted')}")
    return 0


def cmd_mv(client: EchoLinkClient, args: argparse.Namespace) -> int:
    if args.to:
        data = client.call("files.rename", {"path": args.path, "destination": args.to, "overwrite": args.overwrite})
    else:
        data = client.rename(args.path, args.name, overwrite=args.overwrite)
    print(f"{data.get('from')} -> {data.get('to')}")
    return 0


def cmd_find(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.search(args.root, args.pattern, max_depth=args.depth, limit=args.limit)
    if args.json:
        _dump(args, data)
        return 0
    print(f"在 {data.get('root')} 中匹配 {data.get('pattern')}: {data.get('count')} 项"
          + ("(已截断)" if data.get("truncated") else ""))
    for item in data.get("results") or []:
        print(f"  {item['path']}  ({human_size(item.get('size'))})")
    return 0


def cmd_hash(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.hash_file(args.path, args.algorithm)
    print(f"{data.get('algorithm')} {data.get('digest')}  {data.get('path')}")
    return 0


def cmd_preview(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.preview(args.path)
    if data.get("binary"):
        print(f"[二进制文件] {data.get('path')} {human_size(data.get('size'))}")
        return 0
    print(data.get("text", ""))
    if data.get("truncated"):
        print(f"\n…(仅显示前 {human_size(args.max_bytes if hasattr(args, 'max_bytes') else 65536)})")
    return 0


def cmd_get(client: EchoLinkClient, args: argparse.Namespace) -> int:
    local = args.local
    if not local:
        local = os.path.basename(args.remote.rstrip("/")) or "download.bin"
    data = client.download(
        args.remote,
        local,
        progress=None if args.quiet else _progress_printer("下载"),
        overwrite=not args.no_overwrite,
    )
    print(f"已保存: {data['path']} ({human_size(data['size'])})")
    return 0


def cmd_put(client: EchoLinkClient, args: argparse.Namespace) -> int:
    remote = args.remote
    if not remote:
        remote = os.path.basename(args.local)
    else:
        try:
            info = client.stat(remote)
        except RemoteError:
            info = None
        if info and info.get("type") == "dir":
            remote = join_remote(info.get("path") or remote, os.path.basename(args.local))
    data = client.upload(
        args.local,
        remote,
        progress=None if args.quiet else _progress_printer("上传"),
        overwrite=not args.no_overwrite,
    )
    print(f"已上传: {data['path']} ({human_size(data['size'])})")
    return 0


def cmd_shot(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.screenshot(args.out)
    print(f"已保存截图: {data.get('path')} ({human_size(data.get('size'))})")
    return 0


def cmd_mute(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.mute(args.state)
    if data.get("muted") is None:
        print("已发送静音快捷键(无法读取实际状态)")
    else:
        print("已静音" if data["muted"] else "已取消静音")
    return 0


def cmd_volume(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.volume(args.level)
    print(f"音量已设为 {data.get('level')}%")
    return 0


def cmd_audio(client: EchoLinkClient, args: argparse.Namespace) -> int:
    _dump(args, client.call("system.audio"))
    return 0


def _power(action: str):
    def _handler(client: EchoLinkClient, args: argparse.Namespace) -> int:
        if action == "lock":
            client.lock()
        elif action == "sleep":
            client.sleep()
        elif action == "shutdown":
            client.shutdown(delay=args.delay, force=args.force)
        elif action == "restart":
            client.restart(delay=args.delay, force=args.force)
        elif action == "logoff":
            client.logoff()
        elif action == "cancel-shutdown":
            _dump(args, client.call("system.cancel_shutdown"))
            return 0
        print(f"已执行: {action}")
        return 0

    return _handler


def cmd_clipboard(client: EchoLinkClient, args: argparse.Namespace) -> int:
    if args.set is not None:
        client.clipboard_set(args.set)
        print(f"已写入剪贴板({len(args.set)} 字符)")
    else:
        print(client.clipboard_get())
    return 0


def cmd_open(client: EchoLinkClient, args: argparse.Namespace) -> int:
    _dump(args, client.open_target(args.target))
    return 0


def cmd_notify(client: EchoLinkClient, args: argparse.Namespace) -> int:
    _dump(args, client.notify(args.title, args.message))
    return 0


def cmd_media(client: EchoLinkClient, args: argparse.Namespace) -> int:
    _dump(args, client.media(args.action))
    return 0


def cmd_ps(client: EchoLinkClient, args: argparse.Namespace) -> int:
    data = client.processes(args.limit)
    if args.json:
        _dump(args, data)
        return 0
    print(f"{'PID':>8}  进程")
    for item in data.get("processes") or []:
        print(f"{item['pid']:>8}  {item['name']}")
    return 0


def cmd_kill(client: EchoLinkClient, args: argparse.Namespace) -> int:
    if not args.yes:
        answer = input(f"确认结束进程 {args.pid} ? 输入 yes 继续: ").strip().lower()
        if answer != "yes":
            print("已取消")
            return 1
    _dump(args, client.kill(args.pid))
    return 0


# ==================== 交互式 shell ====================
SHELL_HELP = """可用命令:
  ls [目录]              列出目录
  cd <目录>              切换目录(支持 .. 与根目录名)
  pwd                    显示当前目录
  get <远程文件> [本地]   下载
  put <本地文件> [远程]   上传
  cat <远程文件>          预览文本
  rm <路径>               删除文件
  mkdir <目录>            新建目录
  info                   电脑概况
  mute [on|off|toggle]   静音控制
  volume <0-100>         设置音量
  lock / sleep / shutdown / restart
  shot [文件名]           截屏到本地
  ps [条数]               进程列表
  kill <pid>              结束进程
  clipboard [文本]        读取/写入剪贴板
  notify <标题> <正文>    桌面通知
  media <play_pause|next|prev|stop>
  exit                   退出
"""


class InteractiveShell:
    def __init__(self, client: EchoLinkClient):
        self.client = client
        self.cwd = ""

    def run(self) -> int:
        host = self.client.server.get("hostname") or "server"
        print(f"已连接 {self.client.server.get('name')} ({host})。输入 help 查看命令。")
        while True:
            try:
                line = input(f"echolink:{self.cwd or '/'}> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not line:
                continue
            if line in ("exit", "quit", "q"):
                return 0
            if line in ("help", "?"):
                print(SHELL_HELP)
                continue
            try:
                parts = shlex.split(line)
            except ValueError as exc:
                print(f"参数解析失败: {exc}")
                continue
            try:
                if not self._dispatch(parts[0], parts[1:]):
                    return 0
            except RemoteError as exc:
                print(f"错误: {exc}")
            except (TransportError, ProtocolError) as exc:
                print(f"连接错误: {exc}")
            except Exception as exc:  # pragma: no cover - 交互容错
                print(f"执行失败: {exc}")

    def _dispatch(self, name: str, argv: List[str]) -> bool:
        client = self.client
        if name == "ls":
            data = client.list_dir(argv[0] if argv else self.cwd)
            self.cwd = data.get("path") or self.cwd
            for entry in data.get("entries") or []:
                suffix = "/" if entry["type"] == "dir" else ""
                print(f"  {entry['name']}{suffix}")
        elif name == "cd":
            target = argv[0] if argv else ""
            if target in ("/", "~", ""):
                self.cwd = ""
                return True
            data = client.list_dir(target)
            self.cwd = data.get("path") or ""
        elif name == "pwd":
            print(self.cwd or "/")
        elif name == "get":
            if not argv:
                print("用法: get <远程文件> [本地路径]")
                return True
            local = argv[1] if len(argv) > 1 else os.path.basename(argv[0])
            data = client.download(argv[0], local, progress=_progress_printer("下载"))
            print(f"已保存 {data['path']} ({human_size(data['size'])})")
        elif name == "put":
            if not argv:
                print("用法: put <本地文件> [远程目录]")
                return True
            remote = argv[1] if len(argv) > 1 else join_remote(self.cwd, os.path.basename(argv[0]))
            data = client.upload(argv[0], remote, progress=_progress_printer("上传"))
            print(f"已上传 {data['path']}")
        elif name == "cat":
            data = client.preview(argv[0])
            print(data.get("text", ""))
        elif name == "rm":
            data = client.delete(argv[0])
            print(f"已删除 {data.get('deleted')}")
        elif name == "mkdir":
            data = client.mkdir(join_remote(self.cwd, argv[0]))
            print(f"已创建 {data.get('absolute')}")
        elif name == "info":
            cmd_info(client, argparse.Namespace(json=False))
        elif name == "mute":
            cmd_mute(client, argparse.Namespace(state=argv[0] if argv else "toggle"))
        elif name == "volume":
            cmd_volume(client, argparse.Namespace(level=int(argv[0])))
        elif name in ("lock", "sleep", "logoff"):
            getattr(client, name)()
            print(f"已执行 {name}")
        elif name in ("shutdown", "restart"):
            getattr(client, name)()
            print(f"已执行 {name}")
        elif name == "shot":
            data = client.screenshot(argv[0] if argv else "screenshot.png")
            print(f"已保存 {data['path']}")
        elif name == "ps":
            cmd_ps(client, argparse.Namespace(limit=int(argv[0]) if argv else 30, json=False))
        elif name == "kill":
            _dump(argparse.Namespace(json=False), client.kill(int(argv[0])))
        elif name == "clipboard":
            if argv:
                client.clipboard_set(" ".join(argv))
                print("已写入剪贴板")
            else:
                print(client.clipboard_get())
        elif name == "notify":
            client.notify(argv[0] if argv else "EchoLink", " ".join(argv[1:]))
            print("已发送通知")
        elif name == "media":
            client.media(argv[0] if argv else "play_pause")
            print("已发送媒体键")
        elif name in ("exit", "quit", "q"):
            return False
        else:
            print(f"未知命令: {name}(输入 help 查看)")
        return True


def cmd_shell(client: EchoLinkClient, args: argparse.Namespace) -> int:
    return InteractiveShell(client).run()


# ==================== 参数解析 ====================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echolink cli",
        description="EchoLink 命令行客户端:远程浏览/传输文件,控制电脑音量、电源等",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="连接目标示例: 192.168.1.8:8765 / tcp://192.168.1.8:8765 / serial:COM5 / bt:AA:BB:CC:DD:EE:FF",
    )
    parser.add_argument("-t", "--target", default="127.0.0.1:8765", help="连接目标(默认 127.0.0.1:8765)")
    parser.add_argument("-s", "--secret", default="", help="配对密钥(64 位十六进制)")
    parser.add_argument("--secret-file", default="", help="从文件读取密钥")
    parser.add_argument("--timeout", type=float, default=20.0, help="请求超时秒数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="电脑概况").set_defaults(func=cmd_info)
    sub.add_parser("ping", help="连通性测试").set_defaults(func=cmd_ping)
    sub.add_parser("caps", help="查询能力与命令列表").set_defaults(func=cmd_caps)

    p_ls = sub.add_parser("ls", help="列出目录")
    p_ls.add_argument("path", nargs="?", default="")
    p_ls.add_argument("-a", "--hidden", action="store_true", help="显示隐藏文件")
    p_ls.set_defaults(func=cmd_ls)

    p_stat = sub.add_parser("stat", help="路径详情")
    p_stat.add_argument("path")
    p_stat.set_defaults(func=cmd_stat)

    p_mkdir = sub.add_parser("mkdir", help="新建目录")
    p_mkdir.add_argument("path")
    p_mkdir.set_defaults(func=cmd_mkdir)

    p_rm = sub.add_parser("rm", help="删除文件/目录")
    p_rm.add_argument("path")
    p_rm.add_argument("-r", "--recursive", action="store_true", help="递归删除目录")
    p_rm.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    p_rm.set_defaults(func=cmd_rm)

    p_mv = sub.add_parser("mv", help="重命名 / 移动")
    p_mv.add_argument("path")
    p_mv.add_argument("-n", "--name", default="", help="同目录下的新名字")
    p_mv.add_argument("--to", default="", help="目标完整路径")
    p_mv.add_argument("--overwrite", action="store_true")
    p_mv.set_defaults(func=cmd_mv)

    p_find = sub.add_parser("find", help="按文件名检索")
    p_find.add_argument("root", nargs="?", default="")
    p_find.add_argument("-p", "--pattern", default="*")
    p_find.add_argument("-d", "--depth", type=int, default=6)
    p_find.add_argument("-l", "--limit", type=int, default=200)
    p_find.set_defaults(func=cmd_find)

    p_hash = sub.add_parser("hash", help="计算文件校验值")
    p_hash.add_argument("path")
    p_hash.add_argument("-a", "--algorithm", default="sha256")
    p_hash.set_defaults(func=cmd_hash)

    p_preview = sub.add_parser("preview", help="预览文本文件")
    p_preview.add_argument("path")
    p_preview.add_argument("--max-bytes", type=int, default=64 * 1024)
    p_preview.set_defaults(func=cmd_preview)

    p_get = sub.add_parser("get", help="下载文件")
    p_get.add_argument("remote")
    p_get.add_argument("local", nargs="?", default="")
    p_get.add_argument("-q", "--quiet", action="store_true")
    p_get.add_argument("--no-overwrite", action="store_true")
    p_get.set_defaults(func=cmd_get)

    p_put = sub.add_parser("put", help="上传文件")
    p_put.add_argument("local")
    p_put.add_argument("remote", nargs="?", default="")
    p_put.add_argument("-q", "--quiet", action="store_true")
    p_put.add_argument("--no-overwrite", action="store_true")
    p_put.set_defaults(func=cmd_put)

    p_shot = sub.add_parser("shot", help="截屏保存到本地")
    p_shot.add_argument("out", nargs="?", default="screenshot.png")
    p_shot.set_defaults(func=cmd_shot)

    p_mute = sub.add_parser("mute", help="静音控制")
    p_mute.add_argument("state", nargs="?", default="toggle", choices=["on", "off", "toggle"])
    p_mute.set_defaults(func=cmd_mute)

    p_vol = sub.add_parser("volume", help="设置音量")
    p_vol.add_argument("level", type=int)
    p_vol.set_defaults(func=cmd_volume)

    sub.add_parser("audio", help="读取音量/静音状态").set_defaults(func=cmd_audio)

    sub.add_parser("lock", help="锁屏").set_defaults(func=_power("lock"))
    sub.add_parser("sleep", help="睡眠").set_defaults(func=_power("sleep"))
    sub.add_parser("logoff", help="注销").set_defaults(func=_power("logoff"))
    sub.add_parser("cancel-shutdown", help="取消关机").set_defaults(func=_power("cancel-shutdown"))

    for name, helper in (("shutdown", "关机"), ("restart", "重启")):
        p_power = sub.add_parser(name, help=helper)
        p_power.add_argument("--delay", type=int, default=0, help="延迟秒数")
        p_power.add_argument("--force", action="store_true", help="强制关闭程序")
        p_power.set_defaults(func=_power(name))

    p_clip = sub.add_parser("clipboard", help="读取/写入剪贴板")
    p_clip.add_argument("--set", default=None, help="写入内容(省略则读取)")
    p_clip.set_defaults(func=cmd_clipboard)

    p_open = sub.add_parser("open", help="在电脑上打开路径/网址")
    p_open.add_argument("target")
    p_open.set_defaults(func=cmd_open)

    p_notify = sub.add_parser("notify", help="桌面通知")
    p_notify.add_argument("title")
    p_notify.add_argument("message", nargs="?", default="")
    p_notify.set_defaults(func=cmd_notify)

    p_media = sub.add_parser("media", help="媒体按键")
    p_media.add_argument("action", nargs="?", default="play_pause",
                         choices=["play_pause", "next", "prev", "stop"])
    p_media.set_defaults(func=cmd_media)

    p_ps = sub.add_parser("ps", help="进程列表")
    p_ps.add_argument("-l", "--limit", type=int, default=60)
    p_ps.set_defaults(func=cmd_ps)

    p_kill = sub.add_parser("kill", help="结束进程")
    p_kill.add_argument("pid", type=int)
    p_kill.add_argument("-y", "--yes", action="store_true")
    p_kill.set_defaults(func=cmd_kill)

    sub.add_parser("shell", help="交互式 shell").set_defaults(func=cmd_shell)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    secret = _resolve_secret(args)
    try:
        with EchoLinkClient.connect(args.target, secret, timeout=args.timeout, app="echolink-cli") as client:
            if not args.json and args.command not in ("ls", "info", "get", "put", "shot", "ps", "shell", "ping"):
                print(f"已连接 {client.server.get('name')} ({client.transport.describe if client.transport else ''})")
            return int(args.func(client, args) or 0)
    except RemoteError as exc:
        print(f"服务端错误: {exc}", file=sys.stderr)
        return 1
    except (TransportError, ProtocolError) as exc:
        print(f"连接失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
