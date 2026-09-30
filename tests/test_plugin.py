"""插件系统：发现、加载、归属、配置、卸载。"""

from __future__ import annotations

import sys

from novalark.matcher import dispatch, registry
from novalark.model import GroupMessageEvent
from novalark.plugin import manager

ECHO_SOURCE = """
from novalark import GroupMessageEvent, PluginMeta, on

__plugin__: PluginMeta = {"author": "xiaosu", "version": "1.0.0", "description": "回显"}

SEEN = []


@on(GroupMessageEvent)
async def echo(event):
    SEEN.append(event.plain_text)
"""


def test_discover_finds_files_and_packages(write_plugin, plugin_workspace):
    write_plugin("echo", ECHO_SOURCE)
    write_plugin("_helper", "SEEN = []")  # 下划线开头不当作插件
    package = plugin_workspace / "plugins" / "grouped"
    package.mkdir()
    (package / "__init__.py").write_text("SEEN = []", encoding="utf-8")
    (plugin_workspace / "plugins" / "not_a_plugin.txt").write_text(
        "x", encoding="utf-8"
    )

    assert manager.discover() == ["echo", "grouped"]


def test_load_registers_ownership_and_metadata(write_plugin):
    write_plugin("echo", ECHO_SOURCE)

    plugin = manager.load("echo")

    assert plugin is not None
    assert plugin.name == "echo"
    assert plugin.author == "xiaosu"
    assert plugin.version == "1.0.0"
    assert plugin.description == "回显"
    assert len(plugin.subscriptions) == 1
    # 归属由 fn.__module__ 决定，与模块名一致
    assert registry.get_plugin("echo") is plugin
    assert plugin.subscriptions[0].plugin is plugin


def test_plugin_metadata_is_statically_readable(write_plugin):
    """元信息不执行代码就能读出来。"""
    import ast

    path = write_plugin("echo", ECHO_SOURCE)
    tree = ast.parse(path.read_text(encoding="utf-8"))

    meta = None
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):  # __plugin__: PluginMeta = {...}
            name, value = getattr(node.target, "id", ""), node.value
        elif isinstance(node, ast.Assign):  # __plugin__ = {...}
            name, value = getattr(node.targets[0], "id", ""), node.value
        else:
            continue
        if name == "__plugin__" and value is not None:
            meta = ast.literal_eval(value)

    assert meta == {"author": "xiaosu", "version": "1.0.0", "description": "回显"}


def test_plugin_handler_receives_events(scenario, make_group_event, write_plugin):
    write_plugin("echo", ECHO_SOURCE)
    manager.load("echo")

    async def body():
        await dispatch(make_group_event("你好"))
        assert sys.modules["plugins.echo"].SEEN == ["你好"]

    scenario(body)


def test_current_plugin_is_visible_in_handlers(
    scenario, make_group_event, write_plugin
):
    """运行期 handler 内部要知道自己属于哪个插件，同步 handler 也一样。"""
    write_plugin(
        "ctx",
        """
from novalark import GroupMessageEvent, PluginMeta, get_current_plugin, on

__plugin__: PluginMeta = {"version": "1.0.0"}

ASYNC_SEEN = []
SYNC_SEEN = []


@on(GroupMessageEvent)
async def async_handler(event):
    plugin = get_current_plugin()
    ASYNC_SEEN.append(plugin.name if plugin else None)


@on(GroupMessageEvent, priority=-1)
def sync_handler(event):
    plugin = get_current_plugin()
    SYNC_SEEN.append(plugin.name if plugin else None)
""",
    )
    manager.load("ctx")
    module = sys.modules["plugins.ctx"]

    async def body():
        await dispatch(make_group_event())
        assert module.ASYNC_SEEN == ["ctx"]
        assert module.SYNC_SEEN == ["ctx"]

    scenario(body)


def test_broken_plugin_does_not_block_others(write_plugin):
    write_plugin("broken", "raise RuntimeError('炸了')")
    write_plugin("echo", ECHO_SOURCE)

    loaded = manager.load_all()

    assert [plugin.name for plugin in loaded] == ["echo"]


def test_package_plugin(scenario, make_group_event, plugin_workspace):
    package = plugin_workspace / "plugins" / "greeter"
    package.mkdir()
    (package / "__init__.py").write_text(
        """
from novalark import GroupMessageEvent, PluginMeta, on

__plugin__: PluginMeta = {"version": "1.0.0"}
SEEN = []


@on(GroupMessageEvent)
async def greet(event):
    SEEN.append("greeted")
""",
        encoding="utf-8",
    )
    # 子模块也要归到同一个插件（前缀匹配）
    (package / "helper.py").write_text("VALUE = 1", encoding="utf-8")

    manager.load("greeter")

    assert registry.plugin_of("plugins.greeter.helper") is registry.get_plugin(
        "greeter"
    )

    async def body():
        await dispatch(make_group_event())
        assert sys.modules["plugins.greeter"].SEEN == ["greeted"]

    scenario(body)


def test_plugin_config_generates_template_then_loads(write_plugin, plugin_workspace):
    write_plugin(
        "cfg",
        """
from novalark import PluginConfig, PluginMeta

__plugin__: PluginMeta = {"version": "1.0.0"}


class MyConfig(PluginConfig):
    greeting: str = "你好"
    count: int = 3


CONFIG = MyConfig.load()
""",
    )

    # 首次加载：文件不存在，用默认值并写出模板
    manager.load("cfg")
    assert sys.modules["plugins.cfg"].CONFIG.greeting == "你好"

    config_file = plugin_workspace / "plugin_configs" / "cfg.yaml"
    assert config_file.exists()

    # 改配置后再读一次：拿到新值
    config_file.write_text("greeting: 嗨\ncount: 9\n", encoding="utf-8")

    reloaded = sys.modules["plugins.cfg"].MyConfig.load()
    assert reloaded.greeting == "嗨"
    assert reloaded.count == 9


def test_plugin_config_falls_back_on_invalid_yaml(write_plugin, plugin_workspace):
    write_plugin(
        "cfg",
        """
from novalark import PluginConfig, PluginMeta

__plugin__: PluginMeta = {"version": "1.0.0"}


class MyConfig(PluginConfig):
    count: int = 3


CONFIG = MyConfig.load()
""",
    )
    (plugin_workspace / "plugin_configs").mkdir(exist_ok=True)
    (plugin_workspace / "plugin_configs" / "cfg.yaml").write_text(
        "count: 不是数字\n", encoding="utf-8"
    )

    manager.load("cfg")

    # 坏配置不该让插件崩掉，退回默认值
    assert sys.modules["plugins.cfg"].CONFIG.count == 3
