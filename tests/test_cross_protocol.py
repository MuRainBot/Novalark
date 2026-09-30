"""跨协议一致性。

同一个 QQ 语义在两家 decode 之后必须长得一样 —— 「模型层对协议字段名零
知识」这条原则坏掉的时候，只有这里会响。
"""

from __future__ import annotations

from typing import Any

import pytest

from novalark.adapter.milky.decode import decode_event as milky_decode
from novalark.adapter.milky.segments import MilkyForward
from novalark.adapter.onebot_v11.decode import decode_event as ob_decode
from novalark.adapter.onebot_v11.segments import OneBotForward
from novalark.model import (
    At,
    Face,
    Forward,
    ForwardNode,
    Image,
    LightApp,
    Reply,
    Text,
    UnknownEvent,
    UnknownSegment,
    Xml,
)

_OB_BASE: dict[str, Any] = {
    "post_type": "message",
    "message_type": "group",
    "sub_type": "normal",
    "time": 1,
    "self_id": 10001,
    "group_id": 12345,
    "user_id": 999,
    "message_id": 42,
    "sender": {"user_id": 999, "nickname": "小明"},
}


def ob(segments: list[dict[str, Any]]):
    """喂一条段数组给 OneBot 的 decode。"""
    return ob_decode({**_OB_BASE, "message": segments})


def mk(segments: list[dict[str, Any]]):
    """喂一条段数组给 Milky 的 decode。"""
    return milky_decode(
        {
            "time": 1,
            "self_id": 10001,
            "event_type": "message_receive",
            "data": {
                "message_scene": "group",
                "peer_id": 12345,
                "message_seq": 42,
                "sender_id": 999,
                "segments": segments,
            },
        }
    )


# ── 同一个概念，两家协议写法不同 ───────────────────────────
#
# 模型类 + 两边的协议原文。任何一边把协议名漏进 Segment.type，这张表就会红。

_CONCEPTS = [
    ("文本", Text, {"type": "text", "data": {"text": "hi"}}, {
        "type": "text", "data": {"text": "hi"}
    }),
    ("@某人", At, {"type": "at", "data": {"qq": "10001"}}, {
        "type": "mention", "data": {"user_id": 10001}
    }),
    ("@全体", At, {"type": "at", "data": {"qq": "all"}}, {
        "type": "mention_all", "data": {}
    }),
    ("表情", Face, {"type": "face", "data": {"id": "1"}}, {
        "type": "face", "data": {"face_id": "1"}
    }),
    ("小程序", LightApp, {"type": "json", "data": {"data": "{}"}}, {
        "type": "light_app", "data": {"json_payload": "{}"}
    }),
    ("XML", Xml, {"type": "xml", "data": {"data": "<a/>"}}, {
        "type": "xml", "data": {"xml_payload": "<a/>"}
    }),
    ("图片", Image, {"type": "image", "data": {"file": "a.jpg"}}, {
        "type": "image", "data": {"resource_id": "res-1"}
    }),
    ("转发", Forward, {"type": "forward", "data": {"id": "f1"}}, {
        "type": "forward", "data": {"forward_id": "f1"}
    }),
    ("回复", Reply, {"type": "reply", "data": {"id": "7"}}, {
        "type": "reply", "data": {"message_seq": 7}
    }),
]


@pytest.mark.parametrize(
    "concept, model, ob_seg, mk_seg", _CONCEPTS, ids=[c[0] for c in _CONCEPTS]
)
def test_a_concept_lands_on_the_same_segment(concept, model, ob_seg, mk_seg):
    """两家的段名不一样，模型层的 ``Segment.type`` 必须一样。

    从 OneBot 解出的 LightApp 曾经带着 ``type == "json"``，Milky 那边是
    ``"light_app"``；``At`` 一边是 ``"at"`` 一边是 ``"mention"``。
    """
    from_ob = ob([ob_seg]).content.segments[0]
    from_mk = mk([mk_seg]).content.segments[0]

    assert not isinstance(from_ob, UnknownSegment), concept
    assert not isinstance(from_mk, UnknownSegment), concept
    # 具体子类各家自己定义（转发要各自实现取内容），语义类必须一致
    assert isinstance(from_ob, model), concept
    assert isinstance(from_mk, model), concept
    assert from_ob.type == from_mk.type, concept


@pytest.mark.parametrize(
    "seg",
    [
        {"type": "face", "data": {"id": 123}},
        {"type": "poke", "data": {"type": "126", "id": 2003}},
        {"type": "music", "data": {"type": "qq", "id": 123456}},
    ],
    ids=["face", "poke", "music"],
)
def test_a_numeric_field_does_not_swallow_the_message(seg):
    """协议端给 int 是常态，而 pydantic v2 不做 int→str。

    以前这里整条消息会变成 UnknownEvent：插件收不到事件，日志里只有一条
    debug —— 「用户 @ 了机器人但机器人不理」的一类来源。
    """
    event = ob([seg])

    assert not isinstance(event, UnknownEvent)
    assert not isinstance(event.content.segments[0], UnknownSegment)


def test_milky_has_the_same_numeric_hole():
    """Milky 不是安全的 —— market_face 整份 fields 原样摊进去。"""
    event = mk([{"type": "market_face", "data": {"emoji_id": 5, "emoji_package_id": 1}}])

    assert not isinstance(event, UnknownEvent)
    assert not isinstance(event.content.segments[0], UnknownSegment)


def test_forward_fetch_reaches_the_adapter(scenario):
    """``Forward.fetch()`` 要真的取到内容。

    基类的 ``_load`` 恒抛 ``UnresolvableUri``，而两家的 ``get_forwarded``
    早就实现了却没人调 —— 文档承诺的 fetch() 曾是空头支票。
    """

    async def body():
        from novalark.adapter.bot import set_current_bot

        class FakeBot:
            self_id = 10001

            async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
                assert forward_id == "f1"
                return [ForwardNode(user_id=1, name="小明", content="第一句")]

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        cases = [
            (ob([{"type": "forward", "data": {"id": "f1"}}]), OneBotForward),
            (mk([{"type": "forward", "data": {"forward_id": "f1"}}]), MilkyForward),
        ]
        for event, expected in cases:
            forward = event.content.segments[0]
            assert isinstance(forward, expected)
            assert forward.messages == []  # 收到的是引用形态
            nodes = await forward.fetch()
            assert [node.name for node in nodes] == ["小明"]

    scenario(body)


def test_forward_fetch_on_own_content_skips_the_request(scenario):
    """自己构造的转发内容已经在手上，不该再发请求。"""

    async def body():
        from novalark.adapter.bot import set_current_bot

        class FakeBot:
            self_id = 10001

            async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
                raise AssertionError("手上有内容还去请求了")

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        forward = Forward(messages=[ForwardNode(user_id=1, name="小明", content="第一句")])
        assert [node.name for node in await forward.fetch()] == ["小明"]

    scenario(body)
