# 打包成可执行文件

| 目标 | 工具 | 产物 | 能否在 Windows 上直接做 |
| --- | --- | --- | --- |
| Windows 可执行文件 | PyInstaller | `dist/echolink.exe`、`dist/EchoLink-GUI.exe`(各约 15 MB) | ✅ 可以,本仓库已实测通过 |
| Android APK | buildozer + python-for-android | `bin/echolink-1.0.0-…-debug.apk` | ❌ 需要 Linux / WSL2 / GitHub Actions |

---

## 一、Windows:PyInstaller

### 一键构建

```bash
python packaging/build_windows.py
```

脚本会自动:

1. 挑一个**带 tkinter** 的解释器(当前这个不行就从 `py -0p` 列表里找);
2. 检查 PyInstaller 是否安装;
3. 按 `packaging/echolink.spec` 构建两个 exe;
4. 打印体积并跑一次 `--version` 冒烟。

只检查环境不构建:

```bash
python packaging/build_windows.py --check
```

### 手动构建

```bash
pip install -r packaging/requirements-build.txt
python -m PyInstaller --clean --noconfirm packaging/echolink.spec
```

### 产物

```
dist/
├── echolink.exe        15 MB   控制台版:server / cli / keygen / ports
└── EchoLink-GUI.exe    15 MB   窗口版:双击即开的图形控制台
```

两个都是**单文件**(onefile),拷到任何一台 Windows 10/11 x64 上都能直接跑,不需要装 Python。

```powershell
.\dist\echolink.exe server                 # 电脑端服务
.\dist\echolink.exe cli -t 192.168.1.8:8765 -s <密钥> info
.\dist\EchoLink-GUI.exe                    # 图形控制台
```

### 两个必须知道的坑

**1. 构建必须用带 tkinter 的解释器。**
PyInstaller 只能打包"当前解释器里有的东西"。用精简版/嵌入式 Python 构建,`EchoLink-GUI.exe`
启动就会报缺少 tkinter(程序里放了兜底提示弹窗,不会闪退无提示)。
本机验证:`D:\Soft\Python\Python313\python.exe` 可用,WorkBuddy 自带的 3.13.12 不含 tkinter。

**2. 隐藏导入必须显式声明。**
`pycaw` 和 `pyserial` 都是在函数内部延迟导入的,静态分析看不到;不写进 `hiddenimports`,
打出来的 exe 一调音量就会 `ModuleNotFoundError`。`packaging/echolink.spec` 里已经处理,
另外用 `collect_submodules("echolink") + collect_submodules("comtypes")` 兜住动态导入。

### 为什么分两个 exe

窗口版如果带控制台,双击后会弹一个黑框;控制台版如果无控制台,`server` 的日志就看不见了。
所以拆成两个,各自合适。

窗口版入口做了两件事:`sys.stdout/stderr` 为 `None` 时指向 devnull(否则任何 `print` 都崩),
崩溃时用 `MessageBoxW` 弹窗而不是静默退出。

### 分发与杀软

- PyInstaller 产出的 exe **没有签名**,Windows Defender / 360 有一定概率误报。
  自用没问题;要分发给别人建议买个代码签名证书,或改用 onedir + 安装包。
- onefile 每次启动会把内容解压到 `%TEMP%\_MEIxxxx`,首次启动约 1~2 秒。
  嫌慢就把 spec 里的 `EXE(...)` 改成 `COLLECT(...)` 模式(onedir),启动快但产物是一个文件夹。
- 单文件 exe 里**不含**任何密钥;`server.json` 和 `~/.echolink/` 在用户机器上运行时才生成。

---

## 二、Android:buildozer

Android 的"可执行文件"就是 APK,它必须由 `python-for-android` 交叉编译出 arm 原生库,
**只能在 Linux 上做**(Windows 不行,Windows 上的 Docker 也不行)。

三种可选路径:

### 路径 A:GitHub Actions(最省事,推荐)

仓库里已经准备好 `.github/workflows/build.yml`。推到 GitHub 后:

