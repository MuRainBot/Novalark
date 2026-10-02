"""QQ 语义模型 → OneBot V11 数组格式。

OneBot 表达不了的段类型照实编码发出去，由实现端决定接受与否。
"""

from __future__ import annotations

from typing import Any

from ...model.message import RichText
from ...model.segments import (
    URL_SCHEMES,
    Anonymous,
    At,
    Contact,
    Dice,
    Face,
    Forward,
    LightApp,
    Location,
    MediaSegment,
    Music,
    Poke,
    Reply,
    Rps,
    Segment,
    Shake,
    Share,
    Text,
    UnknownSegment,
    UnresolvableUri,
    Xml,
)


def encode_segments(content: RichText) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for seg in content.segments:
        # OneBot 的合并转发是一串 node 段，不是一个段
        if isinstance(seg, Forward):
            out.extend(_encode_forward(seg))
        else:
            out.append(_encode_segment(seg))
    return out


#: 媒体段自己的声明字段。decode 把它们写成了字段（不是 model_extra），
#: 只摊 model_extra 的话收进来再发出去就掉了。
_MEDIA_FIELDS = ("summary", "width", "height", "duration", "name", "size")

#: 入站专有的键。decode 把整个 ``data`` 摊进模型，``url`` 是入站的原始键
#: （模型里叫 ``temp_url``）；不剔掉的话出站会同时带上 ``file`` 和 ``url``。
_INCOMING_ONLY = frozenset({"url"})


def _encode_forward(seg: Forward) -> list[dict[str, Any]]:
    """合并转发展开成 node 段数组。

    只有引用、没有内容时（收到之后原样转发）退回引用形态 —— 展开成空数组
    的话整段会无声消失。
    """
    if not seg.messages:
        if seg.forward_id:
            return [{"type": "forward", "data": {"id": seg.forward_id}}]
        return []
    return [
        {
            "type": "node",
            "data": {
                "user_id": str(node.user_id),
                "nickname": node.name,
                "content": encode_segments(RichText(segments=node.content)),
            },
        }
        for node in seg.messages
    ]


def _media_data(seg: MediaSegment) -> dict[str, Any]:
    """媒体段的出站 data：协议扩展 + 自己的声明字段 + 内容标识。"""
    data: dict[str, Any] = {
        key: value
        for key, value in (seg.model_extra or {}).items()
        if key not in _INCOMING_ONLY
    }
    declared = type(seg).model_fields
    for field in _MEDIA_FIELDS:
        if field in declared and field not in data:
            data[field] = getattr(seg, field)
    data["file"] = _onebot_file(seg)
    return _compact(data)


def _encode_segment(seg: Segment) -> dict[str, Any]:
    match seg:
        case Text():
            return {"type": "text", "data": {"text": seg.text}}

        case At():
            return {
                "type": "at",
                "data": {"qq": "all" if seg.is_all else str(seg.user_id)},
            }

        case Reply():
            return {
                "type": "reply",
                "data": {"id": str(seg.ref.seq) if seg.ref else ""},
            }

        # 图片 / 语音 / 视频 / 文件：OneBot 一律走 file 字段
        case MediaSegment():
            return {"type": seg.type, "data": _media_data(seg)}

        case Face():
            # OneBot 没有超级表情的概念，is_large 不发
            return {"type": "face", "data": {"id": seg.id}}

        case Poke():
            return {
                "type": "poke",
                "data": _compact({"type": seg.kind, "id": seg.id, "name": seg.name}),
            }

        case Share():
            return {
                "type": "share",
                "data": _compact(
                    {
                        "url": seg.url,
                        "title": seg.title,
                        "content": seg.content,
                        "image": seg.image,
                    }
                ),
            }

        case Contact():
            return {
                "type": "contact",
                "data": _compact({"type": seg.kind, "id": seg.id}),
            }

        case Location():
            return {
                "type": "location",
                "data": _compact(
                    {
                        "lat": str(seg.lat),
                        "lon": str(seg.lon),
                        "title": seg.title,
                        "content": seg.content,
                    }
                ),
            }

        case Music():
            return {
                "type": "music",
                "data": _compact(
                    {
                        "type": seg.kind,
                        "id": seg.id,
                        "url": seg.url,
                        "audio": seg.audio,
                        "title": seg.title,
                        "content": seg.content,
                        "image": seg.image,
                    }
                ),
            }

        case Xml():
            return {
                "type": "xml",
                "data": _compact({"data": seg.data, "service_id": seg.service_id}),
            }

        case LightApp():
            # 和 Milky 的 light_app 是同一个东西，这边叫 json
            return {
                "type": "json",
                "data": _compact({"data": seg.payload, "app_name": seg.app_name}),
            }

        case Rps():
            return {"type": "rps", "data": {}}

        case Dice():
            return {"type": "dice", "data": {}}

        case Shake():
            return {"type": "shake", "data": {}}

        case Anonymous():
            return {
                "type": "anonymous",
                "data": _compact({"ignore": "1" if seg.ignore else ""}),
            }

        # 未知段：原文原样送回
        case UnknownSegment():
            return seg.model_dump()

        case _:
            return {
                "type": seg.type,
                "data": {k: v for k, v in seg.model_dump().items() if k != "type"},
            }


#: 编码时当作「没填」的值
_EMPTY = (None, "", [], {})


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """去掉空值。

    ``0`` 和 ``False`` 不算空 —— ``Contact.id == 0``、``Location.lat == 0.0``
    都是有效值，顺手丢掉是错的。
    """
    return {k: v for k, v in data.items() if v not in _EMPTY}


def _onebot_file(seg: MediaSegment) -> str:
    """uri 转回 OneBot 的 ``file``。

    ``onebot:`` 是收来的协议端句柄，脱掉前缀；地址原样送。别的协议的句柄
    （Milky 的 ``milky:res-1``）实现端认不出来，报错让调用方先去
    ``await seg.get_url()``。
    """
    uri = seg.uri or seg.temp_url or ""
    if uri.startswith("onebot:"):
        return uri.removeprefix("onebot:")
    if not uri or uri.startswith(URL_SCHEMES):
        return uri
    raise UnresolvableUri(uri)
