# 打包 Android App

## 一句话结论

Windows 上不能直接打包。用 **WSL2 里的 Ubuntu** 或任意 Linux 机器执行 `buildozer android debug`,
大约 20~40 分钟(首次要下 Android SDK/NDK,几个 GB),产出 `bin/echolink-1.0.0-debug.apk`。

本仓库**不含预编译 APK**——APK 与构建机、签名密钥绑定,应当由你自己构建。

## 前置条件

- Linux 或 WSL2(Ubuntu 22.04+)。WSL2 需要在 Windows 里开启虚拟化,`wsl --install -d Ubuntu`。
- 磁盘 ≥ 15 GB(SDK + NDK + 中间产物)。
- 内存 ≥ 4 GB(buildozer 编译 C 扩展时会吃内存)。
- JDK 17:

  ```bash
  sudo apt update
  sudo apt install -y openjdk-17-jdk zip unzip git python3-pip \
      autoconf automake libtool pkg-config zlib1g-dev \
      libncurses5-dev libncursesw5-dev libtinfo6 cmake libffi-dev libssl-dev
  ```

## 构建步骤

```bash
# 1. 取代码(WSL 里访问 Windows 盘会慢十倍,建议放在 WSL 自己的文件系统)
git clone <你的仓库地址> ~/EchoSpace
cd ~/EchoSpace

# 2. 装 buildozer
python3 -m pip install --user buildozer cython==0.29.36
export PATH="$HOME/.local/bin:$PATH"

# 3. 开始打包(首次会询问是否同意 SDK 许可)
buildozer -v android debug
```

产物:

```
bin/echolink-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

传到手机安装(`adb install` 或直接拷过去点开)。

## buildozer.spec 里几个关键项

| 配置 | 值 | 说明 |
| --- | --- | --- |
| `requirements` | `python3,kivy==2.3.0,pyjnius,android` | Kivy 提供界面,pyjnius 提供 RFCOMM |
| `android.permissions` | 含 `BLUETOOTH_CONNECT`/`BLUETOOTH_SCAN` | Android 12+ 必须声明,否则连不上已配对设备 |
| `android.api` | 33 | 目标 API 级别 |
| `android.minapi` | 23 | Android 6.0 起可装 |
| `android.archs` | `arm64-v8a,armeabi-v7a` | 覆盖绝大多数手机 |
| `source.exclude_dirs` | `tests,docs,.git,...` | 不把测试和文档塞进 APK |
| `source.exclude_patterns` | `*secret*`, `server*.json` | **绝不把密钥/配置打进安装包** |

`main.py` 在仓库根目录,只是转向 `echolink.client.android.main.run()`,这是 p4a 的硬性约定。

## 首次运行要做的事

1. App 启动时会申请**存储**与**蓝牙**权限,全部允许。
2. 系统设置里先和电脑完成蓝牙配对。
3. 回到 App,点"蓝牙配对设备"选你的电脑(自动填 `bt:MAC`),或手填 `192.168.1.8:8765`。
4. 填入电脑端打印的配对密钥。密钥可以点"从文件导入密钥"选一个文本文件。
5. 连接成功后进入主界面:文件浏览、下载/上传、音量、静音、锁屏、关机、截屏、剪贴板、进程。

配置会存到 App 私有目录,下次打开自动填好。

## 常见问题

| 问题 | 原因 | 处理 |
| --- | --- | --- |
| `buildozer: command not found` | `~/.local/bin` 不在 PATH | `export PATH="$HOME/.local/bin:$PATH"` |
| 卡在 `Downloading NDK` | 网络问题 | 挂代理,或手动把 SDK/NDK 放进 `~/.buildozer/` |
| `javac: invalid source release` | JDK 版本不对 | 装 JDK 17,`update-alternatives --config javac` |
| 编译到一半内存被杀 | WSL 内存上限 | 在 `%UserProfile%\.wslconfig` 里调 `memory=8GB` |
| 装到手机提示"解析包错误" | 架构不匹配 | 确认 `android.archs` 含 `arm64-v8a` |
| 连接一直转圈 | 手机没给蓝牙权限 | 设置 → 应用 → EchoLink → 权限 → 蓝牙 |
| 中文显示成方块 | 没打包中文字体 | 把一个 `.ttf` 放进 `assets/fonts/`,在 `Label(font_name=...)` 指定 |

## 只想要个能用的客户端?

不一定要 APK:安卓上装 **Termux**,`pip install -e .`,`echolink cli -t ...` 一样能连——
命令行版本覆盖全部 37 条命令,功能比 App 更全。
