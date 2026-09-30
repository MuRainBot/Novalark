"""示例插件：演示事件订阅、门禁规则与回复。

直接删掉或改名（下划线开头）即可停用。
"""

from novalark import (
    GroupMessageEvent,
    PluginMeta,
    get_logger,
    on,
    on_command,
    OptionalArg,
    TextArg,
)

__plugin__: PluginMeta = {
    "author": "xiaosu",
    "version": "0.1.0",
    "description": "演示基本用法",
}

logger = get_logger("example")


@on(GroupMessageEvent)
async def log_message(event: GroupMessageEvent):
    """把群消息记进日志。"""
    logger.info(f"[{event.group_id}] {event.sender.display_name}: {event.plain_text}")


@on_command("help", OptionalArg(TextArg("name")))
async def answer_mention(event: GroupMessageEvent, name: str | None = None):
    """``/help`` 或 ``@机器人 help``。"""
    if name:
        await event.send(f"沐云 帮助 - {name}\n阿巴阿巴")
    else:
        await event.send("沐云 帮助")
