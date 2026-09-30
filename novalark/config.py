"""全局配置。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

from .adapter import AdapterConfig, get_adapter_entry
from .log import get_logger
from .paths import get_paths

logger = get_logger("config")


class CoreConfig(BaseModel):
    """框架核心配置。"""

    log_level: str = "INFO"
    thread_pool_size: int = Field(default=16, ge=1)
    #: 命令词对上了但参数不对时，自动回一条用法提示
    command_hint: bool = True
    #: 命令起始符。空集合 = 关掉（命令只能靠 @机器人 触发）；
    #: 含空串 = 裸词模式（``ping`` 直接触发）
    command_starts: frozenset[str] = Field(default_factory=lambda: frozenset({"/"}))
    #: 允许用 @机器人 代替起始符
    command_at: bool = True
    #: 允许命令前带一个引用回复段
    command_reply: bool = True


class Settings(BaseModel):
    """框架主配置。"""

    core: CoreConfig = Field(default_factory=CoreConfig)
    adapter: AdapterConfig | None = None
    plugins: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _resolve_adapter(cls, data: Any) -> Any:
        """把适配器配置分派到它自己的 config 模型。"""
        if not isinstance(data, dict):
            return data

        # YAML 里留空的段（`plugins:`）解析出来是 None，当作没写
        for key in ("core", "plugins"):
            if key in data and data[key] is None:
                data.pop(key)

        raw = data.get("adapter")
        if not raw:
            data["adapter"] = None
            return data

        if not isinstance(raw, dict):
            raise ValueError("adapter 必须是一个字典")
        adapter_type = raw.get("type")
        if not isinstance(adapter_type, str) or not adapter_type:
            raise ValueError("adapter 缺少 'type' 字段")

        try:
            entry = get_adapter_entry(adapter_type)
        except KeyError as exc:
            raise ValueError(exc.args[0]) from None

        try:
            data["adapter"] = entry.config_model.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"adapter ({adapter_type}) 配置校验失败:\n{exc}") from exc

        return data


#: 配置文件不存在时写出来的模板。**手写**而不是 ``yaml.safe_dump`` ——
#: 注释是给人看的，dump 出来的东西一条注释都没有。
#: 改动这里要同步 ``README.md`` 的配置一节。
_CONFIG_TEMPLATE = """\
core:
  log_level: INFO          # 控制台级别；文件始终记 DEBUG
  thread_pool_size: 16

  # 命令起始符。空集合 = 关掉（命令只能靠 @机器人 触发）；
  # 填一个空串 = 裸词模式（ping 直接触发）
  command_starts: ["/"]
  command_at: true         # 允许用 @机器人 代替起始符
  command_reply: true      # 允许命令前带一个引用回复段
  command_hint: true       # 命令词对上了但参数不对时，回一条用法提示

# 一次连一个实现端
adapter:
  type: milky              # 或 onebot_v11
  base_url: http://127.0.0.1:3010
  # onebot_v11 用 host / port 接收上报、api_url 调 API，都可选 access_token

plugins:
"""

_settings: Settings | None = None


def init_settings(config_file: Path | None = None) -> Settings:
    """加载并校验配置。幂等。

    配置文件不存在时就地写一份带注释的模板，再照常加载它。
    """
    global _settings
    if _settings is not None:
        return _settings

    path = config_file or get_paths().CONFIG_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        logger.info(f"配置文件 {path} 不存在，已生成模板")

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    try:
        _settings = Settings.model_validate(data)
    except ValidationError as exc:
        logger.error(f"配置文件 {path} 校验失败:\n{exc}")
        raise
    return _settings


def get_settings() -> Settings:
    if _settings is None:
        raise RuntimeError("配置尚未初始化，请先调用 init_settings()")
    return _settings


def reset_settings() -> None:
    """清掉单例（测试用）。"""
    global _settings
    _settings = None
