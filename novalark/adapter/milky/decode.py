"""Milky 事件 JSON → 模型。

Milky 的事件是单层结构：``event_type`` 加一个 ``data`` 容器，映射到扁平的
``kind``。
"""

from __future__ import annotations

from typing import Any

from ...log import get_logger
from ...model.entities import (
    FriendRequest,
    GroupInviteRequest,
    GroupJoinRequest,
    Sender,
)
from ...model.enums import ImageSubType, ReactionType
from ...model.events import (
    BotOfflineEvent,
    Event,
    FileUploadEvent,
    FriendFileUploadEvent,
    FriendMessageEvent,
    FriendNudgeEvent,
    FriendRecallEvent,
    FriendRequestEvent,
    GroupAdminEvent,
    GroupDisbandEvent,
    GroupEssenceEvent,
    GroupInviteRequestEvent,
    GroupJoinRequestEvent,
    GroupMemberDecreaseEvent,
    GroupMemberIncreaseEvent,
    GroupMessageEvent,
    GroupMuteEvent,
    GroupNameChangeEvent,
    GroupNudgeEvent,
    GroupReactionEvent,
    GroupRecallEvent,
    GroupWholeMuteEvent,
    MessageEvent,
    PeerPinChangeEvent,
    TempMessageEvent,
    UnknownEvent,
)
from ...model.message import Message, RichText
from ...model.refs import ConversationRef, MessageRef, MessageScene
from ...model.segments import (
    At,
    Face,
    LightApp,
    Markdown,
    MarketFace,
    Reply,
    Segment,
    Text,
    UnknownSegment,
    Xml,
)
from .._util import to_int
from .segments import MEDIA_CLASSES, MilkyForward

logger = get_logger("adapter.milky")

_SCENES = {
    MessageScene.GROUP: GroupMessageEvent,
    MessageScene.FRIEND: FriendMessageEvent,
    MessageScene.TEMP: TempMessageEvent,
}


def decode_event(payload: dict[str, Any]) -> Event:
    """把一条 Milky 事件解码成模型事件。识别不了的走 UnknownEvent。"""
    event_type = str(payload.get("event_type") or "")
    common: dict[str, Any] = {
        "time": to_int(payload.get("time")),
        "self_id": to_int(payload.get("self_id")),
        "raw": dict(payload),
    }
    raw_data = payload.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}

    if event_type == "message_receive":
        try:
            return _decode_message(data, **common)
        except Exception:
            return UnknownEvent(kind="milky:message_receive", **common)

    try:
        return _decode_notice(event_type, data, **common)
    except Exception:
        return UnknownEvent(
            kind=f"milky:{event_type}" if event_type else "milky:unknown", **common
        )


def _group_ref(group_id: int, data: dict[str, Any]) -> MessageRef:
    """群通知里定位一条消息：群号在事件上，序列号在 data 里。"""
    return MessageRef(
        conversation=ConversationRef(scene=MessageScene.GROUP, peer_id=group_id),
        seq=to_int(data.get("message_seq")),
    )


