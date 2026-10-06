from __future__ import annotations

import asyncio
from typing import Any

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...log import get_logger
from ...model.actions import ActionResult
from ...model.entities import (
    Friend,
    FriendRequest,
    Group,
    GroupHonor,
    GroupInviteRequest,
    GroupMember,
    GroupRequest,
    ImplInfo,
    User,
)
from ...model.enums import ActionStatus, HonorType
from ...model.message import Message, RichText
from ...model.segments import ForwardNode
from ...model.refs import ConversationRef, MessageRef, MessageScene
from ...model.segments import MediaSegment, Reply, Segment
from .._util import to_int
from ..base import Adapter, register_adapter
from ..bot import clear_current_bot, set_current_bot
from .decode import _decode_content, _decode_sender
from .config import OneBotV11Config
from .decode import decode_event
from .encode import encode_segments
from .segments import resolve_media

logger = get_logger("adapter.onebot_v11")

# 解转发内容时只用来给段一个会话坐标，内容本身不含坐标
_GROUP_PLACEHOLDER = ConversationRef(scene=MessageScene.GROUP, peer_id=0)

#: 群荣誉响应里的键名 → 荣誉种类。``current_talkative`` 是单个对象（当前龙王），
#: 其余几个都是 ``*_list``。
_HONOR_TYPES = {
    "current_talkative": HonorType.TALKATIVE,
    "talkative_list": HonorType.TALKATIVE,
    "performer_list": HonorType.PERFORMER,
    "legend_list": HonorType.LEGEND,
    "strong_newbie_list": HonorType.STRONG_NEWBIE,
    "emotion_list": HonorType.EMOTION,
}


def _node_data(item: Any) -> dict[str, Any] | None:
    """合并转发响应里的一个 node 段：``{"type": "node", "data": {...}}``。"""
    raw = item.get("data") if isinstance(item, dict) else None
    return raw if isinstance(raw, dict) else None


