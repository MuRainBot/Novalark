"""Milky 适配器：解码、编码、资源解析。"""

from __future__ import annotations

import pytest

from novalark.adapter.milky.config import MilkyConfig
from novalark.adapter.milky.decode import decode_event
from novalark.adapter.milky.encode import encode_segments
from novalark.model import (
    ActionResult,
    At,
    Face,
    FriendRecallEvent,
    GroupInviteRequestEvent,
    GroupJoinRequestEvent,
    GroupNameChangeEvent,
    GroupReactionEvent,
    GroupRecallEvent,
    GroupWholeMuteEvent,
    Forward,
    ForwardNode,
    FriendMessageEvent,
    GroupMessageEvent,
    Image,
    LightApp,
    Markdown,
    RichText,
    Text,
    UnknownEvent,
    UnresolvableUri,
)

GROUP_MESSAGE = {
    "time": 1700000000,
    "self_id": 10001,
    "event_type": "message_receive",
    "data": {
        "message_scene": "group",
        "peer_id": 12345,
        "message_seq": 42,
        "sender_id": 999,
        "time": 1700000000,
        "segments": [
            {"type": "text", "data": {"text": "hello "}},
            {"type": "mention", "data": {"user_id": 10001}},
            {
                "type": "image",
                "data": {"resource_id": "res-1", "temp_url": "http://x/y.png"},
            },
        ],
        "group": {"group_id": 12345, "group_name": "测试群"},
        "group_member": {
            "user_id": 999,
            "nickname": "小明",
            "card": "小明卡",
            "role": "admin",
            "title": "群主大人",
        },
    },
}


def test_config_derives_urls():
    config = MilkyConfig(type="milky", base_url="https://bot.example.com/")
    assert config.api_url == "https://bot.example.com/api"
    assert config.event_url == "wss://bot.example.com/event"


def test_decode_group_message():
    event = decode_event(GROUP_MESSAGE)

    assert isinstance(event, GroupMessageEvent)
    assert event.group_id == 12345
    assert event.self_id == 10001
    assert event.sender.nickname == "小明"
    assert event.sender.card == "小明卡"
    assert event.sender.title == "群主大人"
    assert event.sender.is_admin
    assert isinstance(event.content.segments[1], At)


def test_decode_marks_own_message():
    """Milky 把自己的消息也推进来，靠发送者是不是自己区分。"""
    payload = {**GROUP_MESSAGE, "data": {**GROUP_MESSAGE["data"], "sender_id": 10001}}
    assert decode_event(payload).is_self

    assert not decode_event(GROUP_MESSAGE).is_self


def test_decode_private_message():
    payload = {
        "time": 1,
        "self_id": 10001,
        "event_type": "message_receive",
        "data": {
            "message_scene": "friend",
            "peer_id": 999,
            "message_seq": 1,
            "sender_id": 999,
            "segments": [{"type": "text", "data": {"text": "hi"}}],
            "friend": {"user_id": 999, "nickname": "小红"},
        },
    }
    event = decode_event(payload)

    assert isinstance(event, FriendMessageEvent)
    assert event.sender.nickname == "小红"
    assert event.conversation.peer_id == 999


def test_decode_media_keeps_handle_and_snapshot():
    """resource_id 是句柄（加前缀进货 uri），temp_url 是会过期的快照。"""
    event = decode_event(GROUP_MESSAGE)
    image = event.content.segments[2]

    assert isinstance(image, Image)
    assert image.uri == "milky:res-1"
    assert image.temp_url == "http://x/y.png"


def test_unknown_event_is_kept_not_dropped():
    event = decode_event(
        {
            "time": 1,
            "self_id": 10001,
            "event_type": "future_event",
            "data": {"x": 42},
        }
    )
    assert isinstance(event, UnknownEvent)
    assert event.kind == "milky:future_event"
    assert event.raw["data"]["x"] == 42  # 原始字段留在 raw 里


def test_encode_uses_mention_not_at():
    """同语义不同名：OneBot 叫 at，Milky 叫 mention。"""
    encoded = encode_segments(RichText(Text("hi "), At(user_id=7), At.all()))

    assert encoded[0] == {"type": "text", "data": {"text": "hi "}}
    assert encoded[1] == {"type": "mention", "data": {"user_id": 7}}
    assert encoded[2] == {"type": "mention_all", "data": {}}


