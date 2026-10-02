"""QQ 语义模型：坐标、段、消息、事件。"""

from __future__ import annotations

import pytest

from novalark.model import (
    At,
    ConversationRef,
    Face,
    Forward,
    ForwardNode,
    Group,
    GroupMessageEvent,
    Image,
    ImageSubType,
    LightApp,
    Message,
    MessageEvent,
    MessageRef,
    MessageScene,
    Reply,
    RichText,
    Sender,
    Text,
    UnknownSegment,
    UnresolvableUri,
)


def test_conversation_ref_is_hashable_and_roundtrips():
    conv = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
    assert conv.is_group and not conv.is_private

    # frozen=True 让它能直接当状态表的 key
    table = {conv: "state"}
    assert table[ConversationRef(scene=MessageScene.GROUP, peer_id=12345)] == "state"
    assert ConversationRef.model_validate_json(conv.model_dump_json()) == conv


def test_message_ref_roundtrips():
    ref = MessageRef(
        conversation=ConversationRef(scene=MessageScene.FRIEND, peer_id=999), seq=42
    )
    assert MessageRef.model_validate_json(ref.model_dump_json()) == ref


def test_richtext_accepts_mixed_input():
    assert RichText("纯文本").plain_text == "纯文本"
    assert RichText(At(user_id=999), Text(" 你好")).plain_text == " 你好"
    assert (RichText(Text("x")) + "y" + At.all()).plain_text == "xy"
    assert ("前缀" + RichText(Text("后缀"))).plain_text == "前缀后缀"
    assert len(RichText(RichText(Text("a")), "b").segments) == 2


def test_richtext_accepts_a_whole_list_or_dict():
    """段序列整条递进来也行 —— 拼消息时最自然的写法。"""
    assert RichText([Text("a"), At(user_id=1)]).display == "a@1"
    assert RichText({"type": "at", "user_id": 5}).display == "@5"
    assert RichText("a", [At(1), "b"]).display == "a@1b"
    assert len(RichText(["a", ["b", ["c"]]]).segments) == 3

    with pytest.raises(TypeError, match="不支持的消息内容"):
        RichText(object())


def test_segment_type_is_the_semantic_name():
    """段模型的 ``type`` 由类自己定，协议名不许从这里泄漏。

    OneBot 的 ``json`` / Milky 的 ``mention`` 到了模型层都得变回语义名，
    否则 ``if seg.type == "at"`` 会在其中一个协议上失效。
    """
    assert At.model_validate({"type": "mention", "user_id": 1}).type == "at"
    assert At.model_validate({"type": "at", "user_id": 1}).type == "at"
    assert LightApp.model_validate({"type": "json", "payload": "{}"}).type == "light_app"
    assert Face.model_validate({"type": "face", "id": "1"}).type == "face"


def test_segment_keeps_protocol_original_in_raw():
    """``raw`` 留原文但不参与序列化，和 ``Event.raw`` 对称。"""
    face = Face.model_validate(
        {"type": "face", "id": 123, "vendor_x": "keep", "raw": {"id": 123}}
    )
    assert face.id == "123"  # 数字字段不再整段吞掉消息
    assert face.raw == {"id": 123}
    assert "raw" not in face.model_dump()
    assert face.model_extra["vendor_x"] == "keep"


def test_null_protocol_fields_fall_back_to_defaults():
    assert Text.model_validate({"type": "text", "text": None}).text == ""


def test_protocol_values_are_typed():
    """取值由协议定死的字段给的是枚举，不是裸字符串。

    枚举是 ``str`` 的子类：和协议原文直接比、序列化成 JSON 都照旧，插件不用
    为「拿到的是枚举还是字符串」操心。
    """
    image = Image(uri="http://x/y.png", sub_type="sticker")
    assert image.sub_type is ImageSubType.STICKER
    assert image.sub_type == "sticker"
    assert image.model_dump(mode="json")["sub_type"] == "sticker"

    # 协议加了值也不能把段打成未知类型：取值照收，类型还是图片
    future = Image.model_validate({"type": "image", "sub_type": "animated"})
    assert isinstance(future, Image)
    assert future.sub_type == "animated"


def test_a_scene_typo_is_refused():
    """会话场景不从协议解析，是模型自己的取值 —— 写错就该报错。"""
    with pytest.raises(ValueError):
        MessageScene("gruop")


def test_at_all_is_explicit():
    assert At.all().is_all
    assert not At(user_id=1).is_all
    assert At(999).user_id == 999  # 位置参数按字段顺序映射


def test_segments_from_dict_keep_their_type():
    ref = MessageRef(
        conversation=ConversationRef(scene=MessageScene.GROUP, peer_id=1), seq=7
    )
    content = RichText.model_validate(
        {
            "segments": [
                {"type": "text", "text": "hi "},
                {"type": "at", "user_id": 999},
                {"type": "image", "url": "http://x/y.png"},
                {"type": "reply", "ref": ref.model_dump()},
            ]
        }
    )
    assert isinstance(content.segments[0], Text)
    assert isinstance(content.segments[1], At)
    assert isinstance(content.segments[2], Image)
    assert isinstance(content.segments[3], Reply)
    assert content.segments[3].ref == ref
    assert content.plain_text == "hi "


