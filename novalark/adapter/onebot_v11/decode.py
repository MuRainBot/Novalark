"""OneBot V11 事件 JSON → QQ 语义模型。

OneBot 的三层判别字段（``post_type`` → ``message_type`` / ``notice_type`` →
``sub_type``）在这里折叠成模型里协议无关的扁平 ``kind``。
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ...model.entities import (
    FriendRequest,
    GroupInviteRequest,
    GroupJoinRequest,
    Sender,
)
from ...model.enums import ImageSubType
from ...model.events import (
    Event,
    FileUploadEvent,
    FriendAddEvent,
    FriendMessageEvent,
    FriendRecallEvent,
    FriendRequestEvent,
    GroupAdminEvent,
    GroupHonorEvent,
    GroupLuckyKingEvent,
    GroupMemberDecreaseEvent,
    GroupMemberIncreaseEvent,
    GroupMessageEvent,
    GroupInviteRequestEvent,
    GroupJoinRequestEvent,
    GroupMuteEvent,
    GroupNudgeEvent,
    GroupRecallEvent,
    GroupWholeMuteEvent,
    HeartbeatEvent,
    LifecycleEvent,
    MessageEvent,
    TempMessageEvent,
    UnknownEvent,
)
from ...model.message import Message, RichText
from ...model.refs import ConversationRef, MessageRef, MessageScene
from ...model.segments import (
    URL_SCHEMES,
    Anonymous,
    At,
    Contact,
    Dice,
    Face,
    LightApp,
    Location,
    Music,
    Poke,
    Reply,
    Rps,
    Segment,
    Shake,
    Share,
    Text,
    UnknownSegment,
    Xml,
)
from .._util import int_or_none, text_or_none, to_int
from .segments import MEDIA_CLASSES, OneBotForward

_CQ_PATTERN = re.compile(r"\[CQ:([^,\]]+)((?:,[^\]]*)?)\]")
_CQ_ESCAPE_PATTERN = re.compile(r"&(amp|#44|#91|#93);")
_CQ_UNESCAPE = {"&amp;": "&", "&#44;": ",", "&#91;": "[", "&#93;": "]"}


def decode_event(data: dict[str, Any]) -> Event:
    """把一条 OneBot 上报解码成模型事件。识别不了一律走 UnknownEvent。"""
    post_type = str(data.get("post_type") or "")
    common: dict[str, Any] = {
        "time": to_int(data.get("time")),
        "self_id": to_int(data.get("self_id")),
        "raw": dict(data),
    }

    match post_type:
        case "message":
            return _guard(_decode_message, data, "ob11:message", **common)
        case "notice":
            return _guard(_decode_notice, data, "ob11:notice", **common)
        case "request":
            return _guard(_decode_request, data, "ob11:request", **common)
        case "meta_event":
            return _guard(_decode_meta, data, "ob11:meta_event", **common)
        case _:
            return UnknownEvent(kind=f"ob11:{post_type or 'unknown'}", **common)


def _guard(
    decoder: "Callable[..., Event]", data: dict[str, Any], kind: str, **common: Any
) -> Event:
    """解不出来就退回 UnknownEvent。"""
    try:
        return decoder(data, **common)
    except Exception:
        return UnknownEvent(kind=kind, **common)


# ── 通知 ───────────────────────────────────────────────────


def _group_ref(group_id: int, data: dict[str, Any]) -> MessageRef:
    """群通知里定位一条消息：群号在事件上，序列号在 ``message_id`` 里。"""
    return MessageRef(
        conversation=ConversationRef(scene=MessageScene.GROUP, peer_id=group_id),
        seq=to_int(data.get("message_id")),
    )


def _friend_ref(user_id: int, data: dict[str, Any]) -> MessageRef:
    return MessageRef(
        conversation=ConversationRef(scene=MessageScene.FRIEND, peer_id=user_id),
        seq=to_int(data.get("message_id")),
    )


def _decode_notice(data: dict[str, Any], **common: Any) -> Event:
    notice_type = str(data.get("notice_type") or "")
    sub_type = str(data.get("sub_type") or "")
    group_id = to_int(data.get("group_id"))
    user_id = to_int(data.get("user_id"))
    operator_id = to_int(data.get("operator_id"))

    def file_field(key: str, default: Any = "") -> Any:
        raw = data.get("file")
        return raw.get(key, default) if isinstance(raw, dict) else default

    match notice_type:
        case "group_recall":
            return GroupRecallEvent(
                group_id=group_id,
                ref=_group_ref(group_id, data),
                sender_id=user_id,
                operator_id=operator_id,
                **common,
            )
        case "friend_recall":
            return FriendRecallEvent(
                user_id=user_id,
                ref=_friend_ref(user_id, data),
                sender_id=user_id,
                **common,
            )
        case "group_admin":
            return GroupAdminEvent(
                group_id=group_id, user_id=user_id, is_set=sub_type == "set", **common
            )
        case "group_increase":
            return GroupMemberIncreaseEvent(
                group_id=group_id,
                user_id=user_id,
                # OneBot 用 sub_type 区分，统一成「谁批准的/谁邀请的」
                operator_id=operator_id if sub_type == "approve" else 0,
                invitor_id=operator_id if sub_type == "invite" else 0,
                **common,
            )
        case "group_decrease":
            return GroupMemberDecreaseEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                kicked=sub_type in ("kick", "kick_me"),
                **common,
            )
        case "group_ban":
            # user_id 为 0 表示全员禁言，那是另一个概念
            if user_id == 0:
                return GroupWholeMuteEvent(
                    group_id=group_id,
                    operator_id=operator_id,
                    is_mute=sub_type == "ban",
                    **common,
                )
            return GroupMuteEvent(
                group_id=group_id,
                user_id=user_id,
                operator_id=operator_id,
                duration=to_int(data.get("duration")),
                **common,
            )
        case "friend_add":
            return FriendAddEvent(user_id=user_id, **common)
        case "group_upload":
            return FileUploadEvent(
                group_id=group_id,
                user_id=user_id,
                file_id=str(file_field("id")),
                name=str(file_field("name")),
                size=to_int(file_field("size", 0)),
                **common,
            )
        case "notify":
            return _decode_notify(sub_type, data, **common)
        case _:
            return UnknownEvent(kind=f"ob11:notice:{notice_type}", **common)


def _decode_notify(sub_type: str, data: dict[str, Any], **common: Any) -> Event:
    """notify 是 OneBot 里的一层套娃：notice_type 下面还有 sub_type。"""
    group_id = to_int(data.get("group_id"))
    user_id = to_int(data.get("user_id"))
    target_id = to_int(data.get("target_id"))

    match sub_type:
        case "poke":
            # 标准只定义了「群内戳一戳」，没有 group_id 的私聊形态是实现端
            # 自己扩的
            if not group_id:
                return UnknownEvent(kind="ob11:notice:notify:poke", **common)
            return GroupNudgeEvent(
                group_id=group_id,
                sender_id=user_id,
                receiver_id=target_id,
                **common,
            )
        case "lucky_king":
            return GroupLuckyKingEvent(
                group_id=group_id, user_id=user_id, target_id=target_id, **common
            )
        case "honor":
            return GroupHonorEvent(
                group_id=group_id,
                user_id=user_id,
                honor_type=data.get("honor_type") or None,
                **common,
            )
        case _:
            return UnknownEvent(kind=f"ob11:notice:notify:{sub_type}", **common)


# ── 请求 ───────────────────────────────────────────────────


def _decode_request(data: dict[str, Any], **common: Any) -> Event:
    request_type = str(data.get("request_type") or "")
    flag = str(data.get("flag") or "")
    # sub_type 决定批准时该用哪种凭据，丢了就批不动
    sub_type = str(data.get("sub_type") or "")

    match request_type:
        case "friend":
            return FriendRequestEvent(
                request=FriendRequest(
                    user_id=to_int(data.get("user_id")),
                    comment=str(data.get("comment") or ""),
                    flag=flag,
                ),
                **common,
            )
        case "group":
            group_id = to_int(data.get("group_id"))
            comment = str(data.get("comment") or "")
            # sub_type 决定这是哪一类请求，也决定批准时要带哪种凭据
            if sub_type == "invite":
                # 被邀请进群，发起人是 user_id
                return GroupInviteRequestEvent(
                    request=GroupInviteRequest(
                        group_id=group_id,
                        invitor_id=to_int(data.get("user_id")),
                        comment=comment,
                        flag=flag,
                    ),
                    **common,
                )
            return GroupJoinRequestEvent(
                request=GroupJoinRequest(
                    group_id=group_id,
                    user_id=to_int(data.get("user_id")),
                    comment=comment,
                    flag=flag,
                ),
                **common,
            )
        case _:
            return UnknownEvent(kind=f"ob11:request:{request_type}", **common)


# ── 元事件 ─────────────────────────────────────────────────


def _decode_meta(data: dict[str, Any], **common: Any) -> Event:
    meta_type = str(data.get("meta_event_type") or "")

    match meta_type:
        case "lifecycle":
            return LifecycleEvent(state=data.get("sub_type") or None, **common)
        case "heartbeat":
            status = data.get("status")
            alive = status.get("online", True) if isinstance(status, dict) else True
            return HeartbeatEvent(alive=bool(alive), **common)
        case _:
            return UnknownEvent(kind=f"ob11:meta_event:{meta_type}", **common)


# ── 消息 ───────────────────────────────────────────────────


def _decode_message(data: dict[str, Any], **common: Any) -> MessageEvent:
    conversation = _decode_conversation(data)
    ref = MessageRef(conversation=conversation, seq=to_int(data.get("message_id")))
    message = Message(
        ref=ref,
        content=_decode_content(data.get("message"), conversation),
        sender=_decode_sender(data.get("sender"), data.get("user_id")),
    )

    cls = {
        MessageScene.GROUP: GroupMessageEvent,
        MessageScene.FRIEND: FriendMessageEvent,
        MessageScene.TEMP: TempMessageEvent,
    }[conversation.scene]
    return cls(message=message, **common)


def _decode_conversation(data: dict[str, Any]) -> ConversationRef:
    message_type = str(data.get("message_type") or "")
    user_id = to_int(data.get("user_id"))
    group_id = data.get("group_id")

    if message_type == "group":
        return ConversationRef(scene=MessageScene.GROUP, peer_id=to_int(group_id))
    if message_type == "private":
        # sub_type == "group" 是群临时会话：peer 是用户，但上下文属于某个群，
        # 那个群号得留着 —— 没有它连回复都发不出去
        if str(data.get("sub_type") or "") == "group" and group_id is not None:
            return ConversationRef(
                scene=MessageScene.TEMP,
                peer_id=user_id,
                parent_group_id=to_int(group_id),
            )
        return ConversationRef(scene=MessageScene.FRIEND, peer_id=user_id)
    if group_id is not None:
        return ConversationRef(scene=MessageScene.GROUP, peer_id=to_int(group_id))
    return ConversationRef(scene=MessageScene.FRIEND, peer_id=user_id)


def _decode_sender(raw: Any, fallback_id: Any) -> Sender:
    data = raw if isinstance(raw, dict) else {}
    return Sender.model_validate(
        {
            **data,
            "user_id": to_int(data.get("user_id")) or to_int(fallback_id),
            "nickname": str(data.get("nickname") or ""),
            "card": str(data.get("card") or ""),
            # 私聊的 sender 里没有 role，给 None 而不是空串
            "role": data.get("role") or None,
            # title 也在 sender 对象里，不归一化就是个裸的原始值
            "title": str(data.get("title") or ""),
        }
    )


def _decode_content(raw: Any, conversation: ConversationRef) -> RichText:
    if isinstance(raw, list):
        return RichText(segments=[_decode_segment(s, conversation) for s in raw])
    if isinstance(raw, str):
        return _decode_cq(raw, conversation)
    return RichText()


def _decode_segment(data: Any, conversation: ConversationRef) -> Segment:
    if not isinstance(data, dict):
        return UnknownSegment.model_validate({"type": "unknown", "value": data})

    seg_type = str(data.get("type") or "")
    raw_payload = data.get("data")
    payload = raw_payload if isinstance(raw_payload, dict) else {}

    def text(key: str) -> str:
        return str(payload.get(key) or "")

    # 每个分支都把整个 payload 摊进去：协议扩展和实现端自己加的字段要留在
    # extra 里，原始段 dict 进 ``raw``。``type`` 一律不写 —— 段模型的语义名
    # 由段类自己定，模型层不许出现 OneBot 的段名（``json`` 之类）。
    raw = dict(data)
    match seg_type:
        case "text":
            return Text.model_validate({**payload, "raw": raw})
        case "at":
            qq = text("qq")
            who = None if qq in ("", "all", "0") else to_int(qq)
            return At(user_id=who, raw=raw)
        case "reply":
            ref = MessageRef(conversation=conversation, seq=to_int(payload.get("id")))
            return Reply.model_validate({**payload, "raw": raw, "ref": ref})
        case "image" | "record" | "video":
            cls = MEDIA_CLASSES[seg_type]
            return cls.model_validate(
                {
                    **payload,
                    "raw": raw,
                    "uri": _media_uri(payload),
                    "temp_url": text_or_none(payload.get("url")),
                    **_media_fields(seg_type, payload),
                }
            )
        case "forward":
            # 收到的是引用，内容要另外调 get_forward_msg —— 见 OneBotForward.fetch
            return OneBotForward.model_validate(
                {**payload, "raw": raw, "forward_id": text_or_none(payload.get("id"))}
            )
        case "face":
            return Face.model_validate({**payload, "raw": raw})
        case "poke":
            # OneBot 的 type 是表情类型，模型里叫 kind
            return Poke.model_validate({**payload, "raw": raw, "kind": text("type")})
        case "share":
            return Share.model_validate({**payload, "raw": raw})
        case "contact":
            return Contact.model_validate({**payload, "raw": raw, "kind": text("type")})
        case "location":
            return Location.model_validate(
                {
                    **payload,
                    "raw": raw,
                    "lat": float(payload.get("lat") or 0),
                    "lon": float(payload.get("lon") or 0),
                }
            )
        case "music":
            return Music.model_validate({**payload, "raw": raw, "kind": text("type")})
        case "xml":
            return Xml.model_validate({**payload, "raw": raw})
        case "json":
            # OneBot 的 json 段和 Milky 的 light_app 是同一个东西，段类是
            # LightApp，它的语义名由类自己定成 light_app
            return LightApp.model_validate(
                {**payload, "raw": raw, "payload": text("data")}
            )
        case "rps":
            return Rps.model_validate({**payload, "raw": raw})
        case "dice":
            return Dice.model_validate({**payload, "raw": raw})
        case "shake":
            return Shake.model_validate({**payload, "raw": raw})
        case "anonymous":
            raw_ignore = payload.get("ignore")
            ignore = (
                raw_ignore.strip().lower() in ("1", "true", "yes")
                if isinstance(raw_ignore, str)
                else bool(raw_ignore)
            )
            return Anonymous.model_validate({**payload, "raw": raw, "ignore": ignore})
        case _:
            # 其余段类型原样透传，``type`` 保留协议里的原名
            return UnknownSegment.model_validate({**data, "type": seg_type})


def _decode_cq(raw: str, conversation: ConversationRef) -> RichText:
    """解析 CQ 码字符串。字符串格式仍然合法。"""
    segments: list[Segment] = []
    pos = 0

    for match in _CQ_PATTERN.finditer(raw):
        if match.start() > pos:
            text = _unescape_cq(raw[pos : match.start()])
            if text:
                segments.append(Text(text))
        payload = _parse_cq_params(match.group(2))
        segments.append(
            _decode_segment({"type": match.group(1), "data": payload}, conversation)
        )
        pos = match.end()

    if pos < len(raw):
        tail = _unescape_cq(raw[pos:])
        if tail:
            segments.append(Text(tail))

    if not segments:
        segments.append(Text(raw))
    return RichText(segments=segments)


def _parse_cq_params(raw: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in raw.lstrip(",").split(","):
        if not item:
            continue
        key, _, value = item.partition("=")
        params[key.strip()] = _unescape_cq(value)
    return params


def _unescape_cq(text: str) -> str:
    # 一次扫描完成反转义
    return _CQ_ESCAPE_PATTERN.sub(lambda m: _CQ_UNESCAPE[m.group(0)], text)


def _media_uri(payload: dict[str, Any]) -> str:
    """内容标识符：优先 ``file``，没有就退回 ``url``。

    ``file`` 不是 URI 的当协议句柄，加 ``onebot:`` 前缀。
    """
    raw = payload.get("file") or payload.get("url")
    if raw in (None, ""):
        return ""
    text = str(raw)
    return text if text.startswith(URL_SCHEMES) else f"onebot:{text}"


def _media_fields(seg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """各媒体段自己的声明字段。"""
    if seg_type == "image":
        return {
            "summary": str(payload.get("summary") or ""),
            "width": int_or_none(payload.get("width")),
            "height": int_or_none(payload.get("height")),
            # 标准协议没有 sub_type（go-cqhttp 等实现端自己扩展成了 int）。
            # 这边只按标准做，就不采信实现端塞进来的值，一律当普通图片。
            "sub_type": ImageSubType.NORMAL,
        }
    if seg_type == "record":
        return {"duration": to_int(payload.get("duration"))}
    if seg_type == "video":
        return {
            "duration": to_int(payload.get("duration")),
            "width": int_or_none(payload.get("width")),
            "height": int_or_none(payload.get("height")),
        }
    return {}
