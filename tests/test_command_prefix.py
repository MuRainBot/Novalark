"""命令起始符，以及 @机器人 / 引用回复段的自动剥离。"""

from __future__ import annotations

import pytest

import novalark.config as config_module
from novalark import (
    At,
    ConversationRef,
    MessageRef,
    MessageScene,
    Reply,
    RichText,
    Segment,
    Text,
    on_command,
)
from novalark.command.parser import tokenize
from novalark.command.strip import strip_leading
from novalark.config import CoreConfig, Settings
from novalark.matcher import dispatch

BOT = 10001


@pytest.fixture
def set_command_config():
    """覆盖命令前缀配置。

    conftest 的 autouse fixture 把测试环境配成裸词模式；这个 fixture 用来换
    成别的模式，默认换成真实的 ``/``。
    """

    def _set(*, starts=("/",), at=True, reply=True):
        config_module._settings = Settings(
            core=CoreConfig(
                command_starts=frozenset(starts),
                command_at=at,
                command_reply=reply,
            )
        )

    return _set


def _tokens(*segments: Segment) -> list[Segment]:
    return tokenize(RichText(*segments))


def _texts(segments) -> list[str]:
    return [seg.text for seg in segments if isinstance(seg, Text)]


# ── strip_leading：token 层 ────────────────────────────────


def test_mention_only():
    result = strip_leading(
        _tokens(At(user_id=BOT), Text(" ping")), self_id=BOT, starts={"/"}
    )
    assert result.mentioned
    assert not result.prefixed
    assert result.triggered
    assert _texts(result.tokens) == ["ping"]


def test_start_only():
    result = strip_leading(_tokens(Text("/ping")), self_id=BOT, starts={"/"})
    assert result.prefixed
    assert not result.mentioned
    assert _texts(result.tokens) == ["ping"]


def test_start_may_be_separated_by_space():
    """``/ ping`` 和 ``/ping`` 一样认。"""
    result = strip_leading(_tokens(Text("/ ping")), self_id=BOT, starts={"/"})
    assert result.prefixed
    assert _texts(result.tokens) == ["ping"]


def test_mention_and_start_stack():
    """``@bot /ping``：两个都剥，顺序无关。"""
    result = strip_leading(
        _tokens(At(user_id=BOT), Text(" /ping")), self_id=BOT, starts={"/"}
    )
    assert result.mentioned and result.prefixed
    assert result.triggered
    assert _texts(result.tokens) == ["ping"]


def test_reply_then_start():
    result = strip_leading(
        _tokens(Reply(), Text("/ping")), self_id=BOT, starts={"/"}
    )
    assert result.replied and result.prefixed
    assert result.triggered
    assert _texts(result.tokens) == ["ping"]


def test_reply_alone_does_not_trigger():
    """引用回复只是被忽略，不构成"在调命令"。"""
    result = strip_leading(_tokens(Reply(), Text("ping")), self_id=BOT, starts={"/"})
    assert result.replied
    assert not result.triggered


def test_mention_not_at_the_head_does_not_count():
    result = strip_leading(
        _tokens(Text("ping "), At(user_id=BOT)), self_id=BOT, starts={"/"}
    )
    assert not result.mentioned
    assert not result.triggered


def test_mention_of_someone_else_does_not_count():
    result = strip_leading(
        _tokens(At(user_id=5), Text(" ping")), self_id=BOT, starts={"/"}
    )
    assert not result.mentioned
    assert not result.triggered


def test_at_all_is_not_the_bot():
    result = strip_leading(
        _tokens(At.all(), Text(" ping")), self_id=BOT, starts={"/"}
    )
    assert not result.mentioned


def test_unknown_self_id_never_matches_a_mention():
    """机器人还没连上时 ``self_id`` 是 0，不能剥掉任何 @。"""
    result = strip_leading(
        _tokens(At(user_id=BOT), Text(" ping")), self_id=0, starts={"/"}
    )
    assert not result.mentioned
    assert not result.triggered


