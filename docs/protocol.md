# EchoLink 线协议 v1

协议只假设底层能提供两个操作:**按字节可靠收发**、**保持顺序**。因此同一套协议可以跑在 TCP 上,
也可以跑在蓝牙 SPP 串口或 Android RFCOMM 上,不需要任何额外封装。

```
┌──────────────┬───────────────────────────────┐
│ 4 字节大端长度 │  UTF-8 JSON 对象(长度 = N)     │
└──────────────┴───────────────────────────────┘
        N            N 字节正文
```

- `N` 必须大于 0 且不超过 8 MiB(`MAX_FRAME_BYTES`),超限直接断开。
- 正文是一个 JSON 对象,必须带 `type` 字段。
- 二进制数据一律 base64 内联(`data` 字段),不另开二进制通道。代价是约 33% 带宽,
  换来的是**不会出现双通道状态机错位**——蓝牙串口这种半双工链路尤其怕错位。

## 帧类型

| `type` | 方向 | 说明 |
| --- | --- | --- |
| `hello` | C→S | 客户端自我介绍 |
| `challenge` | S→C | 服务端下发随机挑战 |
| `auth` | C→S | 客户端回传 HMAC 应答 |
| `welcome` | S→C | 握手结果(含会话 id / 服务端能力) |
| `req` | C→S | 请求 |
| `res` | S→C | 响应 |
| `evt` | S→C | 服务端主动事件(可选,当前用于长任务进度) |
| `ping` / `pong` | 双向 | 保活 |
| `bye` | 双向 | 主动断开 |

## 握手时序

```
客户端                                     服务端
  │  hello {protocol:"echolink", version:1,   │
  │         client_id, app, device, platform} │
  │ ─────────────────────────────────────────►│
  │                                           │
  │  challenge {version, alg:"HMAC-SHA256",   │
  │             nonce, server, server_version}│
  │ ◄─────────────────────────────────────────│
  │                                           │
  │  auth {client_id, mac}                    │
  │    mac = HMAC-SHA256(secret, "nonce:client_id") 的十六进制
  │ ─────────────────────────────────────────►│
  │                                           │
  │  welcome {ok:true, session, server:{...}, │
  │           commands:[...], roots:[...],    │
  │           capabilities:{...}}             │
  │ ◄─────────────────────────────────────────│
```

失败时服务端同样回 `welcome`,但 `ok=false` 并带 `error`:

```json
{"type": "welcome", "ok": false,
 "error": {"code": "auth_failed", "message": "密钥不匹配,请检查配对密钥"}}
```

要点:

- **密钥不过线**。链路上只有 nonce 与 HMAC 结果,旁听者无法反推密钥。
- **重放无效**。nonce 每次连接随机 32 字节,旧应答在新连接上校验必然失败。
- **恒定时间比较**。`hmac.compare_digest`,避免时序侧信道。
- `client_id` 参与 MAC 计算,防止串改身份后复用他人应答。
- 握手有 `handshake_timeout`(默认 30 秒),超时即断开,防止空连接堆积。

## 请求与响应

```json
{"type": "req", "id": 17, "cmd": "files.list", "args": {"path": "Documents"}}
```

```json
{"type": "res", "id": 17, "ok": true, "data": {"path": "Documents", "entries": [...]}}
```

```json
{"type": "res", "id": 17, "ok": false,
 "error": {"code": "forbidden", "message": "路径不在授权范围内: C:\\Windows"}}
```

- `id` 由客户端生成,单调递增;响应必须带回同一个 `id`。
- 一个连接上同一时刻只处理一个请求(串行),但客户端可以流水线发送,
  服务端按到达顺序处理——这让状态型命令(如分块写)天然有序。
- `args` 省略等价于 `{}`。

## 错误码

| code | 含义 | 客户端应对 |
| --- | --- | --- |
| `bad_request` | 参数缺失/类型不对 | 修参数,不要重试 |
| `not_found` | 路径或对象不存在 | 刷新后重试 |
| `forbidden` | 越出沙箱 / 权限被配置关闭 | 不要重试 |
| `conflict` | 目标已存在、句柄状态冲突 | 询问用户是否覆盖 |
| `auth_failed` | 密钥错误 | 提示重新配对 |
| `confirm_required` | 危险操作缺少 `confirm:true` | 二次确认后再发 |
| `unsupported` | 当前平台不具备该能力 | 隐藏对应按钮 |
| `busy` | 会话数超限 | 退避重连 |
| `too_large` | 超过帧/截图/上传上限 | 分块或拒绝 |
| `timeout` | 操作超时 | 可重试 |
| `unknown_command` | 命令不存在 | 版本不匹配,提示升级 |
| `internal` | 服务端内部错误 | 可重试一次,再失败报 bug |

## 危险操作需要显式确认

关机、重启、注销、睡眠、结束进程等命令带 `danger=True`,请求里必须出现 `confirm: true`,
否则返回 `confirm_required`:

```json
{"type":"req","id":9,"cmd":"system.shutdown","args":{"delay":60,"confirm":true}}
```

这是**协议层**的强制约束,不是 UI 约定——即使有人用脚本直连,也无法"手滑"关掉电脑。

## 分块传输

大文件不整包塞进一个帧,而是走"打开句柄 → 循环读写 → 关闭"三步。

下载:

```
files.open_read {path}                    → {handle, size, name, mtime}
files.read      {handle, offset, length}  → {data(base64), offset, length, eof}
…重复直到 eof=true…
files.close     {handle}
```

上传:

```
files.open_write {path, overwrite}        → {handle}
files.write      {handle, offset, data}   → {written, offset}
…重复…
files.close      {handle, commit}         → {path, size}   # commit=false 丢弃
```

- 每块默认 128 KiB,可用 `limits.max_chunk` 调整(上限 256 KiB)。
- 客户端可以乱序/重发某一块,服务端按 `offset` 定位。
- 上传没有 `commit=true` 就关闭,服务端会删掉临时文件——中断不会留下半截垃圾。
- `files.transfers` 可查看当前打开的句柄,便于排障。

## 保活与断开

- `ping`/`pong` 是应用层心跳,`{"type":"ping","id":n}` → `{"type":"pong","id":n, "time":...}`。
- 任意一端发 `bye` 即表示优雅关闭;接收方回 `bye` 后关闭连接。
- TCP 上还有 `idle_timeout`(默认 0 = 不超时),适合长期挂着的场景。

## 版本兼容

- `version` 是整数,当前为 `1`。不匹配时服务端回 `unsupported` 并断开,
  不做"尽力兼容"——协议改动一律升版本,避免半懂不懂的客户端造成数据损坏。
- `hello.protocol` 字段用于未来多协议共存,当前必须为 `"echolink"` 或省略。
