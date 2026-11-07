# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置:一次构建两个单文件 exe。

    echolink.exe         控制台版 —— server / cli / keygen / ports 全部子命令
    EchoLink-GUI.exe     窗口版   —— 双击即开的图形控制台

构建:
    python -m PyInstaller --clean --noconfirm packaging/echolink.spec

注意必须用**带 tkinter 的解释器**构建,否则窗口版会缺 tkinter。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))  # noqa: F821
ENTRY_DIR = SPECPATH  # noqa: F821
ICON = os.path.join(ROOT, "packaging", "echolink.ico")

# echolink 内部大量使用"延迟导入"(pycaw / pyserial / 平台后端),
# 静态分析看不到,必须显式声明,否则打出来的 exe 一用声音就崩。
HIDDEN = collect_submodules("echolink") + [
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
    "PIL",
    "setuptools",
    "pip",
]

ICON_OPTS = {"icon": ICON} if os.path.exists(ICON) else {}


# ==================== 控制台版 ====================
cli_analysis = Analysis(  # noqa: F821
    [os.path.join(ENTRY_DIR, "entry_cli.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
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


# ==================== 窗口版 ====================
gui_analysis = Analysis(  # noqa: F821
    [os.path.join(ENTRY_DIR, "entry_gui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[item for item in EXCLUDES if item != "tkinter"],
    noarchive=False,
)

gui_pyz = PYZ(gui_analysis.pure)  # noqa: F821

gui_exe = EXE(  # noqa: F821
    gui_pyz,
    gui_analysis.scripts,
    gui_analysis.binaries,
    gui_analysis.datas,
    [],
    name="EchoLink-GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    **ICON_OPTS,
)