def test_encode_image_uses_uri():
    encoded = encode_segments(RichText(segments=[Image(uri="http://cdn/x.jpg")]))
    assert encoded == [
        {"type": "image", "data": {"uri": "http://cdn/x.jpg", "sub_type": "normal"}}
    ]


def test_roundtrip_text_message():
    event = decode_event(GROUP_MESSAGE)
    encoded = encode_segments(event.content)

    assert encoded[0] == {"type": "text", "data": {"text": "hello "}}
    assert encoded[1] == {"type": "mention", "data": {"user_id": 10001}}


def test_media_roundtrip_restores_the_handle():
    """收进来再发出去：``milky:`` 句柄要还原成 resource_id。

    以前会把 ``"milky:res-1"`` 原样塞进 ``uri`` 发出去，协议端认不出来。
    """
    event = decode_event(GROUP_MESSAGE)
    image = encode_segments(event.content)[2]

    assert image == {
        "type": "image",
        "data": {"resource_id": "res-1", "sub_type": "normal"},
    }


def test_foreign_handle_is_refused_not_sent():
    """别的协议的句柄本协议端认不了 —— 报错好过原样发出去。"""
    with pytest.raises(UnresolvableUri):
        encode_segments(RichText(segments=[Image(uri="onebot:abc.jpg")]))


def test_market_face_roundtrip_keeps_url():
    event = decode_event(
        _segments(
            {
                "type": "market_face",
                "data": {
                    "emoji_package_id": 1,
                    "emoji_id": "5",
                    "key": "k",
                    "summary": "表情",
                    "url": "http://cdn/e.png",
                },
            }
        )
    )
    assert encode_segments(event.content)[0]["data"]["url"] == "http://cdn/e.png"


def test_reference_only_forward_survives_encoding():
    """只有引用的转发原样编码回引用形态，不能被展平成空。"""
    event = decode_event(_segments({"type": "forward", "data": {"forward_id": "f1"}}))
    assert encode_segments(event.content) == [
        {"type": "forward", "data": {"forward_id": "f1"}}
    ]


def test_milky_image_resolves_handle_via_api(scenario):
    """句柄换地址走 get_resource_temp_url。"""

    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.milky.segments import MilkyImage

        class FakeBot:
            async def call(self, action, params):
                assert action == "get_resource_temp_url"
                assert params == {"resource_id": "res-1"}
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        assert await MilkyImage(uri="milky:res-1").get_url() == "http://fresh/x.jpg"

    scenario(body)


def test_milky_handle_is_unresolvable_when_api_returns_nothing(scenario):
    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.milky.segments import MilkyImage

        class FakeBot:
            async def call(self, action, params):
                return ActionResult(data={})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        with pytest.raises(UnresolvableUri):
            await MilkyImage(uri="milky:res-1").get_url()

    scenario(body)


def _segments(*items):
    return [
        {
            "time": 1,
            "self_id": 10001,
            "event_type": "message_receive",
            "data": {
                "message_scene": "group",
                "peer_id": 12345,
                "message_seq": 1,
                "sender_id": 999,
                "segments": list(items),
            },
        }
    ][0]


def test_milky_face_uses_face_id():
    """Milky 叫 face_id，OneBot 叫 id —— 进门就统一。"""
    event = decode_event(
        _segments({"type": "face", "data": {"face_id": "123", "is_large": True}})
    )
    face = event.content.segments[0]

    assert isinstance(face, Face)
    assert face.id == "123"
    assert face.is_large is True

    encoded = encode_segments(event.content)
    assert encoded == [{"type": "face", "data": {"face_id": "123", "is_large": True}}]


def test_light_app_uses_json_payload():
    event = decode_event(
        _segments({"type": "light_app", "data": {"json_payload": "{...}"}})
    )
    app = event.content.segments[0]

    assert isinstance(app, LightApp)
    assert app.payload == "{...}"
    assert encode_segments(event.content) == [
        {"type": "light_app", "data": {"json_payload": "{...}"}}
    ]


def test_markdown_is_milky_only():
    event = decode_event(_segments({"type": "markdown", "data": {"content": "# 标题"}}))
    markdown = event.content.segments[0]

    assert isinstance(markdown, Markdown)
    assert markdown.content == "# 标题"


