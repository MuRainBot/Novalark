"""命令系统。

把模板编成解析器，再挂到订阅层上：

    @on_command("ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明"))
    async def ban(event, target, reason): ...

命令参数和 ``Depends`` 注入的值会一起交给 handler。
"""

from __future__ import annotations

from collections.abc import Awaitable, Collection
from typing import TYPE_CHECKING, Any, Callable, Sequence

from ..log import get_logger
from ..model.events import MessageEvent
from ..model.segments import Text
from .parser import (
    Arg,
    AtArg,
    Cmd,
    Fail,
    GreedyTextArg,
    IntArg,
    LiteralArg,
    Ok,
    OptionalArg,
    Or,
    Seq,
    TextArg,
    tokenize,
)
from .strip import strip_leading

if TYPE_CHECKING:
    from ..config import CoreConfig
    from ..matcher.depend import Depend

logger = get_logger("command")

#: 命令词对上了、参数不对时，告诉用户发生了什么。
#: ``event`` 一定是 :class:`MessageEvent`。
MissHandler = Callable[[MessageEvent, "Command", str], Awaitable[None]]


class ParseResult:
    """一次命令解析的结果。

    ``values`` 为 None 就是没匹配上。``reason`` 只在这条消息**看起来像**
    这条命令（命令词对上了）时才有值。
    """

    __slots__ = ("values", "reason")

    def __init__(self, values: dict[str, Any] | None, reason: str = "") -> None:
        self.values = values
        self.reason = reason

    def __repr__(self) -> str:
        if self.values is None:
            return f"<ParseResult 失败 {self.reason!r}>"
        return f"<ParseResult {self.values!r}>"


class Command:
    """一条命令：解析器 + 命令词 + 前缀策略。

    ``starts`` / ``at`` / ``reply`` 是这条命令的前缀策略。``None`` 表示跟随
    全局配置（``core.command_starts`` / ``command_at`` / ``command_reply``），
    非 ``None`` 就是这条命令自己说了算。
    """

    def __init__(
        self,
        parser: Arg[Any],
        words: tuple[str, ...] = (),
        *,
        starts: frozenset[str] | None = None,
        at: bool | None = None,
        reply: bool | None = None,
    ) -> None:
        self.parser = parser
        self.words = words
        self.starts = starts
        self.at = at
        self.reply = reply

    def __repr__(self) -> str:
        return f"<Command {self.display}>"

    @property
    def display(self) -> str:
        return " ".join(self.words) if self.words else repr(self.parser)

    @property
    def specificity(self) -> int:
        """字面量越多越具体。同优先级内靠它决定谁先试。"""
        return _specificity(self.parser)

    def parse(self, event: MessageEvent) -> ParseResult:
        """剥掉命令前缀后解析这条消息。

        既没 ``@机器人`` 也没起始符，就是没在调这条命令：直接返回空结果，
        **不报用法提示**。

        其余情况下，解析失败、或者**没把所有 token 消费完**，都不算匹配。
        """
        starts, at, reply = self._options()
        leading = strip_leading(
            tokenize(event.content),
            self_id=event.bot.self_id,
            starts=starts,
            at=at,
            reply=reply,
        )
        if not leading.triggered:
            return ParseResult(None)

        tokens = leading.tokens
        result = self.parser.parse(tokens)

        if isinstance(result, Fail):
            return ParseResult(None, result.reason if self._words_match(tokens) else "")
        if result.rest:
            return ParseResult(
                None, "后面还有多余的内容" if self._words_match(tokens) else ""
            )
        return ParseResult(result.value)

    def _options(self) -> tuple[Collection[str], bool, bool]:
        """前缀策略：命令级覆盖优先，其余跟随全局配置。"""
        if self.starts is not None and self.at is not None and self.reply is not None:
            return self.starts, self.at, self.reply

        core = _core()
        return (
            self.starts if self.starts is not None else core.command_starts,
            self.at if self.at is not None else core.command_at,
            self.reply if self.reply is not None else core.command_reply,
        )

    def _words_match(self, tokens: list[Any]) -> bool:
        """开头的词是否和命令词一致 —— 决定失败值不值得说。"""
        if not self.words or len(tokens) < len(self.words):
            return False
        for word, token in zip(self.words, tokens):
            if not isinstance(token, Text) or token.text != word:
                return False
        return True


def compile_command(
    *template: str | Arg[Any],
    prefix: Sequence[str] = (),
    starts: str | Sequence[str] | None = None,
    at: bool | None = None,
    reply: bool | None = None,
) -> Command:
    """把模板编成 :class:`Command`。

    既能写 ``("ban", AtArg("target"))``，也能写 ``(Cmd(...) | Cmd(...),)``。

    ``prefix`` 是拼在命令词前面的**字面量**，供 ``Scope(prefix=...)`` 用
    （``admin ban`` 的 ``admin``）；``starts`` / ``at`` / ``reply`` 才是命令
    **起始符与自动剥离**策略，``None`` 表示跟随全局配置。
    """
    if len(template) == 1 and isinstance(template[0], (Seq, Or)):
        parser: Arg[Any] = _with_prefix(tuple(prefix), template[0])
    else:
        parser = Cmd(*prefix, *template)
    return Command(
        parser=parser,
        words=_leading_words(parser),
        starts=_normalize_starts(starts),
        at=at,
        reply=reply,
    )


