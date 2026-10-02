"""适配器层：把协议投影到 QQ 语义模型。"""

from .base import (
    Adapter,
    AdapterConfig,
    AdapterEntry,
    UnsupportedAction,
    UnsupportedSegment,
    all_adapter_entries,
    get_adapter_entry,
    register_adapter,
)
from .bot import Bot, get_bot
from .milky import MilkyAdapter, MilkyConfig

# 导入内置适配器以触发 @register_adapter 注册
from .onebot_v11 import OneBotV11Adapter, OneBotV11Config

__all__ = [
    "Adapter",
    "AdapterConfig",
    "AdapterEntry",
    "Bot",
    "MilkyAdapter",
    "MilkyConfig",
    "OneBotV11Adapter",
    "OneBotV11Config",
    "UnsupportedAction",
    "UnsupportedSegment",
    "all_adapter_entries",
    "get_adapter_entry",
    "get_bot",
    "register_adapter",
]
