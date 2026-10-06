"""OneBot V11 适配器：注册表、解码、编码。"""

from __future__ import annotations

import pytest

from novalark import UnsupportedSegment
from novalark.adapter import get_adapter_entry
from novalark.adapter.onebot_v11.decode import decode_event
from novalark.adapter.onebot_v11.encode import encode_segments
from novalark.model import (
    At,
    Face,
    File,
    FriendRequestEvent,
    GroupMemberDecreaseEvent,
    GroupMuteEvent,
    GroupInviteRequestEvent,
    GroupJoinRequestEvent,
    GroupNudgeEvent,
    GroupRecallEvent,
    GroupWholeMuteEvent,
    Forward,
    ForwardNode,
    FriendMessageEvent,
    GroupMessageEvent,
    Image,
    MessageScene,
    RichText,
    TempMessageEvent,
    Poke,
    RichText as RT,
    Text,
    UnknownEvent,
    UnknownSegment,
    UnresolvableUri,
)

GROUP_MESSAGE = {
    "post_type": "message",
    "message_type": "group",
    "sub_type": "normal",
    "time": 1700000000,
    "self_id": 10001,
    "group_id": 12345,
    "user_id": 999,
    "message_id": 42,
    "message": [
        {"type": "text", "data": {"text": "hello "}},
        {"type": "at", "data": {"qq": "10001"}},
        {"type": "image", "data": {"url": "http://x/y.png"}},
    ],
    "sender": {"user_id": 999, "nickname": "小明", "card": "小明卡", "role": "admin"},
}


def test_registry_lookup():
    entry = get_adapter_entry("onebot_v11")
    assert entry.config_model.__name__ == "OneBotV11Config"
    assert entry.adapter_class.__name__ == "OneBotV11Adapter"

    with pytest.raises(KeyError, match="onebot_v11"):
        get_adapter_entry("onebot_v12")


def test_decode_group_message():
    event = decode_event(GROUP_MESSAGE)
    assert isinstance(event, GroupMessageEvent)
    assert event.group_id == 12345
    assert event.self_id == 10001
    assert event.kind == "group_message"
    assert event.plain_text == "hello "
    assert event.sender.display_name == "小明卡"
    assert event.sender.is_admin
    assert event.ref.seq == 42
    assert event.conversation.scene is MessageScene.GROUP


def test_decode_private_and_temp_are_distinct():
    friend = decode_event(
        {**GROUP_MESSAGE, "message_type": "private", "sub_type": "friend"}
    )
    assert isinstance(friend, FriendMessageEvent)
    assert friend.user_id == 999
    assert friend.conversation.scene is MessageScene.FRIEND

    # 群临时会话：peer 是用户，但场景必须和普通私聊分开
    temp = decode_event(
        {**GROUP_MESSAGE, "message_type": "private", "sub_type": "group"}
    )
    assert isinstance(temp, TempMessageEvent)
    assert temp.user_id == 999
    assert temp.conversation.scene is MessageScene.TEMP


def test_non_standard_post_type_degrades_to_unknown():
    """标准只有 message / notice / request / meta_event 四种 post_type。"""
    event = decode_event({**GROUP_MESSAGE, "post_type": "message_sent"})

    assert isinstance(event, UnknownEvent)
    assert event.kind == "ob11:message_sent"
    assert event.raw["message_id"] == 42  # 原文留着


def test_decode_cq_string_keeps_non_text_content():
    """CQ 码字符串里的非文本内容不能丢。"""
    event = decode_event(
        {
            **GROUP_MESSAGE,
            "message": "看这个[CQ:image,file=http://x/a.png]还有[CQ:at,qq=all]和&#91;转义&#93;",
        }
    )
    segments = event.content.segments
    assert isinstance(segments[1], Image)
    assert isinstance(segments[3], At)
    assert event.content.segments[3].is_all
    assert event.plain_text == "看这个还有和[转义]"


