"""命令模板与解析。"""

from __future__ import annotations

import logging

from novalark import (
    AtArg,
    Cmd,
    Depend,
    Depends,
    GreedyTextArg,
    IntArg,
    OptionalArg,
    Scope,
    TextArg,
    on_command,
    set_miss_handler,
)
from novalark.command.parser import (
    Fail,
    LiteralArg,
    Ok,
    tokenize,
)
from novalark.matcher import dispatch
from novalark.model import At, RichText, Segment, Text


def _tokens(*segments: Segment) -> list[Segment]:
    return tokenize(RichText(*segments))


def test_tokenize_splits_text_and_keeps_segments():
    tokens = tokenize(RichText(Text("ban "), At(user_id=1), Text(" 刷屏 很久")))
    assert [type(token).__name__ for token in tokens] == ["Text", "At", "Text", "Text"]
    assert [seg.text for seg in tokens if isinstance(seg, Text)] == [
        "ban",
        "刷屏",
        "很久",
    ]


def test_literal_matches_whole_word_only():
    assert isinstance(LiteralArg("ban").parse(_tokens(Text("ban"))), Ok)
    # 不是前缀匹配
    assert isinstance(LiteralArg("ban").parse(_tokens(Text("band"))), Fail)


def test_int_arg_reports_what_it_got():
    assert isinstance(IntArg("n").parse(_tokens(Text("42"))), Ok)

    result = IntArg("n").parse(_tokens(Text("abc")))
    assert isinstance(result, Fail)
    assert "abc" in result.reason  # 报错要说清拿到的是什么


def test_text_arg_needs_a_text_token():
    result = TextArg("who").parse(_tokens(At(user_id=1)))
    assert isinstance(result, Fail)
    assert "who" in result.reason


def test_at_arg_rejects_mention_all():
    assert isinstance(AtArg("t").parse(_tokens(At(user_id=1))), Ok)
    # @全体不被当成操作目标
    assert isinstance(AtArg("t").parse(_tokens(At.all())), Fail)


def test_greedy_eats_all_remaining_text():
    parser = GreedyTextArg("content")
    result = parser.parse(_tokens(Text("刷屏"), Text("很久")))
    assert isinstance(result, Ok)
    assert result.value == "刷屏 很久"
    assert result.rest == []

    # 碰到非文本段就停
    result = parser.parse(_tokens(Text("看"), At(user_id=1), Text("这个")))
    assert isinstance(result, Ok)
    assert result.value == "看"
    assert len(result.rest) == 2


def test_seq_collects_named_values():
    parser = Cmd("ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明"))
    result = parser.parse(_tokens(Text("ban"), At(user_id=1), Text("刷屏 很久")))

    assert isinstance(result, Ok)
    assert result.value["target"].user_id == 1
    assert result.value["reason"] == "刷屏 很久"
    assert result.rest == []


def test_optional_arg_does_not_consume_on_miss():
    parser = Cmd("ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明"))
    result = parser.parse(_tokens(Text("ban"), At(user_id=1)))

    assert isinstance(result, Ok)
    assert result.value["reason"] == "未说明"
    assert result.rest == []


def test_or_backtracks_with_original_tokens():
    """分支必须拿原始输入重试，不能被上一个分支留下的残局影响。"""
    parser = Cmd("kick", IntArg("target")) | Cmd("kick", AtArg("target"))

    by_at = parser.parse(_tokens(Text("kick"), At(user_id=7)))
    assert isinstance(by_at, Ok)
    assert by_at.value["target"].user_id == 7

    by_int = parser.parse(_tokens(Text("kick"), Text("7")))
    assert isinstance(by_int, Ok)
    assert by_int.value["target"] == 7


def test_or_accumulates_every_reason():
    parser = Cmd("a", IntArg("n")) | Cmd("a", AtArg("t"))
    result = parser.parse(_tokens(Text("a"), Text("x")))

    assert isinstance(result, Fail)
    assert "整数" in result.reason
    assert "@" in result.reason


def test_fail_is_a_value_not_an_exception():
    """不匹配靠返回值传递，异常留给真正的错误。"""
    assert isinstance(Cmd("ban").parse(_tokens(Text("nope"))), Fail)


def test_template_rejects_junk():
    try:
        Cmd("ban", 42)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "int" in str(exc)
    else:
        raise AssertionError("模板里放非字符串非 Arg 应该报错")


def test_seq_stops_at_first_failure():
    """顺序匹配在第一个失败处停下，报的是那个失败的参数。"""
    parser = Cmd("ban", IntArg("n"), AtArg("t"))
    result = parser.parse(_tokens(Text("ban"), Text("x"), At(user_id=1)))

    assert isinstance(result, Fail)
    assert "n" in result.reason


# ── 接进订阅层 ─────────────────────────────────────────────


def test_on_command_dispatches_with_named_args(scenario, make_group_event):
    async def body():
        seen: list[tuple[int, str]] = []

        @on_command(
            "ban", AtArg("target"), OptionalArg(GreedyTextArg("reason"), "未说明")
        )
        async def ban(event, target, reason):
            seen.append((target.user_id, reason))

        await dispatch(
            make_group_event(segments=[Text("ban "), At(user_id=7), Text(" 刷屏")])
        )
        assert seen == [(7, "刷屏")]

    scenario(body)


def test_command_requires_every_token_consumed(scenario, make_group_event):
    """多出来的 token 让命令不匹配。"""

    async def body():
        seen: list[str] = []

        @on_command("ban")
        async def ban(event):
            seen.append("ban")

        await dispatch(make_group_event("ban 还说了别的"))
        assert seen == []

        await dispatch(make_group_event("ban"))
        assert seen == ["ban"]

    scenario(body)


