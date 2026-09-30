"""业务日志：哪些事该在控制台上看得见。

控制台默认 INFO，文件始终记全量（``log.py``）。这里盯的是"常见动作有没有
一句看得懂的话"，而不是日志格式本身。
"""

from __future__ import annotations

import logging

from novalark import (
    Bot,
    ClientStatusEvent,
    ConversationRef,
    Event,
    HeartbeatEvent,
    MessageRef,
    MessageScene,
    on_command,
)
from novalark.matcher import dispatch


class _FakeAdapter:
    """只够 ``Bot`` 用：记下发了什么，不真发。"""

    name = "fake"

    async def send_message(self, conversation, content, *, reply_to=None):
        return MessageRef(conversation=conversation, seq=1)


def _infos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]


# ── 模型层：给日志用的可读串 ───────────────────────────────


def test_notable_defaults_to_true():
    """默认都值得记 info，高频的才降级。"""
    assert Event.notable is True
    assert HeartbeatEvent.notable is False
    assert ClientStatusEvent.notable is False


def test_conversation_display_is_readable():
    def ref(scene, peer):
        return ConversationRef(scene=scene, peer_id=peer)

    assert ref(MessageScene.GROUP, 12345).display == "群 12345"
    assert ref(MessageScene.FRIEND, 999).display == "好友 999"
    assert ref(MessageScene.TEMP, 5).display == "临时会话 5"


def test_message_summary_is_readable(make_group_event):
    event = make_group_event("你好", group_id=12345)

    assert "群 12345" in event.summary
    assert "你好" in event.summary


# ── 收到事件 ───────────────────────────────────────────────


def test_received_message_is_logged_at_info(scenario, make_group_event, caplog):
    async def body():
        with caplog.at_level(logging.INFO, logger="novalark"):
            await dispatch(make_group_event("你好", group_id=12345))

        assert any("收到" in text and "群 12345" in text for text in _infos(caplog))

    scenario(body)


def test_heartbeat_stays_out_of_the_console(scenario, caplog):
    """心跳几秒一条，不该把控制台刷满。"""

    async def body():
        with caplog.at_level(logging.INFO, logger="novalark"):
            await dispatch(HeartbeatEvent())

        assert not any("收到" in text for text in _infos(caplog))

    scenario(body)


# ── 执行命令 ───────────────────────────────────────────────


def test_command_execution_is_logged_at_info(scenario, make_group_event, caplog):
    """handler 不回复时，这条是控制台上唯一的痕迹。"""

    async def body():
        @on_command("ping")
        async def ping(event):
            pass

        with caplog.at_level(logging.INFO, logger="novalark"):
            await dispatch(make_group_event("ping"))

        assert any("执行命令 ping" in text for text in _infos(caplog))

    scenario(body)


def test_plain_subscription_is_not_logged_at_info(
    scenario, make_group_event, caplog
):
    """普通订阅每条消息都跑，记 info 会淹没真正的动作。"""
    from novalark import GroupMessageEvent, on

    async def body():
        @on(GroupMessageEvent)
        async def watcher(event):
            pass

        with caplog.at_level(logging.INFO, logger="novalark"):
            await dispatch(make_group_event("你好"))

        assert not any("执行" in text for text in _infos(caplog))

    scenario(body)


# ── 发送消息 ───────────────────────────────────────────────


def test_send_is_logged_at_info(scenario, caplog):
    async def body():
        bot = Bot(_FakeAdapter())
        conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)

        with caplog.at_level(logging.INFO, logger="novalark"):
            await bot.send(conversation, "你好呀")

        assert any(
            "发送" in text and "群 12345" in text and "你好呀" in text
            for text in _infos(caplog)
        )

    scenario(body)


def test_reply_is_logged_at_info(scenario, caplog):
    async def body():
        bot = Bot(_FakeAdapter())
        conversation = ConversationRef(scene=MessageScene.FRIEND, peer_id=999)
        target = MessageRef(conversation=conversation, seq=7)

        with caplog.at_level(logging.INFO, logger="novalark"):
            await bot.reply(target, "在的")

        assert any(
            "回复" in text and "好友 999" in text and "在的" in text
            for text in _infos(caplog)
        )

    scenario(body)
