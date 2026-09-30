"""测试共享夹具。"""

from __future__ import annotations

import asyncio
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from novalark import paths as paths_module
from novalark.matcher import registry
from novalark.model import (
    ConversationRef,
    GroupMessageEvent,
    Message,
    MessageRef,
    MessageScene,
    RichText,
    Sender,
    Text,
)
from novalark.runtime import runtime


@pytest.fixture(autouse=True)
def clean_registry():
    """每个测试用干净的订阅表和全局 bot。"""
    from novalark.adapter.bot import set_current_bot

    registry.clear()
    set_current_bot(None)
    yield
    registry.clear()
    set_current_bot(None)


@pytest.fixture(autouse=True)
def bare_command_starts():
    """命令起始符在测试里默认配成"裸词模式"（``{""}``）。

    裸词模式下前缀检查恒通过，匹配行为等同于不带起始符，既有的命令用例
    不必逐个改写。要测起始符本身的用例，自己覆盖 ``novalark.config._settings``
    （或 ``reset_settings()`` 后重新初始化）。
    """
    import novalark.config as config_module
    from novalark.config import CoreConfig, Settings

    config_module._settings = Settings(core=CoreConfig(command_starts=frozenset({""})))
    yield
    config_module.reset_settings()


@pytest.fixture
def scenario():
    """在独立事件循环里跑一个场景，并处理好运行时绑定。

    用法::

        def test_x(scenario):
            async def body():
                assert await task(add(1, 2)) == 3
            scenario(body)
    """

    def _run(body: Callable[[], Awaitable[None]]) -> None:
        async def _wrapped() -> None:
            runtime.bind(pool_size=4)
            try:
                await body()
            finally:
                await runtime.shutdown()
                runtime.reset()

        asyncio.run(_wrapped())

    return _run


@pytest.fixture
def free_port() -> int:
    """要一个当前空闲的端口。测试不抢固定端口（AGENTS.md 的约定）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def make_group_event():
    """构造一条群消息事件（可选绑定一个 stub bot）。"""

    def _make(
        text: str = "hi",
        *,
        group_id: int = 12345,
        user_id: int = 999,
        self_id: int = 10001,
        seq: int = 1,
        segments=None,
        bot=None,
    ) -> GroupMessageEvent:
        conversation = ConversationRef(scene=MessageScene.GROUP, peer_id=group_id)
        event = GroupMessageEvent(
            self_id=self_id,
            message=Message(
                ref=MessageRef(conversation=conversation, seq=seq),
                content=RichText(*(segments if segments is not None else [Text(text)])),
                sender=Sender(user_id=user_id, nickname="小明"),
            ),
        )
        event.bind(bot if bot is not None else _StubBot(self_id))
        return event

    return _make


class _StubBot:
    """最小的 bot 替身：记下发出去的内容，不真发。"""

    def __init__(self, self_id: int) -> None:
        self.self_id = self_id
        self.nickname = "stub"
        self.sent: list[RichText] = []

    async def send(self, conversation: ConversationRef, content) -> MessageRef:
        self.sent.append(RichText(content))
        return MessageRef(conversation=conversation, seq=len(self.sent))

    async def reply(self, target: MessageRef, content) -> MessageRef:
        self.sent.append(RichText(content))
        return MessageRef(conversation=target.conversation, seq=len(self.sent))


@pytest.fixture
def plugin_workspace(tmp_path):
    """把框架的工作目录切到一个临时目录，用完清理干净。"""
    from novalark.plugin import manager

    (tmp_path / "plugins").mkdir()

    paths_module.reset_paths()
    paths = paths_module.init_paths(tmp_path)
    paths.register_import_root()
    manager._loaded.clear()

    try:
        yield tmp_path
    finally:
        while str(tmp_path) in sys.path:
            sys.path.remove(str(tmp_path))
        for key in [
            k for k in sys.modules if k == "plugins" or k.startswith("plugins.")
        ]:
            del sys.modules[key]
        manager._loaded.clear()
        paths_module.reset_paths()


@pytest.fixture
def write_plugin(plugin_workspace):
    """往临时 plugins/ 目录里写一个插件。"""

    def _write(name: str, source: str) -> Path:
        path = plugin_workspace / "plugins" / f"{name}.py"
        path.write_text(source, encoding="utf-8")
        return path

    return _write
