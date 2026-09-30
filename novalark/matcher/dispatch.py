"""事件分发。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..log import get_logger
from ..runtime import task
from .errors import PropagationScope, StopPropagation
from .subscription import registry

if TYPE_CHECKING:
    from ..model.events import Event

logger = get_logger("matcher")


async def dispatch(event: "Event") -> None:
    """把事件交给所有匹配的订阅。

    命令是独占的：一条消息只会触发**一条**命令，先匹配到的赢。普通订阅照常跑。

    进来就先记一条 —— 业务事件走 info，心跳那种高频噪音只有 ``notable``
    为假，降到 debug（文件日志照样收得到）。
    """
    logger.log(
        logging.INFO if event.notable else logging.DEBUG, f"收到 {event.summary}"
    )

    skipped_plugins: set[int] = set()
    command_taken = False
    command_candidates = 0

    for subscription in registry.match(event):
        plugin = subscription.plugin
        if plugin is not None and id(plugin) in skipped_plugins:
            continue
        if subscription.command is not None:
            if command_taken:
                logger.debug(f"{subscription.command.display} 已被认领，跳过这条")
                continue
            command_candidates += 1

        try:
            if await subscription.invoke(event) and subscription.command is not None:
                command_taken = True
        except StopPropagation as stop:
            if stop.scope is PropagationScope.GLOBAL:
                logger.debug(
                    f"{subscription.handler.__name__} 阻断了 {type(event).__name__}"
                )
                return
            if plugin is not None:
                skipped_plugins.add(id(plugin))
        except Exception:
            # 一个 handler 出错不能影响同一事件上的其他订阅
            logger.exception(
                f"订阅 {subscription.handler.__name__} 处理 {type(event).__name__} 时出错"
            )

    # 「我的命令怎么不响应」的第三种情况：有命令订阅在盯着，但一条都没认领。
    # 没有命令订阅参与时不记，否则普通聊天会把控制台刷满。
    if command_candidates and not command_taken:
        logger.debug(f"{command_candidates} 条命令订阅都没认领：{event.summary}")


def emit(event: "Event") -> None:
    """同步入口：把事件排进事件循环异步分发。

    adapter 收到上报后调它，立刻返回（fire-and-forget）。
    """
    task(_dispatch_safe(event))


async def _dispatch_safe(event: "Event") -> None:
    try:
        await dispatch(event)
    except Exception:
        logger.exception(f"分发 {type(event).__name__} 时出错")
