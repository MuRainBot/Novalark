"""QQ 实体：发送者、群、群成员。

字段取各协议的并集，adapter 能填多少填多少。没声明到的原始字段由
``extra="allow"`` 原样保留，解码时不会静默丢掉。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .enums import GroupRole, HonorType, ProtocolPlatform, RequestState, UserSex


class Entity(BaseModel):
    """实体基类。协议给了什么就留什么。"""

    model_config = ConfigDict(extra="allow")


class Sender(Entity):
    """消息发送者 —— 一条消息里能拿到的身份信息。

    ``role`` 只有群消息的发送者才有；私聊里是 ``None``。
    """

    user_id: int
    nickname: str = ""
    card: str = ""  # 群名片
    role: GroupRole | None = None
    title: str = ""  # 群头衔

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or str(self.user_id)

    @property
    def is_admin(self) -> bool:
        return self.role in (GroupRole.OWNER, GroupRole.ADMIN)


class User(Entity):
    """一个用户。不一定是好友 —— 陌生人、群成员都套这个。"""

    user_id: int
    nickname: str = ""
    remark: str = ""  # 备注名，有就优先用它
    sex: UserSex = UserSex.UNKNOWN
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
    honor_type: HonorType | None = None
    description: str = ""


class ImplInfo(Entity):
    """实现端自己的信息。

    两家的字段名不一样（Milky 的 ``impl_name``、OneBot 的 ``app_name``），
    这里统一。
    """

    name: str = ""  # 实现端名字
    version: str = ""  # 实现端版本
    protocol_version: str = ""  # 它实现的那个协议版本
    protocol_type: ProtocolPlatform | None = None  # 跑在什么平台上（Milky 给）


class FriendRequest(Entity):
    """一条好友申请 —— 批它所需的一切。

    两个来路：收到申请时在 ``FriendRequestEvent.request`` 里，查历史时
    ``bot.get_friend_requests()`` 直接返回一串。同一个东西，所以查出来的照样
    能交给 ``bot.approve_friend_request()``；也因为它是个普通模型，
    ``model_dump()`` 存下来过一阵 ``model_validate()`` 回来还能用。

    ``flag`` 是处理它时要带上的凭据。
    """

    user_id: int = 0  # 申请人
    flag: str = ""  # OneBot 的 flag / Milky 的 initiator_uid
    comment: str = ""
    via: str = ""
    time: int = 0
    state: RequestState = RequestState.PENDING
    is_filtered: bool = False  # 发起自风险账户、被过滤掉的申请


class GroupRequest(Entity):
    """一条群请求 —— 批它所需的一切。

    凭据两协议不同：OneBot 用 ``flag``，Milky 用 ``sequence``。两种请求各是
    一个子类，因为批准走的是不同的接口。
    """

    group_id: int = 0
    flag: str = ""  # OneBot：set_group_add_request 的凭据
    sequence: int = 0  # Milky：notification_seq 或 invitation_seq
    comment: str = ""
    state: RequestState = RequestState.PENDING
    is_filtered: bool = False


class GroupJoinRequest(GroupRequest):
    """有人要进群。

    两种来源：自己申请的（``invitor_id == 0``），或被群成员邀请的。OneBot 只报
    ``sub_type=add``，不区分；Milky 区分，批准时带不同的 ``notification_type``。
    """

    user_id: int = 0  # 要进群的那个人（被邀请时是 target，不是邀请者）
    invitor_id: int = 0  # 0 = 自己申请；非 0 = 邀请者 QQ 号


class GroupInviteRequest(GroupRequest):
    """机器人**自己被**邀请进群。"""

    invitor_id: int = 0
    source_group_id: int = 0  # OneBot 不给，恒为 0


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
    role: GroupRole | None = None
    level: int = 0
    title: str = ""

    @property
    def display_name(self) -> str:
        return self.card or self.nickname or str(self.user_id)

    @property
    def is_admin(self) -> bool:
        return self.role in (GroupRole.OWNER, GroupRole.ADMIN)
