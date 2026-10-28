"""QQ 语义模型 → Milky 的段数组。

编码是**纯函数**，只认已经能直接发的内容。收来的句柄要先过
:func:`resolve_handles` 换成地址 —— 那一跳要发请求，所以单独放在
发送路径上，见 ``MilkyAdapter.send_message``。
"""

from __future__ import annotations

import asyncio
from typing import Any

from ...model.message import RichText
from ...model.segments import (
    At,
    Face,
    File,
    Forward,
    ForwardNode,
    LightApp,
    MediaSegment,
    Reply,
    Segment,
    Text,
    UnknownSegment,
    protocol_uri,
)
from ..base import UnsupportedSegment
from .segments import HANDLE_PREFIX, MEDIA_TYPES, temp_url


def encode_segments(content: RichText) -> list[dict[str, Any]]:
    return [_encode_segment(segment) for segment in content.segments]


# ── 句柄 → 地址 ────────────────────────────────────────────


async def resolve_handles(content: RichText) -> RichText:
    """把内容里的协议端句柄换成能直接发的临时链接。

    Milky 的入站媒体给的是 ``resource_id``，而 ``OutgoingSegment`` 的媒体段
    只认 ``uri``（``file://`` / ``http(s)://`` / ``base64://``）—— **出站
    结构里根本没有 ``resource_id`` 这个字段**。所以"收进来再发出去"绕不过
    这一跳，插件不该各自实现一遍。

    同一层并发换；合并转发里的段一并递归。发不了的段类型（Milky 没有出站
    ``file``）直接跳过，留给 :func:`_encode_segment` 报错。
    """
    segments = await asyncio.gather(*(_resolve_segment(s) for s in content.segments))
    return RichText(segments=list(segments))


async def _resolve_segment(seg: Segment) -> Segment:
    if (
        isinstance(seg, MediaSegment)
        and seg.type in MEDIA_TYPES
        and seg.uri.startswith(HANDLE_PREFIX)
    ):
        url = await temp_url(seg.uri.removeprefix(HANDLE_PREFIX))
        return seg.model_copy(update={"uri": url})
    if isinstance(seg, Forward) and seg.messages:
        nodes = await asyncio.gather(*(_resolve_node(n) for n in seg.messages))
        return seg.model_copy(update={"messages": list(nodes)})
    return seg


async def _resolve_node(node: ForwardNode) -> ForwardNode:
    content = await resolve_handles(RichText(segments=node.content))
    return node.model_copy(update={"content": content.segments})


#: 各媒体段出站允许的字段。``OutgoingSegment`` 是**封闭结构**，出站字段就
#: 这几个：``image`` 是 ``uri`` / ``sub_type`` / ``summary``，``record`` 只有
#: ``uri``，``video`` 是 ``uri`` / ``thumb_uri``。入站专有的 ``width`` /
#: ``height`` / ``duration`` / ``resource_id`` 协议端不认，一概不发。
_OUTGOING_MEDIA_FIELDS: dict[str, tuple[str, ...]] = {
    "image": ("summary", "sub_type"),
    "record": (),
    "video": ("thumb_uri",),
}


def _media_data(seg: MediaSegment) -> dict[str, Any]:
    """媒体段的出站 data：协议认的声明字段 + 内容地址。

    按白名单取，而不是「摊开 ``model_extra`` 再剔掉几个键」：出站结构是封闭
    的，多带的字段协议端不认；更要紧的是跨协议转发时，对方的入站字段
    （OneBot 的 ``file`` / ``url``、Milky 的 ``resource_id``…）会被原样吐出去。
    """
    declared = type(seg).model_fields
    data: dict[str, Any] = {
        field: getattr(seg, field)
        for field in _OUTGOING_MEDIA_FIELDS[seg.type]
        if field in declared
    }
    data["uri"] = _outgoing_uri(seg)
    return _compact(data)


def _outgoing_uri(seg: MediaSegment) -> str:
    """出站内容地址。

    只认协议端认的那几种。协议端句柄到这一步应该已经被
    :func:`resolve_handles` 换掉了 —— ``OutgoingSegment`` 里没有
    ``resource_id`` 字段可填，硬发是发不出去的。

    ``data:`` 由 :func:`protocol_uri` 转成 ``base64://``。
    """
    uri = seg.uri or seg.temp_url or ""
    return protocol_uri(uri) if uri else uri


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

        case File():
            # 出站没有这个段类型（Milky 的 file 走上传 API）
            raise UnsupportedSegment(seg.type, "Milky")

        case MediaSegment() if seg.type in MEDIA_TYPES:
            return {"type": seg.type, "data": _media_data(seg)}

        case MediaSegment():
            # 出站没有这个段类型
            raise UnsupportedSegment(seg.type, "Milky")

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

        case LightApp():
            # app_name 是入站专有的，出站只有 json_payload
            return {
                "type": "light_app",
                "data": _compact({"json_payload": seg.payload}),
            }

        # 未知段：原文原样送回。协议端推过来的东西原样退回去是唯一合理的猜测，
        # 而且从 ``raw`` 里来，没有我们臆造的字段。
        case UnknownSegment():
            return seg.model_dump()

        case _:
            # 其余段统统不在 OutgoingSegment 里（Milky 的 xml / markdown /
            # market_face 只有入站，OneBot 的 poke / share / music… 更没有）。
            # 报错好过发个协议端不认识的东西出去。
            raise UnsupportedSegment(seg.type, "Milky")


#: 编码时当作「没填」的值
_EMPTY = (None, "", [], {})


def _compact(data: dict[str, Any]) -> dict[str, Any]:
    """去掉空值。

    ``0`` 和 ``False`` 不算空 —— ``emoji_package_id == 0`` 是有效值。
    """
    return {k: v for k, v in data.items() if v not in _EMPTY}
