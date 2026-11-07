"""基于预共享密钥的挑战-应答认证(HMAC-SHA256)。

为什么不用"直接发密码":
* 密钥永不过线,蓝牙这种可被旁听的链路上也不会泄露;
* 每次连接的 nonce 不同,重放旧应答无效。

密钥格式:32 字节随机数的十六进制串(64 个字符),由 ``echolink keygen`` 生成。
"""

from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256
from typing import Optional

ALGORITHM = "HMAC-SHA256"
MIN_SECRET_BYTES = 16


class SecretError(ValueError):
    """密钥缺失或格式非法。"""


def new_nonce(nbytes: int = 32) -> str:
    """生成一次性的挑战串(十六进制)。"""
    return secrets.token_hex(nbytes)


def generate_secret() -> str:
    """生成 32 字节强随机密钥(hex 形式)。"""
    return secrets.token_hex(32)


def parse_secret(value: str) -> bytes:
    """把用户输入的密钥解析成字节。

    支持两种形式:64 位十六进制串(推荐),或任意长度 >= 16 的明文口令。
    """
    if value is None:
        raise SecretError("缺少密钥")
    text = str(value).strip()
    if not text:
        raise SecretError("密钥不能为空")
    if len(text) == 64:
        try:
            raw = bytes.fromhex(text)
        except ValueError:
            raw = text.encode("utf-8")
        else:
            if len(raw) >= MIN_SECRET_BYTES:
                return raw
    raw = text.encode("utf-8")
    if len(raw) < MIN_SECRET_BYTES:
        raise SecretError(f"密钥至少需要 {MIN_SECRET_BYTES} 字节(当前 {len(raw)})")
    return raw


def compute_mac(secret: bytes, nonce: str, client_id: str) -> str:
    """计算应答值:HMAC(secret, nonce:client_id)。"""
    if not secret:
        raise SecretError("密钥不能为空")
    material = f"{nonce}:{client_id}".encode("utf-8")
    return hmac.new(secret, material, sha256).hexdigest()


def verify_mac(secret: bytes, nonce: str, client_id: str, mac: str) -> bool:
    """恒定时间校验应答值。"""
    if not mac or not isinstance(mac, str):
        return False
    expected = compute_mac(secret, nonce, client_id)
    return hmac.compare_digest(expected, mac.strip().lower())


def read_secret_file(path: str) -> str:
    """读取密钥文件(纯文本或 KEY=VALUE 形式)。"""
    if not os.path.exists(path):
        raise SecretError(f"密钥文件不存在: {path}")
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                line = line.split("=", 1)[1].strip()
            if line:
                return line
    raise SecretError(f"密钥文件为空: {path}")


def write_secret_file(path: str, secret: Optional[str] = None, exclusive: bool = True) -> str:
    """把密钥写入文件(默认不允许覆盖),返回密钥。"""
    value = secret or generate_secret()
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    mode = "x" if exclusive else "w"
    try:
        with open(path, mode, encoding="utf-8", newline="\n") as handle:
            handle.write(value + "\n")
    except FileExistsError as exc:
        raise SecretError(f"密钥文件已存在: {path}(如需覆盖请显式允许)") from exc
    try:  # POSIX 下收紧权限;Windows 依赖目录 ACL
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass
    return value
