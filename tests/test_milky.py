"""Milky 适配器：解码、编码、资源解析。"""

from __future__ import annotations

import pytest

from novalark import UnsupportedSegment
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
    File,
    Forward,
    ForwardNode,
    FriendMessageEvent,
    GroupMessageEvent,
    Image,
    LightApp,
    Markdown,
    MarketFace,
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


def _milky_adapter():
    from novalark.adapter.milky.adapter import MilkyAdapter

    return MilkyAdapter(MilkyConfig(type="milky", base_url="https://bot.example.com/"))


def test_bot_media_url_resolves_a_segment_built_from_a_bare_handle(scenario):
    """插件手上只剩句柄字符串时，从 bot 这一层取地址。

    ``Image(uri=...)`` 造出来的是模型层基类 —— 它不认识任何协议，``get_url()``
    只能抛。适配器认识，所以这条道开在适配器上。
    """

    async def body():
        from novalark.adapter.bot import set_current_bot

        class FakeBot:
            async def call(self, action, params):
                assert action == "get_resource_temp_url"
                assert params == {"resource_id": "res-1"}
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        with pytest.raises(UnresolvableUri):
            await Image(uri="milky:res-1").get_url()

        url = await _milky_adapter().bot.media_url(Image(uri="milky:res-1"))
        assert url == "http://fresh/x.jpg"

    scenario(body)


def test_bot_media_url_passes_a_plain_address_through(scenario):
    """段里本来就是地址，不必问实现端。"""

    async def body():
        url = await _milky_adapter().bot.media_url(Image(uri="http://cdn/x.jpg"))

        assert url == "http://cdn/x.jpg"

    scenario(body)


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
    # 图片段带的是句柄，编码前要先过 resolve_handles —— 这里只看文本和 @
    encoded = encode_segments(RichText(segments=event.content.segments[:2]))

    assert encoded[0] == {"type": "text", "data": {"text": "hello "}}
    assert encoded[1] == {"type": "mention", "data": {"user_id": 10001}}


def test_outgoing_media_is_sent_as_uri():
    """出站媒体段只认 ``uri``。

    ``IncomingSegment`` 给的是 ``resource_id``，而 ``OutgoingSegment`` 里
    **根本没有这个字段**（见 protocol/src/ir/common.ts）—— 两边的媒体段是
    两套结构，名字都不是一套。
    """
    encoded = encode_segments(RichText(segments=[Image(uri="http://cdn/x.jpg")]))

    assert encoded == [
        {"type": "image", "data": {"uri": "http://cdn/x.jpg", "sub_type": "normal"}}
    ]


def test_unresolved_handle_is_refused_not_sent():
    """句柄没解析过就编码，出站没有字段可填 —— 报错好过发出去。"""
    with pytest.raises(UnresolvableUri):
        encode_segments(RichText(segments=[Image(uri="milky:res-1")]))


def test_data_uri_is_sent_as_base64():
    """``data:`` 是模型层认的地址，Milky 却只认 ``base64://`` —— 编码时转一道。

    不转就是协议端一句 ``Unsupported URI scheme``，报错点在网络那头。
    """
    encoded = encode_segments(
        RichText(segments=[Image(uri="data:image/gif;base64,R0lGOD")])
    )

    assert encoded == [
        {"type": "image", "data": {"uri": "base64://R0lGOD", "sub_type": "normal"}}
    ]


def test_resolve_handles_swaps_the_handle_for_a_temp_url(scenario):
    """转发收到的媒体：先把句柄换成临时链接，再编码成 uri。"""

    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.milky.encode import resolve_handles

        class FakeBot:
            async def call(self, action, params):
                assert action == "get_resource_temp_url"
                assert params == {"resource_id": "res-1"}
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        event = decode_event(GROUP_MESSAGE)
        resolved = await resolve_handles(event.content)

        # 原段不动（句柄还在），换出来的是新对象
        assert event.content.segments[2].uri == "milky:res-1"
        assert resolved.segments[2].uri == "http://fresh/x.jpg"
        assert encode_segments(resolved)[2] == {
            "type": "image",
            "data": {"uri": "http://fresh/x.jpg", "sub_type": "normal"},
        }

    scenario(body)