def test_decode_unknown_segment_does_not_crash():
    """未知段类型不能崩。"""
    raw = {"type": "future_segment", "data": {"x": "1"}}
    event = decode_event({**GROUP_MESSAGE, "message": [raw]})
    seg = event.content.segments[0]
    assert isinstance(seg, UnknownSegment)
    assert seg.type == "future_segment"
    assert seg.model_dump() == raw


def test_decode_unknown_event_keeps_raw():
    notice = decode_event(
        {
            "post_type": "notice",
            "notice_type": "future_notice",
            "time": 1,
            "self_id": 10001,
            "operator_id": 888,
        }
    )
    assert isinstance(notice, UnknownEvent)
    assert notice.kind == "ob11:notice:future_notice"
    # 原始字段留在 raw 里
    assert notice.raw["operator_id"] == 888


def test_encode_segments():
    encoded = encode_segments(RichText(Text("hi "), At(user_id=999), At.all()))
    assert encoded[0] == {"type": "text", "data": {"text": "hi "}}
    assert encoded[1] == {"type": "at", "data": {"qq": "999"}}
    assert encoded[2] == {"type": "at", "data": {"qq": "all"}}


def test_unknown_segment_encodes_back_verbatim():
    raw = {"type": "future_segment", "data": {"x": "1"}}
    encoded = encode_segments(RichText(segments=[UnknownSegment.model_validate(raw)]))
    assert encoded == [raw]


def test_decode_media_splits_handle_and_snapshot():
    """file 是内容句柄（加 onebot: 前缀），url 是临时快照。"""
    event = decode_event(
        {
            **GROUP_MESSAGE,
            "message": [
                {
                    "type": "image",
                    "data": {"file": "abc.jpg", "url": "http://cdn/x.jpg"},
                }
            ],
        }
    )
    image = event.content.segments[0]
    assert isinstance(image, Image)
    assert image.uri == "onebot:abc.jpg"
    assert image.temp_url == "http://cdn/x.jpg"


def test_decode_media_falls_back_to_url():
    """没有 file 时拿 url 当标识符，快照照留一份。"""
    event = decode_event(GROUP_MESSAGE)
    image = event.content.segments[2]
    assert isinstance(image, Image)
    assert image.uri == "http://x/y.png"
    assert image.temp_url == "http://x/y.png"


def test_encode_media_reads_uri_and_strips_prefix():
    """出站读 uri：协议句柄去掉前缀，地址原样送。"""
    encoded = encode_segments(RichText(segments=[Image(uri="onebot:abc.jpg")]))
    assert encoded == [{"type": "image", "data": {"file": "abc.jpg"}}]

    encoded = encode_segments(RichText(segments=[Image(uri="http://cdn/x.jpg")]))
    assert encoded == [{"type": "image", "data": {"file": "http://cdn/x.jpg"}}]


def test_encode_media_turns_data_uri_into_base64():
    """``data:`` 是模型层认的地址，OneBot 标准里没有 —— 出站转成 ``base64://``。"""
    encoded = encode_segments(
        RichText(segments=[Image(uri="data:image/gif;base64,R0lGOD")])
    )
    assert encoded == [{"type": "image", "data": {"file": "base64://R0lGOD"}}]


def test_media_roundtrips_through_onebot():
    """收进来再发出去，file 不变。

    入站的 ``url``（模型里叫 ``temp_url``）不回吐 —— 它只是当时的快照，
    出站再带一份和 ``file`` 打架。
    """
    raw = {"type": "image", "data": {"file": "abc.jpg", "url": "http://cdn/x.jpg"}}
    event = decode_event({**GROUP_MESSAGE, "message": [raw]})
    encoded = encode_segments(event.content)
    assert encoded[0]["data"] == {"file": "abc.jpg"}


def test_foreign_handle_is_refused_not_sent():
    """别的协议的句柄本实现端认不了 —— 报错好过原样发出去。"""
    with pytest.raises(UnresolvableUri):
        encode_segments(RichText(segments=[Image(uri="milky:res-1")]))


