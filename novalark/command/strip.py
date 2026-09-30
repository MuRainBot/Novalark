"""命令前缀剥离。

解析前先剥掉消息开头的 ``@机器人`` 段、引用回复段和命令起始符，让这几种
写法落到同一条命令上：

    @bot /ping    →  ping
    [回复] /ping  →  ping

剥离做在 **token 层**：:func:`~novalark.command.parser.tokenize` 按空白切词，
段与段之间的空格本来就在那一步被吃掉，所以"忽略空格"不需要额外处理
（``@bot␣␣ping``、at 前后带空格都一样）。

三种前缀各剥一个，先后顺序无关。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from ..model.segments import At, Reply, Segment, Text


@dataclass(frozen=True)
class Leading:
    """剥离结果：剩下的 token，以及剥掉了什么。"""

    tokens: list[Segment]
    mentioned: bool = False
    replied: bool = False
    prefixed: bool = False

    @property
    def triggered(self) -> bool:
        """有 ``@机器人`` 或 有起始符 —— 满足其一才算在调命令。

        引用回复段不参与判定，它只是被顺带忽略掉。
        """
        return self.mentioned or self.prefixed


def strip_leading(
    tokens: list[Segment],
    *,
    self_id: int | None,
    starts: Collection[str] = (),
    at: bool = True,
    reply: bool = True,
) -> Leading:
    """剥掉开头的 ``@机器人`` / 引用回复段 / 起始符，每种最多一个。

    ``self_id`` 为假（机器人还没连上）时不认任何 ``@``；``starts`` 为空时
    不认起始符。
    """
    at_left = 1 if at else 0
    reply_left = 1 if reply else 0
    has_starts = bool(starts)
    # 长起始符优先，`!!` 才不会被 `!` 抢走
    ordered = sorted(starts, key=len, reverse=True)

    rest = tokens
    mentioned = replied = prefixed = False

    while rest:
        head = rest[0]

        if at_left and self_id and isinstance(head, At) and head.user_id == self_id:
            at_left -= 1
            mentioned = True
            rest = rest[1:]
            continue

        if reply_left and isinstance(head, Reply):
            reply_left -= 1
            replied = True
            rest = rest[1:]
            continue

        if has_starts and isinstance(head, Text):
            cut = _cut(head.text, ordered)
            if cut is not None:
                prefixed = True
                rest = ([Text(cut)] if cut else []) + rest[1:]
            # 起始符之后就该是命令词了，剥完即停
            break

        break

    return Leading(rest, mentioned, replied, prefixed)


def _cut(text: str, starts: list[str]) -> str | None:
    """``text`` 以某个起始符开头就返回剥掉后的剩余，否则 ``None``。

    起始符里有空串时 ``startswith`` 恒真、剩余就是原文 —— 这就是裸词模式，
    不需要额外的分支。
    """
    for start in starts:
        if text.startswith(start):
            return text[len(start) :]
    return None
