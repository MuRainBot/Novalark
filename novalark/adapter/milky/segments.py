"""Milky 的段子类：把 ``resource_id`` 换成可访问的地址。

Milky 收到媒体时只给一个协议端内部的 ``resource_id``，另配一个会过期的
``temp_url``。要新地址就得调 ``get_resource_temp_url``。

这不只是"想要个能下载的地址"：``OutgoingSegment`` 的媒体段只认 ``uri``，
**入站结构里的 ``resource_id`` 在出站结构里没有对应字段**，所以转发收到的
媒体必须走这一跳。见 :func:`novalark.adapter.milky.encode.resolve_handles`。
"""

from __future__ import annotations

from ...model.segments import (
    File,
    Forward,
    ForwardNode,
    Image,
    Record,
    UnresolvableUri,
    Video,
)
from ..bot import get_bot


#: 收来的协议端句柄长这样
HANDLE_PREFIX = "milky:"

#: Milky 认的媒体段，也是句柄能当 ``resource_id`` 用的那三个。
#: ``OutgoingSegment`` 只有 image / record / video —— 它**没有 ``file``**
#: （发文件走 ``upload_*`` API），而 file 段的句柄是 ``file_id``，和
#: ``resource_id`` 不是一回事。
MEDIA_TYPES = ("image", "record", "video")


async def temp_url(resource_id: str) -> str:
    """问协议端要一个临时链接。"""
    data = (
        await get_bot().call("get_resource_temp_url", {"resource_id": resource_id})
    ).unwrap()
    if isinstance(data, dict) and data.get("url"):
        return str(data["url"])
    raise UnresolvableUri(f"{HANDLE_PREFIX}{resource_id}")


async def resolve_media(seg_type: str, uri: str) -> str:
    """``milky:<resource_id>`` → 能访问的地址。

    适配器（``MilkyAdapter._media_url``）和段子类共用这一条。文件段不在这里：
    它的句柄是 ``file_id``，取下载链接要带群号调
    ``get_group_file_download_url``，而段里没有群号。
    """
    if seg_type in MEDIA_TYPES and uri.startswith(HANDLE_PREFIX):
        return await temp_url(uri.removeprefix(HANDLE_PREFIX))
    raise UnresolvableUri(uri)


class _Resolvable:
    """Milky 的媒体段共用的解析逻辑。

    实现只有适配器那一份 —— 这里只是把 ``get_url()`` 接到它上面。
    """

    uri: str
    type: str

    async def _resolve(self) -> str:
        return await resolve_media(self.type, self.uri)


class MilkyImage(_Resolvable, Image):
    pass


class MilkyRecord(_Resolvable, Record):
    pass


class MilkyVideo(_Resolvable, Video):
    pass


class MilkyFile(_Resolvable, File):
    pass


class MilkyForward(Forward):
    """合并转发。``_load`` 补上基类缺的那一跳：拿 ``forward_id`` 去取内容。"""

    async def _load(self) -> list[ForwardNode]:
        if not self.forward_id:
            raise UnresolvableUri("")
        return await get_bot().get_forwarded(self.forward_id)


#: 段类型 → 该造的类
MEDIA_CLASSES = {
    "image": MilkyImage,
    "record": MilkyRecord,
    "video": MilkyVideo,
    "file": MilkyFile,
}
