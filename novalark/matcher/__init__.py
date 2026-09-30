"""订阅层。``@on`` 是唯一入口。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from .depend import Depend, FromGroup, FuncDepend, ToMe, depends
from .deps import Depends, DependencyPlan, build_plan, resolve
from .dispatch import dispatch, emit
from .errors import NotMatched, PropagationScope, StopPropagation
from .scope import Scope
from .subscription import Registry, Subscription, make_subscription, registry

if TYPE_CHECKING:
    from ..model.events import Event


def on(
    event_type: type["Event"],
    *,
    depends: Sequence[Depend | Callable[..., Any]] | None = None,
    priority: int = 0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """订阅一个事件类型。

    ::

        @on(GroupMessageEvent)                                # 纯监听
        @on(GroupMessageEvent, depends=[ToMe()], priority=10)  # 带门禁
        @on(MessageEvent)                                     # 收所有消息子类

    ``depends`` 里的是门禁，返回值丢弃；想在 handler 里拿到依赖的值，用
    ``Depends(...)`` 写在参数默认值上::

        @on(GroupMessageEvent)
        async def handler(event, member: GroupMember = Depends(GetMember())):
            ...

    同步 handler 和异步 handler 都支持。同步的会在线程池里跑，里面的
    框架调用用 ``task(...)`` 桥接::

        @on(MessageEvent)
        def handler(event):
            task(event.reply("hi"))
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        registry.add(
            make_subscription(event_type, fn, depends=depends or (), priority=priority)
        )
        return fn

    return decorator


__all__ = [
    "Depend",
    "Depends",
    "DependencyPlan",
    "FromGroup",
    "FuncDepend",
    "NotMatched",
    "PropagationScope",
    "Registry",
    "Scope",
    "StopPropagation",
    "Subscription",
    "ToMe",
    "build_plan",
    "depends",
    "dispatch",
    "emit",
    "on",
    "registry",
    "resolve",
]