def test_media_roundtrip_keeps_declared_fields():
    """收进来再发出去，段自己的声明字段不能掉。"""
    event = decode_event(
        {
            **GROUP_MESSAGE,
            "message": [
                {
                    "type": "image",
                    "data": {
                        "file": "a.jpg",
                        "summary": "[图片]",
                        "width": 100,
                        "height": 50,
                    },
                }
            ],
        }
    )
    data = encode_segments(event.content)[0]["data"]

    assert data["file"] == "a.jpg"
    assert data["summary"] == "[图片]"
    assert data["width"] == 100
    assert data["height"] == 50


def test_reference_only_forward_survives_encoding():
    """只有引用的转发原样编码回引用形态 —— 以前会整段无声消失。"""
    event = decode_event(
        {**GROUP_MESSAGE, "message": [{"type": "forward", "data": {"id": "fwd-1"}}]}
    )
    assert encode_segments(event.content) == [
        {"type": "forward", "data": {"id": "fwd-1"}}
    ]


def test_onebot_media_resolves_handle_via_api(scenario):
    """句柄过期或要拿新地址时，走实现端的接口换一个。"""

    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.onebot_v11.segments import OneBotImage
        from novalark.model import ActionResult

        class FakeBot:
            async def call(self, action, params):
                assert action == "get_image"
                assert params == {"file": "abc.jpg"}
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        image = OneBotImage(uri="onebot:abc.jpg")
        assert await image.get_url() == "http://fresh/x.jpg"

    scenario(body)


def test_file_segment_is_not_standard():
    """标准消息段里没有 file：收进来降级成未知段，发出去报错。"""
    event = decode_event(
        {**GROUP_MESSAGE, "message": [{"type": "file", "data": {"file": "f1"}}]}
    )
    seg = event.content.segments[0]

    assert isinstance(seg, UnknownSegment)
    assert seg.type == "file"
    assert seg.model_dump() == {"type": "file", "data": {"file": "f1"}}

    with pytest.raises(UnsupportedSegment, match="file"):
        encode_segments(RichText(segments=[File(uri="f1")]))


def test_face_roundtrips_and_has_neutral_defaults():
    """OneBot 没有超级表情的概念，is_large 是 False。"""
    event = decode_event(
        {**GROUP_MESSAGE, "message": [{"type": "face", "data": {"id": "123"}}]}
    )
    face = event.content.segments[0]

    assert isinstance(face, Face)
    assert face.id == "123"
    assert face.is_large is False
    assert encode_segments(event.content) == [{"type": "face", "data": {"id": "123"}}]


def test_forward_expands_into_node_array():
    """OneBot 的合并转发是一串 node 段，不是一个段。"""
    forward = Forward(
        messages=[
            ForwardNode(user_id=1, name="小明", content="第一句"),
            ForwardNode(user_id=2, name="小红", content=RT(Text("第二句"))),
        ]
    )
    encoded = encode_segments(RT(segments=[forward]))

    assert [item["type"] for item in encoded] == ["node", "node"]
    assert encoded[0]["data"]["nickname"] == "小明"
    assert encoded[0]["data"]["content"] == [
        {"type": "text", "data": {"text": "第一句"}}
    ]


def test_forward_reference_decodes_without_content():
    event = decode_event(
        {**GROUP_MESSAGE, "message": [{"type": "forward", "data": {"id": "fwd-1"}}]}
    )
    forward = event.content.segments[0]

    assert isinstance(forward, Forward)
    assert forward.forward_id == "fwd-1"
    assert forward.messages == []  # 引用形态，内容要另外取


def test_poke_kind_alias():
    """OneBot 的 type 表示表情类型，模型里叫 kind。"""
    event = decode_event(
        {
            **GROUP_MESSAGE,
            "message": [{"type": "poke", "data": {"type": "126", "id": "2003"}}],
        }
    )
    poke = event.content.segments[0]

    assert isinstance(poke, Poke)
    assert poke.kind == "126"
    assert encode_segments(event.content) == [
        {"type": "poke", "data": {"type": "126", "id": "2003"}}
    ]