def _decode_notice(event_type: str, data: dict[str, Any], **common: Any) -> Event:
    """通知类事件。"""
    group_id = to_int(data.get("group_id") or data.get("peer_id"))
    user_id = to_int(data.get("user_id"))
    operator_id = to_int(data.get("operator_id"))
    sender_id = to_int(data.get("sender_id") or data.get("initiator_id"))

    def text(key: str) -> str:
        value = data.get(key)
        return "" if value in (None, "") else str(value)

    match event_type:
        case "message_recall":
            # 一个事件管群和私聊，靠 scene 分叉
            scene = _scene_of(data)
            peer_id = to_int(data.get("peer_id"))
            ref = MessageRef(
                conversation=ConversationRef(scene=scene, peer_id=peer_id),
                seq=to_int(data.get("message_seq")),
            )
            if scene is MessageScene.GROUP:
                return GroupRecallEvent(
                    group_id=peer_id,
                    ref=ref,
                    sender_id=to_int(data.get("sender_id")),
                    operator_id=operator_id,
                    suffix=text("display_suffix"),
                    **common,
                )
            return FriendRecallEvent(
                user_id=peer_id,
                ref=ref,
                sender_id=to_int(data.get("sender_id")),
                operator_id=operator_id,
                suffix=text("display_suffix"),
                **common,
            )

        case "group_admin_change":
            return GroupAdminEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                is_set=bool(data.get("is_set")),
                **common,
            )

        case "group_member_increase":
            return GroupMemberIncreaseEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                invitor_id=to_int(data.get("invitor_id")),
                **common,
            )

        case "group_member_decrease":
            return GroupMemberDecreaseEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                # Milky 不给 sub_type，有操作者就说明是被踢的
                kicked=bool(operator_id),
                **common,
            )

        case "group_mute":
            return GroupMuteEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                duration=to_int(data.get("duration")),
                **common,
            )

        case "group_whole_mute":
            return GroupWholeMuteEvent(
                group_id=group_id,
                operator_id=operator_id,
                is_mute=bool(data.get("is_mute")),
                **common,
            )

        case "group_essence_message_change":
            return GroupEssenceEvent(
                group_id=group_id,
                ref=_group_ref(group_id, data),
                operator_id=operator_id,
                is_set=bool(data.get("is_set")),
                **common,
            )

        case "group_name_change":
            return GroupNameChangeEvent(
                group_id=group_id,
                name=text("new_group_name"),
                operator_id=operator_id,
                **common,
            )

        case "group_disband":
            return GroupDisbandEvent(
                group_id=group_id, operator_id=operator_id, **common
            )

        case "group_message_reaction":
            return GroupReactionEvent(
                group_id=group_id,
                ref=_group_ref(group_id, data),
                user_id=user_id,
                face_id=text("face_id"),
                reaction_type=ReactionType(text("reaction_type") or ReactionType.FACE),
                is_add=bool(data.get("is_add")),
                **common,
            )

        case "group_nudge":
            return GroupNudgeEvent(
                group_id=group_id,
                sender_id=sender_id,
                receiver_id=to_int(data.get("receiver_id")),
                action=text("display_action"),
                suffix=text("display_suffix"),
                action_image=text("display_action_img_url"),
                **common,
            )

        case "friend_nudge":
            # is_self_send 决定谁戳谁：自己戳对方时，发送者是机器人
            me = to_int(common.get("self_id"))
            return FriendNudgeEvent(
                user_id=user_id,
                sender_id=me if data.get("is_self_send") else user_id,
                receiver_id=user_id if data.get("is_self_send") else me,
                action=text("display_action"),
                suffix=text("display_suffix"),
                action_image=text("display_action_img_url"),
                **common,
            )

        case "group_file_upload":
            return FileUploadEvent(
                group_id=group_id,
                user_id=user_id,
                file_id=text("file_id"),
                name=text("file_name"),
                size=to_int(data.get("file_size")),
                **common,
            )

        case "friend_file_upload":
            return FriendFileUploadEvent(
                user_id=user_id,
                file_id=text("file_id"),
                name=text("file_name"),
                size=to_int(data.get("file_size")),
                file_hash=text("file_hash"),
                is_self=bool(data.get("is_self")),
                **common,
            )

        case "friend_request":
            # Milky 批准好友申请认的是 uid 不是 flag，把它放进 flag —— 那是
            # 「处理这条申请要带上的凭据」，语义正好
            return FriendRequestEvent(
                request=FriendRequest(
                    user_id=sender_id,
                    comment=text("comment"),
                    via=text("via"),
                    flag=text("initiator_uid"),
                    is_filtered=bool(data.get("is_filtered")),
                ),
                **common,
            )

        case "group_join_request":
            # 自己申请入群：initiator_id 就是申请人
            return GroupJoinRequestEvent(
                request=GroupJoinRequest(
                    group_id=group_id,
                    user_id=to_int(data.get("initiator_id")),
                    comment=text("comment"),
                    sequence=to_int(data.get("notification_seq")),
                    # 批准这条请求时要把它原样回传
                    is_filtered=bool(data.get("is_filtered")),
                ),
                **common,
            )

        case "group_invited_join_request":
            # 群成员邀请他人入群：要进群的是 target_user_id，initiator_id 是
            # 邀请者。语义同 OneBot 的 sub_type=add，所以也归 GroupJoinRequestEvent
            return GroupJoinRequestEvent(
                request=GroupJoinRequest(
                    group_id=group_id,
                    user_id=to_int(data.get("target_user_id")),
                    invitor_id=to_int(data.get("initiator_id")),
                    sequence=to_int(data.get("notification_seq")),
                ),
                **common,
            )

        case "group_invitation":
            # 他人邀请**机器人自身**入群：批准走的是独立 API，凭据是
            # invitation_seq 而不是 notification_seq
            return GroupInviteRequestEvent(
                request=GroupInviteRequest(
                    group_id=group_id,
                    invitor_id=to_int(data.get("initiator_id")),
                    sequence=to_int(data.get("invitation_seq")),
                    source_group_id=to_int(data.get("source_group_id")),
                ),
                **common,
            )

        case "bot_offline":
            return BotOfflineEvent(reason=text("reason"), **common)

        case "peer_pin_change":
            return PeerPinChangeEvent(
                conversation=ConversationRef(
                    scene=_scene_of(data), peer_id=to_int(data.get("peer_id"))
                ),
                is_pinned=bool(data.get("is_pinned")),
                **common,
            )

        case _:
            return UnknownEvent(
                kind=f"milky:{event_type}" if event_type else "milky:unknown", **common
            )


