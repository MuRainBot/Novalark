"""OneBot 的段子类：把协议句柄解成可访问的地址。

OneBot 用 ``file`` 指向内容：收到时是文件名，要地址得另外调接口。基类的
``get_url()`` 解不了这种句柄，这里把 ``_resolve`` 补上。
"""

from __future__ import annotations

from ...model.segments import (
    Forward,
    ForwardNode,
    Image,
    Record,
    UnresolvableUri,
    Video,
)
from ..bot import get_bot


#: 收来的协议端句柄长这样
HANDLE_PREFIX = "onebot:"

#: 段类型 → 取地址的动作。标准里只有图片和语音有「取地址」的接口，视频没有。
MEDIA_ACTIONS = {
    "image": "get_image",
    "record": "get_record",
}


async def _fetch(action: str, uri: str) -> str:
    """调 OneBot 的取媒体接口，从返回值里挑出地址。"""
    handle = uri.removeprefix(HANDLE_PREFIX)
    data = (await get_bot().call(action, {"file": handle})).unwrap()

    if isinstance(data, dict):
        url = data.get("url") or data.get("file")
        if url:
            return str(url)
    if isinstance(data, str) and data:
        return data
    raise UnresolvableUri(uri)


async def resolve_media(seg_type: str, uri: str) -> str:
    """``onebot:<file>`` → 能访问的地址。

    适配器（``OneBotV11Adapter._media_url``）和段子类共用这一条。
    """
    action = MEDIA_ACTIONS.get(seg_type)
    if action is None or not uri.startswith(HANDLE_PREFIX):
        raise UnresolvableUri(uri)
    return await _fetch(action, uri)


class OneBotImage(Image):
    async def _resolve(self) -> str:
        return await resolve_media(self.type, self.uri)


class OneBotRecord(Record):
    async def _resolve(self) -> str:
        return await resolve_media(self.type, self.uri)


class OneBotForward(Forward):
    """合并转发。``_load`` 补上基类缺的那一跳：拿 ``forward_id`` 去取内容。"""

    async def _load(self) -> list[ForwardNode]:
        if not self.forward_id:
            raise UnresolvableUri("")
        return await get_bot().get_forwarded(self.forward_id)


#: 段类型 → 该造的类。标准里没有视频取地址的接口，视频带 url 就够，用基类。
MEDIA_CLASSES = {
    "image": OneBotImage,
    "record": OneBotRecord,
    "video": Video,
}
