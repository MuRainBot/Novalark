"""依赖。

一个 :class:`Depend` 有两种用法：

- ``depends=[...]`` —— 纯门禁，只看能不能过，**返回值丢弃**
- ``Depends(dep)`` —— 返回值注入到**显式声明的那个参数**

不匹配用 ``raise NotMatched`` 表示。

只想写个简单判断时用 :func:`depends`，它接受抛 ``NotMatched`` 的函数。
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Callable, Generic, TypeVar

from ..model.events import MessageEvent
from ..model.segments import At
from .errors import NotMatched

if TYPE_CHECKING:
    from ..model.events import Event

T = TypeVar("T")


async def call_depend(depend: "Depend", event: "Event") -> Any:
    """执行一个依赖。同步的直接调用，不扔线程池；异步的正常 await。

    ``NotMatched`` 照常抛出，由调用方决定当作不匹配还是继续传播。
    """
    result = depend(event)
    if inspect.isawaitable(result):
        result = await result
    return result


class Depend:
    """依赖基类。子类实现 ``__call__``，同步或异步都可以。

    不匹配时 ``raise NotMatched``；返回值在门禁用法里被丢弃，在
    ``Depends(dep)`` 用法里注入到写明的那个参数。抛其他异常是真错误，
    会被记日志，不会被当成不匹配吞掉。
    """

    def __call__(self, event: "Event") -> Any:
        raise NotImplementedError

    # ── 组合子（用于门禁） ─────────────────────────────────

    def __and__(self, other: "Depend") -> "Depend":
        return _And(self, other)

    def __or__(self, other: "Depend") -> "Depend":
        return _Or(self, other)

    def __invert__(self) -> "Depend":
        return _Not(self)

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


class FuncDepend(Depend, Generic[T]):
    """把函数当依赖用。函数内部同样用 ``raise NotMatched`` 表示不匹配。"""

    def __init__(self, fn: Callable[["Event"], T]) -> None:
        self.fn = fn

    def __call__(self, event: "Event") -> T:
        return self.fn(event)

    def __repr__(self) -> str:
        return f"<Depend {getattr(self.fn, '__name__', self.fn)!r}>"


def depends(fn: Callable[["Event"], T]) -> FuncDepend[T]:
    """把函数包装成依赖，顺带获得 ``&`` / ``|`` / ``~`` 组合能力。

    ::

        @depends
        def is_admin(event: GroupMessageEvent) -> None:
            if not event.sender.is_admin:
                raise NotMatched

        @on(GroupMessageEvent, depends=[is_admin & ~is_muted])
    """
    return FuncDepend(fn)


class _And(Depend):
    def __init__(self, *deps: Depend) -> None:
        self.deps = deps

    async def __call__(self, event: "Event") -> None:
        for item in self.deps:
            await call_depend(item, event)


class _Or(Depend):
    def __init__(self, *deps: Depend) -> None:
        self.deps = deps

    async def __call__(self, event: "Event") -> None:
        for item in self.deps:
            try:
                await call_depend(item, event)
            except NotMatched:
                continue
            return
        raise NotMatched


class _Not(Depend):
    def __init__(self, inner: Depend) -> None:
        self.inner = inner

    async def __call__(self, event: "Event") -> None:
        try:
            await call_depend(self.inner, event)
        except NotMatched:
            return
        raise NotMatched


# ── 内置依赖 ───────────────────────────────────────────────


class ToMe(Depend):
    """消息里 @ 了机器人。"""

    def __call__(self, event: "Event") -> None:
        if not isinstance(event, MessageEvent):
            raise NotMatched

        self_id = event.bot.self_id
        if not self_id:
            raise NotMatched

        mentioned = any(
            isinstance(segment, At) and segment.user_id == self_id
            for segment in event.content.segments
        )
        if not mentioned:
            raise NotMatched


class FromGroup(Depend):
    """来自指定群（可以传多个）。"""

    def __init__(self, *group_ids: int) -> None:
        self.group_ids = set(group_ids)

    def __call__(self, event: "Event") -> None:
        from ..model.events import GroupMessageEvent

        if (
            not isinstance(event, GroupMessageEvent)
            or event.group_id not in self.group_ids
        ):
            raise NotMatched
