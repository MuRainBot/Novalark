"""QQ 实体：发送者、群、群成员。

字段取各协议的并集，adapter 能填多少填多少。没声明到的原始字段由
``extra="allow"`` 原样保留，解码时不会静默丢掉。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Entity(BaseModel):
    """实体基类。协议给了什么就留什么。"""

    model_config = ConfigDict(extra="allow")


class Sender(Entity):
    """消息发送者 —— 一条消息里能拿到的身份信息。"""

    user_id: int
    nickname: str = ""
    card: str = ""  # 群名片
    role: str = ""  # owner / admin / member
    title: str = ""  # 群头衔

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or str(self.user_id)

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")


class User(Entity):
    """一个用户。不一定是好友 —— 陌生人、群成员都套这个。"""

    user_id: int
    nickname: str = ""
    remark: str = ""  # 备注名，有就优先用它
    sex: str = ""
    age: int = 0
    level: int = 0
    signature: str = ""

    @property
    def display_name(self) -> str:
        return self.remark or self.nickname or str(self.user_id)


class Friend(User):
    """好友 —— 在好友列表里的 :class:`User`。"""


class GroupAnnouncement(Entity):
    """群公告。"""

    group_id: int = 0
    announcement_id: str = ""
    user_id: int = 0  # 发布者
    time: int = 0
    content: str = ""
    image_url: str = ""


class GroupFile(Entity):
    """群文件。"""

    group_id: int = 0
    file_id: str = ""
    name: str = ""
    size: int = 0
    uploader_id: int = 0
    uploaded_time: int = 0
    downloaded_times: int = 0


class GroupHonor(Entity):
    """群荣誉里的一项（龙王、群聊之火…）。"""

    user_id: int = 0
    nickname: str = ""
    honor_type: str = ""
    description: str = ""


class Group(Entity):
    """群。"""

    group_id: int
    name: str = ""
    member_count: int = 0
    max_member_count: int = 0
    description: str = ""
    announcement: str = ""


class GroupMember(Entity):
    """群成员的完整信息（查询得到）。"""

    group_id: int
    user_id: int
    nickname: str = ""
    card: str = ""
    role: str = ""
    level: int = 0
    title: str = ""

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or str(self.user_id)

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")