def test_empty_starts_leaves_only_mention():
    result = strip_leading(_tokens(Text("ping")), self_id=BOT, starts=set())
    assert not result.triggered

    result = strip_leading(
        _tokens(At(user_id=BOT), Text(" ping")), self_id=BOT, starts=set()
    )
    assert result.triggered


def test_blank_start_is_bare_word_mode():
    """``{""}``：前缀检查恒通过，也就是退化成裸词匹配。"""
    result = strip_leading(_tokens(Text("ping")), self_id=BOT, starts={""})
    assert result.prefixed
    assert result.triggered
    assert _texts(result.tokens) == ["ping"]


def test_longer_start_wins():
    """``!!ping`` 应该按 ``!!`` 剥，而不是被 ``!`` 抢走。"""
    result = strip_leading(_tokens(Text("!!ping")), self_id=BOT, starts={"!", "!!"})
    assert _texts(result.tokens) == ["ping"]


def test_disabled_mention_is_not_stripped():
    result = strip_leading(
        _tokens(At(user_id=BOT), Text(" ping")),
        self_id=BOT,
        starts={"/"},
        at=False,
    )
    assert not result.mentioned
    assert not result.triggered


def test_disabled_reply_blocks_the_command():
    """关掉引用回复支持后，Reply 会挡在命令词前面，带引用的命令就不认了。"""
    result = strip_leading(
        _tokens(Reply(), Text("/ping")),
        self_id=BOT,
        starts={"/"},
        reply=False,
    )
    assert not result.replied
    assert not result.triggered
    assert _texts(result.tokens) == ["/ping"]


# ── RichText.strip_leading：段层 ───────────────────────────


def test_richtext_strips_segments_and_start():
    content = RichText(At(user_id=BOT), Text(" /ping"))
    stripped = content.strip_leading(At, starts="/")
    assert _texts(stripped.segments) == ["ping"]


def test_richtext_does_not_touch_the_original():
    content = RichText(At(user_id=BOT), Text(" ping"))
    content.strip_leading(At)
    assert len(content.segments) == 2


def test_richtext_skips_blank_before_segment():
    content = RichText(Text("  "), At(user_id=BOT), Text(" ping"))
    stripped = content.strip_leading(At)
    assert _texts(stripped.segments) == ["ping"]


def test_richtext_without_starts_only_drops_segments():
    content = RichText(Reply(), Text("/ping"))
    stripped = content.strip_leading(Reply)
    assert _texts(stripped.segments) == ["/ping"]


# ── 端到端：dispatch ───────────────────────────────────────


def test_mention_and_start_reach_the_same_command(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event("/ping"))
        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" ping")]))
        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" /ping")]))
        assert seen == ["pong", "pong", "pong"]

        # 裸词不行了：没有 @ 就必须带起始符
        await dispatch(make_group_event("ping"))
        assert seen == ["pong", "pong", "pong"]

    scenario(body)


