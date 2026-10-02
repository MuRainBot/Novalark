"""订阅与订阅注册表。

只有一层：事件分发和条件匹配是同一套机制、一个 priority 轴。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Concatenate,
    ParamSpec,
    Sequence,
    TypeVar,
)

from ..command import report_miss
from ..log import get_logger
from ..model.events import MessageEvent
from ..plugin.context import Plugin, current_plugin
from ..runtime import run_async
from .depend import Depend, call_depend
from .deps import DependencyPlan, Depends, build_plan, resolve
from .errors import NotMatched

logger = get_logger("matcher")

if TYPE_CHECKING:
    from ..command import Command
    from ..model.events import Event
    from .scope import Scope

#: 订阅的 handler：第一个参数是事件（由 ``on(event_type)`` 锚住），后面接
#: ``Depends(...)`` 注入的那些值。给 ``@on`` 和 ``Scope.on`` 用，让 IDE
#: 在写 handler 时就把第一个参数的类型推出来。
E = TypeVar("E", bound="Event")
P = ParamSpec("P")


@dataclass
class Subscription:
    """一条订阅：事件类型 + 作用域 + 门禁依赖 + 注入依赖 + handler。"""

    event_type: type["Event"]
    handler: Callable[..., Any]
    scope: "Scope | None" = None
    depends: list[Depend] = field(default_factory=list)
    plan: DependencyPlan = field(default_factory=DependencyPlan)
    command: "Command | None" = None
    priority: int = 0
    specificity: int = 0
    plugin: Plugin | None = None
    source_module: str = ""

    def __repr__(self) -> str:
        who = self.plugin.name if self.plugin else "framework"
        what = self.command.display if self.command else self.event_type.__name__
        return f"<Subscription {self.handler.__name__} for {what} @{who}>"

    async def invoke(self, event: "Event") -> bool:
        """跑门禁 → 解析命令 → 求依赖 → 调 handler。

        返回 handler 是否真的执行了。命令解析只做一次，结果直接并进
        handler 的 kwargs。
        """
        try:
            if self.scope is not None and not await self.scope.matches(event):
                return False
            for item in self.depends:
                await call_depend(item, event)
        except NotMatched:
            return False

        command_values: dict[str, Any] = {}
        if self.command is not None:
            if not isinstance(event, MessageEvent):
                return False
            parsed = self.command.parse(event)
            if parsed.values is None:
                if parsed.reason:  # 命令词对上了但参数不对
                    logger.warning(f"{self.command.display}：{parsed.reason}")
                    await report_miss(event, self.command, parsed.reason)
                return False
            command_values = parsed.values

        try:
            kwargs = await resolve(self.plan, event)
        except NotMatched:
            return False

        # handler 内部读插件配置、绑日志、发 StopPropagation(PLUGIN) 都靠它。
        who = self.plugin.name if self.plugin else "framework"
        if self.command is not None:
            # 命令是用户显式发起的，值得记 info：不然 handler 不回复时
            # 控制台上什么都看不到。
            logger.info(
                f"执行命令 {self.command.display} → {who}.{self.handler.__name__}"
            )
        else:
            logger.debug(f"执行 {who}.{self.handler.__name__}")

        token = current_plugin.set(self.plugin)
        try:
            await run_async(self.handler, event, **command_values, **kwargs)
        finally:
            current_plugin.reset(token)
        return True


def make_subscription(
    event_type: type["Event"],
    handler: Callable[..., Any],
    *,
    scope: "Scope | None" = None,
    depends: Sequence[Depend | Callable[..., Any]] = (),
    command: "Command | None" = None,
    priority: int | None = None,
    specificity: int = 0,
) -> Subscription:
    """按注册点构造一条订阅。

    作用域继承和插件归属都在这里落实，``on`` 和 ``Scope.on`` 共用。
    """
    return Subscription(
        event_type=event_type,
        handler=handler,
        scope=scope,
        depends=[Depends(item) for item in depends],
        plan=build_plan(handler),
        command=command,
        priority=(
            scope.effective_priority(priority) if scope is not None else (priority or 0)
        ),
        specificity=specificity,
        plugin=registry.plugin_of(handler.__module__),
        source_module=handler.__module__,
    )


class Registry:
    """全局订阅注册表。"""

    def __init__(self) -> None:
        self._subscriptions: list[Subscription] = []
        self._plugins: dict[str, Plugin] = {}
        self._match_cache: dict[type, list[Subscription]] = {}

    # ── 注册 ───────────────────────────────────────────────

    def add(self, subscription: Subscription) -> None:
        self._subscriptions.append(subscription)
        if subscription.plugin is not None:
            subscription.plugin.subscriptions.append(subscription)
        self._match_cache.clear()

    def clear(self) -> None:
        self._subscriptions.clear()
        self._plugins.clear()
        self._match_cache.clear()

    # ── 插件归属 ───────────────────────────────────────────

    def plugin_of(self, module_name: str) -> Plugin | None:
        """按模块名定位插件归属。

        用 ``fn.__module__``（函数自带）。

        非 ``plugins.`` 前缀的模块返回 None，表示框架级订阅。
        """
        if not module_name.startswith("plugins."):
            return None
        parts = module_name.split(".")
        root = ".".join(parts[:2])  # plugins.foo.bar -> plugins.foo
        plugin = self._plugins.get(root)
        if plugin is None:
            plugin = Plugin(name=parts[1])
            self._plugins[root] = plugin
        return plugin

    def get_plugin(self, name: str) -> Plugin | None:
        for plugin in self._plugins.values():
            if plugin.name == name:
                return plugin
        return None

    # ── 匹配 ───────────────────────────────────────────────

    def match(self, event: "Event") -> list[Subscription]:
        """按事件类型（含子类）与优先级返回要跑的订阅。

        结果按事件的具体类型缓存。

        同优先级内按 ``specificity`` 降序。
        """
        cls = type(event)
        cached = self._match_cache.get(cls)
        if cached is None:
            cached = sorted(
                (s for s in self._subscriptions if issubclass(cls, s.event_type)),
                key=lambda s: (s.priority, s.specificity),
                reverse=True,
            )
            self._match_cache[cls] = cached
        return cached

    @property
    def subscriptions(self) -> list[Subscription]:
        return list(self._subscriptions)


registry = Registry()
