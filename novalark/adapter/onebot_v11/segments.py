"""OneBot 的段子类：把协议句柄解成可访问的地址。

OneBot 用 ``file`` 指向内容：收到时是文件名，要地址得另外调接口。基类的
``get_url()`` 解不了这种句柄，这里把 ``_resolve`` 补上。
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


async def _fetch(action: str, uri: str) -> str:
    """调 OneBot 的取媒体接口，从返回值里挑出地址。"""
    handle = uri.removeprefix("onebot:")
    data = (await get_bot().call(action, {"file": handle})).unwrap()

    if isinstance(data, dict):
        url = data.get("url") or data.get("file")
        if url:
            return str(url)
    if isinstance(data, str) and data:
        return data
    raise UnresolvableUri(uri)


class OneBotImage(Image):
    async def _resolve(self) -> str:
        return await _fetch("get_image", self.uri)


class OneBotRecord(Record):
    async def _resolve(self) -> str:
        return await _fetch("get_record", self.uri)


class OneBotFile(File):
    async def _resolve(self) -> str:
        # OneBot 没有「按 file_id 取下载地址」的接口
        raise UnresolvableUri(self.uri)


class OneBotForward(Forward):
    """合并转发。``_load`` 补上基类缺的那一跳：拿 ``forward_id`` 去取内容。"""

    async def _load(self) -> list[ForwardNode]:
        if not self.forward_id:
            raise UnresolvableUri("")
        return await get_bot().get_forwarded(self.forward_id)


#: 段类型 → 该造的类。视频没有取地址的接口，带 url 就够，用基类。
MEDIA_CLASSES = {
    "image": OneBotImage,
    "record": OneBotRecord,
    "video": Video,
    "file": OneBotFile,
}
