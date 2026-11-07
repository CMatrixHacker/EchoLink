# EchoLink

以**电脑为中心**的多设备互联工具:电脑跑服务端,手机(Android)和另一台电脑(Windows)作为客户端,
通过**蓝牙**或**局域网**接入,远程浏览/上传/下载文件,并控制系统能力——静音、调音量、锁屏、关机、重启、截屏、剪贴板、进程。

- 纯 Python 实现,协议与传输层解耦:同一套会话逻辑可以跑在 TCP 上,也可以跑在蓝牙串口/RFCOMM 上。
- 三种客户端:**命令行**(脚本友好)、**Windows 桌面控制台**(Tkinter)、**Android App**(Kivy + buildozer)。
- 37 条命令、预共享密钥挑战应答认证、目录沙箱、全量审计日志。

```
                 ┌──────────────────────────────┐
   手机 Android  │                              │  Windows 客户端(另一台电脑)
   (Kivy App)    │       电脑端服务进程          │   (Tkinter / CLI)
        │        │  ┌────────────────────────┐  │        │
        │ 蓝牙   │  │ 会话管理 → 命令注册表   │  │  局域网 │
        └───────►│  │   ↓                    │  │◄───────┘
                 │  │ 平台后端(Win32 API)    │  │
        ┌───────►│  │ 音量/电源/截屏/进程/…   │  │
        │ 蓝牙   │  └────────────────────────┘  │
        │        │        文件沙箱 + 审计日志     │
   另一台手机    └──────────────────────────────┘
```

## 快速开始

### 1. 电脑端(被控端)

```bash
pip install -e .                 # 必需
pip install pycaw pyserial       # 可选:精确音量控制 / 蓝牙串口(Windows 强烈建议)

python -m echolink server --init          # 生成 server.json,打印配对密钥
python -m echolink server                 # 启动服务
```

启动后会打印局域网地址与配对密钥,手机端照此填写即可。

> `pycaw` 缺失时静音/音量会退化为"发送系统音量键"(只能切换,读不到具体数值)。

### 2. 命令行客户端(任意设备)

```bash
pip install -e .

echolink cli -t 192.168.1.8:8765 -s <密钥> info          # 电脑概况
echolink cli -t 192.168.1.8:8765 -s <密钥> ls            # 列目录
echolink cli -t 192.168.1.8:8765 -s <密钥> get "D:/电影/a.mp4" ./a.mp4
echolink cli -t 192.168.1.8:8765 -s <密钥> put ./报告.pdf DOCS/
echolink cli -t 192.168.1.8:8765 -s <密钥> mute on
echolink cli -t 192.168.1.8:8765 -s <密钥> shutdown --delay 60
echolink cli -t 192.168.1.8:8765 -s <密钥> shell         # 交互式 shell
```

密钥也可以放进环境变量 `ECHOLINK_SECRET` 或 `~/.echolink/secret`,省去每次输入。

### 3. Windows 桌面控制台

```bash
py -3 -m echolink gui -t 192.168.1.8:8765 -s <密钥>
```

左侧浏览电脑文件、双击进入目录、右键下载;右侧一键静音/音量/截屏/锁屏/关机。

> 图形界面依赖 Python 自带的 `tkinter`。如果提示"未包含 tkinter",请改用 python.org 官方安装包的解释器。

### 4. Android App

```bash
pip install buildozer cython        # 建议在 WSL2 / Linux 中执行
buildozer -v android debug          # 产出 bin/echolink-1.0.0-debug.apk
```

详见 [docs/android.md](docs/android.md)。

## 打包成可执行文件

| 目标 | 命令 | 产物 |
| --- | --- | --- |
| Windows | `python packaging/build_windows.py` | `dist/echolink.exe`(控制台)、`dist/EchoLink-GUI.exe`(窗口),各约 15 MB 单文件 |
| Android | `bash packaging/build_android.sh`,或推到 GitHub 用内置 Actions | `bin/*.apk` |

