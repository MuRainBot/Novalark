"""依赖注入。

注册期静态分析 handler 签名构建计划，运行期只执行。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar, overload

from .depend import Depend, FuncDepend, call_depend

if TYPE_CHECKING:
    from ..model.events import Event

T = TypeVar("T")


@overload
def Depends(dependency: Callable[..., Awaitable[T]]) -> T: ...
@overload
def Depends(dependency: Callable[..., T]) -> T: ...


def Depends(dependency: Depend | Callable[..., Any]) -> Any:
    """声明这个参数由依赖提供。

    类型从依赖的返回类型反推，不写注解也能补全。运行期返回的是依赖
    对象本身，注册期由 :func:`build_plan` 从默认值里认出来。
    """
    if isinstance(dependency, Depend):
        return dependency
    return FuncDepend(dependency)


@dataclass
class DependencyPlan:
    """一个 handler 的依赖计划。"""

    entries: list[tuple[str, Depend]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.entries)


def build_plan(handler: Callable[..., Any]) -> DependencyPlan:
    """扫描签名找出所有依赖默认值。注册期跑一次。"""
    try:
        signature = inspect.signature(handler)
    except (TypeError, ValueError):
        return DependencyPlan()

    entries: list[tuple[str, Depend]] = [
        (name, param.default)
        for name, param in signature.parameters.items()
        if isinstance(param.default, Depend)
    ]
    return DependencyPlan(entries)


async def resolve(plan: DependencyPlan, event: "Event") -> dict[str, Any]:
    """按计划求值。同一个依赖实例在一次事件里只算一次。

    ``NotMatched`` 照常抛出，由调用方决定这算不匹配还是错误。
    """
    if not plan:
        return {}
    cache: dict[int, Any] = {}
    values: dict[str, Any] = {}
    for name, dependency in plan.entries:
        key = id(dependency)
        if key not in cache:
            cache[key] = await call_depend(dependency, event)
        values[name] = cache[key]
    return values
