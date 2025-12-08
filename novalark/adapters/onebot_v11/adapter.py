from functools import partial
from typing import Any, Optional

import anyio
import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from novalark.adapters.onebot_v11.config import OnebotV11Config
from novalark.core import protocols
from novalark.adapters import Adapter
from novalark.core.abstractions import PlatformEvent, MessageReceipt, MessageEvent, Bot, Context, Message
from novalark.core.logger_lib import get_logger
from novalark.core.rich_text import RichText, Text, Image, Segment, register_segment
from novalark.core.task import task_group_context

logger = get_logger()

ONEBOT_V11_TAG = "ob11_"


# =============================================================================
# 1. 辅助工具：富文本转换 (Converter)
# =============================================================================

@register_segment
class OnebotV11Reply(Segment):
    """OneBot V11 特有的回复段 (CQ:reply)"""
    segment_type = ONEBOT_V11_TAG + "reply"

    def __init__(self, message_id: str):
        super().__init__({
            "type": self.segment_type,
            "data": {
                "id": str(message_id)
            }
        })
        self.message_id = str(message_id)

    @classmethod
    def from_json(cls, data: dict[str, Any]):
        return cls(message_id=data.get("id"))


def _rich_text_to_ob11_array(rich_text: RichText) -> list[dict[str, Any]]:
    """将 RichText 转换为 OneBot V11 的数组格式"""
    array = []
    for segment in rich_text.segments:
        if isinstance(segment, Text):
            array.append({"type": "text", "data": {"text": segment.text}})
        elif isinstance(segment, Image):
            array.append({"type": "image", "data": {"file": segment.image_url}})
        elif isinstance(segment, OnebotV11Reply):
            array.append({"type": "reply", "data": {"id": segment.message_id}})
        else:
            # 处理通用 Segment 或 fallback
            seg_data = segment.raw_data.copy()
            # 去除框架特定的前缀，还原成协议原生 type
            if seg_data.get("type", "").startswith(ONEBOT_V11_TAG):
                seg_data["type"] = seg_data["type"][len(ONEBOT_V11_TAG):]
            array.append(seg_data)
    return array


def _ob11_array_to_rich_text(array: list[dict[str, Any]]) -> RichText:
    """将 OneBot V11 数组格式转换为 RichText"""
    segments = []
    for seg in array:
        seg_type = seg.get("type")
        data = seg.get("data", {})

        if seg_type == "text":
            segments.append(Text(data.get("text", "")))
        elif seg_type == "image":
            # OneBot 这里的 url 有时候在 url 字段，有时候在 file 字段，视实现而定
            url = data.get("url") or data.get("file")
            segments.append(Image(image_url=url))
        elif seg_type == "reply":
            segments.append(OnebotV11Reply(message_id=data.get("id")))
        else:
            # 其他 CQ 码，保留原样但加上前缀防止冲突
            raw_data = {"type": ONEBOT_V11_TAG + seg_type, "data": data}
            segments.append(Segment.from_json(raw_data))

    return RichText(*segments)


# =============================================================================
# 2. 核心对象实现 (Context, Receipt, Bot)
# =============================================================================

class OnebotV11Context(Context, protocols.Context):
    """
    OneBot V11 的上下文实现。
    根据 user_id 和 group_id 唯一确定一个聊天窗口。
    """

    def __init__(self, group_id: Optional[int] = None, user_id: Optional[int] = None):
        if group_id is None and user_id is None:
            raise ValueError("Context must have either group_id or user_id")

        self.group_id = group_id
        self.user_id = user_id

    @property
    def id(self) -> str:
        """实现 Identifiable"""
        if self.group_id:
            return ONEBOT_V11_TAG + f"group_{self.group_id}"
        return ONEBOT_V11_TAG + f"private_{self.user_id}"

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, OnebotV11Context):
            return self.group_id == other.group_id and self.user_id == other.user_id
        # 支持与字符串 ID 比较的语法糖
        if isinstance(other, str):
            return self.id == other
        return False

    def __repr__(self):
        return f"<OnebotV11Context {self.id}>"


class OnebotV11MessageReceipt(MessageReceipt):
    """
    消息回执。
    """

    def __init__(self, message_id: str, context: OnebotV11Context):
        self._id = str(message_id)
        self._context = context

    @property
    def id(self) -> str:
        return ONEBOT_V11_TAG + self._id

    @property
    def context(self) -> OnebotV11Context:
        return self._context

    def __hash__(self) -> int:
        return hash(self.id)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, OnebotV11MessageReceipt):
            return self.id == other.id
        if isinstance(other, str):
            return self.id == other
        return False


class OnebotV11Bot(Bot, protocols.SendableBot, protocols.ReplayableBot):
    def __init__(self, http_url: str, bot_id: str, adapter: 'OnebotV11Adapter'):
        super().__init__(bot_id, adapter)
        self.http_url = http_url
        self._http_client = httpx.AsyncClient(base_url=http_url)
        logger.debug(f"OnebotV11Bot created with http_url: {http_url}")

    async def call(self, path: str, others: dict[str, Any] = None, **kwargs):
        logger.debug(f"Calling OB11 payload: {kwargs} to {path}")
        resp = await self._http_client.post(path, json=kwargs, **others or {})
        resp.raise_for_status()
        result = resp.json().get("data", {})
        return result

    async def send(self, context: protocols.Context, content: RichText) -> OnebotV11MessageReceipt:
        if not isinstance(context, OnebotV11Context):
            raise ValueError("OnebotV11Bot can only send to OnebotV11Context")

        payload: dict[str, Any] = {
            "message": _rich_text_to_ob11_array(content),
            "message_type": "group" if context.group_id else "private",
        }

        if context.group_id:
            payload["group_id"] = context.group_id
        else:
            payload["user_id"] = context.user_id

        result = await self.call("send_msg", **payload)

        msg_id = result.get("message_id")
        if not msg_id:
            raise RuntimeError(f"Failed to send message, response: {result}")

        return OnebotV11MessageReceipt(message_id=str(msg_id), context=context)

    async def replay(self, target: MessageReceipt, content: RichText) -> OnebotV11MessageReceipt:
        if not isinstance(target, OnebotV11MessageReceipt):
            raise ValueError("Target must be OnebotV11MessageReceipt")

        full_content = RichText(OnebotV11Reply(message_id=target.id)) + content
        return await self.send(target.context, full_content)

    async def clean_up(self) -> None:
        await self._http_client.aclose()


