"""消息。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
)

from .entities import Sender
from .refs import MessageRef
from .segments import At, Segment, Text, as_segments


class RichText(BaseModel):
    """一串消息段。

    构造很宽松::

        RichText("纯文本")
        RichText(At(user_id), Text(" 你好"))
        RichText([Text("a"), At(1)])       # 整条列表也行
        RichText(Text("a")) + "b" + At.all()

    反序列化走 pydantic 标准路径（``model_validate``）：它先经过自定义
    ``__init__``（pydantic 对定义了 ``__init__`` 的模型会照调），再走
    ``segments`` 的字段校验，两条路都能把 dict 还原成具体段类。

    字段类型写成 ``SerializeAsAny[Segment]``：段的实际类型都是 ``Segment``
    的子类，不这么写的话 ``model_dump()`` 只按基类序列化，子类的字段
    （``Text.text``、``At.user_id``…）会被静默丢掉。
    """

    segments: list[SerializeAsAny[Segment]] = Field(default_factory=list)

    def __init__(self, *items: Content, **data: Any) -> None:
        if items:
            data["segments"] = _flatten(items)
        super().__init__(**data)

    @field_validator("segments", mode="before")
    @classmethod
    def _coerce_segments(cls, value: Any) -> Any:
        """从 dict / 字符串反序列化时按 ``type`` 分派到具体段类。"""
        return value if value is None else as_segments(value)

    def __add__(self, other: "RichText | Segment | str") -> "RichText":
        return RichText(self, other)

    def __radd__(self, other: "Segment | str") -> "RichText":
        return RichText(other, self)

    def __bool__(self) -> bool:
        return bool(self.segments)

    @property
    def plain_text(self) -> str:
        """把所有文本段拼起来。"""
        return "".join(seg.text for seg in self.segments if isinstance(seg, Text))

    @property
    def display(self) -> str:
        """给日志看的可读串：文本原样，其它段用占位符。"""
        parts: list[str] = []
        for segment in self.segments:
            if isinstance(segment, Text):
                parts.append(segment.text)
            elif isinstance(segment, At):
                parts.append("@全体" if segment.is_all else f"@{segment.user_id}")
            else:
                parts.append(f"[{segment.type}]")
        return "".join(parts)

    def strip_leading(
        self,
        *types: type[Segment],
        starts: str | Sequence[str] = (),
    ) -> "RichText":
        """剥掉开头的指定类型段与起始符，各一次、顺序无关。

        空白文本段顺带跳过，所以 ``@bot`` 前面多打几个空格也不挡事::

            event.content.strip_leading(At, Reply, starts="/")

        和 :func:`novalark.command.strip_leading` 同义，区别是这里作用于**段**。
        命令解析走的是 token 层那份，两者互不依赖。
        """
        ordered = sorted(
            (starts,) if isinstance(starts, str) else tuple(starts),
            key=len,
            reverse=True,
        )
        budgets = {item: 1 for item in types}

        rest = list(self.segments)
        while rest:
            head = rest[0]

            if isinstance(head, Text) and not head.text.strip():
                rest = rest[1:]  # 纯空白段：整段丢掉
                continue

            if budgets.get(type(head)):
                budgets[type(head)] -= 1
                rest = _drop_leading_blank(rest[1:])
                continue

            if ordered and isinstance(head, Text):
                cut = _cut_start(head.text.lstrip(), ordered)
                if cut is not None:
                    rest = ([Text(cut)] if cut else []) + rest[1:]
                break

            break

        return RichText(segments=rest)

    def append(self, item: "Segment | RichText | str") -> "RichText":
        self.segments.extend(_flatten((item,)))
        return self

    def __str__(self) -> str:
        return self.plain_text


def _flatten(items: tuple[Any, ...]) -> list[Segment]:
    """把 str / RichText / Segment / 段序列的混合输入展平成段列表。"""
    return as_segments(items)


def _cut_start(text: str, starts: Sequence[str]) -> str | None:
    """``text`` 以某个起始符开头就返回剥掉后的剩余，否则 ``None``。"""
    for start in starts:
        if text.startswith(start):
            return text[len(start) :]
    return None


def _drop_leading_blank(segments: list[Segment]) -> list[Segment]:
    """丢掉开头的前导空白：纯空白段整段丢，文本段只丢前导空白。

    ``@bot`` 和 ``/ping`` 之间的空格属于后者那个文本段，剥掉 @ 之后得顺手
    清掉，剩下的才是干净的正文。
    """
    while segments:
        head = segments[0]
        if not isinstance(head, Text):
            break
        trimmed = head.text.lstrip()
        if not trimmed:
            segments = segments[1:]
            continue
        if trimmed != head.text:
            segments = [Text(trimmed), *segments[1:]]
        break
    return segments


class Message(BaseModel):
    """一条完整的消息：坐标 + 内容 + 发送者。

    ``is_self`` 是显式字段，模型不做推断。
    """

    model_config = ConfigDict(extra="allow")

    ref: MessageRef
    content: RichText
    sender: Sender
    time: int = 0
    is_self: bool = False

    @property
    def plain_text(self) -> str:
        return self.content.plain_text


class MessagePage(BaseModel):
    """一页历史消息。

    ``next_seq`` 是继续往前翻的游标，原样传回 ``get_history(start_seq=...)``
    就是下一页；``None`` 表示没有更早的了。它是个**位置**而不是坐标 ——
    指向的那条消息可能已经被撤回，所以别拿它去 :meth:`get_message`。
    """

    messages: list[Message] = Field(default_factory=list)
    next_seq: int | None = None


#: 能当消息内容交给 :class:`RichText` / ``Bot.send`` / ``Bot.reply`` 的东西。
#: 段序列可以任意嵌套，里面的 dict 会按 ``type`` 还原成具体段类。
Content = RichText | Segment | str | list[Any] | tuple[Any, ...] | dict[str, Any]
