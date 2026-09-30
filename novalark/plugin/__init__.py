"""插件系统。"""

from .config import PluginConfig
from .context import Plugin, PluginMeta, current_plugin, get_current_plugin
from .manager import PluginManager, manager

__all__ = [
    "Plugin",
    "PluginConfig",
    "PluginManager",
    "PluginMeta",
    "current_plugin",
    "get_current_plugin",
    "manager",
]
