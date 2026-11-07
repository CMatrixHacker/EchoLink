# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

    echolink.exe         控制台版(onefile) —— server / cli / keygen / autostart 全部子命令
    EchoLink/            图形控制版(onedir) —— PySide6 科幻界面,启动快,便于做成产品目录

构建:
    python packaging/make_icon.py                 # 首次生成图标
    python packaging/build_windows.py             # 自动挑解释器并构建
"""

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821
ENTRY_DIR = SPECPATH  # noqa: F821
ICON = os.path.join(ROOT, "packaging", "echolink.ico")

# echolink 内部大量使用"延迟导入"(pycaw / pyserial / 平台后端),
# 静态分析看不到,必须显式声明,否则打出来的 exe 一用声音就崩。
_ALL_ECHOLINK = collect_submodules("echolink")
# 图形界面包只属于窗口版,控制台版不需要 PySide6(否则 15MB 涨到 55MB)
UI_MODULES = [m for m in _ALL_ECHOLINK if m.startswith("echolink.client.ui")]
CLI_HIDDEN = [m for m in _ALL_ECHOLINK if not m.startswith("echolink.client.ui")]

HIDDEN = CLI_HIDDEN + [
    "pycaw",
    "pycaw.pycaw",
    "pycaw.api",
    "pycaw.utils",
    "comtypes",
    "serial",
    "serial.tools",
    "serial.tools.list_ports",
]
HIDDEN += collect_submodules("comtypes")
GUI_HIDDEN = _ALL_ECHOLINK + [m for m in HIDDEN if not m.startswith("echolink")]

# 桌面版不需要 Kivy / pyjnius / Android 那套,排除掉能显著减小体积
EXCLUDES = [
    "kivy",
    "jnius",
    "android",
    "pytest",
    "_pytest",
    "numpy",
    "pandas",
    "matplotlib",
    "setuptools",
    "pip",
]
# 控制台版额外排除整套 Qt 与图形界面
CLI_EXCLUDES = EXCLUDES + ["PySide6", "shiboken6", "echolink.client.ui"]

ICON_OPTS = {"icon": ICON} if os.path.exists(ICON) else {}


# ==================== 控制台版(onefile) ====================
cli_analysis = Analysis(  # noqa: F821
    [os.path.join(ENTRY_DIR, "entry_cli.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=CLI_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=CLI_EXCLUDES,
    noarchive=False,
)

cli_pyz = PYZ(cli_analysis.pure)  # noqa: F821

cli_exe = EXE(  # noqa: F821
    cli_pyz,
    cli_analysis.scripts,
    cli_analysis.binaries,
    cli_analysis.datas,
    [],
    name="echolink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    **ICON_OPTS,
)


# ==================== 图形版(onedir,PySide6) ====================
gui_analysis = Analysis(  # noqa: F821
    [os.path.join(ENTRY_DIR, "entry_gui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=GUI_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES + ["tkinter"],
    noarchive=False,
)

gui_pyz = PYZ(gui_analysis.pure)  # noqa: F821

gui_exe = EXE(  # noqa: F821
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="EchoLink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    **ICON_OPTS,
)

gui_collect = COLLECT(  # noqa: F821
    gui_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    strip=False,
    upx=False,
    name="EchoLink",
)
