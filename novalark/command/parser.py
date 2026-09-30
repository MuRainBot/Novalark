"""命令模板与解析。

模板是「字面量和参数排列成的序列」，字符串自动当字面量：

    Cmd("ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明"))

解析的输入是**消息段**，``AtArg`` 要认得出 ``At`` 段。

不匹配用返回值表示（:class:`Ok` / :class:`Fail`），不用异常；``Fail``
里带着失败原因。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from ..model.message import RichText
from ..model.segments import At, Segment, Text

T = TypeVar("T")


class Ok(Generic[T]):
    """解析成功：值和**没被消费的剩余**。"""

    def __init__(self, value: T, rest: list[Segment]) -> None:
        self.value = value
        self.rest = rest

    def __repr__(self) -> str:
        return f"Ok({self.value!r}, 剩 {len(self.rest)})"


class Fail:
    """解析失败：带着原因，供最终提示用。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def __repr__(self) -> str:
        return f"Fail({self.reason!r})"


def tokenize(content: RichText) -> list[Segment]:
    """把消息切成 token。

    文本段按空白切开（每个词一个 ``Text``），非文本段各自成一 token ——
    段的边界天然就是 token 的边界。
    """
    tokens: list[Segment] = []
    for segment in content.segments:
        if isinstance(segment, Text):
            tokens.extend(Text(word) for word in segment.text.split())
        else:
            tokens.append(segment)
    return tokens


class Arg(Generic[T]):
    """一个参数。子类实现 :meth:`parse`。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def parse(self, tokens: list[Segment]) -> "Ok[T] | Fail":
        raise NotImplementedError

    def __or__(self, other: "Arg[Any]") -> "Or":
        """``A | B``：先试 A，不行再用原始输入试 B。"""
        return Or(self, other)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"


# ── 基础参数 ───────────────────────────────────────────────


class LiteralArg(Arg[str]):
    """匹配一个固定的词。

    默认**不产生值**（``name`` 留空），所以 ``"ban"`` 不会变成 handler 的
    一个关键字参数 —— 它只负责定位。想接住它再显式给个名字。
    """

    def __init__(self, word: str, *, name: str = "") -> None:
        super().__init__(name)
        self.word = word

    def parse(self, tokens: list[Segment]) -> "Ok[str] | Fail":
        head = tokens[0] if tokens else None
        if isinstance(head, Text) and head.text == self.word:
            return Ok(self.word, tokens[1:])
        return Fail(f"需要 {self.word!r}")

    def __repr__(self) -> str:
        return repr(self.word)


class TextArg(Arg[str]):
    """匹配一个文本 token。"""

    def parse(self, tokens: list[Segment]) -> "Ok[str] | Fail":
        head = tokens[0] if tokens else None
        if isinstance(head, Text) and head.text:
            return Ok(head.text, tokens[1:])
        return Fail(f"{self.name} 应该是一段文本")


class IntArg(Arg[int]):
    """匹配一个整数。"""

    def parse(self, tokens: list[Segment]) -> "Ok[int] | Fail":
        head = tokens[0] if tokens else None
        if not isinstance(head, Text) or not head.text:
            return Fail(f"{self.name} 应该是一个整数")
        try:
            return Ok(int(head.text), tokens[1:])
        except ValueError:
            return Fail(f"{self.name} 应该是一个整数，拿到 {head.text!r}")


class AtArg(Arg[At]):
    """匹配一个 @。@全体不算。"""

    def parse(self, tokens: list[Segment]) -> "Ok[At] | Fail":
        head = tokens[0] if tokens else None
        if isinstance(head, At) and head.user_id is not None:
            return Ok(head, tokens[1:])
        return Fail(f"{self.name} 应该是一个 @某人")


class GreedyTextArg(Arg[str]):
    """把剩下的文本 token 全部吃掉，拼成一段。"""

    def parse(self, tokens: list[Segment]) -> "Ok[str] | Fail":
        words: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if not isinstance(token, Text) or not token.text:
                break
            words.append(token.text)
            index += 1

        if not words:
            return Fail(f"缺少 {self.name}")
        return Ok(" ".join(words), tokens[index:])


class OptionalArg(Arg[Any]):
    """可选参数。匹配不上就用默认值，**一个 token 都不消费**。"""

    def __init__(self, inner: Arg[Any], default: Any = None) -> None:
        super().__init__(inner.name)
        self.inner = inner
        self.default = default

    def parse(self, tokens: list[Segment]) -> "Ok[Any] | Fail":
        result = self.inner.parse(tokens)
        if isinstance(result, Fail):
            return Ok(self.default, tokens)
        return result


# ── 组合子 ─────────────────────────────────────────────────


class Seq(Arg[dict[str, Any]]):
    """顺序匹配。命令模板就是它。

    各参数按名字收进 dict，交给 handler 当 kwargs。
    """

    def __init__(self, *parts: Arg[Any]) -> None:
        super().__init__("")
        self.parts = parts

    def parse(self, tokens: list[Segment]) -> "Ok[dict[str, Any]] | Fail":
        values: dict[str, Any] = {}
        rest = tokens
        for part in self.parts:
            result = part.parse(rest)
            if isinstance(result, Fail):
                return result
            if part.name:
                values[part.name] = result.value
            elif isinstance(result.value, dict):
                values.update(result.value)  # 嵌套的组合子贡献一整组值
            rest = result.rest
        return Ok(values, rest)

    def __repr__(self) -> str:
        return " ".join(repr(part) for part in self.parts)


class Or(Arg[Any]):
    """依次尝试各个分支。

    每个分支都拿**原始 tokens** 重试，即回溯。全部失败时把所有原因攒起来
    报出去。
    """

    def __init__(self, *branches: Arg[Any]) -> None:
        # 名字由分支自己带，组合子本身不产生值
        super().__init__("")
        self.branches = branches

    def parse(self, tokens: list[Segment]) -> "Ok[Any] | Fail":
        reasons: list[str] = []
        for branch in self.branches:
            result = branch.parse(tokens)
            if isinstance(result, Ok):
                return result
            reasons.append(result.reason)
        return Fail(" 或 ".join(dict.fromkeys(reasons)))  # 去重，保持顺序

    def __or__(self, other: "Arg[Any]") -> "Or":
        """继续往后再接一个分支 —— 展平，不套娃。"""
        return Or(*self.branches, other)

    def __repr__(self) -> str:
        return " | ".join(repr(branch) for branch in self.branches)


def Cmd(*template: str | Arg[Any]) -> Seq:
    """按模板造一个命令解析器。字符串自动当字面量::

    Cmd("ban", AtArg("target"))
    Cmd("kick", AtArg("target")) | Cmd("kick", IntArg("target"))
    """
    parts: list[Arg[Any]] = []
    for item in template:
        if isinstance(item, str):
            parts.append(LiteralArg(item))
        elif isinstance(item, Arg):
            parts.append(item)
        else:
            raise TypeError(f"命令模板里不能放 {type(item).__name__}")
    return Seq(*parts)
