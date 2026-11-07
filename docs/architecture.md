# 架构说明

## 分层

```
┌─────────────────────────────────────────────────────────────┐
│ client/    core.py(Python API)                              │
│            cli.py(命令行 + 交互 shell)                       │
│            gui.py(Tkinter)  android/(Kivy,打包进 APK)         │
└───────────────────────────┬─────────────────────────────────┘
                            │  只用 core.EchoLinkClient
┌───────────────────────────▼─────────────────────────────────┐
│ common/    protocol.py(帧编解码)  auth.py(HMAC)              │
│            paths.py(沙箱)  errors.py(错误码)                 │
└───────────────────────────┬─────────────────────────────────┘
                            │  只依赖 Transport 抽象
┌───────────────────────────▼─────────────────────────────────┐
│ transport/ base.py(抽象)  tcp.py  serial_bt.py  android_bt.py│
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ server/    app.py(服务主循环)  session.py(会话/握手)          │
│            dispatch.py(注册表/权限)  transfers.py(分块句柄)   │
│            audit.py(审计)  config.py(配置)  handlers/*(命令)  │
└───────────────────────────┬─────────────────────────────────┘
                            │
┌───────────────────────────▼─────────────────────────────────┐
│ platform/  windows.py(ctypes→Win32)  generic.py(POSIX)       │
│            base.py(能力接口,不支持就抛 Unsupported)          │
└─────────────────────────────────────────────────────────────┘
```

依赖只向下,不向上:平台后端不知道协议,传输层不知道命令,客户端不碰 Win32。

## 为什么传输层要抽象成"字节流"

蓝牙 SPP 串口没有连接/监听的概念,只有"打开一个 COM 口然后读写";RFCOMM socket 有连接但
没有消息边界。把差异压到 `recv_exact(n)` / `send_all(bytes)` 两个方法后,上面的会话逻辑
**完全不区分** TCP 还是蓝牙——这正是需求里"以电脑为中心、用蓝牙连各设备"能落地的前提。

| 实现 | 场景 | 连接方式 |
| --- | --- | --- |
| `TcpServer` / `TcpClient` | 同一局域网,速度最快 | 监听端口 / 主动 connect |
| `SerialTransport` | Windows 蓝牙 SPP(已配对后的虚拟 COM 口) | 打开 COM 口 |
| `AndroidBluetoothTransport` | 手机端直连电脑的蓝牙 | `BluetoothSocket.connect()` via pyjnius |

## 线程模型

服务端是"一连接一线程"的经典模型,不做异步框架依赖(蓝牙串口是阻塞 API,异步化收益低):

```
主线程            accept 线程            会话线程 × N          蓝牙线程 × 1
  │                  │                      │                    │
  │ start()          │ accept()             │                    │
  ├─────────────────►│──┬──────────────────►│ 握手                │
  │                  │  │                   │ 请求循环            │
  │                  │  │                   │ 命令执行            │
  │                  │  │                   │ (平台 API 可能慢)    │
  │◄─────────────────┴──┴───────────────────┴────────────────────┤
  │ stop(): 置标志 → 关闭监听 → 逐会话退出 → join
```

- **会话之间完全隔离**:一个会话阻塞在 `system.screenshot` 不会影响其他会话。
- **单会话串行**:同一条连接上的请求按到达顺序处理,状态型命令(分块写)天然有序。
- **会话数上限** `limits.max_sessions`,超限直接在握手前回 `busy`,避免线程被耗尽。
- 客户端侧所有网络调用在调用线程同步执行;GUI/Android 各自用后台线程 + 主线程回调刷新界面。

## 请求处理链路

```
Session._loop
  → channel.recv()                 取出 req
  → registry.resolve(cmd)          查命令;不存在 → unknown_command
  → ensure_allowed(meta, config)   检查 permissions(write/delete/power/kill)
  → ctx 构造                        参数解析 + 沙箱 + 传输句柄表
  → handler(ctx)                    执行
  → auditor.record(...)            无论成败都写一行审计
  → channel.send(res)
```

命令用装饰器声明元数据,注册表在导入时自动收集:

```python
@command("system.shutdown", summary="关机(需确认)", danger=True,
         requires=("power",), params={"delay": "延迟秒数", "confirm": "必须为 true"})
def system_shutdown(ctx):
    ctx.require_confirm("关闭电脑")
    return ctx.backend.shutdown_power(restart=False, delay=..., force=...)
```

好处:`system.capabilities` 直接把这张表回给客户端,客户端据此决定显示哪些按钮,
**新增命令不需要改客户端**(CLI 的 `caps` 就会自动列出来)。

## 路径沙箱

所有路径先经过 `common/paths.py`:

1. 展开 `~` 与环境变量,转成绝对路径;
2. `os.path.realpath` 解析 `..` 与符号链接(防止用软链逃逸);
3. 必须落在某个 `roots` 之下(用 `commonpath` 比较,避免 `D:/data` 与 `D:/data2` 前缀误判);
4. Windows 上额外做大小写归一 —— `C:\Windows` 与 `c:\windows` 视为同一路径。

越界即 `forbidden`,连 `stat` 都不给——不泄露"存在但你不能看"的信息。

## 分块传输设计

`server/transfers.py` 维护"会话级句柄表":

- 下载句柄持有文件对象,`read_chunk(offset)` 支持绝对偏移读取(可重传);
- 上传句柄先写临时文件,`close(commit=True)` 才 `os.replace` 到目标;
  `commit=False` 或会话异常退出则删除临时文件;
- 会话结束时 `close_all(commit=False)` 兜底,不留垃圾。

为什么不用一次性大帧?因为 `MAX_FRAME_BYTES` 是 8 MiB,而且蓝牙串口的吞吐只有几十 KB/s,
必须能"断点续传式"重试某一块。

## 平台后端

`platform/base.py` 定义能力接口,不支持的实现直接抛 `Unsupported`,由 `capabilities()`
提前告诉客户端"这台机器能不能做"。这样 CLI/GUI/App 都不需要 `if sys.platform == ...`。

- `windows.py`:全部走 `ctypes` 直连 `user32/gdi32/kernel32/powrprof`,
  **不依赖 pywin32**。音量走 COM(Core Audio,`pycaw`),缺失时退化为发系统音量键。
  截屏用 GDI `BitBlt` + 自写 PNG 编码器,连 Pillow 都不需要。
- `generic.py`:Linux/macOS 的降级实现,能做的做,不能做的报 `unsupported`。

## 审计

`server/audit.py` 每执行一条命令写一行 JSONL:

```json
{"ts":"2026-11-07T16:14:26","client":"echolink-cli-39266","transport":"tcp://127.0.0.1:52148",
 "cmd":"files.list","ok":true,"ms":3.98,"args":{"path":"","show_hidden":false}}
```

写入用追加模式 + 行缓冲,即使进程被强杀也不会破坏已有记录。密钥内容永不落盘到审计。