def test_unknown_segment_degrades_without_losing_data():
    """未知段类型不能崩，且要保留原文。"""
    content = RichText.model_validate(
        {"segments": [{"type": "text", "text": "x"}, {"type": "future", "id": 1}]}
    )
    face = content.segments[1]
    assert isinstance(face, UnknownSegment)
    assert face.type == "future"
    assert face.model_dump() == {"type": "future", "id": 1}
    assert face.id == 1  # 未知字段直接挂在实例上
    # 原文可再编码回去
    assert face.model_extra == {"id": 1}


def test_fetch_group_queries_when_not_inlined(scenario, make_group_event):
    """事件里没有内联群实体时，fetch_group 去查一次。"""

    async def body():
        calls: list[int] = []

        class Bot:
            self_id = 10001

            async def get_group(self, group_id: int) -> Group:
                calls.append(group_id)
                return Group(group_id=group_id, name="测试群")

        event = make_group_event(bot=Bot())
        assert event.group_id == 12345  # 交集字段，零成本
        group = await event.fetch_group()
        assert group.name == "测试群"
        assert calls == [12345]

    scenario(body)


def test_get_url_ignores_snapshot_and_resolves_handle(scenario):
    """快照可能过期，get_url 不碰它；是句柄就交给适配器。"""

    async def body():
        # 有快照也不直接用 —— 它可能早就失效了
        stale = Image(uri="onebot:abc.jpg", temp_url="http://cdn/stale.jpg")
        with pytest.raises(UnresolvableUri):
            await stale.get_url()

        direct = Image(uri="http://cdn/y.jpg")
        assert await direct.get_url() == "http://cdn/y.jpg"

    scenario(body)


def test_adapter_subclass_resolves_handle(scenario):
    """适配器子类重写 _resolve，把协议句柄换成地址。"""

    async def body():
        class FakeImage(Image):
            async def _resolve(self) -> str:
                return f"http://resolved/{self.uri}"

        image = FakeImage(uri="onebot:abc.jpg")
        assert await image.get_url() == "http://resolved/onebot:abc.jpg"

    scenario(body)


def test_event_inheritance_and_helpers():
    conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
    event = GroupMessageEvent(
        self_id=10001,
        message=Message(
            ref=MessageRef(conversation=conversation, seq=1),
            content=RichText("hello"),
            sender=Sender(user_id=999, nickname="小明", card="小明卡", role="admin"),
        ),
    )
    assert isinstance(event, MessageEvent)  # @on(MessageEvent) 要收得到
    assert event.group_id == 12345
    assert event.conversation == conversation
    assert event.plain_text == "hello"
    assert event.sender.display_name == "小明卡"
    assert event.sender.is_admin


def test_event_serialization_roundtrip():
    conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=12345)
    event = GroupMessageEvent(
        self_id=10001,
        message=Message(
            ref=MessageRef(conversation=conversation, seq=1),
            content=RichText("hi"),
            sender=Sender(user_id=999),
        ),
    )
    dump = event.model_dump()
    assert "raw" not in dump  # 协议原文不参与序列化

    restored = GroupMessageEvent.model_validate(dump)
    assert restored.group_id == 12345
    assert isinstance(restored.content.segments[0], Text)
    assert restored.plain_text == "hi"  # 段字段不能在这一来一回里被丢掉


def test_segment_fields_survive_container_dump():
    """段容器必须按**子类**序列化。

    ``segments: list[Segment]`` 这种标注下，pydantic 默认只按基类
    ``Segment`` 序列化，子类字段（``text`` / ``user_id``…）会被静默丢掉；
    靠 ``SerializeAsAny`` 治。这里把两个容器都钉死。
    """
    dumped = RichText([Text("hi"), At(user_id=42, name="小明")]).model_dump()
    assert dumped == {
        "segments": [
            {"type": "text", "text": "hi"},
            {"type": "at", "user_id": 42, "name": "小明"},
        ]
    }

    node = ForwardNode(user_id=1, content=[Text("hi")])
    assert node.model_dump()["content"] == [{"type": "text", "text": "hi"}]

    # 未知段的扩展字段也要原样带出去
    unknown = UnknownSegment.model_validate({"type": "weird", "foo": 1})
    assert RichText(segments=[unknown]).model_dump() == {
        "segments": [{"type": "weird", "foo": 1}]
    }


def test_forward_node_nested_segments_roundtrip():
    """合并转发的嵌套段经 ``model_validate`` 后仍是具体段类，值也还在。"""
    forward = Forward.model_validate(
        {
            "type": "forward",
            "messages": [
                {
                    "user_id": 1,
                    "content": [{"type": "text", "text": "hi"}, {"type": "at", "user_id": 42}],
                }
            ],
        }
    )
    node = forward.messages[0]
    assert [type(seg).__name__ for seg in node.content] == ["Text", "At"]
    assert node.text == "hi"

    # 整个 Forward 走一遍 JSON 往返，值不能丢
    again = Forward.model_validate_json(forward.model_dump_json())
    assert again.messages[0].text == "hi"
    assert again.messages[0].content[1].user_id == 42


def test_unbound_bot_raises_clearly():
    conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=1)
    event = GroupMessageEvent(
        message=Message(
            ref=MessageRef(conversation=conversation, seq=1),
            content=RichText("hi"),
            sender=Sender(user_id=999),
        )
    )
    with pytest.raises(RuntimeError, match="尚未绑定"):
        event.bot
