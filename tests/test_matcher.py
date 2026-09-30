"""订阅层：匹配、优先级、规则、依赖注入、阻断。"""

from __future__ import annotations

from novalark.matcher import (
    Depend,
    Depends,
    FromGroup,
    NotMatched,
    PropagationScope,
    Scope,
    StopPropagation,
    ToMe,
    build_plan,
    depends,
    dispatch,
    on,
    registry,
)
from novalark.matcher.subscription import Subscription
from novalark.model import (
    At,
    GroupMessageEvent,
    MessageEvent,
    MessageRef,
    RichText,
    Text,
)
from novalark.plugin import Plugin
from novalark.runtime import task


async def _pump():
    import asyncio

    await asyncio.sleep(0.02)


def test_subclass_matching(scenario, make_group_event):
    async def body():
        seen = []

        @on(MessageEvent)
        async def any_message(event):
            seen.append(("any", event.plain_text))

        @on(GroupMessageEvent)
        async def only_group(event):
            seen.append(("group", event.group_id))

        await dispatch(make_group_event("hello"))
        assert seen == [("any", "hello"), ("group", 12345)]

    scenario(body)


def test_priority_is_one_global_axis(scenario, make_group_event):
    async def body():
        seen = []

        @on(MessageEvent, priority=0)
        async def low(event):
            seen.append("low")

        @on(MessageEvent, priority=100)
        async def high(event):
            seen.append("high")

        await dispatch(make_group_event())
        assert seen == ["high", "low"]

    scenario(body)


def test_depends_are_gates(scenario, make_group_event):
    async def body():
        seen = []

        @depends
        def is_long(event) -> None:
            if len(event.plain_text) <= 3:
                raise NotMatched

        @on(MessageEvent, depends=[is_long])
        async def only_long(event):
            seen.append(event.plain_text)

        await dispatch(make_group_event("hi"))
        assert seen == []
        await dispatch(make_group_event("hello"))
        assert seen == ["hello"]

    scenario(body)


def test_depend_combinators(scenario, make_group_event):
    async def body():
        seen = []

        @depends
        def is_long(event) -> None:
            if len(event.plain_text) <= 3:
                raise NotMatched

        @depends
        def in_group(event) -> None:
            if event.group_id != 12345:
                raise NotMatched

        @on(MessageEvent, depends=[is_long & in_group])
        async def both(event):
            seen.append("and")

        @on(MessageEvent, depends=[is_long | in_group])
        async def either(event):
            seen.append("or")

        @on(MessageEvent, depends=[~is_long])
        async def short_only(event):
            seen.append("not")

        await dispatch(make_group_event("hi"))  # 短消息、命中群
        assert seen == ["or", "not"]

    scenario(body)


def test_depends_binds_by_declaration_and_caches(scenario, make_group_event):
    async def body():
        seen = []

        class GetMember(Depend):
            calls = 0

            async def __call__(self, event):
                GetMember.calls += 1
                return f"member-{event.sender.user_id}"

        shared = GetMember()

        @on(MessageEvent)
        async def handler(
            event, who: str = Depends(shared), again: str = Depends(shared)
        ):
            seen.append((who, again))

        await dispatch(make_group_event())
        assert seen == [("member-999", "member-999")]
        assert GetMember.calls == 1  # 同一次事件里同一个依赖只求值一次

    scenario(body)


def test_dependency_not_matched_only_skips_itself(scenario, make_group_event):
    async def body():
        seen = []

        class Never(Depend):
            async def __call__(self, event):
                raise NotMatched

        @on(MessageEvent)
        async def skipped(event, x=Depends(Never())):
            seen.append("不该执行")

        @on(MessageEvent)
        async def after(event):
            seen.append("后面的照跑")

        await dispatch(make_group_event())
        assert seen == ["后面的照跑"]

    scenario(body)


def test_sync_handler_bridges_with_task(scenario, make_group_event):
    async def body():
        seen = []

        class FakeBot:
            self_id = 10001

            def send(self, conversation, content):
                async def _send():
                    import asyncio

                    await asyncio.sleep(0.01)
                    return MessageRef(conversation=conversation, seq=7)

                return _send()

        @on(MessageEvent)
        def sync_handler(event):
            result = task(
                event.bot.send(event.conversation, RichText(Text("回复")))
            ).result()
            seen.append(result.seq)

        await dispatch(make_group_event(bot=FakeBot()))
        assert seen == [7]

    scenario(body)


