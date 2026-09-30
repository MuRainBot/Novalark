"""事件。

- 判别靠协议无关的扁平 ``kind`` 字段，不泄漏任何协议自己的字段名
- 语义层次用类继承表达，分发走 MRO，``@on(MessageEvent)`` 收得到子类
- 未识别的事件降级为 :class:`UnknownEvent` 并保留原文

事件本身就是一个依赖：写 ``event.sender.user_id`` 即可。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .entities import Group, Sender
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


class GroupCardEvent(GroupEvent):
    """群名片被改。"""

    kind: str = "group_card"
    user_id: int = 0
    card: str = ""


class GroupReactionEvent(GroupEvent):
    """群消息上的表情回应。"""

    kind: str = "group_reaction"
    ref: MessageRef  # 被回应的那条
    user_id: int = 0
    face_id: str = ""
    reaction_type: str = "face"  # face / emoji
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
    """好友之间戳一戳。"""

    kind: str = "friend_nudge"
    user_id: int = 0
    sender_id: int = 0
    receiver_id: int = 0
    action: str = ""
    suffix: str = ""
    action_image: str = ""


class FriendFileUploadEvent(Event):
    """好友传了文件。"""

    kind: str = "friend_file_upload"
    user_id: int = 0
    file_id: str = ""
    name: str = ""
    size: int = 0
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
    """收到好友申请。``flag`` 是处理它时要带上的凭据。"""

    kind: str = "friend_request"
    user_id: int = 0
    comment: str = ""
    flag: str = ""
    via: str = ""


class GroupRequestEvent(GroupEvent):
    """等着批的群请求。``flag`` 是处理它时要带上的凭据。

    这是个基类，不会被直接派发 —— 实际收到的一定是下面两个子类之一。
    ``@on(GroupRequestEvent)`` 能把两者一起接下。
    """

    comment: str = ""
    flag: str = ""
    sequence: int = 0


class GroupJoinRequestEvent(GroupRequestEvent):
    """有人申请加入群里。"""

    kind: str = "group_join_request"
    user_id: int = 0


class GroupInviteRequestEvent(GroupRequestEvent):
    """机器人被邀请进某个群。"""

    kind: str = "group_invite_request"
    invitor_id: int = 0
    source_group_id: int = 0  # OneBot 不给，恒为 0


class BotOfflineEvent(Event):
    """机器人掉线了。"""

    kind: str = "bot_offline"
    reason: str = ""


class LifecycleEvent(Event):
    """实现端的连接状态变化。"""

    kind: str = "lifecycle"
    state: str = ""


class HeartbeatEvent(Event):
    """心跳。"""

    kind: str = "heartbeat"
    alive: bool = True

    notable: ClassVar[bool] = False  # 几秒一条，控制台扛不住


class GroupHonorEvent(GroupEvent):
    """群荣誉变更（龙王、群聊之火…）。"""

    kind: str = "group_honor"
    user_id: int = 0
    honor_type: str = ""


class GroupLuckyKingEvent(GroupEvent):
    """运气王。"""

    kind: str = "group_lucky_king"
    user_id: int = 0
    target_id: int = 0


class OfflineFileEvent(Event):
    """离线文件。"""

    kind: str = "offline_file"
    user_id: int = 0
    name: str = ""
    size: int = 0
    url: str = ""


class ClientStatusEvent(Event):
    """客户端在线状态变化。"""

    kind: str = "client_status"
    online: bool = True

    notable: ClassVar[bool] = False


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
