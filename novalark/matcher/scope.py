from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..model.events import MessageEvent
from .depend import Depend, call_depend
from .deps import Depends

if TYPE_CHECKING:
    from ..model.events import Event


class Scope:
    """一组订阅的作用域。"""

    def __init__(
        self,
        *,
        depends: Sequence[Depend | Callable[..., Any]] = (),
        priority: int | None = None,
        prefix: str | Sequence[str] = (),
        parent: "Scope | None" = None,
    ) -> None:
        self.depends: list[Depend] = [Depends(item) for item in depends]
        self.priority = priority
        # 写 "admin" 和写 ("admin",) 都行
        self.prefix = (prefix,) if isinstance(prefix, str) else tuple(prefix)
        self.parent = parent
        self.enabled = True

    def __repr__(self) -> str:
        where = " ".join(self.full_prefix()) or "-"
        return f"<Scope {where} depends={len(self.depends)} enabled={self.enabled}>"

    # ── 嵌套 ───────────────────────────────────────────────

    def scope(
        self,
        *,
        depends: Sequence[Depend | Callable[..., Any]] = (),
        priority: int | None = None,
        prefix: str | Sequence[str] = (),
    ) -> "Scope":
        """建一个子作用域。条件沿父链累积。"""
        return Scope(depends=depends, priority=priority, prefix=prefix, parent=self)

    # ── 运行期调整 ─────────────────────────────────────────

    def add(self, dependency: Depend | Callable[..., Any]) -> Depend:
        """加一条门禁，立刻对本作用域的所有订阅生效。"""
        item = Depends(dependency)
        self.depends.append(item)
        return item

    def remove(self, dependency: Depend) -> None:
        self.depends = [item for item in self.depends if item is not dependency]

    # ── 注册 ───────────────────────────────────────────────

    def on(
        self,
        event_type: type["Event"],
        *,
        depends: Sequence[Depend | Callable[..., Any]] = (),
        priority: int | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """在这个作用域里订阅事件。条件叠加作用域自己的门禁。"""
        from .subscription import make_subscription, registry

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            registry.add(
                make_subscription(
                    event_type, fn, scope=self, depends=depends, priority=priority
                )
            )
            return fn

        return decorator

    def command(
        self,
        *template: Any,
        depends: Sequence[Depend | Callable[..., Any]] = (),
        priority: int | None = None,
        starts: str | Sequence[str] | None = None,
        at: bool | None = None,
        reply: bool | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """在这个作用域里注册命令。作用域的前缀拼在命令词前面。"""
        from ..command import compile_command
        from .subscription import make_subscription, registry

        command = compile_command(
            *template,
            prefix=self.full_prefix(),
            starts=starts,
            at=at,
            reply=reply,
        )

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            registry.add(
                make_subscription(
                    MessageEvent,
                    fn,
                    scope=self,
                    depends=depends,
                    command=command,
                    priority=priority,
                    specificity=command.specificity,
                )
            )
            return fn

        return decorator

    # ── 求值 ───────────────────────────────────────────────

    async def matches(self, event: "Event") -> bool:
        """父子链上任何一环不满足就算不匹配。``NotMatched`` 照常抛出。"""
        if not self.enabled:
            return False
        if self.parent is not None and not await self.parent.matches(event):
            return False
        for item in self.depends:
            await call_depend(item, event)
        return True

    # ── 继承 ───────────────────────────────────────────────

    def effective_priority(self, own: int | None = None) -> int:
        """自己的优先，没写就沿父链找。"""
        if own is not None:
            return own
        node: Scope | None = self
        while node is not None:
            if node.priority is not None:
                return node.priority
            node = node.parent
        return 0

    def full_prefix(self) -> tuple[str, ...]:
        """命令词前缀，从最外层往里拼。"""
        parts: list[str] = []
        node: Scope | None = self
        while node is not None:
            parts[:0] = node.prefix
            node = node.parent
        return tuple(parts)