def test_literal_beats_greedy_at_same_priority(scenario, make_group_event):
    """同一优先级下先试字面量，贪婪参数不吃掉它的输入。"""

    async def body():
        seen: list[tuple[str, str | None]] = []

        @on_command("ban", GreedyTextArg("who"))
        async def greedy(event, who):
            seen.append(("greedy", who))

        @on_command("ban", "all")
        async def literal(event):
            seen.append(("literal", None))

        await dispatch(make_group_event("ban all"))
        assert seen == [("literal", None)]

    scenario(body)


def test_command_and_depends_arrive_together(scenario, make_group_event):
    """命令参数和注入的值一起进 handler。"""

    async def body():
        seen: list[tuple[str, str]] = []

        class Who(Depend):
            def __call__(self, event):
                return "依赖"

        @on_command("hi", TextArg("word"))
        async def hi(event, word, who=Depends(Who())):
            seen.append((word, who))

        await dispatch(make_group_event("hi 你好"))
        assert seen == [("你好", "依赖")]

    scenario(body)


def test_scope_prefix_is_prepended(scenario, make_group_event):
    async def body():
        seen: list[int] = []
        admin = Scope(prefix="admin")

        @admin.command("ban", AtArg("target"))
        async def ban(event, target):
            seen.append(target.user_id)

        await dispatch(make_group_event(segments=[Text("admin ban "), At(user_id=5)]))
        assert seen == [5]

        # 少了前缀就不是这条命令
        await dispatch(make_group_event(segments=[Text("ban "), At(user_id=5)]))
        assert seen == [5]

    scenario(body)


def test_scope_prefix_carries_depends_too(scenario, make_group_event):
    """作用域的门禁对命令同样生效。"""

    async def body():
        seen: list[str] = []
        only_here = Scope(prefix="admin", depends=[lambda event: None])

        @only_here.command("ping")
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event("admin ping"))
        assert seen == ["pong"]

        only_here.enabled = False
        await dispatch(make_group_event("admin ping"))
        assert seen == ["pong"]

    scenario(body)


def test_command_or_branches_share_one_handler(scenario, make_group_event):
    async def body():
        seen: list[object] = []

        @on_command(Cmd("kick", AtArg("t")) | Cmd("kick", IntArg("t")))
        async def kick(event, t):
            seen.append(t)

        await dispatch(make_group_event(segments=[Text("kick "), At(user_id=3)]))
        await dispatch(make_group_event("kick 3"))
        assert len(seen) == 2
        assert seen[1] == 3

    scenario(body)


# ── 没匹配上时看得见 ───────────────────────────────────────


def test_command_miss_reaches_log_and_user(scenario, make_group_event, caplog):
    """命令词对上了但参数不对：日志记 warning，用户收到用法提示。

    以前这里只有一条 debug，默认 INFO 级别的控制台什么都看不到 ——
    「我的命令怎么不响应」无从查起。
    """

    async def body():
        answered: list[str] = []

        @on_command("ban", AtArg("target"))
        async def ban(event, target):
            answered.append("handler 不该被执行")

        event = make_group_event("ban")
        with caplog.at_level(logging.WARNING, logger="novalark"):
            await dispatch(event)

        assert answered == []
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ban" in text and "@某人" in text for text in warnings)
        # 用户当场就知道自己少打了什么
        assert [str(item) for item in event.bot.sent] == ["ban：target 应该是一个 @某人"]

    scenario(body)


def test_command_hint_can_be_replaced(scenario, make_group_event):
    """默认行为方便，但插件要能整体换掉。"""

    async def body():
        seen: list[tuple[str, str]] = []

        async def mine(event, command, reason):
            seen.append((command.display, reason))

        set_miss_handler(mine)
        try:

            @on_command("ban", AtArg("target"))
            async def ban(event, target):
                raise AssertionError("不该执行")

            event = make_group_event("ban")
            await dispatch(event)
        finally:
            set_miss_handler(None)

        assert seen == [("ban", "target 应该是一个 @某人")]
        assert event.bot.sent == []  # 换了 handler，框架不再自动回话

    scenario(body)


def test_claimed_message_does_not_report_a_miss(scenario, make_group_event, caplog):
    """认领成功时不该冒出「没认领」的噪音。"""

    async def body():
        seen: list[int] = []

        @on_command("ban", AtArg("target"))
        async def ban(event, target):
            seen.append(target.user_id)

        with caplog.at_level(logging.INFO, logger="novalark"):
            await dispatch(make_group_event(segments=[Text("ban "), At(user_id=5)]))

        assert seen == [5]
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        assert not any("没认领" in text for text in infos)

    scenario(body)


def test_a_stopping_command_keeps_its_claim(scenario, make_group_event):
    """命令 handler 抛 ``StopPropagation`` 之后，独占权仍然是它的。

    「一条消息只会触发一条命令」不该因为那句 StopPropagation 就失效——它只是让
    **本插件**后面的订阅别再跑，不是放弃认领。少了这一步，同名命令会被第二个插件
    再跑一遍。
    """
    from novalark.matcher.errors import PropagationScope, StopPropagation

    async def body():
        seen: list[str] = []

        def register(module: str, label: str, *, stop: bool):
            async def handler(event):
                seen.append(label)
                if stop:
                    raise StopPropagation(PropagationScope.PLUGIN)

            handler.__module__ = module  # 插件归属靠函数自带的模块名
            handler.__qualname__ = f"{label}_handler"
            return on_command("ping")(handler)

        register("plugins.Alpha", "alpha", stop=True)
        register("plugins.Beta", "beta", stop=False)

        await dispatch(make_group_event("ping"))

        assert seen == ["alpha"]

    scenario(body)
