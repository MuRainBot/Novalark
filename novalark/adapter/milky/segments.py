"""Milky 的段子类：把 ``resource_id`` 换成可访问的地址。

Milky 收到媒体时只给一个协议端内部的 ``resource_id``，另配一个会过期的
``temp_url``。要新地址就得调 ``get_resource_temp_url``。
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


async def _temp_url(resource_id: str) -> str:
    """问协议端要一个临时链接。"""
    data = (
        await get_bot().call("get_resource_temp_url", {"resource_id": resource_id})
    ).unwrap()
    if isinstance(data, dict) and data.get("url"):
        return str(data["url"])
    raise UnresolvableUri(f"milky:{resource_id}")


class _Resolvable:
    """Milky 的媒体段共用的解析逻辑：句柄长这样 ``milky:<resource_id>``。"""

    uri: str

    async def _resolve(self) -> str:
        if self.uri.startswith("milky:"):
            return await _temp_url(self.uri.removeprefix("milky:"))
        raise UnresolvableUri(self.uri)


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