1. 打一个 tag 触发:

   ```bash
   git tag v1.0.0 && git push origin v1.0.0
   ```

   或在仓库 Actions 页面点 **Run workflow** 手动触发。
2. 等 20~40 分钟(首次要下 SDK/NDK,缓存命中后约 10 分钟)。
3. 在 run 页面底部下载 `echolink-android` artifact,里面就是 APK。

本地什么都不用装。

### 路径 B:WSL2 / Ubuntu

```bash
# 1. 装系统依赖
sudo apt update
sudo apt install -y git zip unzip openjdk-17-jdk python3-pip \
    autoconf libtool pkg-config zlib1g-dev \
    libncurses5-dev libncursesw5-dev libtinfo6 cmake libffi-dev libssl-dev

# 2. 装 buildozer
python3 -m pip install --user buildozer cython==0.29.36
export PATH="$HOME/.local/bin:$PATH"

# 3. 构建(仓库里的脚本已封装上面两步的检查)
cd /mnt/d/Project/EchoSpace
bash packaging/build_android.sh
```

> WSL 里访问 `/mnt/d` 会慢很多,构建建议先把仓库 clone 到 WSL 自己的文件系统(`~/EchoSpace`)。

### 路径 C:真机免打包

不想打包就在手机上装 **Termux**:

```bash
pkg install python git
git clone <仓库> && cd EchoSpace && pip install -e .
echolink cli -t 192.168.1.8:8765 -s <密钥> info
```

命令行版覆盖全部 37 条命令,功能比 App 还全,只是没有图形界面。

### 安装 APK

```bash
adb install -r bin/echolink-1.0.0-arm64-v8a_armeabi-v7a-debug.apk
```

或者把 APK 拷到手机点开安装(需要允许"安装未知来源应用")。

debug 包可以直接装;release 包必须先签名:

```bash
keytool -genkey -v -keystore echolink.keystore -alias echolink \
        -keyalg RSA -keysize 2048 -validity 10000
# 在 buildozer.spec 里填 [app] android.release_artifact 与签名信息
buildozer -v android release
```

---

## 三、改版本号

三处要同步,否则 exe 显示 1.0.0 而 APK 是 1.0.1:

| 文件 | 字段 |
| --- | --- |
| `echolink/__init__.py` | `__version__`(运行时显示、握手时上报) |
| `pyproject.toml` | `version`(pip 包版本) |
| `buildozer.spec` | `version`(APK 版本名) |

---

## 四、常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `EchoLink-GUI.exe` 提示缺少 tkinter | 用不带 tkinter 的 Python 构建 | 换解释器重新构建,或用 `--python` 指定 |
| exe 里调音量报 `ModuleNotFoundError: pycaw` | spec 的 hiddenimports 被改少了 | 恢复 `packaging/echolink.spec` 里的 HIDDEN 列表 |
| exe 启动慢 1~2 秒 | onefile 要解压 | 改 onedir,或接受它 |
| 中文在管道里乱码 | Windows 重定向默认用 cp936 | 已在 `configure_stdio()` 里处理:非 tty 时强制 UTF-8 |
| `buildozer: command not found` | `~/.local/bin` 不在 PATH | `export PATH="$HOME/.local/bin:$PATH"` |
| buildozer 卡在下载 NDK | 网络 | 挂代理,或把 SDK/NDK 手动放进 `~/.buildozer/` |
| APK 装不上,提示解析包错误 | 架构不匹配 | 确认 `android.archs` 含 `arm64-v8a` |
| GitHub Actions 里 APK 构建失败 | 缓存/SDK 许可 | 看日志;`buildozer.spec` 已设 `android.accept_sdk_license = True` |

---

## 五、产物清单(实测)

本机 Windows 构建结果:

```
-rwxr-xr-x  15M  dist/echolink.exe
-rwxr-xr-x  15M  dist/EchoLink-GUI.exe
```

验证过:exe 服务端 + exe 客户端跑通 ping/文件/截图/音量(frozen 状态下 pycaw 正常),
窗口版能起窗口(标题「EchoLink 控制台」)。APK 因本机 WSL 被安全策略禁用,未在本机构建,
请走路径 A 或 B。
