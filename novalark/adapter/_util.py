"""适配器共用的解析小工具。

协议的字段类型很松：数字可能是字符串、空串当没有。
"""

from __future__ import annotations

from typing import Any


def _to_int(value: Any) -> int | None:
    """尽力转整数，转不了给 None。

    小数和小数文本一视同仁（都截断）。只走 ``int()`` 的话 ``12.5`` 得 12
    而 ``"12.5"`` 得 0 —— 这个落差落在禁言时长上就是「0 秒 = 解禁」。
    """
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None


def to_int(value: Any) -> int:
    """转整数，转不了给 0。"""
    result = _to_int(value)
    return 0 if result is None else result


def int_or_none(value: Any) -> int | None:
    """转整数，空值或转不了给 None —— 用来区分「没有」和「是 0」。"""
    if value in (None, ""):
        return None
    return _to_int(value)


def text_or_none(value: Any) -> str | None:
    """转字符串，空值给 None。"""
    return str(value) if value not in (None, "") else None


def brief(value: Any, limit: int = 160) -> str:
    """把参数或返回值压成一行，太长的截断。给日志用。"""
    text = repr(value)
    return text if len(text) <= limit else f"{text[:limit]}…"
