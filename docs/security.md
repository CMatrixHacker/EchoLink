# 安全模型

先说实话:EchoLink 是一个**远程控制**工具,它做的事和远控木马一模一样——区别只在于
"谁持有密钥、能碰哪些目录"。所以设计上把每个可能失控的点都拧紧了,但**你仍然必须保管好密钥**。

## 威胁模型

| 威胁 | 是否防住 | 机制 |
| --- | --- | --- |
| 局域网内其他人连上来乱操作 | ✅ | 预共享密钥 + HMAC 挑战应答 |
| 抓到一次通信后重放 | ✅ | 每次连接 32 字节随机 nonce,旧应答失效 |
| 通信内容被旁听 | ⚠️ 部分 | 只保证**密钥不过线**;正文不加密(见下文) |
| 用 `..` / 符号链接读授权目录外的文件 | ✅ | realpath + commonpath 沙箱 |
| 用脚本直连绕过界面"手滑关机" | ✅ | 危险命令强制 `confirm: true` |
| 恶意程序占用端口造成资源耗尽 | ✅ | 会话上限、帧上限、握手超时 |
| 密钥泄露 | ❌ | 只能轮换密钥(见"密钥轮换") |
| 有人物理接触电脑并改配置 | ❌ | 超出软件边界 |

## 认证

```
mac = HMAC-SHA256(secret, nonce + ":" + client_id)
```

- 密钥是 32 字节随机数的十六进制串(`echolink keygen` 生成,64 个字符)。
- 密钥**从不上线**:链路上只有 nonce 和 mac。
- 校验用 `hmac.compare_digest`(恒定时间),不给时序侧信道。
- `client_id` 参与计算,改了身份 mac 就对不上。
- 握手失败立刻断开并记审计;连续失败不会锁账号(没有账号概念),但会占满日志,值得关注。

自定义口令也可以(≥16 字节),但不推荐——人选的字符串熵远低于 32 字节随机数。

## 授权:三道闸

**第一道,目录沙箱。** 所有路径:

```
expanduser/expandvars → abspath → realpath → 必须在 roots 之内
```

`realpath` 会解析符号链接,所以 `ln -s /etc secret` 这种把戏绕不过去。
比较用 `commonpath` 而不是 `startswith`,否则 `D:/data2` 会被误判成 `D:/data` 的子目录。
Windows 额外做大小写归一。

越界统一返回 `forbidden`,并且**不区分"不存在"与"不允许"**——不泄露文件是否存在。

**第二道,权限开关。** `server.json` 的 `permissions` 按类别整体关闭:

```json
"permissions": {"write": false, "delete": false, "power": false, "kill_process": false}
```

关掉 `power` 后,关机、重启、注销、睡眠**在命令层就被拒**,不是靠界面隐藏按钮。

**第三道,危险操作确认。** 这类命令带 `danger=True`,请求必须显式带 `confirm: true`:

```json
{"type":"req","cmd":"system.shutdown","args":{"delay":60,"confirm":true}}
```

少了就返回 `confirm_required`。这是协议层约束,CLI/GUI/App 都只是如实遵循。

## 传输加密:目前没有,请按需搭配

**EchoLink 目前不做端到端加密**,只做认证。含义:

- 蓝牙链路:配对本身是加密的(系统层),旁听难度较高;
- 局域网 TCP:JSON 正文是明文,同网段的抓包工具能看到你在传什么文件名、什么内容。

如果你的环境不可信,推荐:

1. 只在**可信局域网**用,或
2. 服务端 `"host": "127.0.0.1"` 配合 SSH 隧道:`ssh -L 8765:127.0.0.1:8765 你的电脑`,客户端连 `127.0.0.1:8765`;
3. 或走已有的 VPN(WireGuard / Tailscale),把链路加密交给成熟方案。

不自己做 TLS 的原因:自签证书要在手机端做信任配置(体验差),而且很容易做成"看起来安全实际
可降级"的半吊子。与其如此,不如把边界说清楚。

## 审计

每条命令一行 JSONL,包含时间、客户端标识、链路、命令、成败、耗时、参数摘要:

```json
{"ts":"2026-11-07T16:14:26","client":"echolink-cli-39266","transport":"tcp://127.0.0.1:52148",
 "cmd":"files.list","ok":true,"ms":3.98,"args":{"path":"","show_hidden":false}}
```

- 密钥从不写入审计。
- 追加写入(append-only),进程被强杀不破坏已有记录。
- 想关掉:`"audit_file": ""`。但强烈建议留着——出事时它是唯一的证据。

## 密钥轮换

怀疑泄露时:

```bash
python -m echolink keygen --force        # 生成新密钥(~/.echolink/secret)
# 或直接在 server.json 里改 secret
```

改完重启服务,所有客户端都要填新密钥。旧密钥立刻失效——因为服务端只用配置里那一个。

## 加固清单

- [ ] `roots` 只放真正需要共享的目录,别直接写 `C:/`
- [ ] 不传文件就关掉 `write` / `delete`
- [ ] 不需要远程关机就关掉 `power`
- [ ] `secret` 用 `keygen` 生成,不要自己编
- [ ] `server.json` 权限 0600(程序已自动设置)
- [ ] Windows 防火墙里只对**专用网络**放行 8765
- [ ] 定期看 `logs/audit.jsonl`,`forbidden` 和 `auth_failed` 扎堆就有问题
- [ ] 不用时停掉服务(`Ctrl+C`),不要长期挂着
- [ ] 不要把 `server.json`、密钥文件提交进 Git(仓库 `.gitignore` 已排除 `*secret*`、`server.local.json`、`logs/`)