def test_reply_does_not_block_a_command(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append("pong")

        conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
        reply = Reply(ref=MessageRef(conversation=conversation, seq=7))

        await dispatch(make_group_event(segments=[reply, Text("/ping")]))
        assert seen == ["pong"]

        # 只有引用回复、既没 @ 也没起始符 → 不触发
        await dispatch(make_group_event(segments=[reply, Text("ping")]))
        assert seen == ["pong"]

    scenario(body)


def test_reply_and_mention_and_start_all_at_once(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append("pong")

        conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
        reply = Reply(ref=MessageRef(conversation=conversation, seq=7))

        await dispatch(
            make_group_event(segments=[reply, At(user_id=BOT), Text(" /ping")])
        )
        assert seen == ["pong"]

    scenario(body)


def test_raw_message_survives_stripping(
    scenario, make_group_event, set_command_config
):
    """handler 拿到的 event 必须还是原始消息，剥离不能动它。"""
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append(event.plain_text)

        event = make_group_event(segments=[At(user_id=BOT), Text(" /ping")])
        await dispatch(event)

        assert seen == [" /ping"]
        assert len(event.content.segments) == 2

    scenario(body)


def test_unescaped_word_is_silent(
    scenario, make_group_event, set_command_config
):
    """@ 了机器人但命令词不对：不算调命令，不提示。"""
    async def body():
        set_command_config()

        @on_command("ping")
        async def ping(event):  # pragma: no cover - 不该执行
            raise AssertionError("不该执行")

        event = make_group_event(segments=[At(user_id=BOT), Text(" pong")])
        await dispatch(event)
        assert event.bot.sent == []

    scenario(body)


def test_trailing_junk_reports_a_hint(
    scenario, make_group_event, set_command_config
):
    """命令词对上了但有多余内容：回一条用法提示。"""
    async def body():
        set_command_config()

        @on_command("ping")
        async def ping(event):  # pragma: no cover - 不该执行
            raise AssertionError("不该执行")

        event = make_group_event(segments=[At(user_id=BOT), Text(" ping 多余")])
        await dispatch(event)
        assert len(event.bot.sent) == 1
        assert "多余" in str(event.bot.sent[0])

    scenario(body)


def test_bare_word_mode_restores_plain_matching(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config(starts=("",))
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event("ping"))
        assert seen == ["pong"]

        # @ 和引用回复照样剥
        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" ping")]))
        assert seen == ["pong", "pong"]

    scenario(body)


def test_closed_starts_need_a_mention(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config(starts=())
        seen: list[str] = []

        @on_command("ping")
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event("/ping"))
        assert seen == []

        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" ping")]))
        assert seen == ["pong"]

    scenario(body)


def test_per_command_override(scenario, make_group_event, set_command_config):
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping", starts="!")
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event("!ping"))
        assert seen == ["pong"]

        # 覆盖之后，全局的 `/` 对这条命令不再生效
        await dispatch(make_group_event("/ping"))
        assert seen == ["pong"]

        # 但 @机器人 仍然有效
        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" ping")]))
        assert seen == ["pong", "pong"]

    scenario(body)


def test_per_command_override_can_disable_a_mention(
    scenario, make_group_event, set_command_config
):
    async def body():
        set_command_config()
        seen: list[str] = []

        @on_command("ping", at=False)
        async def ping(event):
            seen.append("pong")

        await dispatch(make_group_event(segments=[At(user_id=BOT), Text(" ping")]))
        assert seen == []

        await dispatch(make_group_event("/ping"))
        assert seen == ["pong"]

    scenario(body)


def test_scope_prefix_stacks_with_the_start(
    scenario, make_group_event, set_command_config
):
    """``Scope`` 的命令词前缀和起始符是两回事，得能叠加。"""
    from novalark import Scope

    async def body():
        set_command_config()
        seen: list[str] = []
        admin = Scope(prefix="admin")

        @admin.command("ban")
        async def ban(event):
            seen.append("banned")

        await dispatch(make_group_event("/admin ban"))
        assert seen == ["banned"]

        # 少了起始符就不认
        await dispatch(make_group_event("admin ban"))
        assert seen == ["banned"]

    scenario(body)


# ── 配置默认值 ─────────────────────────────────────────────


def test_default_command_starts_is_slash():
    core = CoreConfig()
    assert core.command_starts == frozenset({"/"})
    assert core.command_at is True
    assert core.command_reply is True


def test_command_starts_parses_from_yaml_shape():
    settings = Settings.model_validate({"core": {"command_starts": ["/", "!"]}})
    assert settings.core.command_starts == frozenset({"/", "!"})

    settings = Settings.model_validate({"core": {"command_starts": []}})
    assert settings.core.command_starts == frozenset()
