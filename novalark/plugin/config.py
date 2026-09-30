"""插件配置。

::

    class MyConfig(PluginConfig):
        greeting: str = "你好"

    config = MyConfig.load()

``load()`` 从 ``plugin_configs/<插件名>.yaml`` 读取；文件不存在时用默认值
生成一份模板。
"""

from __future__ import annotations

from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ValidationError

from ..log import get_logger
from ..paths import get_paths
from .context import plugin_name_of

logger = get_logger("plugin.config")


class PluginConfig(BaseModel):
    """插件配置基类。"""

    @classmethod
    def load(cls) -> Self:
        """从插件配置文件读取。文件不存在时写出模板并用默认值。

        插件名取自类的 ``__module__``。
        """
        name = plugin_name_of(cls.__module__)
        path = get_paths().PLUGIN_CONFIGS_DIR / f"{name}.yaml"

        if not path.exists():
            instance = cls()
            _write_template(path, instance)
            return instance

        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            logger.error(f"插件 {name} 的配置校验失败，改用默认值:\n{exc}")
            return cls()


def _write_template(path: Path, instance: "PluginConfig") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(
            instance.model_dump(),
            handle,
            allow_unicode=True,
            sort_keys=False,
        )
    logger.info(f"已生成配置模板 {path}")
