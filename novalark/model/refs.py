"""会话与消息的坐标。

消息主键是 ``(conversation, seq)``。

``frozen=True`` 让它们可哈希，能直接当状态表的 key。
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MessageScene(StrEnum):
    """会话场景。

    TEMP 与 GROUP 分开：临时会话的 peer 是用户 QQ 号，但它的上下文属于某个群。
    """

    FRIEND = "friend"
    GROUP = "group"
    TEMP = "temp"


#: 场景的中文名，给日志用。
_SCENE_LABEL = {
    MessageScene.FRIEND: "好友",
    MessageScene.GROUP: "群",
    MessageScene.TEMP: "临时会话",
}


class ConversationRef(BaseModel):
    """一个会话的坐标：``(场景, 对端)``。

    群聊的对端是群号，私聊的对端是 QQ 号。QQ 语义里这就足以定位一个会话。

    ``TEMP`` 是唯一的例外：它的 ``peer_id`` 是用户 QQ 号，但上下文属于某个
    群，所以额外带一个 ``parent_group_id`` —— 发消息时要靠它定位会话，做
    会话分组时也要靠它决定归到哪一堆。
    """

    model_config = ConfigDict(frozen=True)

    scene: MessageScene
    peer_id: int
    parent_group_id: int | None = None

    @property
    def is_group(self) -> bool:
        return self.scene is MessageScene.GROUP

    @property
    def is_private(self) -> bool:
        return self.scene is MessageScene.FRIEND

    @property
    def display(self) -> str:
        """给日志看的可读串，比如 ``群 12345``。"""
        return f"{_SCENE_LABEL[self.scene]} {self.peer_id}"

    def __str__(self) -> str:
        return f"{self.scene.value}:{self.peer_id}"


class MessageRef(BaseModel):
    """一条消息的坐标：``(会话, 会话内序号)``。

    adapter 负责把协议原生的消息标识填进 ``seq``，同一会话内唯一即可。
    """

    model_config = ConfigDict(frozen=True)

    conversation: ConversationRef
    seq: int

    def __str__(self) -> str:
        return f"{self.conversation}#{self.seq}"