def _normalize_starts(value: str | Sequence[str] | None) -> frozenset[str] | None:
    """``"/"`` / ``("/", "!")`` / ``None`` → ``frozenset``；``None`` 原样透传。"""
    if value is None:
        return None
    if isinstance(value, str):
        return frozenset({value})
    return frozenset(value)


def _with_prefix(prefix: tuple[str, ...], parser: Arg[Any]) -> Arg[Any]:
    """把前缀拼到模板前面。``Or`` 的每个分支都要拼一遍。"""
    if not prefix:
        return parser
    if isinstance(parser, Or):
        return Or(*(_with_prefix(prefix, branch) for branch in parser.branches))

    front = [LiteralArg(word) for word in prefix]
    if isinstance(parser, Seq):
        return Seq(*front, *parser.parts)
    return Seq(*front, parser)


def on_command(
    *template: str | Arg[Any],
    depends: Sequence["Depend | Callable[..., Any]"] = (),
    priority: int | None = None,
    starts: str | Sequence[str] | None = None,
    at: bool | None = None,
    reply: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册一个命令。

    ::

        @on_command("ping")
        async def ping(event): ...

        @on_command("ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明"))
        async def ban(event, target, reason): ...

        不同写法走同一条命令
        @on_command(Cmd("kick", AtArg("target")) | Cmd("kick", IntArg("target")))
        async def kick(event, target): ...

    字符串自动当字面量。模板里的参数按名字变成 handler 的关键字参数。

    解析前会自动剥掉消息开头的 ``@机器人`` 和引用回复段；没 ``@机器人`` 时
    必须带起始符（``core.command_starts``，默认 ``/``）。这三样都能按命令覆盖：
    ``starts="!"``、``starts=()``（关掉）、``at=False``、``reply=False``。
    """
    from ..matcher.subscription import make_subscription, registry

    command = compile_command(*template, starts=starts, at=at, reply=reply)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        registry.add(
            make_subscription(
                MessageEvent,
                fn,
                depends=depends,
                command=command,
                priority=priority,
                specificity=command.specificity,
            )
        )
        return fn

    return decorator


_miss_handler: MissHandler | None = None


def set_miss_handler(handler: MissHandler | None) -> None:
    """替换「命令没匹配上」时的提示行为，传 ``None`` 恢复默认。

    默认实现回一条 ``命令词：原因``，由 ``core.command_hint`` 控制开关。
    """
    global _miss_handler
    _miss_handler = handler


async def report_miss(event: MessageEvent, command: "Command", reason: str) -> None:
    """命令词对上了但参数不对：把原因告诉用户。

    提示发不出去只记日志 —— 一句用法提示不该把分发带崩。
    """
    try:
        await (_miss_handler or _default_miss_handler)(event, command, reason)
    except Exception:
        logger.exception(f"{command.display} 的用法提示没能发出去")


async def _default_miss_handler(
    event: MessageEvent, command: "Command", reason: str
) -> None:
    if not _hint_enabled():
        return
    await event.reply(f"{command.display}：{reason}")


def _core() -> "CoreConfig":
    """``core`` 配置。没初始化时（嵌入式用法）给一份字段默认值。"""
    from ..config import CoreConfig, get_settings

    try:
        return get_settings().core
    except RuntimeError:
        return CoreConfig()


def _hint_enabled() -> bool:
    """``core.command_hint``。配置没初始化（比如测试里）就当开着。"""
    return _core().command_hint


def _specificity(parser: Arg[Any]) -> int:
    """字面量越多越具体；分支取最具体的那个。"""
    if isinstance(parser, Seq):
        return sum(_specificity(part) for part in parser.parts)
    if isinstance(parser, Or):
        return max((_specificity(branch) for branch in parser.branches), default=0)
    return 1 if isinstance(parser, LiteralArg) else 0


def _leading_words(parser: Arg[Any]) -> tuple[str, ...]:
    """开头连续的命名词，用来展示和写日志。"""
    if not isinstance(parser, Seq):
        return ()
    words: list[str] = []
    for part in parser.parts:
        if not isinstance(part, LiteralArg):
            break
        words.append(part.word)
    return tuple(words)


__all__ = [
    "Arg",
    "AtArg",
    "Cmd",
    "Command",
    "Fail",
    "GreedyTextArg",
    "IntArg",
    "LiteralArg",
    "Ok",
    "OptionalArg",
    "Or",
    "Seq",
    "TextArg",
    "compile_command",
    "on_command",
    "report_miss",
    "set_miss_handler",
    "tokenize",
]
