from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..model.actions import ActionResult
from ..model.events import FriendRequestEvent, GroupRequestEvent
from ..model.entities import (
    Friend,
    Group,
    GroupAnnouncement,
    GroupFile,
    GroupHonor,
    GroupMember,
    User,
)
from ..model.message import Content, Message, RichText
from ..model.refs import ConversationRef, MessageRef
from ..model.segments import ForwardNode

if TYPE_CHECKING:
    from .base import Adapter

logger = get_logger("bot")

_current: "Bot | None" = None


def _who(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    return getattr(adapter, "name", None) or type(bot).__name__


def get_bot(name: str | None = None) -> "Bot":
    """当前连接的机器人。

    ``name`` 现在用不上 —— 配置里只有一个适配器（``Settings.adapter``）。
    留着形参是为了将来真要多适配器时不用改公有 API。
    """
    if _current is None:
        raise RuntimeError("机器人尚未连接")
    return _current


def set_current_bot(bot: "Bot | None") -> None:
    """由适配器在**连上实现端之后**登记；传 ``None`` 表示清空。

    在 ``__init__`` 里登记会让 ``get_bot()`` 在 ``start()`` 之前就返回一个
    ``self_id`` 还是 0 的假 bot。顶替一个已连接的 bot 直接报错，不静默覆盖。
    """
    global _current
    if bot is None:
        _current = None
        return
    if _current is not None and _current is not bot:
        raise RuntimeError(
            f"{_who(bot)} 想顶替已经连着的 {_who(_current)}，一次只能连一个"
        )
    _current = bot


def clear_current_bot(bot: "Bot | None" = None) -> None:
    """由适配器在断开之后注销。只清自己，别人的连接不会被误伤。"""
    global _current
    if bot is None or _current is bot:
        _current = None


class Bot:
    """一个账号。"""

    def __init__(self, adapter: "Adapter") -> None:
        self.adapter = adapter
        self.self_id: int = 0  # 连接后由 adapter 从实现端获取
        self.nickname: str = ""

    def __repr__(self) -> str:
        who = f"{self.nickname}({self.self_id})" if self.self_id else "未连接"
        return f"<Bot {self.adapter.name} {who}>"

    # ── 底层 ───────────────────────────────────────────────

    async def call(
        self, action: str, params: dict[str, Any] | None = None
    ) -> ActionResult:
        return await self.adapter.call(action, params)

    async def call_raw(self, action: str, params: dict[str, Any] | None = None) -> Any:
        return await self.adapter.call_raw(action, params)

    # ── 消息 ───────────────────────────────────────────────

    async def send(self, conversation: ConversationRef, content: "Content") -> MessageRef:
        """往一个会话发消息。

        ``content`` 可以是 ``RichText``、单个段、纯文本，或者这些东西的
        列表 —— ``Bot.send(conv, [Text("查到了"), At(1)])`` 直接能用。
        """
        message = RichText(content)
        ref = await self.adapter.send_message(conversation, message)
        logger.info(f"发送 {conversation.display}：{message.display}")
        return ref

    async def reply(self, target: MessageRef, content: "Content") -> MessageRef:
        """回复一条消息（带引用）。"""
        message = RichText(content)
        ref = await self.adapter.send_message(
            target.conversation, message, reply_to=target
        )
        logger.info(f"回复 {target.conversation.display}：{message.display}")
        return ref

    # ── 群管理 ─────────────────────────────────────────────

    async def recall(self, ref: MessageRef) -> None:
        """撤回一条消息。"""
        await self.adapter.recall(ref)

    async def mute_member(self, group_id: int, user_id: int, duration: int) -> None:
        """禁言群成员。``duration`` 为 0 表示解除。"""
        await self.adapter.mute_member(group_id, user_id, duration)

    async def mute_all(self, group_id: int, enable: bool = True) -> None:
        """全员禁言开关。"""
        await self.adapter.mute_all(group_id, enable)

    async def kick_member(
        self, group_id: int, user_id: int, *, reject_request: bool = False
    ) -> None:
        """踢出群成员。"""
        await self.adapter.kick_member(group_id, user_id, reject_request=reject_request)

    async def set_member_admin(
        self, group_id: int, user_id: int, enable: bool = True
    ) -> None:
        """设置或取消群管理员。"""
        await self.adapter.set_member_admin(group_id, user_id, enable)

    async def set_member_card(self, group_id: int, user_id: int, card: str) -> None:
        """改群名片。"""
        await self.adapter.set_member_card(group_id, user_id, card)

    async def set_group_name(self, group_id: int, name: str) -> None:
        """改群名。"""
        await self.adapter.set_group_name(group_id, name)

    async def leave_group(self, group_id: int) -> None:
        """退群。"""
        await self.adapter.leave_group(group_id)

    # ── 查询 ───────────────────────────────────────────────

    async def get_group(self, group_id: int) -> Group:
        """查一个群。每次都发请求，要缓存自己在插件里拿捏。"""
        return await self.adapter.get_group(group_id)

    async def get_groups(self) -> list[Group]:
        """查加入的所有群。"""
        return await self.adapter.get_groups()

    async def get_group_member(self, group_id: int, user_id: int) -> GroupMember:
        """查一个群成员。"""
        return await self.adapter.get_group_member(group_id, user_id)

    async def get_group_members(self, group_id: int) -> list[GroupMember]:
        """查群成员列表。"""
        return await self.adapter.get_group_members(group_id)

    async def get_friends(self) -> list[Friend]:
        """查好友列表。"""
        return await self.adapter.get_friends()

    # ── 消息 ───────────────────────────────────────────────

    async def send_nudge(self, conversation: ConversationRef, user_id: int) -> None:
        """戳一戳。"""
        await self.adapter.send_nudge(conversation, user_id)

    async def send_reaction(
        self, ref: MessageRef, face_id: str, *, add: bool = True
    ) -> None:
        """给消息加表情回应（或取消）。"""
        await self.adapter.send_reaction(ref, face_id, add=add)

    async def get_message(self, ref: MessageRef) -> Message:
        """按坐标取回一条消息。"""
        return await self.adapter.get_message(ref)

    async def get_history(
        self, conversation: ConversationRef, count: int = 20
    ) -> list[Message]:
        """取最近的历史消息，从新到旧。"""
        return await self.adapter.get_history(conversation, count)

    async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
        """取合并转发的内容。"""
        return await self.adapter.get_forwarded(forward_id)

    async def mark_read(self, conversation: ConversationRef, seq: int) -> None:
        """把某条消息及更早的标为已读。"""
        await self.adapter.mark_read(conversation, seq)

    # ── 群 ─────────────────────────────────────────────────

    async def set_member_title(self, group_id: int, user_id: int, title: str) -> None:
        """设群成员的专属头衔。"""
        await self.adapter.set_member_title(group_id, user_id, title)

    async def set_group_avatar(self, group_id: int, uri: str) -> None:
        """改群头像。"""
        await self.adapter.set_group_avatar(group_id, uri)

    async def get_announcements(self, group_id: int) -> list[GroupAnnouncement]:
        """取群公告列表。"""
        return await self.adapter.get_announcements(group_id)

    async def send_announcement(
        self, group_id: int, content: str, image: str = ""
    ) -> None:
        """发群公告。"""
        await self.adapter.send_announcement(group_id, content, image)

    async def delete_announcement(self, group_id: int, announcement_id: str) -> None:
        """删群公告。"""
        await self.adapter.delete_announcement(group_id, announcement_id)

    async def get_essence(self, group_id: int) -> list[Message]:
        """取群精华消息。"""
        return await self.adapter.get_essence(group_id)

    async def set_essence(self, ref: MessageRef, *, add: bool = True) -> None:
        """设精华或取消精华。"""
        await self.adapter.set_essence(ref, add=add)

    async def get_group_files(self, group_id: int) -> list[GroupFile]:
        """取群文件列表。"""
        return await self.adapter.get_group_files(group_id)

    async def get_honor(self, group_id: int, honor_type: str = "") -> list[GroupHonor]:
        """取群荣誉（龙王、群聊之火…）。"""
        return await self.adapter.get_honor(group_id, honor_type)

    # ── 好友与账号 ─────────────────────────────────────────

    async def delete_friend(self, user_id: int) -> None:
        """删好友。"""
        await self.adapter.delete_friend(user_id)

    async def get_user_info(self, user_id: int) -> User:
        """查某人的资料。"""
        return await self.adapter.get_user_info(user_id)

    async def like_profile(self, user_id: int, count: int = 1) -> None:
        """给资料卡点赞。"""
        await self.adapter.like_profile(user_id, count)

    async def set_friend_remark(self, user_id: int, remark: str) -> None:
        """设好友备注。"""
        await self.adapter.set_friend_remark(user_id, remark)

    async def set_nickname(self, nickname: str) -> None:
        """改机器人自己的昵称。"""
        await self.adapter.set_nickname(nickname)

    async def set_bio(self, bio: str) -> None:
        """改机器人自己的签名。"""
        await self.adapter.set_bio(bio)

    async def set_avatar(self, uri: str) -> None:
        """改机器人自己的头像。"""
        await self.adapter.set_avatar(uri)

    # ── 请求处理 ───────────────────────────────────────────

    async def approve_friend_request(
        self, request: FriendRequestEvent, approve: bool = True, remark: str = ""
    ) -> None:
        """同意或拒绝好友申请。"""
        await self.adapter.approve_friend_request(request, approve, remark)

    async def approve_group_request(
        self, request: GroupRequestEvent, approve: bool = True, reason: str = ""
    ) -> None:
        """同意或拒绝一条群请求（别人申请进群 / 机器人被邀请进群）。"""
        await self.adapter.approve_group_request(request, approve, reason)
