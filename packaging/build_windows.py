"""一键构建 Windows 可执行文件。

用法::

    python packaging/build_windows.py          # 构建到 dist/
    python packaging/build_windows.py --check  # 只检查环境

做的事:
1. 挑一个**带 tkinter** 的解释器(否则窗口版打出来是坏的);
2. 确认 PyInstaller 已安装;
3. 调用 PyInstaller 按 packaging/echolink.spec 产出:
       dist/echolink.exe        控制台版
       dist/EchoLink-GUI.exe    窗口版
4. 对产物做一次冒烟(--version)。

不硬编码任何 Python 安装路径:当前解释器不行时,自动从 `py -0p` 列表里找。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC = os.path.join(HERE, "echolink.spec")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build")

COMMON_PATHS = [
    r"D:\Soft\Python\Python313\python.exe",
    r"D:\Soft\Python\python.exe",
    r"C:\Python313\python.exe",
    r"C:\Python312\python.exe",
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313\python.exe"),
    os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312\python.exe"),
]


def has_tkinter(python: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-c", "import tkinter; print(tkinter.TkVersion)"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() not in ("", "0.0")


def has_pyinstaller(python: str) -> bool:
    try:
        result = subprocess.run(
            [python, "-m", "PyInstaller", "--version"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def py_launcher_candidates() -> list[str]:
    """解析 `py -0p`,拿到本机所有 Python 的可执行文件路径。"""
    launcher = shutil.which("py")
    if not launcher:
        return []
    try:
        result = subprocess.run([launcher, "-0p"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in result.stdout.splitlines():
        match = re.search(r"([A-Za-z]:\\.*python[0-9.]*\.exe)$", line.strip())
        if match:
            found.append(match.group(1))
    return found


def pick_python() -> str:
    seen: list[str] = []

    def _try(path: str | None) -> bool:
        if not path:
            return False
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen or not os.path.exists(path):
            return False
        seen.append(norm)
        return has_tkinter(path)

    if _try(sys.executable):
        return sys.executable
    for candidate in py_launcher_candidates() + COMMON_PATHS:
        if _try(candidate):
            return candidate
    raise SystemExit(
        "找不到带 tkinter 的 Python。请安装 python.org 官方版(勾选 tcl/tk),\n"
        "或以 `python packaging/build_windows.py --python <路径>` 指定解释器。"
    )


def ensure_pyinstaller(python: str) -> None:
    if has_pyinstaller(python):
        return
    raise SystemExit(
        f"该解释器未安装 PyInstaller:\n    {python} -m pip install pyinstaller\n"
        "装好后重新运行本脚本。"
    )


def build(python: str) -> int:
    os.makedirs(DIST, exist_ok=True)
    command = [
        python,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        DIST,
        "--workpath",
        WORK,
        SPEC,
    ]
    print("构建命令:", " ".join(command))
    code = subprocess.call(command, cwd=ROOT)
    if code != 0:
        return code

    print("\n产物:")
    # 控制台版 onefile: dist/echolink.exe
    # 窗口版 onedir : dist/EchoLink/EchoLink.exe(整目录才是完整程序)
    targets = [
        os.path.join(DIST, "echolink.exe"),
        os.path.join(DIST, "EchoLink", "EchoLink.exe"),
    ]
    for path in targets:
        if os.path.exists(path):
            if path.endswith("echolink.exe"):
                size = os.path.getsize(path) / (1024 * 1024)
                print(f"  {path}  ({size:.1f} MB)")
            else:
                folder = os.path.dirname(path)
                total = sum(
                    os.path.getsize(os.path.join(root, f))
                    for root, _, files in os.walk(folder)
                    for f in files
                )
                print(f"  {path}  (整目录 {total / (1024 * 1024):.1f} MB)")
        else:
            print(f"  {path}  [缺失]")
            code = 1

    cli = os.path.join(DIST, "echolink.exe")
    if os.path.exists(cli):
        print("\n冒烟测试:")
        probe = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=120)
        print("  --version ->", probe.stdout.strip() or probe.stderr.strip())
        if probe.returncode != 0:
            code = 1
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="构建 Windows 可执行文件")
    parser.add_argument("--python", default=None, help="指定用于构建的解释器")
    parser.add_argument("--check", action="store_true", help="只检查环境,不构建")
    args = parser.parse_args()

    python = args.python or pick_python()
    print(f"构建解释器: {python}")
    print(f"tkinter    : {'可用' if has_tkinter(python) else '缺失'}")
    print(f"PyInstaller: {'可用' if has_pyinstaller(python) else '缺失'}")

    if args.check:
        return 0
    ensure_pyinstaller(python)
    return build(python)


if __name__ == "__main__":
    sys.exit(main())
