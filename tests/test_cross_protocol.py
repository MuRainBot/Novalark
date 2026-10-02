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
    GroupRole,
    Image,
    ImageSubType,
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


# ── 群请求：同一语义，两家必须落到同一个事件类 ─────────────
#
# 事件按**语义**分，不按协议的事件名：Milky 的 ``group_invited_join_request``
# 和 OneBot 的 ``sub_type=add`` 都是「有人要进群」，得落到同一个类；
# ``group_invitation``（机器人自己被邀请）才是另一件事。


def _ob_group_request(sub_type: str, **data: Any):
    return ob_decode(
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": sub_type,
            "time": 1,
            "self_id": 10001,
            "group_id": 12345,
            "user_id": 999,
            "comment": "",
            "flag": "f",
            **data,
        }
    )


def _mk_notice(event_type: str, **data: Any):
    return milky_decode(
        {"time": 1, "self_id": 10001, "event_type": event_type, "data": data}
    )


_GROUP_REQUESTS = [
    (
        "有人要进群",
        lambda: _ob_group_request("add"),
        lambda: _mk_notice(
            "group_join_request", group_id=12345, initiator_id=999, notification_seq=7
        ),
    ),
    (
        "群成员邀请他人入群",
        # OneBot 没有独立表示，也是 sub_type=add
        lambda: _ob_group_request("add"),
        lambda: _mk_notice(
            "group_invited_join_request",
            group_id=12345,
            initiator_id=888,
            target_user_id=999,
            notification_seq=7,
        ),
    ),
    (
        "机器人自己被邀请进群",
        lambda: _ob_group_request("invite"),
        lambda: _mk_notice(
            "group_invitation", group_id=12345, initiator_id=999, invitation_seq=7
        ),
    ),
]


@pytest.mark.parametrize(
    "concept, make_ob, make_mk", _GROUP_REQUESTS, ids=[c[0] for c in _GROUP_REQUESTS]
)
def test_a_group_request_lands_on_the_same_event(concept, make_ob, make_mk):
    """同一个 QQ 语义，两家必须落到同一个事件类。

    事件分类按**语义**定，不按协议的事件名 —— 否则插件得为每个协议端写一套
    分支，``@on(GroupJoinRequestEvent)`` 这种写法就失去意义了。
    """
    from_ob = make_ob()
    from_mk = make_mk()

    assert not isinstance(from_ob, UnknownEvent), concept
    assert not isinstance(from_mk, UnknownEvent), concept
    assert type(from_ob) is type(from_mk), concept


def test_image_sub_type_is_normalised_across_protocols():
    """图片 ``sub_type`` 统一用 Milky 的语义值。

    OneBot 标准协议根本没有这个字段（go-cqhttp 等扩展成了 int），这边只按
    标准做，就不采信实现端塞进来的值。
    """
    sticker = mk(
        [
            {
                "type": "image",
                "data": {"resource_id": "r1", "sub_type": "sticker", "summary": "[表情]"},
            }
        ]
    ).content.segments[0]
    assert sticker.sub_type is ImageSubType.STICKER

    plain = ob(
        [{"type": "image", "data": {"file": "a.jpg", "sub_type": 1}}]
    ).content.segments[0]
    assert plain.sub_type is ImageSubType.NORMAL


def test_a_value_the_protocol_hasnt_taught_us_yet_still_decodes():
    """协议加了新取值，不能把整个段打成未知类型。

    取值集是协议定的，但协议会加值。枚举是开放的：没收录的值原样收下，
    段还是图片段，原文也留在 ``raw`` 里。
    """
    event = mk(
        [{"type": "image", "data": {"resource_id": "r1", "sub_type": "animated"}}]
    )
    image = event.content.segments[0]

    assert isinstance(image, Image)
    assert image.sub_type == "animated"
    assert image.summary == ""
    assert image.raw["data"]["sub_type"] == "animated"


def test_the_same_concept_uses_the_same_enum_in_both_protocols():
    """枚举也是「一个概念一个类」：两家的 ``role`` 落到同一个类型。

    各写各的枚举就得在两边的值之间来回折算，插件也得跟着判断是哪个协议。
    """
    from_ob = ob_decode(
        {**_OB_BASE, "sender": {"user_id": 999, "nickname": "小明", "role": "admin"}}
    )
    from_mk = milky_decode(
        {
            "time": 1,
            "self_id": 10001,
            "event_type": "message_receive",
            "data": {
                "message_scene": "group",
                "peer_id": 12345,
                "message_seq": 42,
                "sender_id": 999,
                "group_member": {"nickname": "小明", "role": "admin"},
                "segments": [],
            },
        }
    )

    assert from_ob.sender.role is GroupRole.ADMIN
    assert from_mk.sender.role is GroupRole.ADMIN
    assert from_ob.sender.is_admin and from_mk.sender.is_admin


def test_cross_protocol_media_drops_the_other_sides_handles():
    """转发别人的图，不能把对方的入站字段一起吐出去。

    OneBot 的 ``file`` / ``cache``、Milky 的 ``resource_id`` 都不是收方协议的
    出站字段，协议端不认。
    """
    from novalark.adapter.milky.encode import encode_segments as mk_encode
    from novalark.adapter.onebot_v11.encode import encode_segments as ob_encode

    # OneBot 收的图（带实现端扩展）→ 发到 Milky
    ob_content = ob(
        [
            {
                "type": "image",
                "data": {"file": "a.jpg", "url": "http://x/y.jpg", "sub_type": 0, "cache": 1},
            }
        ]
    ).content
    ob_content.segments[0].uri = "http://x/y.jpg"  # 模拟句柄已解析
    to_milky = mk_encode(ob_content)[0]["data"]
    assert set(to_milky) <= {"uri", "sub_type", "summary"}, to_milky

    # Milky 收的图 → 发到 OneBot
    mk_content = mk(
        [{"type": "image", "data": {"resource_id": "r1", "sub_type": "sticker"}}]
    ).content
    mk_content.segments[0].uri = "http://x/y.jpg"
    to_onebot = ob_encode(mk_content)[0]["data"]
    assert "resource_id" not in to_onebot, to_onebot
    assert "sub_type" not in to_onebot, to_onebot
