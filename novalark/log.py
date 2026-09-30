"""日志。

标准 ``logging`` + ``rich`` 渲染。控制台走 rich，文件走轮转 handler。
"""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING

from rich.logging import RichHandler

if TYPE_CHECKING:
    from .paths import Paths

ROOT_NAME = "novalark"

#: 控制台里模块名那一列的宽度，要放得下 ``adapter.onebot_v11``。
#: 比它长的照常挤出去，不截断。
MODULE_WIDTH = 18

_console: RichHandler | None = None


def module_name(record: logging.LogRecord) -> str:
    """取日志记录里的模块名，去掉包的公共前缀。

    ``novalark.adapter.onebot_v11`` → ``adapter.onebot_v11``。
    框架根 logger 显示成 ``core``，它记的是进程级的事。
    """
    name = record.name
    if name == ROOT_NAME:
        return "core"
    prefix = f"{ROOT_NAME}."
    return name[len(prefix) :] if name.startswith(prefix) else name


class _ConsoleFormatter(logging.Formatter):
    """控制台排版：时间和级别由 rich 出，这里补一列模块名和消息。"""

    def format(self, record: logging.LogRecord) -> str:
        # markup=False 关掉了 rich 标记解析，所以这里只做空格对齐
        return f"{module_name(record):<{MODULE_WIDTH}} | {record.getMessage()}"


def setup_logging(
    paths: "Paths",
    *,
    level: str = "INFO",
    console_level: str | None = None,
) -> logging.Logger:
    """初始化框架日志。幂等：重复调用只更新控制台级别。

    logger 本身不过滤（恒为 DEBUG），级别交给两个 handler 各自把关：
    控制台按 ``level`` 显示，文件始终记全量。
    """
    global _console

    root = logging.getLogger(ROOT_NAME)
    root.setLevel(logging.DEBUG)
    if _console is not None:
        _console.setLevel((console_level or level).upper())
        return root

    root.propagate = False
    root.handlers.clear()

    console = RichHandler(
        rich_tracebacks=True,
        tracebacks_show_locals=False,
        show_path=False,
        markup=False,  # 不把 QQ 消息里的方括号当作 rich 标记
        log_time_format="%H:%M:%S",  # 不给就退回 rich 的「[日期 时间]」
    )
    console.setLevel((console_level or level).upper())
    console.setFormatter(_ConsoleFormatter())
    root.addHandler(console)
    _console = console

    file_handler = logging.handlers.RotatingFileHandler(
        paths.LOGS_DIR / "latest.log",
        maxBytes=8 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)

    return root


def set_level(level: str) -> None:
    """调控制台显示级别。文件handler 不受影响，始终记 DEBUG。"""
    if _console is not None:
        _console.setLevel(level.upper())


def get_logger(name: str | None = None) -> logging.Logger:
    """取框架 logger。``name`` 用于区分模块或插件。"""
    return logging.getLogger(f"{ROOT_NAME}.{name}" if name else ROOT_NAME)
