from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import websockets

from ...log import get_logger
from ...model.actions import ActionResult
from ...model.entities import (
    Friend,
    Group,
    GroupAnnouncement,
    GroupFile,
    GroupMember,
    User,
)
from ...model.entities import Sender
from ...model.events import (
    FriendRequestEvent,
    GroupInviteRequestEvent,
    GroupRequestEvent,
)
from ...model.message import Message, RichText
from ...model.refs import ConversationRef, MessageRef, MessageScene
from .._util import to_int
from ..base import Adapter, UnsupportedAction, register_adapter
from ..bot import clear_current_bot, set_current_bot
from .config import MilkyConfig
from ...model.segments import ForwardNode
from .decode import _decode_message, _decode_segment, _scene_of, decode_event
from .encode import encode_segments

logger = get_logger("adapter.milky")

# 转发内容里的段没有自己的会话坐标，解析 reply 时只能用个占位
_PLACEHOLDER = ConversationRef(scene=MessageScene.FRIEND, peer_id=0)


@register_adapter("milky", config=MilkyConfig)
class MilkyAdapter(Adapter):
    """连一个 Milky 协议端。"""

    config: MilkyConfig

    def __init__(self, config: MilkyConfig) -> None:
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._closing = False

    def _headers(self) -> dict[str, str]:
        if self.config.access_token:
            return {"Authorization": f"Bearer {self.config.access_token}"}
        return {}

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.config.api_url, headers=self._headers())

    # ── 生命周期 ───────────────────────────────────────────

    async def start(self) -> None:
        if self._client is None:
            self._client = self._make_client()
        self._closing = False
        self._listen_task = asyncio.create_task(self._listen())
        await self._refresh_identity()
        set_current_bot(self.bot)

    async def stop(self) -> None:
        self._closing = True
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        clear_current_bot(self.bot)
        logger.info("Milky 适配器已停止")

    async def _listen(self) -> None:
        """挂着事件通道，断了就重连。"""
        while not self._closing:
            try:
                async with websockets.connect(
                    self.config.event_url, additional_headers=self._headers()
                ) as socket:
                    logger.info(f"事件通道已连接 {self.config.event_url}")
                    async for message in socket:
                        self._feed(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closing:
                    return
                logger.warning(f"事件通道断开（{exc!r}），5 秒后重连")
                await asyncio.sleep(5)

    def _feed(self, message: Any) -> None:
        try:
            payload = json.loads(message)
        except (TypeError, ValueError) as exc:
            logger.warning(f"事件不是合法 JSON: {exc!r}")
            return
        if not isinstance(payload, dict):
            logger.warning(f"事件不是对象: {type(payload).__name__}")
            return
        self.emit(decode_event(payload))

    async def _refresh_identity(self) -> None:
        try:
            data = (await self.call("get_login_info")).unwrap()
        except Exception as exc:
            logger.warning(f"取登录信息失败，self_id 暂缺: {exc!r}")
            return
        if isinstance(data, dict):
            self.bot.self_id = to_int(data.get("uin"))
            self.bot.nickname = str(data.get("nickname") or "")
        logger.info(f"已连接 {self.bot.nickname}({self.bot.self_id})")

    # ── 传输 ───────────────────────────────────────────────

    async def _call(
        self, action: str, params: dict[str, Any] | None = None
    ) -> ActionResult:
        """Milky 的响应信封是 ``{status, retcode, data, message}``。"""
        payload = await self.call_raw(action, params)
        if not isinstance(payload, dict):
            return ActionResult(
                status="failed", retcode=-1, message=f"响应不是对象: {payload!r}"
            )
        return ActionResult(
            status=str(payload.get("status") or "ok"),
            retcode=to_int(payload.get("retcode")),
            data=payload.get("data"),
            message=str(payload.get("message") or ""),
        )

    async def _call_raw(self, action: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise RuntimeError("适配器还没 start()，没有可用的 HTTP 客户端")
        response = await self._client.post(f"/{action}", json=params or {})
        response.raise_for_status()
        return response.json()

    # ── 语义化动作 ─────────────────────────────────────────

    async def send_message(
        self,
        conversation: ConversationRef,
        content: RichText,
        *,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        segments = encode_segments(content)
        if reply_to is not None:
            segments.insert(0, {"type": "reply", "data": {"message_seq": reply_to.seq}})

        if conversation.scene is MessageScene.GROUP:
            action, key = "send_group_message", "group_id"
        else:
            # Milky 把好友和临时会话都归在私聊里，靠 user_id 定位
            action, key = "send_private_message", "user_id"

        data = (
            await self.call(action, {key: conversation.peer_id, "message": segments})
        ).unwrap()
        seq = to_int(data.get("message_seq")) if isinstance(data, dict) else 0
        return MessageRef(conversation=conversation, seq=seq)

    # ── 群管理 ─────────────────────────────────────────────

    async def recall(self, ref: MessageRef) -> None:
        # 撤回按会话类型分流 —— 模型里是一个概念，协议里是两条接口
        if ref.conversation.scene is MessageScene.GROUP:
            (
                await self.call(
                    "recall_group_message",
                    {"group_id": ref.conversation.peer_id, "message_seq": ref.seq},
                )
            ).unwrap()
            return
        (
            await self.call(
                "recall_private_message",
                {"user_id": ref.conversation.peer_id, "message_seq": ref.seq},
            )
        ).unwrap()

    async def mute_member(self, group_id: int, user_id: int, duration: int) -> None:
        (
            await self.call(
                "set_group_member_mute",
                {"group_id": group_id, "user_id": user_id, "duration": duration},
            )
        ).unwrap()

    async def mute_all(self, group_id: int, enable: bool = True) -> None:
        (
            await self.call(
                "set_group_whole_mute", {"group_id": group_id, "is_mute": enable}
            )
        ).unwrap()

    async def kick_member(
        self, group_id: int, user_id: int, *, reject_request: bool = False
    ) -> None:
        (
            await self.call(
                "kick_group_member",
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "reject_add_request": reject_request,
                },
            )
        ).unwrap()

    async def set_member_admin(
        self, group_id: int, user_id: int, enable: bool = True
    ) -> None:
        (
            await self.call(
                "set_group_member_admin",
                {"group_id": group_id, "user_id": user_id, "is_set": enable},
            )
        ).unwrap()

    async def set_member_card(self, group_id: int, user_id: int, card: str) -> None:
        (
            await self.call(
                "set_group_member_card",
                {"group_id": group_id, "user_id": user_id, "card": card},
            )
        ).unwrap()

    async def set_group_name(self, group_id: int, name: str) -> None:
        (
            await self.call(
                "set_group_name", {"group_id": group_id, "new_group_name": name}
            )
        ).unwrap()

    async def leave_group(self, group_id: int) -> None:
        (await self.call("quit_group", {"group_id": group_id})).unwrap()

    # ── 查询 ───────────────────────────────────────────────

    async def get_group(self, group_id: int) -> Group:
        data = (await self.call("get_group_info", {"group_id": group_id})).unwrap()
        return _to_group(data)

    async def get_groups(self) -> list[Group]:
        data = (await self.call("get_group_list")).unwrap()
        return [_to_group(item) for item in _unbox(data, "groups")]

    async def get_group_member(self, group_id: int, user_id: int) -> GroupMember:
        result = await self.call(
            "get_group_member_info", {"group_id": group_id, "user_id": user_id}
        )
        return _to_member(group_id, user_id, result.unwrap())

    async def get_group_members(self, group_id: int) -> list[GroupMember]:
        data = (
            await self.call("get_group_member_list", {"group_id": group_id})
        ).unwrap()
        return [_to_member(group_id, 0, item) for item in _unbox(data, "members")]

    async def get_friends(self) -> list[Friend]:
        data = (await self.call("get_friend_list")).unwrap()
        return [Friend.model_validate(item) for item in _unbox(data, "friends")]

    # ── 消息 ───────────────────────────────────────────────

    async def send_nudge(self, conversation: ConversationRef, user_id: int) -> None:
        if conversation.scene is MessageScene.GROUP:
            (
                await self.call(
                    "send_group_nudge",
                    {"group_id": conversation.peer_id, "user_id": user_id},
                )
            ).unwrap()
            return
        (
            await self.call("send_friend_nudge", {"user_id": conversation.peer_id})
        ).unwrap()

    async def send_reaction(
        self, ref: MessageRef, face_id: str, *, add: bool = True
    ) -> None:
        # 表情回应只有群接口，私聊里拿 peer_id 当群号会把对方 QQ 号发出去
        if ref.conversation.scene is not MessageScene.GROUP:
            raise UnsupportedAction("私聊的表情回应", self.name)
        (
            await self.call(
                "send_group_message_reaction",
                {
                    "group_id": ref.conversation.peer_id,
                    "message_seq": ref.seq,
                    "reaction": face_id,
                    "is_add": add,
                },
            )
        ).unwrap()

    async def get_message(self, ref: MessageRef) -> Message:
        result = await self.call(
            "get_message",
            {
                "message_scene": ref.conversation.scene.value,
                "peer_id": ref.conversation.peer_id,
                "message_seq": ref.seq,
            },
        )
        return _to_message(result.unwrap(), self.bot.self_id)

    async def get_history(
        self, conversation: ConversationRef, count: int = 20
    ) -> list[Message]:
        result = await self.call(
            "get_history_messages",
            {
                "message_scene": conversation.scene.value,
                "peer_id": conversation.peer_id,
                "limit": count,
            },
        )
        return [
            _to_message(item, self.bot.self_id)
            for item in _unbox(result.unwrap(), "messages")
        ]

    async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
        result = await self.call("get_forwarded_messages", {"forward_id": forward_id})
        return [
            ForwardNode.model_validate(
                {
                    "user_id": to_int(item.get("sender_id") or item.get("user_id")),
                    "name": str(item.get("sender_name") or item.get("name") or ""),
                    "time": to_int(item.get("time")),
                    "content": [
                        _decode_segment(s, _PLACEHOLDER)
                        for s in item.get("segments") or []
                    ],
                }
            )
            for item in _unbox(result.unwrap(), "messages")
            if isinstance(item, dict)
        ]

    async def mark_read(self, conversation: ConversationRef, seq: int) -> None:
        (
            await self.call(
                "mark_message_as_read",
                {
                    "message_scene": conversation.scene.value,
                    "peer_id": conversation.peer_id,
                    "message_seq": seq,
                },
            )
        ).unwrap()

    # ── 群 ─────────────────────────────────────────────────

    async def set_member_title(self, group_id: int, user_id: int, title: str) -> None:
        (
            await self.call(
                "set_group_member_special_title",
                {"group_id": group_id, "user_id": user_id, "special_title": title},
            )
        ).unwrap()

    async def set_group_avatar(self, group_id: int, uri: str) -> None:
        (
            await self.call("set_group_avatar", {"group_id": group_id, "image_uri": uri})
        ).unwrap()

    async def get_announcements(self, group_id: int) -> list[GroupAnnouncement]:
        result = await self.call("get_group_announcements", {"group_id": group_id})
        return [
            GroupAnnouncement.model_validate(item)
            for item in _unbox(result.unwrap(), "announcements")
        ]

    async def send_announcement(
        self, group_id: int, content: str, image: str = ""
    ) -> None:
        params: dict[str, Any] = {"group_id": group_id, "content": content}
        if image:
            params["image_uri"] = image
        (await self.call("send_group_announcement", params)).unwrap()

    async def delete_announcement(self, group_id: int, announcement_id: str) -> None:
        (
            await self.call(
                "delete_group_announcement",
                {"group_id": group_id, "announcement_id": announcement_id},
            )
        ).unwrap()

    async def get_essence(self, group_id: int) -> list[Message]:
        result = await self.call(
            "get_group_essence_messages",
            {"group_id": group_id, "page_index": 0, "page_size": 20},
        )
        return [
            _to_message(item, self.bot.self_id)
            for item in _unbox(result.unwrap(), "messages")
        ]

    async def set_essence(self, ref: MessageRef, *, add: bool = True) -> None:
        # 精华属于群，私聊里拿 peer_id 当群号会把对方 QQ 号发出去
        if ref.conversation.scene is not MessageScene.GROUP:
            raise UnsupportedAction("私聊的精华消息", self.name)
        (
            await self.call(
                "set_group_essence_message",
                {
                    "group_id": ref.conversation.peer_id,
                    "message_seq": ref.seq,
                    "is_set": add,
                },
            )
        ).unwrap()

    async def get_group_files(self, group_id: int) -> list[GroupFile]:
        result = await self.call("get_group_files", {"group_id": group_id})
        return [
            GroupFile.model_validate(item) for item in _unbox(result.unwrap(), "files")
        ]

    # ── 好友与账号 ─────────────────────────────────────────

    async def delete_friend(self, user_id: int) -> None:
        (await self.call("delete_friend", {"user_id": user_id})).unwrap()

    async def get_user_info(self, user_id: int) -> User:
        result = await self.call("get_user_profile", {"user_id": user_id})
        return User.model_validate(_unbox_one(result.unwrap(), "profile"))

    async def like_profile(self, user_id: int, count: int = 1) -> None:
        (
            await self.call("send_profile_like", {"user_id": user_id, "count": count})
        ).unwrap()

    async def set_nickname(self, nickname: str) -> None:
        (await self.call("set_nickname", {"nickname": nickname})).unwrap()

    async def set_bio(self, bio: str) -> None:
        (await self.call("set_bio", {"bio": bio})).unwrap()

    async def set_avatar(self, uri: str) -> None:
        (await self.call("set_avatar", {"image_uri": uri})).unwrap()

    # ── 请求处理 ───────────────────────────────────────────

    async def approve_friend_request(
        self, request: FriendRequestEvent, approve: bool = True, remark: str = ""
    ) -> None:
        # Milky 认 uid 不认 flag，解码时把 uid 存进了 flag
        params: dict[str, Any] = {"initiator_uid": request.flag}
        if approve:
            (await self.call("accept_friend_request", params)).unwrap()
            return
        if remark:
            params["reason"] = remark
        (await self.call("reject_friend_request", params)).unwrap()

    async def approve_group_request(
        self, request: GroupRequestEvent, approve: bool = True, reason: str = ""
    ) -> None:
        params: dict[str, Any] = {
            "group_id": request.group_id,
            "notification_seq": request.sequence,
            "notification_type": (
                "invited_join_request"
                if isinstance(request, GroupInviteRequestEvent)
                else "join_request"
            ),
        }
        action = "accept_group_request" if approve else "reject_group_request"
        if not approve and reason:
            params["reason"] = reason
        (await self.call(action, params)).unwrap()


def _to_message(data: Any, self_id: int) -> Message:
    """把协议给的一条消息解成模型。复用事件那条路子，取它的 message 部分。"""
    raw = _unbox_one(data, "message")
    try:
        return _decode_message(raw, time=0, self_id=self_id).message
    except Exception:
        return Message(
            ref=MessageRef(
                conversation=ConversationRef(
                    scene=_scene_of(raw), peer_id=to_int(raw.get("peer_id"))
                ),
                seq=to_int(raw.get("message_seq")),
            ),
            content=RichText(),
            sender=Sender(user_id=to_int(raw.get("sender_id"))),
        )


def _unbox(data: Any, key: str) -> list[Any]:
    """Milky 的列表接口把结果包在命名键里，比如 {"groups": [...]}。"""
    if isinstance(data, dict):
        value = data.get(key)
        return value if isinstance(value, list) else []
    return data if isinstance(data, list) else []


def _unbox_one(data: Any, key: str) -> dict[str, Any]:
    if isinstance(data, dict):
        value = data.get(key)
        if isinstance(value, dict):
            return value
        return data
    return {}


def _to_member(group_id: int, user_id: int, data: Any) -> GroupMember:
    raw = _unbox_one(data, "member")
    # 调用方已经知道是哪个群、哪个人，协议没给就补上
    return GroupMember.model_validate(
        {
            **raw,
            "group_id": group_id or to_int(raw.get("group_id")),
            "user_id": user_id or to_int(raw.get("user_id")),
        }
    )


def _to_group(data: Any) -> Group:
    raw = _unbox_one(data, "group")
    # 协议叫 group_name，模型里叫 name；group_id 是必填的，协议没回就补 0，
    # 别让一个缺字段变成未捕获的 ValidationError
    return Group.model_validate(
        {
            **raw,
            "group_id": to_int(raw.get("group_id")),
            "name": str(raw.get("group_name") or ""),
        }
    )
