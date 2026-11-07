"""内置命令集合。"""

from __future__ import annotations

from echolink.server.dispatch import Registry


def build_registry() -> Registry:
    """加载全部命令模块,返回注册表。"""
    from echolink.server.handlers import desktop, files, system

    registry = Registry()
    for module in (system, files, desktop):
        registry.load_module(module)
    return registry


__all__ = ["build_registry"]
