"""QQ 语义模型 → Milky 的段数组。"""

from __future__ import annotations

from typing import Any

from ...model.message import RichText
from ...model.segments import (
    URL_SCHEMES,
    At,
    Face,
    Forward,
    LightApp,
    Markdown,
    MarketFace,
    MediaSegment,
    Reply,
    Segment,
    Text,
    UnknownSegment,
    UnresolvableUri,
    Xml,
)


def encode_segments(content: RichText) -> list[dict[str, Any]]:
    return [_encode_segment(segment) for segment in content.segments]


#: 媒体段自己的声明字段。decode 把它们写成了字段（不是 model_extra），
#: 只摊 model_extra 的话收进来再发出去就掉了。
_MEDIA_FIELDS = ("summary", "width", "height", "duration", "name", "size")


def _media_data(seg: MediaSegment) -> dict[str, Any]:
    """媒体段的出站 data：协议扩展 + 自己的声明字段 + 内容标识。"""
    data: dict[str, Any] = dict(seg.model_extra or {})
    declared = type(seg).model_fields
    for field in _MEDIA_FIELDS:
        if field in declared and field not in data:
            data[field] = getattr(seg, field)
    key, value = _outgoing_content(seg)
    data[key] = value
    if seg.type == "image":
        data.setdefault("sub_type", "normal")
    return _compact(data)


def _outgoing_content(seg: MediaSegment) -> tuple[str, str]:
    """出站内容标识，返回 ``(参数名, 值)``。

    ``milky:`` 是收来的协议端句柄，脱掉前缀还原成 ``resource_id`` —— 原样
    发 ``milky:res-1`` 协议端认不出来。别的协议句柄（比如 OneBot 的
    ``onebot:abc.jpg``）本协议端也认不出来，直接报错让调用方先去
    ``await seg.get_url()``。
    """
    uri = seg.uri or seg.temp_url or ""
    if uri.startswith("milky:"):
        return "resource_id", uri.removeprefix("milky:")
    if not uri or uri.startswith(URL_SCHEMES):
        return "uri", uri
    raise UnresolvableUri(uri)


def _encode_forward(seg: Forward) -> dict[str, Any]:
    """Milky 的合并转发是**一个**段，内容全塞在里面（OneBot 那边要拆成 node 数组）。"""
    data: dict[str, Any] = {
        "messages": [
            {
                "user_id": node.user_id,
                "sender_name": node.name,
                **({"time": node.time} if node.time else {}),
                "segments": encode_segments(RichText(segments=node.content)),
            }
            for node in seg.messages
        ]
    }
    for key in ("title", "summary", "prompt"):
        value = getattr(seg, key)
        if value:
            data[key] = value
    if seg.preview:
        data["preview"] = seg.preview
    return {"type": "forward", "data": data}


def _encode_segment(seg: Segment) -> dict[str, Any]:
    match seg:
        case Text():
            return {"type": "text", "data": {"text": seg.text}}

        case At():
            if seg.is_all:
                return {"type": "mention_all", "data": {}}
            return {"type": "mention", "data": {"user_id": seg.user_id}}

        case Reply():
            # Milky 引用消息只认序列号，其它字段它自己查
            return {
                "type": "reply",
                "data": {"message_seq": seg.ref.seq if seg.ref else 0},
            }

        case MediaSegment():
            return {"type": seg.type, "data": _media_data(seg)}

        case Forward():
            # 只有引用时原样送回；展开成空 messages 等于把段弄丢
            if not seg.messages and seg.forward_id:
                return {
                    "type": "forward",
                    "data": {"forward_id": seg.forward_id},
                }
            return _encode_forward(seg)

        case Face():
            return {
                "type": "face",
                "data": _compact({"face_id": seg.id, "is_large": seg.is_large}),
            }

        case Xml():
            return {"type": "xml", "data": _compact({"xml_payload": seg.data})}

        case LightApp():
            return {
                "type": "light_app",
                "data": _compact(
                    {"json_payload": seg.payload, "app_name": seg.app_name}
                ),
            }

        case Markdown():
            return {"type": "markdown", "data": {"content": seg.content}}

        case MarketFace():
            return {
                "type": "market_face",
                "data": _compact(
                    {
                        "emoji_package_id": seg.emoji_package_id,
                        "emoji_id": seg.emoji_id,
                        "key": seg.key,
                        "summary": seg.summary,
                        "url": seg.url,
                    }
                ),
            }

        # 未知段：原文原样送回
        case UnknownSegment():
            return seg.model_dump()

        case _:
            # Milky 没有的段（OneBot 的 poke/share/music…）照实发，由协议端报错
            return {
                "type": seg.type,
                "data": {k: v for k, v in seg.model_dump().items() if k != "type"},
            }


#: 编码时当作「没填」的值
_EMPTY = (None, "", [], {})


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """去掉空值。

    ``0`` 和 ``False`` 不算空 —— ``emoji_package_id == 0`` 是有效值。
    """
    return {k: v for k, v in data.items() if v not in _EMPTY}
