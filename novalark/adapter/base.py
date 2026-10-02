"""适配器契约与注册表。

适配器是唯一知道协议细节的地方：

- 入：实现端事件 → decode 成模型 → ``emit()``
- 出：语义化动作 → 编码成协议格式 → ``call()``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, TypeVar

from pydantic import BaseModel

from ..log import get_logger
from ..matcher.dispatch import emit as fanout
from ..model.actions import ActionError, ActionResult
from ..model.entities import (
    Friend,
    FriendRequest,
    Group,
    GroupAnnouncement,
    GroupFile,
    GroupHonor,
    GroupMember,
    GroupRequest,
    ImplInfo,
    User,
)
from ..model.enums import ActionStatus, HonorType
from ..model.events import Event
from ..model.message import Message, RichText
from ..model.refs import ConversationRef, MessageRef
from ..model.segments import URL_SCHEMES, ForwardNode, MediaSegment, UnresolvableUri
from ._util import brief, to_int

if TYPE_CHECKING:
    from .bot import Bot

logger = get_logger("adapter")


class UnsupportedAction(ActionError):
    """这个适配器做不了这个动作。"""

    def __init__(self, action: str, adapter: str = "") -> None:
        who = f"{adapter} " if adapter else ""
        super().__init__(f"{who}不支持 {action}")
        self.action = action


class UnsupportedSegment(ActionError):
    """这个适配器发不了这种段。

    协议只允许发一部分段类型（Milky 的 ``OutgoingSegment`` 里就没有
    ``file`` / ``xml`` / ``markdown``），编码时直接报错好过发出去让对方
    自己猜。
    """

    def __init__(self, seg_type: str, adapter: str = "") -> None:
        who = f"{adapter} " if adapter else ""
        super().__init__(f"{who}发不了 {seg_type} 段")
        self.seg_type = seg_type


class AdapterConfig(BaseModel):
    """适配器配置基类。

    具体适配器把 ``type`` 声明成 ``Literal``，它同时承担判别和校验。
    """

    type: str


class Adapter(ABC):
    """一个实现端的连接。"""

    name: ClassVar[str] = ""

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        from .bot import Bot

        self.bot: Bot = Bot(self)

    # ── 生命周期 ───────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """连接实现端，开始接收事件。不应阻塞。

        连上之后要调 ``set_current_bot(self.bot)`` —— 媒体段的 ``_resolve``
        和 ``Forward._load`` 靠这个全局拿到适配器。
        """

    @abstractmethod
    async def stop(self) -> None:
        """断开连接，释放资源。断开后调 ``clear_current_bot(self.bot)``。"""

    # ── 传输 ───────────────────────────────────────────────

    async def call(
        self, action: str, params: dict[str, Any] | None = None
    ) -> ActionResult:
        """调一个动作，返回 ActionResult。"""
        try:
            result = await self._call(action, params)
        except Exception as exc:
            logger.debug(f"← {action} 出错: {exc!r}", exc_info=True)
            return ActionResult(status=ActionStatus.FAILED, retcode=-1, message=repr(exc))
        return result

    async def call_raw(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """发一个请求，返回原始响应（逃生口，不拆封）。异常照抛。"""
        logger.debug(f"→ {action} {brief(params or {})}")
        data = await self._call_raw(action, params)
        logger.debug(f"← {action} {brief(data)}")
        return data

    @abstractmethod
    async def _call(
        self, action: str, params: dict[str, Any] | None = None
    ) -> ActionResult:
        """真正发请求，并按本协议的响应格式解析。"""

    @abstractmethod
    async def _call_raw(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """真正发请求，不解析。"""

    # ── 语义化动作 ─────────────────────────────────────────

    @abstractmethod
    async def send_message(
        self,
        conversation: ConversationRef,
        content: RichText,
        *,
        reply_to: MessageRef | None = None,
    ) -> MessageRef:
        """发消息。``reply_to`` 非空时带上引用。失败抛 ``ActionError``。"""

    # 下面的语义化动作失败一律抛 ``ActionError``（``call`` 拆出来的失败结果
    # 在这里统一 ``unwrap``），做不到的能力抛 ``UnsupportedAction``。全仓库
    # 一套错误语义，插件只管 ``except ActionError``。

    @abstractmethod
    async def recall(self, ref: MessageRef) -> None:
        """撤回一条消息。"""

    @abstractmethod
    async def mute_member(self, group_id: int, user_id: int, duration: int) -> None:
        """禁言群成员。``duration`` 为 0 表示解除。"""

    @abstractmethod
    async def mute_all(self, group_id: int, enable: bool = True) -> None:
        """全员禁言开关。"""

    @abstractmethod
    async def kick_member(
        self, group_id: int, user_id: int, *, reject_request: bool = False
    ) -> None:
        """踢出群成员。``reject_request`` 表示顺带拒绝他的再次加群申请。"""

    @abstractmethod
    async def set_member_admin(
        self, group_id: int, user_id: int, enable: bool = True
    ) -> None:
        """设置或取消群管理员。"""

    @abstractmethod
    async def set_member_card(self, group_id: int, user_id: int, card: str) -> None:
        """改群名片。"""

    @abstractmethod
    async def set_group_name(self, group_id: int, name: str) -> None:
        """改群名。"""

    @abstractmethod
    async def leave_group(self, group_id: int) -> None:
        """退群。"""

    # ── 查询 ───────────────────────────────────────────────

    @abstractmethod
    async def get_group(self, group_id: int) -> Group:
        """查一个群。"""

    @abstractmethod
    async def get_groups(self) -> list[Group]:
        """查加入的所有群。"""

    @abstractmethod
    async def get_group_member(self, group_id: int, user_id: int) -> GroupMember:
        """查一个群成员。"""

    @abstractmethod
    async def get_group_members(self, group_id: int) -> list[GroupMember]:
        """查群成员列表。"""

    @abstractmethod
    async def get_friends(self) -> list[Friend]:
        """查好友列表。"""

    # ── 消息 ───────────────────────────────────────────────

    async def media_url(self, segment: MediaSegment) -> str:
        """取媒体段里内容的可访问地址。

        段里本来就是地址（``http(s)://`` 等）就直接给；是协议句柄就问实现端
        换一个。``segment`` 收的是模型层的段，**不要求是适配器自己的子类** ——
        插件手上常常只剩个存下来的句柄字符串，自己 ``Image(uri=...)`` 造出来
        的就是基类，走不到段自己的 :meth:`MediaSegment.get_url`。
        """
        if segment.uri.startswith(URL_SCHEMES):
            return segment.uri
        return await self._media_url(segment)

    async def _media_url(self, segment: MediaSegment) -> str:
        """把句柄换成地址。做不到抛 ``UnresolvableUri``。"""
        raise UnresolvableUri(segment.uri)

    async def send_nudge(self, conversation: ConversationRef, user_id: int) -> None:
        """戳一戳。"""
        raise UnsupportedAction("戳一戳", self.name)

    async def send_reaction(
        self, ref: MessageRef, face_id: str, *, add: bool = True
    ) -> None:
        """给消息加表情回应（或取消）。"""
        raise UnsupportedAction("表情回应", self.name)

    async def get_message(self, ref: MessageRef) -> Message:
        """按坐标取回一条消息。"""
        raise UnsupportedAction("取消息", self.name)

    async def get_history(
        self, conversation: ConversationRef, count: int = 20
    ) -> list[Message]:
        """取最近的历史消息，从新到旧。"""
        raise UnsupportedAction("取历史消息", self.name)

    async def get_forwarded(self, forward_id: str) -> list[ForwardNode]:
        """取合并转发的内容。"""
        raise UnsupportedAction("取合并转发内容", self.name)

    async def mark_read(self, conversation: ConversationRef, seq: int) -> None:
        """把某条消息及更早的标为已读。"""
        raise UnsupportedAction("标记已读", self.name)

    # ── 群 ─────────────────────────────────────────────────

    async def set_member_title(self, group_id: int, user_id: int, title: str) -> None:
        """设群成员的专属头衔。"""
        raise UnsupportedAction("设置专属头衔", self.name)

    async def set_group_avatar(self, group_id: int, uri: str) -> None:
        """改群头像。``uri`` 支持 ``file://`` / ``http(s)://`` / ``base64://``。"""
        raise UnsupportedAction("设置群头像", self.name)

    async def get_announcements(self, group_id: int) -> list[GroupAnnouncement]:
        """取群公告列表。"""
        raise UnsupportedAction("取群公告", self.name)

    async def send_announcement(
        self, group_id: int, content: str, image: str = ""
    ) -> None:
        """发群公告。"""
        raise UnsupportedAction("发群公告", self.name)

    async def delete_announcement(self, group_id: int, announcement_id: str) -> None:
        """删群公告。"""
        raise UnsupportedAction("删群公告", self.name)

    async def get_essence(self, group_id: int) -> list[Message]:
        """取群精华消息。"""
        raise UnsupportedAction("取精华消息", self.name)

    async def set_essence(self, ref: MessageRef, *, add: bool = True) -> None:
        """设精华或取消精华。"""
        raise UnsupportedAction("设置精华", self.name)

    async def get_group_files(self, group_id: int) -> list[GroupFile]:
        """取群文件列表。"""
        raise UnsupportedAction("取群文件", self.name)

    async def get_honor(
        self, group_id: int, honor_type: HonorType | None = None
    ) -> list[GroupHonor]:
        """取群荣誉（龙王、群聊之火…）。``honor_type`` 留空取全部。"""
        raise UnsupportedAction("取群荣誉", self.name)

    # ── 群文件 ─────────────────────────────────────────────
    #
    # 上传/下载的 ``uri`` 都是内容地址，不是协议句柄 —— 和媒体段同一个约定。

    async def upload_group_file(
        self, group_id: int, uri: str, name: str, *, folder_id: str = "/"
    ) -> str:
        """上传群文件，返回 ``file_id``。"""
        raise UnsupportedAction("上传群文件", self.name)

    async def upload_private_file(self, user_id: int, uri: str, name: str) -> str:
        """上传私聊文件，返回 ``file_id``。"""
        raise UnsupportedAction("上传私聊文件", self.name)

    async def get_group_file_url(self, group_id: int, file_id: str) -> str:
        """取群文件的下载链接。"""
        raise UnsupportedAction("取群文件下载链接", self.name)

    async def get_private_file_url(
        self,
        user_id: int,
        file_id: str,
        file_hash: str = "",
        *,
        is_self_send: bool = False,
    ) -> str:
        """取私聊文件的下载链接。

        ``file_hash`` 是文件的 TriSHA1，见 ``File.file_hash`` —— 只有 Milky
        对私聊文件给，拿不到就留空。
        """
        raise UnsupportedAction("取私聊文件下载链接", self.name)

    async def create_group_folder(self, group_id: int, name: str) -> str:
        """建群文件夹，返回 ``folder_id``。"""
        raise UnsupportedAction("建群文件夹", self.name)

    async def rename_group_folder(
        self, group_id: int, folder_id: str, name: str
    ) -> None:
        """重命名群文件夹。"""
        raise UnsupportedAction("重命名群文件夹", self.name)

    async def delete_group_folder(self, group_id: int, folder_id: str) -> None:
        """删群文件夹。"""
        raise UnsupportedAction("删群文件夹", self.name)

    async def delete_group_file(self, group_id: int, file_id: str) -> None:
        """删群文件。"""
        raise UnsupportedAction("删群文件", self.name)

    async def move_group_file(
        self,
        group_id: int,
        file_id: str,
        *,
        folder_id: str = "/",
        target_folder_id: str = "/",
    ) -> None:
        """把群文件挪到另一个文件夹。"""
        raise UnsupportedAction("移动群文件", self.name)

    async def rename_group_file(
        self, group_id: int, file_id: str, name: str, *, folder_id: str = "/"
    ) -> None:
        """重命名群文件。"""
        raise UnsupportedAction("重命名群文件", self.name)

    async def persist_group_file(self, group_id: int, file_id: str) -> None:
        """把群文件转存为永久文件。"""
        raise UnsupportedAction("转存群文件", self.name)

    # ── 会话置顶 ───────────────────────────────────────────

    async def get_pinned(self) -> "tuple[list[Friend], list[Group]]":
        """取置顶的会话，``(好友, 群)``。"""
        raise UnsupportedAction("取置顶会话", self.name)

    async def set_pinned(
        self, conversation: ConversationRef, pinned: bool = True
    ) -> None:
        """置顶或取消置顶一个会话。"""
        raise UnsupportedAction("置顶会话", self.name)

    # ── 实现端与账号 ───────────────────────────────────────

    async def get_impl_info(self) -> ImplInfo:
        """取实现端自己的信息（名字、版本、实现的协议版本…）。"""
        raise UnsupportedAction("取实现端信息", self.name)

    async def get_cookies(self, domain: str) -> str:
        """取某个域名下的 Cookies。"""
        raise UnsupportedAction("取 Cookies", self.name)

    async def get_csrf_token(self) -> str:
        """取 CSRF Token。"""
        raise UnsupportedAction("取 CSRF Token", self.name)

    async def get_custom_faces(self) -> list[str]:
        """取自定义表情的 URL 列表。"""
        raise UnsupportedAction("取自定义表情", self.name)

    async def get_friend_info(self, user_id: int) -> Friend:
        """取好友资料（带备注、分组），比 :meth:`get_user_info` 更贴近好友列表。"""
        raise UnsupportedAction("取好友资料", self.name)

    # ── 待处理的请求 ───────────────────────────────────────

    async def get_friend_requests(
        self, limit: int = 20, *, filtered: bool = False
    ) -> list[FriendRequest]:
        """取好友申请。拿到手就能交给 :meth:`approve_friend_request`。"""
        raise UnsupportedAction("取好友申请列表", self.name)

    async def get_group_notifications(
        self, limit: int = 20, *, filtered: bool = False
    ) -> list[dict[str, Any]]:
        """取群通知（入群申请、管理员变更…），按序列号从新到旧。

        通知的种类太多（入群请求、邀请、踢人、退群、管理员变更），各类型用到
        的字段不一样，这里原样给协议记录的字段，按 ``type`` 判别。
        """
        raise UnsupportedAction("取群通知列表", self.name)

    # ── 好友与账号 ─────────────────────────────────────────

    async def delete_friend(self, user_id: int) -> None:
        """删好友。"""
        raise UnsupportedAction("删除好友", self.name)

    async def get_user_info(self, user_id: int) -> User:
        """查某人的资料。"""
        raise UnsupportedAction("查用户资料", self.name)

    async def like_profile(self, user_id: int, count: int = 1) -> None:
        """给资料卡点赞。"""
        raise UnsupportedAction("点赞", self.name)

    async def set_friend_remark(self, user_id: int, remark: str) -> None:
        """设好友备注。"""
        raise UnsupportedAction("设置好友备注", self.name)

    async def set_nickname(self, nickname: str) -> None:
        """改机器人自己的昵称。"""
        raise UnsupportedAction("修改昵称", self.name)

    async def set_bio(self, bio: str) -> None:
        """改机器人自己的签名。"""
        raise UnsupportedAction("修改签名", self.name)

    async def set_avatar(self, uri: str) -> None:
        """改机器人自己的头像。"""
        raise UnsupportedAction("修改头像", self.name)

    # ── 请求处理 ───────────────────────────────────────────

    async def approve_friend_request(
        self, request: FriendRequest, approve: bool = True, remark: str = ""
    ) -> None:
        """同意或拒绝一条好友申请。"""
        raise UnsupportedAction("处理好友申请", self.name)

    async def approve_group_request(
        self, request: GroupRequest, approve: bool = True, reason: str = ""
    ) -> None:
        """同意或拒绝一条群请求（别人要进群 / 机器人被邀请进群）。"""
        raise UnsupportedAction("处理群请求", self.name)

    # ── 事件出口 ───────────────────────────────────────────

    def emit(self, event: Event) -> None:
        """子类 decode 完事件后调用：绑上 bot，交给分发器。

        分发是 fire-and-forget 的，这里不 await。
        """
        event.bind(self.bot)
        fanout(event)


# ── 注册表 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class AdapterEntry:
    name: str
    config_model: type[AdapterConfig]
    adapter_class: type[Adapter]


_ADAPTERS: dict[str, AdapterEntry] = {}


A = TypeVar("A", bound=Adapter)


def register_adapter(name: str, *, config: type[AdapterConfig]):
    """登记一个适配器类型。"""

    def decorator(cls: type[A]) -> type[A]:
        if name in _ADAPTERS:
            raise ValueError(f"适配器类型 {name!r} 已经注册过了")
        cls.name = name
        _ADAPTERS[name] = AdapterEntry(name, config, cls)
        return cls

    return decorator


def get_adapter_entry(name: str) -> AdapterEntry:
    entry = _ADAPTERS.get(name)
    if entry is None:
        known = ", ".join(sorted(_ADAPTERS)) or "（无）"
        raise KeyError(f"未知的适配器类型 {name!r}，已注册的有: {known}")
    return entry


def all_adapter_entries() -> list[AdapterEntry]:
    return list(_ADAPTERS.values())
