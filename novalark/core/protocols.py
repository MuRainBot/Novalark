from typing import Protocol, runtime_checkable, Any

from novalark.core.rich_text import RichText
from novalark.core import abstractions


# =============================================================================
# 基础定义
# =============================================================================


@runtime_checkable
class Context(Protocol):
    """
    代表一个聊天上下文（如群组、私聊窗口）。
    """

    @property
    def id(self) -> str: ...

    def __hash__(self) -> int: ...

    def __eq__(self, other: Any) -> bool: ...


# =============================================================================
# 消息相关
# =============================================================================

@runtime_checkable
class Message(Protocol):
    """
    完整的消息实体。
    包含内容，且可以通过 receipt 属性获取其句柄。
    """

    @property
    def receipt(self) -> abstractions.MessageReceipt: ...

    @property
    def content(self) -> RichText: ...

    @property
    def context(self) -> Context | abstractions.Context:
        return self.receipt.context


# =============================================================================
# Bot 定义 (面向能力编程)
# =============================================================================

@runtime_checkable
class Bot(Protocol):
    async def clean_up(self) -> None: ...


@runtime_checkable
class SendableBot(Bot, Protocol):
    """具备主动发送消息能力的 Bot"""

    async def send(
            self, context: Context, content: RichText
    ) -> abstractions.MessageReceipt:
        ...


@runtime_checkable
class ReplayableBot(Bot, Protocol):
    """具备回复消息能力的 Bot"""

    async def replay(
            self, target: abstractions.MessageReceipt, content: RichText
    ) -> abstractions.MessageReceipt:
        ...


# =============================================================================
# 事件定义
# =============================================================================

@runtime_checkable
class Event(Protocol):
    """基础事件协议"""
    ...


@runtime_checkable
class CancellableEvent(Event, Protocol):
    """可被拦截/取消的事件"""

    def cancel(self) -> None: ...


@runtime_checkable
class PlatformEvent(CancellableEvent, Protocol):
    """来自特定平台的事件，必然绑定了一个 Bot"""

    @property
    def bot(self) -> Bot: ...


@runtime_checkable
class MessageEvent(PlatformEvent, Protocol):
    """消息事件"""

    @property
    def message(self) -> Message: ...

    @property
    def context(self) -> Context:
        return self.message.context


# =============================================================================
# 复合能力事件 (Mixin Style)
# =============================================================================

@runtime_checkable
class SendableMessageEvent(MessageEvent, Protocol):
    """
    代表一个“可以主动发起新对话”的事件上下文。
    """

    @property
    def bot(self) -> SendableBot: ...

    async def send(self, content: RichText) -> abstractions.MessageReceipt:
        """快捷方法：向当前上下文发送消息"""
        return await self.bot.send(self.context, content)


@runtime_checkable
class ReplayableMessageEvent(MessageEvent, Protocol):
    """
    代表一个“可以被回复”的事件。
    """

    @property
    def bot(self) -> ReplayableBot: ...

    async def replay(self, content: RichText) -> abstractions.MessageReceipt:
        """快捷方法：回复当前消息"""
        return await self.bot.replay(self.message.receipt, content)