# =============================================================================
# 3. 消息实体与事件 (Message, Event)
# =============================================================================

class OnebotV11Message(Message, protocols.Message):
    """
    完整的消息实体（接收到的）。
    """

    def __init__(self, message_id: str, context: OnebotV11Context, content: RichText):
        self._receipt = OnebotV11MessageReceipt(message_id, context)
        self._content = content

    @property
    def receipt(self) -> OnebotV11MessageReceipt:
        return self._receipt

    @property
    def content(self) -> RichText:
        return self._content

    @property
    def context(self) -> OnebotV11Context:
        return self._receipt.context


class OnebotV11PlatformEvent(PlatformEvent, protocols.PlatformEvent):
    def __init__(self, event_data: dict[str, Any], bot: OnebotV11Bot):
        super().__init__()
        self.event_data = event_data
        self._bot = bot

    @property
    def bot(self) -> OnebotV11Bot:
        return self._bot


class OnebotV11MessageEvent(OnebotV11PlatformEvent, MessageEvent, protocols.SendableMessageEvent,
                            protocols.ReplayableMessageEvent):
    """
    OneBot V11 消息事件实现。
    """

    def __init__(self, event_data: dict[str, Any], bot: OnebotV11Bot):
        super().__init__(event_data, bot)

        # 1. 解析上下文
        # 如果是群消息
        if self.event_data.get("message_type") == "group":
            ctx = OnebotV11Context(group_id=self.event_data["group_id"])
        # 如果是私聊
        elif self.event_data.get("message_type") == "private":
            ctx = OnebotV11Context(user_id=self.event_data["user_id"])
        else:
            # 异常情况或未知类型
            ctx = OnebotV11Context(user_id=self.event_data.get("user_id"), group_id=self.event_data.get("group_id"))

        # 2. 解析消息内容
        raw_msg = self.event_data.get("message", [])
        if isinstance(raw_msg, str):
            logger.warning(f"OnebotV11实现暂不支持cq码解析，将直接作为Text处理")
            content = RichText(Text(raw_msg))
        else:
            content = _ob11_array_to_rich_text(raw_msg)

        # 3. 构建 Message 实体
        self._message = OnebotV11Message(
            message_id=str(self.event_data["message_id"]),
            context=ctx,
            content=content
        )

    @property
    def bot(self) -> OnebotV11Bot:
        return self._bot

    @property
    def message(self) -> OnebotV11Message:
        return self._message

    @property
    def context(self) -> OnebotV11Context:
        return self._message.context


# =============================================================================
# 4. Adapter 实现
# =============================================================================

async def _handle_http_event(request: Request, bot: OnebotV11Bot):
    """处理 HTTP POST 上报"""
    try:
        data = await request.json()
    except Exception as e:
        logger.exception(f"Failed to parse request json: {repr(e)}")
        return JSONResponse({"status": "failed", "retcode": 1400}, status_code=400)

    # 简单的日志
    logger.debug(f"Received OneBot Event: {data.get('post_type')} | {data.get('sub_type')}\n{data}")

    # 仅处理消息事件，元事件/通知事件暂略
    if data.get("post_type") == "message":
        event = OnebotV11MessageEvent(data, bot)
        event.fire()
    else:
        logger.warning(
            f"OnebotV11 Adapter received unknown event: {data.get('post_type')} | {data.get('sub_type')}\n{data}"
        )
        event = OnebotV11PlatformEvent(data, bot)
        event.fire()

    # OneBot V11 对于上报通常只要求返回 200 OK 或简单的 JSON
    # 快速返回，不阻塞上报端
    return JSONResponse({"status": "ok", "retcode": 0})


class OnebotV11Adapter(Adapter):
    def __init__(self, config: OnebotV11Config):
        super().__init__()
        self.host = config.host
        self.port = config.port
        self.bot_url = config.bot_url
        self._bot_id = config.id

    async def run(self):
        # 1. 创建 Bot 实例
        bot = await anyio.to_thread.run_sync(OnebotV11Bot, self.bot_url, self._bot_id, self)

        # 2. 创建 Starlette 应用
        app = Starlette(
            routes=[
                Route("/", endpoint=partial(_handle_http_event, bot=bot), methods=["POST"])
            ]
        )

        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="warning",
            timeout_keep_alive=60
        )

        server = uvicorn.Server(config)

        logger.info(f"OnebotV11 Adapter running on http://{self.host}:{self.port}")
        logger.info(f"Connecting to Bot at {self.bot_url}")

        try:
            async with task_group_context():
                await server.serve()
        finally:
            await bot.clean_up()
            logger.info("OnebotV11 Adapter stopped.")
