"""事件。

- 判别靠协议无关的扁平 ``kind`` 字段，不泄漏任何协议自己的字段名
- 语义层次用类继承表达，分发走 MRO，``@on(MessageEvent)`` 收得到子类
- 未识别的事件降级为 :class:`UnknownEvent` 并保留原文

事件本身就是一个依赖：写 ``event.sender.user_id`` 即可。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .entities import (
    FriendRequest,
    Group,
    GroupInviteRequest,
    GroupJoinRequest,
    GroupRequest,
    Sender,
)
from .enums import HonorType, LifecycleState, ReactionType
from .message import Content, Message, RichText
from .refs import ConversationRef, MessageRef

if TYPE_CHECKING:
    from ..adapter.bot import Bot


class Event(BaseModel):
    """事件基类。"""

    model_config = ConfigDict(extra="allow")

    #: 是否值得记进 info 日志。心跳这类高频噪音重写成 ``False``，
    #: 它们只写文件日志（那里始终记全量）。
    notable: ClassVar[bool] = True

    kind: str = ""
    time: int = 0
    self_id: int = 0  # 收到该事件的机器人账号
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    # bot 是运行时对象，不参与验证与序列化
    _bot: Any = PrivateAttr(default=None)

    def bind(self, bot: "Bot") -> Self:
        """绑定来源 bot。adapter 在 decode 之后、分发之前调用。"""
        self._bot = bot
        return self

    @property
    def bot(self) -> "Bot":
        if self._bot is None:
            raise RuntimeError(f"{type(self).__name__} 尚未绑定 bot")
        return self._bot

    @property
    def summary(self) -> str:
        """一行摘要，给日志用。"""
        return self.kind or type(self).__name__


class MessageEvent(Event):
    """收到一条消息。"""

    message: Message

    # ── 数据访问 ────────────────────────────────────────────

    @property
    def conversation(self) -> ConversationRef:
        return self.message.ref.conversation

    @property
    def sender(self) -> Sender:
        return self.message.sender

    @property
    def content(self) -> RichText:
        return self.message.content

    @property
    def plain_text(self) -> str:
        return self.message.content.plain_text

    @property
    def ref(self) -> MessageRef:
        return self.message.ref

    @property
    def is_self(self) -> bool:
        return self.message.is_self

    @property
    def summary(self) -> str:
        return (
            f"[{self.conversation.display}] "
            f"{self.sender.display_name}: {self.content.display}"
        )

    # ── 动作（都是普通 async 方法：await 顺序执行，task() 并发） ──

    async def reply(self, content: "Content") -> MessageRef:
        """回复这条消息（自动带上引用）。"""
        return await self.bot.reply(self.message.ref, content)

    async def send(self, content: "Content") -> MessageRef:
        """往同一个会话发消息（不带引用）。"""
        return await self.bot.send(self.conversation, content)


class GroupEvent(Event):
    """带群上下文的事件。

    ``group_id`` 是所有群事件都带的字段；群实体只在有快照的子类里作为
    字段出现，取它要调 ``fetch_group()``。
    """

    group_id: int = 0

    async def fetch_group(self) -> Group:
        """群信息。子类手上有快照就重写它，省掉这次请求。"""
        return await self.bot.get_group(self.group_id)


class GroupMessageEvent(MessageEvent, GroupEvent):
    """群消息。"""

    kind: str = "group_message"

    @model_validator(mode="after")
    def _sync_group_id(self) -> Self:
        """群号由会话坐标决定。"""
        self.group_id = self.conversation.peer_id
        return self


class FriendMessageEvent(MessageEvent):
    """好友私聊消息。"""

    kind: str = "friend_message"

    @property
    def user_id(self) -> int:
        return self.conversation.peer_id


class TempMessageEvent(MessageEvent):
    """群临时会话消息。"""

    kind: str = "temp_message"

    @property
    def user_id(self) -> int:
        return self.conversation.peer_id


# ── 群里的通知 ─────────────────────────────────────────────
#
# 都继承 GroupEvent，``group_id`` 来自事件字段。


class GroupRecallEvent(GroupEvent):
    """群消息被撤回。"""

    kind: str = "group_recall"
    ref: MessageRef  # 被撤回的那条
    sender_id: int = 0  # 原消息的发送者
    operator_id: int = 0  # 谁撤的
    suffix: str = ""  # Milky 的撤回提示后缀


class GroupAdminEvent(GroupEvent):
    """群管理员被设置或取消。``is_set`` 为假表示取消。"""

    kind: str = "group_admin"
    user_id: int = 0
    operator_id: int = 0  # 只有 Milky 给，OneBot 为 0
    is_set: bool = True


class GroupMemberIncreaseEvent(GroupEvent):
    """有人进群。``operator_id`` 是管理员同意，``invitor_id`` 是被人邀请 ——
    两者最多有一个，也可能都没有（自己申请通过）。"""

    kind: str = "group_member_increase"
    user_id: int = 0
    operator_id: int = 0
    invitor_id: int = 0


class GroupMemberDecreaseEvent(GroupEvent):
    """有人退群或被踢。``kicked`` 为真表示是被踢的。"""

    kind: str = "group_member_decrease"
    user_id: int = 0
    operator_id: int = 0
    kicked: bool = False


class GroupMuteEvent(GroupEvent):
    """群成员被禁言。``duration`` 为 0 表示解除。"""

    kind: str = "group_mute"
    user_id: int = 0
    operator_id: int = 0
    duration: int = 0


class GroupWholeMuteEvent(GroupEvent):
    """全员禁言被开启或关闭。"""

    kind: str = "group_whole_mute"
    operator_id: int = 0
    is_mute: bool = True


class GroupEssenceEvent(GroupEvent):
    """群精华消息变更。``is_set`` 为假表示取消精华。"""

    kind: str = "group_essence"
    ref: MessageRef
    sender_id: int = 0  # 只有 OneBot 给
    operator_id: int = 0
    is_set: bool = True


class GroupNameChangeEvent(GroupEvent):
    """群名被改。"""

    kind: str = "group_name_change"
    name: str = ""
    operator_id: int = 0


class GroupDisbandEvent(GroupEvent):
    """群被解散。"""

    kind: str = "group_disband"
    operator_id: int = 0


class GroupReactionEvent(GroupEvent):
    """群消息上的表情回应。"""

    kind: str = "group_reaction"
    ref: MessageRef  # 被回应的那条
    user_id: int = 0
    face_id: str = ""
    reaction_type: ReactionType = ReactionType.FACE
    is_add: bool = True


class GroupNudgeEvent(GroupEvent):
    """群里有人戳了谁。"""

    kind: str = "group_nudge"
    sender_id: int = 0
    receiver_id: int = 0
    action: str = ""
    suffix: str = ""
    action_image: str = ""  # 动作用的图片，优先于 action 文本


# ── 私聊里的通知 ───────────────────────────────────────────


class FriendRecallEvent(Event):
    """好友消息被撤回。"""

    kind: str = "friend_recall"
    ref: MessageRef  # 被撤回的那条
    user_id: int = 0
    sender_id: int = 0
    operator_id: int = 0
    suffix: str = ""


class FriendAddEvent(Event):
    """加了新好友。"""

    kind: str = "friend_add"
    user_id: int = 0


class FriendNudgeEvent(Event):
    """好友之间戳一戳。

    ``sender_id`` / ``receiver_id`` 谁戳谁由协议给的「是不是自己发的」定。
    Milky 还单给一个「是不是自己收的」，但好友之间只有一对一，两者互补，
    从 ``sender_id`` 就推得出来，不再存一份可能对不上的副本。
    """

    kind: str = "friend_nudge"
    user_id: int = 0
    sender_id: int = 0
    receiver_id: int = 0
    action: str = ""
    suffix: str = ""
    action_image: str = ""

    @property
    def is_self_send(self) -> bool:
        """这次戳一戳是机器人自己发出去的吗。"""
        return bool(self.self_id) and self.sender_id == self.self_id

    @property
    def is_self_receive(self) -> bool:
        """是戳到机器人头上的吗。"""
        return bool(self.self_id) and self.receiver_id == self.self_id


class FriendFileUploadEvent(Event):
    """好友传了文件。"""

    kind: str = "friend_file_upload"
    user_id: int = 0
    file_id: str = ""
    name: str = ""
    size: int = 0
    file_hash: str = ""  # Milky 给的 TriSHA1；OneBot 没有这个事件
    is_self: bool = False


# ── 主动上报 ───────────────────────────────────────────────


class FileUploadEvent(GroupEvent):
    """群里有人传了文件。"""

    kind: str = "group_file_upload"
    user_id: int = 0
    file_id: str = ""
    name: str = ""
    size: int = 0


class PeerPinChangeEvent(Event):
    """会话被置顶或取消置顶。"""

    kind: str = "peer_pin_change"
    conversation: ConversationRef | None = None
    is_pinned: bool = True


class FriendRequestEvent(Event):
    """收到好友申请。申请本身在 :attr:`request` 里。"""

    kind: str = "friend_request"
    request: FriendRequest

    async def approve(self, remark: str = "") -> None:
        """同意。``remark`` 是给对方设的备注。"""
        await self.bot.approve_friend_request(self.request, approve=True, remark=remark)

    async def reject(self, remark: str = "") -> None:
        """拒绝。"""
        await self.bot.approve_friend_request(self.request, approve=False, remark=remark)


R = TypeVar("R", bound=GroupRequest)


class GroupRequestEvent(GroupEvent, Generic[R]):
    """等着批的群请求。请求本身在 :attr:`request` 里。

    两个子类靠**语义**分，不靠协议的事件名：

    - :class:`GroupJoinRequestEvent` —— 有人要进群（自己申请，或被别人邀请）
    - :class:`GroupInviteRequestEvent` —— 机器人**自己被**邀请进群
    """

    request: R

    @model_validator(mode="after")
    def _sync_group_id(self) -> Self:
        """群号由请求本身决定。"""
        self.group_id = self.request.group_id
        return self

    async def approve(self, reason: str = "") -> None:
        """同意。"""
        await self.bot.approve_group_request(self.request, approve=True, reason=reason)

    async def reject(self, reason: str = "") -> None:
        """拒绝。``reason`` 会一并发给对方。"""
        await self.bot.approve_group_request(self.request, approve=False, reason=reason)


class GroupJoinRequestEvent(GroupRequestEvent[GroupJoinRequest]):
    """有人要进群，等着批。"""

    kind: str = "group_join_request"


class GroupInviteRequestEvent(GroupRequestEvent[GroupInviteRequest]):
    """机器人自己被邀请进某个群。"""

    kind: str = "group_invite_request"


class BotOfflineEvent(Event):
    """机器人掉线了。"""

    kind: str = "bot_offline"
    reason: str = ""


class LifecycleEvent(Event):
    """实现端的连接状态变化。"""

    kind: str = "lifecycle"
    state: LifecycleState | None = None


class HeartbeatEvent(Event):
    """心跳。"""

    kind: str = "heartbeat"
    alive: bool = True

    notable: ClassVar[bool] = False  # 几秒一条，控制台扛不住


class GroupHonorEvent(GroupEvent):
    """群荣誉变更（龙王、群聊之火…）。"""

    kind: str = "group_honor"
    user_id: int = 0
    honor_type: HonorType | None = None


class GroupLuckyKingEvent(GroupEvent):
    """运气王。"""

    kind: str = "group_lucky_king"
    user_id: int = 0
    target_id: int = 0


# ── 框架生命周期 ───────────────────────────────────────────


class AppStartEvent(Event):
    """框架起来了。适配器已经连上（配了的话），``event.bot`` 可用。"""

    kind: str = "app_start"


class AppStopEvent(Event):
    """框架要停了。适配器还没断开。"""

    kind: str = "app_stop"


class UnknownEvent(Event):
    """未识别的事件。原始字段都在 ``raw`` 里，可按 ``kind`` 过滤。"""

    kind: str = "unknown"
