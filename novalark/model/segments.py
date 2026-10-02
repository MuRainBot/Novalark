"""消息段。

类型覆盖各协议的并集。未知类型一律降级为 :class:`UnknownSegment` 并保留
原始数据，不做不可逆的文本降级。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    ValidationError,
    field_validator,
    model_validator,
)

from .enums import ContactKind, ImageSubType, MusicKind
from .refs import MessageRef

if TYPE_CHECKING:
    from .message import RichText

# uri 里这些前缀表示「本身就是个能访问的地址」
URL_SCHEMES = ("http://", "https://", "file://", "base64://", "data:")


class UnresolvableUri(Exception):
    """``uri`` 是协议句柄，但当前适配器解不了它。

    多半是拿另一个协议收到的段去发，或者句柄已经失效。
    """

    def __init__(self, uri: str) -> None:
        super().__init__(f"无法解析的资源标识: {uri!r}")
        self.uri = uri


class Segment(BaseModel):
    """消息段基类。

    ``type`` 是**模型层的语义名**，恒等于段类自己声明的那个值。协议里的段名
    （OneBot 的 ``json``、Milky 的 ``mention``）不许从这里泄漏出去 —— 判断段
    类型直接比较 ``type`` 或用 ``isinstance``，两个协议结果都一样。

    ``type`` 本身不是枚举：:class:`UnknownSegment` 要原样留住没见过的段名，
    拿枚举圈住就等于把「协议加了新段」变成解码失败。

    ``raw`` 是收到时的协议原文，不参与序列化，和 :class:`Event` 的同名字段
    对称。
    """

    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)

    #: 未知段保留协议给的 ``type``；其余段一律回写成本类的语义名
    preserve_type: ClassVar[bool] = False

    type: str = "unknown"
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)

    @model_validator(mode="before")
    @classmethod
    def _drop_null(cls, data: Any) -> Any:
        """协议把声明字段给成 ``null`` 时当作没给，走默认值。

        未声明的键原样留着 —— 那是协议扩展，要进 ``model_extra``。
        """
        if not isinstance(data, dict):
            return data
        return {
            key: value
            for key, value in data.items()
            if value is not None or key not in cls.model_fields
        }

    @model_validator(mode="after")
    def _canonical_type(self) -> Self:
        """把 ``type`` 钉死成本类的语义名。

        ``coerce_numbers_to_str`` 管不住这里：协议传 ``type="json"`` 时字段
        会被赋成 ``"json"``，得再回写一次。
        """
        if not type(self).preserve_type:
            self.type = type(self).model_fields["type"].default
        return self


class Text(Segment):
    type: str = "text"
    text: str = ""

    def __init__(self, text: str = "", **kwargs: Any) -> None:
        super().__init__(text=text, **kwargs)


class At(Segment):
    """@某人。``user_id`` 为 ``None`` 表示 @全体，用 ``is_all`` 判断。

    ``name`` 是 Milky 给的显示名（**已经去掉 ``@`` 前缀**），OneBot 不给。
    """

    type: str = "at"
    user_id: int | None = None
    name: str = ""

    def __init__(
        self, user_id: int | None = None, name: str = "", **kwargs: Any
    ) -> None:
        super().__init__(user_id=user_id, name=name, **kwargs)

    @property
    def is_all(self) -> bool:
        return self.user_id is None

    @classmethod
    def all(cls) -> "At":
        return cls()


class MediaSegment(Segment):
    """带外部内容的段：图片、语音、视频、文件。

    ``uri`` 是内容标识符，**不保证能直接访问**：它可能是 ``http(s)://`` /
    ``file://`` / ``base64://`` 这类地址，也可能是 ``onebot:abc.jpg`` 这样的
    协议句柄。要一个能访问的地址就调 :meth:`get_url`。

    ``temp_url`` 是收到时的临时地址快照，会过期。发送时给 ``uri`` 即可。
    """

    uri: str = ""
    temp_url: str | None = None

    def __init__(
        self,
        uri: str = "",
        temp_url: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(uri=uri, temp_url=temp_url, **kwargs)

    async def get_url(self) -> str:
        """拿一个能访问的地址。

        ``uri`` 本身是地址就直接给；是协议句柄就问适配器换。

        ``temp_url`` 是收到那一刻的快照，**可能已经过期**，本方法不使用它。

        只对**收来的**段管用：解句柄要认识协议，而模型层不认识。段是自己拿
        句柄字符串造的（基类）时走 ``Bot.media_url(seg)``。
        """
        if self.uri.startswith(URL_SCHEMES):
            return self.uri
        return await self._resolve()

    async def _resolve(self) -> str:
        """把协议句柄换成地址。适配器子类重写它。"""
        raise UnresolvableUri(self.uri)


class Image(MediaSegment):
    """图片。

    ``sub_type`` 用 Milky 的语义值（``normal`` / ``sticker`` 表情包）。OneBot
    **标准协议没有这个字段** —— 实现端（go-cqhttp 等）自己扩展成了 int，本框架
    不采信，从那边收到的图片恒为 ``normal``。
    """

    type: str = "image"
    summary: str = ""
    width: int | None = None
    height: int | None = None
    sub_type: ImageSubType = ImageSubType.NORMAL

    def __init__(
        self,
        uri: str = "",
        temp_url: str | None = None,
        summary: str = "",
        width: int | None = None,
        height: int | None = None,
        sub_type: ImageSubType = ImageSubType.NORMAL,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            uri=uri,
            temp_url=temp_url,
            summary=summary,
            width=width,
            height=height,
            sub_type=sub_type,
            **kwargs,
        )


class Record(MediaSegment):
    """语音。"""

    type: str = "record"
    duration: int = 0

    def __init__(
        self,
        uri: str = "",
        temp_url: str | None = None,
        duration: int = 0,
        **kwargs: Any,
    ) -> None:
        super().__init__(uri=uri, temp_url=temp_url, duration=duration, **kwargs)


class Video(MediaSegment):
    """视频。

    ``thumb_uri`` 是发送时的封面，**只有 Milky 的 ``OutgoingSegment`` 认**，
    且是可选字段。OneBot 没有对应概念，收到的一律是 ``None``。
    """

    type: str = "video"
    duration: int = 0
    width: int | None = None
    height: int | None = None
    thumb_uri: str | None = None

    def __init__(
        self,
        uri: str = "",
        temp_url: str | None = None,
        duration: int = 0,
        width: int | None = None,
        height: int | None = None,
        thumb_uri: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            uri=uri,
            temp_url=temp_url,
            duration=duration,
            width=width,
            height=height,
            thumb_uri=thumb_uri,
            **kwargs,
        )


class File(MediaSegment):
    """文件。

    ``file_hash`` 是 Milky 给的 TriSHA1（**仅私聊文件有**），OneBot 不给。
    """

    type: str = "file"
    name: str = ""
    size: int = 0
    file_hash: str = ""

    def __init__(
        self,
        uri: str = "",
        temp_url: str | None = None,
        name: str = "",
        size: int = 0,
        file_hash: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            uri=uri,
            temp_url=temp_url,
            name=name,
            size=size,
            file_hash=file_hash,
            **kwargs,
        )


class Face(Segment):
    """QQ 表情。

    ``is_large`` 是 Milky 的超级表情，OneBot 上为 ``False``。
    """

    type: str = "face"
    id: str = ""
    is_large: bool = False

    def __init__(self, id: str = "", is_large: bool = False, **kwargs: Any) -> None:
        super().__init__(id=id, is_large=is_large, **kwargs)


class ForwardNode(BaseModel):
    """合并转发里的一条消息。"""

    user_id: int = 0
    name: str = ""
    time: int = 0
    content: list[SerializeAsAny[Segment]] = Field(default_factory=list)

    def __init__(
        self,
        content: "list[Segment] | RichText | str | None" = None,
        **data: Any,
    ) -> None:
        if content is not None:
            data["content"] = as_segments(content)
        super().__init__(**data)

    @property
    def text(self) -> str:
        """里面的纯文本。"""
        return "".join(seg.text for seg in self.content if isinstance(seg, Text))


class Forward(Segment):
    """合并转发。

    接收时协议给的是**引用**（``forward_id`` 加展示用的元数据），发送时
    要的是**内容**（``messages``）。取内容用 :meth:`fetch`。
    """

    type: str = "forward"
    forward_id: str | None = None
    title: str | None = None
    preview: list[str] = Field(default_factory=list)
    summary: str | None = None
    prompt: str | None = None
    messages: list[ForwardNode] = Field(default_factory=list)

    def __init__(
        self,
        messages: "list[ForwardNode] | None" = None,
        forward_id: str | None = None,
        title: str | None = None,
        preview: list[str] | None = None,
        summary: str | None = None,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            messages=list(messages or []),
            forward_id=forward_id,
            title=title,
            preview=list(preview or []),
            summary=summary,
            prompt=prompt,
            **kwargs,
        )

    async def fetch(self) -> list[ForwardNode]:
        """取转发的内容。

        手上已经有（自己构造的）就直接给；只有引用时问适配器要。
        """
        if self.messages:
            return self.messages
        return await self._load()

    async def _load(self) -> list[ForwardNode]:
        """按引用取内容。适配器子类重写。"""
        raise UnresolvableUri(self.forward_id or "")


class Xml(Segment):
    """XML 卡片。"""

    type: str = "xml"
    data: str = ""
    service_id: int | None = None  # Milky 有，OneBot 没有

    def __init__(
        self, data: str = "", service_id: int | None = None, **kwargs: Any
    ) -> None:
        super().__init__(data=data, service_id=service_id, **kwargs)


class LightApp(Segment):
    """小程序卡片。

    OneBot 的 ``json`` 段和 Milky 的 ``light_app`` 是同一个东西；
    ``app_name`` 只有 Milky 给，OneBot 上是空串。
    """

    type: str = "light_app"
    payload: str = ""
    app_name: str = ""

    def __init__(self, payload: str = "", app_name: str = "", **kwargs: Any) -> None:
        super().__init__(payload=payload, app_name=app_name, **kwargs)


# ── 只有 Milky 有的段 ──────────────────────────────────────


class Markdown(Segment):
    """Markdown 消息。"""

    type: str = "markdown"
    content: str = ""

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(content=content, **kwargs)


class MarketFace(Segment):
    """市场表情（表情包）。"""

    type: str = "market_face"
    emoji_package_id: int = 0
    emoji_id: str = ""
    key: str = ""
    summary: str = ""
    url: str = ""

    def __init__(
        self,
        emoji_package_id: int = 0,
        emoji_id: str = "",
        key: str = "",
        summary: str = "",
        url: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            emoji_package_id=emoji_package_id,
            emoji_id=emoji_id,
            key=key,
            summary=summary,
            url=url,
            **kwargs,
        )


# ── 只有 OneBot 有的段 ─────────────────────────────────────


class Poke(Segment):
    """戳一戳（OneBot 把它当段发；Milky 那边是个事件）。"""

    type: str = "poke"
    kind: str = ""
    id: str = ""
    name: str = ""

    def __init__(
        self, kind: str = "", id: str = "", name: str = "", **kwargs: Any
    ) -> None:
        super().__init__(kind=kind, id=id, name=name, **kwargs)


class Rps(Segment):
    """猜拳。"""

    type: str = "rps"


class Dice(Segment):
    """掷骰子。"""

    type: str = "dice"


class Shake(Segment):
    """窗口抖动。"""

    type: str = "shake"


class Anonymous(Segment):
    """匿名发消息。"""

    type: str = "anonymous"
    ignore: bool = False

    def __init__(self, ignore: bool = False, **kwargs: Any) -> None:
        super().__init__(ignore=ignore, **kwargs)


class Share(Segment):
    """链接分享。"""

    type: str = "share"
    url: str = ""
    title: str = ""
    content: str = ""
    image: str = ""

    def __init__(
        self,
        url: str = "",
        title: str = "",
        content: str = "",
        image: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(url=url, title=title, content=content, image=image, **kwargs)


class Contact(Segment):
    """推荐好友 / 群。"""

    type: str = "contact"
    kind: ContactKind = ContactKind.QQ
    id: int = 0

    def __init__(
        self, kind: ContactKind = ContactKind.QQ, id: int = 0, **kwargs: Any
    ) -> None:
        super().__init__(kind=kind, id=id, **kwargs)


class Location(Segment):
    """位置。"""

    type: str = "location"
    lat: float = 0.0
    lon: float = 0.0
    title: str = ""
    content: str = ""

    def __init__(
        self,
        lat: float = 0.0,
        lon: float = 0.0,
        title: str = "",
        content: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(lat=lat, lon=lon, title=title, content=content, **kwargs)


class Music(Segment):
    """音乐分享。"""

    type: str = "music"
    kind: MusicKind = MusicKind.QQ
    id: str = ""
    url: str = ""
    audio: str = ""
    title: str = ""
    content: str = ""
    image: str = ""

    def __init__(
        self,
        kind: MusicKind = MusicKind.QQ,
        id: str = "",
        url: str = "",
        audio: str = "",
        title: str = "",
        content: str = "",
        image: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(
            kind=kind,
            id=id,
            url=url,
            audio=audio,
            title=title,
            content=content,
            image=image,
            **kwargs,
        )


class Reply(Segment):
    """回复引用。

    ``ref`` 只给坐标——要内容得另外查。Milky 1.2 起会在段里**顺带带上**被
    引用消息的内容和发送者，填进下面几个字段；OneBot 不给，那里全是默认值。
    """

    type: str = "reply"
    ref: MessageRef | None = None
    content: list[SerializeAsAny[Segment]] = Field(default_factory=list)
    sender_id: int = 0
    sender_name: str = ""
    time: int = 0

    def __init__(self, ref: MessageRef | None = None, **kwargs: Any) -> None:
        super().__init__(ref=ref, **kwargs)

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, value: Any) -> Any:
        """内容可以是段 / dict / 字符串，统一成段列表。"""
        return [] if value is None else as_segments(value)


class UnknownSegment(Segment):
    """未识别的段类型。

    ``preserve_type`` 让它成为唯一保留协议原名的段类。原始字段由 pydantic
    收进 ``model_extra``（基类开了 ``extra="allow"``），``model_dump()`` 就是
    完整原文，可以原样编码回协议。
    """

    preserve_type: ClassVar[bool] = True


def as_segments(value: Any) -> list[Segment]:
    """把 str / RichText / 段 / 段序列统一成段列表。

    容忍嵌套：``[Text("a"), ["b", At(1)]]`` 和 ``{"type": "at", ...}`` 都能吃。
    """
    from .message import RichText  # 延迟：message 依赖本模块

    if isinstance(value, RichText):
        return list(value.segments)
    if isinstance(value, Segment):
        return [value]
    if isinstance(value, str):
        return [Text(value)]
    if isinstance(value, dict):
        return [segment_from_dict(value)]
    if isinstance(value, (list, tuple)):
        flat: list[Segment] = []
        for item in value:
            flat.extend(as_segments(item))
        return flat
    raise TypeError(f"不支持的消息内容: {type(value).__name__}")


# 显式注册表：协议里的段名 → 段类
_SEGMENT_REGISTRY: dict[str, type[Segment]] = {
    "text": Text,
    "at": At,
    "face": Face,
    "image": Image,
    "record": Record,
    "video": Video,
    "file": File,
    "reply": Reply,
    "forward": Forward,
    "xml": Xml,
    # 「小程序」在两家协议里名字不同，两个键都要认，否则反序列化会掉进
    # UnknownSegment
    "json": LightApp,
    "light_app": LightApp,
    # Milky 的 @ 段；模型里统一叫 at
    "mention": At,
    "markdown": Markdown,
    "market_face": MarketFace,
    "poke": Poke,
    "rps": Rps,
    "dice": Dice,
    "shake": Shake,
    "anonymous": Anonymous,
    "share": Share,
    "contact": Contact,
    "location": Location,
    "music": Music,
}


def segment_from_dict(data: dict[str, Any]) -> Segment:
    """按 ``type`` 把原始 dict 还原成具体段类。

    未注册或验证失败的一律降级为 :class:`UnknownSegment`，未知字段原样保留
    在实例上，可以再编码回协议。
    """
    seg_type = str(data.get("type") or "")
    cls = _SEGMENT_REGISTRY.get(seg_type)
    if cls is not None:
        try:
            return cls.model_validate(data)
        except ValidationError:
            pass
    return UnknownSegment.model_validate({**data, "type": seg_type or "unknown"})
