"""取值由协议定死的字段。

这些字段的可选值是协议自己规定的 —— Milky 的 IR 用 ``enumField`` 声明，OneBot
的标准文档列出「可能的值」。存成裸 ``str`` 就把这份清单丢了：取值集得回协议文档
里翻，写代码时也没有补全。同一个概念两家取值一致时就共用一份（``role`` 两边都是
owner/admin/member），模型层照例只留一个说法。

取值集合属于**协议**，不属于模型：协议会加值（Milky 的 IR 一路带着 ``since``
标记），所以取自协议原文的那些继承 :class:`ProtocolEnum`，没收录的取值照原样
收下 —— 不能因为多了一个新值就把整个段或事件降级成未知类型。代价是写错的值也
照收，这里换的是「协议进化的那天不塌」。模型内部自定的取值（:class:`MessageScene`）
不从协议解析，用普通 ``StrEnum``，写错当场报错。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ProtocolEnum(StrEnum):
    """取值集由协议定义、且会随协议扩展的字符串枚举。

    没收录的取值照收，``.value`` 就是协议给的那个串；数字按 ``str()`` 折算 ——
    实现端把枚举值写成数字是常见的毛病，段模型开的 ``coerce_numbers_to_str``
    也是这个口径。只有 ``None`` 不认：那说明字段压根没送到，该由默认值兜底，
    不该变成 ``"None"``。
    """

    @classmethod
    def _missing_(cls, value: object) -> Any:
        """没见过的取值当场造一个成员，不丢信息也不报错。"""
        if value is None:
            return None
        text = value if isinstance(value, str) else str(value)
        member = str.__new__(cls, text)
        member._name_ = text
        member._value_ = text
        return member


class MessageScene(StrEnum):
    """会话场景。

    TEMP 与 GROUP 分开：临时会话的 peer 是用户 QQ 号，但它的上下文属于某个群。
    """

    FRIEND = "friend"
    GROUP = "group"
    TEMP = "temp"


class GroupRole(ProtocolEnum):
    """群成员权限等级。两家协议的取值一致。"""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class UserSex(ProtocolEnum):
    """性别。``UNKNOWN`` 是协议自己的一档（没公开或没填），不是「协议没给」。"""

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class RequestState(ProtocolEnum):
    """好友申请 / 群请求的处理状态（Milky 定义，OneBot 不给这个字段）。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    IGNORED = "ignored"


class ImageSubType(ProtocolEnum):
    """图片类型（Milky 定义，OneBot 标准没有这个字段）。"""

    NORMAL = "normal"
    STICKER = "sticker"


class ReactionType(ProtocolEnum):
    """表情回应用的是 QQ 表情还是 emoji（Milky 1.2 起）。"""

    FACE = "face"
    EMOJI = "emoji"


class HonorType(ProtocolEnum):
    """群荣誉的种类。通知里只会出现前几种，``ALL`` 只是查询参数。"""

    TALKATIVE = "talkative"  # 龙王
    PERFORMER = "performer"  # 群聊之火
    LEGEND = "legend"  # 群聊炽焰
    STRONG_NEWBIE = "strong_newbie"  # 冒尖小春笋
    EMOTION = "emotion"  # 快乐源泉
    ALL = "all"


class ProtocolPlatform(ProtocolEnum):
    """实现端挂着的 QQ 协议平台（Milky 给，OneBot 不给）。"""

    WINDOWS = "windows"
    LINUX = "linux"
    MACOS = "macos"
    ANDROID_PAD = "android_pad"
    ANDROID_PHONE = "android_phone"
    IPAD = "ipad"
    IPHONE = "iphone"
    HARMONY = "harmony"
    WATCH = "watch"


class LifecycleState(ProtocolEnum):
    """实现端的连接状态（OneBot 的 ``lifecycle`` 元事件）。"""

    CONNECT = "connect"  # WebSocket 连上了
    ENABLE = "enable"  # 启用
    DISABLE = "disable"  # 停用


class ActionStatus(ProtocolEnum):
    """一次接口调用的成败（两家协议的信封是一样的）。"""

    OK = "ok"
    FAILED = "failed"


class ContactKind(ProtocolEnum):
    """推荐的是好友还是群。"""

    QQ = "qq"
    GROUP = "group"


class MusicKind(ProtocolEnum):
    """音乐分享用的是哪家的曲库。"""

    QQ = "qq"
    NETEASE = "163"
    XM = "xm"
    CUSTOM = "custom"
