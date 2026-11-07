"""桌面交互类命令:剪贴板、打开目标、桌面通知、媒体按键。"""

from __future__ import annotations

from typing import Any, Dict

from echolink.server.dispatch import RequestContext, command


@command("desktop.clipboard_get", summary="读取电脑剪贴板")
def clipboard_get(ctx: RequestContext) -> Dict[str, Any]:
    text = ctx.backend.clipboard_get()
    return {"text": text, "length": len(text)}


@command(
    "desktop.clipboard_set",
    summary="写入电脑剪贴板",
    params={"text": "要写入的内容"},
)
def clipboard_set(ctx: RequestContext) -> Dict[str, Any]:
    text = ctx.param_str("text", "", max_length=1024 * 1024)
    result = ctx.backend.clipboard_set(text)
    return {"written": True, **result}


@command("desktop.open", summary="用默认程序打开路径或网址", params={"target": "文件路径或 URL"})
def desktop_open(ctx: RequestContext) -> Dict[str, Any]:
    target = ctx.param_str("target", required=True, max_length=2048)
    return ctx.backend.open_target(target)


@command(
    "desktop.notify",
    summary="在电脑上弹出通知",
    params={"title": "标题", "message": "正文"},
)
def desktop_notify(ctx: RequestContext) -> Dict[str, Any]:
    title = ctx.param_str("title", "EchoLink", max_length=128)
    message = ctx.param_str("message", "", max_length=2048)
    return ctx.backend.notify(title, message)


@command(
    "desktop.media",
    summary="媒体播放控制",
    params={"action": "play_pause | next | prev | stop"},
)
def desktop_media(ctx: RequestContext) -> Dict[str, Any]:
    action = ctx.param_str("action", "play_pause", max_length=16).lower()
    return ctx.backend.media(action)