def test_decoded_segment_keeps_extension_fields():
    """协议扩展和实现端自己加的字段都保留。"""
    event = decode_event(
        {
            **GROUP_MESSAGE,
            "message": [{"type": "face", "data": {"id": "1", "vendor_x": "keep"}}],
        }
    )
    seg = event.content.segments[0]

    assert isinstance(seg, Face)
    assert seg.model_extra["vendor_x"] == "keep"


# ── 通知与请求 ─────────────────────────────────────────────

NOTICE = {
    "post_type": "notice",
    "time": 1,
    "self_id": 10001,
    "group_id": 12345,
    "user_id": 999,
    "operator_id": 888,
}


def test_group_recall_becomes_a_typed_event():
    event = decode_event({**NOTICE, "notice_type": "group_recall", "message_id": 42})

    assert isinstance(event, GroupRecallEvent)
    assert event.group_id == 12345
    assert event.sender_id == 999
    assert event.operator_id == 888
    # 定位消息一律是 MessageRef
    assert event.ref.seq == 42
    assert event.ref.conversation.peer_id == 12345


def test_onebot_decrease_sub_types_collapse_to_one_flag():
    """OneBot 用 leave/kick/kick_me 三种，模型里只要一个「是不是被踢的」。"""
    for sub_type, kicked in (("leave", False), ("kick", True), ("kick_me", True)):
        event = decode_event(
            {**NOTICE, "notice_type": "group_decrease", "sub_type": sub_type}
        )
        assert isinstance(event, GroupMemberDecreaseEvent)
        assert event.kicked is kicked


def test_whole_mute_is_split_from_member_mute():
    """user_id 为 0 是全员禁言，那是另一个概念，不该混进成员禁言。"""
    whole = decode_event(
        {
            **NOTICE,
            "notice_type": "group_ban",
            "sub_type": "ban",
            "user_id": 0,
            "duration": 0,
        }
    )
    assert isinstance(whole, GroupWholeMuteEvent)
    assert whole.is_mute is True

    member = decode_event(
        {
            **NOTICE,
            "notice_type": "group_ban",
            "sub_type": "ban",
            "user_id": 999,
            "duration": 600,
        }
    )
    assert isinstance(member, GroupMuteEvent)
    assert member.duration == 600


def test_poke_is_only_defined_inside_a_group():
    """标准只定义了「群内戳一戳」，没有 group_id 的形态是实现端扩展。"""
    group = decode_event(
        {**NOTICE, "notice_type": "notify", "sub_type": "poke", "target_id": 7}
    )
    assert isinstance(group, GroupNudgeEvent)
    assert group.group_id == 12345
    assert group.receiver_id == 7

    loose = decode_event(
        {
            **NOTICE,
            "notice_type": "notify",
            "sub_type": "poke",
            "group_id": 0,
            "target_id": 7,
        }
    )
    assert isinstance(loose, UnknownEvent)
    assert loose.kind == "ob11:notice:notify:poke"


def test_requests_are_typed_with_their_flag():
    friend = decode_event(
        {
            "post_type": "request",
            "request_type": "friend",
            "time": 1,
            "self_id": 1,
            "user_id": 999,
            "comment": "加个好友",
            "flag": "f1",
        }
    )
    assert isinstance(friend, FriendRequestEvent)
    assert friend.request.flag == "f1"  # 批准时要用

    group = decode_event(
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": "add",
            "time": 1,
            "self_id": 1,
            "group_id": 12345,
            "user_id": 999,
            "comment": "求进群",
            "flag": "f2",
        }
    )
    assert isinstance(group, GroupJoinRequestEvent)
    assert group.group_id == 12345
    assert group.request.user_id == 999

    # sub_type=invite 是「机器人被邀请进群」，另一个事件类
    invite = decode_event(
        {
            "post_type": "request",
            "request_type": "group",
            "sub_type": "invite",
            "time": 1,
            "self_id": 1,
            "group_id": 12345,
            "user_id": 888,
            "flag": "f3",
        }
    )
    assert isinstance(invite, GroupInviteRequestEvent)
    assert invite.group_id == 12345
    assert invite.request.invitor_id == 888
    assert invite.request.flag == "f3"  # 批准时要用