APK 必须在 Linux/WSL2 上构建(本仓库自带 GitHub Actions 工作流,推 tag 即出包)。
完整步骤、签名、常见坑见 [docs/build.md](docs/build.md)。

## 命令一览

| 分类 | 命令 | 说明 |
| --- | --- | --- |
| 会话 | `system.ping` `system.info` `system.capabilities` | 连通性、电脑概况、能力查询 |
| 文件 | `files.roots` `files.list` `files.stat` `files.exists` `files.search` | 目录浏览与检索 |
| 文件 | `files.mkdir` `files.rename` `files.copy` `files.delete` | 增删改 |
| 文件 | `files.hash` `files.preview` | 校验值与文本预览 |
| 文件 | `files.open_read` `files.read` `files.open_write` `files.write` `files.close` `files.transfers` | 分块传输(大文件流式) |
| 系统 | `system.mute` `system.volume` `system.audio` | 音量与静音 |
| 系统 | `system.lock` `system.sleep` `system.shutdown` `system.restart` `system.logoff` `system.cancel_shutdown` | 电源控制 |
| 系统 | `system.screenshot` `system.processes` `system.kill` | 截屏与进程 |
| 桌面 | `desktop.clipboard_get` `desktop.clipboard_set` `desktop.open` `desktop.notify` `desktop.media` | 剪贴板、打开路径、通知、媒体键 |

## 配置

`server.json`(由 `--init` 生成,权限 0600):

```json
{
  "name": "我的电脑上的 EchoLink",
  "secret": "64 位十六进制密钥",
  "roots": ["D:/Users/me/Documents", "D:/Users/me/Downloads"],
  "permissions": {"write": true, "delete": true, "power": true, "kill_process": true},
  "backend": "auto",
  "audit_file": "logs/audit.jsonl",
  "tcp": {"enabled": true, "host": "0.0.0.0", "port": 8765},
  "bluetooth": {"enabled": false, "port": "", "baudrate": 115200, "auto_detect": true},
  "limits": {"max_chunk": 131072, "handshake_timeout": 30, "max_sessions": 8}
}
```

- `roots`:客户端只能在这几个目录里活动,越界请求直接拒绝(路径穿越、符号链接逃逸都覆盖)。
- `permissions`:逐项收紧权限。关掉 `power` 后关机/重启/锁屏全部拒绝。
- `tcp.port` 设为 `0` 表示自动选择空闲端口。
- 相对路径按配置文件所在目录解析。

常用命令行覆盖:`--root` `--port` `--host` `--bt [COM口]` `--no-tcp` `--backend` `--audit`。

## 文档

- [打包与分发](docs/build.md) —— Windows exe / Android APK 的完整构建指南
- [协议规范](docs/protocol.md) —— 帧格式、握手时序、错误码
- [架构说明](docs/architecture.md) —— 模块划分与线程模型
- [蓝牙配对](docs/bluetooth.md) —— Windows 与 Android 的配对步骤与排错
- [安全模型](docs/security.md) —— 威胁模型与加固建议
- [安卓打包](docs/android.md) —— buildozer 构建与常见问题

## 测试

```bash
pip install -r requirements-dev.txt
python -m pytest            # 103 项:协议 / 认证 / 沙箱 / 配置 / 传输 / 端到端
```

## 目录结构

```
echolink/
  common/     协议帧、HMAC 认证、路径沙箱、错误码
  transport/  TCP 服务端/客户端、Windows 蓝牙 SPP、Android RFCOMM
  server/     会话、命令注册表、分块传输、审计、配置、三个命令模块
  platform/   Win32(ctypes) / 通用 POSIX / 空实现后端
  client/     Python API、CLI、Tkinter GUI、Kivy 安卓界面
packaging/    PyInstaller 配置、Windows/Android 构建脚本
main.py       Android 打包入口
buildozer.spec
.github/      CI:推 tag 自动产出 exe 与 APK
tests/
```

## 许可

MIT