def test_outgoing_media_drops_incoming_only_fields(scenario):
    """width / height / resource_id 是入站专有的，不许回吐给出站结构。"""

    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.milky.encode import resolve_handles

        class FakeBot:
            async def call(self, action, params):
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        event = decode_event(
            _segments(
                {
                    "type": "image",
                    "data": {
                        "resource_id": "res-1",
                        "temp_url": "http://x/y.png",
                        "width": 640,
                        "height": 480,
                        "summary": "预览",
                    },
                }
            )
        )
        image = event.content.segments[0]
        assert (image.width, image.height) == (640, 480)  # 模型层照收

        encoded = encode_segments(await resolve_handles(event.content))[0]
        assert encoded == {
            "type": "image",
            "data": {
                "uri": "http://fresh/x.jpg",
                "summary": "预览",
                "sub_type": "normal",
            },
        }

    scenario(body)


def test_resolve_handles_reaches_forward_content(scenario):
    """合并转发里的段一样要换 —— 转发内容也是段。"""

    async def body():
        from novalark.adapter.bot import set_current_bot
        from novalark.adapter.milky.encode import resolve_handles

        class FakeBot:
            async def call(self, action, params):
                return ActionResult(data={"url": "http://fresh/x.jpg"})

        set_current_bot(FakeBot())  # type: ignore[arg-type]

        forward = Forward(
            messages=[
                ForwardNode(
                    user_id=1, name="小明", content=[Image(uri="milky:res-1")]
                )
            ]
        )
        resolved = await resolve_handles(RichText(segments=[forward]))
        node = resolved.segments[0].messages[0]

        assert node.content[0].uri == "http://fresh/x.jpg"

    scenario(body)


def test_foreign_handle_is_refused_not_sent():
    """别的协议的句柄本协议端认不了 —— 报错好过原样发出去。"""
    with pytest.raises(UnresolvableUri):
        encode_segments(RichText(segments=[Image(uri="onebot:abc.jpg")]))


def test_market_face_decodes_but_cannot_be_sent():
    """``market_face`` 只在 ``IncomingSegment`` 里，出站没有这个变体。"""
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
    assert isinstance(event.content.segments[0], MarketFace)

    with pytest.raises(UnsupportedSegment):
        encode_segments(event.content)


def test_file_segment_cannot_be_sent():
    """Milky 没有出站的 ``file`` 段 —— 发文件走 ``upload_*`` API。"""
    with pytest.raises(UnsupportedSegment):
        encode_segments(RichText(segments=[File(uri="file:///tmp/a.txt")]))


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


def test_joining_a_group_is_one_event_however_you_got_there():
    """自己申请、被群成员邀请 —— 都是「有人要进群」，同一个事件类。

    两者在 Milky 靠 ``invitor_id`` 区分（批准时 ``notification_type`` 不同），
    在 OneBot 一律是 ``sub_type=add``，不区分。
    """
    join = decode_event(
        _notice(
            "group_join_request",
            group_id=12345,
            initiator_id=999,
            comment="求进群",
            notification_seq=7,
        )
    )
    assert isinstance(join, GroupJoinRequestEvent)
    assert join.group_id == 12345
    assert join.request.user_id == 999
    assert join.request.invitor_id == 0  # 自己申请的
    assert join.request.sequence == 7

    invited = decode_event(
        _notice(
            "group_invited_join_request",
            group_id=12345,
            initiator_id=888,
            target_user_id=666,
            notification_seq=3,
        )
    )
    assert isinstance(invited, GroupJoinRequestEvent)
    # 要进群的是被邀请者，不是邀请者
    assert invited.request.user_id == 666
    assert invited.request.invitor_id == 888
    assert invited.request.sequence == 3


def test_being_invited_into_a_group_is_its_own_event():
    """机器人**自己被**邀请进群：和「有人要进群」不是一回事。

    批准走的也不是同一组 API —— ``accept_group_invitation`` 认的是
    ``invitation_seq``，不是 ``notification_seq``。
    """
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
    assert invitation.request.invitor_id == 888
    assert invitation.request.sequence == 3
    assert invitation.request.source_group_id == 777
