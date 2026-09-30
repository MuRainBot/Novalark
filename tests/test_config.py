"""配置解析与 App 生命周期。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from novalark.config import Settings


def test_unknown_adapter_type_lists_registered():
    """未知类型要报错并列出已注册的类型。"""
    with pytest.raises(ValidationError) as info:
        Settings.model_validate({"adapter": {"type": "onebot_v1"}})
    text = str(info.value)
    assert "未知的适配器类型" in text
    assert "onebot_v11" in text


def test_invalid_field_is_reported():
    with pytest.raises(ValidationError) as info:
        Settings.model_validate({"adapter": {"type": "onebot_v11", "port": "不是数字"}})
    text = str(info.value)
    assert "onebot_v11" in text
    assert "port" in text


def test_missing_type_is_reported():
    with pytest.raises(ValidationError, match="缺少 'type'"):
        Settings.model_validate({"adapter": {"host": "x"}})


def test_empty_yaml_sections_are_tolerated():
    """YAML 里留空的 `plugins:` 解析出来是 None，不该报类型错。"""
    settings = Settings.model_validate({"core": None, "plugins": None, "adapter": None})
    assert settings.plugins == {}
    assert settings.adapter is None


def test_adapter_config_dispatches_to_concrete_class():
    settings = Settings.model_validate(
        {
            "core": {"log_level": "DEBUG", "thread_pool_size": 4},
            "adapter": {"type": "onebot_v11", "host": "0.0.0.0", "port": 5801},
        }
    )
    assert settings.core.thread_pool_size == 4
    assert type(settings.adapter).__name__ == "OneBotV11Config"
    assert settings.adapter.port == 5801


def test_app_lifecycle_events_reach_plugins(scenario, tmp_path):
    """启动/关闭事件让插件做需要 await 的初始化和收尾。

    没配适配器也要照常派发 —— 只初始化自己资源的插件不该被适配器绑架。
    """
    from novalark import App, AppStartEvent, AppStopEvent, on
    from novalark import paths as paths_module
    from novalark.config import reset_settings

    seen: list[str] = []

    @on(AppStartEvent)
    async def on_start(event):
        await asyncio.sleep(0)  # 真的 await 得动
        seen.append("start")

    @on(AppStopEvent)
    async def on_stop(event):
        seen.append("stop")

    (tmp_path / "plugins").mkdir()

    async def body():
        app = App(work_dir=tmp_path)
        runner = asyncio.create_task(app.run())

        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.05)
        assert seen == ["start"]

        app.stop()
        await asyncio.wait_for(runner, timeout=15)

    reset_settings()
    paths_module.reset_paths()
    try:
        scenario(body)
    finally:
        reset_settings()
        paths_module.reset_paths()

    assert seen == ["start", "stop"]


def test_app_starts_and_stops(scenario, tmp_path, free_port):
    """App 能起来也能停。

    用临时目录和自己的 ``config.yaml``。
    """
    from novalark import App, AppStopEvent, get_bot, on
    from novalark import paths as paths_module
    from novalark.config import reset_settings

    # 关闭事件必须在适配器断开之前派发，那时才发得出最后一条消息
    stop_saw_bot: list[str] = []

    @on(AppStopEvent)
    async def on_stop(event):
        try:
            get_bot()
            stop_saw_bot.append("alive")
        except RuntimeError:
            stop_saw_bot.append("gone")

    (tmp_path / "plugins").mkdir()
    (tmp_path / "config.yaml").write_text(
        f"adapter:\n  type: onebot_v11\n  host: 127.0.0.1\n  port: {free_port}\n",
        encoding="utf-8",
    )

    async def body():
        app = App(work_dir=tmp_path)
        runner = asyncio.create_task(app.run())

        # 等适配器真正连上 —— bot 是在 start() 末尾才登记的，固定 sleep 是竞态
        for _ in range(300):
            try:
                connected = get_bot()
                break
            except RuntimeError:
                await asyncio.sleep(0.05)
        else:
            raise AssertionError("适配器一直没连上")

        assert not runner.done(), "App 不该自己退出"
        assert app.adapter is not None
        assert app.adapter.name == "onebot_v11"
        assert connected is app.adapter.bot

        app.stop()
        await asyncio.wait_for(runner, timeout=15)

    reset_settings()
    paths_module.reset_paths()
    try:
        scenario(body)
    finally:
        reset_settings()
        paths_module.reset_paths()

    assert stop_saw_bot == ["alive"]


def test_missing_config_is_generated_with_comments(tmp_path):
    """配置文件不存在时写一份带注释的模板，并直接拿它当配置。"""
    from novalark.config import init_settings, reset_settings

    reset_settings()
    path = tmp_path / "nested" / "config.yaml"
    settings = init_settings(path)

    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "#" in text
    assert settings.core.command_starts == frozenset({"/"})
    assert settings.adapter is not None


def test_generated_template_covers_every_core_field(tmp_path):
    """模板要写全 CoreConfig 的字段 —— 用户照着它改，才看得见有哪些可调项。"""
    from novalark.config import CoreConfig, init_settings, reset_settings

    reset_settings()
    init_settings(tmp_path / "config.yaml")

    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    for name in CoreConfig.model_fields:
        assert name in text, name


def test_existing_config_is_left_alone(tmp_path):
    from novalark.config import init_settings, reset_settings

    reset_settings()
    path = tmp_path / "config.yaml"
    original = "core:\n  log_level: DEBUG\n"
    path.write_text(original, encoding="utf-8")

    settings = init_settings(path)

    assert settings.core.log_level == "DEBUG"
    assert path.read_text(encoding="utf-8") == original


def test_generating_the_template_is_announced(tmp_path, caplog):
    import logging

    from novalark.config import init_settings, reset_settings

    reset_settings()
    with caplog.at_level(logging.INFO, logger="novalark"):
        init_settings(tmp_path / "config.yaml")

    assert any("已生成模板" in r.getMessage() for r in caplog.records)
