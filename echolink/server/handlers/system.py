"""系统类命令:信息、音量/静音、锁屏、休眠、关机、截图、进程。"""

from __future__ import annotations

import time
from typing import Any, Dict

from echolink import __version__
from echolink.common.errors import CommandError, ErrorCode, Unsupported
from echolink.common.protocol import b64e
from echolink.server.dispatch import RequestContext, command


@command("system.info", summary="查看电脑概况(系统、内存、电量、目录、权限)")
def system_info(ctx: RequestContext) -> Dict[str, Any]:
    data = ctx.backend.info()
    data.update(
        {
            "server": ctx.config.name,
            "server_version": __version__,
            "session": ctx.session.id,
            "roots": ctx.sandbox.describe(),
            "permissions": {
                "write": ctx.config.allow_write,
                "delete": ctx.config.allow_delete,
                "power": ctx.config.allow_power,
                "kill_process": ctx.config.allow_kill_process,
            },
            "now": time.time(),
        }
    )
    return data


@command("system.ping", summary="连通性测试", params={"echo": "任意回显内容"})
def system_ping(ctx: RequestContext) -> Dict[str, Any]:
    return {"pong": time.time(), "echo": ctx.arg("echo"), "session": ctx.session.id}


@command("system.capabilities", summary="查询电脑支持哪些能力")
def system_capabilities(ctx: RequestContext) -> Dict[str, Any]:
    return {
        "capabilities": ctx.backend.capabilities(),
        "commands": [item["name"] for item in ctx.session.registry.catalog()],
    }


@command("system.mute", summary="静音 / 取消静音 / 切换", params={"state": "on | off | toggle"})
def system_mute(ctx: RequestContext) -> Dict[str, Any]:
    state = ctx.param_str("state", "toggle", max_length=16).lower()
    return ctx.backend.set_mute(state)


@command("system.volume", summary="设置音量百分比", params={"level": "0-100"})
def system_volume(ctx: RequestContext) -> Dict[str, Any]:
    level = ctx.param_int("level", required=True, minimum=0, maximum=100)
    assert level is not None
    return ctx.backend.set_volume(level)


@command("system.audio", summary="读取当前音量与静音状态")
def system_audio(ctx: RequestContext) -> Dict[str, Any]:
    return ctx.backend.audio_status()


@command("system.lock", summary="锁定电脑屏幕")
def system_lock(ctx: RequestContext) -> Dict[str, Any]:
    return ctx.backend.lock()


@command("system.sleep", summary="让电脑进入睡眠(需确认)", danger=True, requires=("power",))
def system_sleep(ctx: RequestContext) -> Dict[str, Any]:
    ctx.require_confirm("休眠电脑")
    return ctx.backend.sleep()


@command(
    "system.shutdown",
    summary="关机(需确认)",
    danger=True,
    requires=("power",),
    params={"delay": "延迟秒数", "force": "强制关闭程序", "confirm": "必须为 true"},
)
def system_shutdown(ctx: RequestContext) -> Dict[str, Any]:
    ctx.require_confirm("关闭电脑")
    delay = ctx.param_int("delay", 0, 0, 86400) or 0
    force = ctx.param_bool("force", False)
    return ctx.backend.shutdown_power(restart=False, delay=delay, force=force)


@command(
    "system.restart",
    summary="重启(需确认)",
    danger=True,
    requires=("power",),
    params={"delay": "延迟秒数", "force": "强制关闭程序", "confirm": "必须为 true"},
)
def system_restart(ctx: RequestContext) -> Dict[str, Any]:
    ctx.require_confirm("重启电脑")
    delay = ctx.param_int("delay", 0, 0, 86400) or 0
    force = ctx.param_bool("force", False)
    return ctx.backend.shutdown_power(restart=True, delay=delay, force=force)


@command("system.cancel_shutdown", summary="取消已排定的关机/重启", requires=("power",))
def system_cancel_shutdown(ctx: RequestContext) -> Dict[str, Any]:
    cancel = getattr(ctx.backend, "cancel_shutdown", None)
    if cancel is None:
        raise Unsupported(f"{ctx.backend.platform_label} 不支持取消关机")
    return cancel()


@command("system.logoff", summary="注销当前用户(需确认)", danger=True, requires=("power",))
def system_logoff(ctx: RequestContext) -> Dict[str, Any]:
    ctx.require_confirm("注销当前用户")
    return ctx.backend.logoff()


@command("system.screenshot", summary="截取当前屏幕(PNG)", params={"format": "png"})
def system_screenshot(ctx: RequestContext) -> Dict[str, Any]:
    data, fmt = ctx.backend.screenshot(fmt=ctx.param_str("format", "png", max_length=8))
    limit = ctx.limits.max_screenshot_bytes
    if limit and len(data) > limit:
        raise CommandError(
            f"截图过大({len(data)} 字节,上限 {limit})", ErrorCode.TOO_LARGE
        )
    return {
        "format": fmt,
        "encoding": "base64",
        "size": len(data),
        "data": b64e(data),
        "taken_at": time.time(),
    }


@command("system.processes", summary="列出运行中的进程", params={"limit": "返回条数"})
def system_processes(ctx: RequestContext) -> Dict[str, Any]:
    limit = ctx.param_int("limit", 60, 1, 2000) or 60
    items = ctx.backend.processes(limit=limit)
    return {"count": len(items), "processes": items}


@command(
    "system.kill",
    summary="结束指定进程(需确认)",
    danger=True,
    requires=("kill",),
    params={"pid": "进程号", "confirm": "必须为 true"},
)
def system_kill(ctx: RequestContext) -> Dict[str, Any]:
    ctx.require_confirm("结束进程")
    pid = ctx.param_int("pid", required=True, minimum=1)
    assert pid is not None
    return ctx.backend.kill(pid)