# ── 消息 ───────────────────────────────────────────────────


def _decode_message(
    data: dict[str, Any], *, time: int, self_id: int, **common: Any
) -> MessageEvent:
    scene = _scene_of(data)
    peer_id = to_int(data.get("peer_id"))
    sender_id = to_int(data.get("sender_id"))
    conversation = ConversationRef(scene=scene, peer_id=peer_id)

    message = Message(
        ref=MessageRef(conversation=conversation, seq=to_int(data.get("message_seq"))),
        content=RichText(
            segments=[
                _decode_segment(item, conversation)
                for item in _as_list(data.get("segments"))
            ]
        ),
        sender=_decode_sender(data, scene, sender_id),
        # Milky 把自己的消息也推进来，靠发送者是不是自己来区分
        is_self=sender_id == self_id,
        # 参数里的 time 是事件时间（给 Event 用），消息自己的发送时间在消息里
        time=to_int(data.get("time")),
    )
    return _SCENES[scene](message=message, time=time, self_id=self_id, **common)


def _scene_of(data: dict[str, Any]) -> MessageScene:
    try:
        return MessageScene(str(data.get("message_scene") or "friend"))
    except ValueError:
        return MessageScene.FRIEND


def _decode_sender(data: dict[str, Any], scene: MessageScene, sender_id: int) -> Sender:
    source = (
        data.get("group_member") if scene is MessageScene.GROUP else data.get("friend")
    )
    raw = source if isinstance(source, dict) else {}
    return Sender.model_validate(
        {
            **raw,
            "user_id": sender_id,
            "nickname": str(raw.get("nickname") or ""),
            "card": str(raw.get("card") or ""),
            # 私聊的 sender 里没有 role，给 None 而不是空串
            "role": raw.get("role") or None,
            "title": str(raw.get("title") or ""),
        }
    )


# ── 段 ─────────────────────────────────────────────────────


