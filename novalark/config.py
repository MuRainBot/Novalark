import importlib
from typing import Any, Dict, List, Optional, Type

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from novalark.core.logger_lib import get_logger
from novalark.paths import paths

logger = get_logger()


# =============================================================================
# 1. 基础配置模型 (可供适配器继承)
# =============================================================================

class BaseAdapterConfig(BaseModel):
    """所有适配器配置模型的基类，确保它们都有 id 和 type 字段。"""
    id: str = Field(..., description="适配器实例的唯一标识符")
    type: str = Field(..., description="适配器类型，用于动态加载")


class CoreConfig(BaseModel):
    """框架核心配置。"""
    log_level: str = "INFO"
    superusers: List[str] = []


# =============================================================================
# 2. 主配置模型 (Settings)
# =============================================================================

class Settings(BaseModel):
    """
    框架的主配置模型。
    它负责解析整个配置文件，并动态处理不同类型的适配器。
    """
    core: CoreConfig = Field(default_factory=CoreConfig)

    # ------------------ 动态适配器配置解析 ------------------
    # 字段类型为 List[Any]，因为我们将在自定义校验器中动态确定其具体类型。
    adapters: List[Any] = Field(default_factory=list)

    # ------------------ 插件配置预留位 ------------------
    # 这里暂时用 Dict[str, Any]，将来可以替换为基于注册表的 Discriminated Union。
    plugins: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("plugins", mode='before')
    @classmethod
    def handle_none_plugins(cls, v: Any) -> Dict[str, Any]:
        """如果 'plugins' 字段在 YAML 中不存在或为 None，则返回一个空字典。"""
        return v or {}

    @field_validator("adapters", mode='before')
    @classmethod
    def load_and_validate_adapter_config(cls, v: Any) -> List[BaseAdapterConfig]:
        """
        这个校验器是动态配置的核心。
        - pre=True: 在 Pydantic 尝试任何标准类型校验之前运行。
        - each_item=True: Pydantic 会将列表中的每一项分别传入此函数。
        """
        if not isinstance(v, list):
            # 如果 adapters 字段不是列表（比如缺失或为None），则视为空列表
            v = []

        processed_adapters = []
        for index, item in enumerate(v):
            if not isinstance(item, dict):
                raise ValueError(f"adapters 配置的第 {index + 1} 项必须是一个字典")

            adapter_type = item.get("type")
            if not adapter_type or not isinstance(adapter_type, str):
                raise ValueError(f"adapters 配置的第 {index + 1} 项必须包含一个 'type' 字符串字段")

            module_path = f"novalark.adapters.{adapter_type}.config"
            model_name = "".join(part.capitalize() for part in adapter_type.split("_")) + "Config"

            try:
                config_module = importlib.import_module(module_path)
                config_model_class: Type[BaseAdapterConfig] = getattr(config_module, model_name)

                # 使用找到的具体模型类来实例化配置对象
                processed_adapters.append(config_model_class(**item))

            except ImportError:
                raise ValueError(f"无法加载适配器 '{adapter_type}' (第 {index + 1} 项)：找不到模块 '{module_path}'")
            except AttributeError:
                raise ValueError(f"适配器模块 '{module_path}' (第 {index + 1} 项) 中未找到配置模型 '{model_name}'")
            except ValidationError as e:
                # 重新抛出错误，并附加上下文信息
                raise ValueError(f"适配器 '{adapter_type}' (第 {index + 1} 项) 的配置校验失败:\n{e}") from e

        return processed_adapters

    @model_validator(mode='after')
    def check_adapter_id_uniqueness(self):
        """
        在所有适配器配置都解析完成后，检查它们的 'id' 是否唯一。
        """
        ids = [adapter.id for adapter in self.adapters]
        if len(ids) != len(set(ids)):
            raise ValueError("适配器配置中的 'id' 必须是唯一的")
        return self


# =============================================================================
# 3. 全局单例管理
# =============================================================================

_settings: Optional[Settings] = None


def init_settings() -> None:
    """
    加载并解析 YAML 配置文件，初始化全局配置单例。
    这个函数应该在应用启动时被调用一次。
    """
    global _settings
    if _settings is not None:
        # 防止重复初始化
        return

    try:
        with open(paths.CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _settings = Settings(**data)
    except FileNotFoundError:
        logger.warning(f"警告: 配置文件 '{paths.CONFIG_PATH}' 不存在，将使用默认配置。")
        _settings = Settings()
    except Exception as e:
        logger.error(f"错误: 解析配置文件 '{paths.CONFIG_PATH}' 失败。")
        raise e


def get_settings() -> Settings:
    """
    获取全局配置实例。
    如果配置未初始化，将抛出异常。
    """
    if _settings is None:
        raise RuntimeError(
            "配置尚未初始化。请在应用启动时调用 'init_settings()'。"
        )
    return _settings
