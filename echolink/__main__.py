"""统一入口:``python -m echolink`` / ``echolink``。

子命令::

    echolink server    启动电脑端服务(默认读取 server.json)
    echolink cli       命令行客户端(沿用 echolink cli 的全部子命令)
    echolink gui       桌面图形控制台
    echolink android   启动 Kivy 界面(打包进 APK 后即安卓 App)
    echolink keygen    生成配对密钥
    echolink ports     列出串口/蓝牙串口
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import time
from typing import List, Optional

from echolink import __version__
from echolink.common.auth import read_secret_file, write_secret_file
from echolink.server.config import (
    DEFAULT_PORT,
    ServerConfig,
    config_path_from_args,
    default_config,
)

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format=LOG_FORMAT,
        datefmt="%H:%M:%S",
    )


def _mask(secret: str) -> str:
    if not secret:
        return "(未设置)"
    if len(secret) <= 10:
        return secret[:2] + "***"
    return f"{secret[:6]}…{secret[-4:]}"


def _local_addresses() -> List[str]:
    addresses = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            address = info[4][0]
            if ":" in address:
                continue
            if address.startswith("127.") or address.startswith("169.254."):
                continue
            addresses.add(address)
    except socket.gaierror:  # pragma: no cover
        pass
    if not addresses:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            addresses.add(probe.getsockname()[0])
            probe.close()
        except OSError:  # pragma: no cover
            pass
    return sorted(addresses)


def _print_banner(config: ServerConfig, port: Optional[int]) -> None:
    print("=" * 68)
    print(f" EchoLink {__version__} —— 电脑端服务已启动")
    print("=" * 68)
    print(f" 服务名称 : {config.name}")
    print(f" 配对密钥 : {_mask(config.secret)}   (完整密钥见配置文件)")
    print(f" 可访问   : {', '.join(config.roots)}")
    if config.tcp.enabled:
        for address in _local_addresses() or ["127.0.0.1"]:
            print(f" 局域网地址: tcp://{address}:{port or config.tcp.port}")
    if config.bluetooth.enabled:
        print(f" 蓝牙串口 : {config.bluetooth.port or '自动探测(需先与手机配对)'}")
    print("-" * 68)
    print(" 手机端填写上面的地址与配对密钥即可连接。按 Ctrl+C 停止服务。")
    print("=" * 68)


# ==================== 子命令 ====================
def cmd_server(args: argparse.Namespace) -> int:
    path = config_path_from_args(args.config)
    created = False

    if args.init or not os.path.exists(path):
        config = default_config(path)
        if args.root:
            config.roots = [os.path.abspath(item) for item in args.root]
        if args.host:
            config.tcp.host = args.host
        if args.port:
            config.tcp.port = args.port
        if args.bt is not None:
            config.bluetooth.enabled = True
        saved = config.save(path)
        created = True
        print(f"已生成配置文件: {saved}")
        print(f"配对密钥(请抄到手机上): {config.secret}")
        if args.init:
            print("\n下一步: 运行 `echolink server` 启动服务,或用 `echolink gui` 打开控制台。")
            return 0
    else:
        try:
            config = ServerConfig.load(path)
        except (OSError, ValueError) as exc:
            print(f"读取配置失败: {exc}", file=sys.stderr)
            return 2

    # 命令行覆盖
    if args.host:
        config.tcp.host = args.host
    if args.port:
        config.tcp.port = args.port
    if args.no_tcp:
        config.tcp.enabled = False
    if args.bt is not None:
        config.bluetooth.enabled = True
        if args.bt not in ("", "auto"):
            config.bluetooth.port = args.bt
    if args.backend:
        config.backend = args.backend
    if args.root:
        config.roots = [os.path.abspath(item) for item in args.root]
    if args.audit is not None:
        config.audit_file = args.audit

    try:
        config.validate()
    except ValueError as exc:
        print(f"配置有误: {exc}", file=sys.stderr)
        return 2

    from echolink.server.app import EchoLinkServer

    try:
        server = EchoLinkServer(config)
    except Exception as exc:  # noqa: BLE001
        print(f"初始化失败: {exc}", file=sys.stderr)
        return 2

    try:
        info = server.start()
    except Exception as exc:  # noqa: BLE001
        print(f"启动失败: {exc}", file=sys.stderr)
        return 2

    server.install_signal_handlers()
    _print_banner(config, info.get("tcp", {}).get("port"))
    if created:
        print(f"\n提示: 配置文件在 {config.source_path}");
    try:
        while server.running:
            time.sleep(0.4)
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.stop()
    return 0


def cmd_cli(args: argparse.Namespace) -> int:
    from echolink.client import cli as cli_module

    rest = list(args.rest or [])
    if not rest:
        cli_module.build_parser().print_help()
        return 0
    return cli_module.main(rest)


def cmd_gui(args: argparse.Namespace) -> int:
    secret = args.secret
    if args.secret_file:
        secret = read_secret_file(args.secret_file)
    if not secret:
        default_secret = os.path.join(os.path.expanduser("~"), ".echolink", "secret")
        if os.path.exists(default_secret):
            secret = read_secret_file(default_secret)
    try:
        from echolink.client.gui import main as gui_main
    except ModuleNotFoundError as exc:
        if exc.name == "tkinter":
            print("当前 Python 未包含 tkinter,无法启动图形界面。", file=sys.stderr)
            print(f"  - 当前解释器: {sys.executable}", file=sys.stderr)
            print("  - Windows 请安装 python.org 官方版(自带 tkinter),或用带 tkinter 的解释器运行:", file=sys.stderr)
            print('      py -3 -m echolink gui -t <地址> -s <密钥>', file=sys.stderr)
            print("  - 不装图形界面也能用: `echolink cli <命令>` 覆盖全部功能。", file=sys.stderr)
            return 1
        print(f"无法启动图形界面: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 其他导入问题
        print(f"无法启动图形界面: {exc}", file=sys.stderr)
        return 1
    gui_main(target=args.target, secret=secret)
    return 0


def cmd_android(args: argparse.Namespace) -> int:
    try:
        from echolink.client.android.main import run
    except Exception as exc:  # noqa: BLE001
        print(f"无法启动 Kivy 界面: {exc}", file=sys.stderr)
        print("提示: 桌面调试请先 pip install kivy;打包安卓请用 buildozer。", file=sys.stderr)
        return 1
    run()
    return 0


def cmd_keygen(args: argparse.Namespace) -> int:
    target = args.out or os.path.join(os.path.expanduser("~"), ".echolink", "secret")
    if os.path.exists(target) and not args.force:
        existing = read_secret_file(target)
        print(f"密钥已存在: {target}")
        print(f"密钥: {existing}")
        print("如需重新生成,请加 --force(会让所有设备需要重新配对)")
        return 0
    try:
        secret = write_secret_file(target, exclusive=not args.force)
    except OSError as exc:
        print(f"写入失败: {exc}", file=sys.stderr)
        return 1
    print(f"已生成配对密钥: {secret}")
    print(f"保存位置: {os.path.abspath(target)}")
    print("把它填到手机端,或写进服务端配置的 secret 字段。")
    return 0


def cmd_ports(args: argparse.Namespace) -> int:
    try:
        from echolink.transport.serial_bt import list_serial_ports
    except Exception as exc:  # noqa: BLE001
        print(f"读取串口失败: {exc}", file=sys.stderr)
        return 1
    try:
        ports = list_serial_ports()
    except Exception as exc:  # noqa: BLE001 - pyserial 缺失
        print(f"需要 pyserial: {exc}", file=sys.stderr)
        return 1
    if not ports:
        print("没有发现串口。蓝牙串口需要先把手机与电脑配对。")
        return 0
    print(f"{'设备':<12}{'类型':<10}说明")
    for info in ports:
        kind = "蓝牙 SPP" if info.get("bluetooth") == "1" else "串口"
        print(f"{info['device']:<12}{kind:<10}{info.get('description', '')}")
    return 0


# ==================== 参数解析 ====================
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echolink",
        description="EchoLink —— 以电脑为中心的手机/PC 互联工具(蓝牙 + 局域网)",
    )
    parser.add_argument("-V", "--version", action="version", version=f"EchoLink {__version__}")
    parser.add_argument("--log-level", default="INFO", help="日志级别(DEBUG/INFO/WARNING/ERROR)")
    sub = parser.add_subparsers(dest="command")

    p_server = sub.add_parser("server", help="启动电脑端服务")
    p_server.add_argument("-c", "--config", default=None, help="配置文件路径(默认 server.json)")
    p_server.add_argument("--init", action="store_true", help="只生成默认配置后退出")
    p_server.add_argument("--host", default=None, help=f"监听地址(默认 0.0.0.0)")
    p_server.add_argument("--port", type=int, default=None, help=f"监听端口(默认 {DEFAULT_PORT})")
    p_server.add_argument("--no-tcp", action="store_true", help="关闭局域网监听")
    p_server.add_argument("--bt", nargs="?", const="auto", default=None, help="启用蓝牙串口,可指定 COM 口")
    p_server.add_argument("--root", action="append", default=None, help="覆盖可访问目录(可多次)")
    p_server.add_argument("--backend", default=None, help="强制平台后端: windows/posix/null")
    p_server.add_argument("--audit", default=None, help="审计日志路径(空字符串表示不写文件)")
    p_server.set_defaults(func=cmd_server)

    p_cli = sub.add_parser("cli", help="命令行客户端(其余参数直接传给 cli)")
    p_cli.add_argument("rest", nargs=argparse.REMAINDER, help="cli 子命令与参数")
    p_cli.set_defaults(func=cmd_cli)

    p_gui = sub.add_parser("gui", help="桌面图形控制台")
    p_gui.add_argument("-t", "--target", default="127.0.0.1:8765")
    p_gui.add_argument("-s", "--secret", default="")
    p_gui.add_argument("--secret-file", default="")
    p_gui.set_defaults(func=cmd_gui)

    p_android = sub.add_parser("android", help="启动 Android(Kivy)界面")
    p_android.set_defaults(func=cmd_android)

    p_keygen = sub.add_parser("keygen", help="生成配对密钥")
    p_keygen.add_argument("-o", "--out", default=None, help="输出路径")
    p_keygen.add_argument("-f", "--force", action="store_true", help="覆盖已有密钥")
    p_keygen.set_defaults(func=cmd_keygen)

    p_ports = sub.add_parser("ports", help="列出串口/蓝牙串口")
    p_ports.set_defaults(func=cmd_ports)

    return parser


def _build_global_parser() -> argparse.ArgumentParser:
    """仅解析全局选项的小解析器,用于 ``echolink [全局选项] cli …`` 的前半段。"""
    parser = argparse.ArgumentParser(prog="echolink", add_help=False)
    parser.add_argument("-V", "--version", action="version", version=f"EchoLink {__version__}")
    parser.add_argument("--log-level", default="INFO", help="日志级别(DEBUG/INFO/WARNING/ERROR)")
    return parser


def _split_cli_token(raw: List[str]):
    """定位 ``cli`` 子命令,返回 (前半段, 交给命令行客户端的后半段)。

    ``argparse.REMAINDER`` 在子解析器里遇到 ``-t`` 这类选项时会失效,
    因此 ``echolink cli …`` 的参数改为在进入 argparse 之前原样截取。
    """
    for index, token in enumerate(raw):
        if token != "cli":
            continue
        if index and raw[index - 1] == "--log-level":  # 是某个选项的取值,不是子命令
            continue
        return raw[:index], raw[index + 1 :]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)

    split = _split_cli_token(raw)
    if split is not None:
        head, tail = split
        globals_args, unknown = _build_global_parser().parse_known_args(head)
        if unknown:
            print(f"未知参数: {' '.join(unknown)}", file=sys.stderr)
            return 2
        setup_logging(globals_args.log_level)
        from echolink.client import cli as cli_module

        if not tail:
            cli_module.build_parser().print_help()
            return 0
        return cli_module.main(tail)

    parser = build_parser()
    args = parser.parse_args(raw)
    setup_logging(getattr(args, "log_level", "INFO"))
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