def _decode_segment(data: Any, conversation: ConversationRef) -> Segment:
    if not isinstance(data, dict):
        return UnknownSegment.model_validate({"type": "unknown", "value": data})

    seg_type = str(data.get("type") or "")
    raw_fields = data.get("data")
    fields = raw_fields if isinstance(raw_fields, dict) else {}

    def text(key: str) -> str:
        return str(fields.get(key) or "")

    # 每个分支都把整个 fields 摊进去：协议扩展要留在 extra 里，原始段 dict 进
    # ``raw``。``type`` 一律不写 —— Milky 的段名（``mention`` 之类）不许进模型，
    # 段类自己声明语义名。
    raw = dict(data)
    match seg_type:
        case "text":
            return Text.model_validate({**fields, "raw": raw})
        case "mention":
            # name 是 Milky 1.2 给的显示名（已去掉 @ 前缀），OneBot 不给
            return At.model_validate({**fields, "raw": raw, "name": text("name")})
        case "mention_all":
            return At(raw=raw)
        case "reply":
            ref = MessageRef(
                conversation=conversation, seq=to_int(fields.get("message_seq"))
            )
            return Reply.model_validate(
                {
                    **fields,
                    "raw": raw,
                    "ref": ref,
                    # Milky 1.2 起顺带带回被引用消息的内容与发送者，省一次查询
                    "content": [
                        _decode_segment(item, conversation)
                        for item in _as_list(fields.get("segments"))
                    ],
                    "sender_id": to_int(fields.get("sender_id")),
                    "sender_name": text("sender_name"),
                    "time": to_int(fields.get("time")),
                }
            )
        case "forward":
            # 收到的是引用，内容要另外调 get_forwarded_messages —— 见 MilkyForward.fetch
            preview = fields.get("preview")
            return MilkyForward.model_validate(
                {
                    **fields,
                    "raw": raw,
                    "forward_id": _text_or_none(fields.get("forward_id")),
                    "preview": (
                        [str(item) for item in preview]
                        if isinstance(preview, list)
                        else []
                    ),
                }
            )
        case "image" | "record" | "video" | "file":
            cls = MEDIA_CLASSES[seg_type]
            return cls.model_validate(
                {
                    **fields,
                    "raw": raw,
                    "uri": _incoming_uri(seg_type, fields),
                    "temp_url": _text_or_none(fields.get("temp_url")),
                    **_media_fields(seg_type, fields),
                }
            )
        case "face":
            # Milky 叫 face_id，模型里统一为 id
            return Face.model_validate({**fields, "raw": raw, "id": text("face_id")})
        case "xml":
            return Xml.model_validate({**fields, "raw": raw, "data": text("xml_payload")})
        case "light_app":
            return LightApp.model_validate(
                {**fields, "raw": raw, "payload": text("json_payload")}
            )
        case "markdown":
            return Markdown.model_validate({**fields, "raw": raw})
        case "market_face":
            return MarketFace.model_validate({**fields, "raw": raw})
        case _:
            # 其余段类型原样透传，``type`` 保留协议里的原名
            return UnknownSegment.model_validate({**data, "type": seg_type})


def _incoming_uri(seg_type: str, fields: dict[str, Any]) -> str:
    """内容标识符：优先协议端句柄，没有就用临时链接兜底。"""
    handle = fields.get("file_id") if seg_type == "file" else fields.get("resource_id")
    if handle not in (None, ""):
        return f"milky:{handle}"
    return str(fields.get("temp_url") or "")


def _media_fields(seg_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    """各媒体段自己的声明字段。"""
    if seg_type == "image":
        return {
            "summary": str(fields.get("summary") or ""),
            "width": _int_or_none(fields.get("width")),
            "height": _int_or_none(fields.get("height")),
            # Milky 给的就是语义值（normal / sticker），原样收下
            "sub_type": ImageSubType(fields.get("sub_type") or ImageSubType.NORMAL),
        }
    if seg_type == "record":
        return {"duration": to_int(fields.get("duration"))}
    if seg_type == "video":
        return {
            "duration": to_int(fields.get("duration")),
            "width": _int_or_none(fields.get("width")),
            "height": _int_or_none(fields.get("height")),
        }
    if seg_type == "file":
        return {
            "name": str(fields.get("file_name") or ""),
            "size": to_int(fields.get("file_size")),
            "file_hash": str(fields.get("file_hash") or ""),
        }
    return {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is not None:
        logger.debug(f"segments 不是列表（{type(value).__name__}），当空处理")
    return []


def _text_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
