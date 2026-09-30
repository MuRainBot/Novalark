"""插件上下文。

归属按「定义期」信息定位：注册订阅看 ``fn.__module__``，插件配置看
``cls.__module__``，元信息由 ``PluginManager`` 从模块对象上读 ``__plugin__``。

运行期另有 ``current_plugin``，由分发器在调用 handler 前设置，供 handler
内部读插件配置、绑日志、发 ``StopPropagation(PLUGIN)``。
"""

from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..matcher.subscription import Subscription


class PluginMeta(TypedDict, total=False):
    """插件元信息，写在模块顶层的 ``__plugin__`` 里。"""

    author: str
    version: str
    description: str


def plugin_name_of(module_name: str) -> str:
    """``plugins.echo`` / ``plugins.echo.sub`` → ``echo``。非插件模块原样返回。"""
    parts = module_name.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "plugins" else module_name


class Plugin:
    """一个插件。"""

    def __init__(self, name: str, path: Path | None = None) -> None:
        self.name = name
        self.path = path
        self.author = ""
        self.version = ""
        self.description = ""
        self.subscriptions: list["Subscription"] = []

    def __repr__(self) -> str:
        return f"<Plugin {self.name}>"


current_plugin: ContextVar[Plugin | None] = ContextVar(
    "novalark_current_plugin", default=None
)


def get_current_plugin() -> Plugin | None:
    """当前正在执行 handler 的插件；框架自身的订阅返回 None。"""
    return current_plugin.get()