def test_handler_exception_is_isolated(scenario, make_group_event):
    async def body():
        seen = []

        @on(MessageEvent, priority=10)
        async def boom(event):
            raise RuntimeError("炸了")

        @on(MessageEvent, priority=0)
        async def survivor(event):
            seen.append("活着")

        await dispatch(make_group_event())
        assert seen == ["活着"]

    scenario(body)


def test_stop_propagation_global(scenario, make_group_event):
    async def body():
        seen = []

        @on(MessageEvent, priority=10, depends=[ToMe()])
        async def blocker(event):
            seen.append("blocker")
            raise StopPropagation()

        @on(MessageEvent, priority=0)
        async def never(event):
            seen.append("不该执行")

        await dispatch(make_group_event(segments=[At(user_id=10001), Text(" hi")]))
        assert seen == ["blocker"]

    scenario(body)


def test_stop_propagation_plugin_scope(scenario, make_group_event):
    async def body():
        seen = []
        plugin_a, plugin_b = Plugin("a"), Plugin("b")

        def subscribe(handler, plugin, priority=0):
            registry.add(
                Subscription(
                    event_type=MessageEvent,
                    handler=handler,
                    plan=build_plan(handler),
                    priority=priority,
                    plugin=plugin,
                )
            )

        async def a_stop(event):
            seen.append("a-stop")
            raise StopPropagation(PropagationScope.PLUGIN)

        async def a_after(event):
            seen.append("a-after")

        async def b_other(event):
            seen.append("b-other")

        subscribe(a_stop, plugin_a, priority=10)
        subscribe(a_after, plugin_a, priority=5)
        subscribe(b_other, plugin_b, priority=0)

        await dispatch(make_group_event())
        assert seen == ["a-stop", "b-other"]

    scenario(body)


def test_plugin_attribution_by_module_name(scenario):
    # 归属靠函数自带的模块名，不依赖加载上下文
    assert registry.plugin_of("__main__") is None
    plugin = registry.plugin_of("plugins.echo")
    assert plugin is not None and plugin.name == "echo"
    assert registry.plugin_of("plugins.echo.helpers") is plugin


def test_emit_is_fire_and_forget(scenario, make_group_event):
    """emit 不阻塞调用方。"""
    from novalark.matcher import emit

    async def body():
        seen = []

        @on(MessageEvent)
        async def handler(event):
            seen.append(event.plain_text)

        emit(make_group_event("异步"))
        assert seen == []  # 还没跑
        await _pump()
        assert seen == ["异步"]

    scenario(body)


def test_scope_gate_applies_to_every_child(scenario, make_group_event):
    """作用域的门禁对挂在它下面的所有订阅生效。"""

    async def body():
        seen = []
        here = Scope(depends=[FromGroup(12345)])

        @here.on(MessageEvent)
        async def inside(event):
            seen.append("inside")

        @on(MessageEvent)
        async def outside(event):
            seen.append("outside")

        await dispatch(make_group_event("hi"))
        assert seen == ["inside", "outside"]

        seen.clear()
        await dispatch(make_group_event("hi", group_id=999))
        assert seen == ["outside"]

    scenario(body)


def test_scope_nesting_accumulates(scenario, make_group_event):
    async def body():
        seen = []
        outer = Scope(depends=[FromGroup(12345)])
        inner = outer.scope(depends=[ToMe()])

        @inner.on(MessageEvent)
        async def handler(event):
            seen.append("hit")

        await dispatch(make_group_event("hi"))  # 没 @，内层拦下
        assert seen == []

        await dispatch(make_group_event(segments=[At(user_id=10001), Text(" 在")]))
        assert seen == ["hit"]

    scenario(body)


def test_scope_priority_is_inherited(scenario, make_group_event):
    async def body():
        seen = []
        high = Scope(priority=100)

        @on(MessageEvent, priority=0)
        async def low(event):
            seen.append("low")

        @high.on(MessageEvent)
        async def high_one(event):
            seen.append("high")

        await dispatch(make_group_event())
        assert seen == ["high", "low"]

    scenario(body)


def test_scope_is_mutable_at_runtime(scenario, make_group_event):
    async def body():
        seen = []
        admin = Scope()

        @admin.on(MessageEvent)
        async def handler(event):
            seen.append("hit")

        await dispatch(make_group_event())
        assert seen == ["hit"]

        admin.add(FromGroup(999))  # 加一条不满足的门禁
        await dispatch(make_group_event())
        assert len(seen) == 1

        admin.enabled = False
        await dispatch(make_group_event())
        assert len(seen) == 1

    scenario(body)