def test_forward_stays_one_segment_with_content():
    """Milky 的合并转发是一个段装下全部内容，不像 OneBot 要拆成 node 数组。"""
    forward = Forward(
        title="聊天记录",
        messages=[ForwardNode(user_id=1, name="小明", content="第一句")],
    )
    encoded = encode_segments(RichText(segments=[forward]))

    assert len(encoded) == 1
    assert encoded[0]["type"] == "forward"
    assert encoded[0]["data"]["title"] == "聊天记录"
    assert encoded[0]["data"]["messages"][0]["sender_name"] == "小明"


def test_forward_reference_decodes():
    event = decode_event(
        _segments(
            {
                "type": "forward",
                "data": {"forward_id": "fwd-1", "summary": "查看 3 条消息"},
            }
        )
    )
    forward = event.content.segments[0]

    assert isinstance(forward, Forward)
    assert forward.forward_id == "fwd-1"
    assert forward.summary == "查看 3 条消息"
    assert forward.messages == []


def test_decoded_segment_keeps_extension_fields():
    """协议扩展和实现端自己加的字段都保留。"""
    event = decode_event(
        _segments({"type": "face", "data": {"face_id": "1", "vendor_x": "keep"}})
    )
    seg = event.content.segments[0]

    assert isinstance(seg, Face)
    assert seg.model_extra["vendor_x"] == "keep"


# ── 通知与请求 ─────────────────────────────────────────────


def _notice(event_type, **data):
    return {"time": 1, "self_id": 10001, "event_type": event_type, "data": data}


def test_recall_splits_on_scene():
    """一个 message_recall 管群和私聊，按 scene 分成两个类。"""
    group = decode_event(
        _notice(
            "message_recall",
            message_scene="group",
            peer_id=12345,
            message_seq=42,
            sender_id=999,
            operator_id=888,
            display_suffix="撤回了一条消息",
        )
    )
    assert isinstance(group, GroupRecallEvent)
    assert group.group_id == 12345
    assert group.operator_id == 888
    assert group.suffix == "撤回了一条消息"
    assert group.ref.seq == 42

    friend = decode_event(
        _notice("message_recall", message_scene="friend", peer_id=999, message_seq=7)
    )
    assert isinstance(friend, FriendRecallEvent)
    assert friend.user_id == 999


def test_whole_mute_is_its_own_event():
    event = decode_event(
        _notice("group_whole_mute", group_id=12345, operator_id=888, is_mute=True)
    )
    assert isinstance(event, GroupWholeMuteEvent)
    assert event.is_mute is True


def test_milky_only_events():
    name = decode_event(
        _notice(
            "group_name_change",
            group_id=12345,
            new_group_name="新名字",
            operator_id=888,
        )
    )
    assert isinstance(name, GroupNameChangeEvent)
    assert name.name == "新名字"

    reaction = decode_event(
        _notice(
            "group_message_reaction",
            group_id=12345,
            user_id=999,
            message_seq=42,
            face_id="124",
            reaction_type="face",
            is_add=True,
        )
    )
    assert isinstance(reaction, GroupReactionEvent)
    assert reaction.face_id == "124"
    assert reaction.ref.seq == 42


def test_join_request_and_invite_are_different_events():
    """别人申请进群、和机器人被邀请进群，是两个事件类。"""
    request = decode_event(
        _notice(
            "group_join_request",
            group_id=12345,
            initiator_id=999,
            comment="求进群",
            notification_seq=7,
        )
    )
    assert isinstance(request, GroupJoinRequestEvent)
    assert request.group_id == 12345
    assert request.user_id == 999
    assert request.sequence == 7

    invite = decode_event(
        _notice("group_invited_join_request", group_id=12345, initiator_id=888,
                notification_seq=3)
    )
    assert isinstance(invite, GroupInviteRequestEvent)
    assert invite.invitor_id == 888
    assert invite.sequence == 3


def test_invitation_notice_is_the_same_event_as_an_invite_request():
    """``group_invitation`` 只是通知时机不同，同样是「等着批的邀请」。"""
    invitation = decode_event(
        _notice(
            "group_invitation",
            group_id=12345,
            initiator_id=888,
            invitation_seq=3,
            source_group_id=777,
        )
    )
    assert isinstance(invitation, GroupInviteRequestEvent)
    assert invitation.invitor_id == 888
    assert invitation.sequence == 3
    assert invitation.source_group_id == 777
