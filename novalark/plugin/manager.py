"""插件管理：扫描、加载。

插件是 ``plugins/`` 下的单文件（``foo.py``）或包（``bar/__init__.py``），
模块名形如 ``plugins.foo``。插件之间直接用原生 import 互相依赖，导入顺序
由 Python 保证，框架不维护依赖图。

元信息写在模块级的 ``__plugin__`` 字典里，导入后从模块对象上读。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from ..log import get_logger
from ..paths import get_paths
from .context import Plugin

if TYPE_CHECKING:
    from ..matcher.subscription import Registry

logger = get_logger("plugin")


def _registry() -> "Registry":
    """取订阅注册表。延迟导入 matcher 的注册表。"""
    from ..matcher.subscription import registry

    return registry


class PluginManager:
    """插件的加载。"""

    def __init__(self) -> None:
        self._loaded: dict[str, Plugin] = {}

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._loaded.values())

    def discover(self) -> list[str]:
        """扫描 ``plugins/`` 目录，返回插件名（已排序）。"""
        plugins_dir = get_paths().PLUGINS_DIR
        if not plugins_dir.is_dir():
            return []

        names: list[str] = []
        for entry in sorted(plugins_dir.iterdir()):
            if entry.name.startswith(("_", ".")):
                continue
            if entry.is_file() and entry.suffix == ".py":
                names.append(entry.stem)
            elif entry.is_dir() and (entry / "__init__.py").is_file():
                names.append(entry.name)
        return names

    def load_all(self) -> list[Plugin]:
        """加载所有插件。单个失败不影响其它。"""
        loaded: list[Plugin] = []
        for name in self.discover():
            plugin = self.load(name)
            if plugin is not None:
                loaded.append(plugin)
        return loaded

    def load(self, name: str) -> Plugin | None:
        """导入一个插件模块，模块级代码会注册订阅。"""
        module_name = f"plugins.{name}"
        try:
            module = importlib.import_module(module_name)
        except Exception:
            logger.exception(f"插件 {name} 加载失败")
            return None

        registry = _registry()
        plugin = registry.get_plugin(name) or registry.plugin_of(module_name)
        if plugin is None:
            return None

        _apply_metadata(plugin, getattr(module, "__plugin__", None))

        if not plugin.subscriptions:
            logger.warning(f"插件 {name} 没有注册任何订阅")

        self._loaded[name] = plugin
        suffix = f" v{plugin.version}" if plugin.version else ""
        logger.info(f"已加载插件 {name}{suffix}（{len(plugin.subscriptions)} 条订阅）")
        return plugin

manager = PluginManager()


def _apply_metadata(plugin: Plugin, meta: Any) -> None:
    """把模块里声明的 ``__plugin__`` 填进 Plugin。写得不对就忽略。

    拿到的是运行期数据，不假设它符合 ``PluginMeta``。
    """
    if not isinstance(meta, dict):
        if meta is not None:
            logger.warning(f"插件 {plugin.name} 的 __plugin__ 不是字典，已忽略")
        return
    plugin.author = str(meta.get("author") or "")
    plugin.version = str(meta.get("version") or "")
    plugin.description = str(meta.get("description") or "")