@register_adapter("onebot_v11", config=OneBotV11Config)
class OneBotV11Adapter(Adapter):
    """OneBot V11 实现端连接。

    - 事件：实现端 POST 上报到本适配器监听的端点
    - 动作：本适配器 POST 到实现端的 HTTP API
    """

    config: OneBotV11Config

    def __init__(self, config: OneBotV11Config) -> None:
        super().__init__(config)
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    def _make_client(self) -> httpx.AsyncClient:
        headers = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        return httpx.AsyncClient(
            base_url=self.config.api_url, headers=headers, timeout=30.0
        )

    # ── 生命周期 ───────────────────────────────────────────

    async def start(self) -> None:
        if self._client is None:
            self._client = self._make_client()
        app = Starlette(routes=[Route("/", self._on_report, methods=["POST"])])
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=self.config.host,
                port=self.config.port,
                log_level="warning",
                access_log=False,
                # 关掉 uvicorn 的 WebSocket 实现
                ws="none",
            )
        )
        self._server_task = asyncio.create_task(self._server.serve())

        # 等真正开始监听再返回
        while not self._server.started:
            if self._server_task.done():
                self._server_task.result()  # 把启动失败的原因抛出来
            await asyncio.sleep(0.05)

        logger.info(f"事件上报监听 http://{self.config.host}:{self.config.port}")
        logger.info(f"动作调用发往 {self.config.api_url}")
        await self._fetch_login_info()
        set_current_bot(self.bot)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            try:
                await self._server_task
            except Exception:
                logger.exception("上报服务退出时出错")
            self._server_task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        clear_current_bot(self.bot)
        logger.info("OneBot V11 适配器已停止")

    async def _fetch_login_info(self) -> None:
        """从实现端取登录号填充 bot 身份。失败不阻止启动。"""
        try:
            data = (await self.call("get_login_info")).unwrap()
        except Exception as exc:
            logger.warning(f"取登录信息失败，self_id 暂缺: {exc!r}")
            return
        self.bot.self_id = int(data.get("user_id") or 0)
        self.bot.nickname = str(data.get("nickname") or "")
        logger.info(f"已连接 {self.bot.nickname}({self.bot.self_id})")

    # ── 事件入口 ───────────────────────────────────────────

    async def _on_report(self, request: Request) -> JSONResponse:
        try:
            data = await request.json()
        except Exception as exc:
            logger.warning(f"上报内容不是合法 JSON: {exc!r}")
            return JSONResponse({"status": "failed", "retcode": 1400}, status_code=400)
        if not isinstance(data, dict):
            logger.warning(f"上报内容不是对象: {type(data).__name__}")
            return JSONResponse({"status": "failed", "retcode": 1400}, status_code=400)

        # 立刻回 200，分发在 emit 内部异步进行
        self.emit(decode_event(data))
        return JSONResponse({"status": "ok", "retcode": 0})

    # ── 传输 ───────────────────────────────────────────────

    async def _call(
        self, action: str, params: dict[str, Any] | None = None
    ) -> ActionResult:
        """OneBot 的响应信封是 ``{status, retcode, data, message/wording}``。"""
        payload = await self.call_raw(action, params)
        if not isinstance(payload, dict):
            return ActionResult(
                status=ActionStatus.FAILED,
                retcode=-1,
                message=f"响应不是对象: {payload!r}",
            )
        return ActionResult(
            status=ActionStatus(payload.get("status") or ActionStatus.OK),
            retcode=to_int(payload.get("retcode")),
            data=payload.get("data"),
            message=str(payload.get("message") or payload.get("wording") or ""),
        )

    async def _call_raw(self, action: str, params: dict[str, Any] | None = None) -> Any:
        if self._client is None:
            raise RuntimeError("适配器还没 start()，没有可用的 HTTP 客户端")
        response = await self._client.post(f"/{action}", json=params or {})
        response.raise_for_status()
        return response.json()

    # ── 语义化动作 ─────────────────────────────────────────

    async def _media_url(self, segment: MediaSegment) -> str:
        """调实现端的取媒体接口。"""
        return await resolve_media(segment.type, segment.uri)

    async def send_message(
        self,
        conversation: ConversationRef,
        content: RichText,
        *,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        segments: list[Segment] = list(content.segments)
        if reply_to is not None:
            segments.insert(0, Reply(ref=reply_to))

        params: dict[str, Any] = {
            "message": encode_segments(RichText(segments=segments))
        }
        if conversation.scene is MessageScene.GROUP:
            params["message_type"] = "group"
            params["group_id"] = conversation.peer_id
        else:
            params["message_type"] = "private"
            params["user_id"] = conversation.peer_id
            # 临时会话要靠群号才定位得到，丢了就发不出去
            if conversation.parent_group_id is not None:
                params["group_id"] = conversation.parent_group_id

        data = (await self.call("send_msg", params)).expect("发送消息失败")
        return MessageRef(
            conversation=conversation, seq=int((data or {}).get("message_id") or 0)
        )

    # ── 群管理 ─────────────────────────────────────────────

    async def recall(self, ref: MessageRef) -> None:
        (await self.call("delete_msg", {"message_id": ref.seq})).unwrap()

    async def mute_member(self, group_id: int, user_id: int, duration: int) -> None:
        (
            await self.call(
                "set_group_ban",
                {"group_id": group_id, "user_id": user_id, "duration": duration},
            )
        ).unwrap()

    async def mute_all(self, group_id: int, enable: bool = True) -> None:
        (
            await self.call(
                "set_group_whole_ban", {"group_id": group_id, "enable": enable}
            )
        ).unwrap()

    async def kick_member(
        self, group_id: int, user_id: int, *, reject_request: bool = False
    ) -> None:
        (
            await self.call(
                "set_group_kick",
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
                "set_group_admin",
                {"group_id": group_id, "user_id": user_id, "enable": enable},
            )
        ).unwrap()

    async def set_member_card(self, group_id: int, user_id: int, card: str) -> None:
        (
            await self.call(
                "set_group_card",
                {"group_id": group_id, "user_id": user_id, "card": card},
            )
        ).unwrap()

    async def set_group_name(self, group_id: int, name: str) -> None:
        (
            await self.call("set_group_name", {"group_id": group_id, "group_name": name})
        ).unwrap()

    async def leave_group(self, group_id: int) -> None:
        (await self.call("set_group_leave", {"group_id": group_id})).unwrap()

    # ── 查询 ───────────────────────────────────────────────

    async def get_group(self, group_id: int, *, no_cache: bool = False) -> Group:
        data = (
            await self.call(
                "get_group_info", {"group_id": group_id, "no_cache": no_cache}
            )
        ).unwrap()
        return _to_group(group_id, data)

    async def get_groups(self, *, no_cache: bool = False) -> list[Group]:
        # get_group_list 没有 no_cache 开关：它是提示参数，忽略就是
        data = (await self.call("get_group_list")).unwrap()
        return [_to_group(0, item) for item in _as_list(data)]

    async def get_group_member(
        self, group_id: int, user_id: int, *, no_cache: bool = False
    ) -> GroupMember:
        result = await self.call(
            "get_group_member_info",
            {"group_id": group_id, "user_id": user_id, "no_cache": no_cache},
        )
        return _to_member(group_id, user_id, result.unwrap())

    async def get_group_members(
        self, group_id: int, *, no_cache: bool = False
    ) -> list[GroupMember]:
        # get_group_member_list 同上，没有这个开关
        data = (
            await self.call("get_group_member_list", {"group_id": group_id})
        ).unwrap()
        return [_to_member(group_id, 0, item) for item in _as_list(data)]

    async def get_friends(self, *, no_cache: bool = False) -> list[Friend]:
        # get_friend_list 同上
        data = (await self.call("get_friend_list")).unwrap()
        return [Friend.model_validate(item) for item in _as_list(data)]

    # ── 消息 ───────────────────────────────────────────────

    async def get_message(self, ref: MessageRef) -> Message:
        data = (await self.call("get_msg", {"message_id": ref.seq})).unwrap()
        raw = data if isinstance(data, dict) else {}
        # 坐标调用方已经给了，不用再从返回里推
        return Message(
            ref=ref,
            content=_decode_content(raw.get("message"), ref.conversation),
            sender=_decode_sender(raw.get("sender"), 0),
            time=to_int(raw.get("time")),
        )

    async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
        # 标准响应是 ``{"message": [...]}``，里面每个 node 段的 ``data`` 里放着
        # 发送者和内容；标准没有给时间，``ForwardNode.time`` 保持默认。
        data = (await self.call("get_forward_msg", {"id": forward_id})).unwrap()
        raw = data if isinstance(data, dict) else {}
        items = raw.get("message")

        out: list[ForwardNode] = []
        for item in items if isinstance(items, list) else []:
            node = _node_data(item)
            if node is None:
                continue
            out.append(
                ForwardNode(
                    user_id=to_int(node.get("user_id")),
                    name=str(node.get("nickname") or ""),
                    content=_decode_content(
                        node.get("content"), _GROUP_PLACEHOLDER
                    ).segments,
                )
            )
        return out

    # ── 群 ─────────────────────────────────────────────────

    async def get_honor(
        self, group_id: int, honor_type: HonorType | None = None
    ) -> list[GroupHonor]:
        params: dict[str, Any] = {"group_id": group_id}
        # 不给就是取全部
        params["type"] = honor_type or HonorType.ALL
        data = (await self.call("get_group_honor_info", params)).unwrap()
        raw = data if isinstance(data, dict) else {}

        out: list[GroupHonor] = []
        # OneBot 把各类荣誉分成几个键：多数键下是一串成员，但
        # ``current_talkative``（当前龙王）是**单个对象** —— 不是 list 也一样
        # 是荣誉，不能顺手丢掉。
        for key, value in raw.items():
            if key == "group_id":
                continue
            for item in value if isinstance(value, list) else [value]:
                if not isinstance(item, dict):
                    continue
                out.append(
                    GroupHonor.model_validate(
                        {
                            "user_id": to_int(item.get("user_id")),
                            "nickname": str(item.get("nickname") or ""),
                            "honor_type": _HONOR_TYPES.get(key, key),
                            "description": str(item.get("description") or ""),
                        }
                    )
                )
        return out

    # ── 好友与账号 ─────────────────────────────────────────

    async def get_user_info(self, user_id: int, *, no_cache: bool = False) -> User:
        data = (
            await self.call(
                "get_stranger_info", {"user_id": user_id, "no_cache": no_cache}
            )
        ).unwrap()
        return User.model_validate(data if isinstance(data, dict) else {})

    async def like_profile(self, user_id: int, count: int = 1) -> None:
        (await self.call("send_like", {"user_id": user_id, "times": count})).unwrap()

    # ── 实现端与账号 ───────────────────────────────────────

    async def get_impl_info(self) -> ImplInfo:
        data = (await self.call("get_version_info")).unwrap()
        raw = data if isinstance(data, dict) else {}
        return ImplInfo.model_validate(
            {
                **raw,
                "name": str(raw.get("app_name") or ""),
                "version": str(raw.get("app_version") or ""),
                "protocol_version": str(raw.get("protocol_version") or ""),
            }
        )

    async def get_cookies(self, domain: str) -> str:
        data = (await self.call("get_cookies", {"domain": domain})).unwrap()
        raw = data if isinstance(data, dict) else {}
        return str(raw.get("cookies") or "")

    async def get_csrf_token(self) -> str:
        # 标准里这个字段叫 token，Milky 那边叫 csrf_token
        data = (await self.call("get_csrf_token")).unwrap()
        raw = data if isinstance(data, dict) else {}
        return str(raw.get("token") or "")

    # ── 请求处理 ───────────────────────────────────────────

    async def approve_friend_request(
        self, request: FriendRequest, approve: bool = True, remark: str = ""
    ) -> None:
        (
            await self.call(
                "set_friend_add_request",
                {"flag": request.flag, "approve": approve, "remark": remark},
            )
        ).unwrap()

    async def approve_group_request(
        self, request: GroupRequest, approve: bool = True, reason: str = ""
    ) -> None:
        # sub_type 必须和收到的那条一致
        (
            await self.call(
                "set_group_add_request",
                {
                    "flag": request.flag,
                    "sub_type": (
                        "invite"
                        if isinstance(request, GroupInviteRequest)
                        else "add"
                    ),
                    "approve": approve,
                    "reason": reason,
                },
            )
        ).unwrap()


def _as_list(data: Any) -> list[Any]:
    return data if isinstance(data, list) else []


def _to_group(group_id: int, data: Any) -> Group:
    raw = data if isinstance(data, dict) else {}
    return Group.model_validate(
        {
            **raw,
            "group_id": group_id or to_int(raw.get("group_id")),
            # 协议叫 group_name，模型里叫 name
            "name": str(raw.get("group_name") or ""),
        }
    )


def _to_member(group_id: int, user_id: int, data: Any) -> GroupMember:
    raw = data if isinstance(data, dict) else {}
    return GroupMember.model_validate(
        {
            **raw,
            "group_id": group_id or to_int(raw.get("group_id")),
            "user_id": user_id or to_int(raw.get("user_id")),
        }
    )
